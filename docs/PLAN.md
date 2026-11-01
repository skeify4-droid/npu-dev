# NPU Development Plan: HBM3e LLM Inference NPU (RTL, Verification, Synthesis, Performance Simulator)

Status: v0 draft, 2026-10-31. Every number marked *(P1)* is a starting proposal. The Phase 1 analytical
model confirms or changes it before RTL starts.

---

## 0. Scope and ground rules

| In scope | Out of scope |
|---|---|
| Synthesizable SystemVerilog RTL for every digital block | Tape-out, FPGA, DFT/scan, signoff |
| Unit, integration, formal and end-to-end verification | Real HBM/D2D/SerDes PHYs and analog (behavioral models only) |
| Yosys synthesis of real and reduced configs; PPA extrapolation | Full-chip synthesis or full-chip P&R |
| OpenROAD P&R of selected sub-blocks (ASAP7 main, Nangate45 cross-check) | Running real LLM weights |
| Cycle-level and transaction-level performance simulator, traces, Gantt charts, per-model visualization | Bit-exact numerics in the perf simulator |

**Key principle: one ISA, three consumers.** A single machine-readable ISA definition (`isa/isa.yaml`)
generates the SystemVerilog decode package, the C++ simulator header and the Python assembler and compiler
back end. The RTL and the simulator then run *the same programs*. That is how the simulator gets calibrated
against RTL without full-chip RTL simulation.

**Key principle: three parameter sets.** All RTL is parameterized. Each config has its own job:

| Config | Purpose | Shape *(P1)* | Runs where |
|---|---|---|---|
| `TINY` | Unit and e2e regression, formal | 1 die, 2×2 cores, 4×4 PE array, 64 KB scratchpad/core, 1 HBM stack × 2 pseudo-channels | Verilator/Icarus, < 1 GB, seconds |
| `MID` | Integration, perf-sim calibration, synthesis extrapolation points | 1 die, 4×4 cores, 16×16 PE array, 256 KB scratchpad, 4 stacks × 4 PCs | Verilator, a few GB, minutes |
| `FULL` | Architecture spec and perf simulator; RTL is only elaborated and linted hierarchically | see §2 | perf sim only |
| `PKG_TINY` / `MULTI_TINY` | Multi-die and multi-package RTL checks | 2 `TINY` dies + D2D; 4 packages of 1 `TINY` die + C2C | Verilator |

---

## 1. Environment constraints (macOS arm64, 10 cores, 16 GB)

Measured from `/Volumes/EVO970PLUS/claude/env_setup`:

| Tool | Where | Notes for this project |
|---|---|---|
| Verilator 5.052, Icarus 13.0, sv2v, Yosys 0.70 | macOS native | Main RTL loop. Run Yosys and Icarus on **sv2v output** (their SV support is partial). |
| SymbiYosys + Z3/Yices2/Bitwuzla | macOS native | Formal on `TINY`-scale blocks only |
| cocotb 2.1 + pytest | `$EDA_HOME/venv` (Python 3.13) | Add numpy, ml_dtypes, pandas, pyarrow, plotly, pyyaml, zstandard |
| OpenROAD + ORFS (pinned `8d95c46`) + ORFS Yosys | OrbStack VM `eda`, 12 GB cap (`memory_mib 12288`) | Work dirs must be on the SSD. **Don't bump the OpenROAD pin** (CMake build removed upstream 2026-11-01). |
| PDKs | `$FLOW_HOME/platforms` | ASAP7 has `fakeram7_*` macros and a `fakeram.cfg` generator; Nangate45 has `fakeram45_*`. ORFS ships `designs/asap7/tpu` and `coralnpu` as reference flows. |
| KLayout, Surfer | macOS native | GDS and waveform viewing |

**Memory budget rules** (go in `CLAUDE.md` in Phase 0):
1. **Run one heavy job at a time.** Never run the VM (OpenROAD) together with a large Verilator build or Yosys flat synthesis.
   macOS needs about 4 GB. The VM is capped at 12 GB, so a full-size P&R run leaves macOS almost nothing:
   while OpenROAD runs, close other heavy apps and run nothing large natively. OrbStack only takes memory as the
   VM uses it, so native jobs get the RAM back when the VM is idle. If macOS starts swapping during P&R, lower the cap to 10 GB.
2. Verilator: `-j 4`, `--output-split 20000`, `-O1` for `MID`. Trace only selected scopes (`--trace-fst` plus
   `$dumpvars` on a scope). Never build `FULL`.
3. Yosys: synthesize each leaf once with `hierarchy` kept. Measured in Phase 0 (`docs/reports/capacity.md`): flat
   synthesis peaks at about 5 GB for 0.9 M cells (32×32 MAC array), so the flat cap is about 0.9 M cells. P&R in the VM peaks at 5.9 GB for a 16×16 array (0.27 mm², Nangate45), so the P&R block cap is about 230 k cells.
4. Perf simulator: stream traces to disk (zstd). Keep RSS ≤ 4 GB. Aggregate on the fly.
5. LLM workloads use **only `config.json`** (layer shapes). Weights are never downloaded.

---

## 2. Target architecture (baseline *(P1)*)

### 2.1 HBM and the reticle: the packaging assumption

- The reticle limit is about 26 × 33 mm ≈ 858 mm² **per die**. The number of HBM stacks a die can host is set by its
  **shoreline**: each HBM PHY uses roughly 11 mm of die edge, and the die also needs edge for D2D and C2C I/O.
  The total per package is set by **interposer size**: CoWoS-S is about 3.3× reticle; CoWoS-L / bridge-based packages are larger.
- Your "4 stacks" figure is right **per reticle-sized compute die** when it also needs D2D and SerDes edge. B200 uses
  2 dies × 4 HBM3e = 8 stacks. It is not an absolute physical limit: H100 placed 6 HBM sites on one ~814 mm² die
  with no D2D edge. The plan therefore uses **4 stacks/die as the baseline** and gets more by going multi-die.
  The perf sim also sweeps 4 to 6 stacks per die as a sensitivity study.
- HBM3e baseline per stack: 1024-bit, 9.6 Gb/s/pin → **1.23 TB/s**. 12-Hi stack = **36 GB**. 16 channels × 2
  pseudo-channels = 32 PCs per stack. JEDEC JESD238 (HBM3) timing is used, scaled to HBM3e data rate. Values are
  representative, not vendor-exact.

### 2.2 Hierarchy

```
System:   1–32 packages, scale-up fabric (C2C links; ring / 2D torus / switch modeled)
Package:  2 compute dies + 8 HBM3e stacks on a 2.5D interposer, die-to-die (D2D) link
Die:      ≤ 858 mm², 4 HBM3e stacks (4 controllers × 32 PCs), 2D-mesh NoC,
          32 tensor cores, global SRAM, command processor, D2D port, C2C ports, collectives (distributed, Phase 2)
Core:     MXU (matrix unit) + VPU (vector unit) + SFU + DMA + scratchpad + sequencer
```

Frozen on 2026-11-01 with the Phase 1 justification in **`docs/spec/arch.md`** (it wins where the two differ).

| Item | Baseline *(P1)* | Derived |
|---|---|---|
| Clock | 1.5 GHz (Phase 6 synthesis checks it on ASAP7) | |
| MXU per core | 64×64 PEs, each a 4-way FP8 dot-product (16,384 FP8 MAC/cycle), FP32 accumulate; BF16 at ½ rate | |
| Compute per die | 32 cores | 1.57 PFLOPS FP8 dense, 0.79 PFLOPS BF16 |
| HBM per die | 4 × HBM3e 12-Hi | 144 GB, 4.9 TB/s → **ops:byte ≈ 320** |
| Per package | 2 dies | 3.1 PFLOPS FP8, 288 GB, 9.8 TB/s |
| Scratchpad per core | 4 MB, 32 banks, double-buffered DMA | 128 MB/die |
| Global SRAM per die | 32 MB, software-managed (KV staging, expert prefetch, collectives) | |
| NoC | 8 x 4 2D mesh, 2 planes of 128 B/cycle/link/direction, HBM controllers on 16 edge leaf routers (Phase 2) | sustains 3.3 KB/cycle HBM ingress at ≤ 80% link load |
| D2D | UCIe-like, 1.4 TB/s/direction payload, 4 sub-links (modeled PHY; 5 TB/s until Phase 2, noc.md §5) | |
| C2C | 16 ports × 100 GB/s/direction (modeled SerDes) | 1.6 TB/s/package |
| VPU / SFU per core | 512 FP32 FLOP/cycle; 128 exp/rcp/rsqrt per cycle | 24.6 TFLOPS / 6.1 T ops/s per die |
| Data types | FP8 E4M3/E5M2, BF16, MXFP8, MXFP4 and NVFP4 weights (dequantized to FP8 in the DMA path), NVFP4/FP8/BF16 KV, FP32 accumulate, INT8 optional | |

Capacity check (Phase 1 result, `docs/spec/arch.md` §3): ~1T-parameter models need 8 packages (Mistral Large 4 for its weights,
MiMo-Pro for speed, Muse for KV). "Flash" class models hold their weights on 1-2 packages but need 2-4 to serve 64 x 128K sequences at
>= 50 tokens/s per user. Multi-package support is a
hard requirement, not an extra.

### 2.3 LLM features the hardware and compiler must support

Phase 1 step 1 read each model's `config.json`, then its published code, README and technical report where they exist
(`docs/reports/model_features.md`, `docs/reports/model_survey.md`). The list below is what the 11 real models need,
not what their predecessors needed. Descriptors are checked against the published weight sizes (all 11 within 0.5% after removing
measured vision/audio and duplicated tensors) and parameter counts (within 5%).

- MoE: top-k routing, shared experts, fine-grained experts (DeepSeek, GLM, Ling, Qwen3-Next, Mistral Large lines).
  The design needs fast top-k in the VPU, a gather/scatter DMA, and expert-parallel all-to-all.
- Attention variants (as found in the real configs): GQA/MQA; MLA with weight absorption (GLM, Ling, Mistral); sparse attention with an
  indexer (DeepSeek CSA2, GLM DSA, Mistral, Qwen QSA, Step CSA: 8-token region summaries pooled by a softmax gate, weighted-ReLU scores,
  top-512 regions per KV group) or block top-k (MiniMax MSA: per-token index scoring of the whole cache, block max, top-16 of 128-token blocks per GQA group; decode reads 64x more index-key bytes than selected KV at 1 M context, NVFP4 as served); linear attention (KDA in Ling and GLM-Flash,
  Gated DeltaNet in Qwen); sliding-window hybrids (MiMo, Step, Muse, DeepSeek's 128-token window).
- Multi-token prediction / speculative decode: MTP layers inside the weights (DeepSeek DSpark layers, GLM, Step, Ling, Qwen,
  MiMo's dense MTP) and draft models (MiMo DFlash, MiniMax DSpark, Mistral DSpark inside its weights). Schedules and acceptance are modeled
  (`sw/npumodel/specdec.py`, `opgraph.draft_ops`): x1.4-2.7 per user at batch 16 with each model's own drafter.
- **Hyper-connections (decided: supported).** DeepSeek V4.1 Flash and GLM-5.3-Flash run mHC (4 residual streams, an fp32
  `[24 x 4d]` mixer and a 20-iteration Sinkhorn per sublayer); Qwen3.8-Flash-Next runs a different gated low-rank mixer that
  costs 9.5% of its decode compute. Requirements: residual stream resident on-chip, fused `HC_*` tile ops, Sinkhorn on the VPU/SFU,
  a mixer-precision experiment, and 80 small all-reduces per step under tensor parallelism. See `docs/spec/hyper_connections.md`.
- **Hashed n-gram gather (decided: supported).** DeepSeek Engram (24 FP8 rows per token on 2 layers, a 197 G-element table) and Qwen's
  n-gram embedding (16 BF16 rows, a 51 G-element table). Indices depend on token ids only, so they are computed on the host/CP and
  the gather is prefetched; the DMA needs a `GATHER_ROWS` mode with unaligned rows and MXFP8 dequantization. The tables are the
  main capacity cost (DeepSeek's alone exceeds one die's HBM).
- **KV cache variety.** DeepSeek V4.1: FP4 global KV, MQA with K = V, CSA2 modes (Full / Reindex / Reuse), a hierarchical indexer and a
  causal encoder-decoder split (prefill runs 20 of 40 layers): 890 B of global KV per token. Mistral shares one MLA latent cache across
  the last 9 NoPE layers. Linear-attention layers (KDA, Gated DeltaNet in 4 of 11 models) have constant-size state instead of KV.
- Long context (128K–1M): paged KV cache, so the DMA needs page-table-indirect addressing.
- Norms and activations: RMSNorm, SwiGLU, RoPE (incl. partial/YaRN), softmax with online max (FlashAttention-style tiling).

---

## 3. Repository layout (created in Phase 0)

```
CLAUDE.md                 rules: memory budget, tool commands, coding conventions, one-heavy-job rule
docs/PLAN.md              this file
docs/spec/                arch.md, isa.md, memmap.md, noc.md, hbm.md, d2d_c2c.md, numerics.md
isa/isa.yaml              single ISA source → gen/{npu_isa_pkg.sv, isa.hpp, isa.py}
models/configs/           HF config.json snapshots (11 models)
models/descriptors/       normalized model descriptors (YAML)
rtl/{common,arith,core,noc,hbm,link,die,pkg}/
tb/unit/                  cocotb + pytest per block
tb/formal/                .sby per block
tb/e2e/                   compiled programs run on TINY/MID RTL vs NumPy golden
sw/compiler/              model graph → tiling → ISA programs (Python)
sim/analytic/             roofline / analytical model (Python)
sim/perf/                 cycle-level + transaction-level simulator (C++20, CMake)
sim/viz/                  Perfetto export, plotly reports
syn/yosys/, syn/orfs/<block>/config.mk, syn/results/
scripts/                  regression driver, memory-measured job wrapper
```

---

## 4. Phases

Sizes: **S** = 1–3 sessions, **M** = 4–10, **L** = 10+. Each phase has exit criteria, and the next phase does not
start until they are met.

### Phase 0: Infrastructure and capacity calibration (S)
- Create the repo skeleton, `CLAUDE.md`, a `Makefile` (`make lint|unit|formal|e2e|syn BLOCK=…`) and the venv extras.
- Write a `scripts/memrun` wrapper (`/usr/bin/time -l`, log peak RSS) used by every heavy job.
- **Capacity calibration:** synthesize parameterized PE arrays (4×4, 8×8, 16×16, 32×32) with Yosys and record
  peak RSS, time and cell count. Do the same for Verilator build and run of the same arrays and for ORFS P&R of
  8×8 / 16×16 / 32×32 in the VM (12 GB cap). Fit memory against cell count. That fit sets the real caps used in Phases 5–6.
- Exit: `make lint unit` green on a trivial block. Calibration table in `docs/reports/capacity.md`.

### Phase 1: Workload characterization and analytical model (M)
1. **Done (2026-10-31).** Fetch `config.json` for all 11 models (DeepSeek V4.1 Flash, GLM-5.3, GLM-5.3-Flash, MiMo-V2.6-Pro, MiMo-V2.6-Flash,
   Ling 3.1 Flash, MiniMax-M3.1-Flash, Step 5 Preview, Mistral Large 4, Muse Spark Open, Qwen3.8-Flash-Next).
   Normalize them into a descriptor schema: layer pattern, attention type per layer, MoE (E, k, shared, d_ff),
   vocab size, MTP, and dtype of the published weights. Flag any model whose features fall outside the schema.
   Result: `sw/npumodel`, `models/descriptors/`, `docs/reports/model_survey.md`, `docs/reports/model_features.md`.
2. **Done (2026-11-01).** Op-graph generator for prefill and decode (batch, context, q tokens, KV and weight dtype as parameters),
   plus one drafting round per spec-decode method. Result: `sw/npumodel/opgraph.py`, `sw/npumodel/specdec.py`.
3. **Done (2026-11-01).** Roofline / analytical model: FLOPs, bytes, KV footprint, capacity per die, TP all-reduce and EP all-to-all
   over D2D / C2C, PP hand-off, mapping search. Result: `sim/analytic/{hw,roofline}.py`.
4. **Done (2026-11-01).** Sweeps (`make sweep`): stacks/die, dies/package, packages, dtype (FP8 vs MXFP4 experts), batch, context, draft length.
   Answers (`docs/reports/phase1_sweeps.md`): MoE layers turn compute-bound at ~151 tokens per expert with FP8 weights (~80 with 4-bit),
   i.e. 2.6k-9.6k tokens in flight per EP group, so decode is HBM-bound for all 11 models; decode floors at batch 1 are 1.4-3.1 ms per token;
   package counts are 2 (Qwen, MiMo-Flash, GLM-Flash), 4 (MiniMax, DeepSeek, Ling) and 8 (GLM-5.3, Step 5, MiMo-Pro, Mistral, Muse).
- Exit: **met (2026-11-01).** §2 numbers frozen in `docs/spec/arch.md` with justification plots. The per-model package count is fixed.

### Phase 2: Architecture and micro-architecture spec (M)
**Step 1 done (2026-11-01):** micro-architecture (`docs/spec/microarch.md`: weight-stationary MXU, ACC, SPM banking, DMA, core and die
blocks, TINY/MID/FULL configs, interface tables), ISA v0.1 (`docs/spec/isa.md`, `isa/isa.yaml` with validator, encoder/decoder and
SV/C++/Markdown generators: `make isa`), memory map (`docs/spec/memmap.md`), NoC and deadlock strategy (`docs/spec/noc.md`, checked by
`sim/analytic/noc.py`). **Step 2 done (2026-11-01):** numerics (`docs/spec/numerics.md`, golden model `sw/npumodel/numerics.py`,
experiments `make numerics`). **Step 3 done (2026-11-01):** HBM controller (`docs/spec/hbm.md`, model and timing checker
`sim/analytic/hbm.py`, `make hbm`), links and collectives (`docs/spec/d2d_c2c.md`, `sim/analytic/links.py`, `make links`), spec review
(`docs/reports/spec_review.md`, findings R-1 to R-15). **Independent review done (2026-11-01):** findings I-1 to I-15 and the xhigh
checks X-1 to X-6 (HBM timing-rule coverage, deadlock paths) in the same file. **Fix session done (2026-11-01):** every finding
fixed at the spec level or deferred to a named phase (Resolutions section of the same file); I-8 decided as staging. A verification
session checks the fixes next.
- **Done (v0.1).** `isa.md` + `isa.yaml`: tile-level macro instructions (DMA_LOAD/STORE with 2D/3D strides, gather, page-indirect;
  MXU_GEMM; VPU ops; SFU; TOPK; SYNC/barrier/semaphore; COLLECTIVE; hyper-connection ops HC_MIX / HC_SINKHORN / HC_PRE / HC_POST /
  HC_GATED_MIX; DMA_GATHER_ROWS with unaligned rows and MXFP8 dequantization). Decoupled access/execute queues per core.
  As built: COLLECTIVE became `DMA_LIST` + write-add + `DMA_MCAST` + semaphores (isa.md §10); HC_GATED_MIX folded into
  `HC_PRE mean=1` and `HC_POST GATED`; DMA_GATHER_ROWS is `DMA_GATHER`.
- **Done.** `numerics.md`: FP8/BF16/MXFP4/NVFP4 formats, rounding (RNE), saturation, accumulation order, NaN/Inf policy; the hyper-connection mixer-precision experiment.
  As built: also the NVFP4 fold residual (exact BF16 path when memory-bound, fold when compute-bound), BF16 recurrent state (rejected), RTL test tolerances.
- **Done.** `memmap.md`: global address map (HBM address interleaving across stacks/PCs/banks, scratchpad, global SRAM, remote die/package windows).
- **Done.** `noc.md`: topology, routing (XY), virtual channels for deadlock freedom (request/response separation), credit flow control.
- **Done.** `hbm.md`: controller architecture (per-PC queues, FR-FCFS scheduler, open/close page policy, refresh,
  timing table) and the DFI-like boundary to the behavioral PHY + DRAM model.
  As built: adaptive page policy, per-bank refresh, 32/16-entry queues, `clk_mc` = 1.2 GHz; a cycle model shows 0.95-0.99 on decode traffic.
- **Done.** `d2d_c2c.md`: link layer (flits, credits, CRC/replay optional), collective engine (ring/tree all-reduce, all-to-all).
  As built: CRC-32C + go-back-N on both links (required, not optional); no engine: hierarchical direct all-reduce and rail-staged
  all-to-all as DMA programs.
- Exit: spec reviewed. `isa.yaml` generator emits SV/C++/Python. Every block has an interface table.
  **Met (2026-11-01)** as a self-review, followed by an independent review of the whole spec set (findings I-1 to I-15, X-1 to X-6)
  and a fix session ([spec_review.md](reports/spec_review.md), Resolutions).
  - **Gating findings closed (2026-11-01, fix session).** The findings the review's "Before Phase 3" table lists for `core/dma`
    (I-1, I-2, I-3, I-5, I-12(a), X-3), `core/seq` (I-4, I-11) and `hbm` (I-9, I-10, I-12(b), X-1, X-2(b), X-3) are fixed at the
    spec level; see the Resolutions section of [spec_review.md](reports/spec_review.md). X-2 (a), (c)-(e) stay on the hbm.md H-9
    JESD238 checklist as timing-table parameters.
  - **Verified, then fixed again (2026-11-01).** The verification session (312f927) confirmed the fixes and found V-1 to V-11.
    `hbm` had no new finding. The second fix session resolved all eleven on branch `spec-verification-fixes` (7575888, 2bd39fa,
    eb1f153; spec_review.md "Resolutions (verification fixes)"). V-2 was decided by the user: consumer credits across kernels,
    no kernel barrier. Phase 3 RTL on `hbm` may start now. On `core/dma` and `core/seq` it starts after a verification pass of
    these fixes.
  - **Verified again, then fixed (2026-11-01).** The second verification (d8f4f23) confirmed V-1 to V-11 and found W-1 to
    W-6. A third fix session resolved all six on branch `spec-verification2-fixes` (b62bbc4, 0e23f87, b8b38b8;
    spec_review.md "Resolutions (verification 2 fixes)"). The DMA staging ring takes an entry's scales and raw slots together
    (`isa.staging_ring`); semaphores belong to one call site and move only after a final credit; a stale link `ERR` frees no
    tag. No frozen number changed. `core/dma` waits for one more verification pass, which starts with the ring
    deadlock probe against the chosen rule. `core/seq` has no open item: W-3 is a compiler and runtime rule.
  - **Verified a third time, then fixed (2026-11-01).** The third verification (d015351) found Z-1 to Z-8. A fourth fix
    session resolved all eight on branch `spec-verification3-fixes` (8f6cf62, 4c0cb8b, 27184c4; spec_review.md
    "Resolutions (verification 3 fixes)"). Every DMA read belongs to one ring entry, and `isa.staging_ring` models byte
    ranges and a queued HBM share. Converting reads are paced by CSR `DMA_PACE` (72 KiB, the user's choice). Inline-scaled
    Engram rows are defined. Semaphores move after a drain wait, and the link-down restart waits for every core, the bridges
    and a drain time. No frozen number changed. `core/dma` waits for one more verification pass.
  - **Verified a fourth time, then fixed (2026-11-01).** The fourth verification (9aea46f) found Y-1 to Y-10. A fifth fix
    session resolved all ten on branch `spec-verification4-fixes` (180d3bf, cfffee9, efe47a5; spec_review.md "Resolutions
    (verification 4 fixes)"). Ring entries without raw slots cover one data request. The pace is counted by format and capped at
    4.5 x the ring (the user's choice). A read takes one tag and one credit per granule, and DQs share credits and ring space
    in turns (the user's choice). Partial-granule responses are defined, and an epoch bit drops stale link `ERR`s (the user's
    choice). The Engram table keeps its separate scale array (the user's choice). No frozen number changed. `core/ni` and
    `core/dma` may start Phase 3 RTL, except the `core/dma` read-issue arbiter, which waits for a verification pass.
  - **Verified a fifth time, then fixed (2026-11-01).** The fifth verification (ed2ea70) found X-7 to X-16. A sixth fix
    session resolved all ten on branch `spec-verification5-fixes` (b8e6596, a2eea9a, 7b0f910; spec_review.md "Resolutions
    (verification 5 fixes)"). The ring turn passes when its holder has no entry to start, so a semaphore wait cannot
    deadlock the ring. The pace holds the turn only against converting DQs, write packets count toward a turn, the ring
    turn counts data granules, and one-granule entries are paced in full (the last two taken by the user). `isa.staging_ring`
    models semaphores and writes. A response inside one 128 B half is 1 flit, and responses and acks echo the epoch (the
    user's choices). Read requests are cut at SPM destination runs. `SCALE_INLINE` is reserved in v1 (the user's choice). No
    frozen number changed. The read-issue arbiter may start Phase 3 RTL with its P1-P3 formal properties first, and so may
    the `core/ni` response path.
  - `common` and `arith` do not depend on them and can start now.

### Phase 3: RTL, leaf and core blocks (L)
| Block | Contents | Verification hook |
|---|---|---|
| `common` | FIFOs (incl. async FIFO for the HBM `clk_mc` domain), credit interfaces, arbiters (RR, priority), SRAM wrapper (behavioral ↔ fakeram macro switch) | formal |
| `arith` | FP8/BF16 multipliers, 4-way dot-product PE, FP32 adder/accumulator, converters, MXFP4 decoder | **exhaustive** FP8×FP8 (65,536 pairs per format) vs `ml_dtypes`; BF16 random + corners |
| `core/mxu` | weight-stationary array (decided in Phase 2, microarch.md §2.2), skew/de-skew, column-scale and rowscale multipliers | cocotb GEMM vs NumPy (`numerics.mxu_mm`) |
| `core/acc` | accumulator SRAM (2048 x 64 FP32), MXU read-modify-write port, VPU port, conflict stall | directed + random vs NumPy |
| `core/vpu`, `sfu` | lanes, reductions, exp/recip/rsqrt (LUT + polynomial), top-k unit | ULP-error bounds vs NumPy |
| `core/dma` | strided, gather/scatter, paged (row pages with in-page scales), MXFP4 / NVFP4 / MXFP8 conversion to E4M3 + column scales or BF16 (256 elements/cycle), scales staged before their data (`DMA_STAGE` ring), write packets merged across adjacent rows, four DQs served independently, outstanding-request tracking, ack counting | cocotb + formal on address generator; the memmap.md §6 appends (DeepSeek index key, MiniMax KV), the batched `DMA_SCATTER` append and the n2 = 4 group read against `isa.paged_addr`, a non-paged block slice against `isa.block_addr`, the Engram gather as published (MXFP8 256 B rows with a separate scale array, s1 = 256, `aux0` 8) against `isa.block_addr` (Y-7), and the `ILLEGAL_FIELD` cases (unaligned block base, `page_shift` below 32 / `aux0`, `DMA_STAGE` below 2 KiB or above 32 KiB (N-4), a fold-converted TD whose tiles can cross a 256 B granule (MXFP4 → E4M3 with K = 384 at `s1` = 192, a K slice from element 128, n2 = 2 with n0 = 128, a paged `byte` of 64) and a `BF16_FP32` / `FP32_BF16` source whose base, strides or `row_bytes` are not whole elements (U-1, the user's choice, and U-2; against `isa.cvt_source_check`), and any TD with `SCALE_INLINE`, which is reserved in v1: X-16, the user's choice; the inline gather against `isa.inline_addr` and the inline ring entries are a later version's tests); a converting gather whose scale responses are delayed behind its data (`ni_rsp` must keep sinking); a DQ blocked on a semaphore while another DQ streams; the staging ring against `isa.staging_ring` on non-split streams in four DQs (without the reference's inline entries, X-16, or its split fold tiles: v1 has no raw slots, U-1), with byte ranges whose entries end mid-granule (index-key rows from row 2 of a page: cut reads, Z-1); formal properties that every allocated ring entry needs only tags to complete (W-1) and that no data read waits for the scales of an entry other than its own (every read and every unit in exactly one entry, Z-1); `DMA_PACE` holds the weighted bytes of converting reads in flight (NVFP4 x1, MXFP4 x1/2, MXFP8 x1/4) at min(`DMA_PACE`, 4.5 x the `DMA_STAGE` size), leaves plain reads alone, issues a converting read when none is in flight even with a limit below one request (1 KiB against 2 KiB reads), and counts to 2^17 without wrapping (Z-2, Y-2, Y-6); the RTL's entries without raw slots cover at most one data request, checked on a weight stream and an index-key stream against `isa.staging_ring` with `sizing="rate"` (Y-1); tags and credits (one tag per request, one credit per `RD_RSP`) and the credit-fair turns and ring turns: a KV gather plus a plain stream, two KV gathers plus a plain stream, and NVFP4 weights plus a KV gather in separate DQs each get the shares that `test_staging_ring_tag_arbitration` asserts (none starved), and no request is shrunk (Y-3); **the read-issue arbiter's formal properties (X-7)**, by SymbiYosys k-induction on a reduced configuration (2-4 DQs, 8 credits, a ring of 16 units) under an environment that returns every granule within R cycles: (P1) a ring-turn holder or allocation-queue head that cannot start an entry for a reason other than ring space, credits, pace or the issue turn releases the turn or leaves the queue the next cycle, (P2) the issue turn is held only while the holder's next request lacks credits or pace, and while it lacks only pace a ready plain request of another DQ still issues, (P3) a DQ with a ready read or write packet issues within a bound in R, the turn quantum and the DQ count; against `isa.staging_ring` with semaphores: the X-7 case (2 KiB ring: weights, a KV gather that waits on a flag inside its ring turn, and the KV gather that sets it) and the seeded random four-DQ semaphore programs of `test_staging_ring_ring_turn_passes_when_idle`; at `DMA_PACE` 16 KiB a plain DQ next to paced NVFP4 weights or a KV gather keeps about 76 B/cycle (X-8); a 1 MiB write DQ next to a weight prefetch, with the reader keeping its rate and the writer passing the turn every 8 packets (X-11); NVFP4 + MXFP8 weights in two DQs sharing evenly, the ring turn counted in data granules (X-10); a per-row MXFP4 gather whose one-granule entries are paced in full (X-14) |
| `core/seq` | ISA fetch/decode, queues with captured operands, semaphores (serial-number compare); no scoreboard (isa.md §10); no kernel barrier: a core starts its next kernel when it halts (isa.md §3) | directed + random programs |
| `core/ni` | network interface: initiator for the DMA, SPM / GSRAM / CORECTL target, write-add ALU, ack coalescing (noc.md §2), tag table, request epoch | cocotb vs `noc.ack_coalescer`; the read-response path against `isa.rd_rsp_layout` (Y-5): one NVFP4 granule cut at byte 128 into two requests of two ring entries, checking both halves' data, `gidx` 0, SPM destinations and element offsets (0 and 256), a 264 B row from byte 8, and responses of one request in any order; a `LINK` `ERR` of the other `NI_EPOCH` is dropped without a fault, one of the current epoch faults (Y-10); 1-flit responses (X-9): a 32 B KV scale read and an 8 B Engram scale read in either 128 B half come back as 1 flit and land at the right SPM bytes, a range across the halves as 2, and the HBM and SPM targets pick the flit count from the request; the epoch on responses (X-13): an `RD_RSP` or `WR_ACK` of the other epoch is dropped and frees nothing, its tag or burst id staying outstanding until its own response, and the ack coalescing table keeps two epochs of one burst id apart; fold reads in v1 (U-1): MXFP4 → E4M3 weights at the padded pitch (K = 384, `s1` = 256), one request per row, each response converting whole tiles from its own bytes and the row's 64 B short tile ending at the request's end, in any response order; a read into padded SPM rows (512 B rows to a 576 B pitch), which the DMA issues as one request per row (`isa.read_requests`); response pieces into raw slots (X-12, `isa.rd_rsp_layout(..., unit=)`, fold tiles from byte 64) are a later version's test |
| `core/spm` | banked scratchpad, bank-conflict arbitration | formal (no lost/duplicated requests) |
- Exit: every block has lint-clean code under Verilator `-Wall`, green unit tests and green formal. `TINY` core runs a GEMM program from the assembler.

### Phase 4: RTL, memory system, NoC, links, integration (L)
- `noc`: router (VCs, credits) and a parameterized mesh generator.
- `hbm`: controller RTL. PHY = behavioral DFI responder. DRAM = behavioral bank-state model with a **timing-violation
  checker** (tRCD, tRP, tRAS, tRRD, tFAW, tCCD, refresh, PREA, and the channel-scope command-bus rule).
  The checker runs the `test_hbm.py` sequences (one violating and one exact-gap sequence per rule) and the rule-removal test
  against its own rule table (hbm.md H-5).
- `link`: D2D and C2C link-layer RTL (CRC-32C, go-back-N replay, shared-pool credits), behavioral PHY with configurable latency,
  bandwidth and error injection. Collectives are DMA programs (d2d_c2c.md L-7), not an engine.
  Tests from the independent spec review (d2d_c2c.md L-11):
  - CRC error on an idle flit, and a replayed flit with stale credit counters: no credit lost or counted twice (X-4);
  - credit counters wrapping past 2^12 under a long stream (X-4);
  - REQ blocked at the receiving leaf while RSP keeps draining through the same link (per-VN queues, I-7);
  - `PKG_TINY` link down under load: every core with a transfer through the link faults, with `LINK` (requests still on its
    side, including those in the replay buffer) or `TIMEOUT` (requests the far side had accepted), the mesh drains, faulted cores
    keep sinking responses and serving target requests, and the CP still reaches every core (X-5, V-7);
  - the same with only the reverse path failed (W-2): cores whose transfers completed may also fault with `LINK` (a stale
    replay-buffer `ERR`, possibly for a reused tag), no core faults with any other cause, a response for a tag that is not
    outstanding is dropped, a `LINK` `ERR` frees no tag, `STATUS` stays busy until every tag is freed by its response or
    timeout, and after the CP restarts the step no response of the old step is accepted (a delayed duplicate response for a
    reused tag is injected);
  - a late `LINK` `ERR` at the restart (Z-7): the bridge's replay-buffer `ERR`s are held on the mesh (a stalled RSP leaf)
    past the release of the last tag. The CP restarts only after every core of the step, faulted or stopped, shows `STATUS`
    idle, the bridge reports `err_done` and `T_DRAIN` has passed, and no core of the restarted step faults. With `T_DRAIN`
    shortened below the hold, the restarted core faults `LINK`, which shows the wait is needed. A core the CP stopped
    without a fault, with a response in flight, is not restarted before that response has arrived. The recovery time is
    measured (L-11). With the epoch (Y-10): the same late `ERR`, held past a shortened `T_DRAIN` (an SPM-port stall on a
    live converting core during the drain), is dropped by the restarted core because its epoch is the old one; with the
    epoch compare disabled it faults `LINK`, which shows the bit is what protects the step.
  - late responses and two restarts in a row (X-13): an `RD_RSP` of the old step held on the mesh past its tag's timeout
    and the restart, for a tag the new step has reused, is dropped by its epoch and the new request completes with its own
    data; the same for a `WR_ACK` of a reused burst id. A link that fails again at once after a restart, with a late
    `ERR` of the first step still held: the CP waits conditions 1-3 of the second restart, and the test measures whether a
    packet two epochs old can outlive them (one epoch bit covers one restart; L-4 (f)).
  - several steps on one die (X-13 (c)): steps A and B on disjoint cores both have transfers through the failed link,
    and a step C does not; with the reverse path failed, a stale replay-buffer `ERR` faults a core of B whose transfers
    completed. The CP restarts A and B, each after its own conditions 1-3 and with its own `NI_EPOCH` toggle, and C keeps
    running without a fault.
- `die`: command processor, core instances, global SRAM, mesh, HBM subsystem. `pkg`: 2 dies + D2D. `system`: N packages + C2C.
- Exit: `TINY` die boots a program from HBM, runs GEMM + softmax, writes back. `PKG_TINY` does a cross-die all-reduce.
  `MULTI_TINY` does an all-to-all.

### Phase 5: Verification closure (M, overlaps 3–4)
- Verification plan document with a feature list and a coverage target per feature.
- Formal (SBY): FIFO/arbiter properties, NoC router (no drop, no duplication, bounded liveness), HBM scheduler
  (never violates the timing table), credit protocol, DMA address bounds.
- **E2E:** the compiler lowers a *tiny transformer layer* (real layer types: MLA, GQA, MoE with top-k, SWA, DeltaNet,
  at toy dimensions) to ISA programs. They run on `TINY`/`MID` RTL and are compared against a NumPy golden model
  within the numeric tolerance from `numerics.md`.
- Verilator `--coverage` (line/toggle) on regression runs, merged report.
- Exit: all e2e layer types pass. Coverage targets met or waived with reasons. Zero open timing-checker violations.

### Phase 6: Synthesis, P&R and PPA extrapolation (M)
- Yosys (native, on sv2v output) with ASAP7 and Nangate45 liberty: every leaf at real size where it fits
  (PE, VPU lane, router, DMA, HBM controller logic, sequencer). Arrays are synthesized at 8×8 / 16×16 / 32×32 and extrapolated.
- ORFS P&R in the VM (12 GB cap) on selected blocks. Phase 0 calibration sets the largest block size that fits:
  1. PE cluster 16×16 (calibrated: 13.7 min and 5.9 GB in the 12 GB VM; 32×32 extrapolates to over 14 GB and is out; the main compute density and Fmax evidence)
  2. Scratchpad bank: logic plus `fakeram7_*` macros (new sizes from `fakeram.cfg`)
  3. NoC router
  4. VPU lane slice
  5. HBM controller scheduler (logic only, PHY excluded)
- Build a PPA model: area = Σ(block area × count) + SRAM macro area + modeled PHY area and shoreline
  (HBM ×4, D2D, C2C). **Check die area ≤ 858 mm² and edge length ≥ the PHY shoreline needed.**
  This closes the loop on the reticle question. ASAP7 is predictive, so treat the absolute numbers as indicative.
- Power: OpenSTA with switching activity from Verilator VCD of PE/router workloads → energy-per-op table for the perf simulator.
- Exit: Fmax per block, area table, a die floorplan sketch with the area check, an energy-per-op table, and an Fmax/area update fed back into §2.

### Phase 7: Performance simulator (L)
**Design**
- Core in C++20 (CMake, clang, no heavy dependencies), Python front end (pybind11 or subprocess + files).
  Python alone is too slow for cycle-level simulation of HBM and the NoC.
- Two fidelity modes that share one component graph:
  - **Cycle mode:** per-cycle MXU/VPU/DMA pipelines, NoC flits, HBM per-PC bank state machine with the same timing table as RTL.
    Emits **full memory-access records**: `{cycle, die, core/engine, op_id, addr, size, R/W, stack, PC, bank group, bank, row, col, row-hit/miss, latency}`.
  - **Transaction mode:** tile-op granularity with an analytic NoC and a queue-based HBM. 100–1000× faster. Used for whole-model runs.
  - Hybrid: cycle mode for selected *windows* (a layer, a few decode steps). Repeated identical layers are simulated once and replicated, with the layer pattern taken from the descriptor.
- Input: the **same ISA programs** produced by `sw/compiler` for `FULL` config, plus the routing scenario
  (uniform, Zipf skew α, or a measured histogram).
- Multi-chip: D2D/C2C link models, topology (ring, 2D torus, switch), collectives, TP/EP/PP mapping.
- **Program-level deadlock detector** (independent spec review X-6): report a deadlock when every engine and front end of every core
  is blocked on a semaphore wait (or a full queue behind one) and no transfer is in flight, with the wait chain. The golden
  instruction-level model (`sw/compiler`, Phase 3) gets the same check next to its SPM race checker; the compiler's issue-order
  rule (d2d_c2c.md L-7) is what it guards. It also checks the kernel-boundary rule (isa.md §3, review findings V-2, W-3, Z-5, Z-6): every add to a
  network-facing semaphore (buffer, credit and multi-adder flag semaphores, one per buffer parity) is causally after its
  owner's wait for that semaphore's previous use, also across kernels and when the index moved to another call site (the
  final credit); a single-adder flag exempt from that is one whose adds are `SIGNAL`s from one DQ, and any other
  single-adder flag is checked by the general rule (Y-9); no add of the old call site arrives after a move (the owner's
  drain wait covered its full count, for a credit semaphore its base-table entry of credits expected, including the last
  use's credits; Y-8); every wait target is the carried base plus the use's total (data-dependent totals from the
  program's SPM base table); and a multicast source waits for one credit per destination. Test programs: a credit semaphore
  moved between two collectives with 8 consumers (Z-5), a reused 8-adder ready flag with and without parities, and a
  shared-parity credit semaphore, which must be flagged (Z-6); a credit semaphore moved after an all-to-all whose consumer
  set varies per use (the drain target is the base-table entry, and Y's first write waits for the final credit), and the
  same program with the consumers' last credit removed, which must be reported as a deadlock at the drain wait (Y-8); an
  `rsem`-counted single-adder flag and a single-adder `SIGNAL` flag from two DQs, legal when each add follows the owner's
  previous wait and flagged when one runs ahead (Y-9).
  And the write-after-write rule (hbm.md H-2, W-4 a): two writes of the same HBM bytes are ordered by an ack.
- **Gathers on the mesh** (review finding X-9): the 8 cores of a stack on the paged KV gather with 1-flit scale responses
  load the busiest HBM leaf link to 0.91 at the 0.85 share in `noc.gather_read` (1.21 with 2-flit responses). Phase 7
  measures it with the router and VC model, per-PC queues and the REQ flits of the gather's two requests per row, and
  reports the gather's rate per core against the 155 elements/cycle of the microarch.md §2.5 table.
- **Converting-stream rate** (review findings Z-2, Y-2, Y-4): NVFP4 → BF16, MXFP4 and MXFP8 weight streams and the paged
  NVFP4 KV gather against the `isa.staging_ring` table in microarch.md §2.5 (single-FIFO results), with real per-PC queues,
  at the default `DMA_PACE` and with it off:
  - 8 cores of one stack all on the same converting stream (the fourth verifier's FIFO-per-PC model: KV gather 144 / 132 at
    L = 500 / 845 against 155 / 154 for one core);
  - mixed traffic: one converting core and seven cores streaming plain reads (about 50 B/cycle against 92 in the FIFO-per-PC
    models), and four plus four;
  - MXFP4 and MXFP8 weights at L = 1,000-1,200 under the weighted pace;
  - two options to cost if they fall short: a 24 KiB ring for KV-heavy kernels, and byte-weighted fairness per initiator
    in the HBM scheduler (hbm.md H-9). If either is needed, or the default limit is off, change it before the RTL freezes
    it.

**Data handling within 16 GB**
- Binary trace records (about 24 B each), zstd streamed to the SSD. Rough size: one decode layer on one die is about
  10⁷ accesses → about 240 MB raw, before compression. Statistics (bandwidth per channel per µs, row-hit rate,
  bank heatmap) are aggregated online, so full traces are optional.

**Calibration**
- Run the same micro-programs (GEMM tile, DMA patterns, HBM streams with row-conflict patterns, NoC hot-spot,
  softmax) on `MID` RTL and on the simulator in `MID` config. Target ≤ 5% cycle error in cycle mode and ≤ 15% in transaction mode.
- Exit: calibration report, plus a whole-model decode and prefill run for at least 2 models in transaction mode and 1 window in cycle mode.

### Phase 8: Workload analysis and visualization (M)
For **each of the 11 models**, a generated report (`sim/viz`, plotly → standalone HTML, plus Perfetto traces):
- **Gantt chart:** per-core tracks (DMA / MXU / VPU / SFU / sync), per-HBM-stack tracks, D2D/C2C tracks,
  with op names and layer IDs. Export to Perfetto (`ui.perfetto.dev` handles large traces). A plotly Gantt for short windows.
- **Memory access patterns:** address-versus-time scatter (downsampled), bandwidth over time per stack/PC,
  read/write mix, access-size histogram, row-buffer hit rate, bank-conflict heatmap, a KV-cache versus weight versus activation breakdown.
- **Core behavior:** utilization per engine, stall reasons (waiting on DMA, NoC, sync, HBM), roofline placement per op.
- **Model-level metrics:** TTFT, TPOT, tokens/s per package, HBM bandwidth efficiency, all-to-all time share, a comparison table across models.
- Sweeps: batch, context, dtype, stacks/die, packages, MoE skew.
- Exit: 11 reports and a cross-model summary with architecture conclusions.

### Phase 9: Wrap-up (S)
- Architecture conclusions, a list of known RTL/sim discrepancies, a reproducibility guide (`make all-reports`).

**Order note:** you asked for the performance simulator after development, and Phase 7 follows that. Phase 1's
analytical model is a small Python roofline tool that sizes the hardware. It is not the simulator, but Phase 7 reuses its op graphs.

---

## 5. Model and effort per phase

Rules of thumb:
- **Opus** for decisions that are expensive to reverse (spec, ISA, memory system, simulator architecture) and for hard debugging.
- **Sonnet** for volume work against a clear spec.
- **Haiku** for mechanical work.
- Use **xhigh/max** only for short, high-stakes reasoning (deadlocks, HBM timing correctness, calibration gaps that don't make sense).
- After each Sonnet-written block, run an Opus `/code-review` at a milestone.

| Phase / task | Recommended | Alternative | Why |
|---|---|---|---|
| P0 repo skeleton, Makefile, venv, `memrun` | Haiku / low | Sonnet / low | Mechanical |
| P0 capacity calibration and fitting | Sonnet / medium | — | Scripting plus simple analysis |
| P1 config ingestion and descriptor schema | Sonnet / high | Opus / medium | The schema must cover unusual attention types |
| P1 analytical model and sizing decisions | **Opus / high** | Opus / xhigh for the final freeze | Sets the whole architecture |
| P2 architecture, ISA, memory map, NoC deadlock strategy | **Opus / high** | Opus / xhigh on ISA + HBM | Hardest to change later |
| P2 numerics spec | Opus / medium | — | Precise but bounded |
| P2 `isa.yaml` code generators | Sonnet / medium | — | Routine code generation |
| P3 `common` blocks, SRAM wrappers | Sonnet / medium | Haiku / medium | Standard patterns |
| P3 FP8/BF16/MXFP4 arithmetic | Sonnet / high | Opus / high if exhaustive tests keep failing | Corner-case heavy |
| P3 MXU, VPU, DMA, sequencer | Sonnet / high | — | Large but specified |
| P4 NoC router and mesh | Sonnet / high | Opus / high for VC/deadlock issues | |
| P4 HBM controller + DRAM model + timing checker | **Opus / high** design, Sonnet / high implementation | Opus / xhigh for scheduler bugs | Most intricate block |
| P4 D2D/C2C links, collective engine | Sonnet / high | Opus / high review | |
| P4 die/package/system integration | Sonnet / high | Opus / xhigh for system hangs | |
| P5 verification plan | Opus / medium | — | Judgment about coverage |
| P5 cocotb tests and golden models | Sonnet / medium | — | Volume |
| P5 formal properties (SBY) | Sonnet / high | Opus / high when proofs don't converge | |
| P5 tiny-transformer compiler path + e2e | Sonnet / high | Opus / high for tiling design | |
| P5/P6 hard debugging (hangs, mismatches) | **Opus / xhigh** | Opus / max for the worst cases | Deep reasoning over traces |
| P6 Yosys/ORFS scripts and configs | Haiku / medium | Sonnet / medium | Mechanical but fiddly |
| P6 timing closure, PPA model, reticle check | Opus / medium | Sonnet / high | Interpretation |
| P7 simulator architecture | **Opus / high** | Opus / xhigh | Fidelity/speed trade-offs |
| P7 simulator C++ components | Sonnet / high | — | Large, performance-sensitive |
| P7 compiler/mapper for FULL (TP/EP/PP) | Opus / high | Sonnet / high after design | Mapping quality drives the results |
| P7 calibration against RTL | Opus / high | Opus / xhigh for unexplained gaps | |
| P8 visualization and report generator | Sonnet / medium | Haiku / medium for styling | |
| P8 cross-model analysis and conclusions | Opus / high | — | Insight, not code |
| P9 docs cleanup | Haiku / low | Sonnet / low | |

Fable 5.1 also appears in this environment's model list. I haven't compared it with Opus on this kind of work,
so it isn't in the table. If you want a comparison, try it on a P2 or P7 architecture task.

Session hygiene:
- Keep one block per session.
- Start each session with `CLAUDE.md` + the relevant spec file.
- End each session with tests green and a short status note appended to `docs/reports/status.md`.

---

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Target model configs differ from predecessor assumptions, or are not public yet | Done for config, code and READMEs (11 of 11 public). Descriptors are validated against weight sizes and published parameter counts. Open items are listed in `docs/reports/model_features.md`. Unknown layer types fall back to a generic op with FLOP/byte annotations. |
| Hyper-connection mixer in BF16 is not accurate enough, or the per-step Sinkhorn latency hurts small-batch decode | Phase 2 numerics experiment against the fp32 reference; VPU fp32 FMA as the fallback; the simulator models the 80 small all-reduces per step. |
| Engram/n-gram tables (197 GB for DeepSeek) push the package count up | Phase 1 step 3 capacity analysis includes tables; gather indices are prefetchable so remote placement costs capacity, not latency. |
| Verilator or Yosys runs out of memory | Phase 0 calibration caps, the `TINY`/`MID` configs, hierarchical synthesis, `memrun` logging |
| Yosys SV front end gaps | sv2v in the flow. RTL stays in a conservative SV subset (no classes or interfaces in synthesizable code; packages and structs allowed). |
| VM RAM for P&R | 12 GB VM cap. Block sizes come from Phase 0 calibration. Nothing heavy runs natively during P&R; lower the cap to 10 GB if macOS swaps. |
| HBM3e tCCD_L above 2.5 ns (the scheduler cannot hide it; 0.79 of peak at 4.1 ns) | Check the vendor datasheet before tape-out (hbm.md H-6); fallback: SID-alternating address map. Re-run `make hbm` with real timing. |
| OpenROAD pin becomes stale | Keep the pin. Bumping needs the Bazel build (see the env_setup README). |
| HBM3e timing not public at vendor level | JESD238 timing scaled to 9.6 Gb/s, documented as representative. The sweep shows sensitivity. |
| RTL and simulator drift apart | Single ISA source, shared timing table file (`hbm_timing.yaml` → SV params + C++), calibration in CI-style `make calib` |
| MoE routing realism without running the models | Synthetic Zipf sweeps. Optional: measure routing histograms from a small open MoE run locally with MLX at 4-bit, so it fits in 16 GB. |
| Trace volume | Online aggregation, windowed cycle mode, zstd streaming to the SSD |

---

## 7. Milestones

| M | Content | After phase |
|---|---|---|
| M0 | Environment calibrated, skeleton green | P0 |
| M1 | Architecture frozen with per-model sizing | P1–P2 |
| M2 | `TINY` core runs GEMM from ISA | P3 |
| M3 | `TINY` die + `PKG_TINY` + `MULTI_TINY` run e2e programs | P4 |
| M4 | Verification closure | P5 |
| M5 | PPA plus area/shoreline check against the reticle | P6 |
| M6 | Calibrated perf simulator | P7 |
| M7 | 11 per-model reports and a cross-model summary | P8 |
