# Model descriptor schema (v1)

Source of truth for the format: the module docstring and validator in `sw/npumodel/schema.py`.
Descriptors live in `models/descriptors/<slug>.yaml`, generated from `models/configs/<slug>.json`
(Hugging Face `config.json` snapshots; repositories in `models/repos.tsv`).

## Pipeline
```
make fetch-models   # network: refresh configs/*.json and *.index.json (weight total size)
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
  - Optional parts: `sparse` (`kind: indexer | block_topk`, `index_role: own | reindex | reuse`, `key_pool`, `key_dtype`, `candidate_pool`),
    KV-cache fields `kv_role: own | reuse`, `kv_compress_ratio`, `kv_dtype`, `window_dtype`, and low-rank q / grouped o projections.
  - FFN kinds: `dense`, `moe` (`n_experts`, `top_k`, `d_expert`, shared experts, `router: {scoring, correction_bias, norm_topk, ...}`,
    optional `d_in` for experts narrower than the model dimension).
- `mtp`: extra multi-token-prediction layers stored in the main weights (counted in parameters, not in `layers`).
  `drafter`: a separate speculative-decoding model (DFlash, DSpark) that is not in the main weights.
- `extras`: `hyper_connections` (`variant: mhc_sinkhorn | gated_lowrank`, `mult`, `sinkhorn_iters` or `lowrank`) and `tables`
  (hashed n-gram / per-layer embedding tables: `entries`, `dim`, `lookups_per_token`, `proj_params`).
- `weights`: published `total_bytes` and per-class dtypes (`expert`, `dense`, `embed`, `table`, `hc`).
- `published`: total / active parameter counts quoted by the authors. `sources`: what the uncertain facts were verified against.
- `flags`: `{id, severity, detail}`. `required` = outside the base schema but the hardware/compiler will support it,
  `warn` = approximated, inferred or undecided, `info` = recorded only, `unsupported` = no plan to support (none today).
  Nothing is silently dropped.
- `derived`: parameter counts, the weight-size check (estimated / published bytes), the published-count ratios, and prefill-active
  parameters when a model has an encoder split.

Derived metrics in `schema.py`: `count_params`, `active_params`, `expected_weight_bytes`, `kv_breakdown` / `kv_bytes`
(global, index, window, state), `hc_ops` / `hc_params_per_sublayer`.

## Validation
Two independent guards against wrong ingestion. (1) Bytes estimated from the layer plan and dtypes must match the published safetensors total within 7%
(all 11 are within 6%). (2) Parameter counts must match the authors' figures (total within 6%, active within 15%).
They caught that MiMo and MiniMax store experts as 4-bit although their `quantization_config` says FP8/MXFP8, that MiMo's MTP layers are dense, and that
DeepSeek's DSpark layers have 128, not 384, experts. DeepSeek also reproduces its published 890 bytes of global KV per token exactly (a test).

## Not modeled yet
Draft/verify schedules and acceptance rates, vision/audio towers, the MiniMax and Step sparse-attention scoring details (see `docs/reports/model_features.md`).
