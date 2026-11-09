# Micro-architecture (Phase 2)

Status: **reviewed 2026-11-01** (Phase 2 self-review, [spec_review.md](../reports/spec_review.md)). Refines the frozen numbers of [`arch.md`](arch.md) into blocks, datapaths and interfaces.
Companion specs: [`isa.md`](isa.md) (programming model, `isa/isa.yaml`), [`memmap.md`](memmap.md) (address map),
[`noc.md`](noc.md) (network and deadlock freedom), [`numerics.md`](numerics.md) (formats, rounding, accumulation order),
[`hbm.md`](hbm.md) (HBM controller, timing, DRAM model), [`d2d_c2c.md`](d2d_c2c.md) (link layer, collective schedules).
Items marked *(P2)* are decisions made here; §8 lists what they changed in `arch.md`.

## 1. Die

```
          HBM stack 0 (north-west)              HBM stack 1 (north-east)
         [N1]   [N2]   [N3]   [N4]            [N5]   [N6]   [N7]   [N8]       N/S leaves: 4 per stack, 8 PCs each
          |      |      |      |                |      |      |      |
  [W1]--(1,1)--(2,1)--(3,1)--(4,1)----------(5,1)--(6,1)--(7,1)--(8,1)--[E1]
           |      |      |      |                |      |      |      |
  [W2]--(1,2)--(2,2)--(3,2)--(4,2)----------(5,2)--(6,2)--(7,2)--(8,2)--[E2]   (x,y) = core tile: core + router
           |      |      |      |                |      |      |      |                 + 1 MiB GSRAM slice
  [W3]--(1,3)--(2,3)--(3,3)--(4,3)----------(5,3)--(6,3)--(7,3)--(8,3)--[E3]
           |      |      |      |                |      |      |      |
  [W4]--(1,4)--(2,4)--(3,4)--(4,4)----------(5,4)--(6,4)--(7,4)--(8,4)--[E4]
          |      |      |      |                |      |      |      |
         [S1]   [S2]   [S3]   [S4]            [S5]   [S6]   [S7]   [S8]
          HBM stack 2 (south-west)              HBM stack 3 (south-east)

  W1-W4: C2C leaves (2 SerDes ports each); W2 also hosts the command processor, W3 the host interface.
  E1-E4: D2D sub-links to the sibling die (which is the same die rotated 180 degrees).
```

| Block | Count per die | Notes |
|---|---|---|
| Core tile | 32 (8 x 4) | core, 6-port router (2 planes), 1 MiB GSRAM slice |
| HBM leaf | 16 | 2-port router: mesh port + HBM controller port (8 pseudo-channels) |
| HBM controller | 4 (one per stack) | 32 pseudo-channel engines (read queue 32, write queue 16 granules), 4 NoC ports (one per leaf), own clock 1.2 GHz; [hbm.md](hbm.md) |
| D2D leaf + bridge | 4 sub-links | 2 lanes (one per plane) of 156 B link flits per cycle per direction each: 1.87 TB/s raw link rate, 1.45 TB/s payload per direction per die, 2 UCIe-A x64 modules per sub-link *(P2)*; [d2d_c2c.md](d2d_c2c.md) |
| C2C leaf + bridge | 4 leaves, 8 ports | 6 SerDes lanes per port: 150 GB/s raw, 116 GB/s payload per direction (frozen 100); rail-aligned (noc.md §6, [d2d_c2c.md](d2d_c2c.md)) |
| Command processor (CP) | 1 | RV64-class control core, behavioral in RTL; boots and launches cores, runs the runtime's page-table and n-gram-hash work |
| Host interface | 1 | PCIe-class, about 64 GB/s per direction, modeled |

Physical intent (for Phase 6): HBM PHYs on the two 33 mm edges (2 stacks each, about 22 mm of each edge), D2D on the short edge that faces
the sibling die, C2C SerDes on the outer short edge. The D2D link needs only about a quarter of its short edge at 1.4 TB/s
(8 UCIe-Advanced x64 modules, d2d_c2c.md L-2), which leaves room for a fifth HBM site on that edge; the 5-6 stacks/die question stays a Phase 6 floorplan item.

## 2. Core tile

```
                 IMEM 64 KiB ──► FRONT END (scalar unit, 32 x 64-bit regs, branches, semaphore waits)
                                    │ issue (operands captured from registers)
            ┌─────────┬─────────┬───┴─────┬─────────┬─────────┬─────────┐
           DQ0       DQ1       DQ2       DQ3        MQ        VQ     (16-entry in-order queues)
            └────┬────┴────┬────┴─────────┘          │         │
               DMA engine (512 reads in flight,      MXU      VPU + SFU + top-k unit
               convert, gather/scatter, paged)    64x64x4 WS     16 lanes x 16 FP32
                    │          │                     │ ▲         │ ▲
                    │          │                    ACC 512 KiB ─┘ │  (2048 rows x 64 FP32)
                    ▼          ▼                     ▲             │
               ┌──────── SPM 4 MiB: 32 banks x 128 KiB, 64 B wide, 1R1W ────────┐
               └────────────────────────────────────────────────────────────────┘
                    ▲
          NETWORK INTERFACE (2 planes; initiator for DMA, target for SPM / CORECTL; write-add ALU)
                    │
          ROUTER (N, S, E, W, core, GSRAM ports) ── GSRAM slice 1 MiB (its own target and write-add ALU)
```

### 2.1 Front end and queues
- In-order scalar unit, one instruction per cycle from IMEM (64 KiB = 4096 instructions; overlays by DMA into IMEM).
- Scalar instructions, branches and `SWAIT/SSET/SINC` execute in the front end. Tile instructions read their register operands at
  issue and enter one of six queues (DQ0-DQ3, MQ, VQ). The queued copy is self-contained, so later register writes do not affect it.
  - What is captured *(review finding I-11)*: the 128-bit instruction, every register operand of its format (8 for `T_DMA`, 6 for
    `T_MXU` and `T_VPU`, 2 for the other DMA formats) and the 32-bit wait value `R[wreg]`. A DQ entry is 128 + 8 x 64 + 32 = 672
    bits, an MQ / VQ entry 544 bits: about 7.5 KiB of queue storage per core.
  - Tile descriptors are not captured: engines read DTAB when an instruction starts (isa.md §4).
- Engines execute their queue in order. Cross-queue ordering comes only from semaphores (isa.md §3).
  - DMA queues may overlap consecutive transfers.
  - MQ and VQ instructions see the memory effects of earlier instructions in the same queue.
- Branches resolve in the front end; there is no prediction. The two-cycle bubble is irrelevant next to tile instructions that run
  for tens to thousands of cycles.

### 2.2 MXU: weight-stationary *(P2)*
- 64 x 64 PEs (`DIM` x `DIM` with `DIM` a parameter: 64 `FULL`, 16 `MID`, 8 `TINY`). Each PE holds 4 stationary FP8 values (K = 4) and computes a 4-way dot product plus the partial sum from the PE above.
  A stationary tile is K = 256 x N = 64 in `FULL` (16 KiB FP8; BF16: K = 128, each PE a 2-way BF16 dot product, A rows still one per cycle, so
  half the FP8 MAC rate; numerics.md N-3); in a reduced config K = `4·DIM` (FP8) / `2·DIM` (BF16) and N = `DIM`, which changes the
  accumulation order (numerics.md N-3, review finding S-2). The fold's column-scale tile stays 256 / 128 deep in every config (§4).
- A rows stream from the left (256 B/cycle, skewed). Partial sums flow down the columns and leave at the bottom: 64 FP32 per cycle into ACC.
- **Stationary load port 512 B/cycle, double-buffered (shadow weights).**
  - A tile loads in 32 cycles while the previous tile streams (the port limit). The **tile period at `M = 1` is longer**: with two
    shadow buffers the skew of tile `n` against the load of tile `n + 2` into the same buffer bounds it at **39.5 cycles** in
    `FULL` (**measured** at `DIM` 64, `M = 1`: 415 B/cycle; 21.5 at `DIM` 32, 13 at `DIM` 16, matching the bound
    `((DIM - 1) + DIM/8 + 8) / 2`; mxu.md §7, M-21, review finding S-5). With the SPM's 1R banks modelled, conflict-free pitches stay
    at 39.5-39.75; a pitch that makes the 8-row load beat collide (2.5 KiB) costs **43** (§2.3), not the 48 of the load alone,
    because the skew sets the period. A third buffer would restore 32 (mxu.md §9, not simulated).
  - Row-wise or column-wise load: a column load takes 256 contiguous bytes, i.e. one row of an HF `[out, in]` weight. So weights stay in
    their published layout (`trans_w = 1`), and attention can use K-cache tiles as `K^T` with no transpose pass.
- ACC: 2048 rows x 64 FP32 (512 KiB), read-modify-write at 256 B/cycle each way. A row is `DIM` FP32 in `TINY` / `MID` (256 / 512 rows
  of 8 / 16 FP32; review finding S-2).
  - Two FP32 multipliers per column at the ACC input: `rowscale` rescales the existing ACC row (online-softmax `alpha`), and
    `colscale` scales a K tile's partial sum (block-scaled weights, NVFP4 tensor scale).
  - The VPU reads ACC directly (4 rows per cycle).
  - Rules for the blocks that instantiate the arithmetic (`mxu_pe`, `acc_update`: flags, reset, unused lanes, PE alignment, ACC
    lane controls): arith.md "Integration contract", items 1-5.

Why weight-stationary and not output-stationary:

| | Weight-stationary (chosen) | Output-stationary |
|---|---|---|
| Decode (M = 1-30 tokens per expert) | MXU-load-bound: port 512 B/cycle, but the measured tile period of 39.5 cycles (§2.2) gives 415 B/cycle of weights: 2.15x the core's peak HBM share with 4-bit weights expanded to FP8 (193 elements/cycle; 2.5x at 0.85), **1.15x for the exact NVFP4 → BF16 path** (207 elements/cycle against a need of 181; 1.35x at 0.85; 1.05x / 1.24x at a colliding 2.5 KiB pitch; review finding S-5, mxu.md M-21). Before S-5 this row said 2.9x and 1.5x | Needs A and W both streaming at 256 B/cycle each; also M/64 utilization |
| SPM read bandwidth for a GEMM | A 256 B/cycle + W during loads only | A 256 + W 256 B/cycle every cycle |
| Long K | Accumulates in ACC (RMW), any K | Accumulates in PE registers; drains 16 KiB per output tile |
| Epilogue overlap | VPU reads one ACC region while the MXU fills another | Needs double-buffered PE accumulators (2 x 4096 FP32 registers) |
| Block-scaled weights | One FP32 multiply per column per K tile at the ACC input | Scale per PE or per K step inside the array |

Decode is HBM-bound on every model (arch.md §2), so the MXU's job in decode is only to keep up with weight bytes. Weight-stationary does that
at any M, with the margins above (the exact BF16 path is the thin one: the MXU, not the DMA, is its narrowest stage, numerics.md D1). It keeps the FP32 accumulators out of the PE array and makes the block-scale contract (DMA writes FP8 plus FP32 column scales;
isa.md §5) a cheap multiply at the ACC input.

### 2.3 Scratchpad (SPM)
- 4 MiB, 32 banks x 128 KiB, 64 B per bank access, one read and one write port per bank (1R1W): 2 KiB/cycle read plus 2 KiB/cycle write.
- Bank = `addr[10:6] ^ addr[15:11]`. Contiguous 2 KiB always covers all 32 banks. Power-of-two row pitches do **not** all spread
  (corrected, review finding I-13): over 64 rows the row starts reach 32, 16, 8, 4, 2 and 1 banks at 2, 4, 8, 16, 32 and 64 KiB.
  A stationary load of two 256 B rows per cycle therefore collides at 2, 4 and 64 KiB pitches (the shape the first rule was derived
  for). **The MXU's load beat is 8 rows x 64 B (trans and non-trans FP8) or 4 rows x 128 B (non-trans BF16)** (mxu.md §4.3), and
  the first rule is not conflict-free for it (review finding S-4): at the 2 KiB + 512 B pitch half of the beats collide, 48 cycles
  per tile instead of 32 (load alone; the measured MXU tile period at `M = 1` only rises from 39.5 to 43, mxu.md M-21). **Rule now (mxu.md M-17):** pad a 2 KiB pitch by **1 KiB (3 KiB)**, 4 KiB and 8 KiB by 512 B, 16 KiB by
  1 KiB, and 32 KiB or more by 512 B. That is conflict-free for all three beat shapes at every 2 KiB-aligned tile base
  (`scripts/mxu_review/pitch_scan.py`, `test_isa.py::test_mxu_load_beat_pitches`). Stationary-load tiles with a padded pitch start on
  a 2 KiB boundary (review finding V-8): at 256 B-aligned bases the BF16 non-transposed beat at 4 KiB + 512 B can cost 40 cycles.
  The other pitches (not powers of two) are mostly not conflict-free: only 26 of the 256 pitches from 64 B to 16 KiB in 64 B steps
  are, so the compiler keeps a pitch table. A collision is a stall of the beat (the SPM shim of §5), never a wrong result.
  - MXU A rows (4 lanes, 256 B in `FULL`) are conflict-free for 256 B-aligned A bases and `s1`; at 64 B alignment 6 of 1,024 starts
    cost one stall cycle (review finding S-4).
  - Not changed: folding `addr[20:16]` into the hash (the review's option) still collides at 2 and 4 KiB, and a hash that spreads
    every row bit over the upper bank bits fixes powers of two but loses on common non-power-of-two pitches (6 and 7 KiB).
- Peak read clients:

| Client | Read (B/cycle) |
|---|---|
| MXU A stream | 256 |
| MXU stationary load | 512 |
| MXU scales (`scale_rd`: rowscale, colscale) | 64 |
| VPU (two BF16 sources at 256 elements per cycle) | 1,024 |
| DMA out (also staged scales and raw units for the converter) | 256 |
| NI target (remote reads, and the read half of write-add) | 128 |
| **Sum** | **2,240** |

  The sum is 9% above the 2,048 B/cycle port capacity (corrected, review finding I-13: the NI target and MXU scale reads were left
  out). All clients never peak together: the stationary load runs 32 cycles per tile, and the VPU's two-source peak is an
  epilogue. Arbitration is per bank, so an overload costs stall cycles, not correctness. Writes peak at 1,152 B/cycle: VPU 512,
  DMA in 512 (review finding I-3) and the NI target 128.
  Arbitration is round-robin per bank with an age bound. The compiler places tiles to avoid systematic conflicts.
- If the ASAP7 fakeram flow cannot make 1R1W macros, the fallback is 64 single-port banks of 64 KiB (Phase 6 check).

### 2.4 VPU, SFU, top-k
- 16 lanes x 16 FP32 elements. 256 FMA/cycle = 512 FLOP/cycle (frozen). 32 KiB of internal vector registers for microcode temporaries.
- Tile instructions stream operands from SPM/ACC through the lanes. Fused instructions are microcoded sequences of lane operations:
  RMSNorm, RoPE, online softmax, quantize, router, hyper-connection ops, triangular inverse.
  The ISA fixes their semantics, not their microcode.
- SFU: 128 results per cycle (8 per lane) for exp, exp2, log, reciprocal, rsqrt, sqrt. sigmoid, SiLU, softplus, tanh and GELU are
  short compositions.
- Top-k unit:
  - It accepts 256 scores per cycle against a running per-row threshold (the current k-th value).
  - Survivors enter a 64-wide bitonic merge into the per-row state (k ≤ 2048, the largest any target model uses; larger values of
    the 12-bit `k` field fault `ILLEGAL_FIELD`; review finding I-15).
  - Ties go to the lower index, so results are bit-exact against the golden model.

### 2.5 DMA
- Four queues share one engine, which serves them independently *(review finding V-3)*:
  - Each DQ has its own sequencer: the instruction or `DMA_LIST` entry at its head, the semaphore wait of that head, and the
    address-generator state of its started transfers.
  - Shared resources are the tags and granule credits, the pace count, the staging ring (below), the SPM ports and NI
    injection. They are taken per read or write packet, round-robin over the DQs that have a packet ready, and only after the
    head's wait is satisfied.
  - **Credit-fair turns *(review findings Y-3, X-8, X-11; rule chosen by the user, 2026-11-01)*.** A read is ready when
    its own conditions hold: a data read's scales are in the ring, a scale read's ring space can be taken (next bullet).
    Tags, granule credits and the pace (below) are not part of readiness. The round-robin winner keeps its turn until it
    has taken 8 granule credits (one full request's worth). A request is never shrunk to fit. The turn passes early when
    the DQ has nothing ready.
    - **Held for credits.** While the winner's next request waits for credits, no other DQ's request overtakes it.
    - **Held for the pace against converting DQs only *(X-8)*.** While its next request has its credits and waits only
      for the pace, a DQ whose next request is plain (a plain read or a write packet) may issue it meanwhile, outside the
      turn; converting reads of other DQs still wait. Plain reads are limited by tags only (pace, below), and a hold
      against them would give a plain DQ one request per paced request of the holder.
    - **Write packets *(X-11)*.** A write packet (at most 256 B, below) takes no credit but counts one toward the 8 of a
      turn, so a DQ that wins with writes passes the turn after 8 packets (2 KiB). The turn is never held for write burst
      ids, which acks free. Read literally, "until it has taken 8 credits" let a 1 MiB SPM-to-SPM write (a MoE dispatch,
      4,096 packets) keep the turn until its last packet, and a weight prefetch in another DQ issued nothing for 4,096
      cycles (`isa.staging_ring`, `write_turn=False`).
    - Tags, credits and pace are freed by responses, which sink unconditionally, so a held turn always ends.
  - **Ring turns *(review findings Y-3, X-7, X-10)*.** The issue turn is never held for ring space, because ring space is
    freed by data reads that such a hold could block. Instead, ring allocation takes turns:
    - A DQ that cannot start its next entry (it does not fit, or another DQ is waiting) joins a queue, and only the DQ at
      its head allocates. It keeps that turn for **8 data granules** of the entries it allocates (one data request, of
      any format; X-10). A DQ that allocates while no DQ waits takes no turn.
    - **The turn passes early whenever its holder cannot start an entry for a reason other than ring space, credits, pace
      or the issue turn *(X-7)*:** its transfers have no entry left and its next instruction or `DMA_LIST` entry is not
      at the head, waits on a semaphore or is a `SIGNAL` waiting for its release, or its next read is plain or a write.
      A queued DQ that becomes idle this way leaves the queue. The holder does wait for credits, pace and the issue turn,
      which responses free or which rotate.
    - Meanwhile every DQ still issues the data reads of its allocated entries, and those need only tags (below), so the
      ring drains and the head's entry fits.
    - Why the pass on idle: without it a DQ that waits on a semaphore inside its turn keeps it, and nothing else
      allocates. 2 KiB ring, L = 500, DQ0 streams NVFP4 weights, DQ1 gathers 37 KV rows, waits on a flag, then gathers
      37 more, DQ2 gathers 200 KV rows and sets the flag when they complete: DQ1 takes the turn, allocates 160 B, waits,
      and the core hangs from cycle 3,649 with the ring empty (the fifth verifier's own model: 3,628). Random four-DQ
      programs with cross-DQ waits, each deadlock-free by itself (`isa.staging_ring` with semaphores, 400 programs, 2-16
      KiB rings, L = 50-845, 8-512 credits): 182 deadlock with the turn kept for its quantum, 59 with a pass only at the
      end of the DQ's stream (the reference before X-7), 0 with the pass on idle; with write transfers added, 169 / 46 /
      0.
    - Why granules: 256 B of ring is one NVFP4 entry but two MXFP4 or four MXFP8 entries, each one data request, so a
      byte quantum split two converting DQs of different formats by their scale bytes (NVFP4 + MXFP8 weights 17 / 66
      B/cycle). In granules each turn is one data request.
  - Why both: per request or per entry, round-robin hands out shared space in proportion to the request or entry size.
    Data bytes per cycle at L = 845 with `DMA_PACE` (`isa.staging_ring`, `hbm="server"`, per DQ from the latest DQ's 5%
    mark to the earliest DQ's 95% mark of its own data):

    | arbitration | KV gather + plain | two KV gathers + plain | NVFP4 weights + KV gather | NVFP4 + MXFP8 weights | NVFP4 + MXFP8 + plain |
    |---|---|---|---|---|---|
    | ready includes free credits (`arb="greedy"`) | 73 / **5** | 37 / 37 / **2** | 37 / 37 | 40 / 40 | 28 / 28 / 28 |
    | turn held for one request (`"hold"`, the fourth verifier's proposal) | **1** / 85 | **5** / **5** / 76 | 38 / 37 | 40 / 40 | 21 / 21 / 42 |
    | shrink to the free credits (`"shrink"`, the model before Y-3) | 28 / 56 | 20 / 20 / 41 | 37 / 37 | 40 / 40 | 26 / 27 / 30 |
    | **credit-fair turns + ring turns (the rule, `"fair"`)** | 28 / 56 | 19 / 19 / 40 | 37 / 37 | 40 / 40 | 28 / 28 / 28 |
    | the rule without ring turns (`ring_turn=False`) | 28 / 56 | 19 / 19 / 41 | **2** / 74 | **2** / 82 | **6** / 39 / 40 |
    | the rule with a 256 B ring quantum (`ring_q="bytes"`, before X-10) | 28 / 56 | 19 / 19 / 40 | 37 / 37 | **17** / 66 | **9** / 38 / 38 |

    - "greedy": a DQ of one-granule requests (a KV gather: a 32 B scale read and a 256 B row) takes each credit as it is
      freed, and a 2 KiB read in another DQ, which needs 8 at once, waits (the fourth verifier's model: 1-4 B/cycle).
    - "hold": round-robin per request gives a 2 KiB read 8 times the bytes of a one-granule read, so it starves the gather.
    - Without ring turns, the small entries of one DQ (32 B KV entries, 64 B MXFP8 entries) take freed ring space before
      a 256 B NVFP4 entry fits.
    - Under the rule, credits are shared evenly. The KV gather gets less in bytes (28 B/cycle of data plus 3 of scales),
      because half its credits carry 32 B scale reads. At L = 500 the rule gives 28 / 56, 20 / 20 / 42, 39 / 38, 40 / 40
      and 28 / 28 / 28. NVFP4 weights + plain share 41 / 41.
    - Shrinking gives similar shares, but it splits reads into more, smaller requests, so it was not taken. Cost of the rule:
      a credit count per turn, a granule count per ring turn and a 4-entry allocation queue.
    - **When the pace binds *(X-8)*.** `DMA_PACE` 16 KiB at L = 845, data B/cycle: NVFP4 weights + plain 2 KiB reads 10
      / 76 under the rule, 17 / 17 with the turn held for the pace against every DQ (the text before X-8; the fifth
      verifier's own model 17 / 17), 9 / 76 under "greedy"; KV gather + plain 11 / 75, 17 / 34 and 11 / 75. At the
      default `DMA_PACE` the two holds give identical shares.
      - **The trade *(review N-2)*.** The paced stream alone gets about 17 B/cycle (NVFP4 weights 16.6, KV gather 17.0;
        12 at L = 1,200). Next to the plain DQ it drops to about 10 (9.6 / 10.7), because the plain DQ's queue lengthens
        its round trip, while the two together go from 17 + 17 to 86. The pace is a deliberate throttle, and the plain
        DQ is limited by tags only. A cap on the plain DQ's bytes in flight only moves bytes between the two (the total
        stays 85-86, and the paced stream can never exceed its rate alone) and costs the plain DQ more than it gives at
        long round trips (the sixth verifier's model: a 48 KiB cap gives 11.7 / 40.2 at L = 1,200), so there is none. The reference before X-8 showed a weaker coupling (14 / 38)
      because a plain DQ starting a plain read took the ring turn with 0 bytes and kept the converting DQ from reading
      scales ahead; plain reads take no ring turn.
  - So a DQ whose head waits on a semaphore holds nothing another DQ needs (with the ring turn's pass on idle, X-7). A
    started transfer holds its tags until its responses sink, and its ring space until its data has been converted
    (review finding W-6). Neither waits on another DQ: every ring entry, once allocated, needs only tags to complete
    (below). d2d_c2c.md L-7 issue rule 2 relies on this.
  - **Liveness, as Phase 3 proves it *(X-7)*.** Under an environment that returns every granule within R cycles:
    - (P1) a ring-turn holder, or the head of the allocation queue, that cannot start an entry for one of the listed
      reasons (its transfers have no entry left and its next instruction or `DMA_LIST` entry is not at the head, a
      semaphore wait, a `SIGNAL` waiting for its release, or a plain read or write next) releases the turn (or
      leaves the queue) the next cycle. Phase 3 proves it on that list, the predicates the RTL has, not on "any reason
      other than ring space, credits, pace or the issue turn" *(review N-1)*;
    - (P2) the issue turn is held only while the holder's next request lacks credits or pace, and while it lacks only
      pace, a ready plain request of another DQ whose own resources are free (a tag and its credits for a plain read, a
      burst id for a write packet) issues, one request per cycle *(N-1: "still issues" was false for a plain read that
      lacks credits)*;
    - (P3) a DQ with a ready read, or a write packet, issues within a bound in R, the turn quantum and the number of DQs;
    - (P4) a DQ at the head of the allocation queue allocates, or leaves the queue, within a bound in R, the ring size
      and the quanta *(N-1)*. P1, P3 and the W-1 property imply it, but the X-7 hang was exactly a head that never
      allocated, so Phase 3 proves it directly.
    - The argument: a held issue turn waits only for credits or pace, which responses free; a ring-queue head waits only
      for space, which allocated entries free, since their reads need only credits, pace and issue turns, which rotate;
      an empty ring takes any entry ≤ the ring (the allocator restarts at 0). For P4: while a head waits, no other DQ
      allocates, so the entries in the ring are a fixed set that P3 drains within its bound per data request plus R and
      the conversion, and then the head's entry fits; a head that becomes idle leaves the queue by P1.
- **Tags and granule credits *(review finding Y-3; option chosen by the user, 2026-11-01)*.** A read request takes one **tag**
  and one **granule credit** per 256 B granule it touches, which is one per `RD_RSP` it receives (noc.md §2). There are 512
  of each, so the DMA keeps up to 512 granules (128 KiB) in flight, about 2x the core's HBM share (102 B/cycle) times a typical
  500-cycle loaded round trip.
  - A tag is an entry of the tag table, named by the 9-bit `tag` of the `RD_REQ` and of each of its `RD_RSP`s, whose `gidx`
    names the granule. Any free entry serves, so allocation does not fragment. Every request takes at least one credit, so
    the table never runs out before the credits.
  - A credit is freed when its `RD_RSP` sinks, and the tag when the request's last one has. An `ERR` other than `LINK`, or the
    request's timeout, frees the tag and its remaining credits (isa.md §8).
  - A scale read is a read request like any other: a KV row's 32 B scale read takes one tag and one credit, a 2 KiB scale
    read one tag and 8 credits.
  - Rejected: consecutive granule tags (a request takes k consecutive tags). Allocation would need k contiguous free tags and
    would fragment like the ring.
  - **Converting reads are paced *(review findings Z-2, Y-2, Y-6, X-14; options chosen by the user, 2026-11-01)*.** At most
    min(`DMA_PACE`, 4.5 x the `DMA_STAGE` size) **weighted bytes** of converting reads are in flight (CSR `DMA_PACE`, reset
    72 KiB; 4.5 x the 16 KiB default ring is also 72 KiB). Converting reads are the scale and data reads of block-format
    transfers, counted at issue and returned by their responses.
    - NVFP4 reads count their bytes, MXFP4 reads half and MXFP8 reads a quarter: a shift by the transfer's format, so the
      count follows the scale bytes in flight (32, 16 and 8 B per 256 B granule).
    - **By entry shape *(X-14; taken by the user, 2026-11-01)*:** the reads of an entry whose data is one granule (a row
      gather: a KV row, an Engram row) count their bytes in full, whatever the format. The DMA knows the shape when it
      forms the entry, so the shift select has one more term.
    - Hardware: an 18-bit counter (512 granules of NVFP4 reads weigh 2^17 B), a shift-add for the cap, a min and a compare.
    - A converting read may always issue when no converting read is in flight, so any nonzero limit makes progress, also
      one below a single request (review finding Y-6; the literal "at most `DMA_PACE` bytes" hangs at 1 KiB on the first 2
      KiB read). 0 turns the limit off, the cap included.
    - Plain reads are limited by tags only. Reads beyond the share times the round trip add nothing to the delivered rate;
      they only wait in the HBM queue, where a converting stream's scale reads would wait behind its own data and every ring
      entry and tag would be held longer (staging ring, below).
    - Why by format: under a flat 72 KiB the MX streams, which the ring covers far beyond 845 cycles, are bound by the
      limit alone (rate ≤ limit / L). MXFP4 weights then run at 136 elements/cycle and MXFP8 at 69 at L = 1,000, against
      164 / 84 with tags only. Counted by format, both keep the full rate to 1,200 cycles and NVFP4 is unchanged. NVFP4 and
      MXFP4 in two DQs at L = 1,000 deliver 80 B/cycle together instead of 66.
    - Why the cap: the ring covers a stream's scales for ring / (2 x scale bytes per cycle) cycles. The share times that is
      4.5 x the ring for NVFP4 (32 B of scales per 288 B), whatever the share. So a runtime that shrinks the ring needs no
      new `DMA_PACE`. At 8 KiB the cap (36 KiB) gives the KV gather 130 elements/cycle at L = 500, against 104 with 72 KiB.
    - Why the CSR stays (rejected: the limit derived from `DMA_STAGE` alone, the fourth verifier's proposal): the KV gather
      is bound by its tags (two per row), not by the ring, and its best limit is about 72 KiB at any ring size. A 24 or 32
      KiB ring with a derived 108 or 144 KiB limit drops it from 154 to 123 / 110 at 845 cycles.
    - Why by entry shape: for an entry of many granules the pace bounds the scale bytes in the ring, which the format
      weight tracks. For a one-granule entry it bounds the HBM queue, and a row gather is tag-bound (two tags per row):
      a longer queue holds every tag longer. Counted by format, a per-row MXFP4 gather (16 B scale read + 256 B row) ran
      at 110 elements/cycle at L = 845 against 154 counted in full, and the Engram MXFP8 rows (8 B scale read + 256 B row,
      hyper_connections.md §4) at 0.22 rows/cycle against 0.30 (the fifth verifier's own model: 110 / 151 and 0.218 /
      0.291). By entry shape both get the flat rates, the MX weight streams keep 164 / 84 to 1,200 cycles, and NVFP4 and
      the NVFP4 KV gather are unchanged (`test_staging_ring_pace_hold_and_formats`). No surveyed model has an MX row gather
      on a critical path (Engram is hidden, G-1); this keeps one from losing about a third.
- Read requests cover up to 2 KiB (one HBM leaf or one SRAM target) and return one response packet per 256 B granule: 2 flits,
  or 1 flit when the request's bytes in the granule lie in one 128 B half (review finding X-9, noc.md §2). Each response carries a
  tag, a granule index and the request's epoch, and its destination SPM address is held in the tag table. Response data therefore
  needs no staging buffer: the SPM space is reserved when the request is issued, which is what keeps the response network
  deadlock-free (noc.md §4).
  - **One destination run per request *(review finding X-12)*.** The DMA cuts a read request where its SPM destination run
    ends (for a converting read, the run of converted output), as it cuts write packets, and at the 2 KiB block: two
    adjacent 512 B HBM rows read into SPM rows padded to 576 B are two requests (`isa.read_requests`).
  - **Tag-table entry *(review findings Y-5, X-12, U-1, N-4)*:** the SPM destination of the request's first byte, the byte
    offset of that byte in its granule, `len`, the granules still outstanding (an 8-bit mask), the DQ, the epoch, and for a
    converting read its format and entry shape (pace weight) and the ring offset of its first unit's scales. Byte 128 of an
    NVFP4 granule is element 256, so the second half of a cut granule converts from element 256 into its own entry. In v1
    no conversion unit is split (below, U-1 and U-2), so a converting read starts on a unit of its own entry and needs no
    unit size, unit offset or raw-slot index. About 70 bits (22-bit SPM destination, 8-bit granule offset, 11-bit `len`,
    8-bit mask, 2-bit DQ, the epoch, 5 bits of format and shape, an 11-bit ring offset), 4.5 KiB for 512 tags.
    - **Largest ring *(N-4)*.** The ring offset counts 16 B units in 11 bits, so `DMA_STAGE` holds at most 32 KiB, and a
      block-format conversion with a larger `DMA_STAGE` faults `ILLEGAL_FIELD`, as one below 2 KiB does. That covers the
      24 and 32 KiB rings Phase 7 costs (below). `DMA_STAGE`'s size field ([53:32]) is wider; the bound is the tag table's.
    - A later version with raw slots (split units, inline rows) adds the X-12 fields: the ring offset of the entry's raw
      slots, the unit size, the first byte's offset in its unit and that unit's raw-slot index (about 95-100 bits).
  - Response `gidx` g lands at the destination + (g x 256 - the first byte's offset), except g = 0, which lands at the
    destination. Its valid bytes are the request's range within the granule (noc.md §2, partial granules;
    `isa.rd_rsp_layout`).
  - **A converting response in v1 *(review findings U-1, U-2)*.** No v1 conversion unit spans two granules, so every
    response converts from its own valid range. The units of a fold read are its rows' 256-element tiles, T bytes each
    from the request's first byte (T = 128 B at 4 bits, 256 B for MXFP8); only the last may be short, and it ends at the
    request's end. That holds because a fold-converted source starts every dim-0 run on a tile (below), so a request
    that spans rows has whole tiles only (s1 = the row's bytes), and a row with a short last tile has a gap after it, so
    its request ends there. Other conversions have elements of at most 1 B.
  - **Pieces of a converting response *(X-12; a later version with raw slots, not in the v1 `core/ni`)*.** The NI cuts
    the valid range at conversion-unit boundaries. A unit that lies inside the granule converts from this response, at
    its element offset. A piece of a split unit is written to the unit's raw slot, at the raw-slot base + slot x the
    slot pitch (the unit size in 16 B units) + its offset in the unit; slot is the number of the entry's split units
    before it. It is the request's first unit's raw-slot index (the entry's split units before that unit, whether the
    first unit is split or not) plus the split units from there to the piece's unit. For power-of-two units of at most
    256 B, every granule boundary falls at the same offset in a unit, so either every boundary inside the request splits a
    unit or none does, and the split units from the first unit to the piece's unit are the granule boundaries from the
    first unit's start (the request's first byte minus its offset in its unit) up to the piece's unit start (review N-3;
    the boundaries the piece itself has crossed is a different count, wrong on every tail of a split unit). A granule of
    contiguous MXFP4 fold tiles from byte 64 therefore feeds two raw slots and the converter: the 64 B tail of one split
    tile (its raw slot), a whole tile, and the 64 B head of the next split tile (the next raw slot). One ring offset per
    tag, as the text before X-12 had it, could place only one of them. `isa.rd_rsp_layout(..., unit=)`,
    `test_rd_rsp_pieces_and_destination_runs`. A fold row that is not whole tiles gives a request units of two sizes,
    which one unit size per tag cannot describe (U-1); a version that allows it cuts a converting request where the
    unit size changes, raw slots at the full tile pitch.
- **Read data always sinks in local SPM** (or DTAB / IMEM). A DMA never forwards read data as a write, so one of its two operands is
  local (`TD_SPACE` otherwise; review finding I-1). Global-to-global moves are two transfers through an SPM buffer.
- **Inbound rate *(review findings I-3, V-1)*:** the converter produces 256 elements per cycle and the SPM write port takes
  512 B/cycle, shared by the converted data and the block scales staged before it. The exact NVFP4 → BF16 path (2 B of data and
  1/16 B of staged scale written per element) therefore carries 1.60x a core's HBM share at 0.85 (numerics.md D1). The network
  can deliver up to 256 B/cycle of compressed data, more than the converter takes, so `ni_rsp` has a ready signal and a 4-flit
  inbound buffer.
- **Every response converts from local state only *(review finding V-1)*.** A conversion needs the element bytes, their block
  scales and, for the fold, the whole 256-element K tile of the row (numerics.md N-2). The scales are separate responses, so if the
  converter waited for them a data granule at the head of `ni_rsp` would wait for a scale queued behind it in the same RSP channel.
  So the DMA never lets a response wait for another one:
  - **Scales first.** Out-of-line block scales (the `scale_reg` array, or the page's scale region) are read into the staging ring
    before the data that uses them. A data read is issued only after the scales of all its granules are in the ring. Scale
    responses are plain SPM writes. The DMA reads scales a round trip ahead, so the rate is unchanged; the first data of a
    transfer starts one round trip later (the second verifier's estimate: about 0.33 µs per dependent converting transfer,
    at most about 2% of a batch-1 DeepSeek or MiniMax decode step).
  - **No split units in v1 *(review findings U-1, U-2; (b) chosen by the user, 2026-11-02)*.** A conversion unit whose
    bytes would arrive in more than one response is illegal:
    - **Fold tiles.** A fold-converted source (`cvt` `MXFP4_E4M3`, `NVFP4_E4M3`, `MXFP8_E4M3`) has its base (a PAGED
      operand: the paged pointer's `byte`), `s1`, `s2` and `s3` as multiples of a tile's bytes T (128 B at 4 bits, 256 B
      for MXFP8), and n0 a multiple of 256 when n2 or n3 > 1, else `ILLEGAL_FIELD`. A gather's rows start at the base +
      index x `s1`, so the same fields cover them. Every 256-element tile, the row's short last tile included, then
      starts on a T boundary and has at most T bytes, so it lies in one granule, and a K slice starts on a tile. The rule
      is slightly stricter than "no tile crosses a granule" (128 MXFP4 elements from byte 64 would fit), as the W-5 block
      rule is, so the check is a few low-bit ORs. Before U-1 a row that is not whole tiles (K = 384, `s1` = 192) or a K
      slice off a tile was legal and split tiles; the response path of such a request had units of two sizes and no
      defined placement.
    - **2 and 4 B elements.** A converting source of 2 or 4 B elements (`BF16_FP32`, `FP32_BF16`) has its base, `s1`-`s3`
      and a gather's `row_bytes` as multiples of the element size, else `ILLEGAL_FIELD`. Without it a `BF16_FP32` copy
      from 0x…0FF has element 0 in two responses, and with no scale read there is no ring entry to stage it.
    - Other conversions have elements of at most 1 B (two 4-bit elements share a byte) and never split.
    - Reference: `isa.cvt_source_check`, tested in `test_isa.py` (`test_cvt_source_check_no_split_units`).
    - So the v1 `core/dma` has no raw slots and the v1 `core/ni` no response pieces. A later version may stage split
      units raw: a fold tile across a granule, or an inline-scaled row (`SCALE_INLINE`, reserved in v1, review finding
      X-16; a 264 B MXFP8 row of a repacked table: 256 B of elements, then their 8 B of scales; memmap.md §6). Its
      responses are written to a raw slot, and the converter takes the unit from SPM once it is complete; its raw bytes add
      to the DMA's SPM write and read traffic. The rules below for raw slots are that version's, and `isa.staging_ring`
      keeps them.
  - **Staging ring:** an SPM region given by CSR `DMA_STAGE` (base and size), reserved by the runtime and shared by the four DQs.
    Size 0 makes block-format conversion fault (`ILLEGAL_FIELD`).
  - **Ring allocation *(review findings W-1, Z-1)*.** The ring is allocated in **entries**. An entry is a run of whole
    conversion units: the scale reads of their out-of-line scales, the raw slots of its split units (a later version; v1 has
    none, U-1), and the data reads of those units. **An entry without raw slots covers the units of at most one data request** (at most 2 KiB inside one 2 KiB
    block; review finding Y-1, below). It has one scale read, or two when its scales cross a 2 KiB block of the scale array
    (a fold tile's 8 or 16 B of scales at an offset that is not a multiple of their size, as with K not a multiple of 256).
    - **Every data read and every conversion unit belongs to exactly one entry *(review finding Z-1)*.** An entry boundary is
      a unit boundary. Where it falls inside a granule, the DMA cuts the read there: that granule is two partial reads (a
      read request's `len` is in bytes, noc.md §2), one in each entry. So a data read waits only for its own entry's scales.
      - Why: if the granule were one read, issued once both entries' scales are in the ring, the older entry could complete
        only after the younger one is allocated. With several DQs the ring's oldest entry can be the only one of its DQ,
        with the ring full behind it, and in-order reclaim lets nothing past it. Four DQs that stream DeepSeek index-key
        rows (64 B) from row 2 of a page, with 1 KiB or 2 KiB scale reads (entries above the Y-1 size, which the rule must
        survive anyway), deadlock that way at cycle 1,716 (4 KiB ring) and
        5,977 (16 KiB); contiguous fold tiles from byte 64 at 1,037 and 1,292 (`isa.staging_ring`, `boundary="shared"`;
        the third verifier's own model had 1,715 / 5,976 and 1,051 / 1,298). One DQ cannot deadlock this way, because its
        next entry is the ring's next allocation.
      - Cost: one more request per entry boundary inside a granule, and at most one 32 B HBM burst read twice. The DMA ends
        an entry on a granule boundary when a unit boundary falls on one, so aligned streams have no cut. With Y-1 a
        contiguous non-split stream ends its entries on 2 KiB blocks, so in v1 cuts come from rows that share a granule
        (index-key rows, the second half of a cut NVFP4 granule); a later version's raw-slot entries (fold tiles from byte
        64, contiguous inline rows) add more. The fold-tile deadlock above is that version's case.
    - **An entry's scales and the raw slots of all its split units are one allocation, taken when its first scale read is
      issued** (in v1 an entry has no raw slots, U-1). In v1 every entry that uses the ring has a scale read (`SCALE_INLINE` is reserved, X-16; a later version's
      inline entries are allocated at their first data read). Its data reads then
      need only tags, and tags are freed by responses, which sink unconditionally. So every allocated entry completes, the
      oldest one frees its space, and the ring always drains.
    - **Inline entries *(review findings Z-3, X-16; a later version: not in the v1 `core/dma`)*.** An entry of `SCALE_INLINE`
      rows (memmap.md §6) has no scale read. It holds
      whole rows, each with all its reads: a 264 B inline row that crosses a 2 KiB block is two reads, both in the row's
      entry. Its raw slot holds the rows, 272 B per row in 16 B units.
    - The ring is circular, in 16 B units. An entry is contiguous (if it does not fit before the end it starts at the
      beginning) and is freed when all its data has been converted. Space comes back in allocation order.
    - **Ring and entry size *(review findings Z-3, Y-1, N-4)*.** `DMA_STAGE` must hold at least 2 KiB and at most 32 KiB
      (the tag table's ring offset, above), or a block-format conversion faults `ILLEGAL_FIELD` (size 0 included). Liveness needs only entry ≤ ring; the two size rules are rate
      rules, and the DMA meets them by shortening the run of units.
      - **An entry without raw slots covers at most one data request** (256 B of NVFP4 scales for 2 KiB of data, 128 B of
        MXFP4, 64 B of MXFP8). A non-split entry's scales stay in the ring from its scale read until its last element is
        converted: two round trips plus the time T_entry its own data takes to stream. So the ring must hold scale rate x
        (2 L + T_entry), which is scale rate x 2 L plus about one entry's scales, and large entries take ring space from the
        round trip. NVFP4 weights at L = 845 (`isa.staging_ring`, paced / with `DMA_PACE`): one data request per entry
        153 / 151 elements/cycle; 16 granules 151 / 149; 64 granules (one 2 KiB scale read) 141 / 137; a quarter of the
        ring (4 KiB of scales, 32 KiB of data) 124 / 123, as the formula predicts (124). The cost is one scale request per
        data request, about one per 27 cycles at the share.
      - **An entry with raw slots is at most a quarter of the ring** (a later version). Split units and inline rows hold
        their data in the ring, so those streams are ring-bound anyway (below); the quarter keeps one entry from taking the
        ring.
      - In v1 a unit lies inside one granule, so inside one request, and its entry needs at most one data request's scales
        (256 B). In a later version no single unit's entry is larger than 272 B (a split MXFP8 fold tile is 256 + 8 B, as
        is a 264 B inline row; a split 4-bit tile 128 + 8 or 16 B), which fits a quarter of the 2 KiB minimum. The DMA can
        therefore always meet both rules. A ring below the 16 KiB default lowers the rate
        (below), never liveness.
      - Replaced (Z-3): "the quarter is a rate target: four entries in flight let scales run a round trip ahead". For
        entries without raw slots it is wrong at 845 cycles (124 against 153 above).
    - **Order of reads *(review finding Z-8)*.** Within a DQ, the scale read of its next entry goes before its own data reads
      whenever that entry's space can be taken (greedy read-ahead). That is what keeps a converting stream at the HBM rate.
      Across DQs, scale and data reads alike take the shared resources per packet, round-robin (above), so one DQ's scale
      read does not overtake another DQ's data read; per-DQ order is all the read-ahead needs. A scale read takes one tag
      and one credit per granule it touches, as a data read of that size would (above).
    - Reference: `isa.staging_ring`, a cycle model of the rule. Entries can carry HBM byte ranges; their reads are then the
      granule pieces of the range, cut at entry boundaries and grouped into requests inside one 2 KiB block. A DQ's stream
      can also hold write transfers, semaphore waits, `SIGNAL`s and front-end gaps (review finding X-7). Its defaults are
      the rule:
      - entry sizes checked (`sizing="rate"`);
      - credit-fair turns (`arb="fair"`; `"hold"` is the rejected turn per request), held for the pace against
        converting DQs only (`pace_hold="converting"`), with a write packet counted as one credit (`write_turn=True`);
      - ring turns taken only when a DQ waits (`ring_take="contended"`), counted in data granules (`ring_q="granules"`)
        and passed on idle (`ring_pass="idle"`);
      - one response and one credit per granule (`rsp="granule"`);
      - the pace weighted by format and entry shape, with the cap (`pace="shape"`, `fmt` per DQ or `("fmt", ...)` per
        transfer).
      `test_isa.py` runs mixed split and non-split streams (the split ones for a later version; the v1 RTL is compared on
      non-split streams, U-1), one DQ or four, with greedy read-ahead, and random four-DQ
      programs with cross-DQ semaphore waits and writes; they always complete. The shared-granule reading deadlocks with
      four DQs, and the ring turn without its pass on idle deadlocks with a semaphore wait inside a turn. Phase 3 adds
      `core/dma` formal properties: every allocated ring entry needs only tags to complete, no data read waits for the
      scales of an entry other than its own, and the arbiter's P1-P4 (above).
    - Rejected: taking the raw slot at the unit's first data read (the text before W-1). Read-ahead fills the ring with
      scales whose data can never get a raw slot: the all-split fold stream below deadlocks at cycle 687 (`test_isa.py`).
    - Rejected: capping read-ahead so that the largest raw slot still fits. In a ring it deadlocks too, because a raw slot
      freed behind newer scale entries is reclaimed only in allocation order. It works only with a free-list allocator, and
      even then it carries 16.5 elements/cycle on the all-split fold stream against 27.6 for the atomic rule (one probe,
      not committed).
  - **What 16 KiB is sized for: scales.** A non-split unit's scales live from their read to the data's conversion, about two
    round trips. 16 KiB holds 1,024 cycles of NVFP4 scales at the converter's rate (16 B/cycle). At the HBM share (87 B/cycle,
    155 elements/cycle of NVFP4 → BF16) it keeps the full rate up to a round trip of about 845 cycles, with entries of one
    data request (`isa.staging_ring`: 154.7 at 500, 153.3 at 845, 129 at 1,000), and 8 KiB does not (about 75 at 845). A
    smaller ring only lowers the rate. MXFP4 and MXFP8 carry 16 and 8 B of scales per granule, so 16 KiB covers them to
    about 1,600 and 3,100 cycles.
    - **These rates need the pacing *(review finding Z-2)*.** They come from the paced model (`hbm="paced"`: every
      response exactly one round trip after its issue). With the share modelled as a queue (`hbm="server"`: requests reach
      a FIFO served at 87 B/cycle, one response per granule, so the core's own backlog adds to the round trip) and greedy
      issue up to 512 credits, the weight stream runs at 130 / 110 / 103 elements/cycle at 500 / 845 / 1,000 cycles, and the
      paged NVFP4 KV gather (one entry per row: a 32 B scale read, then the 256 B row) at 136 / 111 / 104. That is the third
      verifier's model (130 / 110 / 103 and 135 / 111); with one response per request (`rsp="request"`, the model before
      Y-3) the weights had 142 / 119 / 111. The backlog lengthens both round trips of every entry, so the weight stream
      becomes ring-bound. It also lengthens every tag's hold time, and the KV gather spends one tag per 32 B scale read, so
      it becomes tag-bound, and a larger ring does not help it.
    - **With the pace rule** (72 KiB, about the share times the 845 cycles the ring covers for NVFP4, counted by format,
      capped at 4.5 x the ring; above) the queue model gives the paced rates back. Elements/cycle at L = 500 / 700 / 845 /
      1,000 / 1,200 (`isa.staging_ring`, `hbm="server"`; weights in entries of one 2 KiB request; "all" means the full rate
      at every L). These are **single-FIFO results for one core** (review finding Y-4): the KV-gather column in particular
      is optimistic with real per-PC queues and other cores' traffic (below). On the mesh, the 8 cores of a stack on the
      KV gather load its busiest leaf link to 0.91 with 1-flit scale responses (1.21 with 2-flit ones; noc.md §5, review
      finding X-9). Phase 7 checks every column:

      | | NVFP4 → BF16 weights | MXFP4 weights | MXFP8 weights | paged NVFP4 KV gather |
      |---|---|---|---|---|
      | need (the share) | 155 | 164 | 84 | 155 |
      | paced bound (16 KiB) | 155 / 155 / 153 / 129 / 107 | all | all | 155 / 155 / 155 / 130 / 108 |
      | queue, tags only (16 KiB) | 130 / 118 / 110 / 103 / 96 | all | all | 136 / 118 / 111 / 104 / 96 |
      | **queue, the rule (16 KiB)** | 155 / 155 / 151 / 127 / 106 | all | all | 155 / 155 / 154 / 130 / 108 |
      | queue, flat 72 KiB (the Z-2 rule) | 155 / 155 / 151 / 127 / 106 | 164 / 164 / 160 / 136 / 114 | 84 / 84 / 82 / 69 / 57 | 155 / 155 / 154 / 130 / 108 |
      | queue, 24 KiB, tags only | 155 / 155 / 155 / 155 / 144 | all | all | 136 / 118 / 110 / 104 / 96 |
      | queue, 24 KiB + the rule | 155 / 155 / 151 / 128 / 106 | all | all | 155 / 155 / 154 / 130 / 108 |
      | queue, 32 KiB + `DMA_PACE` 80 KiB | 155 / 155 / 155 / 142 / 119 | all | all | 155 / 155 / 146 / 130 / 108 |
      | queue, 8 KiB + the rule (cap 36 KiB) | 125 / 90 / 75 / 64 / 53 | 164 / 164 / 151 / 127 / 106 | all | 130 / 93 / 77 / 65 / 55 |
      | queue, 8 KiB + flat 72 KiB | 103 / 89 / 75 / 64 / 53 | 164 / 164 / 151 / 127 / 106 | 84 / 84 / 82 / 69 / 57 | 104 / 89 / 76 / 64 / 54 |

      Above about 845 cycles the ring (NVFP4 weights) and the tags (KV gather) bind in every model; the MX weight streams
      are bound by neither before 1,200 cycles. The limit is fairly sharp: 64 KiB gives 134 / 137 at 845. A larger ring
      helps the weights only with a larger `DMA_PACE` (24 KiB + 80 KiB: 155 at 845), at the KV gather's cost (146), so the
      default stays 72 KiB and the cap covers smaller rings.
    - Options costed for Z-2: the limit (taken: a counter, a compare and a CSR; the ring stays 16 KiB); a larger ring alone
      (8-16 KiB more SPM reserved per core, no hardware, but the KV gather stays 29% short at 845 cycles); both (no better
      for the KV gather, and 24 KiB gains the weights only 2-3 elements/cycle). Rejected: limiting all reads, which would
      cut a plain stream to 72 B/cycle at a 1,000-cycle round trip; a tag cap, which the third verifier found helps the
      weight stream and hurts the KV gather.
    - **Per-PC queues and other cores *(review finding Y-4)*.** The queue model serves one core's requests in issue order
      at its share. Real HBM has 32 per-PC queues per stack, shared by 8 cores. Two effects follow, and neither model below
      has bank timing, FR-FCFS, refresh or the mesh, so Phase 7 decides (PLAN):
      - Per-PC latency varies, and in-order reclaim turns the tail into ring occupancy, so the paged KV gather becomes
        ring-bound. With 8 cores of one stack on it at 72 KiB (32 per-PC FIFOs of 21.75 B/cycle): the fourth verifier's
        model 144 / 132 elements/cycle at L = 500 / 845 (slowest core 143 / 131), a scratch model of this session 146 / 140,
        against 155 / 154 for one core alone. The NVFP4 weights: 155 / 146 and 149 / 143. Tags only: 126 / 107 and 128 / 109.
      - A per-PC FIFO shares bandwidth in proportion to the bytes queued. A converting core's queued bytes are capped by its
        ring and its pace; a plain core's only by its 128 KiB of credits. hbm.md H-3 is FR-FCFS and oldest first, with no
        fairness per initiator, and noc.md §7 has no QoS in v1. One converting core among 7 cores streaming plain reads
        gets about 50 B/cycle (weights) or 44-49 (KV gather), each plain core 90-93, paced or not.
      - Options for Phase 7 to cost, not taken here:
        - a 24 KiB ring for KV-heavy kernels (8 KiB more SPM reserved per core, no hardware): 8 cores on the KV gather
          152 / 141 in the verifier's model, 146 / 142 in this session's;
        - fairness per initiator in the HBM scheduler. Per-PC round-robin over initiators gives the weight core 85 / 74
          B/cycle at L = 500 / 845, but the KV core drops to 41 / 38: its 32 B scale reads count as whole turns. So the
          fairness would have to be weighted by bytes (deficit round-robin), a change to the H-3 scheduler.
      - The weights hold within about 3-5% of the table, and the mixed-traffic loss does not depend on `DMA_PACE`.
    - Split units hold about 17 times more space per element (a 128 B MXFP4 tile and its 8 B of scales). If every fold tile
      of a stream were split, the ring would carry about 28 elements/cycle, against a need of about 164.
    - **So the compiler never splits fold tiles, and in v1 a split one faults *(U-1 (b))*:** the compiler pads the HBM
      row pitch of fold-converted weights to a whole number of 256-element tiles (128 B at 4 bits, 256 B for MXFP8),
      places them on a 256 B boundary, and cuts K slices on tile boundaries. Padding is in the pitch only; the TD's n0
      still covers the real K, and the last tile of a row is short but inside one granule (its request ends with it, since
      the padding is a gap). Every surveyed model already has K a multiple of 256 (expert K 2,048-6,144 and 2,304), so it
      costs nothing there. A layout that is not padded faults `ILLEGAL_FIELD` under a fold `cvt` (the rule above). It can
      be repacked, or converted with the exact `*_BF16` path, which has no tiles and accepts it (2 B per element in SPM
      and the BF16 MXU rate). A later version with raw slots could convert it at the ring-limited rate.
    - **Engram rows *(review finding Y-7; layout chosen by the user, 2026-11-01)*.** The table keeps its published separate
      scale array, so a row is one aligned 256 B granule plus an 8 B scale read: not split, one ring entry of 16 B. The
      gather is tag-bound like the KV gather: about 0.33 rows/cycle per core at 500 cycles and 0.30 at 845, with its reads
      paced in full as one-granule entries (X-14; 0.27 / 0.22 counted by format). A DeepSeek
      batch-256 decode step gathers 12,288 rows (24 per token in each of 2 layers); even if one die's 32 cores gather all
      of them, that takes about 1 µs, issued at the start of the step and hidden (hyper_connections.md G-1). A remote
      row's longer round trip lowers the rate in proportion, still hidden. Rows staged through the sibling arrive with
      their scale rows unconverted and convert SPM to SPM, with a short round trip.
    - Inline-scaled rows (a repacked table; reserved in v1, X-16) stay split, because a 264 B row always spans two granules. The ring carries
      about 62 of them per round trip, 0.12 rows/cycle per core at 500 cycles.
  - Ready therefore depends only on the converter and the local SPM ports, which drain at a fixed rate without waiting for the
    network. A response is still guaranteed to sink (noc.md §4 rule 2); it may wait a bounded time in its RSP virtual channel.
  - Not taken: choosing the fold's power of two from the block scales alone. That would make every granule convertible on its
    own and remove the fold buffer, but it changes the fold's numerics (numerics.md N-2), so it is left as a Phase 3 option.
- Addressing:
  - 4-D strided addressing from tile descriptors.
  - Paged mode, with a page table in SPM (32-bit PTE, 4 KiB frames).
  - Row gather and scatter with arbitrary row size (Engram 264 B rows) and int32/int64 index lists.
  - Descriptor lists (`DMA_LIST`).
- Conversion on the inbound path: MXFP4/NVFP4/MXFP8 to E4M3 plus FP32 column scales, or to BF16 exactly; E4M3/E5M2 to BF16;
  BF16 to and from FP32. Outbound data is written as is.
  - Scales come from the operand's scale array (`scale_reg`) or, for paged KV, from the scale region of the same page. An
    element's scale is found from its row and its whole in-row offset (dims 0, 2 and 3; review finding V-4). The NVFP4 tensor
    scale comes from `ts_reg` (isa.md §5, memmap.md §6).
  - `cvt = NONE` moves only the bytes the TDs address, never the scales.
  - Fold padding, the NVFP4 column-scale multiply (an `fp32_mul` on `fold_c.cs` and `R[ts_reg]`) and the flags of conversions:
    arith.md "Integration contract", items 1, 6 and 8.
  - Quantization (KV, activations) is a VPU instruction (`VQUANT`), because it needs row maxima.
- **Write packets *(review finding V-5)*:** a packet holds contiguous destination bytes. Adjacent runs are merged, including
  consecutive paged rows of one page, up to a 256 B granule boundary. `HBM_ALIGN` is checked per packet. So eight 4 B scale rows
  that form an aligned 32 B block are one 32 B write (memmap.md §6 worked example).
- Write operations at the destination: plain write, FP32/BF16 add, FP32 max. SRAM targets have the ALU in their network interface; HBM
  takes plain writes only.

### 2.6 Network interface and semaphores
- The interface is an initiator for the DMA and a target for SPM and CORECTL (IMEM, DTAB, semaphores, CSRs).
  - The tile's GSRAM slice is **not** behind the interface. It is a target of its own on the router's sixth port (§3), so
    GSRAM traffic uses neither the interface nor the router's core port.
  - The slice implements the same SRAM-target rules as the SPM target below: write-add/max with its own ALU, ack
    coalescing, the epoch copy, and `rsem` to the owning tile's semaphores. In RTL both are one SRAM-target module,
    instantiated in `core/ni` (SPM) and in `die/gsram`.
- Each SRAM target (SPM here, GSRAM in its slice) handles: reads, writes, write-add/max, and byte-counting semaphore adds attached to writes (`rsem`).
  - How the ALU composes `ADD_F32`, `ADD_BF16` and `MAX_F32` (DAZ operands, FTZ results), and whose `FP_STATUS` its flags go to
    (the target core's): arith.md "Integration contract", items 1 and 7.
  - A read response is 1 flit when the request's bytes in its granule lie in one 128 B half, else 2 (noc.md §2, review
    finding X-9); the target picks the half from the request.
  - Every response and ack copies the request's `epoch` (review finding X-13).
- As an initiator, the interface drops a response, ack or `ERR` (`LINK`) whose `epoch` differs from `NI_EPOCH` (d2d_c2c.md
  L-4 (f)), and places a 1-flit response's half from the tag table's range.
- Plane choice is per packet: round-robin over planes with credits. No ordering is needed (noc.md §7).
- Write acknowledgements carry a byte count and are coalesced per 2 KiB burst (noc.md §2, hbm.md H-2):
  - every `WR` carries its burst's total bytes (`blen`), and the target acks when it has accepted them all. A burst stays inside
    one 2 KiB-aligned block, so it has one target (review finding V-10). The coalescing entry is per (initiator, burst id,
    epoch) (X-13);
  - a full table acks its oldest entry early with a partial count, so accepting a write never waits for ack state (review finding R-3);
  - an entry idle for `ACK_FLUSH` cycles (CSR, default 64) is acked with its partial count, which completes bursts whose entry was
    evicted (review finding X-3);
  - an SRAM target acks only after the write, including a write-add, is visible to a later read.
- 64 semaphores (32-bit) per core, readable and writable over the network (memmap.md §5).

## 3. Die-level blocks

- **GSRAM:** 32 slices of 1 MiB, one per core tile, on the router's sixth port.
  - Software-managed and slice-contiguous: no hardware interleave.
  - Uses: collective staging, KV and expert prefetch, broadcast sources.
  - Each slice is its own network target, on the router's sixth port, with the same functions as the SPM target in the
    network interface (§2.6): reads, writes, write-add/max with its own ALU, ack coalescing, the epoch copy, and
    semaphores (`rsem`) on the owning tile. A write-add's flags go to the owning tile's `FP_STATUS`.
- **Command processor:**
  - Loads IMEM/DTAB images through CORECTL, writes start registers, collects `IRQ`/`HALT` interrupts.
  - Owns page tables (KV pages) and the n-gram hash (hyper_connections.md G-2).
  - It is not on the per-layer critical path. A whole decode step runs from per-core programs synchronized by semaphores.
- **Collectives *(P2)*:** there is no central collective engine. Collectives are DMA programs (`DMA_LIST`) built from three primitives:
  - write-add at SRAM targets;
  - XY-tree multicast inside a die (`DMA_MCAST`);
  - byte-counting semaphores.

  Example: an intra-die all-reduce of a `[64 tokens x 25]` FP32 hyper-connection vector is a reduce-scatter by write-add to 32 owners,
  then 32 multicasts. That is about 0.1 µs, against about 0.6 µs through one central reduction point (HC-5 needs 80 per step).
  Inter-die and inter-package steps use the same writes over D2D/C2C with credit semaphores. The schedules (hierarchical direct
  all-reduce, rail-staged all-to-all) are in [d2d_c2c.md](d2d_c2c.md) L-7.
- **HBM controllers:** one per stack, 32 pseudo-channel engines behind 4 NoC ports (8 PCs per port), in their own 1.2 GHz clock domain.
  The scheduler (FR-FCFS, adaptive page policy, per-bank refresh), timing table and DRAM model are in [hbm.md](hbm.md).
- **D2D / C2C bridges:**
  - They convert NoC packets to link flits with per-virtual-network credits, and inject arriving packets into class X virtual channels.
  - They never forward a packet that arrived over a link (noc.md §6).
  - One link layer for both: 156 B link flits, per-VN credits with a shared pool, CRC-32C and go-back-N replay ([d2d_c2c.md](d2d_c2c.md)).

## 4. Configurations for RTL and simulation

| Config | Cores (mesh) | MXU per core | SPM / ACC per core | VPU | HBM model | Links | Use |
|---|---|---|---|---|---|---|---|
| `TINY` | 4 (2 x 2) | 8 x 8 x 4 | 256 KiB / 256 rows | 4 x 4 | 1 stack, 4 PCs, 2 leaves | none | unit and e2e RTL (Icarus/Verilator) |
| `PKG_TINY` | 2 x `TINY` | | | | | 1 D2D sub-link | cross-die all-reduce |
| `MULTI_TINY` | 2 x `PKG_TINY` | | | | | + 1 C2C port per die | all-to-all |
| `MID` | 8 (4 x 2) | 16 x 16 x 4 | 1 MiB / 512 rows | 8 x 16 | 2 stacks, 8 PCs each | 1 D2D | Verilator performance windows, calibration |
| `FULL` | 32 (8 x 4) | 64 x 64 x 4 | 4 MiB / 2048 rows | 16 x 16 | 4 stacks, 32 PCs each | 4 D2D, 8 C2C | perf simulator only (never built in Verilator) |

Every parameter that differs between configs is a SystemVerilog parameter, and the ISA field widths stay those of `FULL`. The
**semantic widths** (review finding S-2, mxu.md M-13) follow the config where the hardware does, and nowhere else:
- MXU: K tile `4·DIM` FP8 / `2·DIM` BF16, N tile `DIM`, so a result's last bits depend on `DIM` (numerics.md N-3, N-8);
  ACC row `DIM` FP32 (`TINY` 8, `MID` 16; `FULL` 64).
- Not config-dependent: the DMA fold tile (256 FP8 / 128 BF16 elements) and with it the column-scale array (`[K tiles x N]`, row
  pitch `roundup(4·N, 64)`, isa.md §5); the MXU of a reduced config reads scale row `t >> log2(64 / DIM)` for its tile `t`.
- Not config-dependent: SPM banks (64 B in every config) and the legality rules of `MXU_MM` (64 B alignment, isa.yaml). The MXU of a
  reduced config has `DIM`-byte lanes, served by the shim of §5; no SPM client other than the MXU sees `DIM`-byte lanes.

Sizing basis is the Phase 0 calibration (capacity.md):
- An 8 x 8 PE array synthesizes in 4 s at 0.36 GB.
- 16 x 16 takes 18 s at 1.3 GB, and 14 min at 5.9 GB through P&R.
- `MID`'s eight 16 x 16 arrays are therefore for Verilator only, not for flat synthesis.

## 5. Interface tables

Conventions:
- valid/ready handshakes unless marked credit.
- Widths are for `FULL`.
- All ports crossing a block boundary are packed vectors (CLAUDE.md).
- `flit_t` is 1024 data bits + 128 header bits + 3-bit VC + head/tail (noc.md §3).
- **Floating-point flags** (numerics.md N-5; arith.md "Integration contract", items 1-3):
  - Every block that does floating-point arithmetic or conversion has an `fp_flags` output: 4 bits in the `FP_STATUS` bit
    order (`INVALID`, `OVERFLOW`, `SATURATE`, `NAN_IN`), pulse, one cycle per operation group.
  - The block ANDs the `rtl/arith` flags with the valid of the data they belong to. This covers pipeline bubbles, unused
    lanes, columns and rows, short-tile padding and the cycles after reset. It then ORs them across lanes. The output is
    therefore valid-qualified, unlike the raw `rtl/arith` flags.
  - `core/seq` ORs the `fp_flags` inputs into the sticky `FP_STATUS` CSR.
  - Ordering: a block drives an instruction's flags no later than that instruction's completion `sem_inc`. A target drives
    a write-add's flags no later than its ack. So a program that waits for completion and then reads `FP_STATUS` sees every
    flag of the work it waited for.
  - A write-add or max at an SRAM target sets the `FP_STATUS` of the core that owns the target (SPM: the core of the NI;
    GSRAM: the core of the slice's tile), never the initiator's.

**Front end (`core/seq`)**

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| imem_rd | out/in | addr 12, data 128 | 1-cycle SRAM | instruction fetch |
| q_push[6] | out | instruction 128 + captured registers (DQ: 8 x 64; MQ, VQ: 6 x 64) + wait value 32 | valid/ready | issue to DQ0-3, MQ, VQ |
| sem_if | in/out | 64 x 32 | register file, 1 write port per engine + network | wait conditions, signals |
| spm_scalar | out/in | addr 22, data 64 | valid/ready | `LD` / `ST` |
| csr_if | in/out | addr 12, data 64 | 1-cycle | CSRs |
| fp_flags_in | in | 6 x 4 | pulse | `fp_flags` of DMA, MXU, ACC, VPU, NI target and the tile's GSRAM slice, ORed into the sticky `FP_STATUS` CSR (write 1 to clear) |
| fault_in | in | 4 x (valid 1, cause 4, addr 64) | pulse | one per source: DMA (the four DQs merged), MXU, VPU, NI (the front end's own faults enter the same latch). `core/seq` latches the **first** fault into `FAULT_CAUSE` / `FAULT_ADDR`, drops later ones, and takes simultaneous faults in the order front end, DMA, MXU, VPU, NI (review finding S-9). `addr` is the 64-bit `FAULT_ADDR` of isa.md §8, not 32 bits |
| eng_stop | out | 1 | level | a fault, or a CP stop: the engines stop taking instructions from their queues (isa.md §8). Instructions in flight may be cut off or complete |
| ctl | in | start, start_pc[11:0] | level | from CORECTL |
| irq | out | 32 | pulse | to CP via network write |

**DMA (`core/dma`)**

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| q_pop[4] | in | instruction + operands | valid/ready | DQ0-DQ3; one sequencer per DQ, so a head waiting on a semaphore holds no shared resource (§2.5, review finding V-3) |
| dtab_rd | out/in | idx 8, data 256 | request/grant (arbitrated in `core/dtab`), data 1 cycle after the grant (review finding V-9) | tile descriptors when an instruction starts |
| spm_rd / spm_wr | out | 256 / 512 B/cycle | valid/ready, banked | data in/out, index lists, page tables, scales in the staging ring (CSR `DMA_STAGE`; raw units in a later version, U-1) |
| ni_req | out | flit_t | valid/ready | read requests, write packets |
| ni_rsp | in | flit_t | valid/ready, 4-flit buffer; ready depends only on the converter and the SPM ports, because scales are staged before their data (§2.5, review finding V-1) | read data, scales, acks |
| sem_inc | out | 6 + 4 | pulse | completion signals |
| fp_flags | out | 4 | pulse, valid-qualified | conversions (`cvt_dec`, `cvt_fp32_fp`, `mx_dequant_bf16`, fold), in-range elements only, and the NVFP4 column-scale `fp32_mul` (arith.md contract items 6, 8) |

**MXU (`core/mxu`)**

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| q_pop | in | instruction + operands | valid/ready | MQ |
| dtab_rd | out/in | idx 8, data 256 | request/grant (arbitrated in `core/dtab`), data 1 cycle after the grant | `a_td`, `w_td` when an instruction starts (review findings I-11, V-9) |
| a_rd | out/in | 256 B/cycle | valid/ready | A rows from SPM (4 lanes of `DIM` bytes; the shim below turns `DIM`-byte lanes into 64 B bank accesses in `TINY` / `MID`) |
| w_rd | out/in | 512 B/cycle | valid/ready | stationary loads (8 lanes per beat) |
| scale_rd | out/in | 64 B/cycle | valid/ready | rowscale / colscale |
| fault | out | valid 1, cause 4, addr 64 | pulse | legality faults of `MXU_MM` (isa.yaml, review finding S-9) to `core/seq` `fault_in`; the MXU stops taking instructions |
| acc_* stream | out | row 11, part `DIM` x 32, ncols 7, rescale / overwrite / use_cs 1 each, rowscale 32, cs `DIM` x 32, last 1; in: `acc_done` | push, **no ready**; `acc_done` exactly `ACC_LAT` cycles after the `acc_last` row | one output row per cycle to the ACC, which does the read-modify-write; payload valid only in the `acc_valid` cycle; `ACC_LAT` from `acc_pkg`, checked at elaboration. [acc.md](acc.md) §3, §5 (review finding S-11) |
| sem_inc | out | 6 + 4 | pulse | completion |
| fp_flags | out | 4 | pulse, valid-qualified | `mxu_pe` flags at the bottom of each column, for rows `< M` and columns `< N` only, masked for `REG_DOT + 64·REG_OUT` cycles plus the A skew after reset (arith.md contract items 2-4) |

**VPU (`core/vpu`, incl. SFU and top-k)**

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| q_pop | in | instruction + operands | valid/ready | VQ |
| spm_rd[2] / spm_wr | out | 512 B/cycle each | valid/ready, banked | operand streams |
| acc_rd / acc_wr | out/in | 4 rows x `DIM` x 32 | valid/ready, fixed latency | ACC epilogues; ACC as source / destination (the ACC table's `vpu_rd` / `vpu_wr`) |
| dtab_rd | out/in | idx 8, data 256 | request/grant (arbitrated in `core/dtab`), data 1 cycle after the grant (review finding V-9) | operand shapes when an instruction starts |
| sem_inc | out | 6 + 4 | pulse | completion |
| fp_flags | out | 4 | pulse, valid-qualified | VPU and SFU arithmetic and conversions, including `VQUANT` (Inf and NaN in a block detected from its inputs; arith.md contract item 8) |

**SPM (`core/spm`)**

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| rd_req / rd_rsp [clients] | in/out | addr 22 + 64 B lanes | valid/ready, fixed-latency response | MXU A, MXU W, MXU scales (`scale_rd`), VPU x2, DMA, NI target, scalar. The three MXU clients are **gang requests** (below) |
| wr_req [clients] | in | addr 22, 64 B lanes, byte mask | valid/ready | VPU, DMA, NI target, scalar |

**MXU ports of the SPM: shim and gang grant** (review finding S-3; mxu.md M-10). The SPM keeps 64 B banks and the same client port in every
config. The MXU asks for 4 lanes (A), 8 lanes (W) or 1 lane (scales) per request, each lane with its own address, and wants them
**granted together** with the response exactly `SPM_LAT` cycles after the grant, never back-pressured. A shim `mxu_spm_shim`
(parameter `DIM`, between the MXU and `core/spm`) serves this:
- `DIM`-byte lanes are read out of 64 B bank rows (`DIM` divides 64, so a lane never crosses a bank row). At `DIM` 64 the shim only
  groups the lanes.
- **The grant is the commit.** When two lanes of one request fall on the same bank (1R per bank), the shim reads the colliding lanes
  in successive cycles into a collector while `ready` is low, and grants only when the data is complete; the data then follows
  after exactly `SPM_LAT`. A request therefore never waits for "all its banks free at once", so a bank collision slows the beat by
  one cycle per extra lane and cannot deadlock the MXU. The MXU needs no change: it already tolerates arbitrary grant stalls
  (`tb_mxu` runs 30% random stalls).
- Arbitration with the other clients stays per bank, round-robin with the age bound of §2.3.
- `core/spm` and the shim are the owners of this contract; `tb_mxu`'s behavioural SPM has no bank model, so the first bank-accurate
  test is the one for the shim.

**Network interface (`core/ni`)**

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| rtr_out[2] / rtr_in[2] | out/in | flit_t | credit per VC | one per plane |
| init_req / init_rsp | in/out | flit_t | valid/ready | from/to DMA |
| tgt_spm / tgt_ctl | out/in | 128 B/cycle | valid/ready | target accesses incl. write-add (GSRAM is not behind the NI: it is its own target, `die/gsram`) |
| sem_add | out | 6 + 32 | pulse | `rsem` byte counts, remote `SIGNAL` |
| fp_flags | out | 4 | pulse, valid-qualified | write-add / max at the SPM target (`ADD_F32`, `ADD_BF16`, `MAX_F32`; arith.md contract item 7), to this core's `FP_STATUS` |

**Router (`noc/router`)**

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| in[6] / out[6] | in/out | flit_t | credit per VC (5 VCs) | N, S, E, W, core, GSRAM |
| coord | in | x[3:0], y[2:0] | static | XY / YX route computation |
| mc_rect | in header | 14 | | multicast rectangle (REQ-L only) |

**HBM controller (`hbm/ctrl`)**

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| noc[4] | in/out | flit_t | credit per VC | one per leaf (8 PCs each) |
| dfi[32] | out/in | per PC: 1 row + 1 column command per `clk_mc` cycle, 256-bit write and read data; the two PCs of a channel share its command buses (arbiter in hbm.md H-3) | DFI-like, fixed read latency | to the behavioral PHY + DRAM model ([hbm.md](hbm.md) H-5) |
| csr | in/out | 64 | valid/ready | timing table, policy, refresh rate, per-PC counters (hbm.md H-7) |
| clk_mc | in | 1 | | controller clock, 1.2 GHz (`MC_SYNC = 1`: same as `clk`) |

The PC engine, the DFI-like signals and the DRAM model have their own tables in hbm.md H-5 and H-7.

**D2D / C2C bridge (`link/*`)**

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| noc | in/out | flit_t | credit per VC | the leaf router |
| link_tx / link_rx | out/in | 1248-bit link flit per lane | valid/ready; credits per VN (REQ, RSP) + shared pool inside the flit | behavioral PHY with latency, bandwidth and error-injection parameters |
| csr | in/out | 64 | valid/ready | routing table (C2C peer ports), replay and CRC counters, link state |
| irq | out | 1 | level | link down |

The link-layer TX/RX and the behavioral PHY have their own tables in d2d_c2c.md L-9.

**GSRAM slice (`die/gsram`)**

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| noc | in/out | flit_t | credit per VC | router port 6. The slice is an SRAM target with the rules of §2.6: write-add/max ALU, ack coalescing, epoch copy |
| sem_add | out | 6 + 32 | pulse | to the tile's semaphore file |
| fp_flags | out | 4 | pulse, valid-qualified | write-add / max at this slice, to the owning tile's `FP_STATUS` (`core/seq`) |

**DTAB (`core/dtab`)**, 256 x 256 bits

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| rd | in/out | idx 8, data 256 | request/grant: one grant per cycle, round-robin over DMA, MXU, VPU; data 1 cycle after the grant | TDs at instruction start |
| wr | in | 32 B | valid/ready | DMA (space `DTAB`) and CORECTL writes; ordered by software (isa.md §4) |

**Accumulator (`core/acc`)**, 2048 rows x 64 FP32 (`FULL`; rows of `DIM` FP32 in `TINY` / `MID`, 256 / 512 rows; review finding S-2), shared by the MXU and the VPU

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| acc_* stream | in/out | as the MXU table (inputs) + `acc_done` (out) | push, no ready; one row per cycle always accepted; fixed latency `ACC_LAT` = `acc_pkg::ACC_LAT` (8 budget) | K-tile accumulation: `colscale` of `part` in an input stage in parallel with the SRAM read, `rowscale` of the old row, add or overwrite; word-masked write for columns `< ncols`; row = `DIM` FP32. [acc.md](acc.md) §3-§6 |
| vpu_rd / vpu_wr | in/out | 4 rows x `DIM` x 32 per cycle (`FULL` 64) | valid/ready (ready per port), fixed latency after acceptance; a 4-row request is accepted whole or refused and repeated | epilogues; ACC as a VPU operand or destination (space `ACC`) |
| (priority) | | | rule, no port | **the MXU stream wins every SRAM port conflict, on any row**, via `vpu_rd` / `vpu_wr` `ready`; the MXU is never delayed (replaces the same-row `conflict` level, review finding S-11; acc.md §7) |
| fp_flags | out | 4 | pulse, valid-qualified | `acc_update` lanes (rowscale, colscale, add) of the columns `< ncols` (arith.md contract item 5); a row's flags no later than its write-visible cycle, so those of the `acc_last` row no later than `acc_done` and the MXU's `sem_inc` (acc.md §6) |

**Leaf router (`noc/leaf`)**, 24 per die

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| mesh[2] | in/out | flit_t | credit per VC (5 VCs) | one per plane, to the boundary core router |
| ep[k] | in/out | flit_t | credit per VC | endpoints: HBM controller port, D2D bridge, C2C bridge (2 ports), CP, host interface |

A leaf never forwards between its mesh ports (noc.md §1). Arrivals from a link endpoint enter class X VCs.

**Host interface (`die/host`)**, behavioral

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| noc | in/out | flit_t | credit per VC | via leaf W3; the host is an initiator (reads, writes) and a target (DMA to host memory) |
| host | in/out | 64 B/cycle per direction | valid/ready | PCIe-class stream, modeled |

**Async FIFO (`common/afifo`)**: the HBM controller's clock crossing (`clk` 1.5 GHz to `clk_mc` 1.2 GHz)

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| wr_* / rd_* | in/out | parameter (flit_t at the HBM ports) | valid/ready in each clock domain | Gray-coded pointers, 2-flop synchronizers, depth parameter (default 8) |

**Command processor (`die/cp`)**

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| noc | in/out | flit_t | credit per VC | via leaf W2 |
| host | in/out | flit_t | valid/ready | via leaf W3 (host interface) |
| irq_in | in | network writes to the CP mailbox | | core `IRQ` / `HALT` |

## 6. Throughput checks against the frozen numbers

| Path | Need | Provided |
|---|---|---|
| HBM to cores (die) | 3,277 B/cycle at peak | 16 HBM leaves x 2 planes x 128 = 4,096 B/cycle (80% at peak); mesh links at most 80% with stack-affine placement, 85% fully interleaved (noc.md §5) |
| HBM share to one core | 102 B/cycle | 256 B/cycle per network interface (2 planes) |
| Weights into the MXU in decode | ≤ 193 elements/cycle (4-bit weights at the core's peak HBM share; 164 at 0.85); NVFP4 → BF16: 181 / 154 | DMA converter 256 elements/cycle, DMA SPM write 512 B/cycle shared with the staged scales: 248 elements/cycle for NVFP4 → BF16, 256 in FP8. **MXU at the measured tile period of 39.5 cycles (§2.2): 415 B/cycle, 415 FP8 or 207 BF16 elements/cycle: 2.15x (FP8, peak share; 2.5x at 0.85) and 1.15x (NVFP4 → BF16; 1.35x at 0.85), the narrowest stage on the exact path.** The port limit alone (32 cycles) would give 512 B/cycle (2.65x, 1.41x). With a colliding pitch (43 cycles measured; 48 for the load alone) 1.05x / 1.24x (review finding S-5, mxu.md M-21; numerics.md D1) |
| D2D | collectives; the Phase 1 roofline is insensitive to D2D bandwidth above 1 TB/s | 1.4 TB/s per direction payload |
| C2C | 0.8 TB/s per die (8 ports) | 4 leaves x 256 B/cycle = 1.5 TB/s (55% at peak) |
| Prefill MXU feed | A 256 B/cycle + W 16 KiB per 256 A rows | SPM 2 KiB/cycle read |

## 7. Open items

- MXU efficiency at small M and the exact stationary-load overlap are measured in Phase 3/7 (arch.md §6 assumed a flat 0.8).
- Whether decode keeps the hyper-connection stream replicated rather than sharded (hyper_connections.md §6). The ISA supports both.
- 1R1W SRAM availability in the ASAP7 macro flow (§2.3 fallback).
- CP implementation in RTL: a behavioral model is enough for `TINY` e2e. A real RV64 core is out of scope.

## 8. Changes to arch.md made here

| Item | arch.md before | Now | Evidence |
|---|---|---|---|
| D2D bandwidth | 5 TB/s per direction | 1.4 TB/s payload per direction (4 sub-links) | The mesh edge can feed at most 1.45 TB/s (noc.md §5). The roofline changes by ≤ 0.2% at the serving point, and by ≤ 7% for prefill even when TP = 2 is forced across D2D |
| NoC | one 2D mesh, 128 B/cycle links | two identical mesh planes of 128 B/cycle links | A single plane cannot take 820 B/cycle per HBM stack in through 4 leaves (noc.md §5) |
| SRAM | 128 MiB SPM + 32 MiB GSRAM | + 16 MiB MXU accumulators (ACC), + 2.3 MiB IMEM/DTAB | §2.2: a weight-stationary MXU accumulates outside the array |
| Collective engine | a die block | distributed: write-add in SRAM targets, multicast, semaphores | §3 |
| Buffer SRAM outside the cores (review R-9) | not counted | HBM controllers 1 MiB per die (hbm.md H-1), link replay + receive buffers 1.4 MiB per die (d2d_c2c.md L-4) | `make hbm`, `make links` |
| C2C PHY | 100 GB/s per port, unspecified | 6 x 200 Gb/s lanes per port (48 per die) to carry 100 GB/s of payload after flit overhead | d2d_c2c.md L-2 |
