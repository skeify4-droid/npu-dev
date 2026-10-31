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
3. Yosys: synthesize each leaf once with `hierarchy` kept. Flatten only up to the cell count Phase 0
   calibration shows to be safe (expected roughly 0.3–0.5 M cells for 6 GB; to be measured).
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
          32 tensor cores, global SRAM, command processor, D2D port, C2C ports, collective engine
Core:     MXU (matrix unit) + VPU (vector unit) + SFU + DMA + scratchpad + sequencer
```

| Item | Baseline *(P1)* | Derived |
|---|---|---|
| Clock | 1.5 GHz (Phase 6 synthesis checks it on ASAP7) | |
| MXU per core | 64×64 PEs, each a 4-way FP8 dot-product (16,384 FP8 MAC/cycle), FP32 accumulate; BF16 at ½ rate | |
| Compute per die | 32 cores | 1.57 PFLOPS FP8 dense, 0.79 PFLOPS BF16 |
| HBM per die | 4 × HBM3e 12-Hi | 144 GB, 4.9 TB/s → **ops:byte ≈ 320** |
| Per package | 2 dies | 3.1 PFLOPS FP8, 288 GB, 9.8 TB/s |
| Scratchpad per core | 4 MB, 32 banks, double-buffered DMA | 128 MB/die |
| Global SRAM per die | 32 MB, software-managed (KV staging, expert prefetch, collectives) | |
| NoC | 2D mesh, 128 B/cycle/link/direction, HBM controllers on edge routers | must sustain ≥ 3.3 KB/cycle HBM ingress |
| D2D | UCIe-like, 5 TB/s/direction (modeled PHY) | |
| C2C | 16 ports × 100 GB/s/direction (modeled SerDes) | 1.6 TB/s/package |
| Data types | FP8 E4M3/E5M2, BF16, MXFP4 weights (decompressed in the DMA path), FP32 accumulate, INT8 optional | |

Capacity check (Phase 1 refines it): a ~1T-parameter model at FP8 needs ≥ 4 packages for weights, and in practice
8 or more to leave room for KV cache. "Flash" class models should fit 1 package. Multi-package support is a
hard requirement, not an extra.

### 2.3 LLM features the hardware and compiler must support

These come from the predecessor families of the target models. **I could not verify the exact configs of the
listed releases; several are newer than my training data.** Phase 1 step 1 reads each model's `config.json`
before any of this is frozen.

- MoE: top-k routing, shared experts, fine-grained experts (DeepSeek, GLM, Ling, Qwen3-Next, Mistral Large lines).
  The design needs fast top-k in the VPU, a gather/scatter DMA, and expert-parallel all-to-all.
- Attention variants: GQA; MLA (latent KV compression and weight absorption, DeepSeek line); sparse/indexer attention
  (DeepSeek V3.2 DSA-style top-k token selection); linear/recurrent hybrids (Qwen3-Next Gated DeltaNet, MiniMax
  lightning attention); sliding-window hybrids (MiMo line); Step-3 MFA.
- Multi-token prediction / speculative decode heads (DeepSeek, GLM, MiMo lines).
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
1. Fetch `config.json` for all 11 models (DeepSeek V4.1 Flash, GLM-5.3, GLM-5.3-Flash, MiMo-V2.6-Pro, MiMo-V2.6-Flash,
   Ling 3.1 Flash, MiniMax-M3.1-Flash, Step 5 Preview, Mistral Large 4, Muse Spark Open, Qwen3.8-Flash-Next).
   Normalize them into a descriptor schema: layer pattern, attention type per layer, MoE (E, k, shared, d_ff),
   vocab size, MTP, and dtype of the published weights. Flag any model whose features fall outside the schema.
2. Build an op-graph generator for prefill and decode (batch, context length, KV dtype as parameters).
3. Build a roofline / analytical model: FLOPs, bytes, KV footprint, capacity per die, all-to-all volume per parallelism scheme (TP/EP/PP/DP).
4. Sweep: stacks/die, dies/package, packages, dtype (FP8 vs MXFP4 weights), batch, context.
   Answer: the batch per expert at which MoE layers become compute-bound (≈ ops:byte / 2 tokens per expert at FP8);
   the decode bandwidth floor per model; the minimum package count per model.
- Exit: §2 numbers frozen in `docs/spec/arch.md` with justification plots. The per-model package count is fixed.

### Phase 2: Architecture and micro-architecture spec (M)
- `isa.md` + `isa.yaml`: tile-level macro instructions (DMA_LOAD/STORE with 2D/3D strides, gather, page-indirect;
  MXU_GEMM; VPU ops; SFU; TOPK; SYNC/barrier/semaphore; COLLECTIVE). Decoupled access/execute queues per core.
- `numerics.md`: FP8/BF16/MXFP4 formats, rounding (RNE), saturation, accumulation order, NaN/Inf policy.
- `memmap.md`: global address map (HBM address interleaving across stacks/PCs/banks, scratchpad, global SRAM, remote die/package windows).
- `noc.md`: topology, routing (XY), virtual channels for deadlock freedom (request/response separation), credit flow control.
- `hbm.md`: controller architecture (per-PC queues, FR-FCFS scheduler, open/close page policy, refresh,
  timing table) and the DFI-like boundary to the behavioral PHY + DRAM model.
- `d2d_c2c.md`: link layer (flits, credits, CRC/replay optional), collective engine (ring/tree all-reduce, all-to-all).
- Exit: spec reviewed. `isa.yaml` generator emits SV/C++/Python. Every block has an interface table.

### Phase 3: RTL, leaf and core blocks (L)
| Block | Contents | Verification hook |
|---|---|---|
| `common` | FIFOs, credit interfaces, arbiters (RR, priority), SRAM wrapper (behavioral ↔ fakeram macro switch) | formal |
| `arith` | FP8/BF16 multipliers, 4-way dot-product PE, FP32 adder/accumulator, converters, MXFP4 decoder | **exhaustive** FP8×FP8 (65,536 pairs per format) vs `ml_dtypes`; BF16 random + corners |
| `core/mxu` | output-stationary or weight-stationary array (Phase 2 decides), skew/de-skew, accumulator SRAM | cocotb GEMM vs NumPy |
| `core/vpu`, `sfu` | lanes, reductions, exp/recip/rsqrt (LUT + polynomial), top-k unit | ULP-error bounds vs NumPy |
| `core/dma` | strided, gather, page-indirect, MXFP4 decompress, outstanding-request tracking | cocotb + formal on address generator |
| `core/seq` | ISA fetch/decode, queues, scoreboard, semaphores | directed + random programs |
| `core/spm` | banked scratchpad, bank-conflict arbitration | formal (no lost/duplicated requests) |
- Exit: every block has lint-clean code under Verilator `-Wall`, green unit tests and green formal. `TINY` core runs a GEMM program from the assembler.

### Phase 4: RTL, memory system, NoC, links, integration (L)
- `noc`: router (VCs, credits) and a parameterized mesh generator.
- `hbm`: controller RTL. PHY = behavioral DFI responder. DRAM = behavioral bank-state model with a **timing-violation
  checker** (tRCD, tRP, tRAS, tRRD, tFAW, tCCD, refresh).
- `link`: D2D and C2C link-layer RTL, behavioral PHY with configurable latency and bandwidth. Collective engine.
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
  1. PE cluster 16×16, or 32×32 if calibration shows it fits (the main compute density and Fmax evidence)
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
| Target model configs differ from predecessor assumptions, or are not public yet | Phase 1 step 1 comes first. The descriptor schema is extensible. Unknown layer types fall back to a generic op with FLOP/byte annotations. |
| Verilator or Yosys runs out of memory | Phase 0 calibration caps, the `TINY`/`MID` configs, hierarchical synthesis, `memrun` logging |
| Yosys SV front end gaps | sv2v in the flow. RTL stays in a conservative SV subset (no classes or interfaces in synthesizable code; packages and structs allowed). |
| VM RAM for P&R | 12 GB VM cap. Block sizes come from Phase 0 calibration. Nothing heavy runs natively during P&R; lower the cap to 10 GB if macOS swaps. |
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
