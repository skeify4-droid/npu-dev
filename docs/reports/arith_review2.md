# Re-review: the fixes of the arith review (`51f8cbe`)

Second independent review of branch `p3-arith`, 2026-11-08. Subject: commit `51f8cbe` ("fix the findings of the independent
review (F1-F13)") against the findings of [arith_review.md](arith_review.md). Each finding was checked from the code and the
diff, not from the commit message.
- Scope: `rtl/arith`, `tb/unit/tb_*` and `test_arith.py`, the generator, the Makefile, `numerics.py`, and the changed
  numerics.md / arith.md text.
- Nothing in `rtl/`, `tb/`, `sw/`, the Makefile or the specs was changed. The new scripts are in `scripts/arith_review/`
  (listed at the end). Nothing was committed.
- Part 3 was derived from the spec text before `arith_ref` was consulted; `arith_ref.py` was called but not read.
  `rtl/arith` had already been read for parts 1 and 2, so part 3 is independent of the reference, not of the RTL.

## Verdict

- **The 13 findings: 9 fixed, 4 partly fixed (F6, F10, F12, F13), none not fixed.** No regression; every check of the
  author reproduces (table below). The partial ones are documentation and spec edits that did not reach every place.
  - F6: N-1 still says BF16 subnormals are "(flushed)".
  - F10: the short-tile rule names the element but not its scale.
  - F12: N-5's SATURATE and MXU rows contradict the new N-2 / N-3 text, and the NVFP4 step cites isa.md for a statement
    isa.md does not make.
  - F13: status.md calls the committed fixes "working tree".
- **New test infrastructure (part 2): it now catches every accidental damage the first review used**, but it does not bind
  the vectors to the generator that should have made them.
  - A truncated file passes as soon as `counts.svh` is edited to match, down to a single row, or zero rows with `CNT = 0`
    (S1, S3). That includes a full `make unit` run (rc 0, "ALL PASS").
  - An old but self-consistent vector set (the generator of `815acb1`) lets the pre-fix `acc_update` and `cvt_fp32_fp`, and
    the 38-bit FP8 adder, pass (S4).
  - Only `fp8_mul`, `cvt_dec` and `mx_dequant_bf16` resist this, with hard-coded exhaustive counts.
- **New mutants: 35, of which 24 killed, 5 equivalent, 6 survivors that are not equivalent.**
  - Two are vector gaps in `acc_update`: Inf read as NaN by the F3 logic.
  - One is a gap in the new `mxu_pe` reset test: the dot's OVERFLOW register is never set during reset.
  - One drops the flags of the first cycle after reset.
  - One is in an untested `fold_amax` configuration.
  - A sixth survivor, `acc2_inv_supp`, is not a bug but an **unpinned spec choice**: no vector has INVALID and NAN_IN
    together in the ACC lane, so the ambiguous rule below is not tested either way.
- **Spec (part 3): 77 cases worked by hand from the text agree with `arith_ref` and the oracle wherever the text is clear.**
  - Seven are ambiguous. The most important: does `INVALID` survive in the ACC lane when the lane also has a NaN input?
  - numerics.md has 8 contradictions or stale sentences (C1-C8).
- **Integration (part 4):** of ten assumptions the arith blocks make about `core/mxu`, `core/acc`, `core/dma` and
  `core/ni`, microarch.md (the integrators' interface spec) states none. Three are stated nowhere. Proposed contract at
  the end.

## Reproduction

All runs on this machine, one at a time, every heavy one under `scripts/memrun` (logs `logs/rev2_*`; `make syn` wraps itself). Vectors were regenerated first
(`rm build/arith/.stamp`).

| Check | Result |
|---|---|
| `make lint` | clean: 53 lint runs (20 arith tops, 24 parameter variants, `rtl/common`), rc 0 |
| `make unit` | ALL PASS, 92 s, 0.20 GB; every PASS line carries the exact count of `counts.svh` (e.g. `fp8_dot4` 200,908, `bf16_dot2` 168,075, `mxu_pe` 26,937 rows, four variants, reset tests) |
| `make pytest` | 263 passed, 43 s |
| `run_arith_mutants.py` | 39 mutants all as expected (35 killed, 4 equivalent), 202 s, 0.20 GB |
| `scripts/arith_review/my_mutants.py` | 44 mutants all as predicted (36 killed, 8 equivalent), 358 s, 0.20 GB |
| `scripts/arith_review/check_files.py` | 41 files, 2,368,489 rows, 0 disagreements with the independent oracle, 17 s |
| `scripts/arith_review/handpicked.py` | 279 hand cases, 0 disagreements |
| `make syn BLOCK=mxu_pe` | 6,580 cells, 70 flops (6 async-reset flag flops, 64 data), no warnings, 1.5 s, the same as before |
| `make syn BLOCK=cvt_fp32_int` | 837 cells, no warnings, 0.4 s |
| gate netlists of `cvt_fp32_int` (new, `netlist_int.sh`) | five Yosys flat netlists (INT8, UINT8, INT16, INT32, UINT32) reproduce all 214,008 vector rows, 22 s |

One side effect of F6: the first review's `rnd_ftz_le` mutant is no longer killed by `cvt_fp32_fp`, because no conversion uses
the FTZ path any more. It is still killed by `fp32_mul` and `bf16_dot2`.

## Part 1: the findings, one by one

| # | Status | Evidence |
|---|---|---|
| F1 | **fixed** | See below. |
| F2 | **fixed** | `MAXN` = the exact count (`tb_bf16_dot2.sv:6`, `tb_mxu_pe.sv:15`); 168,075 and 26,937 rows run. arith.md and status.md tables carry the real counts. |
| F3 | **fixed** | See below. |
| F4 | **fixed** | See below. |
| F5 | **fixed** | 4,096 corner rows, every lane ±448², ±448·1, ±2⁻⁹·2⁻⁹, ±448·2⁻⁹ in all sign patterns (`gen_arith_vectors.py:260-266`), plus the two hand rows (`arith_hand_cases.py` DOT8). `dot_width38` is killed; so are the new `dot2_sign37` and `dot2_amag37`. |
| F6 | **partly** | See below. |
| F7 | **fixed** | numerics.md:93-94 splits the row (FP16 → BF16 is RNE, cannot overflow); N-3 (:142-144) states the 3-bit loss. My six FP16 → BF16 cases (part 3, R1) agree. |
| F8 | **fixed** | See below. |
| F9 | **fixed** | See below. |
| F10 | **partly** | See below. |
| F11 | **fixed** | See below. |
| F12 | **partly** | See below. |
| F13 | **partly** | arith.md:106 says [−144, 127] with the per-format ranges; the count tables are right. Two stale points remain. |

**F1 (fixed).**
- The generator writes `counts.svh` (`gen_arith_vectors.py:76-81`), and every arith testbench sizes its memory from it and
  requires `n == MAXN`, for example `tb_fp32_add.sv:7,27`, `tb_mxu_pe.sv:15,103` and `tb_fold_quant.sv:56,89`.
- `make unit` fails on `Unable to open`, `Not enough words` and `Too many words` (`Makefile:72-74`); the old `grep -v` filter
  is gone.
- `make arith-vec` regenerates when an output is missing (`--check`, `Makefile:53-55`).
- Part 2, S2: a cut file, a long file and a removed file each fail.
- `run_arith_mutants.py` now reports a missing vector file as ERROR, not as a kill.
- Residual: see part 2.

**F3 (fixed).**
- `acc_update.sv:33-41`: NAN_IN only when `acc` or `part` is NaN and reaches the add unscaled. Scaled operands report through
  `f_rs` / `f_cs`.
- `arith_ref.py:201-203` and `indep.py` follow the same rule.
- The 8 hand ACC rows are in the vectors; `acc_nan_made` and `acc_nan_part` are killed.
- The `ADD_BF16` trap is now written into N-6 (numerics.md:260-263) and arith.md:157-158.
- New residuals: two vector gaps (`acc2_inf_as_nan`, `acc2_pinf_as_nan`) and the unpinned INVALID rule (part 3, A1).

**F4 (fixed).**
- `tb_mxu_pe.sv:49-102` holds INVALID, NAN_IN (BF16 and E4M3) and OVERFLOW inputs.
- It checks that the flags are non-zero before the reset, which guards against a vacuous case.
- It then asserts `rst_n` between clock edges and checks zero at once, while low and for two cycles after release.
- `pe_flags_norst`, `pe_fdot_norst`, `pe2_flags_sync` and `pe2_fdot_sync` are killed.
- New gaps `pe2_fdot_ovf_norst` and `pe2_mask_first`: see part 2.

**F6 (partly).**
- Fixed:
  - `cvt_fp32_fp.sv:22` (FTZ = 0 for every target).
  - `numerics.cvt_fp32_bf16` (`numerics.py:93`), which `test_arith.py:97` uses with FP32 subnormal inputs (:92).
  - N-2 (:89) and N-5 (:239) agree.
  - The seven new BF16 subnormal mutants are killed.
- Not fixed: N-1's table (numerics.md:45) still lists BF16 "Min subnormal: (flushed)". See C3.

**F8 (fixed).**
- N-2 step 1 (:102-104) states the rule.
- `numerics.fold_e4m3` ignores NaN in amax (`numerics.py:242`), and NaN elements stay NaN through `round_to`.
- `test_arith.py:253` and the following lines feed NaN-scale blocks and NaN elements; the skip is gone.
- My fold cases R6 (256-element tiles, a NaN-only tile, a zero element in a NaN-scale block) agree.

**F9 (fixed).**
- `cvt_fp32_int.sv:31` reads `if (rs > 24)`, so `frac_m[rs-1]` indexes at most bit 23.
- `int_rs25` (the old code) is equivalent; `int_rs24_off` (`rs > 23`) is killed.
- The gate netlists of all five types reproduce every vector row (table above).

**F10 (partly).**
- Fixed:
  - `REG_DOT=1, REG_OUT=0` is simulated (`p10`, `tb_mxu_pe.sv:24`).
  - 256-element tiles are tested, 556 per format, including one tile per leaf position (`gen_arith_vectors.py:433-447`).
    The new `fold2_n256_half` and `fold2_leaf255` are killed only by them.
  - `fold_quant` SATURATE is reachable through `foldq_*` (`foldq_sat_off` killed, 68,445 saturating rows).
- Open:
  - The short-tile rule (numerics.md:104, arith.md:105) says positions are "presented to the fold as zero elements", but
    not with which scale. A zero element with a NaN block scale is written `0x7F` with NAN_IN, by the same step's rule and
    by the RTL (part 3, R6 case 5).
  - `fold_amax` with N not a power of two is still never simulated (`fold2_pad_ones` survives).

**F11 (fixed).**
- `same()` compares signed zeros (`test_arith.py:31`) in the round, chain and MX tests.
- The `m >= 2^53` skip is gone (:143). FP32 subnormal inputs are fed (:92). The MX test uses `numerics.mx_dequant` /
  `nvfp4_dequant` (:235).
- The hand literals are tied to `arith_ref` (:282).
- Still true and acknowledged: flags, `acc_update`, `fp32_max` and the integer conversions have no counterpart in
  `numerics.py`. The hand literals and the oracle are their only independent check.

**F12 (partly).**
- Stated now:
  - round-then-flush (:26-29);
  - the `sat` definition (:82-85);
  - integer SATURATE (:92);
  - INVALID with a NaN input (:250);
  - MAX_F32 canonical NaN (:244);
  - BF16 Inf (:190-191);
  - the NVFP4 product (:107-109).
- But three edits did not reach the rest of the document: C1, C2, C5.

**F13 (partly).** Two stale points remain:
- status.md:968 and :1031 call the fixes "in the working tree, not committed"; they are commit `51f8cbe`.
- arith.md:111 gives "about 46k saturating rows". The run prints 10,710 + 47,151 + 10,584 = 68,445.

## Part 2: attacking the new test infrastructure

Scripts: `attack_infra.sh` (scenarios on scratch copies in `build/arith_review/attack/`, through `run_tb.sh`, which applies the
`make unit` checks) and `my_mutants2.py` (35 new mutants). `build/arith` itself was only read.

### Broken inputs

| Scenario | Testbench | Result |
|---|---|---|
| S1: file cut to 1,000 rows, `CNT_*` edited to 1,000 | `fp32_add`, `fp32_mul`, `fp32_max`, `bf16_dot2`, `fp8_dot4`, `acc_update` | **PASS** (attack works) |
| S1: `mxu_pe.hex` cut to 100 rows, count edited | `mxu_pe` (four variants, reset tests) | **PASS** |
| S1: `fold256_mxfp8.hex` cut to **1 tile**, count edited | `fold_quant` | **PASS** |
| S1: `cvt_fp32_e4m3.hex` cut to **1 row**, count edited | `cvt_fp32_fp` | **PASS** |
| S1: same, through `make unit BUILD=<scratch>` with a fresh stamp (8 files cut, counts edited) | all 23 testbenches | **`unit: ALL PASS`, rc 0**; the PASS lines print the small counts ("fp32_add (1000 vectors)", "E4M3 1"), and nothing fails on them |
| S1: `mxu_pe_dot.hex` cut to 1,000 rows | `mxu_pe_dot` | FAIL: its floor of 1,000 BF16 rows catches it (the first 1,000 rows are all FP8) |
| S1: `cvt_dec`, `fp8_mul` cut, count edited | `cvt_dec`, `fp8_mul` | FAIL: hard-coded exhaustive counts |
| S2: file cut, long by 5 rows, or removed; counts untouched | `fp32_add`, `cvt_fp32_fp` | FAIL (the F1 fix works) |
| S3: `CNT_FP32_ADD 0` and a 2-row file | `fp32_add` | **PASS with zero rows checked**: `mem [0:MAXN-1]` is `[0:-1]`, a two-word array, which a 2-row file fills without a warning, and the loop runs 0 times |
| S3: `CNT_FP32_ADD 0` and an empty file | `fp32_add` | FAIL (`Not enough words`) |
| S4: vectors of the `815acb1` generator and reference, counts from `wc -l` | `fp8_dot4` with `dot_width38` (38-bit sum) | **PASS**; FAIL on the current vectors |
| S4: same vectors | `acc_update` with the pre-F3 `acc_update.sv` | **PASS**; FAIL on the current vectors |
| S4: same vectors | `cvt_fp32_fp` with the pre-F6 `cvt_fp32_fp.sv` (BF16 flush) | **PASS**; the current RTL FAILs on them |

Conclusions:
- `counts.svh` is generated in the same run as the files it counts. It catches damage to a file after generation (S2), but
  not a consistent replacement (S1, S3, S4), and not a generator change that silently drops a group of rows. The count
  follows the change, so the corner sums of F5 could disappear again without any test noticing.
- Nothing ties `build/arith` to the sources once the stamp is newer than them. A `cp -R` of another checkout's `build/`, or
  a restored backup, is used as is. `run_arith_mutants.py` checks only that the files exist (`--check`), not that they are
  current (`run_arith_mutants.py:97`).
- Only the three testbenches with hard-coded exhaustive counts (`fp8_mul` 65,536; `cvt_dec`; `mx_dequant_bf16`) resist S1.
- Suggested fix:
  - Commit a manifest `scripts/arith_vectors.lock` with each file's row count and SHA-256, written by
    `gen_arith_vectors.py --lock`.
  - `make unit` compares `build/arith` with it before simulating and fails on any difference.
  - A generator change then shows up as a manifest diff in review, and a stale or edited `build/` cannot pass.
  - Also give each generator group a minimum count, or assert the presence of named rows such as the F5 corners, as
    `test_hand_cases_cover_the_review_findings` does for two of them.
  - Caveat: the stream of NumPy's `default_rng` is not guaranteed across NumPy versions; a version change would then
    (correctly) show up as a lock mismatch.
- S5, include precedence: `include "arith/counts.svh"` picks a copy in the compiler's current directory before the `-I`
  directory. `make unit` compiles from the repo root, so a stray `arith/counts.svh` there would silently replace `build`'s.
  Without it, `-I` wins over the testbench's own directory. Low risk; `include "counts.svh"` with `-I build/arith` would
  remove it.

### New mutants (`my_mutants2.py`)

| Mutant | Change | Result | Class |
|---|---|---|---|
| `acc2_and` | NAN_IN only if both `acc` and `part` are NaN | killed | |
| `acc2_cs_nan_unused`, `acc2_rs_nan_unused` | NAN_IN of an unused multiplier reported | killed | |
| `acc2_ow_nan` | OVERWRITE copy of a NaN `part` sets NAN_IN (the literal N-5 reading, A2) | killed | the vectors pin the RTL's choice |
| `acc2_inf_as_nan` | `acc_nan` true for ±Inf | **survives** | **vector gap**: needs `acc = Inf` with a NaN made by the column scale (`part = Inf, cs = 0`); one row of 20,008 has an Inf `acc` reaching the add |
| `acc2_pinf_as_nan` | `part_nan` true for ±Inf | **survives** | **vector gap**: `part = Inf` with `acc_r` = NaN made by the rowscale |
| `acc2_inv_supp` | INVALID dropped whenever NAN_IN is set (the "one unit" reading, A1) | **survives** | **unpinned spec choice**: no `acc_update` row has flags 9 |
| `acc2_nanadd_norsc`, `acc2_nanadd_nocs`, `acc2_nan_direct` | drop the `!rescale` / `!use_cs` gates; `nan_add` without `f_add[3]` | survive | equivalent: a scaled NaN is already in `f_rs` / `f_cs`; `nan_add` implies `f_add[3]` |
| `cvt2_sub_elsb`, `cvt2_sub_daz`, `cvt2_sub_hidden`, `cvt2_exp1_as_sub` | subnormal input scaled wrong, read as 0, given a hidden bit; exponent 1 decoded as subnormal | killed | |
| `cvt2_bf16_daz_in`, `cvt2_bf16_minnorm`, `cvt2_zero_sign` | BF16 flushes subnormal inputs; flushes the round-up to 2^-126; loses the sign of zero | killed | |
| `fold2_hi_eq_lo`, `fold2_top_half`, `fold2_signed_cmp`, `fold2_key_msb` | max tree ignores odd leaves / the top level / signs the keys / drops the key MSB | killed | |
| `fold2_n256_half`, `fold2_leaf255` | only for N = 256: lower half of the tile, leaf 255 ignored | killed (by `fold256_*` only) | |
| `fold2_nan_zero_el`, `fold2_q_c_unsigned`, `fold2_c_ge` | zero element of a NaN-scale block not NaN; `c` read unsigned; `>= 0xE0` | killed | |
| `fold2_pad_ones` | padding of a non-power-of-two N with ones | **survives** | **untested configuration** (N = 5 is only linted) |
| `fold2_amax_gt` | `>` instead of `>=` | survives | equivalent (equal keys) |
| `dot2_sign37`, `dot2_amag37` | sign or magnitude from bit 37 of the 39-bit sum | killed (by the F5 corner rows only) | |
| `pe2_flags_sync`, `pe2_fdot_sync` | synchronous instead of asynchronous reset | killed | |
| `pe2_fdot_ovf_norst` | reset leaves the dot's OVERFLOW bit of `f_dot_q` | **survives** | **vector gap**: no reset case has a dot that overflows (the OVERFLOW case overflows in the add) |
| `pe2_mask_first` | output flags forced to 0 in the first cycle after reset release | **survives** | **vector gap**: after each release the test drives benign inputs and only checks for zero |
| `pe2_flags_b2` | reset loads `f_new[2]` | survives | equivalent (`f_new[2]` is constant 0) |

24 killed, 5 equivalent, 6 survivors: four vector gaps, one untested configuration, one unpinned spec choice. 751 s, 0.20 GB.

## Part 3: the changed rules, re-derived from the text

Method:
- I read numerics.md N-2, N-3, N-5, N-6 and the principles as they are at `51f8cbe`, and worked 77 cases by hand in 12 rule
  groups, writing the expected bits before running anything.
- `spec_cases2.py` then compares each case with `arith_hand_cases.py`, with `arith_ref` (called as a black box) and with the
  oracle `indep.py`.
- Result: 70 agree everywhere, 7 are ambiguous (the text admits two answers), 0 disagree. None of the 77 contradicts a hand
  literal of `arith_hand_cases.py`; 36 of them have the same inputs there.

| Rule (text) | Cases | Same inputs among the hand cases | Result |
|---|---|---|---|
| R1 FP16 → BF16 RNE (N-2, N-3) | 6 | 4 | agree; 0x03FF → 0x3880 and 0x0001 → 0x3380 (FP16 subnormals) are not hand cases, but `cvt_dec` is exhaustive |
| R2 FP32 → BF16 keeps subnormals (N-2, N-5) | 6 | 2 | agree (0x00080000 → 0x0008, 0x80018000 → 0x8002, ...) |
| R3 round, then flush (principles) | 6 | 2 | agree, both signs |
| R4 `sat` and its ties (N-2) | 9 | 7 | agree. E4M3 464 → 448 without SATURATE, but E5M2 61440 → 57344 **with** SATURATE and E2M1 7 → 6 **with** it: the tie goes to the even neighbour, which is above max for E5M2 and E2M1, below for E4M3 |
| R5 FP32 → INT (N-2) | 8 | 8 | agree; NaN → 0: the text names no flag (A6) |
| R6 fold with NaN / short tile (N-2 step 1) | 6 | 0 | agree, except the padding scale (A3). No hand case uses a 256-element tile |
| R7 NVFP4 `2^c × T` as `fp32_mul` (N-2 step 4) | 6 | 0 | agree with `fp32_mul`. **No test exercises the composition** (no block computes it); see C5 |
| R8 INVALID suppressed by a NaN input (N-5) | 6 | 4 | agree for one operation; the ACC lane is ambiguous (A1) |
| R9 NAN_IN from external operands (N-5) | 7 | 3 | agree, except OVERWRITE of a NaN `part` (A2) |
| R10 `MAX_F32` (N-5) | 6 | 2 | agree; DAZ result bits (A4) |
| R11 `ADD_BF16` flags (N-6) | 6 | 0 | agree when composed as N-6 says. **No test exercises it** (`core/ni` does not exist); DAZ of the operands (A5) |
| R12 Inf in BF16 mode (N-3) | 5 | 4 | agree |

**Ambiguous rules** (what `arith_ref`, the RTL and the oracle do is in brackets; all three agree, and a test pins the choice
unless marked):
- **A1. INVALID with a NaN input in the ACC lane.**
  - The case: `acc = NaN`, `part = Inf`, `cs = 0`, accumulate.
  - N-5:250 suppresses INVALID "when the same operation also has a NaN input", while N-5:253 treats the lane as one unit for
    NAN_IN. Is the "operation" the multiply or the lane?
  - [INVALID | NAN_IN = 9.] The "one unit" reading gives NAN_IN only.
  - **No vector distinguishes them:** `acc2_inv_supp` survives, and no `acc_update` row has flags 9.
  - Decide, and add a hand case.
- **A2. OVERWRITE of a NaN `part` without a column scale.** It is "a NaN among its external operands" (N-5:253), but nothing
  consumes it. [No flag, `y` = the NaN's bits; pinned by the vectors: `acc2_ow_nan` is killed.] Harmless, since the PE column
  reported it already, but the text says otherwise.
- **A3. Short tile padding** "presented as zero elements" (N-2:104) does not say the scale. [A NaN scale makes the padding
  `0x7F` with NAN_IN.]
- **A4. `MAX_F32` with a DAZ'd subnormal.** Is the result +0 or the subnormal's bits? [+0; pinned by hand cases.]
- **A5. Write-add subnormals.** The principles say arithmetic is DAZ; the N-5 write-add row says only "FTZ".
  `ADD_F32(2^-149, 2^-126)` is `0x00800000` with DAZ and `0x00800001` without. [DAZ, through `fp32_add`.]
- **A6. FP32 → INT of NaN, and the DMA decodes of NaN.** N-2 names no flag, and N-5:253 says "arithmetic unit". arith.md and
  the RTL raise NAN_IN in every converter. [NAN_IN.]
- **A7. Which core's `FP_STATUS` a write-add at a remote SRAM target sets** (the ALU sits in the target's NI): not stated
  anywhere.

**Contradictions inside numerics.md and against the other specs:**
- **C1.** N-5's table, MXU row (numerics.md:240), says Inf comes "from FP32 overflow only". The new N-3 text (:190-191) says
  BF16 operands and a dot overflow produce it too. The row was not updated.
- **C2.** N-5 `SATURATE` (:252) says "a conversion to FP8/FP4 saturated a finite value". N-2 (:92) now sets it for an integer
  clamp too. The row was not updated.
- **C3.** N-1 (:45) lists BF16 "Min subnormal: (flushed)". N-2 and N-5 now keep BF16 subnormals on the 2^-133 grid.
- **C4.** N-2 (:89), FP32 → BF16, FP16 row: "VPU outputs are FTZ FP32 values, so for them the case does not arise". That is
  true for BF16 and false for FP16: a normal FP32 between 2^-24 and 2^-14 becomes an FP16 subnormal.
- **C5.** N-2 step 4 (:107-109), on the NVFP4 product `2^c × T`:
  - It says "exact unless it leaves the normal FP32 range (above: rounds or overflows)". A power-of-two multiple of an FP32
    never rounds: it is exact, Inf with OVERFLOW, or flushed.
  - It says "the DMA engine applies the tensor-scale multiply with an `fp32_mul` instance (isa.md §5)". isa.md §5 only says
    `R[ts_reg]` "multiplies `2^c`", and microarch.md §2.5's DMA has no FP32 multiplier.
  - microarch.md §2.2 instead says the ACC's `colscale` multiplier applies "the NVFP4 tensor scale". That is compatible
    (cs already holds `2^c·T`), but a `core/dma` author reading microarch would not know the multiply, its DAZ/FTZ, or its
    flags.
- **C6.** For MX folds with `c < −126`, `fold_c` writes a subnormal FP32 `cs`, which the ACC multiplier reads as 0 (DAZ, by
  the principles). The golden model keeps it (arith.md decision 8, `fold_c.sv` header). numerics.md says neither, so golden
  and RTL diverge for tiles with amax < 2^-118 and the tests avoid that range. This was a known open item, and the edited
  step 4 did not settle it.
- **C7.** `sat` for E4M3 uses "as if the exponent range were unbounded". For E4M3 that has to include 480 (the NaN code
  `S.1111.111`) as a value, or the tie at 464 is not a tie. The 464 example pins it for E4M3. A VQUANT implementer copying
  that example for E5M2 or E2M1 would get 61440 and 7 without the flag, opposite to R4. One sentence naming the E5M2 and
  E2M1 ties would close it.
- **C8.** N-2's block rule (:136) gives a block with an Inf a NaN scale. A NaN made from non-NaN inputs is INVALID by N-5:250,
  but the rule does not say whether VQUANT sets it. `cvt_fp32_fp` gives ±max for Inf without a flag, so the converter's flags
  cannot tell the quantizer about Inf (see I-8).

## Part 4: what the arith blocks assume about their integrators

Read from numerics.md, isa.md, microarch.md and arith.md only (`core/*` does not exist yet). "Stated in" names where the
integrator would find the assumption. microarch.md is the interface spec each `core/*` block will be written from.

| # | Assumption of the arith blocks | Stated in | Gap |
|---|---|---|---|
| I-1 | Flags are per operation, not sticky; the integrator ORs them into `FP_STATUS` | arith.md:10-11; N-5 (bit order; "set by any unit, including the DMA conversions") | microarch.md has no flag signal on any interface table (MXU, ACC, DMA, NI, VPU) and no path to the CSR |
| I-2 | Flags are **not valid-qualified**: `mxu_pe` (registered, aligned with `psum_out`), and every combinational block | arith.md:11, :53; `mxu_pe.sv:10-12` | microarch.md: none. Bubbles in the A skew, unused columns (N < 64), rows beyond M and ACC lanes of unused columns produce flags from whatever is on the wires |
| I-3 | Only `mxu_pe`'s flag registers reset; data registers do not. After release, flags derived from unreset `d_q` / `psum_out` must be masked until valid data has passed | arith.md:55 | not in microarch.md. Also, `tb_mxu_pe` drives benign inputs at release; real silicon will not |
| I-4 | Unused operand lanes are +0 in **both** `a` and `w`: lanes beyond K in the last K tile, stale stationary weights of unused columns, A rows beyond M. NaN × 0 is NAN_IN and Inf × 0 is INVALID (BF16), so zeroing one side is not enough | **nowhere** (isa.md: "`MXU_MM` ... for any M, K and N") | yes |
| I-5 | The top PE row's `psum_in` is +0 (not −0, not X) | N-3 order step 1 | microarch.md: no |
| I-6 | With `REG_DOT = 1`, `a`, `w` **and `mode_bf16`** lead `psum_in` by one cycle. Latency a/w → psum_out is `REG_DOT + REG_OUT`, psum_in → psum_out is `REG_OUT`. `REG_OUT = 0` makes a column one combinational chain | arith.md:49-52 (mentions only "the A word") | microarch.md §2.2: "skewed", no alignment rule; `mode` and the weight swap of the double buffer are not mentioned |
| I-7 | ACC lane control: `rescale` only on the first K tile of a `RESCALE` instruction, `overwrite` only on the first tile of `OVERWRITE`, never both (flags undefined: `acc_rs_ow_flags`); `rowscale` = rowscale[m] broadcast over the row, `cs` = cs[t][n] per column | N-3 order step 4; isa.md `acc_mode` | the "never both" and the flag consequence are only implied by the enum |
| I-8 | Fold (`core/dma`): one 256-element tile per (row, K tile), the block scale replicated to each element; padding positions of a short last tile as element 0 **with a non-NaN scale** (or key forced to 0 and their `v` and flags dropped); the 256 `fold_quant` flags ORed for in-range positions only | N-2 step 1 (element only); arith.md:105 ("key 0") | the scale (A3); microarch.md §2.5 describes the short tile (:326) but no padding |
| I-9 | NVFP4: `cs = fp32_mul(fold_c.cs, R[ts_reg])`, its flags ORed (OVERFLOW for a large T, NAN_IN for a NaN T); MX formats write `fold_c.cs` raw (subnormal for c < −126); NVFP4 → BF16 writes T raw | N-2 step 4 | isa.md §5 and microarch.md §2.5 do not say it (C5) |
| I-10 | `ADD_BF16` = `fp32_add({old,16'h0},{new,16'h0})`, then `cvt_fp32_fp #(0)`; flags = add's \| (converter's without NAN_IN). `ADD_F32` = `fp32_add`; `MAX_F32` = `fp32_max` (NaN → canonical, NAN_IN); operands DAZ | N-6:260-263; arith.md:157 | microarch.md §2.6 ("write-add/max"): no; whose `FP_STATUS` (A7) |

Also for `VQUANT` (not an arith block, but it uses `cvt_fp32_fp`):
- E2M1 reports a NaN input as code 0 with NAN_IN, but an Inf input gives ±6 **without** a flag (N-2 `sat`).
- The block rule "NaN or Inf → NaN scale" must therefore come from the block's own amax, not from the converter's flags
  (arith.md decision 5 suggests the flag).

### Proposed section for arith.md: "Integration contract"

> Rules every block that instantiates `rtl/arith` must follow. microarch.md §2.2 (MXU, ACC), §2.5 (DMA) and §2.6 (NI) should
> point here.
>
> 1. **Flags.** `flags` is per operation, not sticky and not valid-qualified. AND it with the valid of the data it belongs to,
>    carry it through your pipeline registers with that data, and OR it into the `FP_STATUS` of the core that executes the
>    instruction. A write-add or max at an SRAM target sets the `FP_STATUS` of *[decide: the target's / the initiator's]*
>    core.
> 2. **Reset.** Only `mxu_pe`'s flag registers reset. Mask flags after reset until valid data has propagated: `REG_DOT +
>    REG_OUT` cycles for one PE, `REG_DOT + 64·REG_OUT` plus the A skew for a column.
> 3. **Unused operands.** Lanes past K in the last K tile, columns past N and rows past M carry +0 in *both* `a` and `w` (or
>    their flags and results are masked). A stale stationary weight may be NaN. The top PE row's `psum_in` is +0.
> 4. **PE alignment.** With `REG_DOT = 1`, `a`, `w` and `mode_bf16` are presented one cycle before the `psum_in` they belong
>    to, and the stationary-weight swap follows `a`. The array uses `REG_OUT = 1`.
> 5. **ACC lane.** `rescale` only with the first K tile of `RESCALE`, `overwrite` only with the first K tile of `OVERWRITE`,
>    never both; `use_cs` when `cs_reg` is set; `rowscale` per row, `cs` per (K tile, column). A NaN `acc` is reported by the
>    lane (the ACC may have been loaded from memory). A NaN `part` copied under `OVERWRITE` without a column scale is not
>    reported again (the PE column did).
> 6. **Fold.** Present each 256-element K tile with the block scale replicated per element. Pad a short last tile with
>    element 0 and a finite, non-NaN scale (E8M0 127, E4M3 `0x38`), and drop the `v` and flags of the padding. OR the flags
>    of the in-range elements. Column scale:
>    - MX formats: write `fold_c.cs` as is. For c < −126 it is an FP32 subnormal, which the ACC reads as 0.
>    - NVFP4: write `fp32_mul(fold_c.cs, R[ts_reg])` and OR that multiply's flags.
>    - NVFP4 → BF16: write `R[ts_reg]` unchanged.
> 7. **Write-add.**
>    - `ADD_F32` is `fp32_add`.
>    - `ADD_BF16` is `fp32_add` on the BF16 operands shifted left by 16, then `cvt_fp32_fp #(FMT=0)`, with flags =
>      `f_add | (f_cvt & ~NAN_IN)`.
>    - `MAX_F32` is `fp32_max`.
> 8. **Conversions.** OR the flags of every converted element that is in range. `cvt = NONE` raises nothing. `VQUANT`
>    detects Inf in a block from its inputs: `cvt_fp32_fp` does not flag Inf.

## Recommendations

Before merge:
1. Close the stale text: C1, C2, C3, C4, the C5 wording and its isa.md reference, and status.md:968 / :1031 and arith.md:111.
2. Decide A1, add one hand case, and make `acc2_inv_supp` killed or equivalent by decision.
3. Add vectors that kill `acc2_inf_as_nan`, `acc2_pinf_as_nan` and `pe2_fdot_ovf_norst`. Examples:
   - ACC: `acc = Inf, part = Inf, cs = 0, use_cs`, which must give INVALID only;
   - ACC: `acc = Inf, rowscale = 0, RESCALE, part = Inf`;
   - PE: a reset case whose BF16 dot overflows (`7F7F × 7F7F`).
   - For `pe2_mask_first`: a flagging row in the first cycle after release.

Before `core/*` integration:

4. Add the integration contract to arith.md, and pointers to it in microarch.md §2.2, §2.5 and §2.6. Answer A3, A5, A6 and A7.
5. Bind `build/arith` to the generator with a committed lock file (part 2).

## Reviewer's scripts (new in this review)

In `scripts/arith_review/` (README there); outputs in `build/arith_review/`.
- `my_mutants2.py`: the 35 mutants of part 2. Same runner as `my_mutants.py`.
- `attack_infra.sh`: scenarios S1-S5. `--make` adds the full `make unit` run on S1.
- `spec_cases2.py`: the 77 cases of part 3.
- `netlist_int.sh`: Yosys gate netlists of `cvt_fp32_int` for the five types, run on every vector row.
