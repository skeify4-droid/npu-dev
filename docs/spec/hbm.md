# HBM subsystem (Phase 2)

Status: **reviewed, 2026-11-01** ([spec review](../reports/spec_review.md)).
- Model and checks: `sim/analytic/hbm.py`:
  - `Dram` is the bank-state and timing checker of one pseudo-channel.
  - `simulate()` is the scheduler.
  - `make hbm` writes [`hbm_experiments.md`](../reports/hbm_experiments.md).
  - `tb/unit/test_hbm.py` tests both.
- Address mapping: [memmap.md](memmap.md) §3. NoC side: [noc.md](noc.md). Block view: [microarch.md](microarch.md) §3.

Items marked *(P2)* are decisions made here.

## H-1. Structure

Each die has 4 HBM3e stacks:
- 1024 bits per stack at 9.6 Gb/s, 1.23 TB/s per stack.
- 32 pseudo-channels (PCs) per stack, each 32 bits wide.
- 3 stack IDs (SIDs) per stack for a 12-Hi stack of 24 Gb dies.

There is **one controller per stack**:

```
 NoC leaf N1..N4 (or S1..S4), 2 planes each
        │ flit_t, credits per VC                    clk (1.5 GHz)
 ┌──────┴───────────────────────────────────────────────────────────┐
 │ port 0..3: async FIFOs (CDC), request decoder, response packer   │
 │   └─ per port 8 PCs (pc[4:3] = port, memmap.md §3)                │  clk_mc (1.2 GHz)
 │ PC engine x 32: read queue 32, write queue 16, read return 16,   │
 │   FR-FCFS scheduler, page policy, per-bank refresh, timing table  │
 │ CSRs (DIECTL block 2 + stack): timing, policy, counters           │
 └──────┬───────────────────────────────────────────────────────────┘
        │ DFI-like, per PC: 1 row + 1 column command per clk_mc cycle
 behavioral PHY + DRAM model (RTL testbench) / HBM3e PHY (real silicon, out of scope)
```

- **Clock *(P2)*:** the controller runs at `clk_mc` = CK / 2 = 1.2 GHz.
  - One BL8 burst (32 B) on one PC takes exactly one `clk_mc` cycle, so one column command per PC per cycle is full rate.
  - The two PCs of a channel share the channel's row and column command buses. PC p drives CK phase p of each `clk_mc` cycle. A
    command that occupies one CK leaves the PCs independent, but an ACT that occupies 2 CK (as in HBM2; `ACT_CK`, (A)) holds the
    other PC's next slot. So the PC engines are **not** independent: the two of a channel share a row-bus arbiter (H-3 step 7),
    and the checker has a channel-scope rule (H-4; review finding X-2(b)).
  - Async FIFOs separate `clk_mc` from the 1.5 GHz NoC.
  - In `TINY` and `MID` the parameter `MC_SYNC = 1` puts both on one clock, so unit simulations need no clock crossing.
- **Bandwidth check:**
  - Per port: 8 PCs x 32 B x 1.2 GHz = 307 GB/s = 205 B per NoC cycle.
  - The leaf carries 256 B/cycle (2 planes), so the port runs at 80% at peak (noc.md §5).
- **Buffers per PC:**
  - read queue: 32 granule descriptors;
  - read return buffer: 16 x 256 B = 4 KiB;
  - write queue: 16 x (descriptor + 256 B + burst mask) = 4 KiB.

  That is 256 KiB per controller and **1 MiB per die** (microarch.md §8).
- **Reduced configurations (review finding R-11):**
  - `TINY`: 1 stack, 2 leaves, 4 PCs. `MID`: 2 stacks of 8 PCs.
  - Both keep the memmap.md §3 decode unchanged and **fold** the 5-bit PC index `pc` (`hbm.fold_pc`):
    - leaf = the low log2(LEAVES) bits of `pc[4:3]`;
    - PC within the leaf = `pc[2:0] mod PCS_PER_LEAF`;
    - the k = log2(32 / PCS) unused `pc` bits become the lowest bits of an extended row;
    - **and they are XORed into `{ba, bg}`** (bank group first; review finding I-10). Without this the granules of a 2 KiB block
      that land on one physical PC differ only in the row, so they are row conflicts in one bank: `seq_read` reached 0.68-0.72 on
      `MID` and 0.55 on `TINY` against 0.99 on `FULL`. With it they reach 0.98-0.99 (hbm_experiments.md H6).
  - A 2 KiB block therefore still lands on one leaf, and the map stays one-to-one (the folded bits are kept in the row, so the XOR
    can be undone).
  - The DFI row address is `15 + k` bits: 17 for `MID`, 18 for `TINY` (parameter `ROW_BITS`, H-5). The DRAM model's sparse storage
    does not care about the larger row count.

## H-2. Requests from the NoC

The unit of work inside the controller is a **granule**: 256 B in one row of one bank, which is 8 consecutive bursts (memmap.md §3).

| Packet | Controller action |
|---|---|
| `RD_REQ` (≤ 2 KiB, one leaf) | Decode the granules. They fall on 8 different PCs of this port. Push one read descriptor (bank, row, column-high, first burst, burst count) into each PC's read queue: only the bursts the request's byte range touches, so a request that starts or ends inside a granule reads part of it (review finding Y-5). The request leaves the input only when every granule has been queued. |
| `RD_RSP` (to the core) | One per granule touched, 2 flits, tag + granule index (counted from the granule of the request's first byte). The payload is the granule, aligned; bursts not read are undefined (noc.md §2, partial granules). Sent when all of its bursts are in the read return buffer. Granules of one request return in any order. |
| `WR` (≤ 256 B, inside one granule) | Write the bursts into a write queue entry (burst mask). It is **posted**: acknowledged once it is in the write queue. |
| `WR_ACK` (to the core) | Carries the burst id and a **byte count**. Coalesced per (source, burst id): sent when the count reaches the burst total `blen` carried by every `WR`, early with a partial count when the table is full, or after `ACK_FLUSH` idle cycles (see below). |
| anything else | `ERR`: write-add or max to HBM (`HBM_WADD`), a misaligned write (`HBM_ALIGN`), or an address in reserved HBM. |

**Write alignment *(P2)*:**
- HBM writes must be 32 B aligned in both address and size. The controller has no read-modify-write path, and the DFI-like interface
  has no data mask. Anything else faults with `HBM_ALIGN` (isa.yaml `fault_cause`).
- The compiler lays out data accordingly. Paged KV keeps each page's block scales in a scale region of the same page (memmap.md §6);
  a token's scale row can be smaller than 32 B (8 B for one 128-wide NVFP4 KV group, 4 B for a DeepSeek MXFP4 index key).
- **Tail-block rewrite *(review finding I-14)*:** per-token scale rows are stored at their natural stride, not padded. The compiler
  keeps the current 32 B block of scale rows (4 or 8 tokens) in SPM and rewrites the whole aligned block each step; the next block
  starts empty when the current one fills.
  - Padding each token's scales to 32 B instead would grow a DeepSeek index key from 68 B to 96 B (+41% of the index-scan bytes,
    and the scan is up to 12% of a step, arch.md §4). The rewrite costs 28 B of extra write per token per step, and no read bytes.
  - **Ordering is the program's job** *(corrected, review finding W-4 a)*. The controller keeps same-granule writes in arrival
    order (write after write, below), but the network does not keep issue order: planes are chosen per packet (memmap.md §9). Two
    writes of one block issued back to back can land in either order, and the older block, landing last, silently drops a
    token's scales. So two writes of one block are either one write, or the second is issued only after the first one's ack
    (its `ssem` completion):
    - several tokens of one sequence in one step (MTP, speculative decoding, chunked prefill): the SPM block copy takes all of
      them and the block is written once per step;
    - one write per step: the next step's rewrite waits on this one's completion semaphore. In decode that wait is normally
      already satisfied, because the next token depends on this step's attention, which waited for the write's `SIGNAL`. The
      compiler still emits it: a writer whose attention reads the current token from SPM has no such dependency;
    - more than one writing core: one owner core per block (the one that holds the SPM copy) issues every write of it.
  - The step's readers wait on the write's ack or semaphore (memmap.md §9). The earlier text said the rewrite was safe because
    queued writes merge; merging keeps arrival order, not issue order.
- The DMA splits writes at 256 B granule boundaries and merges adjacent runs into one packet, including consecutive rows of
  one page (review finding V-5). So a tail block of 4 or 8 scale rows is one 32 B write (memmap.md §6 worked example).

**Ordering and hazards inside a PC:**
- **Read after write:** a read descriptor is checked against the write queue (16-entry granule CAM). On a match, the read waits, and
  that write is promoted: it issues even in read mode.
  - That is enough because a write is acknowledged when queued. A reader ordered after the ack (memmap.md §9) always sees the data.
- **Write after write:** a write to a granule that already has a write queue entry that has not started issuing merges into it (data
  overwrite, mask OR). Otherwise it is queued behind that entry, and same-granule entries issue in order.
  - That order is arrival order at the controller. Two writes to the same bytes land in issue order only if the second is issued
    after the first one's ack (review finding W-4 a); the tail-block rewrite above is the case that needs it.
- Reads are not ordered among themselves.

**Partial acknowledgements *(P2, review findings R-3, X-3)*:**
- A target never needs ack-coalescing state in order to accept a write. If the coalescing table is full, the controller acks an entry
  early with the bytes counted so far.
- Without this rule, a full table of partially received bursts could block the packets that would complete them.
- **Completion trigger (X-3).** Every `WR` carries its burst's total bytes (`blen`, noc.md §2). An entry is acked as soon as its count
  reaches `blen`. An entry idle for `ACK_FLUSH` cycles (CSR `CTRL`, default 64) is acked with its partial count; that completes a
  burst whose first entry was evicted early, because its remaining packets can never reach `blen` in a new entry.
- The DMA counts acknowledged bytes per burst until they reach `blen`, so a burst may be acked in pieces.
- The same rules hold for SRAM targets (noc.md §2), which ack only once the write is visible.

**Head-of-line blocking:**
- A request whose 8 granules cannot all be queued holds its port input.
- Because the hash spreads granules evenly over PCs, a full PC queue is transient: H3 shows no saturation loss at depth 32.
- Per-PC input skid buffers are the fix if Phase 7 shows otherwise.

## H-3. PC scheduler *(P2)*

Every `clk_mc` cycle, each PC engine issues at most one column command (RD or WR, with or without auto-precharge) and at most one
row command (ACT, PRE, REFpb; REFab only in the rejected all-bank mode).

1. **Mode:**
   - **Read mode** by default.
   - It enters **write drain** when the write queue reaches 12 of 16 entries, or when the read queue is empty and writes are waiting.
   - It leaves write drain at 4 entries if reads are waiting, or when the write queue is empty.
   - A promoted write (read-after-write) issues in either mode.
2. **Column command, first ready first served (FR-FCFS):**
   - Among the current mode's entries whose bank has their row open and whose timing allows a burst now, the oldest issues one burst.
   - Bursts of different granules interleave freely, which is how the bus stays full under tCCD_L (H-6).
3. **Page policy, adaptive:**
   - The last burst of a granule carries auto-precharge **unless another queued entry (read or write) targets the same row**.
   - Rows therefore stay open while hits are queued, and close as soon as they are not.
4. **Row command, oldest first:**
   - ACT the bank of the oldest entry whose bank is closed.
   - Or PRE a bank whose open row has no queued hits and an entry waiting for another row.
5. **Age cap:**
   - An entry older than 1,024 cycles (0.85 µs) marks its bank *closing*: no new column commands to that bank's open row, and PRE as soon
     as legal.
   - Other banks continue. This bounds the latency of a row conflict under a stream of hits.
6. **Refresh, per bank (REFpb):**
   - Credit grows by one every tREFI / 48 (81 ns; half that above 85 °C, set by CSR `REF_RATE` from the stack temperature).
   - With credit, the engine refreshes a bank not yet refreshed in this round of 48. The bank must be closed and have no queued entries,
     and the refresh uses an idle row-command slot.
   - At 8 credits (8 postponed) refresh becomes urgent: the next bank in the round is closed and refreshed even if requests wait for it.

7. **Channel row-bus arbiter *(review finding X-2(b))*:**
   - The two PC engines of a channel decide in phase order each `clk_mc` cycle: PC 0 (CK phase 0) first, then PC 1.
   - A PC issues a row command only if the channel's row bus is free in its slot. An ACT of PC 0 in cycle t holds PC 1's slot in
     cycle t; an ACT of PC 1 in cycle t holds PC 0's slot in cycle t + 1. Commands of one CK never collide, because the phases
     differ. Column commands take one CK and need no arbitration (an (A) to confirm, H-9).
   - No PC starves: a PC issues at most one ACT per tRRD_S (3 cycles), so it blocks the other at most one slot in three.
   - Cost (H7): at most 0.001 of utilization on streaming, random and 64 B traffic.

The sequence is the reference for the RTL scheduler. `simulate()` is the executable form and is cycle-accurate to it;
`simulate_channel()` runs two PC schedulers with the step-7 arbiter on one `Channel` checker. The RTL may pipeline the selection over 2
cycles if it keeps one command per bus per cycle.

## H-4. Timing table

Representative HBM3 values, converted at `clk_mc` = 1.2 GHz (0.833 ns) and rounded up.
- **Source:** the Ramulator 2 `HBM3_6400Mbps` preset, which its authors mark as a guesstimate (nCK at 0.625 ns, kept in ns).
- **(A):** an assumption of this spec where no public value exists.
- JESD238 values replace the table when available. All entries are CSRs, so the RTL needs no change.

| Parameter | ns | cycles | Source |
|---|---|---|---|
| tRCDRD | 19.375 | 24 | Ramulator 2 |
| tRCDWR | 9.375 | 12 | Ramulator 2 |
| tRP | 16.25 | 20 | Ramulator 2 |
| tRAS | 28.125 | 34 | Ramulator 2 |
| tCL | 12.5 | 15 | Ramulator 2 |
| tCWL | 6.25 | 8 | Ramulator 2 |
| tWR | 20.625 | 25 | Ramulator 2 |
| tRTP | 5.625 | 7 | Ramulator 2 |
| tRRDS | 2.5 | 3 | Ramulator 2 |
| tRRDL | 3.125 | 4 | Ramulator 2 |
| tFAW | 15 | 18 | Ramulator 2 (4 activates per PC, REFpb counts) |
| tWTRS | 4.375 | 6 | Ramulator 2 |
| tWTRL | 6.25 | 8 | Ramulator 2 |
| tCCDL | 2.5 | 3 | (A) 4 nCK at 6.4 Gb/s, kept in ns |
| tRREFD | 8 | 10 | (A) REFpb to REFpb in one PC |
| tRREFDA | 8 | 10 | (A) REFpb to ACT of another bank in one PC, assumed = tRREFD (review I-9 b; H-9) |
| tRFCPB | 220 | 264 | (A) 24 Gb die |
| tRFCAB | 410 | 492 | (A) 24 Gb die, DDR5 class |
| tREFI | 3900 | 4680 | JEDEC convention; halved above 85 °C |

Fixed and derived values (cycles):

| Name | Cycles | Definition |
|---|---|---|
| BL, CCDS | 1 | one BL8 burst at 9.6 Gb/s = 0.833 ns |
| CCDR | 2 | (A) column to column across SIDs |
| PHY_RD | 10 | (A) PHY read path, added to CL |
| RC | 54 | tRAS + tRP |
| RTW | 9 | CL + BL + 1 - CWL (read to write, 1 cycle bubble) |
| WTR_S / WTR_L | 15 / 17 | CWL + BL + tWTR_S / tWTR_L |
| WRP | 34 | CWL + BL + tWR (write to precharge) |
| REFIPB | 97.5 | tREFI / 48 banks |
| ACT_CK | 2 CK | (A) CK slots an ACT occupies on the channel's row bus; every other command 1 (review X-2(b); H-9) |

**Rule table** (`hbm.rules()`):
- The checker enforces, for every pair (previous command, next command, scope), a minimum gap.
- Scopes:
  - same bank;
  - same bank group (same SID);
  - same SID;
  - other SID;
  - whole PC;
  - channel (the row bus shared by two PCs; the gap is in CK slots, PC p in slot 2t + p).

| Next | Previous -> gap |
|---|---|
| ACT | ACT: RC (bank), RRDL (bank group), RRDS (PC); PRE or PREA: RP (bank); REFpb: RFCPB (bank), RRDL (bank group), RREFDA (PC); REFab: RFCAB; at most 4 ACT/REFpb per tFAW |
| RD | ACT: RCDRD (bank); RD: CCDL (bank group), CCDS (SID), CCDR (other SID); WR: WTR_L (bank group), WTR_S (PC) |
| WR | ACT: RCDWR (bank); WR: CCDL / CCDS / CCDR as for RD; RD: RTW (PC) |
| PRE | ACT: RAS; RD: RTP; WR: WRP (all same bank). Auto-precharge happens at max(RD + RTP or WR + WRP, ACT + RAS) |
| PREA | the PRE rules for every bank of the PC (RAS, RTP, WRP); closes every bank, and RP then applies to each |
| REFpb | PRE or PREA: RP (bank); REFpb: RFCPB (same bank, also across a round boundary), RREFD (PC); ACT: RRDL (bank group), RRDS (PC); REFab: RFCAB; counts toward tFAW |
| REFab | PRE or PREA: RP (every bank); REFpb: RFCPB; REFab: RFCAB |
| any row command | ACT on either PC of the channel: ACT_CK CK slots (channel) |

Review findings I-9 and X-1 added PREA, REFpb → ACT of any bank (RREFDA), REFpb → REFpb of the same bank (RFCPB), REFab → REFab
(RFCAB) and the channel rule. Four rules are redundant under this table and kept because a different device table could make them
bind: RC (= RAS + RP), CCDS for RD and WR (one column command per cycle), and REFpb → ACT in the bank group (RRDL ≤ RREFDA).

State rules:
- ACT only to a closed bank. RD/WR only to the open row. REFpb only to a closed bank, and each bank once per round of 48. REFab only
  with every bank closed. PREA closes every bank.
- One row and one column command per cycle.
- At most 8 REFpb behind schedule.
- Any other command (power-down, self-refresh, MRS, ZQ, ECS, ...) is rejected: the model does not cover it, so the checker must not
  accept it silently.
- Not checked: tRAS maximum and the refresh pull-in limit. The scheduler bounds both (refresh closes every bank once per round).

## H-5. DFI-like boundary and the behavioral DRAM model

The boundary is per PC, at `clk_mc`. It is "DFI-like": one row and one column command slot per cycle, write data presented with the
command, and read data returned at a fixed latency.
- The PHY model applies tCWL and the burst timing internally.
- Training, frequency change and low-power states are not modeled.

| Signal | Dir | Width | Meaning |
|---|---|---|---|
| `row_cmd` | ctrl -> phy | 3 | NOP, ACT, PRE, PREA, REFPB, REFAB |
| `row_sid`, `row_bg`, `row_ba` | ctrl -> phy | 2, 2, 2 | bank of the row command |
| `row_addr` | ctrl -> phy | `ROW_BITS` = 15 + log2(32 / PCS): 15 (`FULL`), 17 (`MID`), 18 (`TINY`) | row (ACT), extended by the PC fold (H-1) |
| `col_cmd` | ctrl -> phy | 2 | NOP, RD, WR |
| `col_ap` | ctrl -> phy | 1 | auto-precharge |
| `col_sid`, `col_bg`, `col_ba` | ctrl -> phy | 2, 2, 2 | bank of the column command |
| `col_addr` | ctrl -> phy | 5 | burst within the 1 KiB row |
| `wr_data` | ctrl -> phy | 256 | the burst, valid with `col_cmd = WR` |
| `rd_data`, `rd_valid` | phy -> ctrl | 256, 1 | the burst, `CL + PHY_RD` cycles after its RD, in command order |
| `init_done` | phy -> ctrl | 1 | PHY ready (behavioral: after a parameterized delay) |

The behavioral DRAM model (`tb/models/hbm_pc_model.sv`, testbench only, Phase 4) contains:
- bank state and sparse storage, an associative array keyed by (SID, bank group, bank, row, column);
- **the timing checker**: the H-4 rule table and state rules, per PC, plus the channel-scope rule over the two PCs of a channel. It
  reports a violation with `$error` and counts them, and the regression requires zero;
- the refresh-lag check;
- parameters: `t_*` (default: H-4), `PHY_RD`, and an optional random read-data delay jitter that stays within the fixed-latency
  contract and is off by default.

`hbm.Dram` and `hbm.Channel` are its reference. `tb/unit/test_hbm.py` has, for every rule in `hbm.rules()`, one sequence that violates
it by one cycle and one that meets it exactly, plus the state rules. A rule-removal (mutation) test deletes each rule in turn and
requires a sequence to start passing; only the four redundant rules above may be deleted unnoticed, and the test checks that they
are redundant (review finding X-1). Phase 4 runs the same sequences against the SV model, and the same mutation test against its
rule table.

## H-6. Results ([hbm_experiments.md](../reports/hbm_experiments.md))

The frozen roofline assumes 0.85 sustained HBM efficiency (arch.md). The controller alone, on one PC, reaches:

| Pattern | Utilization | Comment |
|---|---|---|
| sequential streams (decode weights, KV scans) | 0.99 | 1 ACT per 29 bursts |
| + 5% random 256 B writes (KV appends) | 0.95 | |
| reads : writes 3 : 1 | 0.90 | prefill; compute-bound anyway |
| random 256 B granules | 0.97 | paged KV, sparse attention blocks |
| random 264 B rows (Engram) | 0.89 bus, 0.82 useful | 0.917 of the bytes moved are row bytes |
| random 64 B | 0.40 | bound by tFAW and tRREFDA: avoid; the DMA gathers whole rows |

The 0.85 frozen value therefore keeps 0.10-0.14 margin for what this model leaves out:
- load imbalance across PCs;
- PHY retraining and ZQ calibration;
- head-of-line blocking at the port;
- the NoC.

Decisions this evidence supports:
- **Per-bank refresh *(P2)*:** all-bank refresh costs 12% (normal) and 25% (above 85 °C) of streaming bandwidth (H2).
- **Adaptive page policy *(P2)*:** best or tied on every pattern. Open page loses up to 0.06 to row conflicts; close page loses up to 0.19 on
  mixed traffic (H1).
- **Queue depths *(P2)*:**
  - read 32: 16 loses 9-11%;
  - write 16: 32 gains 1% on decode traffic and 5% on 3:1 traffic, at twice the buffer.
  - Read return buffer 16 granules: at most 11 are in transfer at once (H3).
- **Latency:**
  - At 85% load, a granule waits 133 ns on average and 367 ns at p99 from queue entry to the last burst out of the PHY (H4).
  - With about 40 ns of NoC, CDC and response overhead each way, the loaded round trip is about 640 core cycles at p99.
  - The DMA's 512 granules in flight cover 102 B/cycle x 1,285 cycles, which is 2x margin (microarch.md §2.5).

**Device requirement found here: tCCD_L ≤ 2.5 ns (3 cycles); 3.3 ns is the limit.**
- The bus is full only if the scheduler can rotate over enough bank groups: one SID offers 4, and switching SID costs CCDR.
- H5:
  - at 2.5 ns, 0.97-0.99;
  - at 3.3 ns (4 cycles), random granules drop to 0.86;
  - at 4.1 ns (5 cycles), everything drops to 0.77-0.79 whatever the queue depth: the ceiling is 4 bank groups / 5 cycles.
- The vendor HBM3e datasheet value must be checked against this before tape-out. If it fails, the fallback is an address-map change
  that alternates SIDs inside a granule stream, so that CCDR replaces tCCD_L on consecutive bursts.

## H-7. Interfaces

**HBM controller (`hbm/ctrl`)**, one per stack:

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| `noc[4]` | in/out | flit_t (2 planes) | credit per VC | one per leaf router; 8 PCs each |
| `dfi[32]` | out/in | H-5 signals | fixed-latency, one row + one column slot per cycle | PHY + DRAM |
| `csr` | in/out | 64 | valid/ready | DIECTL block 2 + stack |
| `irq` | out | 1 | level | error (ERR response sent, refresh lag) to the CP |
| `clk`, `clk_mc`, `rst_n` | in | | | `MC_SYNC = 1`: `clk_mc` = `clk` |

**PC engine (`hbm/pc`)**, inside the controller:

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| `rd_desc` | in | 64 | valid/ready | read granule descriptor (bank, row, col_hi, first burst, count, tag) |
| `wr_desc` + `wr_data` | in | 64 + 256 B + 8 | valid/ready | write granule, burst mask |
| `rd_done` | out | tag + 256 B | valid/ready | completed granule to the response packer |
| `chan_row` | in/out | busy-next-slot, 1 each way | level | to the other PC engine of the channel: an ACT here holds its next slot (H-3 step 7) |
| `wr_acc` | out | source, burst id, bytes | pulse | write accepted (to the ack coalescer) |
| `dfi` | out/in | H-5 | | |
| `cfg` | in | timing table + policy | static | from CSRs |

**CSRs (DIECTL blocks 2-5, one per stack)**

| Offset | Name | Contents |
|---|---|---|
| `0x000-0x0FF` | `TIMING[n]` | H-4 entries in cycles, 16 bits each, in table order |
| `0x100` | `CTRL` | page policy (adaptive/open/close), refresh mode (pb/ab), queue watermarks, age cap, `ACK_FLUSH` |
| `0x108` | `REF_RATE` | 1x / 2x refresh (temperature) |
| `0x110` | `STATUS` | init done, error flags, refresh lag maximum |
| `0x200-0x5FF` | `PC_CNT[32]` | per PC: read bursts, write bursts, ACTs, row hits, refreshes, max queue occupancy |

## H-8. Decisions and alternatives

| Decision | Alternative rejected | Reason |
|---|---|---|
| Controller at CK/2 with a DFI-like 1:2 boundary, async FIFOs to the NoC | controller at the 1.5 GHz NoC clock | one burst per PC per cycle exactly; timing table in whole cycles |
| Granule (8 bursts, one row) as the queue unit, burst-level interleaving | 32 B requests | 8x fewer queue entries and CAM compares; interleaving keeps tCCD_L hidden |
| FR-FCFS + age cap + adaptive page | strict FCFS; open / close page | H1: FCFS cannot hide tRCD; adaptive is best or tied everywhere |
| Per-bank refresh with postponement | all-bank | H2: 12-25% bandwidth |
| Posted writes, acked at the write queue, RAW check against the write queue | ack after the DRAM write | ack latency stays short; the CAM is 16 entries per PC |
| No masked writes; 32 B aligned HBM writes, else `HBM_ALIGN` | read-modify-write in the controller | a hazard-prone RMW path for a case the compiler can avoid by layout |
| Partial (byte-count) write acks | strict one ack per 2 KiB burst | a strict coalescing table can deadlock the request network |

## H-9. Open items

- Replace the H-4 timing values with JESD238 / vendor HBM3e values. Re-run `make hbm`. Confirm tCCD_L ≤ 2.5 ns (H-6).
  **JESD238 checklist** (review finding X-2; each is a timing-table parameter or rule, so the answer changes values, not the RTL):
  - (a) Does tRREFD also govern REFpb → ACT of another bank (`tRREFDA`, assumed equal), and is there a same-bank-group term?
  - (b) How many CK an ACT and the other row and column commands occupy on the channel's shared buses (`ACT_CK` = 2 assumed, others 1).
  - (c) Is there a separate write-to-write same-bank-group gap (DDR5 has tCCD_L_WR for on-die ECC)? If so, split CCDL for WR → WR.
  - (d) The scope of tFAW and tRRD with 3 SIDs: per PC (assumed) or per SID.
  - (e) Does tPPD (PRE → PRE) exist? If so, add a PRE / PREA → PRE / PREA rule.
- Host-side ECC: v1 relies on the HBM3 on-die ECC, and the interface carries no ECC bits. To be decided with the RAS requirements.
- PHY training, ZQ calibration and ECS (error check and scrub) intervals: account for them in the perf simulator (Phase 7) as a fixed
  bandwidth tax.
- Port head-of-line blocking and the 4-port split under real traffic: Phase 7 perf simulator.
- **Fairness per initiator *(review finding Y-4)*.** FR-FCFS shares a PC in proportion to the bytes queued, so a core with a
  deep plain backlog takes about twice the share of a converting core whose ring and pace cap its queue (microarch.md §2.5).
  Phase 7 measures it with 8 cores and mixed traffic. If it matters, the fix is byte-weighted round-robin over initiators
  in step 2's choice of the oldest entry; plain round-robin per request hurts the KV gather, whose 32 B scale reads would
  count as whole turns.
