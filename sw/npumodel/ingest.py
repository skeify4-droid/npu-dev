"""Normalize HF config.json snapshots (models/configs) into descriptors (models/descriptors).

Usage:  PYTHONPATH=sw python3 -m npumodel.ingest [--root REPO_ROOT]
One adapter per model family, selected by `model_type`. Anything an adapter cannot express in the
base schema is recorded in `flags` (severity: info | warn | required | unsupported), never silently
dropped. Facts that are not in config.json were verified against the model's published code, README
or technical report; each descriptor lists them in `sources` (full write-up: docs/reports/model_features.md).
"""
import argparse
import csv
import hashlib
import json
import os
import sys

from . import schema as S

HF = "https://huggingface.co/"


def flag(i, sev, detail):
    return {"id": i, "severity": sev, "detail": detail}


def rle(pairs):
    out = []
    for a, f in pairs:
        if out and out[-1]["attn"] == a and out[-1]["ffn"] == f:
            out[-1]["n"] += 1
        else:
            out.append({"n": 1, "attn": a, "ffn": f})
    return out


def moe(n, k, d, ns=0, ds=0, scoring="sigmoid", bias=True, norm_topk=True, **kw):
    return {"kind": "moe", "n_experts": n, "top_k": k, "d_expert": d, "n_shared": ns, "d_shared": ds,
            "router": {"scoring": scoring, "correction_bias": bias, "norm_topk": norm_topk, **kw}}


def dense(d):
    return {"kind": "dense", "d_ff": d}


# ---------------------------------------------------------------- adapters
def deepseek_v41(cfg, flags):
    t = cfg["text_config"]
    L = t["num_hidden_layers"]
    ratios = t["compress_ratios"]
    kv_src, idx_src = set(t["kv_source_layer_ids"]), set(t["index_source_layer_ids"])
    cand = t["candidate_source_layer_id"]
    pool = t["candidate_topk_blocks"] * t["candidate_block_size"]
    base = dict(kind="gqa", n_heads=t["num_attention_heads"], n_kv_heads=t["num_key_value_heads"], head_dim=t["head_dim"],
                kv_k_eq_v=True, rope_dim=t["qk_rope_head_dim"], q_lora_rank=t["q_lora_rank"], o_lora_rank=t["o_lora_rank"],
                o_groups=t["o_groups"], window=t["sliding_window"], window_dtype="fp8")
    at, plan = {"swa": dict(base)}, []
    for i in range(L):
        r = ratios[i]
        if not r:
            plan.append("swa")
            continue
        own = i in kv_src
        role = "own" if own else ("reindex" if i in idx_src else "reuse")
        mode = {"own": "full", "reindex": "reindex", "reuse": "reuse"}[role]
        name = f"csa_r{r}_{mode}"
        if name not in at:
            sp = {"kind": "indexer", "n_heads": t["index_n_heads"], "head_dim": t["index_head_dim"], "topk": t["index_topk"],
                  "index_role": role, "key_dtype": "mxfp4", "q_in": t["q_lora_rank"], "k_in": t["head_dim"]}
            if role == "reindex" and 0 <= cand < i:
                sp["candidate_pool"] = pool
            at[name] = dict(base, kv_compress_ratio=r, kv_role="own" if own else "reuse", kv_dtype="nvfp4", sparse=sp)
        plan.append(name)
    ffn = {"moe": moe(t["n_routed_experts"], t["num_experts_per_tok"], t["moe_intermediate_size"], t["n_shared_experts"],
                      t["moe_intermediate_size"], t["scoring_func"], routed_scale=t["routed_scaling_factor"]),
           "moe_dspark": moe(t["dspark_n_routed_experts"], t["dspark_num_experts_per_tok"], t["moe_intermediate_size"],
                             t["n_shared_experts"], t["moe_intermediate_size"], t["scoring_func"],
                             routed_scale=t["routed_scaling_factor"])}
    eng = t["engram_layer_ids"]
    n_cols = (t["engram_max_ngram_size"] - 1) * t["engram_n_heads"]
    tables = [{"name": f"engram_L{l}", "layer": l, "entries": e, "dim": t["engram_head_dim"], "lookups_per_token": n_cols,
               "lookup": "hashed_ngram_gather", "ngram_sizes": list(range(2, t["engram_max_ngram_size"] + 1)),
               "proj_params": n_cols * t["engram_head_dim"] * t["hidden_size"] * (t["hc_mult"] + 1)}
              for l, e in zip(eng, t["engram_num_embeddings"])]
    n_dk = t["num_nextn_predict_layers"]
    flags += [
        flag("hyper_connections", "required", f"mHC with {t['hc_mult']} residual streams: per sublayer an fp32 [{(2 + t['hc_mult']) * t['hc_mult']} x "
             f"{t['hc_mult']}d] mixing projection, Sinkhorn ({t['hc_sinkhorn_iters']} iters on a {t['hc_mult']}x{t['hc_mult']} matrix), pre-collapse and "
             "post-expand. The coefficients computed by one sublayer are consumed by the next (Single-Pass mHC). See docs/spec/hyper_connections.md."),
        flag("engram_tables", "required", f"Hashed n-gram embedding on layers {eng}: {n_cols} FP8 rows of {t['engram_head_dim']} per token per layer from a "
             f"{sum(x['entries'] for x in tables) / 1e6:.0f} M-row table ({sum(x['entries'] * x['dim'] for x in tables) / 1e9:.0f} G elements, MXFP8), then a "
             "linear projection and a per-stream sigmoid gate into the residual. Needs a hashed-gather DMA."),
        flag("causal_encoder_decoder", "required", f"CED: layers 0..{L // 2 - 1} are the encoder, {L // 2}..{L - 1} the decoder. Prefill runs only the encoder "
             f"(8B active vs 16B in decode); decoder global KV is projected from the encoder's last hidden state; decoder SWA KV is rebuilt by replaying only "
             f"the last {t['sliding_window']} prompt tokens (SWA Bounded Replay). The scheduler and compiler need per-phase layer ranges."),
        flag("csa2_modes", "required", "Every attention layer attends over a 128-token raw window plus top-512 entries of the global cache. Full layers "
             f"(ids {sorted(kv_src)}) build the cache and the indexer; Reindex layers (ids {sorted(idx_src - kv_src)}) rescore the shared index keys with their own "
             f"query; Reuse layers read the previous top-k. Reindex layers only search a {pool}-position candidate pool (Hierarchical Sparse Indexer)."),
        flag("fp4_kv_cache", "required", "Global KV is FP4 E2M1 with an E4M3 scale per 16 channels (0.5625 B/element); indexer K/Q are FP4 with an E8M0 scale per 32; "
             "the 128-token window stays FP8. 890 B of global KV per token, reproduced by this descriptor."),
        flag("dspark_in_weights", "warn", f"{n_dk} DSpark draft layers are in the main weights ({t['dspark_n_routed_experts']} experts, top-{t['dspark_num_experts_per_tok']}, FP4), "
             f"block size {t['dspark_block_size']}, with a Markov head and a confidence head. Modeled as MTP layers; the draft/verify schedule is not."),
        flag("router_sqrtsoftplus", "info", "Router score = sqrt(softplus(logit)); a bias selects experts but does not scale them; top-k weights normalized then x1.5."),
        flag("mqa_kv_equals_v", "info", "MQA with one 512-wide KV head; K and V are the same vector. Grouped low-rank output projection (o_lora 1024, 8 groups)."),
        flag("vision_tower", "info", "Vision tower excluded (text decode only)."),
    ]
    return dict(hidden=t["hidden_size"], vocab=t["vocab_size"], n_layers=L, enc=L // 2, max_ctx=t["max_position_embeddings"],
                tie=t["tie_word_embeddings"], dtypes=dict(expert="mxfp4", dense="fp8", embed="bf16", table="mxfp8", hc="fp32"),
                attn=at, ffn=ffn, layers=rle([(n, "moe") for n in plan]), mtp=dict(n_layers=n_dk, attn="swa", ffn="moe_dspark"),
                extras=dict(hyper_connections={"variant": "mhc_sinkhorn", "mult": t["hc_mult"], "sinkhorn_iters": t["hc_sinkhorn_iters"]}, tables=tables),
                spec_decode=dict(method="dspark", draft_tokens=t["dspark_block_size"], in_weights="mtp", target_layer_ids=t["dspark_target_layer_ids"],
                                 markov_rank=t["dspark_markov_rank"], source="config dspark_*; DSpark paper (arXiv 2607.05147) production DSpark-5"),
                published=dict(total_b=748, active_b=16, source="tech report abstract: 552B backbone + 196B Engram; 16B active in decode, 8B in prefill"),
                sources=["deepseek-ai/DeepSeek-V4.1-Flash/inference/model.py", "deepseek-ai/DeepSeek-V4.1-Flash/inference/engram.py",
                         "deepseek-ai/DeepSeek-V4.1-Flash/DeepSeek_V41_Tech_Report.pdf (sec 2.2, 2.3)", "README: 890 B/token global KV"],
                notes="Dense FP8 (32x32 UE8M0 scales), experts FP4 (scale per 32), Engram FP8 (scale per 32); mHC weights FP32.")


def _glm_mla(t, role, nope, rope):
    a = dict(kind="mla", n_heads=t["num_attention_heads"], q_lora_rank=t["q_lora_rank"], kv_lora_rank=t["kv_lora_rank"],
             qk_nope_dim=nope, qk_rope_dim=rope, v_head_dim=t["v_head_dim"])
    a["sparse"] = {"kind": "indexer", "n_heads": t["index_n_heads"], "head_dim": t["index_head_dim"], "topk": t["index_topk"],
                   "index_role": role, "q_in": t["q_lora_rank"]}
    if t.get("index_kpool_compress"):
        a["sparse"]["key_pool"] = t["index_kpool"]
    return a


def glm_moe_dsa(cfg, flags):
    t = cfg
    L = t["num_hidden_layers"]
    at = {"mla_own": _glm_mla(t, "own", t["qk_nope_head_dim"], t["qk_rope_head_dim"]),
          "mla_shr": _glm_mla(t, "reuse", t["qk_nope_head_dim"], t["qk_rope_head_dim"])}
    ffn = {"dense": dense(t["intermediate_size"]),
           "moe": moe(t["n_routed_experts"], t["num_experts_per_tok"], t["moe_intermediate_size"], t["n_shared_experts"],
                      t["moe_intermediate_size"], t["scoring_func"], routed_scale=t["routed_scaling_factor"])}
    pairs = [("mla_own" if t["indexer_types"][i] == "full" else "mla_shr",
              "dense" if t["mlp_layer_types"][i] == "dense" else "moe") for i in range(L)]
    flags += [flag("dsa_indexer", "warn", f"DeepSeek-Sparse-Attention lightning indexer, top-{t['index_topk']} tokens. "
                   f"{t['indexer_types'].count('full')} layers compute it, the rest reuse the previous result (per indexer_types).")]
    return dict(hidden=t["hidden_size"], vocab=t["vocab_size"], n_layers=L, max_ctx=t["max_position_embeddings"],
                tie=t["tie_word_embeddings"], dtypes=dict(expert="fp8", dense="fp8", embed="bf16", table="fp8"),
                attn=at, ffn=ffn, layers=rle(pairs),
                mtp=dict(n_layers=t["num_nextn_predict_layers"], attn="mla_own", ffn="moe"), extras={},
                spec_decode=dict(method="mtp", draft_tokens=t["num_nextn_predict_layers"], in_weights="mtp", source="config num_nextn_predict_layers"),
                published=dict(total_b=753.3, active_b=None, source="Hugging Face safetensors parameter count (no figure in the README)"), sources=["config.json + weight-size check"], notes="FP8 e4m3 128x128 block scales.")


def glm5_next(cfg, flags):
    t = cfg["text_config"]
    L = t["num_hidden_layers"]
    la = t["linear_attn_config"]
    hc = {"variant": "mhc_sinkhorn", "mult": t["hc_mult"], "sinkhorn_iters": t["hc_sinkhorn_iters"]}
    at = {"kda": dict(kind="linear", variant="kda", n_k_heads=la["num_heads"], n_v_heads=la["num_heads"], k_dim=la["head_dim"],
                      v_dim=la["head_dim"], conv_kernel=la["short_conv_kernel_size"], f_gate="lowrank", out_gate="lowrank",
                      gate_rank=la["head_dim"], w_dtype="bf16"),
          "mla_nr": _glm_mla(t, "own", t["qk_nope_head_dim"], t["qk_rope_head_dim"])}
    ffn = {"dense": dense(t["intermediate_size"]),
           "moe": moe(t["n_routed_experts"], t["num_experts_per_tok"], t["moe_intermediate_size"], t["n_shared_experts"],
                      t["moe_intermediate_size"], t["scoring_func"], routed_scale=t["routed_scaling_factor"])}
    pairs = [("kda" if t["layer_types"][i] == "linear_attention" else "mla_nr",
              "dense" if t["mlp_layer_types"][i] == "dense" else "moe") for i in range(L)]
    flags += [
        flag("hyper_connections", "required", f"mHC, same form as DeepSeek V4.1: fp32 [{(2 + hc['mult']) * hc['mult']} x {hc['mult']}d] projection per sublayer "
             f"plus Sinkhorn ({hc['sinkhorn_iters']} iters), and a final 'hyper head' collapse. See docs/spec/hyper_connections.md."),
        flag("kda_linear_attention", "warn", f"{t['layer_types'].count('linear_attention')} Kimi-Delta-style linear-attention layers: q/k/v/o full size, "
             "forget and output gates low-rank (rank 128), short conv, chunked delta-rule scan. All their projections are BF16 "
             "(quantization_config.modules_to_not_convert); the MLA and MoE weights are FP8."),
        flag("indexer_key_pooling", "warn", f"Indexer keys are pooled by {t['index_kpool']} with a learned gate (index_kpool); the tail block is always selected."),
        flag("mla_without_rope", "info", "MLA layers use qk_rope_head_dim=0 (mla_use_nope): all 256 query/key dims are content dims."),
        flag("vision_tower", "info", "Vision tower excluded."),
    ]
    return dict(hidden=t["hidden_size"], vocab=t["vocab_size"], n_layers=L, max_ctx=t["max_position_embeddings"],
                tie=t["tie_word_embeddings"], dtypes=dict(expert="fp8", dense="fp8", embed="bf16", table="fp8", hc="fp32"),
                attn=at, ffn=ffn, layers=rle(pairs), mtp=dict(n_layers=t["num_nextn_predict_layers"], attn="mla_nr", ffn="moe"),
                extras=dict(hyper_connections=hc),
                spec_decode=dict(method="mtp", draft_tokens=t["num_nextn_predict_layers"], in_weights="mtp", source="config num_nextn_predict_layers"),
                published=dict(total_b=320, active_b=18, source="README"),
                sources=["transformers modeling_glm5_next.py (HyperConnection, LinearAttention, Indexer)", "zai-org/GLM-5.3-Flash README"],
                notes="FP8 e4m3; hyper-connection and norm weights kept in higher precision.")


def mimo_v2(cfg, flags, drafter_cfg):
    t = cfg
    L = t["num_hidden_layers"]
    rope = int(round(t["partial_rotary_factor"] * t["head_dim"]))
    at = {"full": dict(kind="gqa", n_heads=t["num_attention_heads"], n_kv_heads=t["num_key_value_heads"], head_dim=t["head_dim"],
                       v_head_dim=t["v_head_dim"], rope_dim=rope, o_dtype="bf16"),
          "swa": dict(kind="gqa", n_heads=t["swa_num_attention_heads"], n_kv_heads=t["swa_num_key_value_heads"], head_dim=t["swa_head_dim"],
                      v_head_dim=t["swa_v_head_dim"], rope_dim=rope, window=t["sliding_window"], sink_bias=True, o_dtype="bf16")}
    ns = t.get("n_shared_experts") or 0
    ffn = {"dense": dense(t["intermediate_size"]),
           "moe": moe(t["n_routed_experts"], t["num_experts_per_tok"], t["moe_intermediate_size"], ns, t["moe_intermediate_size"], t["scoring_func"])}
    pat, freq = t["hybrid_layer_pattern"], t["moe_layer_freq"]
    pairs = [("swa" if pat[i] else "full", "moe" if freq[i] else "dense") for i in range(L)]
    n_mtp = t.get("num_nextn_predict_layers") or 0
    d = drafter_cfg
    dc = d["dflash_config"]
    drafter = {"kind": "dflash", "in_main_weights": False, "n_layers": d["num_hidden_layers"], "hidden": d["hidden_size"], "d_ff": d["intermediate_size"],
               "attn": {"kind": "gqa", "n_heads": d["num_attention_heads"], "n_kv_heads": d["num_key_value_heads"], "head_dim": d["head_dim"]},
               "window": d["sliding_window"], "block": d["block_size"], "target_layer_ids": dc["target_layer_ids"],
               "fc_in": len(dc["target_layer_ids"]) * d["hidden_size"], "dtype": "bf16", "aux_dtype": "bf16",
               "shares_target_embed_and_lm_head": True, "source": "dflash/config.json, dflash/dflash.py, dflash safetensors header"}
    flags += [flag("attention_sink_bias", "info", "SWA layers add a learned sink logit to the softmax."),
              flag("hybrid_pattern", "info", f"hybrid_layer_pattern: 1 = sliding window ({pat.count(1)} layers), 0 = full ({pat.count(0)}). Layer 0 is full attention with a dense FFN."),
              flag("separate_drafter", "warn", f"Speculative decoding uses a separate {d['num_hidden_layers']}-layer DFlash drafter (SWA window {d['sliding_window']}, "
                   f"block {d['block_size']}, reads target hidden states) that is not in the main weights."),
              flag("experts_stored_fp4", "warn", "quantization_config says FP8, but the safetensors expert tensors are packed 4-bit (U8). The HF tensor dtype counts and the "
                   "total-size check both agree. Modeled as MXFP4 experts."),
              flag("modalities", "info", "Vision and audio towers excluded.")]
    return dict(hidden=t["hidden_size"], vocab=t["vocab_size"], n_layers=L, max_ctx=t["max_position_embeddings"],
                tie=t["tie_word_embeddings"], dtypes=dict(expert="mxfp4", dense="fp8", embed="bf16", table="fp8"),
                attn=at, ffn=ffn, layers=rle(pairs),
                mtp=dict(n_layers=n_mtp, attn="swa", ffn="dense") if n_mtp else None, drafter=drafter, extras={},
                spec_decode=dict(method="dflash", draft_tokens=d["block_size"] - 1, in_weights="drafter",
                                 source="tech report 2.4: 7 tokens per forward pass (block 8); RL rollouts use block 6"),
                published=dict(total_b=309 if n_mtp else 1020, active_b=15 if n_mtp else 42, source="README"),
                sources=["README (params, layer split, drafter)", "dflash/config.json", "dflash/dflash.py (spec_generate)", "MiMo_V2_6_technical_report.pdf (Table 1, 2.4, RL infra)",
                         "model.safetensors.index.json (mtp tensors are dense)"],
                notes="FP8 dense, 4-bit experts (see flag); o_proj kept in BF16 (ignored_layers).")


def bailing_hybrid(cfg, flags):
    t = cfg
    L, g = t["num_hidden_layers"], t["layer_group_size"]
    at = {"mla": dict(kind="mla", n_heads=t["num_attention_heads"], q_lora_rank=t["q_lora_rank"], kv_lora_rank=t["kv_lora_rank"],
                      qk_nope_dim=t["qk_nope_head_dim"], qk_rope_dim=t["qk_rope_head_dim"], v_head_dim=t["v_head_dim"], out_gate="head"),
          "kda": dict(kind="linear", variant="kda", n_k_heads=t["num_attention_heads"], n_v_heads=t["num_attention_heads"], k_dim=t["head_dim"],
                      v_dim=t["head_dim"], conv_kernel=t["short_conv_kernel_size"], f_gate="full", out_gate="full")}
    ffn = {"dense": dense(t["intermediate_size"]),
           "moe": moe(t["num_experts"], t["num_experts_per_tok"], t["moe_intermediate_size"], t["num_shared_experts"],
                      t["moe_shared_expert_intermediate_size"], t["scoring_func"], routed_scale=t["routed_scaling_factor"],
                      n_group=t["n_group"], topk_group=t["topk_group"])}
    pairs = [("mla" if (i + 1) % g == 0 else "kda", "dense" if i < t["first_k_dense_replace"] else "moe") for i in range(L)]
    flags += [flag("layer_pattern", "info", f"Confirmed in modeling_bailing_moe_v3.py and the README (63 KDA + 9 gated MLA, 7:1): layer i is MLA iff (i+1) % {g} == 0."),
              flag("kda_linear_attention", "warn", "Kimi-Delta linear attention: q/k/v/o plus full-size forget (f_proj) and output (g_proj) gates, short conv, chunked scan."),
              flag("group_limited_routing", "info", f"Router picks {t['topk_group']} of {t['n_group']} expert groups first."),
              flag("modalities", "info", "Text path only.")]
    return dict(hidden=t["hidden_size"], vocab=t["vocab_size"], n_layers=L, max_ctx=t["max_position_embeddings"],
                tie=t["tie_word_embeddings"], dtypes=dict(expert="fp8", dense="fp8", embed="bf16", table="fp8"),
                attn=at, ffn=ffn, layers=rle(pairs), mtp=dict(n_layers=t["num_nextn_predict_layers"], attn="mla", ffn="moe"), extras={},
                spec_decode=dict(method="mtp", draft_tokens=t["num_nextn_predict_layers"], in_weights="mtp", source="config num_nextn_predict_layers"),
                published=dict(total_b=560, active_b=25, source="README"),
                sources=["inclusionAI/Ling-3.1-flash/modeling_bailing_moe_v3.py", "README"], notes="FP8 e4m3.")


def minimax_m3(cfg, flags, drafter_cfg):
    t = cfg["text_config"]
    L = t["num_hidden_layers"]
    sp = t["sparse_attention_config"]
    at = {"gqa_sparse": dict(kind="gqa", n_heads=t["num_attention_heads"], n_kv_heads=t["num_key_value_heads"], head_dim=t["head_dim"],
                             rope_dim=t["rotary_dim"], qk_norm=True,
                             sparse={"kind": "block_topk", "n_heads": sp["sparse_num_index_heads"], "head_dim": sp["sparse_index_dim"],
                                     "topk": sp["sparse_topk_blocks"] * sp["sparse_block_size"], "block_size": sp["sparse_block_size"],
                                     "index_role": "own", "k_heads": 1, "score": sp["sparse_score_type"],
                                     "local_blocks": sp["sparse_local_block"], "init_blocks": sp["sparse_init_block"],
                                     "key_dtype": "nvfp4", "q_dtype": "fp8"}, kv_dtype="nvfp4")}
    ffn = {"dense": dense(t["dense_intermediate_size"]),
           "moe": moe(t["num_local_experts"], t["num_experts_per_tok"], t["intermediate_size"], t["n_shared_experts"],
                      t["shared_intermediate_size"], t["scoring_func"], routed_scale=t["routed_scaling_factor"])}
    pairs = [("gqa_sparse", "moe" if t["moe_layer_freq"][i] else "dense") for i in range(L)]
    d = drafter_cfg.get("text_config", drafter_cfg)
    drafter = {"kind": "dspark", "in_main_weights": False, "n_layers": d["num_hidden_layers"], "hidden": d["hidden_size"], "d_ff": d["dspark_ffn_hidden_size"],
               "attn": {"kind": "gqa", "n_heads": d["num_attention_heads"], "n_kv_heads": d["num_key_value_heads"], "head_dim": d["head_dim"]},
               "window": d["sliding_window"], "block": d["dspark_block_size"], "markov_rank": d["dspark_markov_rank"], "vocab": d["vocab_size"],
               "confidence_head": d["enable_confidence_head"], "aux_params": d["hidden_size"] + d["dspark_markov_rank"] + 1,
               "dtype": "mxfp8", "aux_dtype": "bf16", "source": "dspark/config.json, dspark safetensors header",
               "target_layer_ids": d["dspark_target_layer_ids"], "fc_in": len(d["dspark_target_layer_ids"]) * d["hidden_size"],
               "shares_target_embed_and_lm_head": True, "aux_gemm_precision": d["dspark_aux_gemm_precision"], "paper": "arXiv 2607.05147"}
    flags += [flag("block_sparse_attention", "warn", f"MiniMax Sparse Attention: {sp['sparse_topk_blocks']} blocks of {sp['sparse_block_size']} chosen by a "
                   f"{sp['sparse_num_index_heads']} index query heads (one per GQA group) and ONE index key head shared by all groups (arXiv 2606.13392 eq. 5). "
                   f"Every cached token is scored (dot product / sqrt(d_idx)), the score of a block is the max over its tokens, then top-k blocks per group; "
                   f"the query's own (local) block is forced and counts as one of the {sp['sparse_topk_blocks']} slots (init_block={sp['sparse_init_block']}, so no sink block). "
                   "Decode therefore reads all N index keys plus only k*block KV tokens, in every layer. vLLM serves both the KV cache and the index-key cache "
                   "in NVFP4 (--kv-cache-dtype nvfp4, indexer_kv_dtype nvfp4); indexer_decode_cp splits the decode index scan across TP ranks by context."),
              flag("separate_drafter", "warn", f"DSpark draft head ({d['num_hidden_layers']} layers, dense FFN {d['dspark_ffn_hidden_size']}, block {d['dspark_block_size']}, "
                   "Markov head, confidence head) ships in dspark/ and is not in the main weights."),
              flag("experts_stored_nvfp4", "required", "quantization_config says MXFP8, but the routed experts are NVFP4: packed E2M1 (U8, two per byte), an FP8 E4M3 scale per 16 elements "
                   "and an FP32 global weight scale and input scale per expert matrix (checked on the safetensors headers: w1 (3072, 3072) U8 with scale (3072, 384)). "
                   "Attention, dense and shared-expert projections are FP8 E4M3 with an E8M0 (U8) scale per 32 (MXFP8). Router gate is FP32."),
              flag("swiglu_oai", "info", f"hidden_act=swigluoai (alpha {t['swiglu_alpha']}, limit {t['swiglu_limit']}): clamped SwiGLU variant."),
              flag("gemma_norm", "info", "Gemma-style RMSNorm (1+w) and per-head QK norm."),
              flag("modalities", "info", "Vision tower excluded.")]
    return dict(hidden=t["hidden_size"], vocab=t["vocab_size"], n_layers=L, max_ctx=t["max_position_embeddings"],
                tie=t["tie_word_embeddings"], dtypes=dict(expert="nvfp4", dense="mxfp8", embed="bf16", table="fp8"),
                attn=at, ffn=ffn, layers=rle(pairs), mtp=None, drafter=drafter, extras={},
                spec_decode=dict(method="dspark", draft_tokens=d["dspark_block_size"], in_weights="drafter",
                                 source="vLLM recipe: num_speculative_tokens 7 (Blackwell), 3 (MI355X); draft KV fp8"),
                published=dict(total_b=428, active_b=23, active_convention="main",
                               source="README ('approximately 23B activated'); convention inferred: 23.5 B without the 1.23 B LM head, 24.7 B with it"),
                sources=["README", "dspark/config.json", "vLLM recipe (KV and indexer caches NVFP4 on Blackwell, FP8 on Hopper)"],
                notes="MXFP8 dense per quantization_config; routed experts NVFP4 (see flag). KV and index-key caches NVFP4 as served by vLLM on Blackwell.")


def step5(cfg, flags):
    t = cfg["text_config"]
    L = t["num_hidden_layers"]
    kinds = t["layer_types"]
    base = dict(kind="gqa", n_heads=t["num_attention_heads"], n_kv_heads=t["num_attention_groups"], head_dim=t["head_dim"], out_gate="head",
                kv_dtype="bf16", window_dtype="bf16")
    sc = t["sparse_config"]
    rb = sc["region_block_size"]
    at = {"swa": dict(base, window=t["sliding_window"], rope_dim=t["head_dim"]),
          "full": dict(base, rope_dim=int(round(t["head_dim"] / 3)),
                       sparse={"kind": "indexer", "n_heads": sc["sparse_indexer_num_heads"], "head_dim": sc["proxy_dim"], "topk": sc["topk"] * rb,
                               "block_size": rb, "key_pool": rb, "key_gate": True, "k_heads": sc["sparse_indexer_num_k_heads"],
                               "groups": sc["num_provider_groups"], "score": "weighted_relu", "index_role": "own", "key_dtype": "fp8"})}
    moe_set = set(t["moe_layer_list"])
    q = t.get("quantization_config") or cfg.get("quantization_config") or {}
    bf16_exp = sorted({int(p) for n in q.get("modules_to_not_convert", []) if n.startswith("model.layers.") and ".moe.experts." in n
                       for p in n.split(".")[2:3]})
    ffn = {"dense": dense(t["intermediate_size"]),
           "moe": moe(t["moe_num_experts"], t["moe_top_k"], t["moe_intermediate_size"], 1, t["share_expert_dim"], "sigmoid",
                      routed_scale=t["moe_router_scaling_factor"])}
    ffn["moe_bf16"] = dict(ffn["moe"], expert_dtype="bf16")
    pairs = [("swa" if kinds[i] == "sliding_attention" else "full", ("moe_bf16" if i in bf16_exp else "moe") if i in moe_set else "dense") for i in range(L)]
    n_mtp = t["num_nextn_predict_layers"]
    g = sc["num_provider_groups"]
    flags.append(flag("bf16_expert_layers", "info", f"Routed experts of layers {bf16_exp} stay BF16 (quantization_config.modules_to_not_convert); all other experts are FP8."))
    flags += [flag("csa_region_compression", "warn", f"Full-attention layers (CSA): one shared index key (dim {sc['proxy_dim']}) and a gate z per token; every {rb} tokens are pooled "
                   f"into one FP8 region summary by a per-dimension softmax over z (s_d = sum_t softmax_t(z_td) k_td). {sc['sparse_indexer_num_heads']} index query heads in "
                   f"{g} groups (one per KV group) score summaries as sum_h w_h relu(q_h . s); each group takes the top-{sc['topk']} regions "
                   f"({sc['topk'] * rb} tokens) and attends to their raw BF16 K/V plus the unfinished region. Decode scores N/{rb} summaries."),
              flag("head_wise_attn_gate", "info", "Per-head sigmoid gate on attention output."),
              flag("fp32_residual", "info", "fp32 residual stream and fp32 norms (fp32_residual_connection)."),
              flag("mtp_layers_in_layer_types", "info", f"layer_types has {len(kinds)} entries = {L} main + {n_mtp} MTP layers (MTP is in the main weights, "
                   "dense FFN, sliding-window attention, own output head per layer)."),
              flag("router_sigmoid_bias", "info", "Sigmoid routing with a selection-only bias (confirmed in inference/model.py)."),
              flag("modalities", "info", "Vision tower excluded.")]
    return dict(hidden=t["hidden_size"], vocab=t["vocab_size"], n_layers=L, max_ctx=t["max_position_embeddings"], tie=False,
                dtypes=dict(expert="fp8", dense="bf16", embed="bf16", table="fp8"), attn=at, ffn=ffn, layers=rle(pairs),
                mtp=dict(n_layers=n_mtp, attn="swa", ffn="dense", own_lm_head=True), extras={},
                spec_decode=dict(method="mtp", draft_tokens=n_mtp, in_weights="mtp", source="config num_nextn_predict_layers"),
                published=dict(total_b=600, active_b=27, source="README"),
                sources=["stepfun-ai/Step-5-Preview/inference/model.py", "inference/kernel.py (csa_compress_regions, indexer_logits, DSALayerCache)",
                         "safetensors headers (all 26 shards)", "README"],
                notes="Routed experts FP8 e4m3 (128x128 F32 scales); attention, shared experts, dense MLPs and MTP layers BF16 (safetensors headers). "
                      "Each MTP layer has its own output head. KV cache BF16 (reference DSALayerCache), region summaries FP8.")


def mistral_large4(cfg, flags, params):
    t = cfg["text_config"]
    L = t["num_hidden_layers"]
    src = [int(x) for x in params["kv_sharing"]["source_layers_str"].split(",")]
    reuse = {i for i in range(L) if src[i] != i}
    mk = lambda role, kvrole: dict(kind="mla", n_heads=t["num_attention_heads"], q_lora_rank=t.get("q_lora_rank"), kv_lora_rank=t["kv_lora_rank"],
                                   qk_nope_dim=t["qk_nope_head_dim"], qk_rope_dim=t["qk_rope_head_dim"], v_head_dim=t["v_head_dim"],
                                   kv_role=kvrole, kv_dtype="fp8",
                                   sparse={"kind": "indexer", "n_heads": t["index_n_heads"], "head_dim": t["index_head_dim"], "topk": t["index_topk"],
                                           "index_role": role, "key_dtype": "fp8"})
    at = {f"mla_{r}_{k}": mk(r, k) for r in ("own", "reuse") for k in ("own", "reuse")}
    ffn = {"dense": dense(t["intermediate_size"]),
           "moe": moe(t["n_routed_experts"], t["num_experts_per_tok"], t["moe_intermediate_size"], t["n_shared_experts"], t["moe_intermediate_size"],
                      "softmax", bias=False, routed_scale=t["routed_scaling_factor"])}
    pairs = [(f"mla_{'own' if t['indexer_types'][i] == 'full' else 'reuse'}_{'reuse' if i in reuse else 'own'}",
              "dense" if t["mlp_layer_types"][i] == "dense" else "moe") for i in range(L)]
    m = params["mtp"]
    tl = [int(x) for x in m["target_activation_layers"].split(",")]
    h = t["hidden_size"]
    r = m["dspark"]["markov_rank"]
    drafter = {"kind": "dspark", "in_main_weights": True, "n_layers": m["n_layers"], "hidden": h, "d_ff": 24576,
               "attn": {"kind": "mla", "n_heads": 64, "kv_lora_rank": 512, "qk_nope_dim": 64, "qk_rope_dim": 64, "v_head_dim": 128},
               "window": int(m["ragged_attention"].split(",")[0]), "block": m["depth"], "target_layer_ids": tl, "fc_in": len(tl) * h,
               "markov_rank": r, "markov_head": m["dspark"]["head_type"], "vocab": t["vocab_size"], "aux_params": 3 * r * (h + 2 * r) + 3 * r,
               "dtype": "bf16", "aux_dtype": "bf16",
               "source": "params.json mtp; mtp_dspark.* tensors in consolidated-00054 (shapes from the safetensors header)"}
    flags += [flag("dsa_indexer", "warn", f"Indexed sparse attention, top-{t['index_topk']}, FP8 indexer and FP8 KV; {t['indexer_types'].count('full')} layers own an indexer "
                   "(params.json dsa.sharing equals indexer_types)."),
              flag("kv_sharing", "warn", f"params.json kv_sharing: layers {sorted(reuse)} read layer 35's latent KV (their own q/attention still run). Modeled with kv_role=reuse."),
              flag("drafter_in_weights", "info", f"DSpark draft head: {m['n_layers']} dense BF16 MLA layers (64 heads, no q-LoRA, d_ff 24576), {m['dspark']['head_type']} Markov head of rank "
                   f"{r}, target taps at layers {tl}; stored as mtp_dspark.* in the last shard (4.20 GB, inside the published total)."),
              flag("router_softmax", "info", "Router is softmax over fp32 logits with normalized top-k weights and no selection bias (confirmed in transformers modeling_mistral_large4.py)."),
              flag("nope_layers", "info", f"{t['no_rope_layers'].count(0)} layers use no positional rotation (pattern '{params['nope']}')."),
              flag("softmax_scaling", "info", "Query scaling by position (ssmax) and tanh logit soft-capping at 30."),
              flag("modalities", "info", "Vision tower excluded.")]
    return dict(hidden=t["hidden_size"], vocab=t["vocab_size"], n_layers=L, max_ctx=t["max_position_embeddings"], tie=t["tie_word_embeddings"],
                softcap=t["final_logit_softcapping"], dtypes=dict(expert="fp8", dense="fp8", embed="bf16", table="fp8"), attn=at, ffn=ffn,
                layers=rle(pairs), mtp=None, drafter=drafter, extras={},
                spec_decode=dict(method="dspark", draft_tokens=m["depth"], in_weights="drafter", source="params.json mtp.depth"),
                published=dict(total_b=1000, active_b=49, active_convention="main", source="README: ~1T total, 49B active (52B incl. embeddings)"),
                sources=["params.json", "transformers modeling_mistral_large4.py", "README", "consolidated.safetensors.index.json + last-shard header"],
                notes="FP8 128x128 block scales. Weights shipped as consolidated-*.safetensors (no index).")


def muse_spark(cfg, flags):
    t = cfg["text_config"]
    L = t["num_hidden_layers"]
    base = dict(kind="gqa", n_heads=t["num_attention_heads"], n_kv_heads=t["num_key_value_heads"], head_dim=t["head_dim"], rope_dim=t["head_dim"],
                out_gate="elem", qk_norm=True)
    at = {"swa": dict(base, window=t["sliding_window"]), "full": dict(base)}
    ffn = {"moe": moe(t["num_local_experts"], t["num_experts_per_tok"], t["moe_intermediate_size"], 0, 0, "sigmoid", routed_scale=t["routed_scaling_factor"],
                      d_in=t["moe_hidden_size"])}
    ffn["moe"]["d_in"] = t["moe_hidden_size"]
    pairs = [("swa" if t["layer_types"][i] == "sliding_attention" else "full", "moe") for i in range(L)]
    flags += [flag("narrow_expert_width", "warn", f"Experts run at moe_hidden_size={t['moe_hidden_size']} (model dim {t['hidden_size']}) behind pre/post projections with a centered RMSNorm."),
              flag("published_bf16", "warn", "Only BF16 weights are published (1.64 TB); FP8/FP4 deployment needs our own quantization."),
              flag("router_sigmoid_bias", "info", "Router is sigmoid(logits x output_multiplier) with a selection-only correction bias and normalized top-k (confirmed in transformers modeling_muse_spark.py)."),
              flag("residual_gate", "info", f"Learned per-channel residual gates (alpha*x + beta*f(x), temperature {t['residual_gate_temperature']}) replace the plain residual add."),
              flag("custom_scaling", "info", f"Unit-scale QK norm x {t['qk_scale_factor']:.3f}, elementwise sigmoid output gate with output norm, logit soft-cap {t['final_logit_softcapping']}."),
              flag("modalities", "info", "Vision/audio towers excluded.")]
    return dict(hidden=t["hidden_size"], vocab=t["vocab_size"], n_layers=L, max_ctx=t["max_position_embeddings"], tie=t["tie_word_embeddings"],
                softcap=t["final_logit_softcapping"], dtypes=dict(expert="bf16", dense="bf16", embed="bf16", table="bf16"), attn=at, ffn=ffn,
                layers=rle(pairs), mtp=None, extras={}, spec_decode=None,
                published=dict(total_b=816, active_b=42, active_convention="main", source="README; active convention inferred (fits without the LM head)"),
                sources=["transformers modeling_muse_spark.py", "README"],
                notes="BF16 as published; weights fetched from the unsloth mirror (original repo is gated).")


def qwen4_exp(cfg, flags):
    t = cfg["text_config"]
    L = t["num_hidden_layers"]
    hc = {"variant": "gated_lowrank", "mult": t["hc_count"], "lowrank": t["hc_lowrank"]}
    at = {"gdn": dict(kind="linear", variant="gdn", n_k_heads=t["linear_num_key_heads"], n_v_heads=t["linear_num_value_heads"], k_dim=t["linear_key_head_dim"],
                      v_dim=t["linear_value_head_dim"], conv_kernel=t["linear_conv_kernel_dim"], out_gate="full"),
          "full": dict(kind="gqa", n_heads=t["num_attention_heads"], n_kv_heads=t["num_key_value_heads"], head_dim=t["head_dim"],
                       rope_dim=int(t["head_dim"] * t["partial_rotary_factor"]), out_gate="elem",
                       sparse={"kind": "indexer", "n_heads": t["indexer_n_heads"], "head_dim": t["indexer_head_dim"], "topk": t["indexer_budget"],
                               "index_role": "own", "key_pool": t["indexer_compress_ratio"]})}
    ffn = {"moe": moe(t["num_experts"], t["num_experts_per_tok"], t["moe_intermediate_size"], 1, t["shared_expert_intermediate_size"], "softmax", bias=False)}
    pairs = [("gdn" if t["layer_types"][i] == "linear_attention" else "full", "moe") for i in range(L)]
    heads = (t["ngram_size"] - 1) * t["heads_per_ngram"]
    ple_layer = t["ple_layer_ids"][0] - 1
    tables = [{"name": "ngram_embedding", "layer": ple_layer, "entries": heads * t["ngram_vocab_size_base"], "dim": t["ple_embed_dim"] // heads,
               "lookups_per_token": heads, "lookup": "hashed_ngram_gather", "ngram_sizes": list(range(2, t["ngram_size"] + 1)),
               "proj_params": t["ple_embed_dim"] * (t["hc_count"] + 1) * t["hidden_size"]}]
    flags += [flag("hyper_connections", "required", f"Gated low-rank hyper-connection (no Sinkhorn): {hc['mult']} streams, per sublayer a grouped RMSNorm, a "
                   f"[{hc['mult']}d -> {hc['lowrank']} -> {hc['mult']}d] SiLU/sigmoid mixer that collapses the streams by a mean, and a [{hc['mult']}d -> {hc['mult']}] "
                   "sigmoid injection gate. A different design from mHC. See docs/spec/hyper_connections.md."),
              flag("ngram_embedding", "required", f"Per-layer embedding on layer {ple_layer}: {heads} hashed 2/3-gram heads of {t['ple_embed_dim'] // heads} (BF16) from a "
                   f"{heads * t['ngram_vocab_size_base'] / 1e6:.0f} M-row table = 51 B elements (README: '51B n-gram embedding'), then key/value projections into the {hc['mult']} streams. "
                   "Needs a hashed-gather DMA."),
              flag("gated_deltanet", "warn", f"{t['layer_types'].count('linear_attention')} Gated-DeltaNet linear-attention layers (README: 3 x (GDN -> MoE) then 1 x (sparse attention -> MoE))."),
              flag("qsa_indexer", "warn", f"Qwen Sparse Attention on the {t['layer_types'].count('full_attention')} full-attention layers: MQA indexer ({t['indexer_n_heads']} query heads, 1 key head, dim "
                   f"{t['indexer_head_dim']}) over keys compressed by {t['indexer_compress_ratio']}; budget {t['indexer_budget']} tokens = {t['indexer_budget'] // t['indexer_compress_ratio']} blocks."),
              flag("router_softmax", "info", "Router is softmax with normalized top-k, no selection bias (confirmed in transformers modeling_qwen4_exp.py)."),
              flag("published_bf16", "warn", "Only BF16 weights published; FP8/FP4 deployment needs our own quantization."),
              flag("modalities", "info", "Vision tower excluded.")]
    return dict(hidden=t["hidden_size"], vocab=t["vocab_size"], n_layers=L, max_ctx=t["max_position_embeddings"], tie=t["tie_word_embeddings"],
                dtypes=dict(expert="bf16", dense="bf16", embed="bf16", table="bf16", hc="bf16"), attn=at, ffn=ffn, layers=rle(pairs),
                mtp=dict(n_layers=t["mtp_num_hidden_layers"], attn="full", ffn="moe"), extras=dict(hyper_connections=hc, tables=tables),
                spec_decode=dict(method="mtp", draft_tokens=t["mtp_num_hidden_layers"], in_weights="mtp", source="config mtp_num_hidden_layers"),
                published=dict(total_b=180, active_b=6, active_convention="main",
                               source="README: 125B (6B active) + 51B n-gram embedding + 4B MTP; active convention inferred (fits without the LM head)"),
                sources=["transformers modeling_qwen4_exp.py (GatedResidual, NGramEmbedding, QSAIndexer, TopKRouter)", "README"], notes="BF16 as published.")


ADAPTERS = {"deepseek_v41": deepseek_v41, "glm_moe_dsa": glm_moe_dsa, "glm5_next": glm5_next, "mimo_v2": mimo_v2,
            "bailing_hybrid": bailing_hybrid, "minimax_m3_vl": minimax_m3, "step5_vl": step5,
            "mistral_large4": mistral_large4, "muse_spark": muse_spark, "qwen4_exp": qwen4_exp}
# adapters that need a second snapshot: models/configs/<slug>.<extra>.json
EXTRA = {"mimo_v2": "drafter", "minimax_m3_vl": "drafter", "mistral_large4": "params"}

NAMES = {"deepseek_v4_1_flash": "DeepSeek V4.1 Flash", "glm_5_3": "GLM-5.3", "glm_5_3_flash": "GLM-5.3-Flash",
         "mimo_v2_6_pro": "MiMo-V2.6-Pro", "mimo_v2_6_flash": "MiMo-V2.6-Flash", "ling_3_1_flash": "Ling 3.1 Flash",
         "minimax_m3_1_flash": "MiniMax-M3.1-Flash", "step_5_preview": "Step 5 Preview", "mistral_large_4": "Mistral Large 4",
         "muse_spark_open": "Muse Spark Open", "qwen3_8_flash_next": "Qwen3.8-Flash-Next"}


def weight_bytes_adjust(root):
    """Measured bytes to remove from the published total before the weight check: vision/audio tensors and tensors
    stored twice (models/weight_bytes.tsv, from the safetensors headers)."""
    p = os.path.join(root, "models", "weight_bytes.tsv")
    rows = csv.DictReader(open(p), delimiter="\t") if os.path.exists(p) else []
    return {r["slug"]: {"non_text_bytes": int(r["non_text_bytes"]), "duplicate_bytes": int(r["duplicate_bytes"])} for r in rows}


def build(slug, repo, root):
    cdir = os.path.join(root, "models", "configs")
    raw = open(os.path.join(cdir, f"{slug}.json"), "rb").read()
    cfg = json.loads(raw)
    total = json.load(open(os.path.join(cdir, f"{slug}.index.json")))["total_size_bytes"]
    mt = cfg.get("model_type")
    if mt not in ADAPTERS:
        raise SystemExit(f"{slug}: no adapter for model_type '{mt}'")
    flags = []
    args = [cfg, flags]
    if mt in EXTRA:
        args.append(json.load(open(os.path.join(cdir, f"{slug}.{EXTRA[mt]}.json"))))
    r = ADAPTERS[mt](*args)
    desc = {"schema_version": S.SCHEMA_VERSION, "name": NAMES[slug], "slug": slug, "hf_repo": repo,
            "config_sha256": hashlib.sha256(raw).hexdigest()[:16],
            "arch": {"model_type": mt, "architectures": cfg.get("architectures")},
            "dims": {"hidden": r["hidden"], "vocab": r["vocab"], "n_layers": r["n_layers"], "max_ctx": r["max_ctx"],
                     "tie_embeddings": r["tie"], "final_logit_softcap": r.get("softcap"), "encoder_layers": r.get("enc")},
            "weights": {"total_bytes": int(total), **weight_bytes_adjust(root).get(slug, {"non_text_bytes": 0, "duplicate_bytes": 0}),
                        "dtypes": r["dtypes"], "notes": r["notes"]},
            "attn_types": r["attn"], "ffn_types": r["ffn"], "layers": r["layers"], "mtp": r.get("mtp"), "drafter": r.get("drafter"),
            "spec_decode": r.get("spec_decode"),
            "extras": r["extras"], "published": r.get("published"), "sources": r.get("sources", []), "flags": flags}
    S.validate(desc)
    c = S.count_params(desc)
    exp = S.expected_weight_bytes(desc)
    w = desc["weights"]
    total_text = total - w.get("non_text_bytes", 0) - w.get("duplicate_bytes", 0)  # compare like with like
    dv = {"params_total_b": round(c["total"] / 1e9, 1), "params_active_b": round(c["active"] / 1e9, 1),
          "params_expert_b": round(c["expert"] / 1e9, 1), "params_table_b": round(c["table"] / 1e9, 1),
          "params_mtp_layer_b": round(c["active_mtp_layer"] / 1e9, 2), "params_drafter_b": round(c["drafter"] / 1e9, 2),
          "expected_weight_gb": round(exp / 1e9, 1), "published_weight_gb": round(total / 1e9, 1),
          "weight_check_ratio": round(exp / total_text, 3)}
    if desc["dims"]["encoder_layers"]:
        dv["params_active_prefill_b"] = round(S.active_params(desc, desc["dims"]["encoder_layers"]) / 1e9, 1)
    pub = desc["published"]
    if pub:
        dv["published_total_ratio"] = round(c["total"] / 1e9 / pub["total_b"], 3)
        if pub.get("active_b"):
            dv["published_active_ratio"] = round(S.active_by_convention(desc, pub.get("active_convention", "main+lm_head")) / 1e9 / pub["active_b"], 3)
    desc["derived"] = dv
    return desc


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
    a = ap.parse_args(argv)
    rows = list(csv.DictReader(open(os.path.join(a.root, "models", "repos.tsv")), delimiter="\t"))
    os.makedirs(os.path.join(a.root, "models", "descriptors"), exist_ok=True)
    for r in rows:
        d = build(r["slug"], r["repo"], a.root)
        S.dump(d, os.path.join(a.root, "models", "descriptors", f"{r['slug']}.yaml"))
        dv = d["derived"]
        pr = f"  vs published total x{dv['published_total_ratio']:.3f}" if "published_total_ratio" in dv else ""
        pr += f" active x{dv['published_active_ratio']:.3f}" if "published_active_ratio" in dv else ""
        print(f"{d['name']:<22} {dv['params_total_b']:>7.1f}B/{dv['params_active_b']:>5.1f}B  weights x{dv['weight_check_ratio']:.3f}{pr}")


if __name__ == "__main__":
    sys.exit(main())
