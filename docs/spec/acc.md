# Accumulator (`rtl/core/acc*.sv`, `core/acc`) interface

Status: **specification only, 2026-11-08; `core/acc` is not built.** This is the contract that answers review item S-11 (all seven
points) and the ACC part of S-2 (`docs/reports/mxu_review.md`). It replaces the `acc_rmw` row of the MXU table and the `mxu_rmw` /
`conflict` rows of the ACC table in [microarch.md](microarch.md) §5. The producer is `core/mxu` ([mxu.md](mxu.md) §4.2, §4.5, §5); the
arithmetic lane is `acc_update` ([arith.md](arith.md) "Integration contract" item 5, N-3 step 4 in [numerics.md](numerics.md)).
Decisions are numbered ACC-1 .. ACC-9 (§8). Where this document and mxu.md §5 / microarch.md §5 differ, this document wins and the
others carry a pointer.

## 1. What `core/acc` is

2048 rows x 64 FP32 in `FULL` (512 KiB); rows of `DIM` FP32 in `MID` / `TINY`. Two clients:

- **MXU**: one row per cycle in, as a push stream (§3). Per row it does a read-modify-write with `acc_update`
  (column scale, optional rescale of the old value, add or overwrite). The stream has **no `ready`**.
- **VPU**: 4 rows read and 4 rows written per cycle (epilogues, `ACC` as an operand or destination). Fixed latency, but it can be
  refused (§7).

`core/acc` owns: the SRAM and its banking, the RMW pipeline, `ACC_LAT` (§5), the completion pulse `acc_done` and the `fp_flags`
of the `acc_update` lanes. It does not own the instruction (the MXU raises `sem_inc`) or the legality of rows (the MXU checks
`R[acc_reg] + NT·M <= ACC_ROWS` before the first row, mxu.md §4.1, so `acc_row < ACC_ROWS` always).

## 2. Parameters and widths per configuration

| Quantity | Formula | `FULL` | `MID` | `TINY` |
|---|---|---|---|---|
| `DIM` (row width in FP32 words, = MXU columns) | | 64 | 16 | 8 |
| `ACC_ROWS` | | 2048 | 512 | 256 |
| Row bytes | `4·DIM` | 256 | 64 | 32 |
| SRAM bytes | `ACC_ROWS · 4·DIM` | 512 KiB | 32 KiB | 8 KiB |
| `acc_part`, `acc_cs` width | `DIM · 32` | 2,048 | 512 | 256 |
| `acc_row`, `acc_ncols` width | **fixed at the `FULL` widths** (11, 7) | 11, 7 | 11, 7 | 11, 7 |

- **Row width is `DIM` in every config** (S-2.3, M-13). A row index addresses `DIM` words; the VPU's "rows of 64" in
  microarch.md §5 and memmap.md §4 read "rows of `DIM`" outside `FULL`. Semantic widths follow the config; the ISA field widths and
  the two scalar port widths above stay those of `FULL` (microarch.md §4: unused high bits of `acc_row` / `acc_ncols` are 0).
- `acc_ncols` is in `1..DIM` (`N >= 1`, mxu.md §4.1), never 0.
- Parameters of `core/acc`: `DIM`, `ACC_ROWS`; internal (not visible at a port): `ACC_BANKS`, the stage split of §5.
- The ACC contents are not reset. The valid bits of the RMW pipeline are.

## 3. The MXU stream (`acc_*`)

Direction as seen from `core/acc`. Names are those of `rtl/core/mxu.sv` (they do not change).

| Port | Dir | Width | Meaning |
|---|---|---|---|
| `acc_valid` | in | 1 | an output row is presented **in this cycle** and is consumed in this cycle. No `ready`: ACC-2 |
| `acc_row` | in | 11 | ACC row index (`< ACC_ROWS`) |
| `acc_part` | in | `DIM · 32` | partial sum row, FP32 per column |
| `acc_ncols` | in | 7 | columns `< ncols` are active (written, flagged); the others are untouched (§6) |
| `acc_rescale` | in | 1 | multiply the old row by `acc_rowscale` first |
| `acc_overwrite` | in | 1 | ignore the old row (first K tile of `OVERWRITE`) |
| `acc_use_cs` | in | 1 | multiply `acc_part` by `acc_cs` (column scale) |
| `acc_rowscale` | in | 32 | `rowscale[m]`; meaningful with `acc_rescale` |
| `acc_cs` | in | `DIM · 32` | column scales of the pass; meaningful with `acc_use_cs` |
| `acc_last` | in | 1 | last row of the instruction |
| `acc_done` | out | 1 | pulse, **exactly `ACC_LAT` cycles after the `acc_valid` cycle of an `acc_last` row**: its write is visible (§5, §9) |
| `fp_flags` | out | 4 | `FP_STATUS` order; valid-qualified pulse; ordering in §6 |

**Control combinations** (mxu.md §4.2; `core/acc` may assert them in simulation): `acc_rescale` and `acc_overwrite` are never both set
(arith.md item 5: flags undefined); `acc_overwrite` and `acc_rescale` only on the first K tile of an N tile; `acc_use_cs` is
constant over an instruction. Any other combination is legal: `{rescale, overwrite, use_cs} = {0,0,0}` is `ACCUM` / `t > 0`.

**Sampling rule (ACC-4).** `acc_cs`, `acc_rowscale`, `acc_part`, `acc_row` and the controls are valid **only in the cycle in which
`acc_valid` is high, and `core/acc` samples them in that cycle**. The MXU happens to hold `acc_cs` until the next pass and to
present the same `acc_cs` for every row of a pass; `core/acc` must not rely on either. In particular it has no per-stage copy of
`acc_cs` (item 5 of S-11 is closed by §4).

**One row per cycle, any cycle.** `core/acc` accepts a row in every cycle in which `acc_valid` is high, whatever the VPU does
(ACC-2, §7). It never drops a row and never returns a stall.

**`M = 0`** (mxu.md M-14) presents no row and so produces no `acc_done`. There is exactly one `acc_done` pulse per `acc_last` row.

## 4. Where the column scale is applied (ACC-3)

`p = use_cs ? RNE(part · cs) : part` is computed **in an input stage on the row as it arrives, in parallel with the SRAM read
of the old row**, before the old value is available (N-3 step 4: `P_t · cs` does not depend on the ACC contents, so this equals the
`acc_update` order). Only `p` (32 bits per lane) is carried to the join; `cs` is dead after the input stage. The rowscale is
carried to the rescale multiplier with the row (32 bits per row per stage, not per lane).

```
cycle  T          T+RD_LAT                T+RD_LAT+MUL         T+...+ADD       T+ACC_LAT = V
       acc_valid  old row (SRAM)          rescaled old row     sum             write visible
       ├ read issue (bank of acc_row)
       └ input stage: p = use_cs ? part*cs : part   (CS_STG <= RD_LAT cycles, hidden under the read)
                                ├ rescale mul (always MUL_STG stages; pass-through when !rescale) ┤ add (ADD_STG) ┤ write (WR_LAT)
```

- Two-stage structure, one result: stage A = `fp32_mul(part, cs)` and the `use_cs` select; stage B = `fp32_mul(acc, rowscale)`,
  select, `fp32_add`, `overwrite` select. **The pair must be bit-identical to the combinational `acc_update`, flags included**
  (rtl/arith is not changed; `core/acc` builds the stages from `fp32_mul` / `fp32_add`). The `NAN_IN` rule of `acc_update`
  (`nan_add`) crosses the stage boundary: stage A carries `part_nan & !use_cs` (one bit per lane) to the add, stage B adds `acc_nan &
  !rescale`. The flags of stage A and stage B are ORed per row.
- No bypass: a row takes `ACC_LAT` cycles whatever its controls are (fixed latency; the MXU spacing of §5 relies on it).

## 5. `ACC_LAT`: one owner, one assertion (ACC-1)

`ACC_LAT` is the number of cycles from the `acc_valid` cycle of a row to the cycle `V` in which its write is visible. "Visible"
means: a read issued in cycle `V` (an MXU RMW read or a VPU read) returns the new data. It is **the minimum spacing the MXU keeps
between two updates of the same ACC row**, and it is the same number as the `acc_done` delay.

**Owner.** A package `rtl/core/acc_pkg.sv`, compiled before `mxu.sv` and `acc*.sv` in every flow (lint, `make unit`, formal, synthesis):

```
package acc_pkg;
  localparam int ACC_RD_LAT  = 2;   // SRAM read, address to data (microarch.md §5: "read 2 cycles")
  localparam int ACC_CS_STG  = 2;   // input stage: column-scale multiply (stage A), overlapped with the read
  localparam int ACC_MUL_STG = 2;   // stage B: rescale multiply
  localparam int ACC_ADD_STG = 3;   // stage B: add, overwrite select
  localparam int ACC_WR_LAT  = 1;   // write issue to visible
  localparam int ACC_LAT = ((ACC_RD_LAT > ACC_CS_STG) ? ACC_RD_LAT : ACC_CS_STG) + ACC_MUL_STG + ACC_ADD_STG + ACC_WR_LAT;  // 8
endpackage
```

The stage counts are a **budget** that reproduces the 8 cycles `mxu.md` has used so far; they are fixed when `core/acc` is
written and timed (Phase 6). Changing them changes `ACC_LAT` here and nowhere else. If the VPU arbitration of §7 needs a registered
copy of the incoming row address (timing), add `ACC_IN_STG = 1` to the sum: `ACC_LAT` becomes 9, nothing else changes.

**Users.**
- `core/acc` builds its pipeline from the constants above. It has no `ACC_LAT` parameter of its own and its `acc_done` delay counts
  `acc_pkg::ACC_LAT`.
- `core/mxu` keeps its `ACC_LAT` parameter (tests raise it to make gaps longer) with default `acc_pkg::ACC_LAT`, and checks at
  elaboration:

```
if (ACC_LAT < acc_pkg::ACC_LAT) begin : g_acc_lat_chk
  $error("mxu ACC_LAT is below acc_pkg::ACC_LAT");   // one string argument: Icarus accepts no format arguments here
end
if (ACC_LAT > 255) begin : g_acc_lat_max           // gap_cnt is 8 bits
  $error("mxu ACC_LAT above 255");
end
```

A value above the package value is legal (extra idle cycles, no wrong result). The check was tried on Icarus, Yosys and Verilator
with a generate-`if` and a single-string `$error`: all three stop elaboration when the condition holds (Verilator reports
`%Warning-USERERROR` and exits 1). A testbench that replaces `core/acc` by a behavioural ACC (`tb_mxu`, the formal stub) takes its
latency from the package, or overrides the MXU parameter upwards and keeps its own model at least as slow.

**What the MXU guarantees** (mxu.md §4.4, formal P3): two `acc_valid` cycles with the same `acc_row` are at least `ACC_LAT` cycles
apart, whatever the SPM grants do, including across instructions with any `acc_base`, `M` and `N` (M-22: after the last pass of
an instruction the MXU waits `ACC_LAT - 2` cycles before the next pass issues, review R-1; the earlier gap of `ACC_LAT - M` cycles only
covered the passes of one instruction).
**What `core/acc` guarantees:** a row presented at cycle `T2 >= T1 + ACC_LAT` reads the result of the row presented at `T1`. No
hazard logic, no forwarding.
Failing configuration of review S-11(iii) (ACC with a 10-cycle loop, MXU with 8) cannot elaborate any more: the loop length is
`acc_pkg::ACC_LAT` by construction, and the MXU refuses a smaller value.

The `core/acc` simulation checker (`assert`) also flags two same-row `acc_valid` cycles closer than `ACC_LAT`, so a testbench with a
foreign driver is caught on the ACC side.

## 6. Masked writes, flags and completion

**Masked row writes (ACC-5).** The SRAM write is word-masked: lane `c` is written iff `c < acc_ncols`. The words `c >= acc_ncols`
keep their old content (they may be live VPU data in the same row, memmap.md §4: ACC rows are shared with the VPU). Their `acc_part`
bits are `+0` or garbage; the lanes' flags are masked by the same condition. The SRAM needs per-32-bit-word write enables (or an
internal merge with the old row, which the RMW read already has). For the read side nothing is masked.

**Flags.** `fp_flags` is the OR over the active lanes (`c < acc_ncols`) of the flags of stage A and stage B of one row, ANDed with
the row's valid, as a one-cycle pulse. It is the `fp_flags` of the ACC in the `core/seq` `fp_flags_in` list (microarch.md §5), separate
from the MXU's own `fp_flags` (the PE chain).

**Ordering (ACC-6; S-11 item 4; microarch.md "Floating-point flags").** The flags of a row are driven **in a cycle `<=` the cycle `V`
in which the row's write is visible**. For the `acc_last` row, `V` is the cycle of `acc_done`, so its flags are out no later than
the `sem_inc` of the instruction, which `core/mxu` raises combinationally from `acc_done` (the same cycle). Flags of every earlier row
are earlier or equal: rows are in order and the flags travel with the row through the same pipeline. A testbench check: for every
`acc_last` row, the flag pulse of its row (possibly 0) is at a cycle `<= acc_done`, and the sticky OR at the cycle after `acc_done`
equals the OR over all rows of the instruction.

## 7. SRAM ports and priority (ACC-2)

The SRAM organisation (banks, `ACC_BANKS`, 1R1W per bank) is `core/acc`'s. The contract is behavioural.

- **The MXU has priority on every SRAM port conflict, not only on the same row.** An MXU RMW needs one read port (in the `acc_valid`
  cycle, the bank of `acc_row`) and one write port (in the write cycle, `V - WR_LAT`, the bank of the same row). A VPU access that needs any
  read (or write) port the MXU needs in that cycle, whatever its row, is **refused**; the MXU is never delayed. The MXU read is
  reserved whenever `acc_valid` is high (even for `overwrite`, which does not need the old value: the schedule is data-independent).
- **VPU port protocol.** `vpu_rd` and `vpu_wr` get a `ready` each (`vpu_rd_ready`, `vpu_wr_ready`), replacing the old `conflict`
  level. A 4-row request is accepted or refused as a whole. Read data returns exactly `VPU_RD_LAT` cycles after the accepted request
  (fixed). A refused request is repeated by the VPU; it must not consume data or advance on a refused cycle.
  `ready` may depend combinationally on the request's rows and on `acc_valid` / `acc_row`; the VPU's request must not depend on `ready`.
- **No deadlock.** The MXU never waits for the ACC or the VPU (no back-pressure), so the only waiting party is the VPU, and the MXU
  stream is finite (one instruction's rows). Starvation of the VPU lasts at most the MXU's streaming time, which the VPU's
  semaphore wait on the MXU completion normally excludes.
- **Same-row accesses of the VPU and the MXU** remain a software ordering matter (semaphores, isa.md §3: `MXU: ACC written`). The
  hardware guarantee is: no port conflict corrupts or delays an MXU update, no other row is touched, and a VPU write to a row
  during its MXU read-to-write window is overwritten by the MXU write (the MXU update is the later write of the row).
- Timing risk, Phase 6: the path acc_valid / acc_row to `vpu_*_ready`. Fallback: register the incoming row (`ACC_IN_STG`, §5).

## 8. Decisions

| # | Decision | Why |
|---|---|---|
| ACC-1 | One `ACC_LAT`, owned by `acc_pkg` (sum of the RMW stage constants); `core/acc` has none of its own; `core/mxu` takes it as parameter with that default and fails elaboration below it (and above 255); the same number is the `acc_done` delay | S-11(iii): the MXU parameter had no tie to the ACC; spacing correctness is a property of the loop length, which has one definition |
| ACC-2 | MXU stream without `ready`; the ACC accepts one row per cycle always; the MXU has priority on every SRAM port conflict; the VPU ports get `ready` (replaces `conflict`); VPU request whole-or-nothing; MXU read port reserved on every `acc_valid` | S-11(ii): "same row only" is not enough with a banked SRAM; adding a `ready` to the stream would break the P3 spacing proof and the fixed `acc_done` delay |
| ACC-3 | The column scale is applied to `part` in an input stage, in parallel with the SRAM read; `cs` is not carried; the `ACC_LAT` formula counts `max(ACC_RD_LAT, ACC_CS_STG)`, so a slower multiplier lengthens `ACC_LAT` instead of breaking the overlap | S-11(v): no 2,048-bit copy per pipeline stage; allowed by N-3 (`P_t · cs` is independent of the ACC) |
| ACC-4 | All stream payload is valid and sampled only in the `acc_valid` cycle; the MXU's holding of `acc_cs` is not part of the contract | the ACC must not need a latch; gives the MXU freedom to pop the `cs` FIFO early |
| ACC-5 | Word-masked row writes for columns `>= acc_ncols`; flags masked likewise; read unmasked | S-11(vi): the row is shared with the VPU; N tails must not clobber live words |
| ACC-6 | Flags of a row in a cycle `<=` its write-visible cycle; for `acc_last`, `<= acc_done` | S-11(iv): microarch.md "flags no later than `sem_inc`" had no owner on the ACC side |
| ACC-7 | Row width is `DIM` words in every config; `acc_row` / `acc_ncols` ports keep the `FULL` widths | S-11(vii), S-2.3, M-13 |
| ACC-8 | `acc_done` is exactly `ACC_LAT` cycles after an `acc_last` row; one pulse per `acc_last`; `M = 0` produces none | the in-flight FIFO of the MXU pops on it; the formal environment can be exact |
| ACC-9 | The stage structure of §4 is bit-identical to `acc_update`, flags included, with `part_nan & !use_cs` carried to the add | `acc_update` stays the single reviewed definition (tb_acc_update, 20,012 vectors, mutants) |

## 9. Verification required of `core/acc`

| Test | Covers |
|---|---|
| equivalence | the two-stage lane against combinational `acc_update`, same vector set as `tb_acc_update` plus random rows: `y`, flags, including `NAN_IN` with `use_cs = 0 / 1` and `rescale = 0 / 1` |
| masked writes | `acc_ncols < DIM`: words `>= ncols` bit-unchanged from a poison pattern; their NaN / Inf `part` raise no flag |
| sampling | junk on `acc_cs`, `acc_rowscale`, `acc_part` when `acc_valid = 0`, and a new `acc_cs` the cycle after each row: results unchanged |
| spacing | same row at exactly `ACC_LAT`: sees the previous result; at `ACC_LAT - 1`: sees the stale value (negative control, the test must be able to fail) |
| priority | random full-rate VPU traffic against a full-rate MXU stream, all rows and all bank patterns: the MXU result and `acc_done` timing are identical to a run with no VPU traffic; VPU data exact; refused requests repeat |
| flags | for every `acc_last` row, the row's flag pulse is at a cycle `<= acc_done`; sticky OR matches |
| `acc_done` | exactly `ACC_LAT` after `acc_last`; one per `acc_last` |
| elaboration | `mxu` with `ACC_LAT = acc_pkg::ACC_LAT - 1` fails (script test, compile expected to fail on Icarus, Yosys, Verilator) |
| configs | lint and build at `DIM` 8 / 16 / 64 with `ACC_ROWS` 256 / 512 / 2048 |
| mutants | stage split off by one in `ACC_LAT`, unmasked tail write, `cs` taken from the cycle after, flags one cycle late, priority inverted on a different-row same-bank conflict, `nan_add` without the carried bit |

## 10. Changes to `rtl/core/mxu.sv` and its environment (not made)

| # | Where | Change |
|---|---|---|
| 1 | new `rtl/core/acc_pkg.sv` | the package of §5. Added to the file lists: `Makefile` (`CORE_SV` is a glob, `acc_pkg.sv` sorts before `mxu.sv`; check lint and `make unit` order), `tb/formal/mxu.sby` (`read -formal acc_pkg.sv` before `mxu.sv`), synthesis scripts |
| 2 | `mxu.sv:12` `parameter int ACC_LAT = 8` | default `acc_pkg::ACC_LAT`; add the two generate checks of §5 (lower and 255 bound; `gap_cnt` is 8 bits, `mxu.sv:376`, `:440`) |
| 3 | `mxu.sv:7-8` header comment | state the contract by reference to acc.md (no `ready`, `acc_done` exactly `ACC_LAT` after the `acc_last` row, spacing >= `ACC_LAT`) |
| 4 | `mxu.sv:113`, `:125` (`inf_pop = acc_done`) | with M-14 (`M = 0`): an `M = 0` entry retires in order **without** an `acc_done` (one `acc_done` per `acc_last` row only, §3). This is the already planned M-14 change; the ACC contract fixes that `acc_done` cannot be used to pop it |
| 5 | ports `acc_*` (`mxu.sv:57-67`) | none: names, widths (`acc_row` 11, `acc_ncols` 7, data `DIM · 32`) and directions stay. `acc_cs` may keep being held (harmless); the cs FIFO entry still lives until the last row of the pass, so `cs_free_ev` (`:514`) is unchanged |
| 6 | formal environment `mxu.sv:526`, `:560-570` | replace "`acc_done` within 3 cycles after the last row" by "exactly `ACC_LAT` cycles after" (or the window 1..`ACC_LAT`, still sound); P3 already uses `ACC_LAT`. Re-run `make formal` for the mxu (also due to M-14); cover points shift with `ACC_LAT` |
| 7 | `tb_mxu.sv:182` `acc_done = dsr[4]` ("five cycles") and the combinational ACC model (`:170-205`) | `acc_done` `ACC_LAT` cycles after the last row (shift register of that depth, default from the package; the spacing check at `:200` already uses `ACC_LAT`). The model keeps updating the image in the row cycle (it is correct because the MXU never reads a row inside its `ACC_LAT` window); the `ACC_LAT(24)` run stays legal (above the package value) |
| 8 | `tb_mxu.sv:276-282` (second MXU instance, `acc_done(1'b0)`) | unchanged (illegal-instruction run, no rows) |
| 9 | `mxu.md` | §4.4 and §7 (latency `SPM_LAT + ARR_LAT + M + ACC_LAT` stays correct: `ACC_LAT` includes the input stage), §5 ACC rows and the `acc_cs` sentence (holding is not a contract), §8 M-19 and the "S-11 undecided" lines point here. Done with this document |
| 10 | `scripts/gen_mxu_vectors.py` | none (expected image comes from the logical `A x W`) |

Not an RTL change but a precondition: `acc_update` is combinational and unchanged (rtl/arith, locked vectors and mutants). The
split into the two stages of §4 lives in `core/acc` and is proved equal by the equivalence test.

## 11. Open items

- The stage split of §5 (2 / 2 / 3 / 1) is a budget; confirm with `fp32_mul` / `fp32_add` timing in Phase 6. `ACC_LAT` can only
  grow by an explicit edit of `acc_pkg`, and the MXU adapts by construction; a longer `ACC_LAT` costs idle cycles only for passes with
  `M < ACC_LAT` (mxu.md §4.4).
- `ACC_BANKS` and the bank hash (row-interleaved vs. row-block): decides how often the VPU's 4-row access is refused during an MXU
  stream (with 4 banks every VPU 4-row access conflicts with every MXU row; 8 or more banks let the two overlap). Not part of the
  contract; a core/acc design item with a performance experiment.
- VPU side: `core/vpu` gets `acc_wr` (the VPU table of microarch.md §5 lists only `acc_rd`) and the refuse/repeat rule of §7.
- Whether the VPU should be told to expect the MXU read-to-write window per row (a hardware interlock instead of software ordering):
  not needed by any instruction in isa.md §7; left to software.
