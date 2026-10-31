
## Phase 0 (repo skeleton, toolchain, capacity calibration)

### Changes
- Repo skeleton, `.gitignore`, `CLAUDE.md` (memory rules, conventions), `Makefile` (`lint`, `unit`, `syn BLOCK=`; `formal`/`e2e` are placeholders).
- `scripts/memrun`: wraps a command with `/usr/bin/time -l`, logs peak RSS and wall time to `logs/`.
- Trivial block `rtl/common/trivial_add.sv` with `tb/unit/tb_trivial_add.sv`.
- Calibration tooling: `scripts/gen_pe_array.py`, `gen_pe_pr.py`, `verilator_calib.sh`, `orfs_calib.sh`, `tb/calib/pe_main.cpp`, `syn/orfs/pe_pr_*.mk`.
- Venv extras: numpy, ml_dtypes, pyyaml, pandas, plotly, zstandard.
- `docs/PLAN.md`: measured caps (flat synthesis about 0.9 M cells; P&R block 16×16, about 230 k cells).

### Test results
- `make lint`: clean (Verilator 5.052, `-Wall`). `make unit`: `PASS tb_trivial_add`.
- Yosys flat: 32×32 array = 918 k cells, 5.0 GB, 88 s.
- Verilator: builds under 2 s at every size, peak RSS flat at 0.23 GB (may undercount).
- ORFS Nangate45 in the 12 GB VM: 8×8 = 3 min 9 s, 2.4 GB; 16×16 = 13 min 41 s, 5.9 GB; 32×32 not run (extrapolates above 14 GB).
- Details: `docs/reports/capacity.md`.

### Next steps
- Phase 1 onward per `docs/PLAN.md`; fill `isa/isa.yaml`, `tb/formal`, `tb/e2e`.
- Re-check Verilator memory on a larger design.
- Verify target model configs (still unverified).

## Phase 1, step 1: config ingestion and descriptor schema

### Changes
- `models/repos.tsv` + `scripts/fetch_configs.sh`: config.json and weight-size snapshots for all 11 models in `models/configs/`.
  Muse Spark Open comes from the unsloth mirror (original repo gated); MiMo-V2.6-Pro uses the `-RL` repo.
- `sw/npumodel/`: descriptor schema and validator (`schema.py`), per-family adapters (`ingest.py`), survey generator (`report.py`).
- `models/descriptors/*.yaml` (generated), `docs/reports/model_survey.md` (generated), `docs/spec/model_descriptor.md`.
- Makefile: `make models`, `make fetch-models`, `make pytest`; `PY` now defaults to the venv Python.

### Test results
- `make pytest`: 27 passed. `make lint unit`: still green.
- Weight-size check (estimate from layer plan / published safetensors size): all 11 within 6% (0.941 to 1.034).
- Parameter names reproduced: Muse Spark 816 B, Mistral Large 4 about 52 B active.

### Findings that change the plan
- Hyper-connections (4 residual streams + Sinkhorn) appear in DeepSeek V4.1, GLM-5.3-Flash and Qwen3.8; hashed n-gram
  "engram" tables (DeepSeek 197 G elements, Qwen 51 G) need a gather DMA. Neither is in the Phase 0 architecture.
- MiMo-V2.6 and MiniMax-M3.1 store experts as 4-bit although their quant config says FP8: FP4 weight support matters more.
- Linear attention (KDA, Gated DeltaNet) is in 4 of 11 models; sparse/indexer attention in 7.
- Muse Spark Open and Qwen3.8 are published in BF16 only (1.64 TB and 360 GB).

### Next steps
- Phase 1 steps 2 to 4: op-graph generator (prefill/decode), roofline model, sweeps; then freeze `docs/spec/arch.md`.
- Verify the inferred items: Ling layer pattern, DeepSeek compress_ratios semantics, router scoring for Mistral and Muse.
- Decide how the plan handles hyper-connections and engram gather (hardware support vs. out of scope).

## Phase 1, step 1b: feature investigation and hyper-connection support

### Changes
- Investigated every unclear feature against the models' own code, READMEs and the DeepSeek V4.1 tech report; results in `docs/reports/model_features.md`
  (20 resolved questions, 6 still open). Corrections applied to the descriptors (see that file).
- Schema extended: KV roles and dtypes, indexer roles, `drafter` vs in-weights `mtp`, linear-attention gates, hyper-connection variants and cost model
  (`hc_ops`), gather tables, `published` counts, `sources`, severity `required`.
- `docs/spec/hyper_connections.md`: requirements for hyper-connections (mHC with Sinkhorn, Qwen's gated low-rank mixer) and hashed n-gram gather.
  `docs/PLAN.md` updated (section 2.3, Phase 1 and 2, risks).

### Test results
- `make pytest`: 29 passed. `make lint unit`: green.
- DeepSeek V4.1: 890.0 B/token global KV (published 890), expert elements 557.2 G (HF 557.17 G), prefill-active 7.6 B (published 8 B).
- Weight check 0.941 to 0.999 for all 11; published totals within 5%.

### Findings that change the plan
- DeepSeek V4.1 KV is 890 B/token, not ~10 KB: long context is far cheaper than assumed. Its capacity is dominated by experts (272 GB) and Engram (197 GB).
- Prefill for DeepSeek runs only 20 of 40 layers (causal encoder-decoder); the op-graph generator must support per-phase layer ranges.
- Qwen's hyper-connection mixer is 9.5% of its decode compute (mHC is 0.3%): it is not a rounding error.
- Muse router is sigmoid+bias, Mistral softmax; both confirmed from code.

### Next steps
- Phase 1 steps 2 to 4: op-graph generator, roofline model, sweeps (include HC ops, CED prefill range, gather traffic, drafter models).
- Read Step's block-compression code (the MiniMax items are closed, see the MiniMax follow-up section).
- Phase 2: HC mixer-precision experiment.

## Phase 1 step 1c: MiniMax open items closed (2026-10-31)

### Changes
- Read the MSA paper (arXiv 2606.13392) and the DSpark paper (arXiv 2607.05147); fetched all 49 safetensors headers by range request.
- MiniMax experts are NVFP4 (FP8 scale per 16, FP32 global scales), not MXFP4: the weight check went from 0.941 to 0.999. Added `weights.non_text_bytes` for the excluded vision tower.
- MSA modeled exactly: one shared index key head, per-group top-16 blocks of 128, max-over-block score, forced local block, no init block. Removed the DSA-only per-head weight from `block_topk` parameter counts.
- Drafter descriptor gained `target_layer_ids`, `fc_in`, `shares_target_embed_and_lm_head`. Rows 21-25 added to `docs/reports/model_features.md`.

### Test results
- `make pytest`: 30 passed (new `test_minimax_msa_and_nvfp4_experts`).

### Findings that change the plan
- MSA decode scans every index key: at 1 M context 8.05 GB of index keys vs 126 MB of selected KV per decoded token per sequence (FP8 assumed). Attention is index-scan bound, so the index-key dtype, a dedicated high-bandwidth scan path and the 32.2 G index MACs per token must be in the Phase 1 roofline.
- The DSpark scheduler needs a profiled steps-per-second curve over verification batch size: a simulator input, not a constant.

### Next steps
- Step 5 block compression code; Phase 1 steps 2 to 4 (include the MSA index scan and DSpark schedule).

## Phase 1 steps 1d-4: model open items closed, analytical model, architecture frozen (2026-11-01)

### Changes
- **Model open items closed** (`docs/reports/model_features.md` rows 26-34). Each is backed by the model's own code or published headers.
  - MiniMax caches: NVFP4 KV and index keys, from the vLLM recipe.
  - MiniMax active count: 23.5 B without the LM head, against "approximately 23B" published.
  - Step 5 CSA compression: 8-token softmax-gated region summaries, weighted-ReLU scoring, per-KV-group top-512, from `kernel.py`.
  - Step 5 weights: BF16 attention and MTP, BF16 experts in layers 88-90, a separate output head per MTP layer, and 2.73 GB of duplicated tensors.
  - GLM-5.3 shared indexer layers reuse the previous top-k (transformers code).
  - Mistral's drafter is inside its weights (4.20 GB).
  - MiMo: DFlash schedule and `o_proj` in BF16. GLM-5.3-Flash: KDA projections in BF16.
- **Weight check:** vision/audio and duplicated bytes are measured for all 11 repos (`scripts/measure_weight_bytes.py` -> `models/weight_bytes.tsv`).
  All 11 now match within 0.5%; before this session the range was 0.962-0.999.
- **Schema additions:**
  - `drafter` parameter and byte models, which match the drafter file headers within 0.5%.
  - `spec_decode` (method, draft tokens).
  - `published.active_convention`; "active" no longer counts MTP layers.
  - Per-part dtypes: `w_dtype`, `o_dtype`, `expert_dtype`.
  - MTP `own_lm_head`, and the Step 5 sparse fields.
- **Phase 1 step 2:** `sw/npumodel/opgraph.py` builds the per-layer op list for prefill and decode.
  - It covers GEMMs, attention with exact causal sums, index scans, top-k, linear-attention scans, MoE experts touched, HC, table gathers and the LM head.
  - `draft_ops` builds one drafting round for MTP, DFlash and DSpark.
  - `sw/npumodel/specdec.py` holds acceptance profiles fitted to the DSpark paper's Table 1.
- **Phase 1 step 3:** `sim/analytic/hw.py` and `roofline.py` model:
  - per-die peaks with efficiencies;
  - TP / EP / PP mapping, TP all-reduce, EP all-to-all over D2D or C2C, and PP hand-off;
  - KV replication under TP for MLA/MQA, and capacity per die;
  - a mapping search and the minimum package count.
- **Phase 1 step 4:** `sim/analytic/sweep.py` (`make sweep`, 3 s, 17 MB RSS) writes `docs/reports/phase1_sweeps.md`, six SVG plots
  in `docs/spec/plots/` (dependency-free `svgplot.py`) and CSVs in `build/phase1/`.
- **`docs/spec/arch.md`:** the §2 numbers are frozen with their justification, and the package count per model is fixed.
  `PLAN.md` §2.2, §2.3 and Phase 1 are updated, as are `model_descriptor.md` and `CLAUDE.md` (tool and generated-file lines).

### Test results
- `make pytest`: 53 passed. That is 30 earlier, 4 new descriptor tests and 19 new analytic tests:
  - acceptance fits reproduce Table 1;
  - batch-1 decode touches exactly top-k experts per layer and reads weights within the active-parameter bounds;
  - the MiniMax index scan equals its index cache and is 64x the selected KV;
  - prefill uses the causal triangle;
  - experts touched saturate;
  - baseline peaks; batch-1 decode is memory-bound; package counts; speculative decoding speeds up a memory-bound decode.
- `make lint unit`: green. `make sweep`: rc 0.

### Findings that change the plan
- **Decode is HBM-bound for every model** at 64 x 128K: 97-100% of op time. Routed experts are 50-85% of the step.
  MoE turns compute-bound only at about 151 tokens per expert (FP8) or about 80 (4-bit), which is 2.6k-9.6k tokens in flight per EP group.
- **Package counts:** 2 for Qwen, MiMo-Flash and GLM-Flash; 4 for MiniMax, DeepSeek and Ling; 8 for GLM-5.3, Step 5, MiMo-Pro, Mistral and Muse.
  The scale-up fabric must support at least 8 packages.
- **6 HBM stacks per die** gives x1.43-1.66 throughput per package and halves the package count for 6 models. It is the largest lever.
  It stays a Phase 6 floorplan question (baseline 4).
  Dies per package (1/2/4) changes throughput per die by under 7%.
- **SFU raised to 128 exp/cycle/core.** At 64, Muse's head_dim-64 prefill attention would be SFU-bound by 1.6x.
- **Linear-attention FP32 state** costs 0.25-0.6 GB of HBM traffic per sequence per step and cannot fit in SRAM. A BF16 state is a Phase 2 numerics item.
- **Speculative decoding** with each model's own drafter gives x1.4-2.7 per user at batch 16. The DSpark Markov head reads a
  V x r BF16 matrix gamma times per round, so caching it on chip is worth evaluating.
- **Step 5's BF16 reference KV** (9.5 GB per sequence at 128K) doubles its package count: FP8 KV would serve it on 4.

### Next steps
- Phase 2: ISA (`isa.yaml`) and the micro-architecture specs (numerics, memmap, NoC, HBM, D2D/C2C).
  Include the index-scan path, NVFP4 DMA and KV store, CSA region-summary writes, and verification batches.
- Phase 2 numerics experiments: mHC mixer in BF16, and BF16 recurrent state.
