"""pytest: op-graph generator, speculative-decoding model, roofline (Phase 1 steps 2-4).  Run: make pytest"""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "sw"), os.path.join(ROOT, "sim")]
from npumodel import schema as S, opgraph as G, specdec  # noqa: E402
from analytic import hw as HW, roofline as R  # noqa: E402

SLUGS = [l.split("\t")[0] for l in open(os.path.join(ROOT, "models", "repos.tsv")).read().split("\n")[1:] if l]


def desc(slug):
    return S.load(os.path.join(ROOT, "models", "descriptors", f"{slug}.yaml"))


def test_specdec_reproduces_table1():
    for method, tab in specdec.METHOD_TABLE.items():
        for dom, vals in specdec.TABLE1_QWEN3_8B[tab].items():
            assert specdec.tau(method, dom, 7) == pytest.approx(sum(vals) / 3, abs=0.01)
    assert specdec.tau("dspark", "code", 0) == 1.0
    t = [specdec.tau("dflash", "chat", g) for g in range(8)]
    assert all(b > a for a, b in zip(t, t[1:]))


@pytest.mark.parametrize("slug", SLUGS)
def test_decode_batch1_reads_the_active_weights(slug):
    """At batch 1 one token touches top-k experts, so the op graph reads exactly the active weights."""
    d = desc(slug)
    c = S.count_params(d)
    ops = G.build(d, "decode", 1, 4096)
    w = G.totals(ops)["w_bytes"]
    dt = d["weights"]["dtypes"]
    # active params at the dense dtype is a lower bound (experts may be 4-bit; LM head / router are bf16)
    lo = (c["active"] - d["dims"]["vocab"] * d["dims"]["hidden"]) * min(S.DTYPE_BYTES[dt["expert"]], S.DTYPE_BYTES[dt["dense"]])
    hi = c["active"] * 2.0 + c["hc"] * 4.0   # everything at bf16, hyper-connection mixers at fp32
    assert lo <= w <= hi
    assert sum(o["experts_touched"] for o in ops if o["kind"] == "moe_experts") == pytest.approx(
        sum(f["top_k"] for _, f in S.expand_layers(d) if f["kind"] == "moe"))


def test_minimax_index_scan_matches_cache():
    d = desc("minimax_m3_1_flash")
    ops = G.build(d, "decode", 1, 1 << 20)
    scan = sum(o["kv_bytes"] for o in ops if o["kind"] == "index_scan")
    assert scan == pytest.approx(S.kv_breakdown(d, 1 << 20)["index"])
    attn = sum(o["kv_bytes"] for o in ops if o["kind"] == "attn")
    assert scan / attn == pytest.approx(64, rel=0.01)   # every index key vs 2048 selected tokens (same NVFP4 dtype)


def test_prefill_causal_triangle():
    d = desc("glm_5_3")   # MLA, every layer attends with a sparse top-2048 selection
    a = d["attn_types"]["mla_own"]
    ops = G.attention_ops(a, 0, d["dims"]["hidden"], "prefill", 1, 1000, 1, d["weights"]["dtypes"], "fp8", "fp8")
    core = next(o for o in ops if o["kind"] == "attn")
    H, dqk, dv = a["n_heads"], a["kv_lora_rank"] + a["qk_rope_dim"], a["kv_lora_rank"]
    assert core["flops"] == pytest.approx(2 * H * (1000 * 1001 / 2) * (dqk + dv))   # ctx < top-k: plain causal


def test_moe_experts_touched_saturates():
    d = desc("deepseek_v4_1_flash")
    f = d["ffn_types"]["moe"]
    small = next(o for o in G.ffn_ops(f, 0, d["dims"]["hidden"], 1, d["weights"]["dtypes"]) if o["kind"] == "moe_experts")
    big = next(o for o in G.ffn_ops(f, 0, d["dims"]["hidden"], 100000, d["weights"]["dtypes"]) if o["kind"] == "moe_experts")
    assert small["experts_touched"] == pytest.approx(f["top_k"])
    assert big["experts_touched"] == pytest.approx(f["n_experts"], rel=1e-6)


def test_hw_baseline_numbers():
    hw = HW.make()
    p = HW.peaks(hw)
    assert p["peak_fp8"] == pytest.approx(1.573e15, rel=1e-3)        # 32 cores x 16,384 FP8 MAC x 1.5 GHz x 2
    assert p["peak_hbm_bw"] == pytest.approx(4.915e12, rel=1e-3)     # 4 x HBM3e at 9.6 Gb/s/pin
    assert p["hbm_bytes"] == 144e9
    assert HW.ops_per_byte(hw) == pytest.approx(301, abs=1)
    with pytest.raises(KeyError):
        HW.make(stacks_per_die=4)


def test_roofline_batch1_is_memory_bound():
    d = desc("mimo_v2_6_flash")
    hw = HW.make()
    r = R.decode(d, hw, 1, 1, 8192, pp=1, tp=2)
    br = r["breakdown"]
    assert br["memory"] > 0.95 * (br["memory"] + br["compute"])
    t = G.totals(G.build(d, "decode", 1, 8192))
    floor = (t["w_bytes"] / 2 + t["kv_bytes"] / 2) / HW.peaks(hw)["hbm_bw"]   # TP=2 splits weights and KV over 2 dies
    assert r["step_s"] >= floor * 0.99


def test_capacity_and_package_count():
    hw = HW.make()
    muse = desc("muse_spark_open")   # 820 B in BF16 = 1.64 TB: needs at least 8 packages of 288 GB
    n, _, _ = R.min_packages(muse, hw, 1, 8192)
    assert n == 8
    n8, _, _ = R.min_packages(muse, hw, 1, 8192, weight_dtype={"dense": "fp8", "expert": "fp8", "table": "fp8"})
    assert n8 == 4
    q = desc("qwen3_8_flash_next")
    small = R.capacity(q, hw, 2, 1, 2, 1, 8192)
    large = R.capacity(q, hw, 2, 1, 2, 64, 131072)
    assert large["kv"] > small["kv"] and large["weights"] == small["weights"]


def test_spec_decode_helps_memory_bound_decode():
    d = desc("minimax_m3_1_flash")
    hw = HW.make()
    r0 = R.best_decode(d, hw, 4, 16, 131072, objective="tok_s_user")
    r7 = R.best_decode(d, hw, 4, 16, 131072, gamma=7, objective="tok_s_user")
    assert r7["tau"] == pytest.approx(specdec.tau("dspark", "code", 7))
    assert r7["tok_s_user"] > 1.5 * r0["tok_s_user"]
    assert r7["step_s"] > r0["step_s"]          # a verify + draft round costs more than one plain step
