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
     has a NaN input (`bf16_dot2([NaN, Inf], [1, 0])` gives `NAN_IN` only).
   - Conversion of a NaN to E2M1 (no NaN code) gives code 0 and `NAN_IN`, so the block quantizer can apply the "NaN scale,
     zero elements" rule of N-2.
   - `MAX_F32` returns the canonical NaN when an operand is NaN.
6. **NVFP4 scale sign.** The E4M3 scale of NVFP4 is positive in published checkpoints; `mx_exact` honours its sign bit anyway
   (no cost, and a negative scale is then still exact).
7. **Fold with NaN.** NaN elements and the elements of a NaN-scale block do not take part in `amax` and are written as E4M3 NaN
   (`0x7F`) with `NAN_IN`. The column scale of the tile is that of its other elements. The golden `numerics.fold_e4m3` does the
   same (it used `np.max` and returned `cs = 1` for a tile with a NaN). The positions of a short last tile must be presented
   to the fold as zero elements (key 0); the DMA integrator does that.
8. **Fold scale range.** `c` is in [-144, 127] (enumerated over every code: MXFP4 [-136, 121], MXFP8 [-144, 127], NVFP4 [-18,
   3]). The column scale `2^c` (`fold_c.cs`) is an exact FP32 down to c = -149 and 0 below. The ACC multiplier flushes a scale
   below 2^-126, which needs `amax < 2^-118`; real weights do not reach it, the golden model keeps such a scale, and the tests
   avoid that range. The NVFP4 tensor-scale product (`2^c x R[ts_reg]`) is an `fp32_mul` in the DMA engine, not part of `fold_c`.
9. **Fold rounding by format.** `fold_quant` saturation never fires when `c` comes from the tile's own `amax`, so the `SATURATE`
   flag is tested with `fold_quant` alone, with `c` below, at and above the element's own (`foldq_*.hex`, about 46k saturating
   rows). NVFP4 rounds (the N-2 residual) and the unit tests check exactly that rounding.

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
| `tb_mxu_pe_dot`, `tb_mxu_pe` | both modes through the mux (15,638 FP8 + 11,299 BF16 rows); four pipeline variants (`REG_DOT` x `REG_OUT`) driven one row per clock; flag reset: inputs that raise INVALID, NAN_IN and OVERFLOW held during reset and in the middle of a run (asynchronous assert, flags must read 0 at once, while low and after release) | 26,937 rows each |
| `tb_acc_update` | all control combinations, the hand cases (NAN_IN only from external operands) | 20,008 |
| `tb_cvt_fp32_fp` | five formats: random FP32, every rounding midpoint of the target +- one FP32 ulp, max + 1/2 ulp, subnormal / normal boundaries (BF16 subnormals kept), NaN, Inf, the hand cases | 90k-286k each |
| `tb_cvt_fp32_int` | five integer types: integers and +-0.25 / 0.5 / 0.75 around zero and the limits, one ulp either side, random, NaN, Inf | 43k each |
| `tb_cvt_dec` | every code of BF16, FP16, E4M3, E5M2, E2M1 to FP32 and BF16 | exhaustive |
| `tb_mx_dequant_bf16` | every (element, scale) pair of MXFP4, MXFP8, NVFP4 | exhaustive |
| `tb_fold_quant` | `fold_key` + `fold_amax` + `fold_c` + `fold_quant` on 64-element tiles (random, narrow, 14- and 15-binade spreads, extreme scale codes, NaN, all-zero, the hand tiles) and on 256-element tiles (the spec's tile and the default of `fold_amax`: random, plus one tile per position with a single non-zero element); `fold_quant` alone with `c` below, at and above the element's own | 1,503-1,508 tiles of 64, 556 tiles of 256 (per format); 24,576 / 96,000 / 24,576 single-element rows |

Every vector file has an exact row count. `scripts/gen_arith_vectors.py` writes `build/arith/counts.svh` (one `` `define CNT_<file> <rows> `` per file);
each testbench includes it, sizes its memory with it and requires exactly that many rows. `make unit` also fails on the
simulator's `Unable to open`, `Not enough words` and `Too many words`, and `make arith-vec` regenerates when an output file is
missing, so a missing, short or long file cannot pass. The hand-derived cases (`scripts/arith_hand_cases.py`, 279 of them: inputs
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
[status.md](../reports/status.md), "Phase 3: arith". The reviewer's 44 mutants are in `scripts/arith_review/my_mutants.py`.

## Open items
- Timing and area (Phase 6): the stage cuts, the BF16 multiplier cost against sharing (N-3), whether the 29-bit aligner can be
  narrower for `fp32_add` (a PE-array-wide cost).
- The SFU and the VPU FMA are separate blocks (`core/sfu`, `core/vpu`). `fp_round` and `fp_addu` are meant to be reused there
  (the FMA needs a 48-bit aligned add in front of `fp_round`).
- Write-add `ADD_BF16` is `fp32_add` on the BF16-extended operands followed by `cvt_fp32_fp #(FMT=0)`; `core/ni` composes it and
  takes `NAN_IN` from the add only (Inf - Inf is `INVALID` in the add; the NaN it made is not consumed again by the converter).
