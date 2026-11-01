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
          NETWORK INTERFACE (2 planes; initiator for DMA, target for SPM / GSRAM / CORECTL; write-add ALU)
                    │
          ROUTER (N, S, E, W, core, GSRAM ports) ── GSRAM slice 1 MiB
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
- 64 x 64 PEs. Each PE holds 4 stationary FP8 values (K = 4) and computes a 4-way dot product plus the partial sum from the PE above.
  A stationary tile is K = 256 x N = 64 (16 KiB FP8; BF16: K = 128, each PE a 2-way BF16 dot product, A rows still one per cycle, so
  half the FP8 MAC rate; numerics.md N-3).
- A rows stream from the left (256 B/cycle, skewed). Partial sums flow down the columns and leave at the bottom: 64 FP32 per cycle into ACC.
- **Stationary load port 512 B/cycle, double-buffered (shadow weights).**
  - A tile loads in 32 cycles while the previous tile streams.
  - Row-wise or column-wise load: a column load takes 256 contiguous bytes, i.e. one row of an HF `[out, in]` weight. So weights stay in
    their published layout (`trans_w = 1`), and attention can use K-cache tiles as `K^T` with no transpose pass.
- ACC: 2048 rows x 64 FP32 (512 KiB), read-modify-write at 256 B/cycle each way.
  - Two FP32 multipliers per column at the ACC input: `rowscale` rescales the existing ACC row (online-softmax `alpha`), and
    `colscale` scales a K tile's partial sum (block-scaled weights, NVFP4 tensor scale).
  - The VPU reads ACC directly (4 rows per cycle).

Why weight-stationary and not output-stationary:

| | Weight-stationary (chosen) | Output-stationary |
|---|---|---|
| Decode (M = 1-30 tokens per expert) | MXU-load-bound at 512 B/cycle of weights: 2.9x the core's HBM share with 4-bit weights expanded to FP8 (174 B/cycle at 85%), 1.5x for BF16 | Needs A and W both streaming at 256 B/cycle each; also M/64 utilization |
| SPM read bandwidth for a GEMM | A 256 B/cycle + W during loads only | A 256 + W 256 B/cycle every cycle |
| Long K | Accumulates in ACC (RMW), any K | Accumulates in PE registers; drains 16 KiB per output tile |
| Epilogue overlap | VPU reads one ACC region while the MXU fills another | Needs double-buffered PE accumulators (2 x 4096 FP32 registers) |
| Block-scaled weights | One FP32 multiply per column per K tile at the ACC input | Scale per PE or per K step inside the array |

Decode is HBM-bound on every model (arch.md §2), so the MXU's job in decode is only to keep up with weight bytes. Weight-stationary does that
at any M. It keeps the FP32 accumulators out of the PE array and makes the block-scale contract (DMA writes FP8 plus FP32 column scales;
isa.md §5) a cheap multiply at the ACC input.

### 2.3 Scratchpad (SPM)
- 4 MiB, 32 banks x 128 KiB, 64 B per bank access, one read and one write port per bank (1R1W): 2 KiB/cycle read plus 2 KiB/cycle write.
- Bank = `addr[10:6] ^ addr[15:11]`. Contiguous 2 KiB always covers all 32 banks. Power-of-two row pitches do **not** all spread
  (corrected, review finding I-13): over 64 rows the row starts reach 32, 16, 8, 4, 2 and 1 banks at 2, 4, 8, 16, 32 and 64 KiB.
  A stationary load of two 256 B rows per cycle therefore collides at 2, 4 and 64 KiB pitches. The compiler pads such SPM pitches
  by 512 B (1 KiB at a 16 KiB pitch), which makes every power-of-two pitch from 2 to 128 KiB conflict-free (`test_isa.py`) for
  tiles whose base is 2 KiB aligned. The compiler places stationary-load tiles with a padded pitch that way (review finding V-8):
  at an 8 KiB + 512 B pitch half of the 256 B-aligned bases collide, and at bases that are only 64 B aligned most padded pitches
  do. Every other padded pitch is also conflict-free at any 256 B-aligned base.
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
  - Shared resources are the tag table, the staging ring (below), the SPM ports and NI injection. They are taken per read or
    write packet, round-robin over the DQs that have a packet ready, and only after the head's wait is satisfied.
  - So a DQ whose head waits on a semaphore holds nothing another DQ needs. A started transfer holds its tags until its
    responses sink, and its ring space until its data has been converted (review finding W-6). Neither waits on another DQ:
    every ring entry, once allocated, needs only tags to complete (below). d2d_c2c.md L-7 issue rule 2 relies on this.
- It keeps up to 512 read granules (256 B) in flight, 128 KiB. That is about 2x the core's HBM share (102 B/cycle) times a typical
  500-cycle loaded round trip.
  - **Converting reads are paced *(review finding Z-2; option chosen by the user, 2026-11-01)*.** At most `DMA_PACE` bytes
    (CSR, reset 72 KiB) of converting reads are in flight: the scale and data reads of block-format transfers, counted at
    issue and returned by their responses (a 17-bit counter and a compare; 0 turns the limit off). Plain reads are limited
    by tags only. Reads beyond the share times the round trip add nothing to the delivered rate; they only wait in the HBM
    queue, where a converting stream's scale reads would wait behind its own data and every ring entry and tag would be
    held longer (staging ring, below).
- Read requests cover up to 2 KiB (one HBM leaf or one SRAM target) and return 256 B response packets. Each response carries a tag
  and granule index, and its destination SPM address is held in the tag table. Response data therefore needs no staging buffer: the
  SPM space is reserved when the request is issued, which is what keeps the response network deadlock-free (noc.md §4).
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
  - **Split units are staged raw.** A conversion unit whose bytes arrive in more than one response gets a raw staging slot: an
    inline-scaled row (`SCALE_INLINE`, such as a 264 B Engram row: 256 B of elements, then their 8 B of scales; memmap.md
    §6), or a fold tile that is not inside one 256 B granule (a fold
    weight row that is not a whole number of 256-element tiles, or a K slice that does not start on one). Its responses are
    written there raw, and the converter takes the unit from SPM once it is complete. Its raw bytes add to the DMA's SPM write
    and read traffic.
  - **Staging ring:** an SPM region given by CSR `DMA_STAGE` (base and size), reserved by the runtime and shared by the four DQs.
    Size 0 makes block-format conversion fault (`ILLEGAL_FIELD`).
  - **Ring allocation *(review findings W-1, Z-1)*.** The ring is allocated in **entries**. An entry is a run of whole
    conversion units: the scale reads of their out-of-line scales, the raw slots of its split units, and the data reads of
    those units. It usually has one scale read; it has two when the run's scales cross a 2 KiB block of the scale array
    (a fold tile's 8 or 16 B of scales at an offset that is not a multiple of their size, as with K not a multiple of 256).
    - **Every data read and every conversion unit belongs to exactly one entry *(review finding Z-1)*.** An entry boundary is
      a unit boundary. Where it falls inside a granule, the DMA cuts the read there: that granule is two partial reads (a
      read request's `len` is in bytes, noc.md §2), one in each entry. So a data read waits only for its own entry's scales.
      - Why: if the granule were one read, issued once both entries' scales are in the ring, the older entry could complete
        only after the younger one is allocated. With several DQs the ring's oldest entry can be the only one of its DQ,
        with the ring full behind it, and in-order reclaim lets nothing past it. Four DQs that stream DeepSeek index-key
        rows (64 B) from row 2 of a page, with 1 KiB or 2 KiB scale reads, deadlock that way at cycle 1,716 (4 KiB ring) and
        5,977 (16 KiB); contiguous fold tiles from byte 64 at 1,037 and 1,292 (`isa.staging_ring`, `boundary="shared"`;
        the third verifier's own model had 1,715 / 5,976 and 1,051 / 1,298). One DQ cannot deadlock this way, because its
        next entry is the ring's next allocation.
      - Cost: one more request per entry boundary inside a granule, and at most one 32 B HBM burst read twice. The DMA ends
        an entry on a granule boundary when a unit boundary falls on one, so aligned streams have no cut.
    - **An entry's scales and the raw slots of all its split units are one allocation, taken when its first scale read is
      issued** (when its first data read is issued if it has no scale read, as for inline-scaled rows). Its data reads then
      need only tags, and tags are freed by responses, which sink unconditionally. So every allocated entry completes, the
      oldest one frees its space, and the ring always drains.
    - **Inline entries *(review finding Z-3)*.** An entry of `SCALE_INLINE` rows (memmap.md §6) has no scale read. It holds
      whole rows, each with all its reads: a 264 B Engram row that crosses a 2 KiB block is two reads, both in the row's
      entry. Its raw slot holds the rows, 272 B per row in 16 B units.
    - The ring is circular, in 16 B units. An entry is contiguous (if it does not fit before the end it starts at the
      beginning) and is freed when all its data has been converted. Space comes back in allocation order.
    - **Ring and entry size *(review finding Z-3)*.** `DMA_STAGE` must hold at least 2 KiB, or a block-format conversion
      faults `ILLEGAL_FIELD` (size 0 included). The DMA forms entries of at most a quarter of the ring, shortening the run
      of units. The quarter is a rate target: four entries in flight let scales run a round trip ahead of their data.
      Liveness needs only entry ≤ ring. No single unit's entry is larger than 272 B (an Engram row; a split MXFP8 fold
      tile is 256 + 8 B, a split 4-bit tile 128 + 8 or 16 B), which fits a quarter of the 2 KiB minimum, so the DMA can
      always meet the target. A ring below the 16 KiB default lowers the rate (below), never liveness.
    - **Order of reads *(review finding Z-8)*.** Within a DQ, the scale read of its next entry goes before its own data reads
      whenever that entry's space can be taken (greedy read-ahead). That is what keeps a converting stream at the HBM rate.
      Across DQs, scale and data reads alike take the shared resources per packet, round-robin (above), so one DQ's scale
      read does not overtake another DQ's data read; per-DQ order is all the read-ahead needs. A scale read takes
      ceil(scale bytes / 256) tags, as a data read of that size would.
    - Reference: `isa.staging_ring`, a cycle model of the rule. Entries can carry HBM byte ranges; their reads are then the
      granule pieces of the range, cut at entry boundaries and grouped into requests inside one 2 KiB block. `test_isa.py`
      runs mixed split and non-split streams, one DQ or four, with greedy read-ahead, and they always complete; the
      shared-granule reading deadlocks with four DQs. Phase 3 adds `core/dma` formal properties: every allocated ring entry
      needs only tags to complete, and no data read waits for the scales of an entry other than its own.
    - Rejected: taking the raw slot at the unit's first data read (the text before W-1). Read-ahead fills the ring with
      scales whose data can never get a raw slot: the all-split fold stream below deadlocks at cycle 687 (`test_isa.py`).
    - Rejected: capping read-ahead so that the largest raw slot still fits. In a ring it deadlocks too, because a raw slot
      freed behind newer scale entries is reclaimed only in allocation order. It works only with a free-list allocator, and
      even then it carries 16.5 elements/cycle on the all-split fold stream against 27.6 for the atomic rule (one probe,
      not committed).
  - **What 16 KiB is sized for: scales.** A non-split unit's scales live from their read to the data's conversion, about two
    round trips. 16 KiB holds 1,024 cycles of NVFP4 scales at the converter's rate (16 B/cycle). At the HBM share (87 B/cycle,
    155 elements/cycle of NVFP4 → BF16) it keeps the full rate up to a round trip of about 845 cycles (`isa.staging_ring`:
    154.7 at 500, 152.9 at 845, 127 at 1,000), and 8 KiB does not (76 at 845). A smaller ring only lowers the rate.
    - **These rates need the pacing *(review finding Z-2)*.** They come from the paced model (`hbm="paced"`: every
      response exactly one round trip after its issue). With the share modelled as a queue (`hbm="server"`: requests reach
      a FIFO served at 87 B/cycle, so the core's own backlog adds to the round trip) and greedy issue up to 512 tags, the
      weight stream runs at 142 / 119 / 111 elements/cycle at 500 / 845 / 1,000 cycles, and the paged NVFP4 KV gather (one
      entry per row: a 32 B scale read, then the 256 B row) at 136 / 111 / 104. The third verifier's model, with
      per-granule requests, had 130 / 110 / 103 and 135 / 111; `max_req=1` gives 128 / 109 / 102. The backlog lengthens
      both round trips of every entry, so the weight stream becomes ring-bound. It also lengthens every tag's hold time,
      and the KV gather spends one tag per 32 B scale read, so it becomes tag-bound, and a larger ring does not help it.
    - **With `DMA_PACE` at 72 KiB** (about the share times the 845 cycles the ring covers) the queue model gives the paced
      rates back:

      | elements/cycle at L = 500 / 700 / 845 / 1,000 | weights (NVFP4 → BF16) | paged NVFP4 KV gather |
      |---|---|---|
      | paced bound (16 KiB) | 155 / 155 / 153 / 129 | 155 / 155 / 155 / 130 |
      | queue, tags only (16 KiB) | 142 / 128 / 119 / 111 | 136 / 118 / 111 / 104 |
      | queue, `DMA_PACE` 72 KiB (the rule) | 155 / 155 / 150 / 127 | 155 / 155 / 154 / 130 |
      | queue, 24 or 32 KiB ring, tags only | 155 / 155 / 155 / 155 | 136 / 118 / 110 / 103 |
      | queue, 24 KiB + 72 KiB | 155 / 155 / 152 / 130 | 155 / 155 / 154 / 130 |
      | queue, 32 KiB + 80 KiB | 155 / 155 / 155 / 145 | 155 / 155 / 146 / 130 |

      Above about 845 cycles the ring (weights) and the tags (KV gather) bind in every model. The limit is fairly sharp: 64
      KiB gives 138 / 137 at 845, 80 KiB 144 / 146. A runtime that sets a larger ring scales it, to about the share x ring /
      (2 x the stream's scale bytes per cycle).
    - Options costed for Z-2: the limit (taken: a counter, a compare and a CSR; the ring stays 16 KiB); a larger ring alone
      (8-16 KiB more SPM reserved per core, no hardware, but the KV gather stays 29% short at 845 cycles); both (no better
      for the KV gather, and 24 KiB gains the weights only 2-3 elements/cycle). Rejected: limiting all reads, which would
      cut a plain stream to 72 B/cycle at a 1,000-cycle round trip; a tag cap, which the third verifier found helps the
      weight stream and hurts the KV gather.
    - The queue model serves one core's requests in issue order. Real HBM has per-PC queues and other cores' traffic; the
      Phase 7 performance simulator is the executable check of these rates.
    - Split units hold about 17 times more space per element (a 128 B MXFP4 tile and its 8 B of scales). If every fold tile
      of a stream were split, the ring would carry about 28 elements/cycle, against a need of about 164.
    - **So the compiler never splits fold tiles:** it pads the HBM row pitch of fold-converted weights to a whole number of
      256-element tiles (128 B at 4 bits, 256 B for MXFP8), places them on a 256 B boundary, and cuts K slices on tile
      boundaries. Padding is in the pitch only; the TD's n0 still covers the real K, and the last tile of a row is short but
      inside one granule. Every surveyed model already has K a multiple of 256 (expert K 2,048-6,144 and 2,304), so it costs
      nothing there. A layout that cannot be padded is legal and converts at the ring-limited rate.
    - Inline-scaled rows stay split, because a 264 B Engram row always spans two granules. The ring carries about 62 of them
      per round trip, 0.12 rows/cycle per core at 500 cycles. A DeepSeek batch-256 decode step gathers 12,288 rows (24 per
      token in each of 2 layers); even if one die's 32 cores gather all of them, that takes about 2 µs, issued at the start
      of the step and hidden (hyper_connections.md G-1). A remote row's longer round trip lowers the rate in proportion,
      still hidden. Rows staged through the sibling arrive unconverted and convert SPM to SPM, with a short round trip.
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
  - Quantization (KV, activations) is a VPU instruction (`VQUANT`), because it needs row maxima.
- **Write packets *(review finding V-5)*:** a packet holds contiguous destination bytes. Adjacent runs are merged, including
  consecutive paged rows of one page, up to a 256 B granule boundary. `HBM_ALIGN` is checked per packet. So eight 4 B scale rows
  that form an aligned 32 B block are one 32 B write (memmap.md §6 worked example).
- Write operations at the destination: plain write, FP32/BF16 add, FP32 max. SRAM targets have the ALU in their network interface; HBM
  takes plain writes only.

### 2.6 Network interface and semaphores
- The interface is an initiator for the DMA and a target for SPM, GSRAM and CORECTL (IMEM, DTAB, semaphores, CSRs).
- Each target handles: reads, writes, write-add/max, and byte-counting semaphore adds attached to writes (`rsem`).
- Plane choice is per packet: round-robin over planes with credits. No ordering is needed (noc.md §7).
- Write acknowledgements carry a byte count and are coalesced per 2 KiB burst (noc.md §2, hbm.md H-2):
  - every `WR` carries its burst's total bytes (`blen`), and the target acks when it has accepted them all. A burst stays inside
    one 2 KiB-aligned block, so it has one target (review finding V-10);
  - a full table acks its oldest entry early with a partial count, so accepting a write never waits for ack state (review finding R-3);
  - an entry idle for `ACK_FLUSH` cycles (CSR, default 64) is acked with its partial count, which completes bursts whose entry was
    evicted (review finding X-3);
  - an SRAM target acks only after the write, including a write-add, is visible to a later read.
- 64 semaphores (32-bit) per core, readable and writable over the network (memmap.md §5).

## 3. Die-level blocks

- **GSRAM:** 32 slices of 1 MiB, one per core tile, on the router's sixth port.
  - Software-managed and slice-contiguous: no hardware interleave.
  - Uses: collective staging, KV and expert prefetch, broadcast sources.
  - Same target functions as SPM (write-add, semaphores on the owning tile).
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

Every parameter that differs between configs is a SystemVerilog parameter, and the ISA field widths stay those of `FULL`.

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

**Front end (`core/seq`)**

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| imem_rd | out/in | addr 12, data 128 | 1-cycle SRAM | instruction fetch |
| q_push[6] | out | instruction 128 + captured registers (DQ: 8 x 64; MQ, VQ: 6 x 64) + wait value 32 | valid/ready | issue to DQ0-3, MQ, VQ |
| sem_if | in/out | 64 x 32 | register file, 1 write port per engine + network | wait conditions, signals |
| spm_scalar | out/in | addr 22, data 64 | valid/ready | `LD` / `ST` |
| csr_if | in/out | addr 12, data 64 | 1-cycle | CSRs |
| ctl | in | start, start_pc[11:0] | level | from CORECTL |
| irq | out | 32 | pulse | to CP via network write |

**DMA (`core/dma`)**

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| q_pop[4] | in | instruction + operands | valid/ready | DQ0-DQ3; one sequencer per DQ, so a head waiting on a semaphore holds no shared resource (§2.5, review finding V-3) |
| dtab_rd | out/in | idx 8, data 256 | request/grant (arbitrated in `core/dtab`), data 1 cycle after the grant (review finding V-9) | tile descriptors when an instruction starts |
| spm_rd / spm_wr | out | 256 / 512 B/cycle | valid/ready, banked | data in/out, index lists, page tables, scales and raw units in the staging ring (CSR `DMA_STAGE`) |
| ni_req | out | flit_t | valid/ready | read requests, write packets |
| ni_rsp | in | flit_t | valid/ready, 4-flit buffer; ready depends only on the converter and the SPM ports, because scales are staged before their data (§2.5, review finding V-1) | read data, scales, acks |
| sem_inc | out | 6 + 4 | pulse | completion signals |

**MXU (`core/mxu`)**

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| q_pop | in | instruction + operands | valid/ready | MQ |
| dtab_rd | out/in | idx 8, data 256 | request/grant (arbitrated in `core/dtab`), data 1 cycle after the grant | `a_td`, `w_td` when an instruction starts (review findings I-11, V-9) |
| a_rd | out/in | 256 B/cycle | valid/ready | A rows from SPM |
| w_rd | out/in | 512 B/cycle | valid/ready | stationary loads |
| scale_rd | out/in | 64 B/cycle | valid/ready | rowscale / colscale |
| acc_rmw | out/in | 64 x 32 read + 64 x 32 write per cycle | fixed latency | ACC update |
| sem_inc | out | 6 + 4 | pulse | completion |

**VPU (`core/vpu`, incl. SFU and top-k)**

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| q_pop | in | instruction + operands | valid/ready | VQ |
| spm_rd[2] / spm_wr | out | 512 B/cycle each | valid/ready, banked | operand streams |
| acc_rd | out/in | 4 rows x 64 x 32 | fixed latency | ACC epilogues |
| dtab_rd | out/in | idx 8, data 256 | request/grant (arbitrated in `core/dtab`), data 1 cycle after the grant (review finding V-9) | operand shapes when an instruction starts |
| sem_inc | out | 6 + 4 | pulse | completion |

**SPM (`core/spm`)**

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| rd_req / rd_rsp [clients] | in/out | addr 22 + 64 B lanes | valid/ready, fixed-latency response | MXU A, MXU W, MXU scales (`scale_rd`), VPU x2, DMA, NI target, scalar |
| wr_req [clients] | in | addr 22, 64 B lanes, byte mask | valid/ready | VPU, DMA, NI target, scalar |

**Network interface (`core/ni`)**

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| rtr_out[2] / rtr_in[2] | out/in | flit_t | credit per VC | one per plane |
| init_req / init_rsp | in/out | flit_t | valid/ready | from/to DMA |
| tgt_spm / tgt_gsram / tgt_ctl | out/in | 128 B/cycle | valid/ready | target accesses incl. write-add |
| sem_add | out | 6 + 32 | pulse | `rsem` byte counts, remote `SIGNAL` |

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
| noc | in/out | flit_t | credit per VC | router port 6 |
| sem_add | out | 6 + 32 | pulse | to the tile's semaphore file |

**DTAB (`core/dtab`)**, 256 x 256 bits

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| rd | in/out | idx 8, data 256 | request/grant: one grant per cycle, round-robin over DMA, MXU, VPU; data 1 cycle after the grant | TDs at instruction start |
| wr | in | 32 B | valid/ready | DMA (space `DTAB`) and CORECTL writes; ordered by software (isa.md §4) |

**Accumulator (`core/acc`)**, 2048 rows x 64 FP32, shared by the MXU and the VPU

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| mxu_rmw | in/out | 64 x 32 read + 64 x 32 write per cycle, row index 11 | fixed latency (read 2 cycles) | K-tile accumulation, `rowscale` / `colscale` at the input |
| vpu_rd / vpu_wr | in/out | 4 rows x 64 x 32 per cycle | fixed latency | epilogues; ACC as a VPU operand or destination (space `ACC`) |
| conflict | out | 1 | level | same-row MXU write and VPU access in one cycle: the VPU stalls (semaphores make this rare) |

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
| Weights into the MXU in decode | ≤ 193 elements/cycle (4-bit weights at the core's peak HBM share; 164 at 0.85) | DMA converter 256 elements/cycle, DMA SPM write 512 B/cycle shared with the staged scales, stationary load 512 B/cycle: 248 elements/cycle for NVFP4 → BF16, 256 in FP8 (numerics.md D1) |
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
