# Model descriptor schema (v1)

Source of truth for the format: the module docstring and validator in `sw/npumodel/schema.py`.
Descriptors live in `models/descriptors/<slug>.yaml`, generated from `models/configs/<slug>.json`
(Hugging Face `config.json` snapshots; repositories in `models/repos.tsv`).

## Pipeline
```
make fetch-models   # network: refresh configs/*.json and *.index.json (weight total size)
python3 scripts/measure_weight_bytes.py   # network: vision/audio and duplicated tensor bytes -> models/weight_bytes.tsv
make models         # configs -> descriptors (ingest) -> docs/reports/model_survey.md (report)
make pytest         # schema, reproducibility, weight-size check, KV scaling
```
The descriptors are committed so Phase 1 work does not need the network. `test_descriptor_is_reproducible`
fails if a descriptor differs from a fresh ingest of its config snapshot.

## Structure
- `dims`: hidden, vocab, n_layers (main layers only), max_ctx, tie_embeddings, final_logit_softcap, encoder_layers (DeepSeek's
  causal encoder: prefill executes only this many layers).
- `attn_types` / `ffn_types`: named specs. Layers reference them by name, in run-length form
  `layers: [{n, attn, ffn}]`.
  - Attention kinds: `gqa` (covers MQA and sliding window via `window`; `kv_k_eq_v` for a single shared K/V vector), `mla` (latent KV),
    `linear` (`variant: gdn | kda`, recurrent state, `f_gate` / `out_gate`: none | lowrank | full).
  - Optional parts: `sparse` (`kind: indexer | block_topk`, `index_role: own | reindex | reuse`, `topk` (attended entries), `key_pool`
    (tokens per scored key: Qwen 4, Step 5 8), `key_dtype`, `k_heads` (shared index key heads), `candidate_pool`, `score` (`max` for MSA,
    `weighted_relu` for Step 5), `groups` (independent selections, one per KV group), `key_gate` (Step 5's softmax-gated pooling projection);
    for `block_topk` also `block_size`, `local_blocks`, `init_blocks`),
    KV-cache fields `kv_role: own | reuse`, `kv_compress_ratio`, `kv_dtype`, `window_dtype`, and low-rank q / grouped o projections.
  - Per-part weight dtypes that differ from `weights.dtypes`: attention `w_dtype` (whole block; GLM-5.3-Flash KDA is BF16) or `o_dtype`
    (output projection; MiMo is BF16), FFN `expert_dtype` (Step 5 layers 88-90 keep BF16 experts).
  - FFN kinds: `dense`, `moe` (`n_experts`, `top_k`, `d_expert`, shared experts, `router: {scoring, correction_bias, norm_topk, ...}`,
    optional `d_in` for experts narrower than the model dimension).
- `mtp`: extra multi-token-prediction layers stored in the main weights (counted in parameters, not in `layers` or in "active");
  `own_lm_head` when every MTP layer ships its own output head (Step 5).
- `drafter`: a speculative-decoding model (DFlash, DSpark): `n_layers`, `hidden`, `d_ff`, `attn` (gqa or mla dims), `window`, `block`,
  `target_layer_ids`, `fc_in`, `markov_rank`, `vocab`, `aux_params`, `dtype` (layers), `aux_dtype` (fc / Markov head), `in_main_weights`
  (true for Mistral, whose drafter is inside the published total). `drafter_params` / `drafter_bytes` reproduce the drafter files' headers.
- `spec_decode`: how the authors' stack drafts: `method` (`mtp` | `dflash` | `dspark`), `draft_tokens` (trained block), `in_weights`
  (`mtp` layers or a `drafter`). Acceptance profiles: `sw/npumodel/specdec.py`.
- `extras`: `hyper_connections` (`variant: mhc_sinkhorn | gated_lowrank`, `mult`, `sinkhorn_iters` or `lowrank`) and `tables`
  (hashed n-gram / per-layer embedding tables: `entries`, `dim`, `lookups_per_token`, `proj_params`).
- `weights`: published `total_bytes`, `non_text_bytes` (vision/audio tensors) and `duplicate_bytes` (tensors stored twice), both measured
  from the safetensors headers into `models/weight_bytes.tsv`, and per-class dtypes (`expert`, `dense`, `embed`, `table`, `hc`).
- `published`: total / active parameter counts quoted by the authors and `active_convention` (`main` | `main+lm_head` (default) |
  `main+embed+lm_head`), the way the author counts "active". `sources`: what the uncertain facts were verified against.
- `flags`: `{id, severity, detail}`. `required` = outside the base schema but the hardware/compiler will support it,
  `warn` = approximated, inferred or undecided, `info` = recorded only, `unsupported` = no plan to support (none today).
  Nothing is silently dropped.
- `derived`: parameter counts, the weight-size check (estimated / published bytes), the published-count ratios, and prefill-active
  parameters when a model has an encoder split.

Derived metrics in `schema.py`: `count_params`, `active_params`, `active_by_convention`, `expected_weight_bytes`, `kv_breakdown` / `kv_bytes`
(global, index, window, state), `index_keys_scanned`, `attn_dtype_split`, `drafter_params` / `drafter_bytes`,
`hc_ops` / `hc_params_per_sublayer`. The per-step operator list built from a descriptor is `sw/npumodel/opgraph.py` (Phase 1 step 2).

## Validation
Two independent guards against wrong ingestion. (1) Bytes estimated from the layer plan and dtypes must match the published safetensors total,
minus `non_text_bytes` and `duplicate_bytes`, within 2% (all 11 are within 0.5%). (2) Parameter counts must match the authors' figures
(total within 6%; active within 8%, counted by `active_convention`).
They caught that MiMo and MiniMax store experts as 4-bit although their `quantization_config` says FP8/MXFP8 (MiniMax is NVFP4, not MXFP4), that MiMo's MTP layers are dense, and that
DeepSeek's DSpark layers have 128, not 384, experts. DeepSeek also reproduces its published 890 bytes of global KV per token exactly (a test).

## Not modeled yet
Vision/audio towers (only their bytes are measured); acceptance rates of the 11 targets themselves (the profiles are Qwen3-8B fits).
