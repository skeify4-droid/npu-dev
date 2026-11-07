# Review: Phase 3 arithmetic blocks (`rtl/arith`)

Independent review of branch `p3-arith` at `815acb1` (commits `95d918d`, `5677746`, `815acb1`), 2026-11-07.
Scope: `rtl/arith/*.sv` (20 files), [arith.md](../spec/arith.md), the `tb_*` testbenches of those blocks,
`scripts/gen_arith_vectors.py`, `sw/npumodel/arith_ref.py`, `tb/unit/test_arith.py`, `scripts/arith_mutants/run_arith_mutants.py`,
against [numerics.md](../spec/numerics.md) N-1 to N-6 and N-8. Nothing in `rtl/`, `tb/`, `sw/` or the Makefile was changed; the reviewer's scripts are in `scripts/arith_review/`.

## Verdict

- **Arithmetic: no wrong result found.** An independent oracle, written from numerics.md with exact rational arithmetic and no code
  shared with `arith_ref.py`, agrees with every row of all 35 vector files (2,209,152 rows, values and flags). 265 hand-derived
  cases agree with the reference, with the oracle and with the RTL.
- **Correctness arguments: they hold.** I re-derived the `fp_addu` guard-bit bound (`SW + G >= 27`), the `SUBN_MAX` shortcut, the
  39-bit FP8 sum, the fold key range and "0xE1 is unreachable", and confirmed each with targeted vectors or exhaustive enumeration.
- **The regression does not yet guard the RTL as status.md says.**
  - `make unit` passes with vector files missing (F1). That is the one blocking finding.
  - Three testbenches silently read truncated files (F2).
  - The `mxu_pe` reset check is vacuous (F4).
  - No FP8 vector reaches the top bit of the 39-bit sum (F5).
  - Of my 44 mutants, 32 are killed and 8 are equivalent. The 4 survivors that are not equivalent are F4 (two), F5 and F10.
- **Flags and the spec.**
  - One flag rule contradicts N-5 and the PE's own rule (F3, `acc_update` NAN_IN).
  - The golden model `numerics.py` disagrees with the RTL in two places the tests never compare (F6 BF16 flush, F8 fold with NaN).
  - numerics.md needs edits for decisions 1, 2, 3 and 7.

Severity: **blocking** (fix before merge), **should fix**, **note**.

## Step 0: reproduction

All runs on this machine, one at a time; everything but `make lint` under `scripts/memrun` (logs `logs/review_*`,
`logs/arith_mutants*`, `logs/syn_mxu_pe*`). Vectors regenerated first (`rm build/arith/.stamp`).

| Check | Author's claim | Reproduced |
|---|---|---|
| `make lint` | clean | clean: 20 arith tops + 24 parameter variants (53 lint runs with `rtl/common`), rc 0 |
| `make unit` | ALL PASS | ALL PASS, 117 s, 118 MB. But see F1 and F2: `bf16_dot2`, `mxu_pe`, `mxu_pe_dot` print "Too many words" |
| `make pytest` | green | 261 passed, 80 s |
| `run_arith_mutants.py` | 36 as expected (32 killed, 4 equivalent) | 36 as expected, 237 s, 63 MB |
| `make syn BLOCK=mxu_pe` | - | 6,580 cells, 70 flops (64 data + 6 async-reset flag flops; flag bit 2 is constant 0), no warnings, 2.6 s |

## Findings

### F1 (blocking): `make unit` passes when vector files are missing or short

Evidence:
- `make unit` with `build/arith/cvt_fp32_fp16.hex` removed: rc 0, `unit: ALL PASS`, and
  `PASS cvt_fp32_fp (BF16 285869, FP16 0, E4M3 90791, E5M2 90773, E2M1 90077 vectors)`. Icarus printed
  `ERROR: ... $readmemh: Unable to open arith/cvt_fp32_fp16.hex`, but nothing fails on it. The stamp
  (`Makefile:52`) does not notice a deleted output, so `arith-vec` does not regenerate it.
- Same testbenches, scratch copy of the vectors:
  - `cvt_fp32_fp` passes with the FP16, E4M3 and E5M2 files all missing. `tb_cvt_fp32_fp.sv:42` checks only `n[0]` and `n[4]`.
  - `cvt_fp32_int` passes with the INT16, INT32 and UINT8 files missing. `tb_cvt_fp32_int.sv:42` does the same, and its PASS line
    still claims "42795 vectors per type".
  - `fp32_add`, `fp32_mul`, `fp32_max`, `fp8_dot4`, `bf16_dot2` and `acc_update` pass with their files cut to 1,000 rows (of
    20k-168k), because they check only `n >= 1000`.
- `Makefile:66` pipes the simulator output through `grep -v "Not enough words"`, which hides the one warning a short file produces.

Contradicts: status.md "Phase 3: arith" ("All bit-exact against the reference") and arith.md "Verification": the PASS line does not
mean the listed vectors ran. Only `fp8_mul`, `cvt_dec`, `mx_dequant_bf16` and `fold_quant` check exact counts.

Suggested fix:
- Have `gen_arith_vectors.py` write the row count of every file, for example a `build/arith/counts.svh` with one `define` per file,
  or a first header line. Every checker then requires `n == expected` for each instance.
- In the `unit` recipe, fail on `Unable to open`, `Not enough words` and `Too many words` instead of filtering them.
- Make the stamp depend on the file list: check that every expected `.hex` exists, or make the hex files the targets.

### F2 (should fix): three testbenches read only part of their vector file

Evidence (`wc -l` against `MAXN`):

| Testbench | File rows | Read | Rows never simulated |
|---|---|---|---|
| `tb_bf16_dot2` (`MAXN = 150000`, line 5) | 168,049 | 150,000 | 18,049 of the 30,000 "sums near 2^-126 and near the maximum" (the last generator group) |
| `tb_mxu_pe_dot`, `tb_mxu_pe` (`MAXN = 20000`) | 22,841 | 20,000 | 2,841 BF16 rows: all "near 2^-126 / maximum", all 750 "rounds up to 2^-126", 591 of "power of two minus a smaller product" |

- So no BF16 "rounds up to 2^-126" row ever goes through the PE wrapper.
- Icarus reports `Too many words in the file for the requested range` and the run passes.
- status.md reports "150,000" and "20,000" as the vector counts, which are the array sizes.
- arith.md's table gives "~123k" (`bf16_dot2`), "18k" (`mxu_pe`) and "~40k" (`fp32_max`; the file has 55,729).

I ran the full files on the RTL with copies of the three testbenches with `MAXN` raised: see "Verified".

Suggested fix: size `MAXN` from the generated counts (F1), and fail on "Too many words".

### F3 (should fix): `acc_update` sets NAN_IN for a NaN it made itself

Evidence (`acc_update.sv:31`; `arith_ref.py:187-200` composes the same way):

| acc | part | rowscale | cs | rescale, use_cs, overwrite | y | flags | N-5 expects |
|---|---|---|---|---|---|---|---|
| `3F800000` | `7F800000` | `3F800000` | `00000000` | 0, 1, 0 | `7FC00000` | `9` (INVALID, NAN_IN) | `1` (INVALID) |
| `7F800000` | `3F800000` | `00000000` | `3F800000` | 1, 0, 0 | `7FC00000` | `9` | `1` |

- The column-scale (or rowscale) multiply makes NaN from Inf × 0, which is INVALID.
- The add then "consumes" that NaN, and the OR of the three flag words adds NAN_IN.
- N-5 defines NAN_IN as "an arithmetic unit consumed a NaN" and INVALID as "produced NaN from non-NaN inputs". The ACC lane is one
  unit and consumed no NaN.

Contradicts: arith.md decision 5 (NAN_IN "for a NaN consumed by an arithmetic block") and `mxu_pe`, which handles exactly this case
by taking NAN_IN only from the first stage (`mxu_pe.sv:44-55`). My independent oracle composed the lane the same naive way, so this
is the kind of shared reading Step 1 asked about. The vectors cannot catch it.

Suggested fix:
- Compute NAN_IN from the lane's external operands only: `acc` (when it is used), `part`, `rowscale` (when `rescale`) and `cs`
  (when `use_cs`).
- Drop `f_add[3]` for an operand that came from a multiplier.
- Change `arith_ref.acc_update` the same way.
- The same trap applies to write-add `ADD_BF16`, which arith.md composes in `core/ni` as `fp32_add` then `cvt_fp32_fp`. Inf − Inf
  gives INVALID from the add and NAN_IN from the converter. Note it for `core/ni`.

### F4 (should fix): the `mxu_pe` reset check is vacuous

Evidence:
- `tb_mxu_pe.sv:33-36` holds `a = w = psum = 0` during reset. The dot of zeros has no flags, so the flag registers load 0 at every
  clock whether or not the reset works.
- My mutants `pe_flags_norst` (`if (!rst_n) flags <= f_new;`) and `pe_fdot_norst` (same for `f_dot_q`) both pass `tb_mxu_pe`.

Contradicts: arith.md "only the flag registers reset (asynchronous, low)" and the testbench header ("Also checks the registered
flags clear on reset"). The claim may well be true, but no test checks it.

Suggested fix: hold `a`/`w` at a value whose dot sets a flag during reset, for example BF16 `Inf * 0` (INVALID) or an E4M3 NaN
(NAN_IN), clock it, then check that the flags read 0 while `rst_n` is low and after release. Also check that a reset in the
middle of a run clears both flag stages.

### F5 (should fix): no FP8 vector reaches the top of the 39-bit adder

Evidence:
- My mutant `dot_width38` narrows the `fp8_dot4` sum to 38 bits (`logic signed [37:0] sum`, `sum[38]` → `sum[37]`). It overflows
  for |sum| ≥ 2^19 (three or more near-maximum products of one sign), and it passes `tb_fp8_dot4` on the author's vectors.
- Over every non-NaN row the largest exact sum is 402,528 in `fp8_dot4.hex` and 401,857 in `mxu_pe_dot.hex`, below 2^19 = 524,288.
  - The "large and small magnitude" group picks ±448 for about 10% of the operands.
  - Three equal-sign 448 × 448 products in one row then has a probability of about 1e-6.
- My hand cases kill the mutant:
  - `a = w = 7E7E7E7E` (4 × 448² = 802,816): expected `49440000`, mutant `C8700000`;
  - `817E7E7E · 017E7E7E` (3 × 448² − 2^-18): expected `49130000`, mutant `C8DA0000`.

Contradicts: N-8 "random and corner 4-way sums", and arith.md, which states the 39-bit width (36 bits of range, 2 carry bits and
the sign) but never tests it. The RTL is right: it passes both hand cases.

Suggested fix: add the corner sums explicitly to `fp8_dot_rows`: all four lanes ±448 × ±448 in each sign pattern, three maxima
minus the smallest product, and four maxima of mixed sign.

### F6 (should fix, spec): FP32 → BF16 below 2^-126: N-2 and N-5 disagree, and so do the golden model and the RTL

Evidence:
- N-2: "BF16 results below 2^-126 flush to ±0".
- N-5, DMA conversions: "BF16 kept".
- `numerics.bf16(x)` defaults to `ftz=False`: `bf16(2^-130)` = 7.35e-40, which is kept.
- `cvt_fp32_fp #(FMT=0)` and `arith_ref`: FP32 `0x00080000` (2^-130) → BF16 `0x0000`, flushed.
- `test_arith.py:87` reaches agreement only by passing `ftz=(fmt == "BF16")` to `round_to`. The golden model's own BF16 conversion
  (used by `bf16_split`) behaves the other way.

Assessment (arith.md decision 2):
- The author's reading is defensible: the N-2 row names `DMA FP32_BF16`.
- But the case only arises for FP32 subnormal inputs. FTZ arithmetic never produces one, and a normal FP32 cannot round below 2^-126.
- So it only affects raw FP32 data converted by the DMA, where N-5 and the "conversions keep subnormals" principle say "kept".
- The same DMA converter already rounds MX products onto the BF16 subnormal grid (`mx_dequant_bf16`, I-15).

Suggested fix: decide in numerics.md, preferably "kept", so one rule covers every DMA conversion. Make N-2 and N-5 say the same
thing. Add a named `numerics.cvt_fp32_bf16` that the test calls instead of a hand-set `ftz` argument.

### F7 (should fix, spec): FP16 → BF16 is not exact

The author is right (decision 1): N-2 lists FP16 → BF16 as "exact", but an FP16 significand has 11 bits and BF16 has 8.
Hand cases through `cvt_dec #(FMT=1, TO_BF16=1)` and the oracle agree:
- `3C01` → `3F80`;
- `3C04` (tie) → `3F80`;
- `3C0C` (tie, odd) → `3F82`;
- `7BFF` (65504) → `4780` (65536, no overflow).

Suggested fix:
- N-2: split the row. FP16 → FP32 is exact; FP16 → BF16 is `RNE`.
- N-3: say that FP16 tensors lose 3 significand bits on the way to the MXU (relative error ≤ 2^-9). Today N-3 says they "are
  converted to BF16 by the DMA" as if nothing were lost.

### F8 (should fix, spec + golden): fold of a tile that contains a NaN

Evidence: `numerics.fold_e4m3` on a tile `[NaN, 1000, 1, 0.001, 0 ...]` returns `cs = 1.0`. `np.max` gives NaN,
`fold_exponent` maps a non-positive amax to `c = 0`, and 1000 then saturates to 448. The RTL and `arith_ref` take `c` from the
non-NaN elements (MXFP8 tile with `0x7F`, `0x7E × 2^1`, `0x38`: `c = 1`) and write `0x7F` for the NaN.

Assessment (arith.md decision 7):
- The author's policy is the right one. A NaN still propagates through the MXU (N-3), and the other elements keep their accuracy.
- The golden model behaves differently by accident, and `test_arith.py:251` skips every tile with a NaN element.
- About 40% of the random MXFP8 tiles are skipped, so the difference is never seen.

Suggested fix:
- N-2 fold step 1: "NaN elements and the elements of a NaN-scale block do not take part in amax and are written as `0x7F`".
- `numerics.fold_e4m3`: use `nanmax` and write NaN for those elements.
- Remove the `continue` in `test_fold_matches_golden_fold_e4m3`.

### F9 (note): out-of-range bit-select in `cvt_fp32_int`

`cvt_fp32_int.sv:34` reads `frac_m[rs-1]` with `rs` up to 25, so `frac_m[24]` of a `[23:0]` vector for inputs in [0.25, 0.5)
(exponent field 125).
- Icarus returns X and takes the `if` as false. The result, 0, is correct.
- A Yosys netlist of the block matches the RTL on 44,000 inputs with exponent fields 120-130.
- The correct result still depends on each tool treating the out-of-range read as 0; another synthesis tool may treat it as a
  don't-care.

Suggested fix: `if (rs > 24)`, which my mutant `int_rs24` shows is equivalent (rs = 25 always rounds to 0), or widen `frac_m`
to 25 bits.

### F10 (note): untested configurations

- `mxu_pe` `REG_DOT = 1, REG_OUT = 0` is linted but never simulated. `tb_mxu_pe` runs 11, 01 and 00.
- `fold_amax` with `N = 256`, the spec's tile and the default, is never simulated: `tb_fold_quant` uses 64-element tiles.
- N-2's "last tile may be shorter" needs the integrator to zero the unused keys. Nothing states or tests that.
- `fold_quant`'s SATURATE output cannot fire when `c` comes from the tile's own amax. My mutant that ties it to 0 survives, so
  the wire is untested. Either test `fold_quant` alone with a smaller `c`, or remove the flag.

### F11 (note): gaps in `test_arith.py` (the link to the golden model)

- Signed zeros are never compared against the golden model or NumPy: `got == want` treats −0 and +0 as equal (lines 142, 171,
  185, 201, 231). Only the fold, decode and FP32 → narrow tests check the sign of zero (through `encode` / `np.signbit`).
- Silent skips:
  - `test_fold` `continue`s on every tile with a NaN element (line 251; about 40% of the MXFP8 tiles).
  - `test_rnd_matches_golden_round_to` skips `m >= 2^53` (line 139; about 12% of its cases).
- `test_cvt_fp32_fp_matches_golden_and_ml_dtypes` draws exponent fields ≥ 1 only (line 82), so no FP32 subnormal input is
  compared with the golden model (see F6).
- `test_fold` computes the exact weights with `R.mx_exact`, the reference's own decoder. This is acceptable only because
  `test_mx_dequant_matches_golden` ties `mx_exact` to `nm.decode` exhaustively. arith.md says the MX test is "against
  `numerics.mx_dequant`", but it uses `round_to` and its own E8M0 decode (lines 225-229).
- Flags are not tied to any golden: `numerics.py` has no flags. They rest on `arith_ref` alone. My independent oracle agrees with
  all of them on the 2.2 M rows (with the shared reading of F3).
- `acc_update`, `fp32_max` and the integer conversions have no golden counterpart in `numerics.py`. Only NumPy `rint` / `clip`
  covers the integer values.

### F12 (note, spec): rules the RTL had to choose that numerics.md should state

Each matches arith.md and I agree with each choice.
- **Round, then flush (decision 3).** It matches `numerics.round_to(ftz=True)`. Hand case: `fp32_mul(0x1FFFFFFF, 0x20000000)`
  = 2^-126 − 2^-150 → `0x00800000`, where flush-first hardware gives `0x00000000`. numerics.md never says which order; add one
  sentence to the principles or N-5.
- **SATURATE (decision 5).** Inf → E4M3 sets no flag, as N-5 says ("a finite value"). |x| in (448, 464] rounds to 448 without the
  flag, although N-2's `sat` definition ("|x| > max ... map to ±max") could be read as flagging it. State it. Also consider
  whether an Inf converted to FP8 should raise something: as specified it is silent.
- **SATURATE for an integer clamp.** It is not in N-5 (FP8/FP4 only) and not listed among arith.md's decisions.
- **INVALID with a NaN input elsewhere in the same dot.** `bf16_dot2([NaN, Inf], [1, 0])` gives NAN_IN only.
- **`MAX_F32` and NaN.** The NaN result is canonical; N-5 says "propagates", while N-1 says a copied NaN keeps its bits. Say which.
- **Inf in the PE.** N-3 "Inf can only come from the accumulator" holds for FP8 only. In BF16 mode an Inf operand or a dot overflow
  (`bf16_dot2([7F7F,0],[7F7F,0])` → Inf, OVERFLOW) also produce it.
- **`fp8_dot4` −0.** The rule (−0 only when all four products are −0) cannot be observed at the array output: the partial sum
  enters as +0 and +0 + (−0) = +0. Harmless; this is consistent with N-5's "+0 from an all-zero tile".
- **NVFP4 column scale.** N-2 step 4 says `cs = 2^c × T` is "an exact FP32 product". It is not exact if the product leaves the
  normal range, and no `rtl/arith` block computes it (`fold_c` gives `2^c`). Name the block that does.

### F13 (note): documentation

- arith.md decision 8: `c` is in [−144, 127], not 128. Enumerated over every code: MXFP4 [−136, 121], MXFP8 [−144, 127],
  NVFP4 [−18, 3].
- status.md "Phase 3: arith" says "Working tree on `main`, not committed". It is committed on `p3-arith`.
- The vector counts in arith.md's table (F2).

## Mutation results

**Author's set.** All 36 reproduce as expected. I confirmed that the four "survive" mutants are truly equivalent, not just
unkilled:
- `g_fp32_3`, `g_bf16_11`: the bound `SW + G >= 27` is sufficient (proof under "Verified").
- `add_subn_full`: see `SUBN_MAX` under "Verified".
- `foldc_hi`: 0xE1 never occurs (enumerated).

Two weaknesses in the harness itself (note):
- A crash or a missing vector file counts as "kill".
- The harness never checks that `build/arith` matches the current generator.

Run it after `make arith-vec` and, once F1 is fixed, through the count checks.

**Reviewer's set.**
- 44 mutants, one edit each (two use a replace-all), each run against the author's vectors with the testbenches listed.
- The driver is the reviewer's own (see "Reviewer's scripts" at the end); 345 s, 63 MB, under `scripts/memrun review_my_mutants`.
- "Equivalent" means the change cannot alter any output; the reason is given.

| Mutant | Change | Testbenches | Result |
|---|---|---|---|
| `rnd_sticky1` | `fp_round` sticky from one bit only | add, mul, cvt_fp32_fp | killed |
| `rnd_over_ge` | overflow at `code_w >= MAXC` | mul, cvt_fp32_fp | killed |
| `rnd_ftz_le` | flush `code_w <= 1 << MB` (min normal flushed) | mul, bf16_dot2, cvt_fp32_fp | killed |
| `rnd_subn1_st` | `SUBN_MAX = 1` path drops the old guard from the sticky | mul, bf16_dot2 | survives: **equivalent** (with FTZ only an all-ones kept field reaches 2^-126, and its LSB already decides the tie) |
| `swap_nomant` | `fp_addu` swap on exponent only | add, bf16_dot2 | killed |
| `swap_le` | swap on `a_m <= b_m` | add, bf16_dot2 | survives: **equivalent** (equal magnitudes: same sum, and a zero sum's sign does not depend on the order) |
| `zsign_or` | zero sign `a_s \| b_s` | add, bf16_dot2 | killed |
| `zsign_nz` | zero sign `a_s & b_s` (drop the zero flags) | add, bf16_dot2 | survives: **equivalent** (two non-zero operands of one sign cannot sum to 0) |
| `dot_zsign_any` | dot −0 if any product is negative | fp8_dot4 | killed |
| `dot_zsign_nz` | dot −0 rule without `!any_nz` | fp8_dot4 | survives: **equivalent** (all-negative non-zero products give a negative sum anyway) |
| `dot_width38` | 38-bit FP8 sum | fp8_dot4 | survives: **vector gap (F5)**; killed by the hand vectors |
| `dot_width37` | 37-bit FP8 sum | fp8_dot4 | killed |
| `cvt_sat_inf` | SATURATE also for Inf input | cvt_fp32_fp | killed |
| `cvt_ovf_inf` | OVERFLOW also for Inf input | cvt_fp32_fp | killed |
| `add_ovf_inf`, `mul_ovf_inf` | OVERFLOW not masked by Inf operands | add / mul | killed |
| `pe_dot_ovf`, `pe_dot_inv` | PE drops the dot's OVERFLOW / INVALID | mxu_pe | killed |
| `int_sat_inf` | integer SATURATE for Inf | cvt_fp32_int | killed |
| `bf16_inv_nanin` | INVALID even with a NaN input | bf16_dot2 | killed |
| `dq_ovf_off` | `mx_dequant_bf16` OVERFLOW tied to 0 | mx_dequant_bf16 | killed |
| `foldq_sat_off` | `fold_quant` SATURATE tied to 0 | fold_quant | survives: **unreachable in the testbench (F10)** |
| `pe_noalign` | add takes the unregistered dot (`REG_DOT = 1` misaligned) | mxu_pe | killed |
| `pe_flag_skew` | NAN_IN from the unregistered dot flags | mxu_pe | killed |
| `pe_flags_norst` | output flags not cleared by reset | mxu_pe | survives: **vacuous reset check (F4)** |
| `pe_fdot_norst` | dot flags not cleared by reset | mxu_pe | survives: **vacuous reset check (F4)** |
| `pe_mode_dot_q` | flags of both dot paths ORed regardless of mode | mxu_pe_dot, mxu_pe | killed |
| `acc_ow_part` | OVERWRITE writes `part` without the column scale | acc_update | killed |
| `acc_ow_flags` | add flags reported under OVERWRITE | acc_update | killed |
| `acc_rs_flags` | rowscale flags reported without RESCALE | acc_update | killed |
| `acc_rs_mux_ow` | `acc_r` takes the product also under OVERWRITE | acc_update | survives: **equivalent** (under OVERWRITE `acc_r` and the add flags are unused) |
| `acc_rs_ow_flags` | rowscale flags masked under OVERWRITE | acc_update | killed (the testbench exercises RESCALE + OVERWRITE, which the ISA cannot issue; harmless) |
| `int_smin`, `int_smax` | signed limit −127 / +128 | cvt_fp32_int | killed |
| `int_umax`, `int_uneg` | unsigned max − 1; negative clamps to 1 | cvt_fp32_int | killed |
| `int_rs24` | `rs > 24` instead of `rs > 25` | cvt_fp32_int | survives: **equivalent** (rs = 25 means a value < ½, always 0; this is the fix for F9) |
| `int_sh40` | saturate only from `sh > 44` (64-bit shift wraps) | cvt_fp32_int | killed |
| `bf16_daz` | BF16 subnormals not DAZ | bf16_dot2 | killed |
| `bf16_inv0` | only `x_inf & v_z` is INVALID | bf16_dot2 | killed |
| `max_gt` | `fp32_max` picks b on equal positive magnitude | fp32_max | survives: **equivalent** (same bits) |
| `nv_scale_sign` | NVFP4 scale sign ignored | mx_dequant_bf16, fold_quant | killed |
| `fold_nan_amax` | NaN elements take part in amax | fold_quant | killed |
| `addu_dws` | sticky-only path from `d > WS` | add, bf16_dot2 | survives: **equivalent** (at `d = WS` the shift path gives the same `sx = 1`) |

Guard-bit sweep with my targeted vectors (74,278 `fp32_add` and 140,000 `bf16_dot2` rows):

| G (`fp32_add`) | 4 | 3 | 2 |
|---|---|---|---|
| result | pass | pass | fail |

| G (`bf16_dot2`) | 12 | 11 | 10 |
|---|---|---|---|
| result | pass | pass | fail |

This matches the author's harness and the proof. G = 3 / 11 are equivalent, not merely unkilled.

## Verified and found correct

**Independent oracle (Step 1).**
- I wrote the oracle from the numerics.md text: exact `Fraction` values, its own decoders for every format, its own RNE with the
  subnormal grid, saturation and FTZ. It shares no code with `arith_ref.py` or `numerics.py`.
- It recomputed every row of every `build/arith/*.hex`. All 2,209,152 rows agree, values and flags, including the rows the
  testbenches never read (F2).
- Where numerics.md is silent the oracle needs a rule. Its choices match the author's: E2M1 NaN → 0 + NAN_IN; SATURATE on an
  integer clamp; INVALID suppressed when a NaN input is present; NaN outside the fold's amax; dot −0 only from four −0 products.

**Hand-derived cases.** I worked out 265 expected values by hand from numerics.md and wrote them as literals. Each was then
compared with `arith_ref`, with the oracle and, through the existing testbenches, with the RTL. Two of my own hand values were
wrong (2^-12 and 49 × 2^-18 written with the wrong exponent field) and were corrected. After that every case agrees.

| Family | Cases | Covers |
|---|---|---|
| `fp32_add` | 25 | RNE ties both ways, sticky below a tie at d = 25 both signs, d = 100, ±0 combinations, DAZ, exact −2^-149 → −0, results at 2^-127 flushed, max + ½ ulp → Inf, Inf − Inf, NaN payloads, Inf + max without OVERFLOW |
| `fp32_mul` | 21 | 2^-126 exact; 2^-127 flushed with sign; 2^-126 − 2^-150 → `0x00800000` (round then flush); 2^-126 − 2^-149 → 0; ties; Inf × 0 and Inf × subnormal → INVALID; overflow both signs; max × (1 − 2^-24) |
| `fp32_max` | 20 | `max(±0, ∓0)`, `max(−0, −0)`, NaN either side, ±Inf ordering, DAZ of either sign against normals and zeros |
| `fp8_dot4` | 20 | 4 × 448² = 802,816, 2^-18, 448² + 2^-7 tie and + 2^-18, cancellation to 1 and to ±2^-18, all −0 vs mixed, ±NaN, subnormal × subnormal |
| `bf16_dot2` | 26 | max² → Inf; max² − max² = +0 without OVERFLOW; 2^128 − 2^128 = 0; 2^128 + 1 → Inf; Inf × 0, Inf × subnormal, Inf − Inf; NaN + Inf × 0 → NAN_IN only; 2^-126 − 2^-151 / −2^-150 → 2^-126; 2^-126 − 2^-149 → 0; the sticky case 1 − 2^-25 − 2^-32 |
| `cvt_fp32_fp` | 55 | BF16 ties, subnormal round-up to 2^-126 vs flush, max + ½ ulp; FP16 65520 → Inf, 2^-25 tie, 2^-14 − 2^-25; E4M3 464 (tie → 448, no flag) vs 464⁺ (SATURATE), 450, ±Inf, 2^-10, 3 × 2^-10, 15 × 2^-10; E5M2 61440 (SATURATE), Inf; E2M1 every midpoint (0.25, 0.75, 1.25, 2.5, 3.5, 5), 7, NaN, ±Inf |
| `cvt_fp32_int` | 33 | INT8 127.5, −128.5 (tie → −128, no flag), −128.5⁻, 0.3 (the `rs = 25` path, F9), ±Inf; UINT8 −0.5 (no flag), −0.51 (flag), 255.5; INT16 / INT32 / UINT32 limits and ±1 ulp |
| `cvt_dec` | 23 | E4M3 NaN both signs, 448, 2^-9; E5M2 Inf / NaN / 2^-16; E2M1 6, −0; FP16 2^-24, FP16 → BF16 ties (F7); BF16 subnormal kept to FP32 and BF16 |
| `mx_dequant_bf16` | 27 | E8M0 254 × 6 → Inf, scale 0xFF → NaN, scale 0 (2^-127) × 0.5 → BF16 subnormal kept; MXFP8 2^-136 → 0, 2^-134 tie → 0, 5 × 2^-136 → 2^-133, 1.5 × 2^-133 tie → 2 × 2^-133; NVFP4 6 × 448, scale 0x7F / 0xFF NaN, scale 0 and −0 → signed zero, negative scale |
| fold | 15 tiles | amax = 448 · 2^c exactly and 480 just above; MXFP4 amax 6 → c = −6; 14-binade spread (0.5 → 2^-9 exact) vs 15-binade spread (0.5 → 0 and 1.5 → 2^-8: rounding); NVFP4 2688 → c = 3 and the 336 → 320 tie; all zero; NaN block; E8M0 254 and 0 in one tile; amax 2^-136 → c = −144, cs = `0x00000020` (FP32 subnormal); NaN and zero NVFP4 scales; −6 |

**Correctness arguments (Step 2).**
- **`fp_round`.**
  - RNE (`inc = g & (st | kept[0])`) and the carry into the exponent through `code_w = base << MB + kept + inc` are correct.
  - On the subnormal grid the hidden bit shifts into `kept` and a carry lands on the minimum normal code.
  - `sh >= K + 2` correctly keeps only a sticky bit, since the value is below a quarter of the minimum subnormal.
  - Overflow codes are right per KIND: IEEE gives `INFC`; E4M3 gives `0x7E` when saturating, else `0x7F`; E2M1 gives 7.
  - The 32-bit integer arithmetic is safe for every instance:
    - `base` is clamped to `1 << EB`;
    - `(1 << 31) - 1` for FP32 `ONES` wraps to the right pattern;
    - `elsb` ranges: `fp32_mul` [−298, 209], `bf16_dot2` [−279, 229], `cvt_fp32_fp` Inf 400, `fold_quant` [−273, 271], all inside `EW`.
- **`SUBN_MAX`.**
  - 1 is exact with FTZ: a value below 2^-127 rounds to at most 2^-127 and is flushed.
  - 0 is exact for `fp32_add`: a sum below 2^-126 needs both exponents within one of −126, so d ≤ 1 and no sticky bit. It is then
    a multiple of 2^-149 and is flushed.
  - 0 is exact for `fp8_dot4`, whose sums are ≥ 2^-18.
  - With FTZ the extra sticky bit of the `sh = 1` path is irrelevant: only a kept field of all ones can reach 2^-126, and its LSB is
    already 1. My mutant `rnd_subn1_st` is equivalent.
- **`fp_addu` guard-bit bound.** Proof:
  - For d ≥ 2 at most one bit cancels, so the result exceeds 2^(SW+G−2) and its 24-bit rounding position p is ≥ SW + G − 25.
  - The jammed bit 0 is harmless when p ≥ 2. The true and the jammed sums then lie in the same open interval (2n, 2n + 2), which
    contains no multiple of 2^(p−1) and no power of two.
  - d ≤ 1 is exact for G ≥ 1, and d ≥ WS gives `sx = 1` with a true shifted value in (0, ½).
  - So SW + G ≥ 27 suffices. G = 4 and G = 12 have one bit of margin, and G = 3 and G = 11 are equivalent, as the author found.

  Targeted vectors from my oracle: 74,278 `fp32_add` rows (one-bit cancellation, d = 1..60, near-tie mantissas, both orders and
  signs) and 140,000 `bf16_dot2` rows (power-of-two product minus a product 1..59 binades down). G = 4, 3, 12 and 11 pass; G = 2
  and G = 10 fail (e.g. `86800100 + 01319AC3`: expected `867FD599`, G = 2 gives `867FD59A`).
  - I could not build a failing input for G = 4 or G = 12.
  - Zero-flagged operands are never the big one unless both are zero.
  - Unnormalized operands cannot occur: `{1, frac}`, and `prod` in [2^14, 2^16) is normalized by `prod[15]`.
- **`fp8_dot4`.**
  - Each product is < 2^36 (448² · 2^18 = 5.26e10), so four fit in 38 magnitude bits and 39 bits with the sign. The negation of
    the most negative sum fits 38 bits.
  - The −0 rule is what an IEEE sum of four signed zeros gives.
- **`bf16_dot2`.**
  - DAZ covers both operands, and Inf × subnormal becomes INVALID.
  - Product normalization is `prod[15] ? (prod, e + 1) : (prod << 1, e)`.
  - The exponent `e` is in [−252, 256] with Inf/NaN fields, inside the 12-bit `EW`.
- **`cvt_fp32_int`.**
  - `frac_m << sh` with `sh ≤ 36` fits 60 bits, and `sh > 36` means a value ≥ 2^60, beyond every limit.
  - `rs > 25` means a value below ½.
  - The negative-unsigned case is right: −0.5 → 0 with no flag; −0.51 → 0 with SATURATE.
  - Only the `rs = 25` select remains questionable (F9).
- **`fold_key` and `fold_c`.**
  - Over every code, the key exponents are 120..391 (inside 9 bits and never 0), and c is in [−144, 127].
  - The only normalized significand above 0xE0 is 0xF0, in MXFP8 and NVFP4. 0xE1 never occurs, so `> 0xE0` (≡ `>= 0xE1`) and the
    author's `foldc_hi` mutant are equivalent.
  - `> 0xE0` is the exact test for 1.f > 1.75.
- **`mxu_pe`.**
  - Latency is a/w → psum_out = `REG_DOT + REG_OUT` and psum_in → psum_out = `REG_OUT`.
  - The one-cycle A lead is checked by the testbench: my mutant `pe_noalign`, the add taking the unregistered dot, is killed.
  - Flag alignment is also checked (`pe_flag_skew` killed).
  - NAN_IN comes from the dot only, OVERFLOW and INVALID from dot and add, as arith.md says.
  - Synthesis shows exactly the 64 data flops without reset and 6 reset flag flops.
- **`acc_update`.** All eight control combinations are correct against the oracle. RESCALE together with OVERWRITE cannot occur
  through the ISA (`acc_mode` is one enum, isa.yaml), so how that combination flags does not matter.

**Spec decisions (Step 3)**, arith.md "Decisions and gaps":

| # | Decision | Verdict |
|---|---|---|
| 1 | FP16 → BF16 rounds | right; spec change needed (F7) |
| 2 | FP32 → BF16 flush | defensible but contradicts N-5 and the golden default; spec decision needed (F6) |
| 3 | round, then flush | right (matches golden); state it in numerics.md (F12) |
| 4 | signed zeros | right; the dot −0 is unobservable at the array (F12) |
| 5 | flag rules | right as worded, except `acc_update` NAN_IN (F3); integer SATURATE and INVALID with a NaN input need a spec line (F12) |
| 6 | NVFP4 scale sign honoured | right, costs nothing |
| 7 | fold with NaN | right; spec and golden change needed (F8) |
| 8 | fold scale range | right except the bound: 127, not 128 (F13) |
| 9 | fold rounding by format | right; the SATURATE wire is untested (F10) |

**Testbench mechanics.**
- The multi-instance testbenches end with a fixed `#` delay (`#100000000`; `#3000000000` in `tb_cvt_fp32_fp`, below 2^32).
- That delay is longer than any checker's run, which advances `#1` per row (at most 300,000). So PASS is printed only after every
  checker has stopped, and there is no race.
- Their weakness is the count check (F1), not the timing.

**Full-length runs (F2).** Copies of `tb_bf16_dot2`, `tb_mxu_pe_dot` and `tb_mxu_pe` with `MAXN` raised to 200,000 / 30,000 pass
on the unmodified RTL:
- `bf16_dot2`: 168,049 vectors;
- `mxu_pe_dot`: 11,542 FP8 + 11,299 BF16;
- `mxu_pe`: 22,841 rows, three pipeline variants.

So the rows that `make unit` skips are correct. They are just not run by the regression.

**Synthesis.** `make syn BLOCK=mxu_pe` is clean. A Yosys netlist of `cvt_fp32_int` equals the RTL on 44,000 inputs (F9).

## Reviewer's scripts

In [`scripts/arith_review/`](../../scripts/arith_review/README.md); outputs go to `build/arith_review/`.
- `indep.py`: the independent oracle.
- `check_files.py`: recomputes every `build/arith/*.hex` row.
- `handpicked.py`: the 265 hand cases, which writes padded hex files for the testbenches.
- `gbound.py`: the guard-bit vectors.
- `my_mutants.py`: the 44 mutants above.
- `run_tb.sh`: runs any `tb_<block>` against a given RTL directory and vector directory.

The README lists the commands and which mutants should change status once the findings are fixed.
