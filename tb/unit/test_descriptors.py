"""pytest: descriptor schema, ingestion consistency, derived metrics.  Run: make pytest"""
import glob
import json
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "sw"))
from npumodel import schema as S, ingest  # noqa: E402

SLUGS = [l.split("\t")[0] for l in open(os.path.join(ROOT, "models", "repos.tsv")).read().split("\n")[1:] if l]


def desc(slug):
    return S.load(os.path.join(ROOT, "models", "descriptors", f"{slug}.yaml"))


def test_eleven_models():
    assert len(SLUGS) == 11
    assert len(glob.glob(os.path.join(ROOT, "models", "descriptors", "*.yaml"))) == 11


@pytest.mark.parametrize("slug", SLUGS)
def test_validates_and_weight_check(slug):
    d = S.validate(desc(slug))
    # layer plan + dtypes must reproduce the published weight size (vision/audio and duplicated tensors removed)
    assert abs(d["derived"]["weight_check_ratio"] - 1) < 0.02
    assert d["derived"]["params_active_b"] < d["derived"]["params_total_b"]
    # second, independent check: parameter counts quoted by the authors (they round; 'active' conventions differ)
    if d.get("published"):
        assert abs(d["derived"]["published_total_ratio"] - 1) < 0.06
        if d["published"].get("active_b"):  # counted the way the author counts (main layers, +/- LM head, +/- embedding)
            assert abs(d["derived"]["published_active_ratio"] - 1) < 0.08


@pytest.mark.parametrize("slug", SLUGS)
def test_descriptor_is_reproducible(slug):
    """Committed YAML equals a fresh ingest of the committed config snapshot."""
    repo = dict(l.split("\t") for l in open(os.path.join(ROOT, "models", "repos.tsv")).read().split("\n")[1:] if l)[slug]
    fresh = ingest.build(slug, repo, ROOT)
    assert json.loads(json.dumps(fresh)) == json.loads(json.dumps(desc(slug)))


def layers_of(d, attn_pred):
    out, i = [], 0
    for r in d["layers"]:
        for _ in range(r["n"]):
            if attn_pred(d["attn_types"][r["attn"]]):
                out.append(i)
            i += 1
    return out


def test_deepseek_matches_published_facts():
    d = desc("deepseek_v4_1_flash")
    # README: "890 bytes per token" of global KV. Reproduced exactly from the code semantics (FP4 KV, FP4 index keys).
    b = S.kv_breakdown(d, 1 << 20)
    assert (b["global"] + b["index"]) / (1 << 20) == pytest.approx(890.0, abs=0.01)
    # HF tensor dtype counts: I8 (packed FP4) = 557.17 G = 40 x 384 + 3 x 128 experts
    assert d["derived"]["params_expert_b"] == pytest.approx(557.2, abs=0.2)
    # CED: prefill runs only the 20-layer encoder; README says 8B vs 16B
    assert d["dims"]["encoder_layers"] == 20
    assert d["derived"]["params_active_prefill_b"] == pytest.approx(8, rel=0.1)
    assert layers_of(d, lambda a: a.get("kv_role") == "own" and a.get("sparse")) == [2, 8, 14, 20]
    assert layers_of(d, lambda a: a.get("sparse", {}).get("index_role") == "reindex") == [24, 28, 32, 36]
    assert all("candidate_pool" in a["sparse"] for a in d["attn_types"].values() if a.get("sparse", {}).get("index_role") == "reindex")


def test_other_resolved_facts():
    assert abs(desc("muse_spark_open")["derived"]["params_total_b"] - 816) < 6
    assert abs(desc("mistral_large_4")["derived"]["params_active_b"] - 52) < 3
    # Ling: MLA iff (i+1) % 8 == 0 (modeling code + README "63 KDA + 9 MLA")
    ling = desc("ling_3_1_flash")
    assert layers_of(ling, lambda a: a["kind"] == "mla") == list(range(7, 72, 8))
    # Mistral: layers 39, 43, ..., 71 read layer 35's KV (params.json kv_sharing)
    assert layers_of(desc("mistral_large_4"), lambda a: a.get("kv_role") == "reuse") == list(range(39, 72, 4))
    # routers confirmed in code
    assert desc("mistral_large_4")["ffn_types"]["moe"]["router"] == {"scoring": "softmax", "correction_bias": False, "norm_topk": True, "routed_scale": 1.0}
    assert desc("muse_spark_open")["ffn_types"]["moe"]["router"]["scoring"] == "sigmoid"
    assert desc("muse_spark_open")["ffn_types"]["moe"]["router"]["correction_bias"] is True
    assert desc("qwen3_8_flash_next")["ffn_types"]["moe"]["router"]["scoring"] == "softmax"
    # Qwen: 51B n-gram embedding stated in the README
    assert desc("qwen3_8_flash_next")["derived"]["params_table_b"] == pytest.approx(51.2, abs=0.1)
    # MiMo: MTP layers in the main weights are dense; the DFlash drafter is a separate model
    mimo = desc("mimo_v2_6_flash")
    assert mimo["mtp"]["ffn"] == "dense" and mimo["drafter"]["in_main_weights"] is False
    assert abs(mimo["derived"]["params_total_b"] - 309) < 3


def test_hyper_connection_support_is_recorded():
    hc_models = ("deepseek_v4_1_flash", "glm_5_3_flash", "qwen3_8_flash_next")
    for slug in SLUGS:
        d = desc(slug)
        sev = {f["id"]: f["severity"] for f in d["flags"]}
        assert "unsupported" not in sev.values(), slug
        if slug in hc_models:
            assert sev["hyper_connections"] == "required"
            assert d["extras"]["hyper_connections"]["mult"] == 4
        else:
            assert "hyper_connections" not in d.get("extras", {})
    assert desc("deepseek_v4_1_flash")["extras"]["hyper_connections"]["variant"] == "mhc_sinkhorn"
    assert desc("glm_5_3_flash")["extras"]["hyper_connections"]["variant"] == "mhc_sinkhorn"
    assert desc("qwen3_8_flash_next")["extras"]["hyper_connections"]["variant"] == "gated_lowrank"
    assert any(f["id"] == "engram_tables" and f["severity"] == "required" for f in desc("deepseek_v4_1_flash")["flags"])
    assert any(f["id"] == "ngram_embedding" and f["severity"] == "required" for f in desc("qwen3_8_flash_next")["flags"])


def test_hc_cost_model():
    h = 5120
    hc = desc("deepseek_v4_1_flash")["extras"]["hyper_connections"]
    o = S.hc_ops(hc, h)
    assert o["macs_proj"] == 24 * 4 * h          # [(2+n)n x n*h] projection
    assert S.hc_params_per_sublayer(hc, h) == 24 * 4 * h + 24 + 3
    q = desc("qwen3_8_flash_next")["extras"]["hyper_connections"]
    assert S.hc_ops(q, 2560)["macs_proj"] == 2 * 4 * 2560 * 320 + 4 * 2560 * 4


def test_kv_scaling():
    for slug in SLUGS:
        d = desc(slug)
        a, b, c = (S.kv_bytes(d, n) for n in (8192, 131072, 1048576))
        assert 0 < a < b < c
    # pure sliding-window layers do not grow with context; MLA latent cache grows linearly
    glm = desc("glm_5_3")
    assert S.kv_bytes(glm, 2 * 131072) == pytest.approx(2 * S.kv_bytes(glm, 131072), rel=1e-6)
    ling = desc("ling_3_1_flash")  # mostly linear attention: far below GLM per token
    assert S.kv_bytes(ling, 131072) < S.kv_bytes(glm, 131072) / 4


def test_validator_rejects_bad_descriptors():
    d = desc("glm_5_3")
    bad = json.loads(json.dumps(d))
    bad["layers"][0]["attn"] = "nope"
    with pytest.raises(S.SchemaError):
        S.validate(bad)
    bad = json.loads(json.dumps(d))
    bad["dims"]["n_layers"] += 1
    with pytest.raises(S.SchemaError):
        S.validate(bad)
    bad = json.loads(json.dumps(d))
    bad["weights"]["dtypes"]["expert"] = "fp3"
    with pytest.raises(S.SchemaError):
        S.validate(bad)


def test_minimax_msa_and_nvfp4_experts():
    """Facts checked on the MSA paper (arXiv 2606.13392) and on the safetensors headers of the HF repo."""
    d = desc("minimax_m3_1_flash")
    assert d["weights"]["dtypes"]["expert"] == "nvfp4"
    assert abs(d["derived"]["weight_check_ratio"] - 1) < 0.005          # language-model tensors: 248.37 GB on disk
    sp = d["attn_types"]["gqa_sparse"]["sparse"]
    assert (sp["n_heads"], sp["k_heads"], sp["head_dim"], sp["block_size"], sp["topk"]) == (4, 1, 128, 128, 2048)
    assert (sp["score"], sp["local_blocks"], sp["init_blocks"]) == ("max", 1, 0)
    # index projections per layer: q 6144 x (4 x 128) and one shared k 6144 x 128, no per-head weight vector
    h = d["dims"]["hidden"]
    a = d["attn_types"]["gqa_sparse"]
    base = h * 64 * 128 + 2 * h * 4 * 128 + 64 * 128 * h
    assert S._attn_params(a, h) - base == h * 4 * 128 + h * 128
    # decode reads every index key: 60 layers x 128 dims per token, NVFP4 as served by vLLM (0.5625 B/element)
    kb = S.kv_breakdown(d, 1 << 20)
    assert kb["index"] == 60 * 128 * 0.5625 * (1 << 20)
    assert sp["key_dtype"] == "nvfp4" and d["attn_types"]["gqa_sparse"]["kv_dtype"] == "nvfp4"
    # 'approximately 23B activated' counts the main layers without the 1.23 B LM head
    assert d["published"]["active_convention"] == "main"
    assert S.active_by_convention(d, "main") / 1e9 == pytest.approx(23.5, abs=0.1)
    # the DSpark head taps 5 target layers
    dr = d["drafter"]
    assert dr["target_layer_ids"] == [3, 17, 31, 45, 59] and dr["fc_in"] == 5 * h
    ids = {f["id"]: f for f in d["flags"]}
    assert ids["experts_stored_nvfp4"]["severity"] == "required"


def test_drafters_match_their_safetensors():
    """Drafter parameter/byte models reproduce the headers of the published drafter files."""
    mm = desc("minimax_m3_1_flash")["drafter"]
    assert S.drafter_bytes(mm) == pytest.approx(2_301_852_674, rel=0.005)        # dspark/model-dspark-00001 (MXFP8 + BF16)
    mi = desc("mimo_v2_6_flash")["drafter"]
    assert S.drafter_params(mi)["total"] == pytest.approx(1_468_057_152, rel=0.001)  # dflash_draft_model.safetensors
    ml = desc("mistral_large_4")
    assert ml["drafter"]["in_main_weights"] is True                              # mtp_dspark.* in consolidated-00054
    assert S.drafter_bytes(ml["drafter"]) == pytest.approx(4_199_938_048, rel=0.002)
    assert ml["drafter"]["target_layer_ids"] == [1, 18, 36, 54, 71]


def test_step5_from_code_and_headers():
    d = desc("step_5_preview")
    assert abs(d["derived"]["weight_check_ratio"] - 1) < 0.005
    assert d["weights"]["dtypes"]["dense"] == "bf16" and d["weights"]["duplicate_bytes"] == 2_733_196_800
    i, bf16 = 0, []
    for r in d["layers"]:
        for _ in range(r["n"]):
            if d["ffn_types"][r["ffn"]].get("expert_dtype") == "bf16":
                bf16.append(i)
            i += 1
    assert bf16 == [88, 89, 90]                                                   # quantization_config.modules_to_not_convert
    sp = d["attn_types"]["full"]["sparse"]
    assert (sp["key_pool"], sp["topk"], sp["groups"], sp["score"], sp["key_gate"]) == (8, 4096, 4, "weighted_relu", True)
    assert d["mtp"]["ffn"] == "dense" and d["mtp"]["own_lm_head"] is True


def test_spec_decode_recorded():
    expect = {"deepseek_v4_1_flash": "dspark", "minimax_m3_1_flash": "dspark", "mistral_large_4": "dspark",
              "mimo_v2_6_flash": "dflash", "mimo_v2_6_pro": "dflash", "muse_spark_open": None}
    for slug in SLUGS:
        sd = desc(slug).get("spec_decode")
        if slug in expect and expect[slug] is None:
            assert sd is None
            continue
        assert sd["method"] == expect.get(slug, "mtp") and sd["draft_tokens"] >= 1
    assert desc("minimax_m3_1_flash")["spec_decode"]["draft_tokens"] == 7


def test_non_text_bytes_measured():
    assert desc("minimax_m3_1_flash")["weights"]["non_text_bytes"] == 1_734_145_024
    assert desc("glm_5_3")["weights"]["non_text_bytes"] == 0
