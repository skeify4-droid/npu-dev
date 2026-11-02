
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
- **MXU:** weight-stationary consumes 512 B/cycle of weights at any M, which is 2.9x a core's HBM share with 4-bit weights expanded to
  FP8. The MXU is never the decode bottleneck.

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
  and nothing samples `wr_ready` / `rd_valid` / `rd_data`) until its FIFO domain has left reset. The side not reset
  cannot see that its words were dropped (`wr_ready` low looks like full), so a requester tracking outstanding words
  is reset with the FIFO (open question 9). No RTL change for this: an integration rule in the header. (Before review
  the two resets were used directly per domain and had to overlap; with the read reset a few `rd_clk` edges late, the
  reader saw the reset write pointer and popped never-written words.) Throughput at 1:1 with both sides always ready:
  DEPTH 2 gives 1/3, DEPTH 4 gives 2/3, DEPTH 8 gives 1.
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
    grant) or, for `afifo`, a missing synchronizer stage, which neither a zero-delay simulation nor the port-level
    formal tasks can see (below).
- **Formal** (SymbiYosys, yices and abc pdr; 50 tasks under `make formal`; at least one task of every block was also run on a mutant and failed, and
  cover tasks show the properties are not vacuous):
  - `fifo` (16 tasks, DEPTH 1, 3, 4, 5, registered and fall-through): `count`, `full`, `empty`, `in_ready`, `out_valid`
    against a shadow count, no overflow, no underflow, by BMC and k-induction (unbounded); in-order data (one tagged entry
    from push to pop) and "a stalled output keeps its data", unbounded with `abc pdr` (k-induction cannot, the stored data
    is internal state).
  - `afifo` (6 tasks, DEPTH 2, 4, 8, multiclock with two free clocks and two free resets; the only assumption is that
    the run starts in reset): Gray registers follow the binary pointers, the
    pointer chain `srw2 <= srw1 <= rbin <= wbin`, `rbin <= sw2 <= sw1 <= wbin`, at most DEPTH apart; `wr_ready` and `rd_valid`
    are exact functions of the synchronized pointers and the domain reset; a domain in reset has zero pointers on both
    sides; no overflow, no underflow; one tagged entry's data survives. Unbounded by k-induction. The properties live in
    the RTL under `` `ifdef FORMAL `` (they need the pointers). Port-level tasks `ph2` / `ph4` (`abc pdr`, internal
    properties off): pushes and pops counted per domain, `rd_valid` only with a word pushed and not popped, `wr_ready`
    only with a free entry, any reset pattern; both fail on the pre-review RTL (wr reset a few `rd_clk` edges before the
    rd reset gives a phantom word).
  - `afifo` synchronizer depth is a structural / review check, not a functional one. With a zero-delay, step-level clock
    model a missing stage is not a functional error: a mutant that bypasses one stage (`wgray_r2 <= wgray`) passes `d4`,
    `ph4` and the unit tb; a mutant that takes `rd_valid` from `wgray_r1` fails only `d4`, at the internal assert that
    `rd_valid` is an exact function of `wgray_r2`, and passes `ph4` and the unit tb.
  - `afifo` covers (`cov4`), all at the ports: a word goes through (step 9); the FIFO fills, `wr_ready` high since the
    last reset input and then low with no reset in between (step 12); the write input alone asserts while the reader
    shows a word (step 10); the read input alone asserts with words inside (step 7); a word pushed while the read domain
    is still leaving reset is popped later with no reset in between (step 10); a word goes through after a write-only
    reset (step 10). Before review the fill cover was `!wr_ready && wr_rst_n`, reached at step 1 by the reset
    synchronizer alone.
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
7. `afifo` CDC constraints for Phase 6 STA. The full text is in the afifo header. Command options were checked against
   the OpenSTA in the pinned OpenROAD (18f08d7, `help` plus a 3-flop ASAP7 netlist); nothing is checked by simulation,
   lint or formal.
   - No `set_clock_groups` on `wr_clk` / `rd_clk` and no `set_false_path` between them. In this OpenSTA an `-asynchronous`
     group or a false path removes `set_max_delay` without a warning (after a clock group, `set_max_delay -reset_path`
     does not restore it). A `-hold` false path removes `set_min_delay`.
   - Gray pointers: `set_max_delay -ignore_clock_latency` of the launching period (setup included) plus
     `set_min_delay -ignore_clock_latency 0`. That bounds the arrival spread below one period, so no bus-skew check is
     needed (OpenSTA has none). `-datapath_only` is Vivado's option and does not exist here.
   - `mem` to `rd_data`: the endpoint is the consumer's register (`-through` the `rd_data` pins at the top level, or
     `-to` the port at the block level with the consumer's share subtracted). The budget is 2 `rd_clk` periods less the
     uncertainty, not one `wr_clk` period.
   - Resets: one exception, `set_false_path -through` the `arst_n` net. It covers recovery / removal at the four
     reset-synchronizer flops and the assertion paths. `wrst_n` / `rrst_n` to the pointer flops keep normal recovery /
     removal.
   - Minimum pulse width on `arst_n`: a runt pulse that clears one domain only brings back phantom words. OpenSTA does
     not report `set_min_pulse_width` on a RESETN pin, so this is a rule on the reset sources: registered or
     glitch-free, low for at least one period of the slower clock. The ASAP7 cell limit is 25.6 ps at TT.
   - Synchronizer flops, Gray source registers and the `arst_n` gate: `SYNTH_KEEP_MODULES`, never
     `SYNTH_RETIME_MODULES`, and `set_dont_touch` in a Phase 6 script (ORFS has no variable for it).
   - Still open: synchronizer depth (2 flops, data and reset) against the ASAP7 MTBF numbers, and turning this text
     into an SDC with a scripted check that every `wr_clk` / `rd_clk` path in the block is under one of these exceptions.
8. Formal properties of `afifo` sit in the RTL file under `` `ifdef FORMAL ``. The other blocks keep them in `tb/formal`.
9. HBM controller reset sequence (hbm.md). The controller has one `rst_n` for `clk` and `clk_mc`, and hbm.md does not
   say whether the `clk_mc` domain is ever reset alone (for example on PHY retraining or error recovery). If it is,
   then under the afifo reset-domain rule the `clk` side of each crossing must be idle (no `wr_valid` / `rd_ready`,
   nothing sampling the FIFO outputs) or reset at the same time. The words in flight are dropped and nothing at the
   ports says so (`wr_ready` low looks like full). So a read request in a dropped FIFO leaves its tag waiting forever
   in the requester's tag table, unless the requester is reset with it or a separate "flushed" signal retires the
   outstanding tags. hbm.md needs to define who quiesces the `clk` side, how outstanding tags and `WR_ACK` counts
   are retired, and the order of release.

### Next
- `make syn` numbers are generic-cell counts, not ASAP7 areas (Phase 6).
- Phase 3 `arith` and the leaf blocks can instantiate these blocks; `flit_t` width goes in through `WIDTH`.
