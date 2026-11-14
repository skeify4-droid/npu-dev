# MXU (`rtl/core/mxu*.sv`)

Status: **Phase 3, 2026-11-08: built and verified at `DIM` 8, 16 and (one decode instruction) 32; the RTL follows M-10 .. M-18** (`mxu_array`, `mxu_wpack`, `mxu`; below). `BF16X3` is reserved in v1 (M-15); the formal properties pass to depth 56 (§6, bounded). **Spec status:** this document, isa.yaml, isa.md, memmap.md, numerics.md and microarch.md follow decisions M-10 .. M-18 (§8); `numerics.py` (`mxu_mm` `dim`, `fold_e4m3` / `unfold` orientation) does since M-12 / M-13; the RTL (`mxu_dec`, `mxu`), `tb_mxu` and `gen_mxu_vectors.py` do since 2026-11-08 (list at the end of §8; `core/seq`, `core/spm`, `core/acc` still to follow). Inputs: [microarch.md](microarch.md) §2.2 and §5, [numerics.md](numerics.md) N-3,
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

One parameter `DIM` (= PE rows = PE columns; `FULL` 64, `MID` 16, `TINY` 8). **Lane width `LB = DIM` bytes** *inside the MXU*: the
lane of the three MXU ports is `DIM` bytes (64 B in `FULL`, the SPM bank width). Scaling the lane with `DIM` keeps every structure
the same in all configs: 4 load groups, `DIM/2` beats per tile, no sub-lane alignment cases. **The SPM keeps 64 B banks in every
config** (review S-3, M-10; the earlier "`DIM`-byte banks" are withdrawn): a shim `mxu_spm_shim` between the MXU ports and the SPM client
ports serves `DIM`-byte lanes out of 64 B bank rows (microarch.md §5).

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
**What the RTL checks at elaboration** (review I-10, M-25; `scripts/mxu_mutants/guard_check.py` runs it on Icarus, Verilator and Yosys):
`mxu`: `DIM` in {8, 16, 32, 64}, `SPM_LAT >= 1`, `CS_DEPTH >= 2`, `1 <= ACC_LAT <= 255` (the gap counter is 8 bits); `mxu_dec`: `DIM` as
above, `2 <= ACC_ROWS <= 2048` (the narrowed extents use `clog2(ACC_ROWS)` bits, `c_acc_base` is 11 bits). Nothing else is checked:
`SPM_BYTES` above 4 MiB (22-bit lane addresses) and a `CS_DEPTH` or `ACC_LAT` that does not match the other blocks are the integrator's.

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
latency`, about 39 cycles per tile for `FULL` (§7, an extrapolation). The groups remain, so that a buffer is reused as soon as its first rows are free.

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
| Word fields (checked first) | bits `[38:0]` of the word are zero; `mode = NORMAL` (`BF16X3` = 1 is reserved in v1, 2-7 undefined: M-15, M-18); `acc_mode` 0-2 (3 reserved); `rs_reg != 0` exactly with `RESCALE` | `ILLEGAL_FIELD` |
| A, W TD space | `SPM` | `TD_SPACE` |
| dtype | A and W both `E4M3` (FP8 mode) or both `BF16` (BF16 mode) | `ILLEGAL_FIELD` |
| A shape | `n0 = K >= 1`, `n1 = M` (`M = R[m_reg]` as a 64-bit unsigned number when `m_reg != 0`, compared in full), `n2 = n3 = 1`, no TD flag (`WRAP` is DMA-only: M-16) | `ILLEGAL_FIELD` |
| W shape | `trans_w = 1`: `n0 = K`, `n1 = N`; `trans_w = 0`: `n0 = N`, `n1 = K`; `n2 = n3 = 1`, no TD flag | `ILLEGAL_FIELD` |
| K agrees | K of A = K of W | `ILLEGAL_FIELD` |
| `1 <= N` | `M = 0` is **legal** (M-14): all checks run, then a no-op that completes in order | `ILLEGAL_FIELD` |
| Alignment | A base, W base, `s1` of both, `R[cs_reg]` and `R[rs_reg]` (when non-zero): multiples of **64 B in every config** (the SPM bank width, memmap.md §4; M-10); `s1 >= 0`. **No rule on `N`** (the `4·N` rule is deleted: M-11, review S-1) | `ILLEGAL_FIELD` |
| ACC range | `R[acc_reg] + NT·M <= ACC_ROWS`, `NT = ceil(N / DIM)`, `M` taken in full 64 bits | `RANGE` |
| SPM range | **operand extents** (review S-10): `R[a_reg] + (M-1)·s1 + K·eb`, W (`trans_w = 1`: `R[w_reg] + (N-1)·s1 + K·eb`, else `R[w_reg] + (K-1)·s1 + N·eb`), `cs` (`R[cs_reg] + (KTF-1)·CSP + 4·N`, `KTF = ceil(K / 256)` FP8 / `ceil(K / 128)` BF16: the bytes the DMA writes), rowscale (`R[rs_reg] + 4·M`) end at or before the end of the SPM; in 96-bit arithmetic, so a huge register value cannot wrap. An operand with no bytes (A and rowscale when `M = 0`) cannot be out of range. Lanes the MXU reads past an extent (the 4 `cs` lanes of a pass, the last lane of a row, the second BF16 lane past `N`) are discarded and need no check: the lane (A, W trans, FP8 non-trans) or the window of at most 64 B it belongs to starts inside the extent, so it lies in a 64 B granule that the SPM contains; only the `cs` window of a pass (`4·DIM` B) at `DIM` >= 32 and the BF16 non-trans window (`2·DIM` B) at `DIM` 64 can reach past it, and there the 22-bit address wraps (the SPM is exactly 2^22 B; the data is discarded) | `RANGE` |

Order when several apply (M-18): word fields, then `TD_SPACE` (A before W), then the descriptor `ILLEGAL_FIELD` rows in table order,
then `RANGE` (ACC before SPM; SPM operands A, W, `cs`, rowscale). All rows are checked for `M = 0` as for `M >= 1`.

**`M = 0` in the RTL** (`mxu_dec`): after the checks the instruction takes no context and no FIFO entry. It waits in the decoder until the in-flight FIFO is empty, i.e. until every earlier instruction has pulsed its `sem_inc`, then pulses `sem_inc {ssem, sinc}` itself (`z_inc`; the two sources cannot coincide: `acc_done` with an empty FIFO is ignored). The next instruction is not decoded in the meantime, so completion is in order. Equivalent to the zero-row FIFO entry of M-14, with no change to the exit stage or the FIFO.

`FAULT_ADDR` (64 bits, M-18): `R[reg]` of the offending operand for `TD_SPACE`, an alignment fault (a negative `s1` counts as an alignment fault of the `s1` of that operand) and an SPM `RANGE`; `R[acc_reg]` with bit 63 set for the ACC `RANGE`; 0 for any other `ILLEGAL_FIELD` (and `ILLEGAL_OP`). The `fault` port carries it whole.

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

Column scales: `cs` is FP32 `[KTF x N]` (`KTF` fold tiles, below) at `SPM[R[cs_reg]]`, row-major with row pitch `CSP = roundup(4·N, 64)` B (M-11, review S-1;
the DMA writes it with `aux_reg`, isa.md §5); the pass `(t, j)` uses the `DIM` values at `R[cs_reg] + CSP·(t >> log2 R) + 4·DIM·j`,
`R = 64 / DIM` (M-13: the scale rows are per 256 FP8 / 128 BF16 fold tile in every config, `R = 1` in `FULL`), constant over
the `M` rows. Rowscale: FP32 `[M]` at `SPM[R[rs_reg]]`, re-read for every N
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

A beat can have no valid lane (every lane past `K` or `N`: `K` tails shorter than 3/4 of a tile and `N` that does not fill 8 columns of a beat
are the norm). M-23 (review R-2): the MXU **grants such a beat itself and never presents it as a request** (`w_req_v = 0`, no `ready`
needed), so an SPM whose `ready` needs a valid lane cannot hang it. The beat still takes its response slot (`SPM_LAT` cycles later,
same tag pipeline), so the all-zero window is written into the shadow buffer (which holds the previous tile) like any other beat and
the beat order and period do not change. The A port (lane 0 always valid) and the scale port never make an empty request.

Beats of a group of the buffer being loaded (`l_buf`, toggled per tile) may start only when `free[g]` is set; `free[b][g]` clears at the first beat of a group in buffer `b` and sets on `swap_tap[b·NG + g]`; only the first beat of a group waits for it.
The pass's column scales are fetched in the same step (4 lanes, `cs` FIFO slot reserved first).

### 4.4 A stream (`a_rd`) and the dependency between passes
One request per row: `A_LANES` lanes of `LB` bytes, lane `l` at `a_base + m·s1 + t·KB + l·LB`, lane valid for `l·LB < K bytes
left in tile t`; bytes past the tile's K are zeroed in the response path. The swap pulses with the first row of a pass.

A pass may issue its first row when (a) its shadow tile is complete (the load FSM has pushed its token), (b) `gap` idle cycles have
passed since the last row of the previous pass was issued (any `SPM`-grant stall of that pass only lengthens the gap), with
`gap = max(0, ACC_LAT - M)` after a pass that is not the last of its instruction, and `gap = max(ACC_LAT - 2, ACC_LAT - M)` after the
last pass of an instruction (M-22, review R-1; `GAP_X = ACC_LAT - 2` in `mxu.sv`, `ACC_LAT - M` only matters for `M = 1`).
This makes every ACC row see its next read-modify-write at least `ACC_LAT` cycles after the previous one, whatever the stalls, so
the RMW needs no hazard logic. The first row of a pass issues at least `2 + gap` cycles after the last row of the previous one (the
token take in `S_IDLE`, then `S_RUN`). Proof, with `T` the issue cycle of the previous pass's last row:
- *Same instruction* (the `KT` passes of one N tile): the next pass hits the same rows in the same order, so row `m` of the next pass
  issues `(M-1-m) + 2 + gap + m = M + 1 + gap >= ACC_LAT + 1` cycles after row `m` of the previous one (one cycle of slack). Different N
  tiles of one instruction update disjoint rows (`j·M + m`).
- *Next instruction* (any `acc_base`, `M`, `N`, mode; the next pass's row `m'` may land on the previous pass's row `m` with
  `m - m'` up to `M - 1`): every row of the previous instruction was issued at or before `T`, every row of the next one at or after
  `T + 2 + (ACC_LAT - 2) = T + ACC_LAT`, so any two updates of one row are at least `ACC_LAT` apart. Rows of earlier passes were issued
  earlier still. An `M = 0` instruction between them issues no row, and the gap counts down in `S_IDLE` whatever the token does.

The first version of this rule (`gap = max(0, ACC_LAT - M)` everywhere) proved the same-instruction case only and applied it across
instructions, where the next pass need not hit the same rows in the same order: a `M = 9` instruction (`ACC_LAT = 8`) followed by a
`M = 1` one on its row 5 updated that row 6 cycles apart (review R-1). Cost of the fix: nothing at `M = 1` (the gap stays `ACC_LAT - 1`,
the tile period of §7 is unchanged, measured), up to `ACC_LAT - 2 = 6` cycles per instruction boundary for `M >= 2`, and only when the next
tile is already loaded (the weight load of an instruction hides behind its last pass of `M` rows; `FULL` loads take `>= DIM/2` cycles). A
gap that depended on whether the two row ranges overlap (a scoreboard of the last `ACC_LAT` rows, or two range comparators) would remove
that cost; it was not taken (M-22). With the default `ACC_LAT = 8` the load of a tile takes `DIM/2` cycles at least, and the swap-to-swap
period is that long, so the same-instruction gap costs nothing.

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
| `w_req_*`, `w_rsp_data` | | 8 lanes | same, stationary load. A beat with no valid lane is never requested (M-23, §4.3): `ready` is only ever looked at for a request with a valid lane |
| `s_req_*`, `s_rsp_data` | | 1 lane | same, rowscale and column scales (two sources, rowscale first) |
| `acc_valid` | out | 1 | an output row; no `ready`: the ACC accepts one MXU row per cycle (MXU has priority on every ACC SRAM port conflict, [acc.md](acc.md) §7, M-19) |
| `acc_row` | out | 11 | row index |
| `acc_part` | out | `COLS · 32` | partial sum row |
| `acc_ncols` | out | 7 | columns `< N` of this N tile; the ACC writes and flags only those |
| `acc_rescale`, `acc_overwrite`, `acc_use_cs` | out | 1 each | lane controls (§4.2) |
| `acc_rowscale` | out | 32 | `rowscale[m]` (valid with `acc_rescale`) |
| `acc_cs` | out | `COLS · 32` | column scales of the pass; the MXU holds it for the pass, but **only the value in the `acc_valid` cycle is contract** (the ACC samples it there, acc.md ACC-4) |
| `acc_last` | out | 1 | last row of the instruction |
| `acc_done` | in | 1 | pulse exactly `ACC_LAT` cycles after the `acc_last` row (`ACC_LAT` = `acc_pkg::ACC_LAT`, acc.md §5): its write is visible and its flags are out. None for an `M = 0` instruction (it completes by itself: below) |
| `sem_inc_valid`, `sem_inc_idx`, `sem_inc_amt` | out | 1, 6, 4 | completion |
| `fp_flags` | out | 4 | valid-qualified `mxu_pe` flags (microarch.md §5) |
| `fault_valid`, `fault_cause`, `fault_addr` | out | 1, 4, 64 | to `core/seq` `fault_in` (microarch.md §5). 64 bits since M-18 |

The ACC (`core/acc`) is specified in [acc.md](acc.md) (review S-11, M-19): the stream above, the `ACC_LAT` contract, masked
writes, flags before `acc_done`, row width `DIM`. **`ACC_LAT` in the RTL today:** a literal parameter (default 8) that `mxu` accepts from 1
to 255 (elaboration error outside, review I-10). There is no `acc_pkg` yet, so the check of acc.md §5 / §10 (items 1 and 2: default
`acc_pkg::ACC_LAT`, error below it) is **not in the RTL**; until `core/acc` exists the parameter must be set equal to the ACC's `acc_done`
delay by whoever instantiates both (open item, §9). A value above the ACC's is legal (idle cycles only). An `acc_done` pulse while no
instruction is in flight is ignored (`inf_pop` needs a non-empty FIFO, I-9); `core/acc` must not produce one, the proofs assume it does not.

## 6. Verification

| Test | Covers | Status |
|---|---|---|
| `tb_mxu_array` (iverilog; `scripts/gen_mxu_vectors.py`) | 8 x 8 and 16 x 16 arrays against an independent model (window mapping, swap copy, PE chain with `arith_ref`): back-to-back tiles, bubbles, FP8 / BF16 on consecutive waves, trans / non-trans windows, shadow writes in the tap cycle, NaN / Inf / overflow, column mask on flags, reset, `swap_tap` timing every cycle | **PASS**, 17 mutants (swap / write buffer select, per-buffer taps, skew, column mask, mappings, resets), all killed |
| `tb_mxu` | whole instructions, 31 per size (13, then the 18 chained `ACCUM` instructions of R-1 below), on `DIM` 8 and 16, each with and without 30% random SPM grant stalls (and `ACC_LAT` 24 in one, and two instances whose SPM raises `ready` only for a request with a valid lane, `RDYV`, M-23): behavioural SPM (128 KiB, fixed latency; NaN-valued garbage in every byte nobody requested, that lies past K / N, or in the guard rows behind a non-trans W), DTAB, semaphores, and an ACC built from the RTL's `acc_update`. The expected ACC image comes from the **logical** `A x W` (`arith_ref` PE chain per K tile, then `acc_update`; cross-checked against `numerics.mxu_mm(dim=DIM)`), bit-exact over all 256 rows. FP8 / BF16, `trans_w` 0 / 1, K / N / M not multiples of the tiles, M = 1, M from `m_reg` (decoy in the TD), `OVERWRITE` / `ACCUM` (onto the previous instruction's rows) / `RESCALE` (also one K pass onto large rows with 3 rowscale lanes and 2 N tiles, so that the rowscale reaches the result bits), column scales in the `CSP` layout (all bases and pitches 64 B aligned), specials. **R-1 / M-22 (2026-11-10):** three groups of five and one of three chained `ACCUM` instructions (`K = 10` FP8 trans, one short pass per N tile, no semaphore wait) on shifted row ranges: a long pass (`M` = `D+1`, `2D+1`, `3D`), then `M = 1` on its last row (`m - m' = M - 1`), another long pass on that row, `M = 2` on the last two rows, `M = 3` starting two rows early; and `NT = 2`, `KT = 2` followed by two `M = 1` instructions on its last row. With the rule before M-22 every group fails the "ACC hazard" check (rows 2-5 cycles apart, DIM 8 and 16, with and without stalls); with it all pass. **One case per rule of M-10 .. M-18 and S-10:** S-1 (`trans_w = 1`, `N = 40`, `cs_reg != 0`, BF16 K = 131: two scale rows at `CSP` = 192), S-7 (`R[m_reg] = 0` with a decoy `n1`, queued behind a normal instruction, followed by an instruction that waits for its semaphore; `M = 0` first in the queue; `M = 0` from the TD with `cs_reg`; each must change no ACC row and complete after the instruction ahead of it: `tb_mxu` compares the cycle of the first increments), S-10 (K = 200 FP8: one 64 B scale row at the very end of the SPM). Checks every lane address (aligned, inside the SPM), the ACC row spacing (`ACC_LAT` between any two updates of a row, across instructions too), an instruction starts only after its semaphore wait, final semaphore values and OR of `fp_flags`. Also: one `M = 1` decode instruction on 32 x 32 (bit-exact; tile period checked <= 26 cycles), and an 8 x 8 run where the A port refuses requests for 40 cycles after each grant, so the loader runs ahead of the swap and the buffer interlock is exercised. Optional bank model (+BANK=1: ports with banks of their own, +BANK=2: shared banks; 32 x 64 B banks, memmap.md §4 hash, a gang split over as many cycles as its busiest bank has distinct 64 B granules, `ready` low until the last lane is read, data `SPM_LAT` after the grant) and a `-DPERF` top that reports the gaps between consecutive ACC rows (§7). The whole `tb_mxu` (all sizes, stalls, A / W / scale ports at once) **passes with `+BANK=2`** (2026-11-09, 459 s, 0.74 GB): bit-exact, no deadlock, with 13-157 A, 54-144 W and 24-45 scale beats per run split over more than one cycle, so the gang-split semantics of M-10 (the grant is the commit) are exercised for the first time (review S-3). Plus 72 illegal instructions after a reset each (cause **and 64-bit `FAULT_ADDR`**, no SPM / ACC / semaphore action, queue stops): every row of §4.1 and the M-18 priority (word field before `TD_SPACE`, `TD_SPACE` before descriptor rows before alignment before `RANGE`; ACC before A, W, `cs`, rowscale), 64 B alignment of each operand at lane alignment, `R[m_reg] = 2^16 + 1` and `2^63` (with two N tiles, so `NT·M` wraps), `R[acc_reg] = 2^64 - 1`, 49-bit-style addresses, `M = 0` still checked | **PASS**. Mutants: `scripts/mxu_mutants/mxu_mutants.py` (77 one-change copies of `rtl/core`, CLAUDE.md; the last 7 test the decoder's `M = 0` path and checks and are judged by `mxu_dec.sby`, see the formal row). **Re-run 2026-11-11 after M-22 / M-23 (full mode, `tb` and `formal`; 77 mutants, all as expected, 0 unexpected).** Four mutants are new: `gap_x_off` and `gap_x_short` (the boundary gap removed / one cycle short: killed by `tb_mxu` on the R-1 chained instructions, and caught by `mxu.sby` task `bmc_d` in 55 s; `gap_x_off` is the rule before M-22), `gap_x_every_pass` (the boundary gap after every pass: equivalent, passes), `w_gnt_wait` (the MXU waits for the grant of an empty W beat: killed by the `RDYV` instances, which time out). `gap_cnt` and `no_gap_f` changed their text with the RTL and are killed as before. Result by expectation: 55 `kill` + 1 `kill_full` + 1 `formal_blind` + 2 `formal_x` killed by `tb_mxu`; 4 `equiv` pass (`ncols_pack`, `range_cs_bytes_read`, `token_at_request`, `gap_x_every_pass`); of the control mutants of the proofs 7 (`bmc_a`) + 2 (`bmc_d`) caught, `token_live` survives `bmc_a` as recorded (killed by simulation); of the decoder's 7, 6 caught by `mxu_dec.sby` and `z_m16` equivalent. (The `tb` pass was run in six parallel workers and the `formal` pass separately, each with its own work directory.) **Earlier full mode (DIM 8 and 16), 2026-11-09, committed RTL and vectors, all 73 as expected:** the 66 of the first set as below, and of the 7 decoder mutants 6 are also killed by `tb_mxu` (`z_m16` passes: equivalent). The 66 of the first set: 54 + 1 killed by `tb_mxu` (`gap_cnt` only by the `ACC_LAT = 24` run), 7 + 1 more are control mutants for the proofs (below); 3 equivalent and passing: `ncols_pack` (the extra column is masked at the ACC; the array test kills the column mask), `range_cs_bytes_read` (whole padded scale rows instead of the exact bytes: both end in the same 64 B granule), `token_at_request` (the token one latency earlier, hidden by the fixed latency of both ports). The first rounds of this set found five holes in the tests, now closed: no alignment-versus-ACC `RANGE` priority case, no `NT·M` wrap case, a RESCALE whose rowscale did not reach the result bits (`rs_lane`), a `cs` fold-tile case (`fold_tiles`), and a kill of `lane_valid_k` that depended on what the next allocation happened to hold |
| formal (`tb/formal/mxu.sby`, `make formal`) | `ifdef FORMAL` block of `mxu.sv` on DIM 8 with stub PEs (`mxu_stubs.sv`) and a stub decoder that pushes a free but legal context (`mxu_dec_stub.sv`; `acc_base` free per instruction; M <= 2, K tiles <= 2, N tiles <= 2 in `bmc_a` .. `bmc_c`, **M <= 3 in `bmc_d` and `covx`**, M-22). Environment: free grants (a request waits at most 2 cycles), `acc_done` within 3 cycles after the last row and not before. **P1** no beat is written over a shadow beat its swap has not copied (also: no duplicated beat), **P2** a swap needs every beat of its tile, **P3** one arbitrary ACC row is updated at most every `ACC_LAT` cycles, **P4** rows leave in pass order (`first`, consecutive rows, `lastp`), **P5** column-scale / rowscale FIFO counts equal an independent count of reservations minus frees and stay within depth, entries are filled before they are read, a rescaling pass starts at element 0 of a lane. Bounded model checking to depth 56 (Yices; three size mixes `bmc_a` .. `bmc_c` and, since M-22, `bmc_d` with `M <= 3`, one K tile, one N tile) passes; cover points (two buffers reused, `sem_inc`, rescale with column scales, and since M-22 the P3 row updated by two different instructions within 3 cycles of the allowed instant) are reachable at steps 26-39, so the model is not vacuous | **PASS** to depth 56, not unbounded (below), re-run 2026-11-10 after M-22 / M-23 (all six tasks in one `sby` run, 831 s wall, tasks in parallel: `bmc_a` 828 s, `bmc_b` 831 s, `bmc_c` 489 s, **`bmc_d` 735 s** (depth 56, no counterexample), `cov` 19 s, **`covx` 23 s**; the six cover points of `cov` are reached at steps 26-37 and those of `covx` at steps 26-39, the new cross-instruction one at step 37, well inside depth 56). `bmc_d` / `covx` use a stub decoder with `M <= 3` and a free `acc_base` per instruction: at `M <= 2` the rows of two instructions differ by at most 1 and the old rule passed P3 (review R-1). The rule before M-22 fails P3 in `bmc_d` (mutant `gap_x_off`, below). The assumption that an empty W beat waits at most 2 cycles for `ready` is gone (M-23). The stub decoder never makes an `M = 0` instruction (`z_inc` is tied to 0), so these four tasks say nothing about it. The `M = 0` path of the decoder has its own proof, **`tb/formal/mxu_dec.sby`** (the real `mxu_dec`, free inputs; `ifdef FORMAL` block at the end of `mxu_dec.sv`; task `bmc` depth 40, 90 s, 0.6 GB; task `cov`): one outcome per instruction taken (context, `M = 0` completion or fault, never two), `z_inc` only in `D_PUSH` with an empty in-flight FIFO and never with `ctx_push`, an `M = 0` that found the FIFO not empty is still there the next cycle, `M` taken as `R[m_reg]` in 64 bits or `n1` of the TD (shadow registers read from the inputs), `ctx_sync` is the instruction's `{ssem, sinc}`, a fault stops in `D_FAULT` and `D_PUSH` is entered only from a check without one (`M = 0` included), A and rowscale of an `M = 0` never `RANGE`. Cover: an `M = 0` completion that waited and one that did not, from `m_reg` and from the TD, a context, a fault of an `M = 0` (steps 7-9). It does not cover the wiring in `mxu.sv` (`inf_empty = (inf_cnt == 0)`, the `sem_inc` mux, `sem_inc_valid`): `tb_mxu` checks the order of the first increments there, and the `mxu.sby` tasks do not see it. The arithmetic of the legality checks is not proved (a first version that asked the solver to compare two copies of the 96-bit address multipliers did not finish in 15 min; the properties are stated on the previous cycle's result instead). 7 mutants of the decoder (`scripts/mxu_mutants`, `formal`): 6 caught (`z_no_wait`, `z_ctx_too`, `z_leave_early`, `z_mreg_ignored`, `z_unchecked`, `z_range_a`), `z_m16` equivalent (every `M` >= 2^16 is already an ACC `RANGE` fault). 8 mutants of the control (`scripts/mxu_mutants`, task `bmc_a`): 7 caught (`no_free_gate`, `no_gap_f` P3, `swap_early`, `last_beat_early` P2, `cs_pop_first`, `rs_pop_end`, `cs_cnt_leak` P5), `token_live` still not seen by the proofs (`bmc_a`; M <= 2) and killed by simulation; `dup_beat` (equivalent) is no longer in the set |
| review part I additions (2026-11-13; M-24, M-25) | `tb_mxu`: one spurious `acc_done` right after reset in every instance, checking `inf_cnt`, `inf_push` and `sem_inc_valid` (the I-9 guard; the proofs assume no spurious `acc_done`, so only the simulation sees it). `scripts/mxu_mutants/guard_check.py`: 25 elaboration cases of `mxu` / `mxu_dec` (I-10) on Icarus, Verilator and Yosys. `scripts/mxu_review/run_dec_miter.sh`: the current `mxu_dec` against the decoder of the reviewed commit 0255c28, DIM 8 and 64 (I-3); `scripts/mxu_mutants/tb_dec_miter_x.sv` is the reviewer's miter with one more `s1` case (around 2^30). `mxu_mutants.py misc` runs the mutants of these checks (guards, narrowed decoder widths). `fix_array_packed.sh` proves the packed `ps` / `fl` array equivalent to the old one and counts the PE instances kept by a blackbox PE (I-2). | **PASS** (results: status.md 2026-11-13) |

Bit-exactness: the array output equals `numerics.mxu_mm` for one K tile (`test_arith.py` ties `arith_ref`'s PE chain to it). The
top-level test compares the ACC image after the `acc_update` lanes with the logical `A x W` evaluated tile by tile with `arith_ref`
for the config's `DIM` (table above). Since M-13 `numerics.mxu_mm` has a `dim` parameter, and `scripts/gen_mxu_vectors.py` computes every
`tb_mxu` case twice, with the `arith_ref` chain + `acc_update` and with `mxu_mm(A, W, mode, cs, acc, rowscale, dim=DIM)`, and fails
unless the two ACC images are bit-identical (NaN payloads aside). The column scales are generated per fold tile (256 FP8 / 128 BF16)
and the SPM image replicates each one for the `64 / DIM` passes it covers; two cases span two fold tiles. The S-2 example is
`test_mxu_mm_order_depends_on_dim_review_s2` (`test_numerics.py`). `scripts/mxu_vectors.lock` holds the row count and SHA-256 of every
`build/mxu` file (`scripts/mxu_lock.py`, checked by `make mxu-vec` and so `make unit`; after an intended change of the generator,
`arith_ref.py`, `numerics.py` or NumPy: `scripts/gen_mxu_vectors.py --lock`). Tiles with an MX scale below 2^-126 are exempt (numerics.md N-8).

## 7. Performance (estimates; `tb_mxu` measures them)

- Tile load: `DIM/2` beats (32 cycles `FULL`) at one beat per cycle, 512 B/cycle: the port limit.
- Pass period at small `M`, **measured** (`scripts/mxu_perf.sh`; `tb_mxu` with `-DPERF`, vectors from `gen_mxu_vectors.py --perf`; one
  `M = 1` FP8 `trans_w = 1` instruction of 24-64 passes per case, W 2 KiB aligned, A 256 B aligned; mean gap between consecutive ACC
  rows over the steady state, i.e. leaving out the first 4 and the last 3 passes; every case bit-exact). The SPM model has 32 banks of
  64 B, `bank = addr[10:6] ^ addr[15:11]`, one 64 B read per bank and cycle, a gang split over as many cycles as its busiest bank
  has distinct 64 B granules (the shim of M-10: `ready` low until the last lane is read, data `SPM_LAT` after the grant);
  "ports" = A, W and scale ports with banks of their own, "shared" = one set of banks, rotating priority:

  | `DIM` | ideal SPM (no bank model) | 3 KiB and 4.5 KiB pitch (conflict-free): ports / shared | 2.5 KiB pitch (colliding): ports / shared |
  |---|---|---|---|
  | 16 | 13.0 | 13.0 / 13.05-13.3 | 13.5 / 13.9 |
  | 32 | 21.5 | 21.5 / 21.75 | 22.75 / 22.95 |
  | 64 (`FULL`) | **39.5** (gaps alternate 37 and 42) | **39.5** / 39.7-39.75 | **42.9** (gaps 37, 42, 53) / **43.1** (38-54) |

  The skew bound `P >= ((DIM - 1) + DIM/8 + k) / 2` with `k = 8` (12.5, 21.5, 39.5) was fitted to the 32 x 32 point only. It is now
  confirmed by an independent point: `DIM` 64 measures **39.5**, and `DIM` 16 measures 13 (12.5 rounded up). The old 39-cycle
  "extrapolation" was right to within half a cycle. Two shadow buffers (M-9) replaced the single one (`1.25·DIM + 9`: 25 cycles at
  `DIM` 16, 49 at 32); what remains is the skew of tile `n` against the load of tile `n + 2` into the same buffer.
  - Conflict-free pitches cost nothing over the ideal SPM (at most 0.25 cycle with the A, W and scale ports sharing banks).
  - **A colliding pitch costs 3.4-3.6 cycles per tile at `FULL` (43 instead of 39.5), not 8.5 (48):** half of the beats collide (16 extra
    cycles in a 32-beat load), but the load is not what limits the period, the skew is, so most of the extra load time is hidden;
    every fourth tile (6 of 24 gaps) pays the rest (53-54 cycles). At `DIM` 16 / 32 the cost is 0.5-1.5 cycles.
  - Port-limited ideal for comparison: 32 cycles (512 B/cycle). A regression check (`MAX_PERIOD = 26` on the 32 x 32 decode instruction)
    stays in `tb_mxu`.
- **Decode margins with the measured period** (M-21; microarch.md §2.2 / §6, numerics.md D1). 16 KiB per tile; need 193 elements/cycle
  at the core's peak HBM share (164 at 0.85), NVFP4 -> BF16 181 (154):

  | period | B/cycle | FP8 (MXFP4 / MXFP8 fold) peak / 0.85 | NVFP4 -> BF16 (exact path, D1) peak / 0.85 |
  |---|---|---|---|
  | 39.5 (conflict-free, ideal SPM) | 415 | 2.15x / 2.53x | **1.15x / 1.35x** |
  | 39.75 (conflict-free, shared banks, worst) | 412 | 2.14x / 2.51x | 1.14x / 1.34x |
  | 43.1 (2.5 KiB pitch, shared banks) | 380 | 1.97x / 2.32x | **1.05x / 1.24x** |
  | 32 (port limit, not simulated) | 512 | 2.65x / 3.12x | 1.41x / 1.66x |

  The exact path stays the narrowest stage of the weight path, narrower than the DMA's 248 elements/cycle. A colliding pitch no longer
  puts it below the HBM share (the 0.94x of review S-5 assumed 48 cycles): it leaves 1.05x. The third shadow buffer (period 32,
  +131 k flops, §9) would give 1.41x; that is the decision this measurement is for.
- Large `M` (prefill): one A row per cycle. The load of the next tile hides behind `M >= 32` rows, but the period bound above gives
  `P >= ~39.5` in `FULL`, so **the period is `max(M, ~39.5)`, not `M`**, for `M < 40` (review S-5; measured at `M = 1` only, the `M` between 32 and 40 is not simulated).
- Latency of an `M`-row pass from the first A grant to the last ACC write: `SPM_LAT + ARR_LAT + M + ACC_LAT`.

**SPM bank conflicts of the load beats** (`scripts/mxu_review/pitch_scan.py`, `test_isa.py::test_mxu_load_beat_pitches`; review S-4).
With 1R per bank, a beat takes as many cycles as its busiest bank has lanes. The group-ordered trans beat reads one lane from each of
8 consecutive W rows; the non-trans FP8 beat 8 k-rows, the BF16 beat 4 k-rows x 2 lanes. At the pitch rule of V-8 (2 KiB + 512 B) **half
the beats collide** (lanes 0 and 7 of a beat): 48 cycles per tile, not 32 (V-8 verified only the two-row 256 B shape). The rule is now
re-derived for this beat (memmap.md §4, microarch.md §2.3, M-17): 2 KiB pitches are padded by 1 KiB, 4 KiB and 8 KiB by 512 B, 16 KiB
by 1 KiB, 32 KiB and up by 512 B, all conflict-free at 2 KiB-aligned bases; of all pitches 64 B..16 KiB in 64 B steps only 26 are
conflict-free for the three shapes, so the compiler keeps a pitch table. The shim (M-10) makes a collision one stall cycle per extra
lane of the beat, never a wrong result. A rows are conflict-free for 256 B-aligned bases and `s1`; at 64 B alignment 6 of 1,024 starts
cost one stall cycle.

## 8. Decisions

| # | Decision | Why |
|---|---|---|
| M-1 | A is broadcast along a PE row; no output de-skew, no per-column delay | one wave per cycle, columns aligned at the ACC. A 64-way fan-out is a Phase 6 buffering question; a register every 8 columns with a de-skew is the fallback |
| M-2 | A skew in `mxu_array` as a triangular register set (`ROWS(ROWS+1)/2` words), not by staggering the SPM reads | keeps the A read one contiguous `KB` bytes (conflict-free for 256 B-aligned A bases and `s1`; one stall cycle in 6 of 1,024 starts at 64 B alignment, M-17); the 64k flops (`DIM` 64) are about 1.0-1.2% of the PE array's arithmetic by an ASAP7 area estimate (flops only, no wires; the "1.5%" of the first version was not derived from areas, review I-11, 2026-11-13) |
| M-3 | Swap skewed with the A skew; shadow load by group | removes the `ROWS`-cycle bubble between tiles and the `1.5·ROWS` period |
| M-4 | Lane width = `DIM` bytes, inside the MXU only (the SPM keeps 64 B banks: M-10) | one design for all configs (§2) |
| M-5 | N outer, K inner | consecutive passes update the same ACC rows, so the `cs` / rowscale logic sees one pass sequence per N tile |
| M-6 | ACC RMW hazards solved by pass spacing (§4.4), not by interlocks in the ACC | a pass period is at least the weight load, which is longer than the RMW |
| M-7 | `cs` held per pass in a FIFO (`CS_DEPTH`), rowscale prefetched in 16-row lanes into a FIFO | the ACC input sees them aligned with the rows; no SPM read on the critical path |
| M-8 | MXU raises `ILLEGAL_FIELD` for unaligned operands | memmap.md §4 already requires 64 B tile bases; the lane model needs the pitches too. The rule itself is restated by M-10 (64 B in every config) |
| M-9 | Two shadow buffers, alternating per tile | removes the dependency of the next tile's load on the current swap's skew; cost 131 k flops, about 1.9-2.4% of the PE arithmetic by an ASAP7 area estimate (PE 483 µm², flop 0.29-0.36 µm², before the write selects and wiring; the "1.5%" of the first version was too low, review I-11, 2026-11-13), measured 2.2x shorter tile period at `M = 1` |
| M-10 | **Lane shim, one alignment rule** (review S-3; amends M-4, M-8 and §2). `LB = DIM` stays inside the MXU. The SPM keeps 64 B banks and the same client port in every config; the "`DIM`-byte banks" of §2 are withdrawn. A shim `mxu_spm_shim` (parameter `DIM`) between the MXU ports and the SPM client ports serves `DIM`-byte lanes out of 64 B bank rows (`DIM` divides 64, so a lane never crosses a bank row). Alignment, in every config: A base, W base, `s1` of both, `cs_reg` and `rs_reg` addresses are multiples of 64 B (faults `ILLEGAL_FIELD`). Gang semantics (a `core/spm`-side requirement, to go into microarch.md §5): **the grant is the commit**. The shim reads colliding lanes of a request in successive cycles into a collector while `ready` is low, then grants; data follows exactly `SPM_LAT` after the grant. The MXU does not change (it already tolerates arbitrary grant stalls) and cannot deadlock on a bank collision. | one design for all configs; legality independent of `DIM`, so the golden ISA model needs no `DIM` to predict faults; memmap.md §4 already states the 64 B base rule. TINY programs pad `s1` to 64 B |
| M-11 | **Column-scale layout** (review S-1). `cs` is FP32 `[KT x N]` row-major with row pitch `CSP = roundup(4·N, 64)` bytes, defined once in isa.yaml for `DMA_COPY` (writes element (n, t) at `aux + t·CSP + 4·n`) and `MXU_MM` (pass `(t, j)` reads `R[cs_reg] + t·CSP + 4·DIM·j`). `N` is the destination row count of the conversion / the `N` of `MXU_MM`; no new field. The words past `N` are never written by the DMA and are masked by `acc_ncols` at the ACC. The rule "`4·N` a multiple of `LB`" of §4.1 is deleted | the DMA cannot pad `N` (no TD carries a pitch); a derived pitch lets ragged `N` (e.g. 40 keys) work |
| M-12 | **Orientation of the golden model** (review S-1). `numerics.fold_e4m3` returns `cs` as `[K tiles, N]` and `unfold` takes it in that orientation, the same as `numerics.mxu_mm` and the ISA. `fold_e4m3`, `unfold`, `cvt_source_check`, `test_arith.py` / numerics tests and the generated `numerics_experiments.md` change with it (to do, with `make numerics`) | one orientation everywhere; chosen over a transposing helper |
| M-13 | **Reduced configs** (review S-2). The fold tile stays 256 FP8 / 128 BF16 in every config, and `cs` stays `[ceil(K / 256) x N]` (BF16: `/128`). Pass `t` of the MXU uses `cs` row `t >> log2(R)` with `R = 64 / DIM` (FP8: `256 / (4·DIM)`; BF16: `128 / (2·DIM)`; `R = 1` in `FULL`). The `cs` FIFO still gets one entry per pass (re-fetched). `numerics.mxu_mm` gets a `dim` parameter (default 64): PE rows `0..dim-1`, K tile `4·dim` / `2·dim`, `cs[t // R]`; N-3 is restated with `DIM`. Bit-exactness is per `DIM` (TINY / MID results differ from `FULL` in the last bits, review S-2 item 1). ACC rows are `DIM` FP32 wide in `TINY` / `MID` (memmap.md §4, microarch.md §5 to say so) | keeps the fold numerics and the DMA independent of the config; equal to a DMA that replicates each scale `R` times, with no extra storage |
| M-14 | **`M = 0`** (review S-7). A no-op with completion: all legality checks run as for `M >= 1` (a program faults or not independently of the data), no SPM or ACC access, no `fp_flags`; the instruction takes its place in the in-flight FIFO as a zero-row entry, retires in order after the earlier instructions' `acc_done`, and pulses `sem_inc {ssem, sinc}`. `M` is `R[m_reg]` as 64-bit unsigned, compared in full (`R[m_reg] = 2^16 + 1` is not truncated); `NT·M > ACC_ROWS` is `RANGE`. `1 <= M` is removed from §4.1. `N >= 1` stays. Needs the decoder, the exit stage and a formal re-run (`make formal`, mxu) | the MoE example of isa.md §7 passes a data-dependent count; zero tokens for an expert is routine in decode |
| M-15 | **`BF16X3` is reserved in v1** (review S-6): `ILLEGAL_FIELD`, as `SCALE_INLINE`. isa.yaml, isa.md §5 / §6 and the coverage table (isa.md:339) are changed to say so, then `make isa`. No RTL change. `numerics.mxu_mm_bf16x3` stays as the definition for a later version; the mHC mixer uses two `NORMAL` BF16 passes (numerics.md D2). §9 keeps the sketch of the implementation | no user of the instruction; the operand dtypes (FP32 A / W) were never defined |
| M-16 | **`WRAP` is DMA-only** (review S-8). The MXU operands keep faulting on any TD flag. memmap.md §7 is reworded: the DMA appends at `pos mod window`, and attention reads the window in slot order (K and V in the same order; the softmax is order-invariant), so the window needs no copy. Open: the `WINDOW` mask of `VSOFTMAX` must be slot-aware, or the ring must be full; not yet checked against the VPU spec | WRAP has no start-row field and is the identity on a linear walk, so it has no meaning on an MXU operand |
| M-17 | **Load beats and the tile period** (review S-4, S-5). No change to the beat order or the buffers. The pitch rule (microarch.md §2.3, memmap.md §4) is re-derived for the 8-row beat (`scripts/mxu_review/spec_checks.py`, pitch scan): pad a 2 KiB pitch by 1 KiB (3072 B); 4 KiB + 512, 8 KiB + 512, 16 KiB + 1 KiB and 32 KiB or more + 512 stay as they are. Only 26 of the 256 pitches 64 B..16 KiB are conflict-free for all three beat shapes at 2 KiB-aligned bases, so a compiler pitch table covers the other (non-power-of-two) pitches; the shim of M-10 makes a collision slower (one stall per colliding beat) but never wrong. The tests I-13 / V-8 of `test_isa.py` are replaced by an 8-row-beat test. §2 M-2 and §7 are corrected: the A read is conflict-free for 256 B-aligned A bases and `s1` (at 64 B alignment 6 of 1,024 starts cost one stall cycle); the period for `M < 40` is `max(M, ~39)`, not `M`; the decode margins (microarch.md §2.2 / §6, numerics.md D1, status.md) are restated with the 39-cycle period: FP8 2.2x, NVFP4 -> BF16 1.16x at the peak HBM share (1.36x at 0.85). The 39 was an extrapolation; **M-21 replaces it by the measured period** and restates the margins | no MXU RTL change; the buffer decision waits for a measured period |

| M-18 | **Faults of `MXU_MM`** (review S-9; the points of S-9 that M-10 .. M-17 left open, chosen when the specs were updated, 2026-11-08). Undefined values fault `ILLEGAL_FIELD`: `mode` 2-7, `acc_mode` 3, non-zero bits `[38:0]` of the word, `rs_reg != 0` without `RESCALE`, `RESCALE` with `rs_reg = 0`. `FAULT_ADDR` is the 64-bit CSR: `R[reg]` of the offending operand for `TD_SPACE`, alignment and an SPM `RANGE`; `R[acc_reg] \| 1<<63` for an ACC `RANGE`; 0 for every other `ILLEGAL_FIELD` (a queue entry has no PC). Priority: word fields, `TD_SPACE` (A, W), descriptor `ILLEGAL_FIELD` in table order, `RANGE` (ACC, SPM). In-flight instructions of any engine when a fault occurs may be cut off or complete: software relies on neither. `core/seq` gets a `fault_in` (first fault latched, simultaneous ones in the order front end, DMA, MXU, VPU, NI) and an `eng_stop` (microarch.md §5). The `aux_reg` of the DMA is 64 B aligned (partner of the alignment of `cs_reg`) | one deterministic fault per program, independent of the config and the data, so the golden ISA model predicts `FAULT_CAUSE` / `FAULT_ADDR`; a stale `rs_reg` is a compiler bug worth a fault |
| M-19 | **`core/acc` stream contract** (review S-11, all seven items; [acc.md](acc.md) ACC-1 .. ACC-9, 2026-11-08). `acc_*` replaces `acc_rmw` / `mxu_rmw`; no `ready`, MXU priority on every ACC SRAM port conflict; one `ACC_LAT` owned by `acc_pkg` (this module's parameter defaults to it, elaboration error below it or above 255) and equal to the `acc_done` delay; ACC `fp_flags` of the `acc_last` row no later than `acc_done`; `cs` applied to `part` in the ACC input stage; word-masked writes for columns `>= ncols`; ACC row `DIM` words per config. RTL changes listed in acc.md §10, not made | the MXU spacing proof (P3) and the `sem_inc` ordering need these as stated obligations of `core/acc` |
| M-20 | **`RANGE` on operand extents; `M = 0` kept in the decoder** (review S-10, and the way M-14 is implemented; recorded 2026-11-08 after the RTL change). (a) `RANGE` is judged on the bytes each operand has, as §4.1 gives them (A `(M-1)·s1 + K·eb`, W by `trans_w`, `cs` `(KTF-1)·CSP + 4·N` with `KTF` the rows of the DMA-written array, not the MXU passes, rowscale `4·M`), in 96-bit arithmetic; an operand with no bytes (A and rowscale when `M = 0`) cannot fault. Lanes the MXU reads past an extent are discarded and not checked; the two windows that can leave the 64 B granule of the extent (`cs`, 4·DIM B at `DIM` >= 32; BF16 non-trans W, 2·DIM B at `DIM` 64) wrap in the 22-bit SPM address and are not clamped. (b) M-14 said "a zero-row entry in the in-flight FIFO"; the RTL has no entry: `mxu_dec` holds the `M = 0` instruction in its `D_PUSH` state until the FIFO is empty and then pulses `sem_inc` (`z_inc`). The visible behaviour is M-14's (no SPM / ACC access, completes after every earlier instruction's `acc_done`, in order, before any later instruction starts). The cost is that the instruction behind an `M = 0` does not decode (no DTAB read, no prefetch) until the earlier ones have finished, i.e. it loses the overlap with them; accepted for the routine zero-token expert, revisit if a program has runs of them | the old `cs` extent counted MXU passes and faulted legal reduced-config programs, and the ACC sum could wrap near 2^64; the zero-row FIFO entry costs a field and a third kind of exit in the load / stream control that the proofs of `mxu.sby` would have to cover, the decoder wait costs one state |
| M-21 | **Measured tile period; margins restated** (review S-4, S-5; 2026-11-09). `tb_mxu` gets a bank model (+BANK=1 / 2: 32 banks of 64 B, memmap.md §4 hash, 1R per bank, gang split with the grant as the commit and a fixed latency after it; §6) and a `-DPERF` top; `gen_mxu_vectors.py --perf`, `scripts/mxu_perf.sh`. Results (§7): `FULL` `M = 1` period **39.5 cycles** (ideal SPM, and conflict-free pitches 3 KiB / 4.5 KiB with banks: 39.5-39.75), **43 cycles at the colliding 2.5 KiB pitch** (not 48); `DIM` 32: 21.5 (22.75-22.95 colliding); `DIM` 16: 13 (13.5-13.9). The fitted bound of §7 holds at `DIM` 64. Decode margins: FP8 2.15x (2.53x at 0.85), NVFP4 -> BF16 **1.15x (1.35x)** conflict-free, **1.05x (1.24x)** at a colliding pitch. microarch.md §2.2 / §6, numerics.md D1 follow. No RTL change. The third shadow buffer (§9) stays undecided: it is worth 39.5 -> 32 cycles (1.15x -> 1.41x on the exact path) for +131 k flops | the extrapolation is replaced by a measurement, and the collision penalty is smaller than the 48-cycle bound because the skew, not the load, sets the period |
| M-22 | **ACC spacing across instructions** (review R-1; 2026-11-10). The gap of §4.4 after the last pass of an instruction is `max(ACC_LAT - 2, ACC_LAT - M)`, after any other pass `max(0, ACC_LAT - M)` (`GAP_X` in `mxu.sv`, chosen at the pass end from `s_last`, so a late next token only finds the gap already spent). Option (a) of the review, chosen over (b) (a scoreboard of the last `ACC_LAT` issued rows) and (c) (a compiler rule nothing checks): the token path adds 2 cycles (`S_IDLE` take, then `S_RUN`), so `ACC_LAT - 2` is exactly enough for the worst pair of rows (previous last row `m = M - 1`, next row `m' = 0`: `ACC_LAT` cycles apart), and no state beyond `gap_cnt` is added. Cost: the `M = 1` tile period is unchanged, **measured** with `scripts/mxu_perf.sh 16 32` after M-22 and M-23 (all 18 cases, `DIM` 16 / 32 x 3 pitches x 3 bank models: mean, min, max and histogram identical to the results before, e.g. 13.00 / 21.50 ideal, 13.05 / 21.75 shared banks), because one instruction of 24-64 passes has no instruction boundary inside it; a chain of `M = 1` instructions keeps its gap of `ACC_LAT - 1` by construction (the new override needs `M >= 2`), not measured separately; for `M >= 2` at most `ACC_LAT - 2 = 6` cycles per instruction boundary, and only when the next tile is already loaded. A gap that depends on whether the two row ranges overlap (two comparators on `acc_base`, `NT·M`) would remove that cost for prefill chains; not taken, since nothing measured needs it. §4.4's proof is restated (same instruction: unchanged; next instruction: any row is `>= ACC_LAT` after any row of the previous one); acc.md §5 ("including across instructions") now holds as written. Tests: `tb_mxu` 18 chained `ACCUM` instructions on shifted row ranges (DIM 8 and 16; the old rule fails them with rows 2-5 cycles apart), the review suite's `acc_spacing_x_ins` and fuzz programs with `+RMW=1`, formal `bmc_d` / `covx` (stub decoder `M <= 3`, free `acc_base`: the old rule fails P3), mutants `gap_x_off`, `gap_x_short` (caught by `bmc_d` and `tb_mxu`), `gap_x_every_pass` (equivalent) | the first rule proved only the passes of one instruction (the next pass hits the same rows in the same order); the formal environment capped `M` at 2, where two instructions' rows differ by at most 1, and `tb_mxu` chained instructions on the same rows |
| M-23 | **Empty W beats** (review R-2; 2026-11-10). Option (b): the MXU grants a beat with no valid lane itself (`w_gnt = w_want && (w_req_ready \|\| wl_lane_v == 0)`) and never presents it as a request; it keeps its response slot (same tag pipeline, `SPM_LAT` later) so the zero window is still written into the shadow buffer, and the beat order, the load time and the tile period do not change. §4.3, §5 and microarch.md §5 (shim contract) say so. Chosen over (a) (`ready` independent of `valid` as an SPM / shim contract): it makes no demand on a block that does not exist yet, and an SPM that raises `ready` only for a request works. `tb_mxu` gets a `RDYV` mode (a port's `ready` low while its request has no valid lane; two instances in the default regression, DIM 8 with 30% stalls and DIM 16, and `+RDYV=1` for any run); the review's `probe_rdyv.py` reports the hangs of its 78 programs (71 before). Mutant `w_gnt_wait`. The formal assumption "an empty beat's `w_want` waits at most 2 cycles for `ready`" is dropped (the grant of an empty beat no longer depends on `ready`) | 71 of 78 programs of the review hang on a ready-needs-valid SPM; empty beats are the norm (any K tail shorter than 3/4 of a tile) |
| M-24 | **`mxu_dec` without dividers and wide multipliers** (review I-3; 2026-11-13). The two 64-bit dividers (K tiles, rows of the column-scale array) are shifts (the divisors are `2·DIM` / `4·DIM` and 128 / 256), and the products are cut to what the M-18 priority needs. An operand extent is looked at only after the word, `TD_SPACE`, descriptor and alignment checks and the ACC `RANGE` check, so there `K`, `N` are in 1 .. 65535, `s1 >= 0` and `M <= ACC_ROWS`; where one of those does not hold an earlier flag of the chain wins and the later flag is not used. A extent: `(M-1)` on `clog2(ACC_ROWS)` bits x `s1[30:0]`; W extent: one 16 x 31 product, rows and elements per row muxed by `trans_w`; `cs` extent: `(KTF-1)` on 10 bits x `CSP` (19 bits, the low 6 are zero); ACC: `R[acc_reg] + NT·M` on `clog2(ACC_ROWS+1) + 14 + 1` bits under the two terms that already cover `R[acc_reg] > ACC_ROWS` and `M > ACC_ROWS`; the sums stay 96 bits. Four multipliers (11 x 31, 16 x 31, 10 x 13, 14 x 12 after width reduction), no divider. **Before / after** (same commit, `scripts/mxu_review/syn_core.sh`, `depth.sh`, `asap7.sh`; Yosys 0.70+post): generic cells **42,316 -> 12,108**, longest path **856 -> 93** gates (the PE: 217), ASAP7 area 2,711 -> 1,271 µm², ABC delay at 667 ps target 2.17 -> 1.47 ns; the whole `mxu` at `DIM` 8 is 478,667 cells hierarchical (`make syn BLOCK=mxu HIER=1`). **Chosen over** spreading `D_CHECK` over more cycles (the review's other option): no cycle per instruction, no change of the decoder's states or of the formal run, and the cone is no longer the deepest control logic; spreading stays open (§9) if ORFS shows `dst` / `fault_*` / `c_*` as critical. Check: `tb_dec_miter` (`scripts/mxu_review/run_dec_miter.sh`, the decoder of commit 0255c28 against the current one, every output every cycle; not a proof), the `tb_mxu` case of every rule, `mxu_dec.sby`, and the `dec_*` mutants of the widths (`mxu_mutants.py misc`). The review's remark that `s1` without bit 30 is equivalent does not hold here: `s1 = 2^30`, `M >= 2` is a `RANGE` fault (mutant `dec_a_s1_bit30` is killed by the miter). `ACC_ROWS` is checked at elaboration (2 .. 2048) because the widths follow from it | the decoder was 8.3% of the `DIM` 8 MXU and its deepest control cone; it is evaluated once per instruction, so nothing needs its width |
| M-25 | **Local fixes of review part I** (2026-11-13). I-2: `ps` / `fl` of `mxu_array` are packed (`logic [ROWS-1:0][COLS-1:0][31:0]`); with an unpacked 2-D array and a blackbox `mxu_pe` Yosys deleted every PE instance (0 of 64 left; 64 with the packed arrays; equivalence proven at 8 x 8 and 16 x 16). I-8: `swp0` / `swp1` are one `assign` of `{swp_q, swap && ...}` over a separate flop vector (one kind of driver per vector). I-7: the per-pass configuration of the stream FSM (`s_t`, `s_Mrows`, `s_kb`, `s_ncols`, `s_last`, `s_bf16`, `s_cs_en`, `s_buf`, `s_accm`, `s_a_pitch`, `s_rs_base`, 99 bits) and `inf_mem` (40 bits) are in `always_ff @(posedge clk)` processes of their own: no flop without reset in an async-reset process, so `rst_n` is no longer in their D logic. I-9: `inf_pop = acc_done && inf_cnt != 0`; an `acc_done` with nothing in flight is ignored (before: `inf_cnt` wrapped to 7 and the MXU stopped for good); `tb_mxu` pulses one right after reset. The proofs assume it never happens (`f_pend`), so only the testbench and the mutant `spur_pop` see this. I-10: elaboration guards (§2); `ACC_LAT` 1 .. 255 (the check against `acc_pkg::ACC_LAT` of acc.md §5 waits for the package, §9). No change of function, latency or tile period: the vectors of `make mxu-vec` and their lock are unchanged | each was local and cheap; the array and the stream registers are the ones a hierarchical flow with a hard-macro PE (I-1) depends on |
| M-26 | **`make syn` builds `rtl/core` blocks** (review I-6; 2026-11-13). `make syn BLOCK=<core block> [PARAMS="P=v"] [HIER=1]` reads `isa_pkg`, every core file and every arith file as `make lint` does; `HIER=1` drops `-flatten` and prints the totals with `stat -top`. The review's proposal (a core branch and `HIER`) was not enough: with `hierarchy -top` before `chparam` a block with a parametrized child (`mxu_array` -> `mxu_pe`) fails ("Module `\mxu_pe' ... is not part of the design", the first `hierarchy` replaces `mxu_pe` by its parametrized copy), and `hierarchy -chparam` trips Yosys' internal assertion on `mxu_array`; the recipe reads with `-defer` and applies `chparam` before `hierarchy`. The counts of the common and arith blocks are unchanged (`mxu_pe` 6,580, `fifo DEPTH=8` 181, `trivial_add W=16` 99) | CLAUDE.md asks for hierarchical synthesis; the target could not build the blocks the plan is about |

Decisions M-10 .. M-17 were taken 2026-11-08, answering review part S (S-1 .. S-8; docs/reports/mxu_review.md); M-18 completes S-9; M-20 (2026-11-08) records S-10 and how M-14 is built.
**Specs updated** (S-n cited at each change): isa.yaml (`MXU_MM`, `DMA_COPY` `aux_reg`, `mxu_mode`, `WRAP`, fault CSRs; `make isa`),
isa.md §4-8, memmap.md §4 / §7, numerics.md N-2 / N-3 / N-8 / D1, microarch.md §2.2 / §2.3 / §4 / §5 / §6, this document (S-13).
**Done in the RTL, 2026-11-08** (`mxu_dec`, `mxu`; `tb_mxu`, `gen_mxu_vectors.py`; each rule has a `tb_mxu` case, §6): alignment 64 B in every config, the `4·N` rule deleted, `M = 0` (and `R[m_reg]` in 64 bits), reserved word bits / `mode` / `acc_mode` / `rs_reg` checks, the M-18 priority, 64-bit `FAULT_ADDR`, `cs` row pitch `CSP` and scale row `t >> log2 R`, `RANGE` on operand extents (S-10: the `cs` extent was `4·KT·N` with `KT` = MXU passes, a false `RANGE` for a legal reduced-config program; the ACC sum could wrap for `R[acc_reg]` near 2^64).
**Still old** and to follow these rows: the shim `mxu_spm_shim` does not exist, `core/seq` (`fault_in`, `eng_stop`), `core/spm`, and `docs/reports/status.md` (line 191 "the MXU is never the decode bottleneck"). Review items not decided
here: S-12 (DTAB / prefetch ordering wording). S-10 (extents) and the decoder-side `M = 0` are recorded as M-20. S-11 is decided by M-19 ([acc.md](acc.md); the RTL list is acc.md §10, not yet done).

## 9. Open items
- `BF16X3` (FP32 operands split into BF16 hi / lo on the fly: three passes per K tile, the W fetch reads 4 B elements, A likewise):
  reserved in v1 (M-15; the first RTL faults it, as the ISA now says). Needs a splitter (`x_hi = RNE_BF16(x)`, `x_lo = RNE_BF16(x - x_hi)`, exact in FP32), a third pass
  order in the loop (§4.2 `t` becomes `(t, pass)`) and a legality row. The hyper-connection mixer uses two `NORMAL` BF16 passes
  with pre-split weights (numerics.md D2), so it does not need the instruction.
- A third shadow buffer would take the period from the measured 39.5 to the 32-beat port limit (not simulated). Not "not needed": on the exact NVFP4 -> BF16 decode path the MXU is the narrowest stage at 1.15x (1.05x at a colliding pitch; §7, M-21). The simulation of `DIM` 64 at `M = 1` is done; the decision itself (+131 k flops for 1.15x -> 1.41x) is open. Bank-conflict-aware beat order (a colliding pitch costs 3.5 cycles, so this is worth less than the buffer). Not measured: BF16 and non-trans W beats, `M` between 2 and 40, other SPM clients (DMA writes, VPU reads) competing for the banks, the shim as RTL (`mxu_spm_shim` does not exist; the testbench model is behavioural).
- **Review part I, not done in the session of 2026-11-13** (I-1, I-4, I-5; each is a change of structure that only a P&R-level measurement can justify; none changes the function):
  - **I-1 weight storage and its muxes live in `mxu_array` itself** (about 0.67 M generic cells at `DIM` 64: 393 k weight flops, 67 k skew flops, about 163 k muxes; measured 10,642 / 42,168 cells at `DIM` 8 / 16, the `DIM` 32 and 64 figures are the reviewer's and extrapolated; ASAP7: the array's own logic is 11.2% of the 8 x 8 array's area, 3,888 µm² against 64 x 483 µm² of PEs). *Would change:* a per-PE or per-row wrapper (`mxu_cell` = `w_sh0` / `w_sh1` / `w_ac`, write select, `mxu_pe`; or `mxu_row`) so that the hierarchy can split the array and a P&R block can be a few rows; the window decode (`we`) stays outside as one enable per (group, column octet) or (group, row pair). No change of latency, tile period (M-21) or ports; `tb_mxu_array`, `tb_mxu` and the formal run (stub PE) see the same ports; the cell count does not change. *Measurement that decides:* an ORFS (ASAP7, VM) run of `mxu_pe` alone and of an 8 x 8 `mxu_array` (443 k generic cells, the largest the flat budget takes) at 1.5 GHz, flat and with the wrapper: area per PE with its share of the array logic against the 483 + 59 µm² estimate, the worst slack of the `wl_*` / `swap` paths, and whether the P&R tile that the budget allows is one row, a few rows or a column strip. If a tile of at least one row cannot be formed with the weights in the array module, do the wrapper.
  - **I-4 W / S request addresses are combinational from the loop counters through multipliers** (25 `$mul` in `mxu.sv`, 20 in the W lane addresses; `w_req_addr` cone 1.02 ns in the ASAP7 estimate at `DIM` 8 against 0.62 ns for the A address, reproduced). *Would change:* (a) strength reduction, lane `l` = lane 0 + `l·pitch` by shifted adds or a per-beat increment as `s_addr` does for A: no latency, period or formal change, a `tb_mxu` + mutant re-run; (b) registering `w_req_*` / `s_req_*`: one cycle more between the decision and the request, so the `w_tag` / `s_tag` pipelines and the `free_g` clear / tap relation shift by one, the load of a tile is one cycle longer (hidden while the skew sets the period, M-21: measure with `scripts/mxu_perf.sh 16 32 64`; it would show with a third shadow buffer), and P1 / P2 / P5 of the formal run must be re-run (`bmc_a` .. `bmc_d`, about 15 min) with the mutants of the tag pipeline. *Measurement that decides:* ORFS at 1.5 GHz of `mxu` with `mxu_array` as a macro, slack of `w_req_addr`, `w_req_v`, `s_req_addr` at the port with the SPM stage budget of the SPM spec (bank hash and arbitration, memmap.md §4) as output delay. The native Yosys / ABC figure is pessimistic (a 32-bit add is 561 ps there), so do (a) first, and (b) only if the slack is still negative.
  - **I-5 shadow load reaches the whole array in one cycle** (`w_rsp_data` -> `mxu_wpack` -> `wl_data` -> every shadow flop of the group in the cycle of the response; at `DIM` 64 `wl_trans` selects 131 k muxes, `wl_en` / `wl_buf` gate 262 k flops, each window word goes to 32 PEs in each mode; checked against `mxu_array.sv`, the 1.5 mm span is the reviewer's ASAP7-area estimate, not a placement). *Would change:* a register on `win` (4,096 flops) and on the response tag `wr_*` (or keep the packer next to the SPM port and register its output): the shadow write one cycle after the response, `l_w_done` and the token one cycle later; the tile period at `M = 1` should not change (skew-bound, to be measured as for I-4(b)); P1's `f_got` bookkeeping refers to the delayed write and `bmc_a` .. `bmc_d` are re-run. *Measurement that decides:* placement of the 8 x 8 array in ORFS with `wl_*` as input ports (a path `wl_data` -> `w_sh*` D, the fan-out tree of `wl_trans` / `wl_en`), scaled to 64 PEs per row with the ASAP7 wire RC; and the same register inserted in the RTL to see the cost at `DIM` 16 / 32 / 64 in `mxu_perf.sh`.
  - **`acc_pkg`** does not exist yet: the default and lower-bound check of `ACC_LAT` against it (acc.md §5, §10 items 1-2) are not in `mxu.sv` (§5); add them with `core/acc`. `D_CHECK` over more cycles (M-24) only if ORFS shows the decoder's `dst` / `fault_*` / `c_*` paths as critical.
- **`tb_mxu` does not reach the extent aliasing cases of the decoder** (found by the I-3 mutants `range_a_m0` and `range_acc_wrap`, which `tb_mxu` killed before the narrowing and no longer kills; the miter against the 0255c28 decoder does). The decoder's `RANGE` flags on a wrapped `M - 1` or on `R[acc] + NT x M` beyond 2^64 are covered by the miter and the decoder proof only for `M = 0`. Add such cases to `gen_mxu_vectors.py` / `tb_mxu` (then regenerate `scripts/mxu_vectors.lock`) or keep the miter as the check; decide with the next change of `mxu_dec`.
- **`tb_mxu` never holds the in-flight FIFO full with different entries** (mutant `inf_mem_always`, I-7, survives: writing `inf_mem[inf_wr]` every cycle only changes the head when the FIFO is full and the head differs from `ctx_sync`; measured: `inf_cnt` reaches 4 in every instance but for one cycle at a time and never with a different head, because `acc_done` comes 5 cycles after the last row). A slower `acc_done` in one `tb_mxu` instance would reach it (no new vectors); until then the mutant is listed as `equiv` in `mxu_mutants.py`.
- DTAB read and weight prefetch start after the instruction's own semaphore wait, and §4.4 (b) includes the last pass of the previous instruction (S-12: wording, not changed here). `RANGE` is on operand extents since the RTL change of 2026-11-08 (review S-10, §4.1); `core/acc` stream contract: decided, M-19 and [acc.md](acc.md).
- Timing (Phase 6): the fan-out of A and `swap`/mode along a PE row, the 66-stage sideband, whether `REG_DOT` is enough.
- Formal: k-induction does not close (its step starts from arbitrary pipeline contents; P1 and P3 need a joint invariant of the free flags, the weight-tag pipeline and the shadow bookkeeping, and of the sideband chain against the gap counter), so P1-P5 are bounded to depth 56. Bounded liveness (an instruction in flight completes within N cycles) did not scale (16 minutes per step at depth 52); deadlock freedom rests on simulation (random and 40-cycle A stalls) and on the cover points. Tap timing relative to the swap is checked by `tb_mxu_array`, not by P1, which uses the tap signal itself. Larger tiles (`BPG > 1`, `DIM = 16`) are not covered by the proofs. P3 across instructions (M-22) is seen only with `M <= 3` (`bmc_d`), the first size at which the old rule fails at `ACC_LAT` 8; larger `M` (worst pair `m - m' = M - 1`) rests on the proof of §4.4 and on `tb_mxu` / the review suite (`M` up to 3·`DIM`). The hang of the MXU on a ready-needs-valid SPM (M-23) is a liveness property: it is found by the `RDYV` runs and `probe_rdyv.py`, not by the proofs. With M <= 2 a pass is too short for the next instruction's token to arrive during it, so the proofs cannot see a stream FSM that reads the token registers instead of its own copy (mutant `token_live`, not caught at depth 72); the system test kills it by a wide margin (long passes).
