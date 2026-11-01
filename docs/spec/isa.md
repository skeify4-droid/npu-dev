# ISA: programming model (Phase 2)

Status: **v0.1, reviewed 2026-11-01** (Phase 2 self-review, [spec_review.md](../reports/spec_review.md)).
- Source of truth: `isa/isa.yaml`. `make isa` validates it and regenerates `isa/gen/isa_pkg.sv` (SV), `isa/gen/isa.h` (C++),
  `isa/gen/isa_defs.py` (Python constants) and the instruction reference [isa_ref.md](isa_ref.md).
- This document explains the model and the decisions. Bit layouts and the full instruction list are in the reference.
- Micro-architecture: [microarch.md](microarch.md). Addresses and ordering: [memmap.md](memmap.md).

## 1. Model in one paragraph

Each core runs its own program from a 64 KiB instruction memory.
- **Front end:** a small in-order scalar unit executes control flow and scalar arithmetic, and issues **tile instructions** into six
  queues:
  - four DMA queues (`DQ0`-`DQ3`);
  - the MXU queue (`MQ`);
  - the VPU queue (`VQ`, which also covers the SFU and the top-k unit).
- **Execution:** each engine runs its queue in order, decoupled from the front end and from the other engines (decoupled
  access/execute).
- **Synchronization:** only through **64 counting semaphores per core**.
  - Every tile instruction can wait for one semaphore to reach a value before it starts, and increment one when its effects are complete.
  - The same semaphores can be added to over the network, which is how cores, dies and packages hand off data.
- **No caches and no virtual memory.** The compiler places every tensor (memmap.md).

## 2. State

| State | Size | Notes |
|---|---|---|
| R0-R31 | 64-bit | R0 reads zero; addresses, loop counters, packed FP32 parameters |
| PC | 12-bit instruction index | |
| S1-S63 | 32-bit counters, compared as serial numbers (§3) | S0 encodes "no semaphore" in sync fields |
| DTAB | 256 x 32 B tile descriptors | loaded with the program image, or by DMA (space `DTAB`) with the ordering rule of §4 |
| CSRs | `isa.yaml` enum `csr` | core id, node id, cycle counter, fault cause/address, perf counters |
| IMEM | 4,096 x 128-bit | written by the CP or by DMA overlays (space `IMEM`) followed by `FENCE` |

## 3. Synchronization

Every tile instruction carries a sync block:
- `wsem`, `wreg`, `wimm`: start only when sem[wsem] has reached `T = R[wreg] + wimm`, i.e. `(int32)(sem[wsem] - T) >= 0`
  (serial-number compare, review finding I-4; `isa.sem_ready` is the reference). `SWAIT` uses the same compare.
- `ssem`, `sinc`: `sem[ssem] += sinc` when the instruction's effects are complete:
  - **MXU:** ACC written.
  - **VPU:** destinations written.
  - **DMA:** every write is acknowledged, or for a read every byte is in SPM. A `DMA_MCAST` completes when every packet is
    injected (below).

Rules:
- Semaphores are **monotonic counters**. Counts wrap modulo 2^32, and the serial compare stays correct across the wrap as long as a
  wait's target is less than 2^31 ahead of the value. A byte-counting semaphore can therefore count any total (a 32K-token prefill
  puts about 2.6 GB through one all-reduce semaphore) provided no single wait spans 2 GiB.
  - A ping-pong pipeline over iteration i waits for "filled >= i + 1" and "freed >= i - 1". No decrement races are possible.
- **Two kinds of semaphore** (the compiler assigns them):
  - **Local:** only this core's engines and `SINC` change it. `SSET` may reset it, and faults `SEM_BUSY` while a queued instruction
    references it.
  - **Network-facing:** any semaphore that `rsem`, `SIGNAL` or a remote write can reach. It is **never reset while any core is
    running**, because adds from other cores may be in flight and nothing orders them against `SSET`. The CP zeroes these only at a
    quiescent point (boot, or a restart after a fault, with every core stopped and the links drained). Using `SSET` or `SIGNAL`
    mode 1 on one in a kernel is a program error; the golden model flags it.
- **Kernel boundaries.** A kernel's wait targets on a network-facing semaphore are `base + count`, where `base` is the value after
  every add of the previous kernel has landed: the previous kernel's final wait target, which the program or the runtime carries
  forward (the CP writes it into SPM with the launch arguments). Reading the live value would not do, because adds of the next kernel
  from a faster producer may already have arrived. Totals per kernel are fixed at compile time, so the carried base is exact.
  - Rejected: a kernel-boundary barrier before `SSET`, which would need a system-wide quiescence point every kernel.
- The register value in a wait condition is captured at issue, so loop counters work directly.
- **Same queue:**
  - MQ and VQ instructions see the memory effects of earlier instructions in the same queue.
  - DMA instructions in the same queue may overlap. A dependency between two transfers needs a semaphore or a `SIGNAL` between them.
- **Different queues:** only semaphores order them. The hardware does not check SPM address overlaps between queues; the golden model
  does (a race checker in simulation).
- **Front-end interaction:**
  - `SWAIT` stalls the front end (needed before `LD` reads data produced by an engine, e.g. MoE token counts).
  - `FENCE` waits for selected queues to drain.
  - `HALT` drains everything and interrupts the CP.
- **Remote:**
  - `SIGNAL` adds to any semaphore in the system after all earlier transfers of its DMA queue are acknowledged (release).
  - A DMA with `rsem` adds its payload byte count to the destination core's semaphore, packet by packet (put-with-signal, no ordering
    needed).
- **Multicast *(review finding I-5)*:** `MC_WR` packets are posted, with no acknowledgement (noc.md §2). So:
  - A `DMA_MCAST` completes, and `FENCE`, `HALT` and `SIGNAL` treat it as done, once every packet is **injected**. That frees the
    source buffer; it says nothing about delivery.
  - The data is ordered at each destination only by its `rsem`, which is therefore required (`ILLEGAL_FIELD` if S0). A `SIGNAL`
    after a multicast is not a release of the multicast data.
  - Each destination program waits for the full byte count of every multicast it is sent before it halts. Then all multicast data of
    a kernel has landed when every core of the kernel has halted, which is what the CP waits for before it reads results or loads the
    next kernel.
  - Rejected: per-destination acks. A full-die rectangle returns 32 ack flits per 16-flit burst to the source, so acks would halve the
    multicast rate at the source's ejection port.

## 4. Tile descriptors

- A tile operand is a register (base address) plus a DTAB index. Operands include DMA source/destination, MXU A/W, and VPU d/s0/s1/s2.
- The descriptor gives:
  - dtype and space (GLOBAL, SPM, ACC, DTAB, IMEM);
  - flags (PAGED, IDX64, WRAP, SCALE_INLINE, SCALE_REGION);
  - up to 4 dimensions (`n0` contiguous, `n1`-`n3` with signed byte strides `s1`-`s3`; stride 0 broadcasts);
  - `aux0`: the scale row stride of a block format (or the `VSOFTMAX` softcap);
  - `row_bytes`: the row size of a gather or scatter;
  - `page_shift` (log2 rows per page) and `sc_off` (the scale region inside a page) for PAGED operands (memmap.md §6).
- Each field has one meaning (review finding I-2: `aux0` used to be both the page shift and the scale stride).
- Why descriptors instead of operand fields:
  - A 4-D strided DMA needs about 300 bits of shape.
  - Shapes repeat across loop iterations while bases change.
  - So shapes live in DTAB, set up once per kernel, and the 128-bit instruction carries only register indices.
- Dynamic sizes (tokens per expert, a ragged last tile) come from the `n_reg` / `m_reg` override of the outermost count.
- **When a TD is read** *(review finding I-11)*: an engine reads its TDs from DTAB when it starts an instruction, not at issue, so a
  queued instruction sees DTAB as it is when it starts. DTAB updates are therefore ordered like IMEM overlays:
  - before a DMA writes DTAB entry e, `FENCE` the queues that hold instructions naming e (or wait on their completion semaphores);
  - before issuing an instruction that names the new e, wait on the DTAB-writing DMA's completion (`ssem`, or `FENCE`).
  - The hardware does not check this; the golden model's race checker does (as for SPM, §3). DTAB rewrites are rare (kernel setup),
    so the rule costs nothing in practice.
  - Rejected: capturing TD contents at issue. Queue entries would grow by 512-1,024 bits each (about 15 KiB per core) and the front
    end would need up to 4 DTAB reads per cycle.

## 5. Instruction classes

**Front end (`FE`):**
- `LI` (64-bit immediate), integer ALU (`ADD`, `ADDI`, `SUB`, `MUL[I]`, logic, shifts, `MIN`/`MAX`);
- branches (`BEQ`, `BNE`, `BLT`, `BGE`, `BLTU`, PC-relative);
- `LD`/`ST` on the local SPM, `CSRR`/`CSRW`;
- `SWAIT`/`SSET`/`SINC`, `FENCE`, `IRQ`, `HALT`.

**DMA (`DQ0`-`DQ3`):**
- `DMA_COPY`:
  - 4-D strided copy between a local space (SPM, DTAB, IMEM) and any space;
  - **at most one operand is `GLOBAL`**, otherwise `TD_SPACE` (review finding I-1). Read data must sink in this core's
    reserved SPM without waiting for anything on the network (noc.md §4 rule 2), so the DMA never sends read data on as a
    write. A global-to-global move (an HBM page move, or HBM into another core's SPM) is two transfers through a local SPM
    buffer with a semaphore between them, or the consumer core reads the data itself;
  - optional paging of the row dimension (memmap.md §6);
  - conversion on the way in (`cvt`) and a write operation at the destination (`wop`: write, add FP32/BF16, max FP32; adds only to SRAM).
- **Operand registers of `T_DMA`.** Each has one meaning:

  | Field | Holds |
  |---|---|
  | `src_reg`, `dst_reg` | base addresses; a PAGED operand's base is a paged pointer (`isa.yaml` `paged_ptr`: node, first row, byte in the row) |
  | `idx_reg` | SPM offset of the index list (gather, scatter) |
  | `n_reg` | override of the outermost count (0: from the TD) |
  | `scale_reg` | address of the scale array of a non-paged block-format operand (row stride TD `aux0`) |
  | `aux_reg` | SPM offset where FP8- and NVFP4-target conversions write the FP32 column scales (R0: none) |
  | `pt_reg` | SPM offset of the page table of the PAGED operand (at most one operand is PAGED) |
  | `ts_reg` | NVFP4 FP32 tensor scale in bits [31:0] (R0: 1.0) |

- `DMA_GATHER` / `DMA_SCATTER`: row lists with any row size and int32/int64 indices. Optional page translation of the row index, so
  sparse-attention KV blocks and paged caches gather in one instruction.
- `DMA_MCAST`: one SPM tile to the same offset in a rectangle of cores. Posted: complete when injected, ordered at the destinations
  only by `rsem` (§3).
- `DMA_LIST`: a precomputed list of transfers in SPM. Collective schedules and MoE all-to-all are lists.
  - Each entry is 32 B, `isa.yaml` `dma_list_entry` (review finding I-12(a)): `COPY`, `GATHER`, `SCATTER`, `MCAST` or `SIGNAL`, with
    absolute addresses, TD indices, `wop`, `rsem` and a count override. Entries carry no conversion and no paging.
  - **An entry can wait on a semaphore** (`wsem`, `wval`, serial compare). Entries start in order, so a waiting entry holds the ones
    behind it, but transfers already started keep going. This is what the per-chunk forward of the staged all-to-all needs
    (d2d_c2c.md L-7): one `DMA_LIST` of chunk forwards, each waiting on its chunk's count, takes one queue slot instead of one per
    chunk.
  - A `SIGNAL` entry releases every earlier entry of the list and every earlier instruction of the queue.
- `SIGNAL`: remote semaphore add/set with release semantics.
- **Block-scale contract:**
  - Converting MXFP4 / NVFP4 / MXFP8 to E4M3 writes the elements plus one FP32 **column scale** per (output row of the weight,
    256-deep K tile) to `aux_reg`.
  - `MXU_MM` multiplies each K tile's partial sum by it (`cs_reg`).
  - The per-16 / per-32 block scales inside a K tile are folded into the elements (exact for MX formats unless an element underflows;
    NVFP4 rounds, see numerics.md N-2).
  - Converting to BF16 is exact and needs no column scales. The NVFP4 FP32 tensor scale `R[ts_reg]` still goes into `aux_reg` as a
    column scale. For the fold it multiplies `2^c` (numerics.md N-2 step 4).
  - Scales of a paged operand live in the same page as its rows (memmap.md §6), so one page-table entry translates both.
- **Width of the inbound path** (review finding I-3): the converter produces 256 elements per cycle and the DMA writes SPM at
  512 B/cycle, so the exact NVFP4 → BF16 path carries 1.65x a core's HBM share (numerics.md D1).

**MXU (`MQ`):**
- `MXU_MM` computes `ACC (+)= A x W` for any M (≤ ACC rows / N tiles), K and N. Inside the instruction the MXU tiles K (256 FP8 or
  128 BF16 deep) and N (64 wide).
- `trans_w` takes W in `[N, K]` layout: HF weights, and K-cache tiles for `Q K^T`.
- `acc_mode`:
  - `OVERWRITE` (first K tile writes);
  - `ACCUM`;
  - `RESCALE`: multiply existing ACC rows by `rowscale` first; this is the FlashAttention `O = alpha * O + P V` update.
- `mode = BF16X3` emulates FP32 with three BF16 passes (numerics.md N-3). The hyper-connection mixer uses its two-pass form (the
  stream is exact in BF16, so the `A_lo` pass is dropped; numerics.md D2).

**VPU (`VQ`), all arithmetic in FP32, outputs rounded to the destination dtype:**
- `VEW`: element-wise, unary/binary/ternary. Includes the router scoring functions, SwiGLU (also clamped), integer index math.
- `VRED`: row/column reductions, also grouped (Step 5 region pooling) and the indexer's weighted-ReLU sum.
- `VSCAN`: prefix sums/products (linear-attention decays, offsets).
- `VNORM`: RMS, Layer, grouped RMS (q/k norm, Qwen HC), RMS statistic only (mHC).
- `VROPE`: half or interleaved, partial rotary dims. The cos/sin tables are gathered by position, so YaRN scaling stays in the tables.
- `VSOFTMAX`: one FlashAttention step.
  - Scale, causal or sliding-window mask, optional softcap.
  - Updates the running max and sum, and writes P and `alpha`.
- `VQUANT`: E4M3 per row or tensor, MXFP8, MXFP4, NVFP4. Used for KV writes and dynamic activation quantization.
- `VTOPK`: streaming per-row top-k with a running state; plain or block-max (MiniMax MSA); deterministic tie-break.
- `VROUTE`: the whole MoE router.
  - Scoring: softmax / sigmoid / sqrt-softplus.
  - Correction bias, group-limited selection (Ling: 4 of 8 groups), top-k, normalization, routed scale.
- `VBUCKET`: counting sort of expert ids into counts, offsets and a permutation (MoE dispatch).
- `VXPOSE`, `VMATVEC` (FP32 skinny matmul on the VPU), `VTRINV` (unit-lower-triangular inverse for chunked DeltaNet/KDA).
- Hyper-connections (hyper_connections.md HC-4): `HC_MIX`, `HC_SINKHORN`, `HC_PRE` (also Qwen's gated mean), `HC_POST`
  (mHC or gated).

## 6. Coverage of the 11 models

| Feature (arch.md §2.3) | Instructions |
|---|---|
| Dense / expert GEMM, LM head, MTP and drafters | `MXU_MM` (+ `DMA_COPY` with `cvt` for 4-bit and MXFP8 weights) |
| MoE routing and dispatch | `VROUTE`, `VBUCKET`, `DMA_GATHER`, `DMA_SCATTER wop=ADD_F32`, `DMA_LIST` (all-to-all) |
| GQA / MQA / MLA (absorbed) attention | `MXU_MM trans_w` (`Q K^T`), `VSOFTMAX`, `MXU_MM RESCALE` (`P V`) |
| Sliding window, attention sinks | `VSOFTMAX` mask `WINDOW`, `WRAP` ring buffers, sink logits as initial `m`/`l` |
| Indexer sparse attention (DeepSeek, GLM, Mistral, Qwen, Step) | `MXU_MM` scores, `VRED WSUM_RELU`, `VTOPK`, `DMA_GATHER` (paged) |
| Block top-k (MiniMax) | `MXU_MM` index scan, `VTOPK BLOCKMAX` |
| Step 5 CSA region summaries | `VEW EXP`, `VRED` grouped, `VEW DIV`, `VQUANT E4M3` |
| Linear attention (KDA, Gated DeltaNet) | `MXU_MM`, `VSCAN`, `VTRINV`, `VEW` |
| Hyper-connections, n-gram gather | `HC_*`, `VMATVEC` / `MXU_MM BF16X3`, `DMA_GATHER` with `SCALE_INLINE` |
| KV writes (NVFP4 / FP8 / BF16) | `VQUANT` + `DMA_COPY` / `DMA_SCATTER` (paged) |
| Collectives (TP all-reduce, EP all-to-all, HC-5 small all-reduces) | `DMA_LIST`, `DMA_* wop=ADD`, `DMA_MCAST`, `rsem`, `SIGNAL` |

## 7. Example: one decode expert on one core

The FFN `down(swiglu(gate(x), up(x)))` for the tokens routed to one expert.
- Weights are MXFP4 in HBM; `R10` is their address.
- `R11` holds the token count (from `VBUCKET` and `LD`).
- ACC rows 0-511 hold gate/up, and rows 1024+ hold down.

```
    DMA_COPY  dq=0 src=R10 td=W_GU  dst=R20 td=SPM_GU cvt=MXFP4_E4M3 aux=R21     ssem=S1 sinc=1   # gate+up weights -> SPM
    DMA_COPY  dq=1 src=R12 td=W_D   dst=R22 td=SPM_D  cvt=MXFP4_E4M3 aux=R23     ssem=S2 sinc=1   # down weights (prefetch)
    MXU_MM    a=R24 td=X  w=R20 td=SPM_GU trans_w=1 acc=R0 m=R11 cs=R21  wsem=S1 wimm=1  ssem=S3 sinc=1
    VEW       func=SWIGLU d=R25 td=H  s0=R26 td=ACC_G  s1=R27 td=ACC_U              wsem=S3 wimm=1  ssem=S4 sinc=1
    MXU_MM    a=R25 td=H  w=R22 td=SPM_D  trans_w=1 acc=R28 m=R11 cs=R23 wsem=S4 wimm=1  ssem=S5 sinc=1   # (also waits S2: see note)
    VEW       func=COPY d=R29 td=Y_BF16 s0=R30 td=ACC_Y                              wsem=S5 wimm=1  ssem=S6 sinc=1
    DMA_SCATTER dq=2 src=R29 td=Y_ROWS dst=R31 td=OUT idx=R32 wop=ADD_F32 rsem=S7     wsem=S6 wimm=1   # combine
```

Each instruction waits on at most one semaphore. The second `MXU_MM` also depends on S2, so in real code the down-weight DMA signals
S4 as well and the wait is `S4 >= 2`. The compiler's scheduler does this counting.
- Bigger experts loop the weight DMA over N chunks in ping-pong buffers, with `wreg` holding the loop counter.

## 8. Faults

A fault stops the core, records `FAULT_CAUSE` (isa.yaml enum `fault_cause`) and `FAULT_ADDR`, and sends an interrupt to the CP. Causes:
- illegal opcode or field value;
- a descriptor whose space is not allowed for that operand, including a DMA with both operands `GLOBAL` (`TD_SPACE`);
- address faults (memmap.md §8), including `RAIL`;
- ACC or SPM range overflow;
- write-add to HBM, and HBM writes that are not 32 B aligned in address and size (hbm.md H-2);
- an invalid page-table entry in paged DMA;
- a D2D/C2C link going down with transfers outstanding (d2d_c2c.md L-4);
- an `SSET` on a semaphore that queued instructions reference (`SEM_BUSY`);
- a `DMA_MCAST` without `rsem` (`ILLEGAL_FIELD`).

Floating-point exceptions do not trap. NaN/Inf/saturation behavior is defined in numerics.md.

## 9. Encoding

- One 128-bit word: opcode `[127:120]`.
- Tile instructions: sync block `[119:91]` and operands `[90:0]`.
- Front-end instructions use `[90:0]` (64-bit immediates in `[63:0]`) and leave the sync bits zero.
- Opcode ranges:

  | Range | Class |
  |---|---|
  | 0x00-0x3F | front end |
  | 0x40-0x5F | DMA |
  | 0x60-0x6F | MXU |
  | 0x80-0xBF | VPU |
  | the rest | free |

- Why 128-bit words:
  - They fit a 64-bit immediate and a full sync block plus six register operands.
  - Decode is one fixed format per class.
  - Instruction bandwidth is irrelevant: one word per tile operation of tens to thousands of cycles.
- RTL uses the generated `isa_pkg`:
  - `isa_pkg::NAME` works in Yosys natively;
  - `import isa_pkg::*` needs the sv2v path (checked with Icarus, Verilator lint and Yosys).

## 10. Decisions and alternatives

| Decision | Alternative rejected | Reason |
|---|---|---|
| Own scalar front end, 128-bit words | RISC-V RV64 with custom tile extensions | 32-bit custom encodings cannot carry the sync block and operands; a RISC-V front end adds a full verification target for no gain in a statically compiled flow |
| Explicit semaphores | hardware scoreboard over SPM addresses | 6 decoupled engines, 4 MiB SPM: address-range tracking is costly and hides the schedule the compiler must reason about anyway |
| Monotonic counting semaphores with `>=` waits | binary semaphores / decrementing counters | no ABA or decrement races; loop counters serve directly as wait values |
| Fused macro instructions (softmax step, router, HC ops) | only primitive vector ops | decode latency: 80 hyper-connection sublayers and 60-90 routers per step would otherwise be many small dependent instructions |
| Collectives as DMA lists + write-add + multicast | `COLLECTIVE` instruction executed by a central engine | a central engine is a hotspot (one endpoint at 256 B/cycle) and adds latency to the 80 small HC all-reduces; distributed reduce-scatter/multicast is about 6x faster for them |
| Quantize in the VPU, dequantize in the DMA | both in the DMA | quantization needs row/block maxima (a reduction), which the VPU has |

## 11. Open items

- Closed by numerics.md: rounding and saturation of every conversion (N-2), the NVFP4 -> E4M3 residual (D1: exact BF16 path when
  memory-bound, fold when compute-bound), HC mixer precision (D2: BF16 hi/lo), BF16 linear-attention state (D3: rejected, FP32 stays).
  `FP_STATUS` CSR added (N-5).
- An assembler and a golden instruction-level model (`sw/compiler`, Phase 3) on top of `isa/isa.py`'s encoder.
- Instruction count and front-end stall rates are to be measured once the compiler emits real kernels (Phase 5/7).
