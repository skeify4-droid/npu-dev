# Arithmetic blocks (`rtl/arith`)

Status: **Phase 3 RTL, built 2026-11-07.** Implements [numerics.md](numerics.md) N-1 to N-6 for the leaf arithmetic: the MXU PE, the
FP32 add / multiply / max, the format converters, the MX dequantizer and the DMA fold. Not in this directory: the SFU
(`core/sfu`), the VPU FMA (`core/vpu`), the `VQUANT` block-scale logic, and the PE array itself (`core/mxu`).

Common rules (all blocks):
- Combinational, packed ports, no clock except `mxu_pe`. Pipelining is a wrapper decision (Phase 6 timing); the blocks are written
  so a register can be cut at the stage boundaries named below.
- `flags[3:0]` follow the `FP_STATUS` bit order of numerics.md N-5: bit 0 `INVALID`, 1 `OVERFLOW`, 2 `SATURATE`, 3 `NAN_IN`.
  The flags are per operation and not sticky; the integrating block ORs them into the CSR under its own valid.
- Every rounding is RNE, once, on the exact value. DAZ on FP32 / BF16 inputs of arithmetic blocks, FTZ on their results.
  FTZ means: round on the subnormal grid first, then flush a result that is still subnormal; a result that rounds up to
  2^-126 is kept (the golden model's `round_to(..., ftz=True)`).
- Conversions (`cvt_fp32_fp`, `cvt_dec`, `mx_dequant_bf16`) keep subnormals: they round onto the destination's subnormal grid and
  do not flush (numerics.md N-2, N-5).
- NaN results are canonical (`0x7FC00000`, BF16 `0x7FC0`, FP16 `0x7E00`, E4M3 `0x7F`, E5M2 `0x7E`).

## Blocks

| Block | Function | Ports | Spec |
|---|---|---|---|
| `fp_round` | exact `mag * 2^elsb` to any binary format: RNE, subnormal grid, optional FTZ, saturate / Inf / NaN overflow | `sign`, `mag[W]`, `elsb` -> `code`, `ovf`, `sat` | N-2 |
| `fp8_mul` | exact E4M3 x E4M3 product, 36-bit fixed point (LSB 2^-18) | `a[8]`, `b[8]` -> `neg`, `nan`, `mag[36]` | N-3 |
| `fp8_dot4` | 4 exact E4M3 products, exact 39-bit sum, one rounding to FP32 | `a[32]`, `w[32]` -> `y[32]`, `flags` | N-3 FP8 PE |
| `bf16_dot2` | 2 exact BF16 products, exact sum, one rounding to FP32 | `a[32]`, `w[32]` -> `y`, `flags` | N-3 BF16 PE |
| `fp_addu` | aligner for an exact two-operand add (unpacked inputs, sticky bit) | unpacked a, b -> `s`, `mag`, `elsb` | internal |
| `fp32_add` | FP32 add, RNE, DAZ/FTZ | `a`, `b` -> `y`, `flags` | N-3, N-6 |
| `fp32_mul` | FP32 multiply, RNE, DAZ/FTZ | `a`, `b` -> `y`, `flags` | N-3 (rowscale, colscale) |
| `fp32_max` | FP32 max, NaN wins, DAZ, `max(-0,+0) = +0` | `a`, `b` -> `y`, `flags` | N-5, N-6 (`MAX_F32`) |
| `mxu_pe_dot` | mode mux: FP8 `fp8_dot4` or BF16 `bf16_dot2` | `mode_bf16`, `a[32]`, `w[32]` -> `d`, `flags` | N-3 |
| `mxu_pe` | PE arithmetic: `psum_out = fp32_add(psum_in, dot)` | `clk`, `rst_n`, `mode_bf16`, `a`, `w`, `psum_in` -> `psum_out`, `flags` | N-3 |
| `acc_update` | one ACC lane: RESCALE, column scale, OVERWRITE / accumulate | `acc`, `part`, `rowscale`, `cs`, `rescale`, `use_cs`, `overwrite` -> `y`, `flags` | N-3 step 4 |
| `cvt_fp32_fp` | FP32 -> BF16 / FP16 / E4M3 / E5M2 / E2M1 (parameter `FMT`) | `x[32]` -> `y[16]`, `flags` | N-2 |
| `cvt_fp32_int` | FP32 -> INT8 / UINT8 / INT16 / INT32 / UINT32 (`BITS`, `SIGNED`) | `x` -> `y[32]`, `flags` | N-2 |
| `cvt_dec` | exact decode BF16 / FP16 / E4M3 / E5M2 / E2M1 -> FP32 or BF16 (`FMT`, `TO_BF16`) | `x[16]` -> `y[32]`, `flags` | N-2 |
| `mx_exact` | exact element x block scale (E2M1 or E4M3 x E8M0 or E4M3) | `el`, `sc` -> `sign`, `nan`, `zero`, `sig[8]`, `elsb` | N-2 |
| `mx_dequant_bf16` | MXFP4 / MXFP8 / NVFP4 element x scale -> BF16 (`ELEM`, `SCALE`) | `el`, `sc` -> `y[16]`, `flags` | N-2 |
| `fold_key` | amax key of an element: `{e_msb + 256, normalized significand}` | `el`, `sc` -> `key[17]` | N-2 fold step 1 |
| `fold_amax` | max of N keys, balanced tree | `keys[N*17]` -> `amax[17]` | fold step 1 |
| `fold_c` | `c` = smallest integer with `amax <= 448 * 2^c`; column scale `2^c` as FP32 | `key` -> `c[10]`, `cs[32]` | fold steps 2, 4 |
| `fold_quant` | `RNE_E4M3(w * 2^-c)` with saturation | `el`, `sc`, `c` -> `v[8]`, `flags` | fold step 3 |

### The MXU PE (`mxu_pe`, `mxu_pe_dot`)
- Operand word: 32 bits per side. FP8 mode (`mode_bf16` = 0): four E4M3 codes, lane `i` at `[8i +: 8]`. BF16 mode: two BF16 codes,
  lane `i` at `[16i +: 16]`. The stationary register of a PE is therefore one 32-bit word in both modes.
- Two roundings per PE: the dot product to FP32, then the FP32 add of the partial sum. The BF16 dot has its own two multipliers
  (N-3: sharing the four FP8 ones would give quarter rate).
- `mxu_pe` parameters `REG_DOT`, `REG_OUT` (default 1, 1). Latency `a`/`w` -> `psum_out` is `REG_DOT + REG_OUT` cycles and
  `psum_in` -> `psum_out` is `REG_OUT`. With `REG_DOT = 1` the A word must be presented one cycle ahead of the partial sum it
  belongs to; the array's A skew absorbs this, and the dot of the next row then does not sit on the partial-sum path. The
  per-row critical path is one FP32 add.
- `flags` are aligned with `psum_out` and are not valid-qualified. `NAN_IN` is the dot's own (a NaN already in `psum_in` was
  reported by the PE above); `OVERFLOW` and `INVALID` are those of the dot and of the add.
- Data registers have no reset; only the flag registers reset (asynchronous, low).

### `fp_round`
Parameters `W`, `EB`, `MB`, `KIND` (0 IEEE-like, 1 FN = E4M3, 2 none = E2M1), `SAT`, `FTZ`, `EW`, `SUBN_MAX`.
- `SUBN_MAX` is the number of binades below the smallest normal that are rounded on the subnormal grid. The default is the
  full range. With `FTZ = 1` a smaller value is exact: a result more than `SUBN_MAX` binades down is below the smallest normal
  after rounding too. `fp32_mul`, `bf16_dot2` use 1 (only [2^-127, 2^-126) can round up to 2^-126, which the golden model
  keeps); `fp32_add` and `fp8_dot4` use 0 (their sums of normal FP32 values or of FP8 products are exact below 2^-126).
  This replaces the barrel shifter by one mux.
- Alignment field of `fp_addu`: guard bits `G` with `SW + G >= 27` (`SW` = significand width). `fp32_add` uses `SW = 24, G = 4`,
  `bf16_dot2` `SW = 16, G = 12`: one bit of margin. Below the bound (`G = 2`, `G = 10`), or without the sticky OR at the bound,
  the unit tests fail (mutation results below).

## Decisions and gaps in numerics.md

The first review of this directory found nine places where numerics.md was silent or inconsistent. The independent review
(`docs/reports/arith_review.md`, F6-F8, F12, F13) confirmed the choices below, and numerics.md N-2, N-3, N-5 and N-6 now state
them. Each is implemented and tested against the golden model; none changes a frozen number.

1. **FP16 -> BF16 is not exact.** An FP16 significand has 11 bits, BF16 holds 8. `cvt_dec` rounds FP16 -> BF16 with RNE; the
   8-bit and 4-bit formats and FP16 -> FP32 are exact. N-2 splits the row; N-3 says an FP16 tensor loses 3 significand bits on
   the way to the MXU (relative error <= 2^-9).
2. **FP32 -> BF16 keeps subnormals** (review F6, decision of the integrator). N-2 and N-5 now agree: every conversion rounds onto
   the destination's subnormal grid, and an FP32 subnormal input of a conversion is converted (BF16 grid 2^-133). The first
   version of `cvt_fp32_fp` flushed BF16 results below 2^-126 (N-2 as it then stood). `numerics.cvt_fp32_bf16` is the named
   golden conversion. It only matters for raw FP32 data through the DMA: FTZ arithmetic never produces a subnormal.
3. **FTZ is "round, then flush"**, as the golden model and as described above (stated in the numerics.md principles). A value just
   below 2^-126 can therefore come out as 2^-126 rather than 0. Hardware that flushes before rounding would differ on those
   values only.
4. **Signed zeros.** (-0) + (-0) = -0; an exact cancellation gives +0 (RNE); a dot product is -0 only if all its products are
   zero and negative (not observable at the array output: the partial sum enters as +0); a flushed or underflowed result keeps
   its sign; `max(-0, +0) = +0`.
5. **Flags** (N-5 states all of them):
   - `SATURATE` is set only for a finite input that would have rounded above the maximum; |x| in (max, max + 1/2 ulp) rounds to
     max without the flag, and an Inf input gives the maximum without the flag. An integer clamp of a finite input sets it too.
   - `OVERFLOW` only for a finite value that rounded to Inf.
   - `NAN_IN` for a NaN among the external operands of an arithmetic unit or converter. A NaN that a unit made itself is
     `INVALID` and is not consumed again: in `acc_update` an Inf x 0 in the rowscale or column scale gives `INVALID` only (review
     F3, fixed); `mxu_pe` takes `NAN_IN` from its dot product only. `core/ni` composes write-add `ADD_BF16` (`fp32_add`, then
     `cvt_fp32_fp #(FMT=0)`) and must take `NAN_IN` from the add only, for the same reason.
   - `INVALID` only when a NaN is made from non-NaN inputs (Inf - Inf, Inf x 0); it is suppressed when the same operation also
     has a NaN input (`bf16_dot2([NaN, Inf], [1, 0])` gives `NAN_IN` only). An operation is one multiply or one add, not the ACC
     lane: `acc_update` with a NaN `acc`, an Inf `part` and `cs = 0` gives `INVALID | NAN_IN` (flags 9; decision A1 of the
     second review).
   - An `OVERWRITE` copy of a NaN `part` without a column scale consumes nothing and sets no `NAN_IN`, and `y` carries the NaN's
     bits (A2). Every converter, including `cvt_fp32_int` (NaN -> 0), raises `NAN_IN` for a NaN input (A6). `fp32_max` returns
     the zero of a DAZ'd subnormal operand, not its bits (A4); write-add operands are DAZ (A5).
   - Conversion of a NaN to E2M1 (no NaN code) gives code 0 and `NAN_IN`, so the block quantizer can apply the "NaN scale,
     zero elements" rule of N-2.
   - `MAX_F32` returns the canonical NaN when an operand is NaN.
6. **NVFP4 scale sign.** The E4M3 scale of NVFP4 is positive in published checkpoints; `mx_exact` honours its sign bit anyway
   (no cost, and a negative scale is then still exact).
7. **Fold with NaN.** NaN elements and the elements of a NaN-scale block do not take part in `amax` and are written as E4M3 NaN
   (`0x7F`) with `NAN_IN`. The column scale of the tile is that of its other elements. The golden `numerics.fold_e4m3` does the
   same (it used `np.max` and returned `cs = 1` for a tile with a NaN). The positions of a short last tile are presented
   to the fold as element 0 with a finite scale (E8M0 127, E4M3 `0x38`), and the integrator drops their `v` and flags (A3; a
   zero element with a NaN scale is written NaN, so the padding's scale must not be NaN). The Integration contract below states it.
8. **Fold scale range.** `c` is in [-144, 127] (enumerated over every code: MXFP4 [-136, 121], MXFP8 [-144, 127], NVFP4 [-18,
   3]). The column scale `2^c` (`fold_c.cs`) is an exact FP32 down to c = -149 and 0 below. The ACC multiplier flushes a scale
   below 2^-126, which needs `amax < 2^-118`; real weights do not reach it, the golden model keeps such a scale (golden and RTL
   differ there, numerics.md N-2 step 4), and the tests avoid that range. The NVFP4 tensor-scale product (`2^c x R[ts_reg]`) is an `fp32_mul` in the DMA engine, not part of `fold_c`.
9. **Fold rounding by format.** `fold_quant` saturation never fires when `c` comes from the tile's own `amax`, so the `SATURATE`
   flag is tested with `fold_quant` alone, with `c` below, at and above the element's own (`foldq_*.hex`, 68,445 saturating
   rows). NVFP4 rounds (the N-2 residual) and the unit tests check exactly that rounding.

## Integration contract

Rules every block that instantiates `rtl/arith` must follow (`core/mxu`, `core/acc`, `core/dma`, `core/ni`). The arith blocks
assume them; nothing in the RTL checks them. microarch.md §2.2 (MXU, ACC), §2.5 (DMA) and §2.6 (NI) point here. The numerics
are in numerics.md N-2, N-3, N-5 and N-6.

1. **Flags.** `flags` is per operation, not sticky and not valid-qualified. AND it with the valid of the data it belongs to,
   carry it through your pipeline registers with that data, and OR it into the `FP_STATUS` of the core that executes the
   instruction. A write-add or max at an SRAM target sets the `FP_STATUS` of the target's core (the ALU is at the target:
   the core's network interface for SPM, the GSRAM slice for GSRAM), not of the initiator (decision A7). The `fp_flags` ports, their valid qualification and their
   ordering against completion semaphores and acks are in microarch.md §5 ("Floating-point flags").
2. **Reset.** Only `mxu_pe`'s flag registers reset. Mask flags after reset until valid data has propagated: `REG_DOT + REG_OUT`
   cycles for one PE, `REG_DOT + 64 * REG_OUT` plus the A skew for a column. (`tb_mxu_pe` drives benign inputs at release and
   also a flagging row in the first cycle; real inputs are not benign.)
3. **Unused operands.** Lanes past K in the last K tile, columns past N and rows past M carry +0 in *both* `a` and `w` (or
   their flags and results are masked): NaN x 0 is `NAN_IN` and Inf x 0 is `INVALID` (BF16), so zeroing one side is not
   enough. A stale stationary weight may be NaN. The top PE row's `psum_in` is +0 (not -0, not X).
4. **PE alignment.** With `REG_DOT = 1`, `a`, `w` and `mode_bf16` are presented one cycle before the `psum_in` they belong
   to, and the stationary-weight swap follows `a`. The array uses `REG_OUT = 1`; `REG_OUT = 0` makes a column one
   combinational chain.
5. **ACC lane.** `rescale` only with the first K tile of `RESCALE`, `overwrite` only with the first K tile of `OVERWRITE`,
   never both (the flags are then undefined); `use_cs` when `cs_reg` is set; `rowscale` per row (`rowscale[m]` broadcast
   over the row), `cs` per (K tile, column). A NaN `acc` is reported by the lane (the ACC may have been loaded from memory).
   A NaN `part` copied under `OVERWRITE` without a column scale is not reported again (the PE column did). An operation is
   one multiply or one add: an Inf x 0 in the lane's multiplier keeps its `INVALID` when the add also has a NaN input
   (`INVALID | NAN_IN`).
6. **Fold.** Present each 256-element K tile (one per row and K tile) with the block scale replicated per element. Pad a
   short last tile with element 0 and a finite, non-NaN scale (E8M0 127, E4M3 `0x38`), and drop the `v` and flags of the
   padding. OR the flags of the in-range elements only. Column scale:
   - MX formats: write `fold_c.cs` as is. For c < -126 it is an FP32 subnormal, which the ACC reads as 0 (the golden model
     keeps it; numerics.md N-2 step 4).
   - NVFP4: write `fp32_mul(fold_c.cs, R[ts_reg])` and OR that multiply's flags (OVERFLOW for a large T, `NAN_IN` for a NaN T).
     The product is exact, Inf with OVERFLOW, or flushed to 0, except the single value 2^-126 - 2^-150 (a T with an all-ones
     significand), which rounds up to 2^-126 (numerics.md N-2 step 4).
   - NVFP4 -> BF16: write `R[ts_reg]` unchanged as the column scale.
7. **Write-add.** All operands are DAZ and results FTZ.
   - `ADD_F32` is `fp32_add`.
   - `ADD_BF16` is `fp32_add` on the BF16 operands shifted left by 16, then `cvt_fp32_fp #(FMT=0)`, with flags =
     `f_add | (f_cvt & ~NAN_IN)`.
   - `MAX_F32` is `fp32_max` (a NaN operand gives the canonical NaN with `NAN_IN`).
8. **Conversions.** OR the flags of every converted element that is in range. `cvt = NONE` raises nothing. `VQUANT` detects an
   Inf or NaN in a block from the block's own inputs: `cvt_fp32_fp` gives an Inf input ±max without a flag, and an E2M1 NaN
   input code 0 with `NAN_IN`.

## Verification

`make lint` (Verilator `-Wall`, every module as its own top), `make unit` (Icarus; vectors from `make arith-vec`), `make pytest`
(`tb/unit/test_arith.py`).

| Test | What it covers | Vectors |
|---|---|---|
| `tb_fp8_mul` | all 65,536 E4M3 x E4M3 products, expected from `ml_dtypes` | 65,536 |
| `tb_fp8_dot4` | every E4M3 code against 7 partners in all lanes, random and large / small magnitude 4-way sums, cancelling pairs, all-zero products with mixed signs, corner 4-way sums (every lane +-448 x 448, +-448 x 1, +-2^-9 x 2^-9 or +-448 x 2^-9 in every sign pattern: 4,096 rows up to 4 x 448^2 = 802,816, which reach the top of the 39-bit adder), the hand cases | 200,908 |
| `tb_bf16_dot2` | specials cross products (zero, +-1, +-Inf, NaN in all four operands), random flavours, product exponents 0-40 apart, cancellation, power-of-two product minus a much smaller one (sticky and guard), sums that round up to 2^-126, sums near 2^-126 and the maximum, the hand cases | 168,075 |
| `tb_fp32_add`, `tb_fp32_mul` | specials cross product, random flavours (subnormal, near 2^-126, near the maximum), alignment shifts 0-40, cancellation, power of two minus a smaller value, exponent sums at under- and overflow, all-ones mantissas, the hand cases | 138,254, 125,750 |
| `tb_fp32_max` | the add vectors, the hand cases | 55,749 |
| `tb_mxu_pe_dot`, `tb_mxu_pe` | both modes through the mux (15,638 FP8 + 11,299 BF16 rows); four pipeline variants (`REG_DOT` x `REG_OUT`) driven one row per clock; flag reset: inputs that raise INVALID, NAN_IN, OVERFLOW (in the add) and OVERFLOW (in the dot only) held during reset and in the middle of a run (asynchronous assert, flags must read 0 at once, while low and after release), and a flagging row on the inputs at release (its flags must appear in the first cycle) | 26,937 rows each |
| `tb_acc_update` | all control combinations, the hand cases (NAN_IN only from external operands; Inf is not a NaN; INVALID and NAN_IN together; an OVERWRITE copy of a NaN) | 20,012 |
| `tb_cvt_fp32_fp` | five formats: random FP32, every rounding midpoint of the target +- one FP32 ulp, max + 1/2 ulp, subnormal / normal boundaries (BF16 subnormals kept), NaN, Inf, the hand cases | 90k-286k each |
| `tb_cvt_fp32_int` | five integer types: integers and +-0.25 / 0.5 / 0.75 around zero and the limits, one ulp either side, random, NaN, Inf | 43k each |
| `tb_cvt_dec` | every code of BF16, FP16, E4M3, E5M2, E2M1 to FP32 and BF16 | exhaustive |
| `tb_mx_dequant_bf16` | every (element, scale) pair of MXFP4, MXFP8, NVFP4 | exhaustive |
| `tb_fold_quant` | `fold_key` + `fold_amax` + `fold_c` + `fold_quant` on 64-element tiles (random, narrow, 14- and 15-binade spreads, extreme scale codes, NaN, all-zero, the hand tiles) and on 256-element tiles (the spec's tile and the default of `fold_amax`: random, plus one tile per position with a single non-zero element); `fold_quant` alone with `c` below, at and above the element's own; `fold_amax` alone with N = 3, 5, 48, 100, 255 (not powers of two: all-zero tile, one key at every position, 400 random tiles each, against a behavioural maximum) | 1,503-1,508 tiles of 64, 556 tiles of 256 (per format); 24,576 / 96,000 / 24,576 single-element rows; `fold_amax`: 1 + N + 400 checks per N |

Every vector file has an exact row count. `scripts/gen_arith_vectors.py` writes `build/arith/counts.svh` (one `` `define CNT_<file> <rows> `` per file);
each testbench includes it (`include "counts.svh"`, compiled inside `build/arith`), sizes its memory with it and requires exactly that many rows, never zero.
`scripts/arith_vectors.lock` (committed; written by `scripts/gen_arith_vectors.py --lock`) holds the row count and SHA-256 of every
file of `build/arith`, `counts.svh` included. `make arith-vec` (so `make unit`) and `run_arith_mutants.py` verify the directory against it
(`scripts/arith_lock.py verify`) and stop on any difference, so a truncated or edited file with a matching `counts.svh`, a zero count, an
old but self-consistent vector set or a stale copy of `build/` cannot pass, and a change of the generator, the reference or the hand
cases shows up as a diff of the lock. NumPy's `default_rng` stream is not guaranteed across versions: after an upgrade the lock
mismatches (correctly); re-check the vectors, then `--lock`. The compile runs inside `build/arith` (Icarus looks in the compile
directory before any `-I`), so a stray `counts.svh` elsewhere, for example in the repo root, cannot shadow the locked one. `make unit` also fails on the
simulator's `Unable to open`, `Not enough words` and `Too many words`, and `make arith-vec` regenerates when an output file is
missing, so a missing, short or long file cannot pass. The hand-derived cases (`scripts/arith_hand_cases.py`, 283 of them: inputs
and expected bits worked out by hand) are in the vectors with the expected values of `arith_ref`; `test_arith.py` asserts that
the hand literals equal `arith_ref`.

The testbenches compare with `sw/npumodel/arith_ref.py`, an exact integer reference (values kept as sign, integer, exponent;
no float64 double rounding). `test_arith.py` ties it to the golden model: rounding against `numerics.round_to`, decode and
conversion against `ml_dtypes` (all codes), the FP8 products against `ml_dtypes` (all pairs), PE chains against
`numerics.mxu_mm` (FP8 and BF16), FP32 add / multiply against NumPy float32 wherever the result is normal, MX dequantization
against `numerics.mx_dequant` / `numerics.nvfp4_dequant`, the FP32 -> BF16 conversion against `numerics.cvt_fp32_bf16` (FP32
subnormal inputs included), the fold against `numerics.fold_e4m3` (tiles with NaN elements and NaN scales included; MXFP4 with a
spread of at most 14 binades unfolds exactly). Signed zeros are compared as such.

Mutation check (`scripts/arith_mutants/run_arith_mutants.py`, 39 one-change copies of the RTL; the unit test must fail unless the change is equivalent): see
[status.md](../reports/status.md), "Phase 3: arith". The reviewers' mutants are in `scripts/arith_review/`: `my_mutants.py` (44: 36 killed, 8 equivalent)
and `my_mutants2.py` (35: 30 killed, 5 equivalent; the six that survived at `51f8cbe`, four vector gaps, one untested configuration and one
unpinned spec choice, are killed by the vectors added for arith_review2.md). The three sets add up to 118 mutants: 101 killed, 17 equivalent.

## Open items
- Timing and area (Phase 6): the stage cuts, the BF16 multiplier cost against sharing (N-3), whether the 29-bit aligner can be
  narrower for `fp32_add` (a PE-array-wide cost).
- The SFU and the VPU FMA are separate blocks (`core/sfu`, `core/vpu`). `fp_round` and `fp_addu` are meant to be reused there
  (the FMA needs a 48-bit aligned add in front of `fp_round`).
- Write-add `ADD_BF16` is `fp32_add` on the BF16-extended operands followed by `cvt_fp32_fp #(FMT=0)`; `core/ni` composes it and
  takes `NAN_IN` from the add only (Inf - Inf is `INVALID` in the add; the NaN it made is not consumed again by the converter).
