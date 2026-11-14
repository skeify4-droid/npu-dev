
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

## Phase 2 step 1: micro-architecture, ISA, memory map, NoC deadlock strategy (2026-11-01)

### Changes
- `docs/spec/microarch.md`:
  - die floorplan (8 x 4 core tiles, 24 leaf routers);
  - core tile: front end, 6 queues, weight-stationary MXU with a 512 B/cycle stationary-load port and a 512 KB FP32 accumulator,
    1R1W banked SPM, VPU/SFU/top-k, DMA with 512 reads in flight;
  - die blocks; TINY/MID/FULL configs; interface tables for 11 blocks.
- `isa/isa.yaml` (v0.1):
  - 55 instructions, 16 formats, 18 enums, tile-descriptor layout, memory map constants.
  - `isa/isa.py` validates it, encodes and decodes, and generates `isa/gen/isa_pkg.sv`, `isa/gen/isa.h` and `docs/spec/isa_ref.md`.
  - The package is lint-clean in Verilator and loads in Icarus and in Yosys (sv2v or `isa_pkg::` names).
  - `docs/spec/isa.md` explains the programming model (semaphores, tile descriptors, block-scale contract) and maps every model
    feature to instructions.
- `docs/spec/memmap.md`:
  - 49-bit global addresses (node + 42-bit die offset, self alias);
  - stack-contiguous HBM with quadrant affinity, plus an XOR-hashed PC/bank map;
  - SPM/GSRAM/CORECTL/DIECTL windows; KV page tables; ordering model.
- `docs/spec/noc.md`:
  - 2 mesh planes, 5 VCs (REQ L/X/MC, RSP L/X), XY requests and YX responses;
  - 2 KB read bursts with coalesced acks;
  - rail-aligned C2C with at most one link per packet.
- `sim/analytic/noc.py`: link-load scenarios plus a whole-system channel-dependency-graph cycle check.
  - The baseline is acyclic at 2 and 4 packages.
  - Each of the 4 rejected alternatives produces a cycle and is kept as a test.
- `docs/spec/arch.md`, `docs/PLAN.md`, `sim/analytic/hw.py`: D2D 5 -> 1.4 TB/s, NoC 2 planes, ACC/IMEM SRAM, distributed collectives.
  `make sweep` was rerun.

### Results
- **HBM traffic:**
  - The mesh carries a full stack's 820 B/cycle at 80% peak link load with stack-affine placement, and 85% with every core reading every stack.
  - With a request per 256 B, the request overhead alone overloaded the mesh (1.20); 2 KB read requests fixed that.
- **D2D:** the mesh edge feeds at most 1.45 TB/s per direction, so a 5 TB/s D2D link was unusable.
  - At 1.4 TB/s, decode at the serving point changes by at most 0.2% and no package count changes.
  - Prefill with TP = 2 forced across D2D slows by up to 7%.
- **MXU:** the stationary-load port carries 512 B/cycle, but the measured tile period at M = 1 is 39.5 cycles in `FULL` (415 B/cycle; mxu.md §7, M-21),
  so the MXU is 2.15x a core's HBM share with 4-bit weights expanded to FP8 (not 2.9x). On the exact NVFP4 -> BF16 decode path it is the narrowest
  stage of the weight path at 1.15x (1.35x at 0.85 share; 1.05x / 1.24x at a colliding 2.5 KiB pitch). Not "never the bottleneck" (corrected 2026-11-09, review S-5).

### Tests
- `make pytest`: 68 passed (53 before, + 7 ISA, + 8 NoC).
- `make isa` regenerates and lints the outputs. `make lint unit` is green.

### Next
- `numerics.md`: conversion rounding, the NVFP4 -> E4M3 residual error, HC mixer precision, BF16 linear-attention state.
- `hbm.md`, `d2d_c2c.md`, then the Phase 2 spec review.

## Phase 2 step 2: numerics (2026-11-01)

### Changes
- `docs/spec/numerics.md`:
  - every format's bits, including the FP4 packing;
  - every conversion's rounding, saturation and NaN/Inf rule;
  - the block-scale fold algorithm and when it is exact;
  - `VQUANT` recipes (OCP MX, NVIDIA NVFP4);
  - the MXU PE arithmetic and accumulation order, and `BF16X3`;
  - VPU reductions and SFU error bounds, online softmax and top-k corner cases;
  - write-add ordering and a deterministic-collective mode;
  - RTL test tolerances.
- `sw/npumodel/numerics.py`: the bit-accurate golden model (formats, codecs, MX/NVFP4 quantizers, DMA fold, MXU order, BF16X3).
- `sim/analytic/numerics_exp.py` (`make numerics`, 40 s, 0.4 GB) generates `docs/reports/numerics_experiments.md` (E1-E4).
- `isa/isa.yaml`: new `FP_STATUS` CSR (sticky INVALID / OVERFLOW / SATURATE / NAN_IN).
- `microarch.md`: corrected the BF16 MXU rate. It said quarter rate; it is now a 2-way BF16 dot product per PE, half rate, as frozen.
- `arch.md`, `isa.md`, `hyper_connections.md`: decisions recorded. No frozen number changed.

### Results
- **NVFP4 → E4M3 fold (E1):**
  - It changes 13-23% of elements and adds 2.1-2.3% GEMM error. That is about the E4M3 activation error every FP8 GEMM pays, and under
    half the error of MiniMax's served W4A4 path.
  - The MXFP4 fold is exact; MXFP8 is below 1e-6.
  - The exact BF16 path would cost +44% on MiniMax prefill. Decision: exact path when memory-bound (decode), fold when compute-bound.
- **mHC mixer (E2):**
  - Two BF16 passes over hi/lo weights match FP32 (KL about 1e-11).
  - Plain BF16 passes the KL < 1e-3 bound (own contribution 2e-6 to 2e-5); E4M3 fails it.
  - Decision: BF16 hi/lo.
- **BF16 recurrent state (E3):**
  - Rounding the state every token gives 0.7-2% output error, 4-12x the BF16 output rounding. Stochastic rounding is worse.
  - The gain would be +3-6% decode tokens/s.
  - Decision: the state stays FP32.
- **Accumulation (E4):** the PE order is within 2-4 ulp at K = 16384, against 4-41 ulp for a naive FP32 loop.
- `ml_dtypes` rounds float64 inputs twice (through float32). The golden model rounds once; tests compare on FP32 inputs.

### Tests
- `make pytest`: 89 passed (68 before, + 21 numerics).
- The golden model matches `ml_dtypes` on every E4M3, E5M2 and E2M1 code, and on all rounding midpoints.

### Next
- `hbm.md`, `d2d_c2c.md`, then the Phase 2 spec review.
- Open: confirm D2 on the real DeepSeek `hc_fn` weights (range requests, 2 MB per sublayer).

## Phase 2 step 3: HBM, links, spec review (2026-11-01)

### Changes
- `docs/spec/hbm.md`:
  - controller per stack, `clk_mc` = 1.2 GHz with async FIFOs, 32 PC engines;
  - read queue 32 / write queue 16 / read return 16 granules;
  - FR-FCFS scheduler with an age cap, write drain by watermarks, adaptive page policy, per-bank refresh;
  - timing table (Ramulator 2 HBM3 preset plus marked assumptions) and the checker rule table;
  - DFI-like boundary, the behavioral DRAM model contract, interfaces and CSRs.
- `sim/analytic/hbm.py`: `Dram` (bank state + timing checker, the reference for the Phase 4 RTL checker) and a cycle-level PC
  scheduler. `make hbm` (60 s, 20 MB) writes `docs/reports/hbm_experiments.md` (H1-H5).
- `docs/spec/d2d_c2c.md`:
  - one link layer for D2D and C2C: 156 B flits, per-VN credits with a shared pool, CRC-32C and go-back-N replay;
  - PHY sizing: 2 UCIe-A x64 modules per D2D sub-link, 6 x 200 Gb/s lanes per C2C port;
  - buffers, bottom-up latency, and collective schedules (hierarchical direct all-reduce, rail-staged all-to-all).
- `sim/analytic/links.py`: `make links` (1 s) writes `docs/reports/links_experiments.md` (L1-L6).
- Spec review: `docs/reports/spec_review.md`, findings R-1 to R-15, all resolved or recorded. Follow-on edits:
  - `isa.yaml`: new `fault_cause` enum.
  - `isa.py`: now also emits `isa/gen/isa_defs.py` (Python).
  - noc.md: partial write acks, link CRC.
  - memmap.md: HBM write alignment, reduced-config PC fold.
  - microarch.md: interface tables for ACC, leaf router, host interface and async FIFO; buffer SRAM accounting.
  - arch.md change log, PLAN Phase 2 and risk table, hyper_connections.md HC-5.

### Results
- **HBM controller (one PC, saturation):** 0.99 on streams, 0.95 with 5% KV writes, 0.97 on random 256 B granules, 0.90 on a 3:1
  read/write mix. The frozen 0.85 keeps 0.10-0.14 of margin for what the model leaves out.
  - All-bank refresh would cost 12% (25% hot), so refresh is per bank.
  - Read queues of 16 lose 9-11%.
  - The p99 granule latency at 85% load is 370 ns, which keeps 2x margin in the DMA's 512 granules in flight.
- **Device requirement:** tCCD_L ≤ 2.5 ns. At 4.1 ns streaming falls to 0.79 whatever the queue depth (4 bank groups / 5 cycles).
  Added to the PLAN risks.
- **Links:**
  - At 1e-15 BER an 8-package system would see an undetected error every 2.5 s, so both links have CRC and replay.
  - C2C needs 6 SerDes lanes per port for the frozen 100 GB/s of payload.
  - Link buffers are 1.4 MiB per die (C2C round trip 470 cycles); HBM controller buffers are 1 MiB per die.
- **Collectives:**
  - The bottom-up step latency is 0.09 µs (D2D) and 0.24 µs (C2C), against the frozen 0.5 / 2 µs.
  - The hierarchical direct all-reduce needs 4 latency steps for any group size; the roofline's ring needs 2(g-1).
  - The roofline under-counts off-package all-to-all bytes (R-1).
  - Net effect on decode at the serving point: -1% to +10% with frozen latencies, up to +16% with bottom-up latencies. No package
    count changes; arch.md stays frozen.

### Tests
- `make pytest`: 137 passed (89 before, + 40 HBM, + 7 links, + 1 ISA Python constants).
- `make isa`, `make lint unit` clean. `make hbm` and `make links` reports are byte-identical across runs.

### Next
- Independent review of the whole spec set before Phase 3 RTL on `core/dma`, `core/seq`, `hbm` (scope in spec_review.md, Not done).
  Correction: an earlier note said PLAN §5 asks for an Opus/xhigh ISA + HBM pass; §5 lists that only as an alternative.
- Phase 3: `common` blocks (FIFOs, async FIFO, credit interfaces, arbiters, SRAM wrapper) and `arith`.
- Open: vendor HBM3e timing (tCCD_L), PHY latencies, the DeepSeek `hc_fn` check for numerics D2.

## Phase 2: independent spec review (2026-11-01)

### Changes
- `docs/reports/spec_review.md`: new "Independent review" section from a fresh Opus 5.5 session (high, then xhigh for the two
  reserved checks). Findings I-1 to I-15 and X-1 to X-6, plus a table of which findings gate which block. No spec, model or test
  changed; all findings are open.
- `docs/PLAN.md`: the Phase 2 exit records the review and its gates.
- Gating fix (this note's session): X-2 gates `hbm` only for (b), the shared command bus of a channel's two PCs. (a), (c)-(e) need
  JESD238 and go on the H-9 checklist instead of blocking RTL.

### Results
- **High severity:**
  - I-1 GLOBAL -> GLOBAL `DMA_COPY` has no response sink (deadlock).
  - I-2 paged block-scaled KV has no complete ISA operand set.
  - I-3 the exact NVFP4 -> BF16 path exceeds the 256 B/cycle DMA SPM write port (0.83 of the rate the roofline assumes).
  - X-3 the R-3 partial-ack coalescing has no completion trigger (hang at low load).
- **Reproduced by the authoring session** (scratch probes, not committed):
  - The six `hbm.Dram` gaps of I-9 / X-1 are all accepted.
  - The mutation test leaves the same 15 of 28 rules deletable.
  - I-10: `MID` 0.683 and `TINY` 0.562 against `FULL` 0.988; XORing the folded bits into the bank restores 0.99.
  - I-13: SPM bank spread over 2-64 KiB strides is 32 down to 1 bank.
  - The I-3 arithmetic.
- Three of the four high findings (I-1, I-3, X-3) are errors of the authoring session that the self-review missed. X-3 was
  introduced by the R-3 fix.

### Tests
- Unchanged: `make pytest` 137 passed. The review's probes (mutation test, the I-1 and I-7 CDG variants) are not yet unit tests.

### Next
- **Fix session (Opus / high):** resolve the findings that gate `core/dma`, `core/seq` and `hbm` (spec_review.md, "Before Phase 3").
  Start with I-1, I-2, I-3 and X-3. Land the checker fixes as tests: the mutation test as a unit test, and the I-1 / I-7 cycles in
  `test_noc.py`.
- In parallel: Phase 3 `common` and `arith`; nothing in the review blocks them.
- Later: Phase 4 `link` / `noc` items (I-7, X-4, X-5) and compiler / runtime items (I-6, I-8, I-14, X-6).
- Open: vendor HBM3e timing (tCCD_L, X-2 checklist), PHY latencies, the DeepSeek `hc_fn` check for numerics D2.

## Phase 2: fix session for the independent review (2026-11-01)

### Changes
One commit per group, `make pytest` green before each. The Resolutions section of `docs/reports/spec_review.md` has one row per
finding (status, group, what was done); the reviewer's tables are unchanged.
- **Group 1 (72fd92c), high severity:** a DMA has at most one `GLOBAL` operand (I-1); row-granular pages with an in-page scale region,
  split TD fields, `T_DMA` `pt_reg` / `ts_reg`, `paged_ptr` (I-2); DMA SPM write 512 B/cycle, converter 256 elements/cycle,
  `ni_rsp` valid/ready (I-3); `blen` in every write plus an idle flush as the ack trigger (X-3); `noc_header` layout (I-12(b)).
- **Group 2 (2a62587), ISA and sequencer:** serial-number semaphore compare and never-reset network semaphores (I-4); multicast
  completes on injection, `rsem` required (I-5); `q_push` widths, MXU `dtab_rd`, DTAB ordering rule (I-11); `dma_list_entry` with an
  optional per-entry wait (I-12(a)).
- **Group 3 (4a21399), HBM:** PREA, REFpb → ACT of any bank, REFpb → REFpb same bank, REFab → REFab, unknown commands rejected
  (I-9, X-1); bank XOR in the reduced-config fold (I-10); `hbm.Channel` and a phase-order row-bus arbiter (X-2(b)). The scheduler
  became `PcSched` (bit-identical results with the new gap set to zero). PLAN Phase 2 gating findings closed.
- **Group 4 (fad67ca), links and compiler rules:** buffer, `rsem` and semaphore-parity rules for the staged all-to-all (I-6);
  per-VN link queues (I-7); Engram rows staged through the sibling die (I-8, the user's decision); tail-block rewrite of scale rows
  (I-14); cumulative per-VN credits (X-4); a link-down bridge keeps sinking (X-5); issue-order rules (X-6).
- **Group 5 (18752f0), low severity:** SPM bank text, pitch padding rule and read budget (I-13); the eight I-15 text items, including
  a DIECTL block/leaf table, CORECTL `START` / `STOP` and a `TIMEOUT` fault cause.

### Results
- No arch.md frozen number changed. The two that could have moved: I-3 keeps D1 (NVFP4 → BF16 now 1.65x at 0.85 share at every
  stage, was 0.83x at the old write port); I-8 option A would have raised DeepSeek's "weights only" count from 2 to 4, option B
  (chosen) changes nothing.
- Regenerated: `make numerics` (D1 feed table), `make hbm` (new gap: single-PC numbers move by at most 0.02, random 64 B 0.42 → 0.40;
  new H6 reduced configs: MID 0.68 / 0.72 → 0.99, TINY 0.55 → 0.98; new H7 channel pair: at most 0.001 lost), `make links` (new L7,
  table-gather options), `make isa`.
- Proposals changed or rejected, with reasons in the Resolutions rows: I-1, I-2, I-3, I-4, I-5, I-6(b), I-11, I-13 (optional hash).

### Tests
- `make pytest`: 190 passed (137 before). `test_isa.py` 8 → 18 (layouts fit and never share bits within a type, header and list
  widths, serial compare, DIECTL, SPM banks); `test_noc.py` 8 → 17 (I-1 and I-7 rejected variants, ack coalescing completes and the
  old rule hangs); `test_hbm.py` 40 → 72 (a violating and an exact-gap sequence per rule, channel bus, rule-removal test, reduced
  configs within 0.02 of FULL, channel scheduler); `test_links.py` 7 → 9.
- `make isa` (Verilator lint of `isa_pkg`) clean. No RTL changed, so `make lint unit` is unaffected.

### Next
- Verification session (prompt in the user's request; first checks listed at the end of spec_review.md Resolutions).
- Then Phase 3 RTL on `core/dma`, `core/seq`, `hbm`; `common` and `arith` were never blocked.
- Open: the hbm.md H-9 JESD238 checklist (X-2 (a), (c)-(e), `ACT_CK`, `tRREFDA`); Phase 4 link tests (X-4, X-5, I-7) and the SV
  checker's rule-removal test; the Phase 7 / golden-model deadlock detector (X-6); vendor PHY latencies; the DeepSeek `hc_fn` check
  for numerics D2.


## Phase 2: fixes for the verification findings (2026-11-01)

### Changes
The verification session (312f927) confirmed the first fix session and found V-1 to V-11 (spec_review.md, Verification). This
session fixed all eleven on branch `spec-verification-fixes`, one commit per group, with `make pytest` green before each. The
"Resolutions (verification fixes)" section of `docs/reports/spec_review.md` has one row per finding. The reviewer's and the
verifier's tables are unchanged.
- **Group 1 (7575888), `core/dma`:**
  - V-1: block scales are staged in an SPM ring (CSR `DMA_STAGE`) before their data, and split units are staged raw, so `ni_rsp`
    never waits on the network. `noc.py` has the old rule as a rejected variant.
  - V-3: the four DQs are served independently.
  - V-4: the scale address uses the whole in-row offset (`isa.paged_addr`).
  - V-5: write packets merge adjacent rows. memmap.md §6 has a worked DeepSeek index-key and MiniMax KV append.
- **Group 2 (2bd39fa), `core/seq`:** V-2, the user's decision. No kernel barrier: the L-6 parity and consumer credits carry across
  kernels. isa.md §3 (I-4 and I-5 text) was made consistent with it.
- **Group 3 (eb1f153), low severity:**
  - V-6: list entries are checked like their instructions.
  - V-7: replay-buffer requests get `ERR` (`LINK`), and the test accepts `TIMEOUT`. A faulted core's NI keeps sinking.
  - V-8: padded W tiles are 2 KiB aligned.
  - V-9: `dtab_rd` is request/grant.
  - V-10: `ERR` gains `err_wr`, and a write burst has one target.
  - V-11: both directions of the serial window.

### Results
- No arch.md frozen number changed (change-log row added). D1 for NVFP4 → BF16 is now 1.60x at 0.85 share (was 1.65x), because the
  staged scales share the DMA's SPM write port. A dedicated scale SRAM would have changed the frozen SRAM row, so the ring is in SPM.
- Regenerated: `make numerics` (D1 table only) and `make isa`. `make hbm` and `make links` inputs are unchanged.
- V-2 cost (asked before choosing): a barrier per kernel would cost 0.4-2.7% of a DeepSeek decode step at one kernel per layer.
  Credits cost 0 extra sync points and 0 semaphores.

### Tests
- `make pytest`: 197 passed (190 before). `test_isa.py` 18 → 23, `test_noc.py` 17 → 19.
- `make isa` (Verilator lint of `isa_pkg`) clean. No RTL changed.

### Next
- A verification pass on these fixes (check-first list at the end of spec_review.md), then Phase 3 RTL on `core/dma` and
  `core/seq`. `hbm`, `common` and `arith` can start now.
- Open: the H-9 JESD238 checklist, the Phase 4 link tests (X-4, X-5, I-7, V-7), the golden-model checks (X-6 detector, V-2
  credits), vendor PHY latencies, and the DeepSeek `hc_fn` check for numerics D2.

## Phase 2: fixes for the second verification (2026-11-01)

### Changes
The second verification (d8f4f23) confirmed V-1 to V-11 and found W-1 to W-6 (spec_review.md, Verification 2). This session
fixed all six on branch `spec-verification2-fixes`, one commit per group, with `make pytest` green before each. The
"Resolutions (verification 2 fixes)" section of `docs/reports/spec_review.md` has one row per finding.
- **Group 1 (b62bbc4), `core/dma`:**
  - W-1: the staging ring takes an entry's scales and the raw slots of its split units as one allocation at scale-read issue,
    so every allocated entry needs only tags. `isa.staging_ring` is a cycle model of the ring. The old rule and a capped
    read-ahead both deadlock in it. 16 KiB is sized for scales, and the compiler pads fold-weight row pitches to whole
    256-element tiles. `noc.py` baseline gains the scales-first edges, still acyclic.
  - W-5: non-paged block bases and `s1` are block-aligned (`isa.block_addr`).
  - W-4 (b, c): the batched `DMA_SCATTER` append, and page scale rows that tile whole 32 B blocks.
  - W-6: two text fixes.
- **Group 2 (0e23f87), compiler and runtime rules:**
  - W-3: one call site per network-facing semaphore, and reassignment after a final credit. Data-dependent bases go in an
    SPM base table, and a credit wait counts one credit per consumer.
  - W-4 (a): the H-2 tail-block reasoning is corrected. Two writes of a block are ordered by an ack.
- **Group 3 (b8b38b8), Phase 4:** W-2. An `ERR` (`LINK`) always faults the core and frees no tag. Responses for tags that are
  not outstanding are dropped, and `FAULT_ADDR` is advisory. The CP restarts the step once `STATUS` shows no outstanding tags.

### Results
- No arch.md frozen number changed (change-log row added). D1 unchanged; `make numerics` not rerun (no input changed).
- Ring model: NVFP4 → BF16 at the 87 B/cycle share runs at 154.7 / 152.9 / 127 elements/cycle at 500 / 845 / 1,000-cycle
  round trips with 16 KiB. When every fold tile is split it carries about 28, which is why the compiler pads.
- W-3 needed no design choice beyond the suggested resolution, so the user was not asked.

### Tests
- `make pytest`: 212 passed (197 before). `test_isa.py` 23 → 37, `test_noc.py` 19 → 20.
- `make isa` (Verilator lint of `isa_pkg`) clean. No RTL changed.

### Next
- A verification pass on these fixes (check-first list at the end of spec_review.md, starting with the ring deadlock probe
  against the chosen rule), then Phase 3 RTL on `core/dma`. `hbm`, `common`, `arith` and `core/seq` can start now.
- Open: the H-9 JESD238 checklist, the Phase 4 link tests (X-4, X-5, I-7, V-7, W-2), the golden-model checks (X-6 detector,
  V-2 / W-3 semaphore order, W-4 (a) write order), vendor PHY latencies, and the DeepSeek `hc_fn` check for numerics D2.

## Phase 2: fixes for the third verification (2026-11-01)

### Changes
The third verification (d015351) found Z-1 to Z-8 (spec_review.md, Verification 3). This session fixed all eight on branch
`spec-verification3-fixes`, one commit per group, with `make pytest` green before each. The "Resolutions (verification 3
fixes)" section of `docs/reports/spec_review.md` has one row per finding.
- **Group 1 (8f6cf62), `core/dma`:**
  - Z-1: every data read and every conversion unit belongs to exactly one staging-ring entry; a granule two entries share is
    two partial reads. `isa.staging_ring` takes HBM byte ranges, and the rejected shared-granule reading deadlocks with four DQs
    at the verifier's cycle numbers.
  - Z-3: `DMA_STAGE` is at least 2 KiB; the quarter-of-the-ring entry bound is a rate target; inline entries are whole rows.
  - Z-4: `SCALE_INLINE` rows (Engram) are exempt from the block-aligned base rule; a row is its elements and then their
    scales (`isa.inline_addr`).
  - Z-8: scale reads go first within their DQ and take ceil(bytes / 256) tags.
- **Group 2 (4c0cb8b), Z-2:** with the HBM share as a queue, greedy issue leaves converting streams 8-12% short of the share at a 500-cycle round trip and 23-28% at 845.
  The user chose an in-flight limit on converting reads: CSR `DMA_PACE`, 72 KiB by default, which gives the paced rates back.
  The ring stays 16 KiB.
- **Group 3 (27184c4), compiler, runtime and Phase 4:**
  - Z-5: a semaphore moves only after a drain wait for its old call site's full count.
  - Z-6: credit semaphores per buffer parity, and multi-adder flags under the parity-and-credit rule.
  - Z-7: after a link-down, the CP restarts once every core of the step is idle, the bridges report `err_done` and a 10 µs
    drain has passed. A recovery time budget is a runtime availability item, not a spec number.

### Results
- No arch.md frozen number changed (change-log row added). D1 unchanged; `make numerics`, `make hbm`, `make links` not rerun
  (no input changed).
- Ring rates with `DMA_PACE` at 72 KiB, queue model: NVFP4 → BF16 weights 155 / 150 / 127 and the paged NVFP4 KV gather
  155 / 154 / 130 elements/cycle at 500 / 845 / 1,000-cycle round trips, against a need of 155. Without it: 142 / 119 / 111
  and 136 / 111 / 104.

### Tests
- `make pytest`: 218 passed (212 before). `test_isa.py` 37 → 43.
- `make isa` (Verilator lint of `isa_pkg`) clean; the generated files changed for `DMA_PACE`. No RTL changed.

### Next
- A verification pass on these fixes (check-first list at the end of spec_review.md, starting with an independent queue
  model of the ring under `DMA_PACE`), then Phase 3 RTL on `core/dma`. `hbm`, `common`, `arith` and `core/seq` can start now.
- Open: the H-9 JESD238 checklist, the Phase 4 link tests (X-4, X-5, I-7, V-7, W-2, Z-7), the golden-model checks (X-6
  detector, V-2 / W-3 / Z-5 / Z-6 semaphore order, W-4 (a) write order), the Phase 7 converting-rate check (Z-2), vendor PHY
  latencies, and the DeepSeek `hc_fn` check for numerics D2.

## Phase 2: fixes for the fourth verification (2026-11-01)

### Changes
The fourth verification (9aea46f) found Y-1 to Y-10 (spec_review.md, Verification 4). This session fixed all ten on branch
`spec-verification4-fixes`, one commit per group, with `make pytest` green before each. The "Resolutions (verification 4
fixes)" section of `docs/reports/spec_review.md` has one row per finding. The user chose between options for Y-2, Y-3, Y-7 and Y-10.
- **Group 1 (180d3bf), `core/dma` issue side:**
  - Y-1: an entry without raw slots covers at most one data request; quarter-size scale-only entries cost 19% at 845 cycles.
  - Y-2: converting reads are counted by format (NVFP4 x1, MXFP4 x1/2, MXFP8 x1/4) against min(`DMA_PACE`, 4.5 x the ring).
    MXFP4 and MXFP8 weights keep the full rate to 1,200 cycles.
  - Y-3: one tag per request plus 512 granule credits. DQs take credits in turns of 8 and ring space in turns of 256 B, so
    neither one-granule nor multi-granule requests starve. The verifier's per-request turn-holding was rejected, because it
    starves a KV gather. Ring-space starvation was found and fixed as part of this.
  - Y-6: a converting read may issue when none is in flight; the counter has 18 bits.
- **Group 2 (cfffee9), `core/ni`, NoC, Phase 7:**
  - Y-5: partial-granule responses: payload granule-aligned, valid bytes from the tag table (`isa.rd_rsp_layout`).
  - Y-10: an epoch bit in request and `ERR` headers and CSR `NI_EPOCH`: a stale `LINK` `ERR` is dropped.
  - Y-4: the §2.5 rates are marked as single-FIFO results; per-PC effects and two options go to Phase 7.
- **Group 3 (efe47a5), compiler and runtime:**
  - Y-7: the Engram table keeps its published separate scale array, so no repack is needed and the gather is not split.
  - Y-8: a credit semaphore's base-table entry is the credits expected so far, which is also its drain target.
  - Y-9: the single-adder flag exception names `SIGNAL`s from one DQ.

### Results
- No arch.md frozen number changed (change-log row added). D1 unchanged; `make numerics`, `make hbm`, `make links` not rerun
  (no input changed).
- The pace rule (16 KiB ring), elements/cycle at 500 / 845 / 1,000 / 1,200-cycle round trips:
  - NVFP4 → BF16 weights 155 / 151 / 127 / 106;
  - MXFP4 164 and MXFP8 84 at every round trip (164 / 160 / 136 / 114 and 84 / 82 / 69 / 57 under a flat 72 KiB);
  - the paged NVFP4 KV gather 155 / 154 / 130 / 108.
- Two DQs at an 845-cycle round trip, data B/cycle: KV gather + plain 28 / 56, NVFP4 weights + KV gather 37 / 37. Without
  the fix the starved side got 5 or 0.
- 8 cores on per-PC queues (scratch model, not committed): KV gather 146 / 140 at 500 / 845, against the verifier's 144 / 132.
  Phase 7 decides.

### Tests
- `make pytest`: 224 passed (218 before). `test_isa.py` 43 → 49.
- `make isa` (Verilator lint of `isa_pkg`) clean; the generated files changed for `epoch` and `NI_EPOCH`. No RTL changed.

### Next
- A verification pass on these fixes (check-first list at the end of spec_review.md, starting with the liveness and fairness
  of the credit and ring turns). Then the `core/dma` read-issue arbiter. `core/ni` and the rest of `core/dma` can start Phase 3
  RTL now, and so can `hbm`, `common`, `arith` and `core/seq`.
- Open: the H-9 JESD238 checklist, the Phase 4 link tests (X-4, X-5, I-7, V-7, W-2, Z-7, Y-10), the golden-model checks (X-6
  detector, semaphore order including Y-8 and Y-9, W-4 (a) write order), the Phase 7 converting-rate check (Z-2, Y-2, Y-4),
  vendor PHY latencies, and the DeepSeek `hc_fn` check for numerics D2.

## Phase 2: fixes for the fifth verification (2026-11-01)

### Changes
The fifth verification (ed2ea70) found X-7 to X-16 (spec_review.md, Verification 5). This session fixed all ten on branch
`spec-verification5-fixes`, one commit per group, with `make pytest` green before each. The "Resolutions (verification 5
fixes)" section of `docs/reports/spec_review.md` has one row per finding. The user chose X-9, X-13 (b) and X-16, and took
the optional X-10 and X-14.
- **Group 1 (b8e6596), the `core/dma` read-issue arbiter:**
  - X-7: the ring turn passes whenever its holder cannot start an entry for a reason other than ring space, credits, pace or
    the issue turn, and a turn is taken only when a DQ waits. A semaphore wait inside a turn hung the core (cycle 3,649;
    182 of 400 random programs under the text, 59 with the reference's end-of-stream pass, 0 now). P1-P3 formal properties
    in PLAN Phase 3.
  - X-8: the issue turn is held for the pace against converting DQs only. At `DMA_PACE` 16 KiB a plain DQ next to paced
    weights gets 76 B/cycle instead of 17.
  - X-11: a write packet counts one credit toward the turn, so a 1 MiB write no longer locks out a reader for 4,096 cycles.
  - X-10: the ring turn is counted in data granules (NVFP4 + MXFP8 weights 17 / 66 → 40 / 40).
  - X-14: one-granule entries are paced in full (MX row gathers +27-40% at 845).
  - X-15 (a), (b), (d): the reference bullet, the `bw_dq` window, isa.md §5.
- **Group 2 (a2eea9a), `core/ni`, `hbm`, NoC:**
  - X-9: a 1-flit `RD_RSP` when the valid bytes lie in one 128 B half; a stack's busiest leaf link with 8 gathering cores
    goes from 1.21 to 0.91.
  - X-12: read requests are cut at SPM destination runs, and converting responses are placed per unit (fold tiles from
    byte 64).
  - X-13 (a), (b): the epoch is echoed in `RD_RSP` and `WR_ACK`, and acks are coalesced per epoch; one bit covers one restart.
- **Group 3 (7b0f910), runtime and ISA text:**
  - X-13 (c): every step with a faulted core is restarted, each with its own conditions and epoch toggle.
  - X-16: `SCALE_INLINE` is reserved in v1 (`ILLEGAL_FIELD`).
  - X-15 (c): the `NI_EPOCH` comment.

### Results
- No arch.md frozen number changed (change-log row added). D1 unchanged; `make numerics`, `make hbm`, `make links` not rerun
  (no input changed). The tag-table estimate grows to about 95 bits (6 KiB), which arch.md does not freeze.
- Arbitration at an 845-cycle round trip, data B/cycle under the rule: KV gather + plain 28 / 56, two KV gathers + plain
  19 / 19 / 40, NVFP4 weights + KV gather 37 / 37, NVFP4 + MXFP8 weights 40 / 40.
- `isa.staging_ring` models write transfers, semaphore waits, `SIGNAL`s and gaps. 800 random four-DQ programs complete
  under the rule.
- A cost found in X-8: at a 16 KiB pace the paced stream itself drops from about 17 to about 10 B/cycle, because the
  plain DQ's backlog lengthens its round trip.

### Tests
- `make pytest`: 230 passed (224 before). `test_isa.py` 49 → 54, `test_noc.py` 20 → 21.
- `make isa` (Verilator lint of `isa_pkg`) clean; `isa_defs.py` and `isa_ref.md` changed for `epoch` and `SCALE_INLINE`. No
  RTL changed.

### Next
- Phase 3 RTL may start on the `core/dma` read-issue arbiter, with the P1-P3 properties first, and on the `core/ni`
  response path, along with the rest of `core/dma`, `core/ni`, `hbm`, `common`, `arith` and `core/seq`. A verification pass
  should still check the new details first (check-first list at the end of spec_review.md).
- Open: the H-9 JESD238 checklist, the Phase 4 link tests (X-4, X-5, I-7, V-7, W-2, Z-7, Y-10, X-13), the golden-model
  checks (X-6 detector, semaphore order including Y-8 and Y-9, W-4 (a) write order), the Phase 7 converting-rate and gather
  checks (Z-2, Y-2, Y-4, X-9), vendor PHY latencies, and the DeepSeek `hc_fn` check for numerics D2.

## Phase 2: fixes for the sixth verification (2026-11-02)

### Changes
The sixth verification (bc8f6af, scoped to the seven check-first items) found U-1 to U-3 and notes N-1 to N-5 (spec_review.md,
Verification 6). This session fixed U-1 to U-3 and N-1 to N-4 on branch `spec-verification6-fixes`, one commit per group,
with `make pytest` green before each. The "Resolutions (verification 6 fixes)" section of `docs/reports/spec_review.md` has
one row per finding and note. The user chose U-1 (b) and the stated residual for U-3.
- **Group 1 (95a2f4b), `core/dma` TD checks, `core/ni`, tag table:**
  - U-1 (b): a fold-converted TD whose tiles could cross a 256 B granule faults `ILLEGAL_FIELD` (base or paged `byte`,
    `s1`-`s3` multiples of the tile's bytes; n0 a multiple of 256 when n2 / n3 > 1). Re-derived: MXFP4, K = 384,
    `s1` = 192 gave one 2 KiB request with units of 128 and 64 B, 24 pieces against 16 placed. v1 has no raw slots or
    response pieces; the reference keeps them for a later version. New reference `isa.cvt_source_check`.
  - U-2: converting BF16 / FP32 sources are element-aligned (`ILLEGAL_FIELD`).
  - N-4: `DMA_STAGE` holds 2-32 KiB; the tag entry is about 70 bits (4.5 KiB). N-3: slot-count wording (later version).
- **Group 2 (e42e8a0), the read-issue arbiter text:** N-1 (P1 on the listed reasons, P2's free resources, P4: a ring-queue
  head allocates within a bound) and N-2 (the paced stream's 17 → 10 B/cycle next to a plain DQ, and no cap).
- **Group 3 (a0cd582), runtime and link text:** U-3: late `WR` / `SEM` requests of a restarted step are kept out only by
  the drain conditions and the timeout; a Phase 4 test.

### Results
- No arch.md frozen number changed (change-log row added). D1 unchanged; `make numerics`, `make hbm`, `make links` not rerun
  (no input changed). The tag-table estimate drops from about 95 to about 70 bits, which arch.md does not freeze.
- N-2 rerun: paced NVFP4 / KV alone 16.6 / 17.0 B/cycle at L = 845; with a plain DQ 9.6 / 76.2 and 10.7 / 75.0.

### Tests
- `make pytest`: 231 passed (230 before). `test_isa.py` 54 → 55.
- `make isa`: `isa_ref.md` only; `isa_pkg.sv` unchanged and lint clean. No RTL changed.

### Next
- Phase 3 RTL may start on all of `core/dma` (the read-issue arbiter with P1-P4 first) and `core/ni`, along with `hbm`,
  `common`, `arith` and `core/seq`. A verification pass should check the new U-1 (b) rule and the 70-bit tag entry first
  (check-first list at the end of spec_review.md).
- Open: as after the verification 5 fixes, plus the U-3 late-request test in Phase 4.

## Phase 3: common blocks (2026-11-02)

Branch `p3-common` (not pushed), one commit per block or group, `make lint`, `make unit`, `make formal` and `make pytest`
green before each. `docs/spec` is unchanged. No ORFS or VM run (the macro branch is a stub, Phase 6).

### Blocks (`rtl/common/`, each with `tb/unit/tb_<name>.sv`)
| Block | Parameters | Ports (summary) |
|---|---|---|
| `fifo` | `WIDTH`, `DEPTH` (any >= 1), `FALLTHROUGH` (0) | `in_*` / `out_*` valid-ready, `count`, `full`, `empty`; async low reset |
| `afifo` | `WIDTH`, `DEPTH` (power of two >= 2, default 8) | `wr_*` in `wr_clk`, `rd_*` in `rd_clk`, raw `wr_rst_n` / `rd_rst_n` (either one resets both domains) |
| `credit_tx` | `NVC` (5), `DEPTH` (4), `PKT` (2) | `send`, `credit_in` (one bit per VC), `can_send`, `can_send_pkt`, `credits`, `err_underflow`, `err_overflow` |
| `credit_rx` | `NVC` (5), `REG` (1) | `free` (one bit per VC) to `credit_out` |
| `arb_rr` | `N` (any >= 1) | `req`, `hold`, `accept`, one-hot `grant` |
| `arb_prio` | `N` | same ports as `arb_rr`, lowest index wins |
| `sram_1r1w` | `WIDTH`, `DEPTH`, `GRAN` (8) | write port with byte mask, read port, registered `rd_data` |
| `sram_1rw` | `WIDTH`, `DEPTH`, `GRAN` (8) | `en`, `we`, `addr`, `be`, registered `rdata` |

Other changes: `make formal` runs every `tb/formal/*.sby` (all tasks) under `scripts/memrun`; the Makefile puts
`$(EDA_HOME)/tools/sby/bin` on `PATH`; `make syn BLOCK=x PARAMS="P=v ..."` sets top-level parameters (chparam) and writes
`build/syn_<block>.stat`. `make unit` is unchanged: each block is one file, helper modules live in the testbench file.

### Choices made (all in the module header comments)
- `fifo`: `in_ready = !full`, no dependence on `out_ready` (a full FIFO is not popped and pushed in one cycle);
  `FALLTHROUGH` defaults to 0 (registered read, no combinational `in_valid` to `out_valid` path); with it, an empty FIFO
  shows `in_*` on `out_*` in the same cycle and a word taken that way is never stored or counted.
- `afifo`: Cummings structure, registered Gray pointers, 2-flop synchronizers, array written in `wr_clk` and read
  combinationally in `rd_clk`. Either raw reset resets the whole FIFO: their AND asserts both domains asynchronously and
  each domain releases through its own 2-flop reset synchronizer, so the release needs no ordering or overlap; words in
  flight are dropped, `wr_ready` / `rd_valid` are 0 in reset. A one-sided reset is a reset-domain crossing: it changes
  the other side's `wr_ready` / `rd_valid` / `rd_data` asynchronously, so across every assertion of either input each
  side's logic is either in reset itself (asserted no later than the FIFO input) or idle (no `wr_valid` / `rd_ready`,
  and nothing samples `wr_ready` / `rd_valid` / `rd_data`) from before the assertion until its FIFO domain has left
  reset. The two ends of that window come from different places: the start only from the owner of the other input
  (it quiesces this side first; a wait on this side cannot give it), the end from a handshake or a fixed wait of three
  of this side's clock periods after the later of the two inputs goes high (sufficient, not necessary; enough when
  recovery time + 2 * jitter < period: in silicon the removal and recovery windows let a domain leave reset on the 1st,
  2nd or 3rd of its edges after both inputs are high, the 2nd in the formal model). `rd_data` changes asynchronously to
  `rd_clk` while `rd_valid` is 0, so a consumer captures it only on an edge where `rd_valid` is 1, the `rd_valid` of that
  same edge (a load enable or a mux), not a registered copy of it (an enable from the registered `rd_valid` captured
  entries 8 ps after their write in the review simulation, 1465 of 196735 captures below the `mem` budget;
  `afifo_review2/rdq/tb_rdq.sv`, not in the repo); a free-running capture register can go metastable and the `mem` budget below then means nothing (integration
  rule in the header). The side not reset
  cannot see that its words were dropped (`wr_ready` low looks like full), so a requester tracking outstanding words
  is reset with the FIFO (open question 9). No RTL change for this: an integration rule in the header. (Before review
  the two resets were used directly per domain and had to overlap; with the read reset a few `rd_clk` edges late, the
  reader saw the reset write pointer and popped never-written words.) Throughput at 1:1 with both sides always ready
  depends on the phase between the clocks (`tb_afifo`, `afifo_thr`): at zero phase DEPTH 2 gives 1/3 and DEPTH 4 gives
  2/3; with the read clock 250 ps after the write clock 0.4 and 0.8; DEPTH 8 gives 1 at both.
- `credit_tx`/`credit_rx`: a credit per flit, saturating counter (illegal events are ignored and flagged, not wrapped);
  `can_send_pkt` (>= `PKT` credits) is the virtual cut-through test for a 2-flit packet, valid only if no new head enters a
  VC while a packet is in progress on it (it does not count credits owed to a started packet's body; the router allocates
  the output VC per packet or locks the port with `hold`). `credit_rx` registers the return:
  free in cycle t, `credit_out` in t+1, counter updated at the end of t+1 (so a credit is usable in t+2, plus link time).
- `arb_rr`/`arb_prio`: `accept` is a separate input; the pointer moves only on an accepted grant. `hold` means "a packet
  is in progress": it is 1 exactly in cycles whose flit at the holder is a body or tail flit and 0 in the cycle after the
  tail is accepted (held one cycle longer, the holder's next head wins under the lock and can keep the port); while it is
  high the grant is the last winner only (zero if that requester drops its request). `arb_prio` has the same ports so the
  two are drop-in replacements; its only state is `last`. The round-robin pointer is a thermometer mask (68 cells at N = 5).
- `sram_1r1w`: same-address read and write in one cycle returns the OLD word (read-first); the output register holds
  while `rd_en` is low; contents are not reset (X until written); addresses >= `DEPTH` out of contract.
- `sram_1rw`: a write cycle leaves `rdata` unchanged (no-change mode).
- `SRAM_MACRO`: selects `sram_macro_1r1w` / `sram_macro_1rw`, wrappers with the same ports and parameters that Phase 6
  provides. Without them elaboration fails on purpose. Checked once by hand with a dummy wrapper (elaborates) and without
  (unknown module); not part of `make unit`.

### Test coverage
- **Unit** (iverilog, randomized against a reference model, with stalls, fill/drain phases and resets in the middle):
  - `fifo`: 12 configurations (DEPTH 1, 2, 3, 4, 5, 8, 16, each registered and fall-through), queue model, `count` / `full` /
    `empty` / `in_ready` / `out_valid` checked every cycle.
  - `afifo`: 9 side-by-side DUTs: 1.5:1.2 (667/833 ps half periods), 1.2:1.5, 1:1 with and without phase offset, writer 5x
    slow, reader 5x slow, near 1:1 (997/1013, drifting phase), DEPTH 2 / 4 / 8 / 16, widths 1 to 32. Ordering, no loss or
    duplication (final drain empties the model), no overflow, no phantom data (also while only the other side is in reset), five kinds of reset: both
    inputs together, write side alone, read side alone, and skewed by 3 clock edges in either order, released after
    different numbers of cycles. The pre-review RTL fails this testbench (phantom words, overflow, data errors).
    `afifo_thr` (6 DUTs, equal clocks, always ready): pops per 1000 `rd_clk` edges at DEPTH 2 / 4 / 8, zero phase 333 / 667
    / 1000 and 250 ps phase 400 / 800 / 1000, within 2.
  - `credit_tx`: 5 configurations against an integer model, legal traffic and an abuse phase (sends without a credit,
    spurious credits), reset in the middle. `credit_rx`: both `REG` settings, no credit lost or invented.
  - `arb_rr` (N = 1, 2, 3, 4, 5, 6, 8, 16): explicit-pointer model, one-hot, non-requester, fairness bound (at most N-1 accepted
    heads ahead of a persistent request), packets of 1 to 3 flits under `hold` with bubbles, stalled accepts. `arb_prio`:
    N = 1 to 16 against the lowest-index model.
  - `sram_1r1w`, `sram_1rw`: 6 and 5 configurations (DEPTH 1, 2, 8, 13, 16; one-lane, nibble, byte and 64-bit words),
    per-lane model, hot-address phases to force same-address collisions.
  - Each testbench was run against hand-made mutants of the RTL (a wrong wrap, a wrong full condition, a dropped
    saturation, a wrong pointer update, a write-first SRAM, an ignored byte mask and so on); every functional mutant fails.
    Mutants that pass are behaviorally equivalent in legal use (for example `arb_prio` updating `last` on an unaccepted
    grant) or, for `afifo`, a missing data-pointer synchronizer stage, which the functional checks cannot see (the zero-delay
    model has no metastability); latency lower bounds and throughput see it (below). Re-run on the current RTL (Phase 3
    session 2, then the fix session):
    one-stage reset synchronizer on either side (survived every task and the unit tb before; now fails `d2` / `d4` /
    `d8` at the release-edge assert, `ph2` / `ph4` and the unit tb still pass it), `rgray_w1` frozen after the first full
    (survived `cov4` with the port-level cover alone; now fails `cov4` at c8). A bypassed data-pointer
    stage (`wgray_r2 <= wgray`, `rgray_w2 <= rgray` or both) passed all six tasks and the unit tb; it now fails the latency
    tasks `l2` / `l4` (L1 / L2) and `tb_afifo`'s throughput check (`afifo_thr`: DEPTH 2 / 4 give 0.4 / 0.8 at zero phase
    instead of 1/3 / 2/3 for a single bypass, `sync_w_bypass` or `sync_r_bypass` (400 / 800 pops per 1000 `rd_clk` edges); the double
    bypass, `sync_wr_bypass`, gives 0.5 / 1.0 (500 / 1000)), and still passes `d2` / `d4` / `d8`, `ph2` / `ph4`, `cov4`. The same stage taken as the flag
    source (`rd_valid` from `wgray_r1`, `wr_ready` from `rgray_w1`) fails `d2` / `d4` / `d8`, `l2` / `l4` and the throughput
    check. Full set of 25 mutants (`scripts/afifo_mutants/mk_rtl_mutants.py`, `run_rtl_mutants.sh`; eight sby tasks and `tb_afifo` each; the `c7_w1_*` runs used the
    unit tb from before the throughput check): none survives. `c7_w*_freeze`, `c7_w2_hold` and `r_r*_freeze[_rst]`
    fail `cov4` only (and the unit tb); the reset-synchronizer mutants fail `d*` (and `l*`); the mask and flag mutants
    fail `d*`.
- **Formal** (SymbiYosys, yices and abc pdr; 52 tasks under `make formal`; at least one task of every block was also run on a mutant and failed, and
  cover tasks show the properties are not vacuous):
  - `fifo` (16 tasks, DEPTH 1, 3, 4, 5, registered and fall-through): `count`, `full`, `empty`, `in_ready`, `out_valid`
    against a shadow count, no overflow, no underflow, by BMC and k-induction (unbounded); in-order data (one tagged entry
    from push to pop) and "a stalled output keeps its data", unbounded with `abc pdr` (k-induction cannot, the stored data
    is internal state).
  - `afifo` (8 tasks, DEPTH 2, 4, 8, multiclock with two free clocks and two free resets; the only assumption is that
    the run starts in reset): Gray registers follow the binary pointers, the
    pointer chain `srw2 <= srw1 <= rbin <= wbin`, `rbin <= sw2 <= sw1 <= wbin`, at most DEPTH apart; `wr_ready` and `rd_valid`
    are exact functions of the synchronized pointers and the domain reset; a domain in reset has zero pointers on both
    sides; no overflow, no underflow; one tagged entry's data survives. Unbounded by k-induction. The properties live in
    the RTL under `` `ifdef FORMAL `` (they need the pointers). Two edge counters inside the module (cleared while either
    input is low, saturating at 2) give the release instant: `wr_rs0` / `rd_rs0` and `wrst_n` / `rrst_n` are asserted
    against the number of own-clock edges since both inputs are high (second edge in this model), so a reset synchronizer
    of one flop fails. Latency tasks `l2` / `l4` (`abc pdr`, `-DFORMAL_LAT`; k-induction cannot prove them): a word is
    announced on `rd_valid` no earlier than the 2nd `rd_clk` edge after the `wr_clk` push (L1), a freed entry offered on
    `wr_ready` no earlier than the 2nd `wr_clk` edge after the pop (L2); `d2` / `d4` / `d8` do not define it. Port-level tasks `ph2` / `ph4` (`abc pdr`, internal
    properties off): pushes and pops counted per domain, `rd_valid` only with a word pushed and not popped, `wr_ready`
    only with a free entry, any reset pattern; both fail on the pre-review RTL (wr reset a few `rd_clk` edges before the
    rd reset gives a phantom word).
  - `afifo` synchronizer depth is a structural / review check, not a functional one. With a zero-delay, step-level clock
    model a missing stage is not a functional error: a mutant that bypasses one stage (`wgray_r2 <= wgray`) passes `d4`
    and `ph4` (it fails only the latency bounds `l2` / `l4` and the throughput check, which count edges); a mutant that takes `rd_valid` from `wgray_r1` fails only `d4`, at the internal assert that
    `rd_valid` is an exact function of `wgray_r2`, and passes `ph4`. The same holds on the write side
    (`rgray_w2 <= rgray`, `wr_ready` from `rgray_w1`). The reset synchronizers are the exception: their release edge is
    counted, so a missing stage there is seen by `d2` / `d4` / `d8`.
  - `afifo` covers (`cov4`), seven at the ports (c1 to c7) and three inside the module (c8, c9, c10): a word goes through (step 9); the FIFO fills, `wr_ready` high since the
    last reset input and then low with no reset in between (step 12); the write input alone asserts while the reader
    shows a word (step 10); the read input alone asserts while the reader shows a word (step 10); a word pushed at a
    `wr_clk` edge after which the read domain is still in reset (strictly before `rrst_n` rises) is popped later with no
    reset in between (step 11); a word goes through after a write-only reset of a running FIFO (both inputs high the
    step before, words inside, a pop done; step 21) and after a read-only reset of one (step 21); and the FIFO fills and
    `wr_ready` returns with no reset input asserted in between (c7, step 14). c7 sees only the ports: it needs `rgray_w2`
    to move after full, not `rgray_w1` (a mutant that freezes `rgray_w1` after the first full still passed `cov4`). c8
    (inside the module, step 15) needs the FIFO to fill, then `rgray_w1` and `rgray_w2` each to change at least once,
    then `wr_ready` high, no reset input asserted in between; that mutant now fails `cov4`. The fill cover alone shows
    neither: its witness (step 12) fills the FIFO with `rd_clk`
    never edging. Each witness trace was read to confirm it shows what its cover says; the new one has four `wr_clk`
    pushes, one `rd_clk` pop, two `wr_clk` edges carrying the read pointer into `rgray_w1` then `rgray_w2`, then
    `wr_ready` high. With an assumption that no step has both clocks rising, a scratch run reaches all eight port-level covers at
    the same steps. c9 is the read-side mirror of c8 (the FIFO drains, `wgray_r1` and `wgray_r2` each change, `rd_valid`
    returns; step 14), c10 a second fill (`wr_ready` returns twice with no reset input in between; step 19). The read-side
    freezes (`r_r1_freeze`, `r_r2_freeze`, `r_r12_freeze` and the reset-clearing `r_r1_freeze_rst`, `r_r2_freeze_rst`)
    fail c9, `c7_w2_hold` fails c10. Before review the fill cover was `!wr_ready && wr_rst_n`, reached
    at step 1 by the reset synchronizer alone. In the next review the old read-only cover fired with the read domain
    still in reset and `rd_valid` never 1, the early-push cover pushed on the `rd_clk` edge that released `rrst_n`, and
    the write-only cover was the tail of the initial reset (empty FIFO); these are the replacements.
  - `credit_tx` / loop (8 tasks): counter in [0, DEPTH] and moves by one in the right direction only on a matching event, for
    any inputs; exact error flags; under legal usage `credits + in_flight == DEPTH`; the `credit_tx` to buffer to `credit_rx`
    to `credit_tx` loop: the buffer never holds more than DEPTH, `credits + buffered + credit_out == DEPTH`, never a send
    without a credit. Unbounded.
  - `arb_rr`, `arb_prio` (14 tasks, N = 1 to 5): grant one-hot or zero, only to a requester, granted when anything requests
    (hold low), alone is granted, hold grants the last winner only, equality with an independent reference model, a grant that
    is not accepted changes nothing, the pointer moves past the winner; `arb_rr` with hold low and all grants accepted
    grants a persistent request within N cycles (N = 2, 4, 5). All unbounded.
  - `sram_1r1w`, `sram_1rw` (6 tasks, DEPTH 2 and 4, one tracked address and lane): a read returns the last write (read-first
    for 1R1W), the output holds without a read (and in a write cycle for 1RW). Unbounded with `abc pdr`.
  - Not proved: `fifo` / `afifo` are proved at small DEPTH only (1 to 5, 2 to 8); the fall-through combinational path is
    covered by the same properties; liveness of the `afifo` (data eventually appears) is covered only by simulation.

### Synthesis (Yosys `synth -flatten`, generic cells, `make syn BLOCK=x PARAMS=...`; logs in `logs/syn_*`, all under 30 MB)
| Block | Parameters | Cells |
|---|---|---|
| `fifo` | W 32, D 8 / W 64, D 4 | 541 / 490 |
| `afifo` | W 64, D 8 / D 16 | 1,057 / 2,115 (reset synchronizers +7) |
| `credit_tx` | NVC 5, D 4 | 108 |
| `credit_rx` | NVC 5 | 5 |
| `arb_rr` | N 5 / N 8 | 68 / 105 |
| `arb_prio` | N 5 / N 8 | 30 / 51 |
| `sram_1r1w` | W 64, D 16 | 2,212 (flops and muxes; macros replace it in Phase 6) |
| `sram_1rw` | W 64, D 16 | 2,214 |

### Open questions for review
1. `fifo` `in_ready` does not depend on `out_ready` (no push through a full FIFO); is a throughput loss at DEPTH 1 acceptable
   (a DEPTH-1 FIFO runs at half rate)? A skid-style FIFO would be needed for a full-rate single entry.
2. `FALLTHROUGH` default 0. Which of the NoC and NI users want the combinational path?
3. Credit return is registered in `credit_rx` (+1 cycle). The router's credit loop latency, and whether the credit wire is one
   bit per VC per cycle (assumed) or coded, is a `noc/router` decision; `credit_tx` takes one bit per VC.
4. `can_send_pkt` needs `PKT` credits for a packet head: noc.md allows packets of at most 2 flits, so `PKT` = 2. If a VC's
   depth is 2 a 2-flit packet needs the whole buffer. `noc/router` must not start a second packet on an output VC before
   the first one's tail (see Choices).
5. `arb_rr` `hold` is "packet in progress" and the pointer moves on the head flit's accept. If the router wants the pointer to
   move on the tail instead, change one line; fairness among packets is the same.
6. SRAM read-during-write: `sram_1r1w` read-first (old data), `sram_1rw` no-change. Check both against the ASAP7 fakeram models
   in Phase 6; if a macro returns undefined data, the wrapper adds a bypass register (read-first) or `core/spm` must not read and write
   one bank word in a cycle. `core/spm` bank logic must not rely on write-first.
7. `afifo` CDC constraints for Phase 6 STA. The full text is in the afifo header. Command options and behaviour were
   checked against the OpenSTA in the pinned OpenROAD (18f08d7) on ORFS ASAP7 netlists of the block (flattened under a
   wrapper with a producer and a consumer register, and the block as top) and, for one configuration, after ORFS
   floorplan and CTS. `make cdc-sta` (`scripts/cdc_sta/`, results in `build/cdc_sta/summary.txt`, 577 checks, all pass) repeats this from the
   header text taken literally and also checks the set sizes, the max / min delay bounds, recovery / removal from
   `wrst_n` / `rrst_n` to every pointer set (one result per set, exact RESETN pin counts, text and post modes), hold slack
   at the synchronizer second stages (every pin printed, with the parasitics of each stage) and, for `w8d8cts`, the flow
   through global and detail route; nothing is checked by simulation, lint or formal. The header's commands (`T_wr` 667 ps,
   `T_rd` 833 ps, `U_g` = `U_m` = 25 ps) put every crossing endpoint in the path-delay group for max and min, with clear
   arcs off and on, none left in a clock group. Endpoints `wr_clk` to `rd_clk` / `rd_clk` to `wr_clk`: WIDTH 8 DEPTH 8
   12 / 4 (4 `wgray_r1` + 8 consumer / 4 `rgray_w1`), DEPTH 2 10 / 2, DEPTH 16 13 / 5, WIDTH 1 5 / 4, WIDTH 64 68 / 4.
   `rbin` to the consumer stays an `rd_clk` path, recovery / removal at the four synchronizer flops is gone, and
   `wrst_n` to `wbin` / `rrst_n` to `rbin` are still timed. The block-level form gives the same on the block (8
   `rd_data` ports in place of the consumer).
   - The commands belong in the SDC the flow reads from floorplan on (ORFS `SDC_FILE`) as well as in signoff STA. With an
     SDC holding only the two clocks, floorplan and CTS `repair_timing` each worked on 12 of the 16 crossing endpoints
     (with clear arcs off the 8 consumer and 4 `rgray_w1` violate at the flow's FF corner and the 4 `wgray_r1` fail only at
     TT; with clear arcs on all 16 violate at FF, `w8d8ctsc` 12 of 16 / 16 of 16; `wr_clk` /
     `rd_clk` 667 / 833 ps treated as related clocks, edges 1 ps apart; WNS -67 ps at floorplan, -59 ps in
     CTS, worst `rgray_w1[3]/D`; `build/cdc_sta/work_w8d8ctsc/logs/.../2_1_floorplan.log`, `4_1_cts.log`). With the
     header's SDC neither stage finds a setup violation (`make cdc-sta`, `w8d8cts` and its control `w8d8ctsc`).
   - `-ignore_clock_latency` ignores `set_clock_uncertainty` and the capture clock's latency (slack unchanged with
     uncertainty 100 ps or `rd_clk` latency 200 ps; `claims/unc.log` under `/Volumes/EVO970PLUS/claude/afifo_review/`, as are the other `claims/` and `logs/` files named below) but not the launch clock's source latency. With
     ideal clocks, `set_clock_latency -source 150` on `wr_clk` moved the slack at one endpoint, `wgray_r1[3]/D`, from 527
     to 377 ps max and from 71 to 221 ps min (the worst bit of the four: 527.1 -> 377.1 max, 60.0 -> 210.0 min; on `rd_clk`,
     the capture clock there, nothing moved; both clocks: same as `wr_clk` alone; `claims/unc.log`, `logs/batch3.log`,
     `afifo_review2/lat/lat_syn.log`); with propagated clocks it is ignored (527 / 71 unchanged; `logs/batch3.log`). So in pre-CTS `repair_timing` and any ideal-clock STA a source latency L on a launch clock makes
     the max slack L worse and the min slack L better (hold looks safe, setup is repaired against an L that is gone
     after CTS). The header says: no `set_clock_latency -source` on `wr_clk` / `rd_clk` in the flow's SDC; if a shared
     SDC carries one, do not read the crossings' min slack from an ideal-clock run, and sign off propagated. Launch-clock
     network latency (`set_clock_latency` without `-source`, 150 ps on `wr_clk`, `rd_clk`, both) was measured too: ignored
     with ideal and with propagated clocks, no slack changed, on the synthesis netlist and after CTS
     (`afifo_review2/lat/lat_syn.log`, `lat_cts.log`).
   - `U` is one number per item, derived by hand, so the margin is written into the numbers. Every skew term is a worst
     case (the latest clock arrival over one register set minus the earliest over the other), and the min delay is
     measured from the launching flop's clock pin, not the ideal edge: the skew terms account for that. Gray: `U_g` =
     period jitter of the launching clock + skew across the launching registers + skew across the capturing registers
     (a synchronizer can capture a code two steps away only if the spread of its sampling instants exceeds
     `T - J - Sl + min(arrival after edge i+1) - max(arrival after edge i)`; with the max delay `T - U_g` and a min
     delay that is at least the capture flop's hold, `U_g >= J + Sl + Sc` rules it out). The capture clock's jitter does
     not enter and the min side needs no margin. mem: `U_m` = `rd_clk` period jitter over two periods + how much later
     the `mem` registers' `wr_clk` arrives than the Gray source registers' + how much later the `wgray_r1` flops'
     `rd_clk` arrives than the consumer register's - (the Gray path's minimum arrival at `wgray_r1` minus its hold,
     which is >= 0 and may be dropped). The min delay includes the capture flop's library hold (8.9 ps required at a
     `wgray_r1` D pin for `set_min_delay 0`, checked); it keeps a pointer change from being captured by an edge that
     samples before its launching edge, which the mem budget relies on. It is not only the replacement for a
     meaningless cross-clock hold check.
   - No `set_clock_groups` on `wr_clk` / `rd_clk` and no `set_false_path` between them. In this OpenSTA an `-asynchronous`
     group or a false path removes `set_max_delay` without a warning (after a clock group, `set_max_delay -reset_path`
     does not restore it). A `-hold` false path removes `set_min_delay` only, a `-setup` one `set_max_delay` only, a plain
     one both. Only `-asynchronous -allow_paths` keeps both. `-exclusive` is not an option here (STA-0400); the forms are
     `-logically_exclusive` / `-physically_exclusive` (not tried). Logged in `claims/{grp,grpa,grpx,fp,fph,fps}.log`.
   - Gray pointers, by endpoint: `set_max_delay -ignore_clock_latency -from [get_clocks wr_clk]` to the `wgray_r1` D
     pins, `T_wr - U_g`, plus `set_min_delay -ignore_clock_latency` 0 (`rgray_w1`: `rd_clk`, `T_rd - U_g`). Not `-from`
     the Gray registers: Yosys merges `wgray[AW]` into `wbin[AW]` and `rgray[AW]` into `rbin[AW]` (flattened or kept
     hierarchy; only the MSB, checked at DEPTH 2, 8, 16, WIDTH 1 and 64), so that form left the MSB under default
     cross-clock timing. No bus-skew check is needed with `U_g` as above (OpenSTA has none). `-datapath_only` is
     Vivado's option and does not exist here.
   - `mem` to the consumer: the budget is 2 `rd_clk` periods less `U_m`, not one `wr_clk` period. Top level:
     `-from` the `mem` registers `-to [get_clocks rd_clk]`, no `-through`. The old `-through` the `rd_data` pins was
     dropped by OpenSTA with a warning (STA-0472): those pins do not exist after a normal link, and a flattened netlist
     has no `rd_data` net. Block level: `set_output_delay -max` of the consumer's share and `-min` of its minimum
     external delay minus its hold on `rd_data`, and `-from` the `mem` registers `-to` the `rd_data` ports at the full
     `2*T_rd - U_m`. OpenSTA subtracts the `-max` output delay itself (1641 ps became 1341 ps with 300 ps), and without
     an output delay `rbin` to `rd_data` is unconstrained. A plain `set_output_delay 300` also sets the hold side: the
     required time of the `rbin` to `rd_data` hold check was -300 (vacuous); with `-min -10` it is +10 (`make cdc-sta`
     `blk_rbin_min_required`, log `rev2_staF`).
   - Resets: one exception, `set_false_path -through` the `arst_n` net. It covers recovery / removal at the four
     reset-synchronizer flops. `wrst_n` / `rrst_n` to the pointer flops keep normal recovery / removal. Clear arcs are
     off by default here (`sta_preset_clear_arcs_enabled` 0), so no assertion path is timed; with them on, `wrst_n` /
     `rrst_n` reach the `wgray_r1` / `rgray_w1` D pins through the clear arcs of the Gray registers (and of
     `wbin[AW]` / `rbin[AW]`), and the endpoint form covers them.
   - Minimum pulse width on `arst_n`: a runt pulse that clears one domain only brings back phantom words. OpenSTA does
     not report `set_min_pulse_width` on a RESETN pin, so this is a rule on the reset sources: registered or
     glitch-free, low for at least one period of the slower clock. `set_min_pulse_width 500` on the four RESETN pins
     reported nothing (`claims/mpw.log`). The ASAP7 cell limit, read from the liberty `min_pulse_width` table of
     `DFFASRHQNx1` RESETN (RVT SEQ): 25.6 ps at TT and 20.1 ps at FF for an input slew up to 10 ps, 30.5 / 23.2 ps at
     20 ps, 40.3 ps at 40 ps.
   - Names: the constraints need only the `wgray_r1` / `rgray_w1` flops, the `mem` registers and the `arst_n` net, and
     these survive flattening (`arst_n` still drives exactly the four synchronizer RESETN pins after CTS), so a kept
     hierarchy is optional. The `mem` cells are `u_f.mem[j][k]`, at WIDTH 1 `u_f.mem[j]`: select them with `u_f.mem[*`.
     After floorplan the TIEHI cells are named after flops (`u_f.wgray_r1[0]$_DFF_PN0__26`), so select the other flops
     by cell type or pin, not by name pattern alone (`scripts/cdc_sta/sel.tcl` does). If a hierarchy is kept,
     `SYNTH_KEEP_MODULES = *afifo`: the module elaborates as `$paramod$<hash>\afifo`, and `afifo` matches nothing (Yosys
     warns and flattens it). Never `SYNTH_RETIME_MODULES`.
   - `set_dont_touch` (replaces the earlier instance list, which broke the flow). In the pinned OpenROAD an instance
     `dont_touch` on the synchronizer flops stops `clock_tree_synthesis` with CTS-0137 ("Clock net wr_clk has 14 input
     pin(s) of dont_touch instances"; the scripted flow with the instance list at floorplan stops the same way, control
     `w8d8ctsi`), and with the Gray source flops on the list `repair_design` stops with RSZ-3006 when it has to buffer
     `wrst_n` / `rrst_n` (seen at `max_fanout` 10; those nets have 15 RESETN loads at DEPTH 8; logs `rev2_staE`,
     `rev2_staE2`, `rev2_staE3`). Instance `dont_touch` does not stop buffering of the flops' nets: with a 60 ps hold
     margin `repair_timing` put an HB4xp67 hold buffer between every `wgray_r1` / `rgray_w1` / `wr_rs0` / `rd_rs0` and its
     second stage, identically with and without the list; `dont_touch` on the stage-1 output nets and the stage-2 D nets
     (both when synthesis put an inverter between) removed them (`rev2_staE3b`, `rev2_staE4`). The header now says:
     net `dont_touch` on the synchronizer-internal nets and on `arst_n` from floorplan on (ORFS `PRE_FLOORPLAN_TCL`; the
     flag stays in the database through CTS); instance `dont_touch`, if wanted, only after CTS (`POST_CTS_TCL`). Scripted
     (`make cdc-sta`, `w8d8cts`, hold margin 40 ps): floorplan and CTS run, no hold buffer on the 20 synchronizer nets, the
     21 nets and 28 flops carry `dont_touch` after CTS and after global and detail route (`4_cts`, `5_1_grt`, `5_route`),
     `arst_n` still has 5 pins. Controls: without `dont_touch` (`w8d8ctsn`) 10 buffer pins on those nets. The price: the
     10 stage-2 D pins sit at +29.8 to +34.2 ps hold slack at the flow's FF corner, inside the 40 ps margin, and
     `repair_timing` cannot touch them (RSZ-0064 at CTS and global route), so a real hold violation there would not be
     repaired. The number depends on the stage's parasitics: worst 30.6 ps after CTS (placement estimate), 29.8 ps after
     global route (`estimate_parasitics -global_routing`), 30.9 ps after detail route (RCX extraction of the routed wires,
     read back from a SPEF, as the flow's final report does); `post_s2_hold` (`make cdc-sta`) prints every pin and fails
     if any of them goes below 0.
   - Post-CTS and after global and detail route (`4_cts.odb`, `5_1_grt.odb`, `5_route.odb` with the SDC as the flow wrote
     it back, propagated clocks, the flow's corner; the parasitics of each stage: placement estimate, global-route
     estimate, RCX extraction): the header's selections resolve and all 12 / 4 endpoints stay in the path-delay group, max
     and min, clear arcs off and on, none of the 16 violated.
   - What `make cdc-sta` reproduces, and what only logs outside the repo show. Reproduced by `make cdc-sta` (and in
     `build/cdc_sta` after a run): set sizes, the bounds and library terms of every crossing class, path-group membership
     with each category excluded in turn (`excl_*`), recovery / removal removed at the four synchronizer flops and kept
     from `wrst_n` / `rrst_n` to the eight pointer sets, clear arcs off and on, the blk form and `blk_rbin_min_required`,
     the flow through CTS and route with no setup repair (`w8d8cts`), the 12 related-clock endpoints repaired against a
     clocks-only SDC (`w8d8ctsc`, 12 of 16 violated with clear arcs off, 16 of 16 on), hold buffers without `dont_touch`
     (`w8d8ctsn`), CTS-0137 with instance `dont_touch` before CTS (`w8d8ctsi`), `dont_touch` flags, and the stage-2 hold
     slacks per stage (30.6 / 29.8 / 30.9 ps worst after CTS / global route / detail route). Only in logs outside the repo
     (`afifo_review/`, `afifo_review2/`): the behaviour of `set_clock_groups` and `set_false_path` on the crossings
     (`claims/`), `-ignore_clock_latency` against uncertainty, source and network latency (`claims/unc.log`,
     `lat/`), the TT-only failure of the 4 `wgray_r1` in the clocks-only run, the 60 ps hold-margin buffer runs and
     RSZ-3006 at `max_fanout` 10 (`rev2_sta*`), the minimum-pulse-width numbers and the unreported `set_min_pulse_width`
     (`claims/mpw.log`), the output-delay arithmetic (1641 -> 1341 ps), the `rd_data` capture-pattern simulation (`rdq/`).
     The harness itself was run against 25 header and netlist mutants (`scripts/afifo_mutants/mk_header_mutants.py`,
     `run_header_mutants.sh`): every one fails except
     `n_allow_paths` (expected to pass: `-allow_paths` keeps both bounds) and `r_keep_msb` (`(* keep *)` on `wgray` still
     merges the MSB; not checked whether it is equivalent). The three reset mutants `n_fp_wbin_rst`, `n_fp_rbin_rst`,
     `n_fp_rgray_w1_rst` (false path to part of a reset set) now all fail the `rst_*` checks; before only the first did.
     `n_hold_unc` (50 ps hold uncertainty) fails in the flow itself (RSZ-0060 in CTS), not at `post_s2_hold`.
   - Still open: synchronizer depth (2 flops, data and reset) against the ASAP7 MTBF numbers; the SDC and the two hook
     scripts as Phase 6 files (the scripted flow generates them from the header text); the steps after detail route
     (fill, final reports) with `dont_touch`; the 12 / 16 counts and slacks are for one build, WIDTH 8 DEPTH 8.
8. Formal properties of `afifo` sit in the RTL file under `` `ifdef FORMAL ``. The other blocks keep them in `tb/formal`.
9. HBM controller reset sequence (hbm.md). The controller has one `rst_n` for `clk` and `clk_mc`, and hbm.md does not
   say whether the `clk_mc` domain is ever reset alone (for example on PHY retraining or error recovery). This applies
   only with `MC_SYNC = 0`: `TINY` and `MID` use `MC_SYNC = 1` (one clock, hbm.md H-1). The microarch.md `hbm/ctrl`
   interface table lists `clk_mc` and no reset (no table there lists one). If `clk_mc` is reset alone,
   then under the afifo reset-domain rule the `clk` side of each crossing must be in reset itself with its reset asserted
   no later than the `clk_mc` reset, or idle (no `wr_valid` / `rd_ready`, nothing sampling the FIFO outputs) from before
   the `clk_mc` reset asserts until the FIFO's `clk` domain has left reset (a handshake, or a wait of three `clk` periods
   after the later of the two resets goes high, which needs recovery time + 2 * jitter < period; afifo header). The start of that window has to come from whoever owns the `clk_mc`
   reset. The words in flight are dropped and nothing at the ports says so (`wr_ready` low looks like full). So a read
   request in a dropped FIFO leaves its tag waiting forever in the requester's tag table, unless the requester is reset
   with it or a separate "flushed" signal retires the outstanding tags. hbm.md needs to define who quiesces the `clk` side, how outstanding tags and `WR_ACK` counts
   are retired, and the order of release.

### Next
- `make syn` numbers are generic-cell counts, not ASAP7 areas (Phase 6).
- Phase 3 `arith` and the leaf blocks can instantiate these blocks; `flit_t` width goes in through `WIDTH`.

## Phase 3: arith (2026-11-07)

Branch `p3-arith` (commits `95d918d` to `2cb3258`: blocks, spec, review). The fixes of the independent review
([arith_review.md](arith_review.md), F1-F13) are commit `51f8cbe`; its re-review ([arith_review2.md](arith_review2.md), commit
`4898eb9`) found them fixed or partly fixed, and the fixes of that re-review (below, "Second review") are commit `64e4a96`.
`make lint`, `make unit` and `make pytest` (`test_arith.py`: 32 tests) are green. The spec for the blocks is
[arith.md](../spec/arith.md); numerics.md N-2, N-3, N-5 and N-6 now state the nine places where it was silent or inconsistent.

### Blocks (`rtl/arith/`, 20 files, each with a `tb/unit/tb_<name>.sv` except the helpers `fp_round`, `fp_addu`, `mx_exact`,
tested through the blocks that instantiate them)
- Rounding core `fp_round` (exact magnitude to any binary format: RNE, subnormal grid, FTZ, saturate / Inf, `SUBN_MAX`),
  `fp_addu` (aligner with sticky).
- MXU PE: `fp8_mul`, `fp8_dot4`, `bf16_dot2`, `mxu_pe_dot` (mode mux), `mxu_pe` (dot, FP32 add, optional registers).
- FP32: `fp32_add`, `fp32_mul`, `fp32_max` (write-add `MAX_F32`), `acc_update` (RESCALE / column scale / OVERWRITE lane).
- Converters: `cvt_fp32_fp` (BF16, FP16, E4M3, E5M2, E2M1), `cvt_fp32_int`, `cvt_dec` (exact decode to FP32 or BF16),
  `mx_exact` + `mx_dequant_bf16` (MXFP4, MXFP8, NVFP4 -> BF16).
- DMA fold: `fold_key`, `fold_amax`, `fold_c`, `fold_quant`.

Other changes: `make unit` also builds the arith testbenches (all `rtl/arith` files per testbench, run from `build/`, vectors from
the new `make arith-vec`, compiled inside `build/arith` for `counts.svh`); `make lint` also lints every arith block as its own top and 24 parameter variants; `make syn
BLOCK=<arith block>` reads the whole directory; `make pytest` includes `test_arith.py`. `sw/npumodel/arith_ref.py` is the exact
integer reference (values as sign, integer, exponent) and `scripts/gen_arith_vectors.py` writes the vectors.

### Test results
| Test | Vectors |
|---|---|
| `fp8_mul` exhaustive, expected from `ml_dtypes` | 65,536 pairs |
| `fp8_dot4` / `bf16_dot2` | 200,908 / 168,075 |
| `fp32_add` / `fp32_mul` / `fp32_max` | 138,254 / 125,750 / 55,749 |
| `mxu_pe_dot`, `mxu_pe` (four pipeline variants, reset tests), `acc_update` | 26,937, 26,937 rows, 20,012 |
| `cvt_fp32_fp` (BF16, FP16, E4M3, E5M2, E2M1) | 285,889, 280,501, 90,805, 90,780, 90,089 |
| `cvt_fp32_int` (INT8, UINT8, INT16, INT32, UINT32) | 42,810, 42,800, 42,798, 42,800, 42,800 |
| `cvt_dec`, `mx_dequant_bf16` | every code / every (element, scale) pair |
| `fold_*` (64-element tiles), `fold256_*` (256-element tiles) | 1,504 / 1,508 / 1,503 and 556 per format (MXFP4, MXFP8, NVFP4) |
| `foldq_*` (`fold_quant` alone, SATURATE reachable) | 24,576 / 96,000 / 24,576 rows |

These are exact row counts: `build/arith/counts.svh` is written by the generator, every testbench requires its count, and `make unit`
fails on a missing, short or long vector file; `scripts/arith_vectors.lock` (rows and SHA-256 of every file) binds `build/arith` to the generator. 283 hand-derived cases (`scripts/arith_hand_cases.py`) are among the vectors.
All bit-exact against the reference, including flags. `test_arith.py` ties the reference to `numerics.py` and `ml_dtypes`
(signed zeros compared, FP32 subnormal inputs, NaN tiles in the fold, no skipped cases) and the hand literals to the reference.

### Mutation check (`scripts/arith_mutants/run_arith_mutants.py`, 39 mutants, 6 min, 0.2 GB; run under `scripts/memrun`)
35 mutants fail their testbench; four pass and are equivalent (the three new ones, `acc_nan_made`, `acc_nan_part`, `int_rs24_off`,
are killed; `bf16_noftz` became `bf16_ftz`, which re-introduces the flush that F6 removed). A mutant run that finds a vector
file missing, short or long is reported as an ERROR, not as a kill, and a `build/arith` that differs from the lock stops the run. The first
reviewer's 44 mutants (`scripts/arith_review/my_mutants.py`): all as predicted (36 killed, including `dot_width38`, `pe_flags_norst`,
`pe_fdot_norst`, `foldq_sat_off`; the eight equivalent ones survive). The re-review's 35 (`my_mutants2.py`, 408 s): all as predicted, 30
killed and 5 equivalent; the six that survived at `51f8cbe` are killed (below). 118 mutants in all: 101 killed, 17 equivalent.
- `g_fp32_3`, `g_bf16_11`: the aligner at the proven bound `SW + G = 27` (the RTL runs one bit above it: `fp32_add` G = 4, `bf16_dot2`
  G = 12). One bit lower (`g_fp32_2`, `g_bf16_10`), or the sticky OR removed at either width, fails.
- `foldc_hi`: a normalized significand of exactly 0xE1 cannot occur for the supported elements and scales.
- `add_subn_full`: the full subnormal shifter in `fp32_add` changes only area (sums of normal FP32 values below 2^-126 are exact).
The first mutation round found two gaps in the vectors, both closed: no vector had both BF16 products infinite with opposite
signs and no NaN operand, and none had a power-of-two product cancelled by a much smaller one (the lost sticky bit merging with
the guard bit) or a sum rounding up into 2^-126.

### Area (Yosys generic cells, `make syn`, flattened, native)
| Block | Cells | | Block | Cells |
|---|---|---|---|---|
| `fp8_dot4` | 2,462 | | `fp32_mul` | 4,025 |
| `bf16_dot2` | 2,147 | | `acc_update` | 9,744 |
| `fp32_add` | 1,455 | | `cvt_fp32_fp` (BF16) | 428 |
| `mxu_pe_dot` | 4,505 | | `mx_dequant_bf16` | 192 |
| `mxu_pe` (registered) | 6,580 | | `fold_quant` (per element) | 249 |
At 6.6k cells a 64 x 64 array is about 27 M generic cells per core, so `FULL` stays a simulator-only configuration and the array
is synthesized hierarchically (capacity.md); the PE is the block to optimize in Phase 6. The first version of `fp32_add` had a
26-bit guard field (1,855 cells; 5,186 for `mxu_pe_dot`); the test-backed minimum cut them to 1,455 and 4,505.

### Findings and their resolution
The independent review ([arith_review.md](arith_review.md)) found no wrong result and 13 findings; the fixes are commit `51f8cbe`.
- Regression (F1, F2, F4, F5, F10): exact vector counts (`counts.svh`), `make unit` fails on a missing, short or long vector file,
  corner 4-way FP8 sums up to 4 x 448^2, a real `mxu_pe` reset test, the `REG_DOT=1, REG_OUT=0` variant, 256-element fold tiles,
  `fold_quant` tested alone so that SATURATE fires.
- RTL (F3, F6, F9): `acc_update` takes `NAN_IN` from external operands only; `cvt_fp32_fp` keeps BF16 subnormals;
  `cvt_fp32_int` has no out-of-range bit select (`rs > 24`).
- Spec (F6, F7, F8, F12, F13): numerics.md N-2, N-3, N-5, N-6 and the principles now state FP16 -> BF16 rounds, FP32 -> BF16 keeps
  subnormals, round-then-flush, the SATURATE / INVALID / NAN_IN / `MAX_F32` rules and the fold of a tile with NaN; the golden
  model has `cvt_fp32_bf16` and a `fold_e4m3` that ignores NaN in `amax`; `c` is in [-144, 127].
- Tests (F11): `test_arith.py` compares signed zeros, feeds FP32 subnormals, has no silent skips and ties the hand literals to `arith_ref`.
The independent oracle `scripts/arith_review/indep.py` has two changes after the review, both approved: the F3 flag rule and the F6
decision (line 249, FP32 -> BF16 keeps subnormals). `check_files.py` reports 0 disagreements on every row of `build/arith`.

### Second review (re-review of `51f8cbe`, [arith_review2.md](arith_review2.md)): fixes in `64e4a96`
The re-review found the 13 findings fixed (9) or partly fixed (4), 6 surviving mutants, an infrastructure that did not bind the vectors to
the generator, and 8 stale or contradictory spec sentences. Decisions of the integrator: A1 `INVALID` stays when the ACC lane also has a
NaN input ("operation" = one multiply or add); A2 an `OVERWRITE` copy of a NaN `part` sets no `NAN_IN`; A3 short-tile padding is element 0
with a finite scale and the integrator drops its `v` and flags; A5 write-add operands are DAZ; A7 a write-add or max at an SRAM target
sets the target core's `FP_STATUS`. A4 (`MAX_F32` returns the zero of a DAZ'd subnormal) and A6 (every converter raises `NAN_IN`) are the
behaviour the RTL, reference and oracle already had; numerics.md states them now.
- Spec: numerics.md N-1 (BF16 / FP32 subnormals), N-2 (the FP16 note, `sat` ties for E5M2 / E2M1, INT NaN flag, short-tile padding, fold
  step 4: the NVFP4 multiply, the MX scale below 2^-126 against the golden model, the `VQUANT` Inf / NaN block rule and its flags), N-5
  (MXU Inf, SATURATE, INVALID / NAN_IN per operation, write-add DAZ, `MAX_F32`, whose `FP_STATUS`), N-6; arith.md "Integration contract"
  (8 rules for `core/mxu`, `core/acc`, `core/dma`, `core/ni`) with pointers in microarch.md §2.2, §2.5, §2.6; the two stale counts here and
  in arith.md.
- Vectors: four hand `acc_update` cases (Inf is not a NaN in the lane's `NAN_IN` rule, flags 9, an `OVERWRITE` copy of a NaN),
  `tb_mxu_pe` reset cases (a dot that overflows alone; a flagging row in the first cycle after release), `fold_amax` with N = 3, 5, 48,
  100, 255. They kill `acc2_inf_as_nan`, `acc2_pinf_as_nan`, `acc2_inv_supp`, `pe2_fdot_ovf_norst`, `pe2_mask_first`, `fold2_pad_ones`.
- Infrastructure: `scripts/arith_vectors.lock` (committed rows and SHA-256 of all 42 files) is verified by `make arith-vec` / `make unit`
  and by `run_arith_mutants.py`; zero counts are refused by the generator, the lock and every testbench; the testbenches include
  `counts.svh` from `-I build/arith`. `attack_infra.sh` S1, S3 and S4 now all fail (caught by the lock), S6 shows the testbench's own zero
  guard, and `make unit` on the truncated S1 copy stops in `arith-vec`.
- Results: `make lint` (53 runs), `make unit` (all pass, 81 s), `make pytest` (263 passed), the 39 + 44 + 35 mutants as predicted,
  `check_files.py` 0 disagreements on 2,368,493 rows of 41 files, `handpicked.py` 283 cases and `spec_cases2.py` 78 cases agree.
- Re-check of `64e4a96` (arith_review2.md, last section): every survivor killed, S1 / S3 / S4 fail (also through the Makefile),
  C1-C4, C7, C8 and A1-A7 resolved. The three documentation items it left open are closed:
  - C5: the NVFP4 scale product rounds up to 2^-126 for the single value 2^-126 − 2^-150 (numerics.md N-2 step 4, arith.md contract item 6);
  - C6: N-8 now exempts MX tiles with `amax < 2^-118` from the bit-exact ACC / MXU rule;
  - the commit wording above.
  The integration gap I-1 is closed too: microarch.md §5 has `fp_flags` ports on DMA, MXU, ACC, VPU, NI and GSRAM, with
  `fp_flags_in` on `core/seq`, and states their valid qualification and their ordering against completion semaphores and acks.
- Stray `counts.svh` (S5 residual): the arith testbenches are now compiled inside `build/arith` (Makefile `unit`,
  `run_arith_mutants.py`, `run_tb.sh`, `netlist_int.sh`). Icarus searches the compile directory before any `-I`, so the locked
  file is the one found. `make unit` with an all-ones `counts.svh` planted in the repo root still passes with the real counts.
- GSRAM attachment (found while adding the flag ports; it predates the arith work): microarch.md described the slice both as
  its own endpoint on router port 6 and as a target behind the core's network interface (`tgt_gsram`). Decided: the slice is
  its own SRAM target on port 6, with its own write-add ALU, ack coalescing and epoch copy, built from the same SRAM-target
  module as the NI's SPM target. The NI is a target for SPM and CORECTL only. Edited: microarch.md §2 diagram, §2.6, §3, the
  NI and GSRAM tables; numerics.md N-5; arith.md contract item 1; PLAN.md `core/ni`. noc.md, which already has the six-port
  router, is unchanged.

### Next
- `core/mxu` instantiates `mxu_pe` (skew, shadow weights, stationary load) and `core/acc` instantiates `acc_update`; the MXU test
  against `numerics.mxu_mm` is then the cocotb GEMM of PLAN Phase 3.
- `core/vpu` / `core/sfu`: FMA (reuse `fp_round`, a 48-bit aligned add in front), exp / reciprocal / rsqrt, `VQUANT` scale logic
  (E8M0 exponent, NVFP4 block scale) on top of `cvt_fp32_fp`.
- `core/ni` write-add ALU: `fp32_add` / `fp32_max`, `ADD_BF16` as `fp32_add` + `cvt_fp32_fp #(FMT=0)`.
- Phase 6: stage cuts of `mxu_pe` against the clock, BF16 multiplier cost against sharing the FP8 ones.

## Phase 3: core/mxu (2026-11-08)

Spec: [mxu.md](../spec/mxu.md). RTL: `rtl/core/mxu_array.sv` (PE grid, double-buffered stationary words with the swap skewed down the rows, A skew,
sideband chain, flag qualification), `mxu_wpack.sv` (SPM lanes of a load beat -> 128-word window), `mxu.sv` (decode and legality checks, load FSM,
stream FSM, scale port, exit stage, completion). Tests: `tb/unit/tb_mxu_array.sv`, `tb/unit/tb_mxu.sv`; vectors `scripts/gen_mxu_vectors.py` (`make mxu-vec`).

### Design decisions (mxu.md §8)
- One parameter `DIM`; the SPM lane of the MXU ports is `DIM` bytes, so there are always 4 load groups of `DIM/4` PE rows and `DIM/2` beats per tile.
- N outer, K inner; ACC read-modify-write hazards are excluded by pass spacing (`ACC_LAT`), not by interlocks. The spacing rule was wrong across instructions (review R-1: the next instruction's rows need not match the previous pass's rows in order; M = 9 then M = 1 on its row 5 updated 6 cycles apart at `ACC_LAT` 8); since M-22 the last pass of an instruction is followed by a gap of `ACC_LAT - 2` whatever `M`.
- Shadow load by group, because the swap reaches PE row `i` `i` cycles after row 0.

### Results
- `mxu_array`: 8 x 8 and 16 x 16 against an independent model on `arith_ref`: bit-exact y, flags every cycle, `swap_tap` timing per buffer and group; 17 mutants, all killed.
- `mxu`: 6 instructions per size on DIM 8 and 16, with and without grant stalls: the whole ACC image is bit-exact against the logical `A x W`; 29 illegal
  instructions raise the right cause; one 32 x 32 decode instruction is bit-exact and checks the tile period; a long-A-stall run (the loader runs ahead of the swap) exercises the buffer interlock; 21 mutants of `mxu.sv` / `mxu_wpack.sv`: 17 killed, 4 equivalent. Lint clean at DIM 8, 16, 32, 64.
- 2026-11-08, RTL follows the MXU decisions M-10 .. M-18 (review S-1, S-7, S-9, S-10): 64 B alignment in every config, no `4N` rule, `M = 0` completes in order, reserved / undefined word fields fault, 64-bit `FAULT_ADDR` with the M-18 priority, `cs` row pitch `CSP` and row `t >> log2 R`, `RANGE` on operand extents. `tb_mxu`: 13 instructions per size and 72 illegal ones with their addresses; `make lint`, `make unit` and `mxu.sby` (all four tasks) pass; `scripts/mxu_mutants` (73 mutants, new set): see mxu.md §6. The `M = 0` path of the real decoder is proved by `tb/formal/mxu_dec.sby` (bmc depth 40 and cover pass; 6 of 7 decoder mutants caught, the 7th equivalent); decision row M-20 records S-10 and the decoder-side `M = 0`. Still open: `core/seq` `fault_in`, `core/spm` shim, `core/acc` stream (acc.md §10).

### Findings
- Spec gap (closed 2026-11-08, mxu.md M-11): the column-scale array `[KT x N]` was lane-aligned only if `4N % LB == 0` (a legality rule and "the DMA must pad `N`"); the array now has the derived row pitch `CSP = roundup(4N, 64)` and the rule is deleted.
- Bug found by the 16 x 16 run: the second beat of a group waited for the group's own `free` flag (deadlock for `DIM >= 16`); fixed.
- **Performance, against my own estimate:** with one shadow buffer the tile period at M = 1 was 25 cycles at DIM 16 (1.25·DIM + 9, about 80 for `FULL`), not 32-40:
  the next pass waited for all four groups, the last of which is free `DIM - 1` cycles after the swap. Decision (user): a second shadow buffer (mxu.md M-9).
  Measured now (2026-11-09, mxu.md M-21): 13 cycles per tile at DIM 16, 21.5 at DIM 32 (the one-buffer formula gave 49), **39.5 at DIM 64** (415 B/cycle of FP8 weights
  against a decode need of 164-193); the fitted bound `((DIM-1)+DIM/8+8)/2` holds at 64.
- Bank-conflict note for the compiler: group beats read one lane from 8 consecutive W rows (mxu.md §7). Measured with a 1R-per-bank SPM model (`tb_mxu +BANK=1/2`,
  `scripts/mxu_perf.sh`): conflict-free pitches (3 KiB, 4.5 KiB) cost at most 0.25 cycle over the ideal SPM; the colliding 2.5 KiB pitch costs 3.4-3.6 cycles per tile
  at `FULL` (43, not the 48 of the load alone). The whole `tb_mxu` passes with `+BANK=2` (real gang splits; review S-3 deadlock concern not borne out).

### Open
- `BF16X3` (faults `ILLEGAL_FIELD`), formal properties, `core/acc` (must accept the `acc_*` stream of mxu.md §5), Phase 6 fan-out of A.
- The third shadow buffer (period 39.5 -> 32, exact-path margin 1.15x -> 1.41x, +131 k flops): the period is now measured, the decision is open (mxu.md §9).
  Not measured: BF16 / non-trans beats, M between 2 and 40, other SPM clients on the banks, the shim as RTL (`mxu_spm_shim` does not exist).
- `scripts/mxu_mutants` was re-run in full on 2026-11-11 (after the bank-model `tb_mxu` change of 2026-11-09 and after M-22 / M-23): 77 mutants (73 + `gap_x_off`, `gap_x_short`, `gap_x_every_pass`, `w_gnt_wait`), `tb` and `formal` modes, all as expected.

- 2026-11-10, review part R (R-1, R-2, R-3; mxu.md M-22, M-23): the ACC spacing rule was wrong across instructions (fixed: `ACC_LAT - 2` cycles after the last pass of an instruction whatever `M`), an empty W beat needed `ready` without `valid` (fixed: the MXU grants it itself), numerics.md N-3 / N-5 say that K-tail slots are `+0` operands and that a `-0` partial sum survives only a full K tile. Results (final RTL): `make lint`, `make unit` (`tb_mxu` now 31 instructions per size, two ready-needs-valid instances), all six `mxu.sby` tasks (new `bmc_d`: `M <= 3`, free `acc_base`, 735 s wall; new `covx`; cross-instruction cover point reached at step 37), the review's suite (`run_review.py --fuzz 60`: 792 runs, 0 failed, including `acc_spacing_x_ins` pipelined), `+RMW=1` on `acc_spacing_x_ins` and the fuzz programs that failed before (DIM 8 fuzz000 / 020 / 053, DIM 16 fuzz001 / 044 / 045: all pass), `probe_rdyv.py` (0 hangs of 78; 71 before), `scripts/mxu_perf.sh 16 32` (tile period at `M = 1` unchanged: all 18 PERF lines identical to before). The vector lock was regenerated (`gen_mxu_vectors.py --lock`) because the generator gained the R-1 instructions; the shared random stream also shifted the `mxu_array` vectors. Mutants (re-run 2026-11-11): 77, all as expected (mxu.md §6).

- 2026-11-13/14, review part I (mxu_review.md part I, responses per finding; mxu.md M-24, M-25, M-26, §9): fixed I-2 (`ps` / `fl` packed: a blackbox PE keeps 64 of 64 instances, was 0; equivalence to 0255c28 proven at 8 x 8 and 16 x 16), I-3 (`mxu_dec` without dividers and wide multipliers: 42,316 -> 12,108 cells, depth 856 -> 93, ASAP7 2,711 -> 1,271 µm², ABC 2.17 -> 1.47 ns), I-6 (`make syn BLOCK=<rtl/core block> [PARAMS] [HIER=1]`; the reviewer's proposed recipe was not enough, `-defer` + `chparam` + `hierarchy -top` is; CLAUDE.md updated), I-7 (unreset registers out of the async-reset processes), I-8 (`swp0` / `swp1` one driver), I-9 (`inf_pop` guarded, comment fixed), I-10 (elaboration guards in `mxu` / `mxu_dec`, mxu.md §2 / §5 match the RTL), I-11 (M-2 / M-9 area wording: skew 1.0-1.2 %, one shadow buffer 1.9-2.4 %, ASAP7 synthesis areas). Not changed, open items in mxu.md §9: I-1 (weight storage in `mxu_array`; `mxu_array` own logic 10,642 / 42,168 cells at `DIM` 8 / 16, 11.2 % of the array area), I-4 (25 `$mul` in `mxu`, `w_req_addr` cone 1,018 ps), I-5 (shadow load fan-out). Results (final RTL): `make lint` (62 runs), `make unit` (281 s), `make mxu-vec` (lock unchanged: no vector changed), `mxu.sby` all six tasks and the cover tasks (1,363 s; cover steps 26-37 / 26-39), `mxu_dec.sby` (`bmc`, `cov`), the review's suite unmodified except the adapted scripts below (`run_review.py --dim 8 16 --fuzz 60`: 792 runs, 0 failed; `--xfill`: 632, 0 failed; `gl_sim.py --dim 8 16 --xfill`: 392, 0 failed; `xfill_controls.py` rc 0), `run_dec_miter.sh` (4 x 200,000 instructions against the decoder of 0255c28: no mismatch; with `tb_dec_miter_x.sv` the same). Adapted reviewer scripts: `fix_array_packed.sh` (original from git `OLD_REF`, fixed copy = the tree file), `run_dec_miter.sh` (gold = 0255c28's `mxu_dec`, fix = the tree). Mutants (`mxu_mutants.py`, 106 in all): `tb` mode 78, all as expected (one is now listed `equiv`: `inf_mem_always`, which `tb_mxu` cannot see, §9); `formal` mode 17, all as expected (run 2026-11-13); `misc` mode 28 (guards, narrowed decoder widths), all as expected. New: `tb_mxu` pulses a spurious `acc_done` after reset (kills `spur_pop`), `scripts/mxu_mutants/guard_check.py` (25 elaboration cases, three tools), `tb_dec_miter_x.sv`. Open gaps found by the mutants: `tb_mxu` no longer reaches the extent aliasing of the decoder (`range_a_m0`, `range_acc_wrap`, now killed by the miter) and never holds the in-flight FIFO full with different entries (`inf_mem_always`). Parallel runs: three `tb` mutant shards ran together (peak 1.5 GB each), after the machine's memory allowed it.

### Formal (mxu.sby)
`ifdef FORMAL` block in `rtl/core/mxu.sv` (decode split into `mxu_dec` so that it can be stubbed: `tb/formal/mxu_dec_stub.sv`, PE stub `mxu_stubs.sv`). P1-P5 proved to depth 56 on DIM 8 for three size mixes (3-6 min each,
`make formal` runs them and the cover task). 8 mutants of the control logic: caught `no_free_gate` (P1), `no_gap` (P3), `swap_early` and `last_beat_early` (P2), `cs_pop_first`, `rs_pop_end` and
`cs_cnt_leak` (P5); `dup_beat` is equivalent. Two survivors of the first round led to new assertions (rowscale lane pointer, independent reservation counts).
Since M-22 `bmc_d` / `covx` (stub decoder `M <= 3`) see the ACC spacing across instructions; the stub's `M <= 2` of `bmc_a` .. `bmc_c` could not (review R-1). Limits: no unbounded proof (induction does not close without joint invariants), no liveness proof (so no proof that an SPM that raises `ready` only for a request cannot hang the MXU: simulation, M-23), DIM 8 only. A wrong assumption in the stub (`8'(ACC_ROWS)` truncating 2048 to 0) made every
property vacuous until the cover points failed to be reached; keep the cover task in the regression.
