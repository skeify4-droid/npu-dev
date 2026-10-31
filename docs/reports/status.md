
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
- Read the MiniMax Sparse Attention paper (arXiv 2606.13392) and Step's block-compression code.
- Phase 2: HC mixer-precision experiment.
