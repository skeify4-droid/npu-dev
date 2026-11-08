# MXU (`rtl/core/mxu*.sv`)

Status: **Phase 3, 2026-11-08: built and verified at `DIM` 8, 16 and (one decode instruction) 32** (`mxu_array`, `mxu_wpack`, `mxu`; below). `BF16X3` is reserved in v1 (M-15); the formal properties pass to depth 56 (§6, bounded). **Spec status:** this document, isa.yaml, isa.md, memmap.md, numerics.md and microarch.md follow decisions M-10 .. M-18 (§8); the RTL, `tb_mxu`, `numerics.py` and `gen_mxu_vectors.py` do not yet (list at the end of §8). Inputs: [microarch.md](microarch.md) §2.2 and §5, [numerics.md](numerics.md) N-3,
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
| SPM range | every byte the instruction reads is inside the SPM (S-10 open: over-reads past an operand by design, §9) | `RANGE` |

Order when several apply (M-18): word fields, then `TD_SPACE` (A before W), then the descriptor `ILLEGAL_FIELD` rows in table order,
then `RANGE` (ACC before SPM). All rows are checked for `M = 0` as for `M >= 1`.

`FAULT_ADDR` (64 bits, M-18): `R[reg]` of the offending operand for `TD_SPACE`, an alignment fault and an SPM `RANGE`; `R[acc_reg]` with bit 63 set for the ACC `RANGE`; 0 for any other `ILLEGAL_FIELD`. The `fault` port carries it whole (the first RTL has 32 bits, §8).

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

Column scales: `cs` is FP32 `[KT x N]` at `SPM[R[cs_reg]]`, row-major with row pitch `CSP = roundup(4·N, 64)` B (M-11, review S-1;
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
| `fault_valid`, `fault_cause`, `fault_addr` | out | 1, 4, 64 | to `core/seq` `fault_in` (microarch.md §5). 64 bits since M-18; the first RTL has a 32-bit port |

The ACC (`core/acc`) instantiates `acc_update` (arith.md): this interface fixes what it must accept. `acc_cs` is `COLS · 32` wide
and held; if the ACC pipeline is deeper than its latch, it copies `acc_cs` into its own stage registers.

## 6. Verification

| Test | Covers | Status |
|---|---|---|
| `tb_mxu_array` (iverilog; `scripts/gen_mxu_vectors.py`) | 8 x 8 and 16 x 16 arrays against an independent model (window mapping, swap copy, PE chain with `arith_ref`): back-to-back tiles, bubbles, FP8 / BF16 on consecutive waves, trans / non-trans windows, shadow writes in the tap cycle, NaN / Inf / overflow, column mask on flags, reset, `swap_tap` timing every cycle | **PASS**, 17 mutants (swap / write buffer select, per-buffer taps, skew, column mask, mappings, resets), all killed |
| `tb_mxu` | whole instructions, 6 per size, on `DIM` 8 and 16, each with and without 30% random SPM grant stalls (and `ACC_LAT` 24 in one): behavioural SPM (fixed latency; NaN-valued garbage in every byte nobody requested or that lies past K / N), DTAB, semaphores, and an ACC built from the RTL's `acc_update`. The expected ACC image comes from the **logical** `A x W` (`arith_ref` PE chain per K tile, then `acc_update`), bit-exact over all 256 rows. FP8 / BF16, `trans_w` 0 / 1, K / N / M not multiples of the tiles, M = 1, M from `m_reg` (decoy in the TD), `OVERWRITE` / `ACCUM` (onto the previous instruction's rows) / `RESCALE`, column scales, rowscale with a partial last lane and 2 N tiles, specials; checks every lane address (aligned, inside the SPM), the ACC row spacing, an instruction starts only after its semaphore wait, final semaphore values and OR of `fp_flags`. Also: one `M = 1` decode instruction on 32 x 32 (bit-exact; tile period checked <= 26 cycles), and an 8 x 8 run where the A port refuses requests for 40 cycles after each grant, so the loader runs ahead of the swap and the buffer interlock is exercised. Plus 29 illegal instructions after a reset each (cause, no SPM / ACC / semaphore action, queue stops) | **PASS**, 21 mutants: 17 killed, 4 equivalent (`ncols_pack`, `lane_valid_k`, `ncols_flag` and `no_gap` in the quick mode are not visible at the ACC (the last one is killed by the `ACC_LAT = 24` run): the extra column or lane is masked there; the array test kills the column mask) |
| formal (`tb/formal/mxu.sby`, `make formal`) | `ifdef FORMAL` block of `mxu.sv` on DIM 8 with stub PEs (`mxu_stubs.sv`) and a stub decoder that pushes a free but legal context (`mxu_dec_stub.sv`; M <= 2, K tiles <= 2, N tiles <= 2). Environment: free grants (a request waits at most 2 cycles), `acc_done` within 3 cycles after the last row and not before. **P1** no beat is written over a shadow beat its swap has not copied (also: no duplicated beat), **P2** a swap needs every beat of its tile, **P3** one arbitrary ACC row is updated at most every `ACC_LAT` cycles, **P4** rows leave in pass order (`first`, consecutive rows, `lastp`), **P5** column-scale / rowscale FIFO counts equal an independent count of reservations minus frees and stay within depth, entries are filled before they are read, a rescaling pass starts at element 0 of a lane. Bounded model checking to depth 56 (Yices; three size mixes, 3 to 6 minutes each) passes; cover points (two buffers reused, `sem_inc`, rescale with column scales) are reachable at steps 26-35, so the model is not vacuous | **PASS** to depth 56, not unbounded (below); 9 mutants of the control: 7 caught by P1-P5, `dup_beat` equivalent, `token_live` caught by simulation only |

Bit-exactness: the array output equals `numerics.mxu_mm` for one K tile (`test_arith.py` ties `arith_ref`'s PE chain to it). The
top-level test compares the ACC image after the `acc_update` lanes with the logical `A x W` evaluated tile by tile with `arith_ref`
for the config's `DIM` (table above). It cannot compare with `numerics.mxu_mm` at `DIM` 8 / 16 while that model is FULL-only (review
S-2): once `mxu_mm` takes `dim` (M-13) the expected image comes from `mxu_mm(A, W, mode, cs, acc, rowscale, dim=DIM)`. Tiles with an
MX scale below 2^-126 are exempt (numerics.md N-8).

## 7. Performance (estimates; `tb_mxu` measures them)

- Tile load: `DIM/2` beats (32 cycles `FULL`) at one beat per cycle, 512 B/cycle: the port limit.
- Pass period at small `M`, **measured** (`tb_mxu`, M = 1 passes, steady state): **21-22 cycles per tile at `DIM = 32`** (16 beats; a
  port-bound period would be 16-17). Two shadow buffers (M-9) replaced the single one, which gave `1.25·DIM + 9` (25 cycles at
  `DIM = 16`, 49 predicted at 32). What remains is the skew of tile `n` against the load of tile `n + 2` into the same buffer:
  `P >= ((DIM - 1) + DIM/8 + k) / 2` with `k = 8` fitted to the 32 x 32 point (one parameter, one point, no check at `DIM` 8 or 16).
  **Extrapolation to `FULL` (not simulated): about 39 cycles**, i.e. 16 KiB per 39 cycles = 420 B/cycle of FP8 weights (the port limit
  is 32 cycles, 512 B/cycle). A regression check (`MAX_PERIOD = 26` on the 32 x 32 decode instruction) is in `tb_mxu`.
- **Decode margins with the 39 cycles** (M-17, review S-5; microarch.md §2.2 / §6, numerics.md D1): FP8 (MXFP4 / MXFP8, always the
  fold) 420 elements/cycle against a need of 193 at the core's peak HBM share: **2.2x** (2.6x at 0.85, need 164). The exact
  NVFP4 -> BF16 path (D1: decode) moves 2-byte elements: 210 elements/cycle against 181: **1.16x** (1.36x at 0.85). It is the narrowest stage
  of the weight path, narrower than the DMA's 248 elements/cycle. With a colliding pitch (48 cycles per tile) it is 0.94x. The 39
  decides on a third shadow buffer (period 32, +131 k flops, §9); simulating `DIM` 64 at `M = 1` is open.
- Large `M` (prefill): one A row per cycle. The load of the next tile hides behind `M >= 32` rows, but the period bound above gives
  `P >= ~39` in `FULL`, so **the period is `max(M, ~39)`, not `M`**, for `M < 40` (review S-5).
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
| M-2 | A skew in `mxu_array` as a triangular register set (`ROWS(ROWS+1)/2` words), not by staggering the SPM reads | keeps the A read one contiguous `KB` bytes (conflict-free for 256 B-aligned A bases and `s1`; one stall cycle in 6 of 1,024 starts at 64 B alignment, M-17); the 64k flops are 1.5% of the PE array's arithmetic (arith.md area) |
| M-3 | Swap skewed with the A skew; shadow load by group | removes the `ROWS`-cycle bubble between tiles and the `1.5·ROWS` period |
| M-4 | Lane width = `DIM` bytes, inside the MXU only (the SPM keeps 64 B banks: M-10) | one design for all configs (§2) |
| M-5 | N outer, K inner | consecutive passes update the same ACC rows, so the `cs` / rowscale logic sees one pass sequence per N tile |
| M-6 | ACC RMW hazards solved by pass spacing (§4.4), not by interlocks in the ACC | a pass period is at least the weight load, which is longer than the RMW |
| M-7 | `cs` held per pass in a FIFO (`CS_DEPTH`), rowscale prefetched in 16-row lanes into a FIFO | the ACC input sees them aligned with the rows; no SPM read on the critical path |
| M-8 | MXU raises `ILLEGAL_FIELD` for unaligned operands | memmap.md §4 already requires 64 B tile bases; the lane model needs the pitches too. The rule itself is restated by M-10 (64 B in every config) |
| M-9 | Two shadow buffers, alternating per tile | removes the dependency of the next tile's load on the current swap's skew; cost 131 k flops (about 1.5% of the PE arithmetic), measured 2.2x shorter tile period at `M = 1` |
| M-10 | **Lane shim, one alignment rule** (review S-3; amends M-4, M-8 and §2). `LB = DIM` stays inside the MXU. The SPM keeps 64 B banks and the same client port in every config; the "`DIM`-byte banks" of §2 are withdrawn. A shim `mxu_spm_shim` (parameter `DIM`) between the MXU ports and the SPM client ports serves `DIM`-byte lanes out of 64 B bank rows (`DIM` divides 64, so a lane never crosses a bank row). Alignment, in every config: A base, W base, `s1` of both, `cs_reg` and `rs_reg` addresses are multiples of 64 B (faults `ILLEGAL_FIELD`). Gang semantics (a `core/spm`-side requirement, to go into microarch.md §5): **the grant is the commit**. The shim reads colliding lanes of a request in successive cycles into a collector while `ready` is low, then grants; data follows exactly `SPM_LAT` after the grant. The MXU does not change (it already tolerates arbitrary grant stalls) and cannot deadlock on a bank collision. | one design for all configs; legality independent of `DIM`, so the golden ISA model needs no `DIM` to predict faults; memmap.md §4 already states the 64 B base rule. TINY programs pad `s1` to 64 B |
| M-11 | **Column-scale layout** (review S-1). `cs` is FP32 `[KT x N]` row-major with row pitch `CSP = roundup(4·N, 64)` bytes, defined once in isa.yaml for `DMA_COPY` (writes element (n, t) at `aux + t·CSP + 4·n`) and `MXU_MM` (pass `(t, j)` reads `R[cs_reg] + t·CSP + 4·DIM·j`). `N` is the destination row count of the conversion / the `N` of `MXU_MM`; no new field. The words past `N` are never written by the DMA and are masked by `acc_ncols` at the ACC. The rule "`4·N` a multiple of `LB`" of §4.1 is deleted | the DMA cannot pad `N` (no TD carries a pitch); a derived pitch lets ragged `N` (e.g. 40 keys) work |
| M-12 | **Orientation of the golden model** (review S-1). `numerics.fold_e4m3` returns `cs` as `[K tiles, N]` and `unfold` takes it in that orientation, the same as `numerics.mxu_mm` and the ISA. `fold_e4m3`, `unfold`, `cvt_source_check`, `test_arith.py` / numerics tests and the generated `numerics_experiments.md` change with it (to do, with `make numerics`) | one orientation everywhere; chosen over a transposing helper |
| M-13 | **Reduced configs** (review S-2). The fold tile stays 256 FP8 / 128 BF16 in every config, and `cs` stays `[ceil(K / 256) x N]` (BF16: `/128`). Pass `t` of the MXU uses `cs` row `t >> log2(R)` with `R = 64 / DIM` (FP8: `256 / (4·DIM)`; BF16: `128 / (2·DIM)`; `R = 1` in `FULL`). The `cs` FIFO still gets one entry per pass (re-fetched). `numerics.mxu_mm` gets a `dim` parameter (default 64): PE rows `0..dim-1`, K tile `4·dim` / `2·dim`, `cs[t // R]`; N-3 is restated with `DIM`. Bit-exactness is per `DIM` (TINY / MID results differ from `FULL` in the last bits, review S-2 item 1). ACC rows are `DIM` FP32 wide in `TINY` / `MID` (memmap.md §4, microarch.md §5 to say so) | keeps the fold numerics and the DMA independent of the config; equal to a DMA that replicates each scale `R` times, with no extra storage |
| M-14 | **`M = 0`** (review S-7). A no-op with completion: all legality checks run as for `M >= 1` (a program faults or not independently of the data), no SPM or ACC access, no `fp_flags`; the instruction takes its place in the in-flight FIFO as a zero-row entry, retires in order after the earlier instructions' `acc_done`, and pulses `sem_inc {ssem, sinc}`. `M` is `R[m_reg]` as 64-bit unsigned, compared in full (`R[m_reg] = 2^16 + 1` is not truncated); `NT·M > ACC_ROWS` is `RANGE`. `1 <= M` is removed from §4.1. `N >= 1` stays. Needs the decoder, the exit stage and a formal re-run (`make formal`, mxu) | the MoE example of isa.md §7 passes a data-dependent count; zero tokens for an expert is routine in decode |
| M-15 | **`BF16X3` is reserved in v1** (review S-6): `ILLEGAL_FIELD`, as `SCALE_INLINE`. isa.yaml, isa.md §5 / §6 and the coverage table (isa.md:339) are changed to say so, then `make isa`. No RTL change. `numerics.mxu_mm_bf16x3` stays as the definition for a later version; the mHC mixer uses two `NORMAL` BF16 passes (numerics.md D2). §9 keeps the sketch of the implementation | no user of the instruction; the operand dtypes (FP32 A / W) were never defined |
| M-16 | **`WRAP` is DMA-only** (review S-8). The MXU operands keep faulting on any TD flag. memmap.md §7 is reworded: the DMA appends at `pos mod window`, and attention reads the window in slot order (K and V in the same order; the softmax is order-invariant), so the window needs no copy. Open: the `WINDOW` mask of `VSOFTMAX` must be slot-aware, or the ring must be full; not yet checked against the VPU spec | WRAP has no start-row field and is the identity on a linear walk, so it has no meaning on an MXU operand |
| M-17 | **Load beats and the tile period** (review S-4, S-5). No change to the beat order or the buffers. The pitch rule (microarch.md §2.3, memmap.md §4) is re-derived for the 8-row beat (`scripts/mxu_review/spec_checks.py`, pitch scan): pad a 2 KiB pitch by 1 KiB (3072 B); 4 KiB + 512, 8 KiB + 512, 16 KiB + 1 KiB and 32 KiB or more + 512 stay as they are. Only 26 of the 256 pitches 64 B..16 KiB are conflict-free for all three beat shapes at 2 KiB-aligned bases, so a compiler pitch table covers the other (non-power-of-two) pitches; the shim of M-10 makes a collision slower (one stall per colliding beat) but never wrong. The tests I-13 / V-8 of `test_isa.py` are replaced by an 8-row-beat test. §2 M-2 and §7 are corrected: the A read is conflict-free for 256 B-aligned A bases and `s1` (at 64 B alignment 6 of 1,024 starts cost one stall cycle); the period for `M < 40` is `max(M, ~39)`, not `M`; the decode margins (microarch.md §2.2 / §6, numerics.md D1, status.md) are restated with the 39-cycle period: FP8 2.2x, NVFP4 -> BF16 1.16x at the peak HBM share (1.36x at 0.85). The 39 is an extrapolation; simulating `DIM` 64 at `M = 1` is still open (§9) and decides on a third shadow buffer (period 32, +131 k flops) | no MXU RTL change; the buffer decision waits for a measured period |

| M-18 | **Faults of `MXU_MM`** (review S-9; the points of S-9 that M-10 .. M-17 left open, chosen when the specs were updated, 2026-11-08). Undefined values fault `ILLEGAL_FIELD`: `mode` 2-7, `acc_mode` 3, non-zero bits `[38:0]` of the word, `rs_reg != 0` without `RESCALE`, `RESCALE` with `rs_reg = 0`. `FAULT_ADDR` is the 64-bit CSR: `R[reg]` of the offending operand for `TD_SPACE`, alignment and an SPM `RANGE`; `R[acc_reg] \| 1<<63` for an ACC `RANGE`; 0 for every other `ILLEGAL_FIELD` (a queue entry has no PC). Priority: word fields, `TD_SPACE` (A, W), descriptor `ILLEGAL_FIELD` in table order, `RANGE` (ACC, SPM). In-flight instructions of any engine when a fault occurs may be cut off or complete: software relies on neither. `core/seq` gets a `fault_in` (first fault latched, simultaneous ones in the order front end, DMA, MXU, VPU, NI) and an `eng_stop` (microarch.md §5). The `aux_reg` of the DMA is 64 B aligned (partner of the alignment of `cs_reg`) | one deterministic fault per program, independent of the config and the data, so the golden ISA model predicts `FAULT_CAUSE` / `FAULT_ADDR`; a stale `rs_reg` is a compiler bug worth a fault |

Decisions M-10 .. M-17 were taken 2026-11-08, answering review part S (S-1 .. S-8; docs/reports/mxu_review.md); M-18 completes S-9.
**Specs updated** (S-n cited at each change): isa.yaml (`MXU_MM`, `DMA_COPY` `aux_reg`, `mxu_mode`, `WRAP`, fault CSRs; `make isa`),
isa.md §4-8, memmap.md §4 / §7, numerics.md N-2 / N-3 / N-8 / D1, microarch.md §2.2 / §2.3 / §4 / §5 / §6, this document (S-13).
**Still old** and to follow these rows: `rtl/core/mxu*.sv` (alignment by `LB` instead of 64 B, the `4·N` rule, `M = 0` and
`R[m_reg]` width, mode / reserved bits / `rs_reg` checks, fault priority and 64-bit `fault_addr`, `cs` row pitch `CSP` and row
`t >> log2 R`; the shim `mxu_spm_shim` does not exist), `tb_mxu` (29 illegal cases, the `4·N` case), `tb/formal/mxu.sby` (re-run after
the `M = 0` change), `sw/npumodel/numerics.py` (`mxu_mm` `dim`, `fold_e4m3` / `unfold` / `cvt_source_check` orientation) with
`test_arith.py` / `test_numerics.py` and the generated `numerics_experiments.md`, `scripts/gen_mxu_vectors.py`, `core/seq` (`fault_in`,
`eng_stop`), `core/spm`, and `docs/reports/status.md` (line 191 "the MXU is never the decode bottleneck"). Review items not decided
here: S-10 (`RANGE` on operand extents), S-11 (`core/acc` stream contract), S-12 (DTAB / prefetch ordering wording).

## 9. Open items
- `BF16X3` (FP32 operands split into BF16 hi / lo on the fly: three passes per K tile, the W fetch reads 4 B elements, A likewise):
  reserved in v1 (M-15; the first RTL faults it, as the ISA now says). Needs a splitter (`x_hi = RNE_BF16(x)`, `x_lo = RNE_BF16(x - x_hi)`, exact in FP32), a third pass
  order in the loop (§4.2 `t` becomes `(t, pass)`) and a legality row. The hyper-connection mixer uses two `NORMAL` BF16 passes
  with pre-split weights (numerics.md D2), so it does not need the instruction.
- A third shadow buffer would take the period from about 39 to the 32-beat port limit. Not "not needed": on the exact NVFP4 -> BF16 decode path the MXU is the narrowest stage at 1.16x (§7, review S-5), so the decision waits for a simulation of `DIM` 64 at `M = 1`. Bank-conflict-aware beat order.
- `RANGE` is defined on bytes read, but the MXU reads past its operands by design (4 `cs` lanes per pass, rowscale lanes past `M`, non-trans bytes past `N`): define it on the operand extents and let over-reads wrap (review S-10, undecided). `core/acc` stream contract (S-11, undecided). DTAB read and weight prefetch start after the instruction's own semaphore wait, and §4.4 (b) includes the last pass of the previous instruction (S-12).
- Timing (Phase 6): the fan-out of A and `swap`/mode along a PE row, the 66-stage sideband, whether `REG_DOT` is enough.
- Formal: k-induction does not close (its step starts from arbitrary pipeline contents; P1 and P3 need a joint invariant of the free flags, the weight-tag pipeline and the shadow bookkeeping, and of the sideband chain against the gap counter), so P1-P5 are bounded to depth 56. Bounded liveness (an instruction in flight completes within N cycles) did not scale (16 minutes per step at depth 52); deadlock freedom rests on simulation (random and 40-cycle A stalls) and on the cover points. Tap timing relative to the swap is checked by `tb_mxu_array`, not by P1, which uses the tap signal itself. Larger tiles (`BPG > 1`, `DIM = 16`) are not covered by the proofs. With M <= 2 a pass is too short for the next instruction's token to arrive during it, so the proofs cannot see a stream FSM that reads the token registers instead of its own copy (mutant `token_live`, not caught at depth 72); the system test kills it by a wide margin (long passes).
