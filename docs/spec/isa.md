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
- Semaphores are **monotonic counters**. Counts wrap modulo 2^32, and the serial compare stays correct across the wrap as long as
  `|value - target| < 2^31` (as `isa.sem_ready` says; review finding V-11): the target is less than 2^31 ahead of the value, and the
  value is less than 2^31 past it. A byte-counting semaphore can therefore count any total (a 32K-token prefill puts about 2.6 GB
  through one all-reduce semaphore) provided no single wait spans 2 GiB and no wait uses a target more than 2 GiB stale. The carried
  base and the buffer credits (below) bound both.
  - A ping-pong pipeline over iteration i waits for "filled >= i + 1" and "freed >= i - 1". No decrement races are possible.
- **Two kinds of semaphore** (the compiler assigns them):
  - **Local:** only this core's engines and `SINC` change it. `SSET` may reset it, and faults `SEM_BUSY` while a queued instruction
    references it.
  - **Network-facing:** any semaphore that `rsem`, `SIGNAL` or a remote write can reach. It is **never reset while any core is
    running**, because adds from other cores may be in flight and nothing orders them against `SSET`. The CP zeroes these only at a
    quiescent point (boot, or a restart after a fault, with every core stopped and the links drained). Using `SSET` or `SIGNAL`
    mode 1 on one in a kernel is a program error; the golden model flags it.
- **Kernel boundaries** *(review findings I-4, V-2; rule chosen by the user, 2026-11-01)*. There is no barrier between kernels: a
  core starts its next kernel when it halts, so adds of kernel k + 1 from a faster producer may arrive while a consumer is still in
  kernel k. Two rules keep the kernels apart:
  - **Credit before reuse.** Every receive buffer follows the d2d_c2c.md L-6 discipline: two buffers per call site used
    alternately, one semaphore per buffer parity, and a credit `SIGNAL` from the consumer once it has consumed a buffer. A
    producer writes a parity's buffer, and adds to its semaphore, only after the credit for that parity's previous use.
    Parities and credit counts carry across kernel boundaries, so a kernel boundary is only another call. This holds for
    collectives, pipeline hand-offs and HBM buffers that complete by `SIGNAL`.
  - **Every network-facing semaphore belongs to one call site** *(review finding W-3)*, like a buffer: buffer semaphores,
    credit semaphores and `SIGNAL`-only handshakes (count exchanges, ready flags) alike. The compiler allocates them for the
    whole program, across kernels, and records the map with the program. A credit or flag counts events, not bytes of a
    reusable buffer, so with an exact base (below) its own waits never pass early and it needs no credit of its own. What it
    must not see is adds from another call site.
  - **Reassignment.** With 63 usable semaphores per core, an index will serve several call sites over a program. It moves from
    call site X to call site Y only:
    - at a quiescent point (boot, or the CP's restart after a fault; above), or
    - after its owner's last wait at X, with every add of Y causally after that wait. Adds from other cores wait for a
      **final credit**: a `SIGNAL` from the owner to each of Y's adders, sent after the last X wait, which Y's first add waits
      for like any later credit. A credit semaphore's adds are caused by its owner's own buffer writes, so the owner issues Y's
      first write after its last X credit wait, in the same DQ or behind a local semaphore.
    - Y's base is X's final target. The extra cost is one `SIGNAL` per adder per move, usually sent long before Y's first add.
    - Example (W-3): kernel k uses S20 for a "counts ready" handshake from 8 dies; kernel k + 1 maps S20 to a pipeline hand-off.
      Without the rule, a producer that has already halted k adds to S20 before the consumer's kernel-k wait, which then passes
      early and reads stale counts. With it, the producer's first hand-off add waits for the consumer's final credit, which the
      consumer sends after that wait.
  - **Carried base.** A wait target is `base + count`, where `base` is the previous use's final wait target (whatever call site
    that was), carried forward exactly, never read from the live value. Where the totals per use are fixed at compile time,
    the CP may write the bases into SPM with the launch arguments. Where they are data-dependent, the program carries them: the
    MoE all-to-all's byte totals come from all-gathered counts, which the CP never sees. So each core keeps a **base table** in a
    runtime-reserved SPM region (64 x 4 B), updated by the program after each wait. It persists across kernels, and the CP zeroes
    it with the semaphores at a quiescent point.
  - **A credit wait counts one credit per consumer**: a buffer read by n consumers (a `DMA_MCAST` destination set, or an owner
    with 32 contributors that each credit their own buffer) is free again at `base + n` credits for that use.
  - Example (V-2): 32 contributors write-add into an owner's buffer with `rsem`. Contributor A halts kernel k and starts k + 1. Its
    adds go to the other parity's semaphore. Its next adds to kernel k's semaphore (kernel k + 2) wait for the owner's credit, which
    the owner sends only after its kernel-k wait. So the owner's kernel-k count holds kernel-k bytes only.
  - The golden model checks that every add to a network-facing semaphore is causally after its owner's wait for that
    semaphore's previous use, whichever call site the use belonged to (the credit for a buffer parity, or the final credit
    after a reassignment), and that every wait target equals the carried base plus the use's total.
  - Rejected:
    - A barrier per kernel (start kernel k + 1 only after every core that shares a network-facing semaphore has halted k). It puts
      a system-wide sync on the critical path of every kernel: about 0.6-4.5 µs across 8 dies, 0.4-2.7% of a DeepSeek decode step
      at one kernel per layer.
    - Semaphore sets alternating by kernel parity. They double the network-facing semaphores, and kernel k + 2 would still need
      the credit or a lagged barrier.
    - More semaphores per core instead of reassignment (W-3). The 6-bit semaphore fields are in every tile instruction's sync
      block, the NoC header (`rsem`) and the list entries, so it is an encoding change, and it only moves the limit.
- The register value in a wait condition is captured at issue, so loop counters work directly.
- **Same queue:**
  - MQ and VQ instructions see the memory effects of earlier instructions in the same queue.
  - DMA instructions in the same queue may overlap. A dependency between two transfers needs a semaphore or a `SIGNAL` between them.
- **Different queues:** only semaphores order them. The hardware does not check SPM address overlaps between queues; the golden model
  does (a race checker in simulation).
  - The four DQs progress independently: a DQ whose head instruction or list entry waits on a semaphore holds nothing the others
    need (microarch.md §2.5, review finding V-3).
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
    a kernel has landed when every core of the kernel has halted, which is what the CP waits for before it reads the kernel's
    results. It does not wait for this before starting a core's next kernel: kernels are kept apart by the credit rule above
    (review finding V-2).
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
  | `scale_reg` | address of the scale of the base element's block, in the scale array of a non-paged block-format operand (row stride TD `aux0`; memmap.md §6). The base element starts a block (review finding W-5). Unused with `SCALE_INLINE` (Z-4) |
  | `aux_reg` | SPM offset where the FP8-target conversions and `NVFP4_BF16` write the FP32 column scales (R0: none) |
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
  - **Each entry is checked like the instruction it encodes** (review finding V-6): `COPY`, `GATHER` and `SCATTER` like `DMA_COPY`
    (at most one `GLOBAL` operand, else `TD_SPACE`; `rsem` only to SRAM, else `ILLEGAL_FIELD`), `MCAST` like `DMA_MCAST` (`rsem`
    required). `FAULT_ADDR` holds the faulting entry's SPM offset.
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
- **Width of the inbound path** (review findings I-3, V-1): the converter produces 256 elements per cycle and the DMA writes SPM at
  512 B/cycle, shared with the block scales it stages before the data, so the exact NVFP4 → BF16 path carries 1.60x a core's HBM
  share (numerics.md D1).
- **Scales before data** (review findings V-1, W-1): block scales are read into the DMA staging ring (CSR `DMA_STAGE`) before the
  data that uses them, and a conversion unit that spans several responses is staged raw there. A unit's raw slot is taken
  with its scales, when the scale read is issued, so a started entry never waits for ring space. Every data read and every
  unit belongs to exactly one ring entry: a granule shared by two entries is two partial reads (review finding Z-1). So a
  read response never waits for another one, and the ring cannot fill with entries that can never complete (noc.md §4 rule
  2, microarch.md §2.5). `DMA_STAGE` holds at least 2 KiB, else a block-format conversion faults `ILLEGAL_FIELD` (Z-3).
- **Scale location and write packets** (review findings V-4, V-5, W-5): an element's block scale is found from its row and its
  whole in-row offset (dims 0, 2, 3). Every dim-0 run starts on a block: the base (or the paged pointer's `byte`), `s1`, `s2` and
  `s3` are multiples of the block's bytes (`ILLEGAL_FIELD`). `SCALE_INLINE` rows (Engram) are exempt: each row is its elements
  followed by their scales, so any base and `s1` work (memmap.md §6, review finding Z-4). Write packets merge adjacent destination bytes, including consecutive rows of one page, so a
  32 B block of 4 B or 8 B scale rows is one aligned write. memmap.md §6 walks two KV appends field by field, and the batched
  (`DMA_SCATTER`) form (review finding W-4).

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
    DMA_SCATTER dq=2 src=R29 td=Y_ROWS dst=R31 td=OUT idx=R32 wop=ADD_BF16 rsem=S7    wsem=S6 wimm=1   # combine (BF16 rows)
```

Each instruction waits on at most one semaphore. The second `MXU_MM` also depends on S2, so in real code the down-weight DMA signals
S4 as well and the wait is `S4 >= 2`. The compiler's scheduler does this counting.
- Bigger experts loop the weight DMA over N chunks in ping-pong buffers, with `wreg` holding the loop counter.

## 8. Faults

A fault stops the core, records `FAULT_CAUSE` (isa.yaml enum `fault_cause`) and `FAULT_ADDR`, and sends an interrupt to the CP.
- "Stops" means the front end and the engines (review finding V-7). The network interface keeps working: responses to the core's
  outstanding requests are still accepted and dropped (their tags freed), and its SPM, GSRAM and CORECTL targets keep serving
  requests. A response for a tag that is not outstanding is dropped, faulted or not. A tag is freed by its response, by an
  `ERR` other than `LINK`, or by its timeout, and `STATUS` shows the DMA busy until every tag and burst id is free; the CP
  restarts the core only then (d2d_c2c.md L-4, review finding W-2). So a faulted core never blocks RSP or REQ on its die, which is what the link-down drain (d2d_c2c.md L-4) and the CP's
  recovery accesses rely on.

Causes:
- illegal opcode or field value;
- a descriptor whose space is not allowed for that operand, including a DMA with both operands `GLOBAL` (`TD_SPACE`);
- address faults (memmap.md §8), including `RAIL`;
- ACC or SPM range overflow;
- write-add to HBM, and HBM writes that are not 32 B aligned in address and size (hbm.md H-2);
- an invalid page-table entry in paged DMA;
- a D2D/C2C link going down with transfers outstanding (`LINK`, from the bridge's `ERR` response; d2d_c2c.md L-4). A request the
  far side had already accepted gets no `ERR` and faults with `TIMEOUT` instead. A replay-buffer `ERR` can be stale and name a
  tag that is free or reused (review finding W-2): an `ERR` (`LINK`) always faults the core, frees no tag, and makes
  `FAULT_ADDR` advisory; the CP restarts every core of the step;
- a DMA request with no response within the timeout CSR (`TIMEOUT`; review finding I-15, which separates it from `LINK`);
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
