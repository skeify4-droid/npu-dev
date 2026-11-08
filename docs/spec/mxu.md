# MXU (`rtl/core/mxu*.sv`)

Status: **Phase 3, 2026-11-08: built and verified at `DIM` 8, 16 and (one decode instruction) 32** (`mxu_array`, `mxu_wpack`, `mxu`; below). `BF16X3` and the formal properties are open. Inputs: [microarch.md](microarch.md) §2.2 and §5, [numerics.md](numerics.md) N-3,
[arith.md](arith.md) "Integration contract" items 1-5, [isa.md](isa.md) §4-5 and `MXU_MM` in [isa_ref.md](isa_ref.md).
Golden model: `numerics.mxu_mm` (the array and the ACC input together); `arith_ref` for the bit-exact PE chain.

## 1. Blocks

```
 MQ ─► mxu (sequencer) ──► A requests ─► SPM a_rd (A_LANES x LB B) ─► mask K tail ─┐
        │  decode, checks   W requests ─► SPM w_rd (8 x LB B) ─► mxu_wpack ─► window write ─┤
        │  tile loop (j, t) scale reqs ─► SPM scale_rd (LB B) ─► cs FIFO / rowscale FIFO     │
        │                                                                                    ▼
        │                                         mxu_array (ROWS x COLS mxu_pe, shadow + active weights, A skew)
        ▼                                                                                    │ y (COLS FP32), flags
   sem_inc, fault                                              ACC input stream: row, part, cs, rowscale, controls
```

| Block | Contents | Status |
|---|---|---|
| `mxu_array` | PE grid, active + shadow stationary words, A skew, swap skew, sideband chain (valid, mode, ncols, metadata), flag qualification | built, `tb_mxu_array` |
| `mxu_wpack` | SPM lane data of one load beat -> 128-word window, zeroing of K tail / N tail, BF16 / FP8 and trans / non-trans byte placement | built (checked through `tb_mxu`) |
| `mxu` | queue head, wait, DTAB reads, legality checks, load FSM (weights + column scales), stream FSM (A rows, rowscale), exit stage (ACC stream, completion), the three SPM ports | built, `tb_mxu` |

## 2. Geometry and parameters

One parameter `DIM` (= PE rows = PE columns; `FULL` 64, `MID` 16, `TINY` 8). **Lane width `LB = DIM` bytes**: the SPM client lane
of the MXU ports is `DIM` bytes (64 B in `FULL`, the SPM bank width). Scaling the lane with `DIM` keeps every structure the same
in all configs: 4 load groups, `DIM/2` beats per tile, no sub-lane alignment cases. A `TINY` / `MID` SPM therefore has `DIM`-byte
banks (core/spm parameter).

| Quantity | Formula | `FULL` | `MID` | `TINY` |
|---|---|---|---|---|
| K tile, FP8 / BF16 (elements) | `4·DIM` / `2·DIM` | 256 / 128 | 64 / 32 | 32 / 16 |
| K tile bytes `KB` (both) | `4·DIM` | 256 | 64 | 32 |
| Stationary tile bytes | `4·DIM·DIM` | 16 KiB | 1 KiB | 256 B |
| A lanes `A_LANES` | `4` (`KB/LB`) | 4 | 4 | 4 |
| Load group rows `GRP` | `DIM/4` | 16 | 4 | 2 |
| Load groups `NG` | 4 | 4 | 4 | 4 |
| Beats per group, per tile | `DIM/8`, `DIM/2` | 8, 32 | 2, 8 | 1, 4 |
| W lanes per beat | 8 | 8 x 64 B = 512 B | 8 x 16 B | 8 x 8 B |
| Array latency `ARR_LAT` | `DIM + 2` | 66 | 18 | 10 |

Other parameters: `SPM_LAT` (fixed read latency of the three SPM ports, default 2), `ACC_LAT` (the ACC's read-modify-write
round trip, default 8), `CS_DEPTH` (column-scale FIFO, default 4), `ACC_ROWS` (2048, 256 / 512 in `TINY` / `MID`).

## 3. The array (`mxu_array`)

**Dataflow.** PE row `i` holds K elements `4i..4i+3` (FP8) or `2i, 2i+1` (BF16) of the tile as one 32-bit word per PE; PE column
`j` is output column `j`. An A row (`ROWS` words) enters once per cycle; word `i` is delayed `i + 1` cycles (the A skew), is
broadcast along PE row `i` and meets the partial sum of the PE above one cycle later (`mxu_pe`, `REG_DOT = REG_OUT = 1`, arith.md
contract item 4). The top row's `psum_in` is +0. The partial sum leaves the bottom row as `COLS` FP32 values, all columns in the
same cycle (A is broadcast, so there is no output de-skew).

**Timing** (`T` = cycle of `a_valid` at the array input): PE row `i` sees word `i` in cycle `T + 1 + i`; `y`, `y_valid`,
`y_meta` appear in cycle `T + ROWS + 2 = ARR_LAT`. Rows may be issued with bubbles; every row is an independent wave.

**Stationary weights: two shadow buffers and an active word.** Each PE has an active word and two shadow words (buffers 0 and 1).
The controller alternates the buffers tile by tile. `swap` in cycle `T` (with the first A row of a new tile, same cycle; `swap_buf`
names the shadow to copy) makes that shadow the active word at PE row `i` at the end of cycle `T + i`: the rows of the previous tile,
which are ahead in the skew, still see the old word. This per-row skew lets a tile follow its predecessor with no bubble.
`swap_tap[b·NG + g]` pulses in the cycle in which a swap from buffer `b` copies the last row of group `g` (cycle
`T + g·GRP + GRP - 1`); from then on (including that cycle) group `g` of buffer `b` may be rewritten. The window write names the
buffer (`wl_buf`).

**Window write.** One 128-word window per cycle, `wl_trans` selects the shape (the packer builds both):

| `wl_trans` | Words | PE |
|---|---|---|
| 1 (`trans_w = 1`: HF `[out, in]`, K-cache) | `(c % 8)·16 + r` | row `g·GRP + r`, column `8·wl_idx + c` (8 columns x `GRP` rows; one SPM lane per column) |
| 0 (`[K, N]`) | `(r % 2)·COLS + c` | row `g·GRP + 2·wl_idx + r`, column `c` (2 rows x `COLS` columns; 8 FP8 k-rows or 4 BF16 k-rows) |

`g = wl_grp`; `wl_idx` runs `0..DIM/8-1` (trans) or `0..GRP/2-1` (non-trans); both are `DIM/8` beats per group.

**Why groups and two buffers.** The swap reaches PE row `i` `i` cycles after row 0, so a buffer is only free group by group
(`swap_tap`). With one shadow buffer the next tile's load had to wait for that, and the tile period at `M = 1` was `1.25·DIM + 9`
(measured: 25 cycles at `DIM = 16`, i.e. about 80 for `FULL`). With two, tile `n + 1` is written into the buffer that tile
`n - 1` swapped from, and the wait for the skew only constrains tile `n + 2` against tile `n`: `2·P >= (DIM - 1) + beats per group +
latency`, about 40 cycles per tile for `FULL` (§7). The groups remain, so that a buffer is reused as soon as its first rows are free.

**Sideband.** One chain of `ARR_LAT` stages carries valid, mode (FP8 / BF16), the column count `ncols` of the pass and a
`META_W`-bit tag per row, so the mode of consecutive passes can differ and the controller can tag every output row.

**Flags** (arith.md contract items 1-3): the PE flags of row `i` are ANDed with the valid of the wave at the cycle the PE reports
them (`T + i + 3`) and with the column mask `j < ncols`, ORed over rows and columns. All flags of a wave are out no later than
its row. After reset the valid chain, the swap chain and the PE flag registers are clear, so nothing is reported until real
data has propagated (contract item 2). Data registers have no reset.

**Unused operands** (contract item 3): the packer writes zero words for K lanes past `K` and columns past `N`; the A path
zeroes bytes past `K`; both sides are +0, because NaN x 0 and Inf x 0 flag.

## 4. Instruction `MXU_MM`

### 4.1 Operands and legality
All of this is checked once, before the first SPM access; a failure raises `fault` (cause below) and the MXU stops taking
instructions (the core stops, isa.md §8).

| Item | Rule | Fault |
|---|---|---|
| A, W TD space | `SPM` | `TD_SPACE` |
| dtype, `mode = NORMAL` | A and W both `E4M3` (FP8 mode) or both `BF16` (BF16 mode) | `ILLEGAL_FIELD` |
| `mode = BF16X3` | **not in the first RTL version** (§9) | `ILLEGAL_FIELD` |
| A shape | `n0 = K >= 1`, `n1 = M` (`M = R[m_reg]` when `m_reg != 0`), `n2 = n3 = 1`, no flags | `ILLEGAL_FIELD` |
| W shape | `trans_w = 1`: `n0 = K`, `n1 = N`; `trans_w = 0`: `n0 = N`, `n1 = K`; `n2 = n3 = 1`, no flags | `ILLEGAL_FIELD` |
| K agrees | K of A = K of W | `ILLEGAL_FIELD` |
| `1 <= M`, `1 <= N` | | `ILLEGAL_FIELD` |
| Alignment | A base, W base, `s1` of both, `cs_reg` and `rs_reg` addresses: multiples of `LB` (64 B in `FULL`; memmap.md §4); `s1 >= 0`; with `cs_reg != 0`, `4·N` a multiple of `LB` (the `[KT x N]` scale rows start on a lane; found by `tb_mxu`; the DMA pads `N` when it writes the scales) | `ILLEGAL_FIELD` |
| ACC range | `R[acc_reg] + NT·M <= ACC_ROWS`, `NT = ceil(N / DIM)` | `RANGE` |
| SPM range | every byte the instruction reads is inside the SPM | `RANGE` |
| `acc_mode = RESCALE` | `rs_reg != 0` | `ILLEGAL_FIELD` |
| `acc_mode` | 3 is reserved | `ILLEGAL_FIELD` |

`FAULT_ADDR` holds the offending operand's address (or the ACC row for `RANGE`).

### 4.2 Loop order and what a pass does
For `j` in `0..NT-1` (N tile), for `t` in `0..KT-1` (K tile, `KT = ceil(K / KTILE)`, ascending K, numerics.md N-3 step 5):
a *pass* loads the stationary tile `(t, j)`, streams the `M` rows of A's K slice `t`, and adds the partial sums to ACC rows
`R[acc_reg] + j·M + m`. N is the outer loop so that the `KT` passes that update the same ACC rows follow each other (the
cross-pass dependency is checked in §4.4); numerics.md fixes only the order within an output element, which this loop keeps.

Per output row, at the ACC input (arith.md contract item 5; the ACC block owns the `acc_update` lane):

| Pass | `acc_mode` | `rescale` | `overwrite` | `use_cs` |
|---|---|---|---|---|
| `t = 0` | `OVERWRITE` | 0 | 1 | `cs_reg != 0` |
| `t = 0` | `ACCUM` | 0 | 0 | `cs_reg != 0` |
| `t = 0` | `RESCALE` | 1 (with `rowscale[m]`) | 0 | `cs_reg != 0` |
| `t > 0` | any | 0 | 0 | `cs_reg != 0` |

Column scales: `cs` is FP32 `[KT x N]` at `SPM[R[cs_reg]]`, row-major; the pass `(t, j)` uses the `DIM` values at
`R[cs_reg] + 4·(t·N + DIM·j)`, constant over the `M` rows. Rowscale: FP32 `[M]` at `SPM[R[rs_reg]]`, re-read for every N
tile. Both are read through `scale_rd`.

### 4.3 Stationary tile fetch (`w_rd`)
Per pass, `NG x DIM/8` beats, group-major. Each beat requests 8 lanes (`LB` bytes each, independent addresses, granted together):

- **trans (`trans_w = 1`)**: lane `l` = column `c = j·DIM + 8·idx + l`, bytes `[t·KB + g·LB, +LB)` of W row `c`
  (`base + c·s1 + t·KB + g·LB`). A lane is invalid (and its words zero) for `c >= N`; bytes past `K·eb - t·KB` are zeroed
  (`eb` = 1 FP8, 2 BF16).
- **non-trans (`trans_w = 0`)**: FP8: lane `l` = k-row `ke0 + 8·(g·GRP/2 + idx) + l`, bytes `[j·DIM, +DIM)` of that row
  (`base + kr·s1 + j·DIM`). BF16: 4 k-rows of `2·DIM` bytes = 2 lanes each; lane `2·kk + h` = k-row `ke0 + 4·(g·GRP/2 + idx) + kk`,
  bytes `[2·j·DIM + h·LB, +LB)`. `ke0 = t·KTILE`. K-rows `>= K` and columns `>= N` are zeroed.
  The word of PE row `i`, column `c` is `{k-row 4i+3, 4i+2, 4i+1, 4i}` at byte `c` (FP8) or `{k-row 2i+1, 2i}` at element `c`
  (BF16), low lane first (arith.md "The MXU PE").

Beats of a group of the buffer being loaded (`l_buf`, toggled per tile) may start only when `free[g]` is set; `free[b][g]` clears at the first beat of a group in buffer `b` and sets on `swap_tap[b·NG + g]`; only the first beat of a group waits for it.
The pass's column scales are fetched in the same step (4 lanes, `cs` FIFO slot reserved first).

### 4.4 A stream (`a_rd`) and the dependency between passes
One request per row: `A_LANES` lanes of `LB` bytes, lane `l` at `a_base + m·s1 + t·KB + l·LB`, lane valid for `l·LB < K bytes
left in tile t`; bytes past the tile's K are zeroed in the response path. The swap pulses with the first row of a pass.

A pass may issue its first row when (a) its shadow tile is complete (the load FSM has pushed its token), (b) at least
`max(0, ACC_LAT - M)` idle cycles have passed since the last row of the previous pass was issued (and any `SPM`-grant stall of
that pass has only lengthened the gap): this makes every ACC row see its next read-modify-write at least `ACC_LAT` cycles after
the previous one, whatever the stalls, so the RMW needs no hazard logic (proof: row `m` of the next pass issues at least
`(M-1-m)` cycles after row `m` of the previous one by stream order, plus the gap, plus the first-row slack). With the default
`ACC_LAT = 8` this costs nothing: the load of a tile takes `DIM/2` cycles at least, and the swap-to-swap period is that long.

### 4.5 Exit stage and completion
Every row leaves the array with its tag `{acc_row, first_of_pass, last_of_pass, last_of_instr, rescale, overwrite, use_cs}`.
The exit stage drives `acc_*` (below), pops the pass's `cs` entry from the FIFO on `first_of_pass` and the lane of the rowscale
FIFO as rows consume it, and ORs the array flags into `fp_flags`. `acc_done` (from the ACC, the cycle the last row's write is
visible) pops the in-flight instruction FIFO (depth 4) and pulses `sem_inc {ssem, sinc}` (isa.md §3: "MXU: ACC written").
Instructions are in order; the next instruction's weight prefetch overlaps the previous one's drain.

## 5. Interfaces (`mxu`, FULL widths)

All ports are packed vectors (CLAUDE.md). `valid/ready` unless marked.

| Port | Dir | Width | Purpose |
|---|---|---|---|
| `q_valid`, `q_ready` | in, out | 1 | MQ head |
| `q_instr` | in | 128 | `MXU_MM` word (isa_pkg field positions; `T_MXU_*`, sync block) |
| `q_regs` | in | 6 x 64 | captured `R[a_reg]`, `R[w_reg]`, `R[acc_reg]`, `R[m_reg]`, `R[rs_reg]`, `R[cs_reg]` (field order of `T_MXU`) |
| `q_wval` | in | 32 | captured wait value `R[wreg] + wimm` |
| `sem_rd_idx`, `sem_rd_val` | out, in | 6, 32 | combinational read of `sem[wsem]`; start when `wsem == 0` or `(int32)(val - wval) >= 0` |
| `dtab_req`, `dtab_idx`, `dtab_gnt`, `dtab_rvalid`, `dtab_data` | | 1, 8, 1, 1, 256 | request/grant; data the cycle after the grant (microarch.md §5) |
| `a_req_*` | out | `A_LANES` x (valid, 22-bit addr), `ready` | one grant takes all valid lanes |
| `a_rsp_data` | in | `A_LANES · 8·LB` | exactly `SPM_LAT` cycles after the grant, never back-pressured |
| `w_req_*`, `w_rsp_data` | | 8 lanes | same, stationary load |
| `s_req_*`, `s_rsp_data` | | 1 lane | same, rowscale and column scales (two sources, rowscale first) |
| `acc_valid` | out | 1 | an output row; no `ready`: the ACC accepts one MXU row per cycle (MXU has priority over the VPU port, microarch.md §5 `conflict`) |
| `acc_row` | out | 11 | row index |
| `acc_part` | out | `COLS · 32` | partial sum row |
| `acc_ncols` | out | 7 | columns `< N` of this N tile; the ACC writes and flags only those |
| `acc_rescale`, `acc_overwrite`, `acc_use_cs` | out | 1 each | lane controls (§4.2) |
| `acc_rowscale` | out | 32 | `rowscale[m]` (valid with `acc_rescale`) |
| `acc_cs` | out | `COLS · 32` | column scales of the pass; changes only with the first row of a pass; the ACC latches it with the row |
| `acc_last` | out | 1 | last row of the instruction |
| `acc_done` | in | 1 | pulse: the write of the `acc_last` row is visible |
| `sem_inc_valid`, `sem_inc_idx`, `sem_inc_amt` | out | 1, 6, 4 | completion |
| `fp_flags` | out | 4 | valid-qualified `mxu_pe` flags (microarch.md §5) |
| `fault_valid`, `fault_cause`, `fault_addr` | out | 1, 4, 32 | |

The ACC (`core/acc`) instantiates `acc_update` (arith.md): this interface fixes what it must accept. `acc_cs` is `COLS · 32` wide
and held; if the ACC pipeline is deeper than its latch, it copies `acc_cs` into its own stage registers.

## 6. Verification

| Test | Covers | Status |
|---|---|---|
| `tb_mxu_array` (iverilog; `scripts/gen_mxu_vectors.py`) | 8 x 8 and 16 x 16 arrays against an independent model (window mapping, swap copy, PE chain with `arith_ref`): back-to-back tiles, bubbles, FP8 / BF16 on consecutive waves, trans / non-trans windows, shadow writes in the tap cycle, NaN / Inf / overflow, column mask on flags, reset, `swap_tap` timing every cycle | **PASS**, 17 mutants (swap / write buffer select, per-buffer taps, skew, column mask, mappings, resets), all killed |
| `tb_mxu` | whole instructions, 6 per size, on `DIM` 8 and 16, each with and without 30% random SPM grant stalls (and `ACC_LAT` 24 in one): behavioural SPM (fixed latency; NaN-valued garbage in every byte nobody requested or that lies past K / N), DTAB, semaphores, and an ACC built from the RTL's `acc_update`. The expected ACC image comes from the **logical** `A x W` (`arith_ref` PE chain per K tile, then `acc_update`), bit-exact over all 256 rows. FP8 / BF16, `trans_w` 0 / 1, K / N / M not multiples of the tiles, M = 1, M from `m_reg` (decoy in the TD), `OVERWRITE` / `ACCUM` (onto the previous instruction's rows) / `RESCALE`, column scales, rowscale with a partial last lane and 2 N tiles, specials; checks every lane address (aligned, inside the SPM), the ACC row spacing, an instruction starts only after its semaphore wait, final semaphore values and OR of `fp_flags`. Also: one `M = 1` decode instruction on 32 x 32 (bit-exact; tile period checked <= 26 cycles), and an 8 x 8 run where the A port refuses requests for 40 cycles after each grant, so the loader runs ahead of the swap and the buffer interlock is exercised. Plus 29 illegal instructions after a reset each (cause, no SPM / ACC / semaphore action, queue stops) | **PASS**, 21 mutants: 17 killed, 4 equivalent (`ncols_pack`, `lane_valid_k`, `ncols_flag` and `no_gap` in the quick mode are not visible at the ACC (the last one is killed by the `ACC_LAT = 24` run): the extra column or lane is masked there; the array test kills the column mask) |
| formal | no lost / duplicated window beat, `free[g]` never allows a write that precedes the swap of its group, the ACC row spacing of §4.4 | later |

Bit-exactness: the array output equals `numerics.mxu_mm` for one K tile (`test_arith.py` ties `arith_ref`'s PE chain to it); the top-level
test compares the ACC image after the `acc_update` lanes with `mxu_mm(A, W, mode, cs, acc, rowscale)`. Tiles with an MX scale below
2^-126 are exempt (numerics.md N-8).

## 7. Performance (estimates; `tb_mxu` measures them)

- Tile load: `DIM/2` beats (32 cycles `FULL`) at one beat per cycle, 512 B/cycle.
- Pass period at small `M`, **measured** (`tb_mxu`, M = 1 passes, steady state): **21-22 cycles per tile at `DIM = 32`** (16 beats; a
  port-bound period would be 16-17). Two shadow buffers (M-9) replaced the single one, which gave `1.25·DIM + 9` (25 cycles at
  `DIM = 16`, 49 predicted at 32). What remains is the skew of tile `n` against the load of tile `n + 2` into the same buffer:
  `P >= ((DIM - 1) + DIM/8 + k) / 2` with `k = 8` fitted to the 32 x 32 point. **Extrapolation to `FULL` (not simulated): about 39
  cycles**, i.e. 16 KiB per 39 cycles = 420 B/cycle of FP8 weights, 2.2x the 193 B/cycle decode need (microarch.md §6; the port
  limit is 32 cycles, 512 B/cycle). A regression check (`MAX_PERIOD = 26` on the 32 x 32 decode instruction) is in `tb_mxu`.
- Large `M` (prefill): one A row per cycle, the load hides behind `M >= 32`, the period is `M`.
- Latency of an `M`-row pass from the first A grant to the last ACC write: `SPM_LAT + ARR_LAT + M + ACC_LAT`.

**SPM bank conflicts of the load beats** (checked with `isa.spm_bank`): the group-ordered trans beat reads one lane from each of
8 consecutive W rows. At a W pitch of 2 KiB + 512 B (the padded 2 KiB, memmap §4) it has one bank collision per beat, as does the
8-k-row non-trans beat at 2.5 KiB, 640 B and 576 B pitches (V-8 verified only the two-row 256 B shape). The cost is a stall in
that beat; the compiler may prefer 4 KiB + 512 B pitches (conflict-free for both shapes).

## 8. Decisions

| # | Decision | Why |
|---|---|---|
| M-1 | A is broadcast along a PE row; no output de-skew, no per-column delay | one wave per cycle, columns aligned at the ACC. A 64-way fan-out is a Phase 6 buffering question; a register every 8 columns with a de-skew is the fallback |
| M-2 | A skew in `mxu_array` as a triangular register set (`ROWS(ROWS+1)/2` words), not by staggering the SPM reads | keeps the A read one contiguous `KB` bytes (conflict-free); the 64k flops are 1.5% of the PE array's arithmetic (arith.md area) |
| M-3 | Swap skewed with the A skew; shadow load by group | removes the `ROWS`-cycle bubble between tiles and the `1.5·ROWS` period |
| M-4 | Lane width = `DIM` bytes | one design for all configs (§2) |
| M-5 | N outer, K inner | consecutive passes update the same ACC rows, so the `cs` / rowscale logic sees one pass sequence per N tile |
| M-6 | ACC RMW hazards solved by pass spacing (§4.4), not by interlocks in the ACC | a pass period is at least the weight load, which is longer than the RMW |
| M-7 | `cs` held per pass in a FIFO (`CS_DEPTH`), rowscale prefetched in 16-row lanes into a FIFO | the ACC input sees them aligned with the rows; no SPM read on the critical path |
| M-9 | Two shadow buffers, alternating per tile | removes the dependency of the next tile's load on the current swap's skew; cost 131 k flops (about 1.5% of the PE arithmetic), measured 2.2x shorter tile period at `M = 1` |
| M-8 | MXU raises `ILLEGAL_FIELD` for unaligned operands | memmap.md §4 already requires 64 B tile bases; the lane model needs the pitches too |

## 9. Open items
- `BF16X3` (FP32 operands split into BF16 hi / lo on the fly: three passes per K tile, the W fetch reads 4 B elements, A likewise):
  the first RTL faults it. Needs a splitter (`x_hi = RNE_BF16(x)`, `x_lo = RNE_BF16(x - x_hi)`, exact in FP32), a third pass
  order in the loop (§4.2 `t` becomes `(t, pass)`) and a legality row. The hyper-connection mixer uses two `NORMAL` BF16 passes
  with pre-split weights (numerics.md D2), so it does not need the instruction.
- A third shadow buffer would take the period from about 39 to the 32-beat port limit; not needed for the decode rate. Bank-conflict-aware beat order.
- Timing (Phase 6): the fan-out of A and `swap`/mode along a PE row, the 66-stage sideband, whether `REG_DOT` is enough.
- Formal properties listed in §6.
