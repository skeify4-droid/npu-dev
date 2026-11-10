# Numerics

Status: **reviewed 2026-11-01** (Phase 2 self-review, [spec_review.md](../reports/spec_review.md)).

This document defines the bits of every data format, every conversion and the arithmetic of each unit:
- formats and conversions (N-1, N-2);
- MXU arithmetic and accumulation order (N-3);
- VPU and SFU arithmetic (N-4);
- special values (N-5);
- write-add and collectives (N-6).

It also records the Phase 2 numerics experiments and the decisions taken from them (N-7), and how RTL is checked (N-8).

Related files:
- **Golden model:** [`sw/npumodel/numerics.py`](../../sw/npumodel/numerics.py) (bit-accurate). It implements this document; the
  tests in `tb/unit/test_numerics.py` check it against `ml_dtypes` and against the rules below.
- **Experiments:** `make numerics` regenerates [`docs/reports/numerics_experiments.md`](../reports/numerics_experiments.md).
- **ISA side:** `cvt`, `quant_fmt`, `mxu_mode` and the `FP_STATUS` CSR are in `isa/isa.yaml`.

Principles:
- Every rounding is round-to-nearest, ties-to-even (RNE). There are no other rounding modes.
- Conversions into FP8 and FP4 saturate (PTX `.satfinite` semantics). Conversions into BF16, FP16 and FP32 are IEEE: overflow gives ±Inf.
- Arithmetic is FP32 inside every unit. FP8 and FP4 subnormals are honoured. FP32 and BF16 subnormals are flushed to zero by
  arithmetic (DAZ on inputs, FTZ on results); copies and conversions keep them (a rounding conversion rounds onto the
  destination's subnormal grid, and an FP32 subnormal input of a conversion is converted, not read as zero).
- FTZ is "round, then flush": the exact result is rounded onto the subnormal grid first, and a result that is still subnormal
  becomes a zero of the same sign. A value just below 2^-126 can therefore come out as 2^-126 instead of 0 (for example
  `fp32_mul(2^-63 · (1 − 2^-24), 2^-63)`, the golden model's `round_to(..., ftz=True)`); hardware that flushes before rounding
  would differ on those values only.
- Floating-point exceptions never trap. Sticky flags in `FP_STATUS` record them (N-5).
- Results are a function of the operands and the instruction's tiling only, never of timing.
  - Single exception: write-add with three or more contributors (N-6).
  - Consequence: the RTL is bit-exact against the golden model wherever N-8 says so.

## N-1. Formats

**Scalar formats:**

| Format | Sign/exp/mantissa bits | Bias | Max finite | Min normal | Min subnormal | Inf | NaN | Used for |
|---|---|---|---|---|---|---|---|---|
| E4M3 (OCP FP8, "fn") | 1/4/3 | 7 | 448 | 2^-6 | 2^-9 | none | `S.1111.111` | MXU FP8 operands, weights, KV, P |
| E5M2 (OCP FP8) | 1/5/2 | 15 | 57344 | 2^-14 | 2^-16 | `S.11111.00` | `S.11111.{01,10,11}` | storage only (converted to BF16) |
| E2M1 (OCP FP4) | 1/2/1 | 1 | 6 | 1 | 0.5 | none | none | MXFP4 / NVFP4 elements |
| E8M0 (MX scale) | 0/8/0 | 127 | 2^127 | 2^-127 | - | none | `0xFF` | MX block scales (no zero, no sign) |
| BF16 | 1/8/7 | 127 | 3.39e38 | 2^-126 | 2^-133 (kept by copies and conversions; read as 0 by arithmetic) | IEEE | IEEE | activations, stream, BF16 weights |
| FP16 | 1/5/10 | 15 | 65504 | 2^-14 | 2^-24 | IEEE | IEEE | storage and conversion only |
| FP32 | 1/8/23 | 127 | 3.40e38 | 2^-126 | 2^-149 (kept by copies and conversions; read as 0 and never produced by arithmetic) | IEEE | IEEE | all internal arithmetic, ACC, scales, state |
| INT8 / UINT8 / INT16 / INT32 | two's complement / unsigned | | | | | | | indices, counts, integer index math |

- E2M1 values are ±{0, 0.5, 1, 1.5, 2, 3, 4, 6}.
- NaNs that the hardware produces are canonical and positive:

  | Format | Canonical NaN |
  |---|---|
  | E4M3 | `0x7F` |
  | E5M2 | `0x7E` |
  | BF16 | `0x7FC0` |
  | FP16 | `0x7E00` |
  | FP32 | `0x7FC00000` |

  A NaN that is only copied keeps its bits.

**Block formats** (OCP MX v1.0 and NVIDIA NVFP4, as published in the target models' safetensors):

| Format | Elements | Block | Block scale | Second-level scale | Bytes/element |
|---|---|---|---|---|---|
| MXFP8 | E4M3 | 32 | E8M0 | - | 1.03125 |
| MXFP4 | E2M1, 2 per byte | 32 | E8M0 | - | 0.53125 |
| NVFP4 | E2M1, 2 per byte | 16 | E4M3, positive | FP32 per tensor | 0.5625 |

- Value = element × block scale (× tensor scale for NVFP4).
- FP4 packing: element 2i is in bits [3:0] of its byte and element 2i+1 in bits [7:4].
- Blocks run along the contiguous (K, input) dimension of a weight, and along the head dimension of a KV entry.
- Weights: scales are a separate array `[rows, K / block]`, row-major, at `R[scale_reg]` with row stride TD `aux0`.
- Paged KV and index keys: each page holds the scales of its rows in a scale region (memmap.md §6).
- The NVFP4 FP32 tensor scale is the DMA operand `R[ts_reg]` (isa.md §5), and `p_reg` for `VQUANT`.

## N-2. Conversions

Notation:
- `RNE_f(x)` is x rounded to format f.
- `sat` means: round with RNE as if the exponent range were unbounded; a magnitude above max then maps to ±max, ±Inf maps to
  ±max, and NaN maps to the canonical NaN. A finite |x| that rounds to max is not saturation: for E4M3, |x| in (448, 464]
  gives 448 (464 is the tie, which goes to the even code) and sets no flag. `SATURATE` (N-5) is set only when a finite input
  would have rounded above max; ±Inf gives ±max and sets no flag.
- The ties at the top follow from "as if the exponent range were unbounded": the next value above max is a value of the
  unbounded range, even where the format has no such code. For E4M3 it is 480 (the code `S.1111.111`, which is the NaN),
  so 464 is a tie between 448 and 480 and goes to the even 448: no flag. For E5M2 the tie 61440 lies between 57344 and
  65536; the even neighbour is 65536, above max, so 61440 gives 57344 **with** `SATURATE`. For E2M1 the tie 7 lies between
  6 and 8; the even neighbour is 8, so 7 gives 6 **with** `SATURATE` (6.5 gives 6 without it).

| From → to | Where | Rule |
|---|---|---|
| FP32 → BF16, FP16 | DMA `FP32_BF16`, VPU outputs | `RNE`. IEEE overflow to ±Inf. Subnormal results are **kept**: RNE onto the destination's subnormal grid (BF16 2^-133, FP16 2^-24), and a result that rounds up to the minimum normal is the minimum normal. An FP32 subnormal input is converted, not read as zero. (VPU outputs are FTZ FP32 values: zero or at least 2^-126. For BF16 that rules out subnormal results, so BF16 subnormals arise only from raw FP32 subnormals through the DMA. For FP16 it does not: a normal FP32 below 2^-14 becomes an FP16 subnormal, or rounds to 0 or to the minimum normal.) |
| FP32 → E4M3, E5M2 | VPU outputs, `VQUANT` | `RNE` + `sat` |
| FP32 → E2M1 | `VQUANT` | `RNE` + `sat` to ±6 (NaN: block rule below) |
| FP32 → INT* | VPU outputs | `RNE`, saturate to the integer range, NaN → 0 with `NAN_IN` (the conversion is an arithmetic unit that consumed a NaN, N-5). `SATURATE` is set when a finite input is clamped (also a negative input that rounds to a non-zero value, to an unsigned type; −0.5 → 0 is not a clamp); ±Inf clamps with no flag |
| E4M3, E5M2, E2M1 → BF16 / FP32; BF16, FP16 → FP32 | DMA, VPU inputs | exact. E5M2 Inf stays Inf. A BF16/FP32 subnormal arriving at an arithmetic unit reads as ±0 |
| FP16 → BF16 | DMA | `RNE`, **not exact**: an FP16 significand has 11 bits and BF16 holds 8 (relative error ≤ 2^-9). The result cannot overflow (the largest FP16, 65504, rounds to 65536 = 2^16, a normal BF16); FP16 subnormals are kept |
| MXFP8, MXFP4, NVFP4 → BF16 | DMA `*_BF16` | exact: element × block scale. E2M1 × E4M3 has at most 6 significant bits, BF16 holds 8. The NVFP4 tensor scale (`R[ts_reg]`) is **not** applied to the elements; it becomes a column scale (one per 128-deep K tile). Results in the BF16 subnormal range are **kept** (RNE onto the subnormal grid, as every exact conversion, N-5; review finding I-15 removed the word "flush"); MXFP4 and NVFP4 products are always exact there; only MXFP8 products below 2^-130 can round. Overflow gives Inf (never seen in real weights) |
| MXFP8, MXFP4, NVFP4 → E4M3 + column scales | DMA `*_E4M3` | **fold**, below |

**Fold (`cvt MXFP4_E4M3`, `NVFP4_E4M3`, `MXFP8_E4M3`).** For each weight row r and each 256-element K tile t (8 MX blocks or 16 NVFP4
blocks; the last tile may be shorter):
1. Compute `w = element × block scale` exactly (tensor scale excluded) and `amax = max |w|` over the tile.
   The DMA buffers one tile per row (≤ 256 elements) to know `amax` before it writes.
   A NaN element, and every element of a block whose scale is NaN, does not take part in `amax` and is written as the E4M3
   NaN `0x7F` (with `NAN_IN`); the other elements of the tile keep their accuracy. A tile with no non-NaN, non-zero element
   has `c = 0`. The positions of a short last tile that hold no element are presented to the fold as element 0 with a finite
   block scale (E8M0 127, E4M3 `0x38`), so they neither take part in `amax` nor turn into NaN (a zero element with a NaN scale is
   written `0x7F` with `NAN_IN`). The DMA engine drops the written value and the flags of those positions.
2. Choose `c`, the smallest integer with `amax ≤ 448 · 2^c` (`c = 0` for an all-zero tile).
3. Write the elements `v = RNE_E4M3(w · 2^-c)` with `sat`. Because of step 2 it never saturates.
4. Write the column scale `cs[t][r] = 2^c` (× the FP32 tensor scale `R[ts_reg]` for NVFP4). The array is `[K tiles, N]` (`r` is the column `n`), the orientation of `mxu_mm` and the ISA (review finding S-1; the golden `fold_e4m3` returns it and `unfold` takes it, mxu.md M-12). `fold_c` (`rtl/arith`) produces
   `2^c`.
   - **NVFP4:** the DMA engine multiplies it by `R[ts_reg]` with one `fp32_mul` (RNE, DAZ/FTZ) and ORs that multiply's flags
     into `FP_STATUS` (OVERFLOW for a huge `T`, NAN_IN for a NaN `T`). The operand is named in isa.md §5; the multiply, its
     flags and the raw write of `T` for `NVFP4_BF16` are in the "Integration contract" of arith.md.
   - The product of a power of two and a normal FP32 is exact while it stays in the normal range; above it, it is Inf with
     `OVERFLOW`. Below 2^-126 it is rounded, then flushed (principles): a zero, except the single value `2^-126 − 2^-150` (a
     `T` whose significand is all ones), which is a rounding tie and goes up to 2^-126 (`fp32_mul(0x08FFFFFF, 2^-17)` =
     `0x00800000`, no flag). A subnormal `T` reads as 0 (DAZ). With `c ≥ −18` (NVFP4) the product goes below 2^-126 only
     for `T < 2^-108`.
   - **MX formats:** `cs = 2^c` is written as is. For `c < −126` (a tile with `amax < 2^-118`; no real weight reaches it)
     `2^c` is an FP32 subnormal, which the ACC's column-scale multiplier reads as 0 (DAZ), so the tile contributes 0. The golden
     model (`numerics.fold_e4m3`, `mxu_mm`) keeps such a scale and multiplies by it, so **golden and RTL differ for these
     tiles**; N-8 exempts them and the unit tests stay out of that range.

`MXU_MM` multiplies each tile's partial sum by `cs` (N-3).

Exactness:
- **MXFP4: exact** whenever the block exponents inside a tile span at most 14 binades. Why:
  - `c ≤ X_max − 6`.
  - The smallest non-zero folded value, `0.5 · 2^(X_b − c)`, stays on the E4M3 subnormal grid (≥ 2^-9) when `X_b ≥ X_max − 14`.
  - E2M1 has one mantissa bit, so every other value fits too.
  - Measured: no element changed on any test distribution (E1).
- **MXFP8:** exact except elements that drop below E4M3's normal range after the shift. Those round. Measured relative weight error is
  ≤ 1e-6 (E1).
- **NVFP4: rounds** (the "N-2 residual"). E2M1 × E4M3 needs up to 6 significant bits and E4M3 holds 4.
  - On realistic weights 13-23% of elements change.
  - The relative weight error is 2.1-2.4%, against 8.7-9.5% for NVFP4 quantization itself.
  - Where the fold may be used is decided in N-7 (D1).

**Quantization (`VQUANT`).** All arithmetic is FP32. Divisions are defined as correctly rounded. The RTL computes them with the SFU
reciprocal and must match within the tolerance of N-8.

| `quant_fmt` | Scale | Elements |
|---|---|---|
| `E4M3_ROW` | `s_r = amax_r / 448` (1.0 if the row is zero), FP32 per row | `RNE_E4M3(x / s_r)`, sat |
| `E4M3_TENSOR` | FP32 from `p_reg` | `RNE_E4M3(x / s)`, sat |
| `MXFP8`, `MXFP4` | OCP MX v1.0: `X = floor(log2 amax) − emax_elem`, with emax_elem = 8 for E4M3 and 2 for E2M1, clamped to [−127, 127]; `X = −127` for a zero block | `RNE(x · 2^-X)`, sat. Exact power-of-two scaling, so no division |
| `NVFP4` | tensor scale `T` from `p_reg`. The compiler passes `amax_tensor / (448 · 6)` for weights and activations, and a calibrated constant (or 1.0, DeepSeek) for KV. Block scale `s_b = RNE_E4M3(amax_b / 6 / T)`, sat | `RNE_E2M1(x / (s_b · T))`, sat; `s_b = 0` gives zero elements |

A block that contains a NaN or Inf gets a NaN scale (`0xFF` for E8M0, `0x7F` for E4M3) and zero elements, following OCP MX.
`VQUANT` finds the NaN or Inf in a block from the block's own inputs (the same pass that finds `amax`), not from the flags of
`cvt_fp32_fp`, which saturates an Inf to ±max without a flag and gives a NaN input to E2M1 as code 0 with `NAN_IN`. Flags:
a block with a NaN input sets `NAN_IN`; a block with an Inf and no NaN makes a NaN scale from non-NaN inputs and sets
`INVALID` (N-5).

## N-3. MXU arithmetic

**Operand modes:**
- `NORMAL` with E4M3 × E4M3, or BF16 × BF16. Both operands share the mode.
- E5M2 and FP16 tensors are converted to BF16 by the DMA; the MXU has no E5M2 or FP16 datapath. E5M2 → BF16 is exact; FP16 →
  BF16 rounds (RNE, 11 to 8 significand bits, relative error ≤ 2^-9), so an FP16 tensor loses 3 significand bits on the way
  to the MXU.
- `BF16X3`: **reserved in v1** (`ILLEGAL_FIELD`, isa.md §5; review finding S-6). Its definition is kept below for a later version.

**Geometry parameter `DIM`** (review finding S-2; mxu.md M-13). The array is `DIM` × `DIM` PEs: `DIM` = 64 in `FULL`, 16 in `MID`, 8 in
`TINY`. Everything below that says 64 is `DIM`: the K tile is `4·DIM` FP8 or `2·DIM` BF16 elements (256 / 128 in `FULL`), the N
tile is `DIM`, the partial sum passes PE rows `0..DIM-1`. **The accumulation order, so the last bits of a result, depend on `DIM`**:
a K = 64 FP8 instruction is one chain of 16 PE rows in `FULL` and two chains of 8 rows (their partials added in the ACC) in `TINY`.
Bit-exactness is therefore per config (N-8); a `TINY` result differs from `FULL` by FP32 rounding (15 of 32 outputs of a
M = 4, K = 64, N = 8 example). The fold tile does not change with `DIM` (step 4 below).

**FP8 PE.**
- Each PE holds 4 stationary E4M3 weights.
- Per cycle it forms the 4 products exactly. Each product is a multiple of 2^-18 below 2^18.
- It adds them exactly in a 39-bit fixed-point adder (36 bits of range, 2 carry bits and the sign).
- It rounds that sum once to FP32 (RNE) and adds it to the incoming partial sum with an FP32 adder (RNE, FTZ).
- So there are two roundings per PE.

**BF16 PE.**
- At half rate the PE holds 2 stationary BF16 weights and forms 2 exact products (16 significant bits each).
- It rounds their exact sum once to FP32, then does the same FP32 add as in FP8 mode.
- A BF16 stationary tile is K = `2·DIM` × N = `DIM` (`FULL`: 128 × 64). A rows (`FULL`: 128 BF16 = 256 B) stream one per cycle, so the array does 8,192 BF16 MAC/cycle,
  half the FP8 rate (arch.md). This corrects microarch.md §2.2, which had each BF16 row taking 2 cycles (quarter rate).
- Each PE needs two BF16 multipliers next to its four FP8 ones. Phase 6 synthesis costs this, since sharing the FP8 multipliers would
  give quarter rate.

**Order** (bit-reproducible):
1. Per K tile, the partial sum enters the top PE row as +0.
2. It passes PE rows `0..DIM-1` in order (`FULL`: 0..63). PE row i holds K elements `4i .. 4i+3` (FP8) or `2i, 2i+1` (BF16) of the tile.
   Slots past the tile's K (the K tail of a short tile) are `+0` operands: a PE row that holds only tail slots forms the products
   `+0 x +0`, so its dot is `+0`. An exact-zero dot follows IEEE: it is `-0` only when every product of the dot is `-0`, else `+0`
   (round-to-nearest). Therefore a `-0` partial sum survives only a full K tile: any all-tail PE row after it adds `-0 + +0 = +0`
   (review R-3). A `-0` result of a short tile becomes `+0`, and a K tile is `4·DIM` / `2·DIM` elements, so the sign of a zero
   result depends on where the tile boundary falls, i.e. on `DIM` (like the rounding, above).
3. The tile's partial P_t (FP32) leaves the bottom of the array.
4. At the ACC input:
   - `RESCALE`, once before the first tile: `ACC ← RNE(ACC × rowscale[m])`.
   - if `cs_reg` is set: `P_t ← RNE(P_t × cs[t // R][n])`, with `R = 64 / DIM` (`R = 1` in `FULL`): the scale array is per 256-deep
     FP8 / 128-deep BF16 fold tile in every config (isa.md §5), and a shorter MXU tile of a reduced config uses the scale row that
     covers it (review finding S-2). The array is `[K tiles, N]`, row pitch `roundup(4·N, 64)` B (review finding S-1);
   - `OVERWRITE` on the first tile: `ACC ← P_t`;
   - otherwise: `ACC ← RNE(ACC + P_t)`.
5. K tiles (`4·DIM` / `2·DIM` deep) are taken in ascending K, starting at the instruction's K origin.
   - Splitting K differently (across instructions, or across cores followed by a reduction) changes results only at FP32 rounding level.
   - The compiler records the split, so the golden model can follow it.

**`BF16X3`** (reserved in v1, see above; the definition is the contract of a later version and of `numerics.mxu_mm_bf16x3`).
- The operands are split as `A = A_hi + A_lo` and `W = W_hi + W_lo`, where `x_hi = RNE_BF16(x)` and `x_lo = RNE_BF16(x − x_hi)`.
- Per `2·DIM`-deep K tile there are three passes, `A_lo·W_hi`, `A_hi·W_lo`, `A_hi·W_hi`, accumulated into ACC in that order.
- The result is FP32-class: it drops `A_lo·W_lo` (about 2^-16 of each product) and the rounding of the lo parts.
- When A is exactly BF16 (`A_lo = 0`, as for the hyper-connection stream), the compiler issues two `NORMAL` BF16 passes instead:
  `W_lo` with `ACCUM`, then `W_hi`. The result is identical in 2/3 of the time. This is the only form v1 runs (the mHC mixer, D2).

**Accuracy (E4).**
- K = 16384, measured: the specified order is within 2.1 ulp (FP8) and 3.8 ulp (BF16) of the exact result at the largest output.
- A one-product-at-a-time FP32 loop is off by 4.0 and 41 ulp.
- With FP8 operands of normal magnitude the FP32 accumulation is often exact.

**Special values.**
- A NaN operand gives NaN.
- FP32 overflow during accumulation gives ±Inf, and `Inf − Inf` gives NaN, as IEEE.
- E4M3 has no Inf, so in FP8 mode Inf can only come from the accumulator. In BF16 mode an Inf operand, and a dot product that
  overflows (`bf16_dot2([7F7F, 0], [7F7F, 0])` gives Inf with `OVERFLOW`), produce it too.

## N-4. VPU and SFU

**Lanes.**
- All lane arithmetic is FP32 with DAZ/FTZ. FMA is fused (one rounding).
- Inputs are converted exactly from their descriptor dtype; outputs are converted by N-2.
- Integer operations wrap modulo 2^32 (2^64 for 64-bit index math).

**SFU accuracy** (maximum error against the correctly rounded FP32 result; Phase 3 unit tests check these bounds against NumPy over the
whole domain):

| Function | Bound | Domain and special cases |
|---|---|---|
| reciprocal | 1 ulp | ±0 → ±Inf, ±Inf → ±0 |
| rsqrt | 2 ulp | x < 0 → NaN, +0 → +Inf |
| sqrt | 1 ulp | x < 0 → NaN |
| exp, exp2 | 2 ulp | overflow → +Inf, underflow → +0 (FTZ), −Inf → +0 |
| log | 2 ulp (absolute 2^-21 near x = 1) | x < 0 → NaN, +0 → −Inf |
| sigmoid = rcp(1 + exp(−x)) | 4 ulp | |
| SiLU = x · sigmoid(x) | 5 ulp | |
| softplus | 4 ulp | x > 20 → x |
| tanh | 4 ulp, or absolute 2^-22 for \|x\| < 2^-4 | |
| GELU (tanh form) | absolute 2^-20 + 4 ulp | |

**Reductions** (`VRED`, the row sums inside `VNORM`, `VSOFTMAX`, `VROUTE`):
1. Element j of a row is added, in order, into accumulator slot `j mod 256`.
2. The 256 slots are combined by a fixed pairwise tree: slot 2i with 2i+1, then pairs of pairs, and so on.

Rows shorter than 256 leave slots at +0. Max and min are exact. The order does not depend on timing, so reductions are bit-reproducible.
`VMATVEC` accumulates each output with an FP32 FMA chain in ascending K.

**Online softmax (`VSOFTMAX`).**
1. `s' = scale · s`; if softcap is set, `s' = cap · tanh(s'/cap)`. Masked positions are −Inf.
2. `m_new = max(m, rowmax s')`, `alpha = exp(m − m_new)`, `P = exp(s' − m_new)`, `l = alpha · l + rowsum(P)`.
   - `rowsum` uses the FP32 values of P, before P is rounded to its E4M3 or BF16 destination.
   - A row that is still fully masked keeps `m = −Inf`, `alpha = 1`, `P = 0` (never NaN).

**Top-k (`VTOPK`, `VROUTE`).**
- Descending by value. Ties go to the lower index. −0 and +0 are equal and go to the tie rule.
- NaN ranks below −Inf: it is chosen only when fewer than k entries are not NaN.
- The routing normalization sums in fixed index order.

## N-5. Special values and status flags

| Unit | NaN | Inf | Subnormal | −0 |
|---|---|---|---|---|
| DMA copy, gather, scatter | bits kept | kept | kept | kept |
| DMA conversions | canonical in the destination | kept (to BF16/FP32); never produced into E4M3 | kept in every destination (FP8, FP4, BF16, FP16, FP32; N-2) | kept |
| MXU | propagates | from FP32 overflow of the accumulation; in BF16 mode also an Inf operand and a dot product that overflows (N-3). E4M3 has no Inf | FP8 kept; BF16 inputs DAZ; results FTZ | `+0` from an all-zero tile; a `-0` partial sum (all products `-0`, or a cancellation flushed to `-0`) leaves the array only from a full K tile, a short tile ends in `+0` (K-tail slots are `+0` operands, N-3 order) |
| VPU / SFU | propagates (IEEE); produced by invalid operations | IEEE | DAZ / FTZ | IEEE |
| Conversions to FP8 / FP4 | canonical NaN; FP4 → block NaN scale | saturates to ±max | kept | kept |
| Write-add (`ADD_F32`, `ADD_BF16`) | propagates | IEEE | operands DAZ, results FTZ (like every arithmetic unit) | IEEE |
| `MAX_F32` | NaN wins; the result is the canonical NaN (a NaN that is only copied, N-1, keeps its bits) | ordered | DAZ: a subnormal operand reads as a zero of its sign, and a result that is such an operand is that zero (`max(2^-149, −1) = +0`) | `max(−0, +0) = +0` |

`FP_STATUS` CSR (per core, sticky, write 1 to clear) is set by any unit of the core, including the DMA conversions. A write-add or max
at an SRAM target sets the `FP_STATUS` of the target's core (the ALU is at the target: the core's network interface for SPM, the slice itself for GSRAM), not of the initiator.
A flag belongs to one **operation**: one multiply, one add, one dot product, one conversion. A lane that chains two (the ACC lane
multiplies, then adds) reports the flags of each:

| Bit | Name | Set when |
|---|---|---|
| 0 | `INVALID` | an operation produced NaN from non-NaN inputs. Not set when the same operation also has a NaN input (the NaN wins: `NAN_IN` only; for example a dot product with a NaN operand and an Inf × 0). "Operation" is one multiply or one add, not the ACC lane: a NaN `acc` added to an Inf × 0 of the column scale gives `INVALID` (the multiply) and `NAN_IN` (the add) |
| 1 | `OVERFLOW` | an FP32/BF16 result overflowed to Inf (a finite exact value that rounded to Inf; an Inf operand does not set it) |
| 2 | `SATURATE` | a conversion to FP8/FP4 saturated a finite value, or a conversion to an integer clamped one (N-2) |
| 3 | `NAN_IN` | an arithmetic unit or converter consumed a NaN: a NaN among its external operands that its arithmetic uses. A NaN made inside the unit is `INVALID` and is not consumed again (the ACC lane multiplies and then adds: Inf × 0 in the column scale gives `INVALID` only; the PE takes `NAN_IN` from its dot product only). A copy consumes nothing: an `OVERWRITE` of a NaN partial sum without a column scale sets no `NAN_IN` (the PE column reported it) |

The CP collects the flags after each kernel in debug runs. Nothing traps (isa.md §8).

## N-6. Write-add and collectives

- `ADD_F32` computes `RNE_FP32(old + new)` per element. The operands are DAZ and the result FTZ, like every arithmetic unit
  (`ADD_F32(2^-149, 2^-126)` is `2^-126`). The flags go to the `FP_STATUS` of the target's core (N-5).
- `ADD_BF16` computes `RNE_BF16(RNE_FP32(old + new))`. That is two roundings, kept so that the target ALU stays an FP32 adder; the
  golden model does the same. The flags are those of one unit: `Inf − Inf` gives `INVALID` from the add, and the NaN it made
  must not set `NAN_IN` again in the FP32 → BF16 converter (`core/ni` composes `fp32_add` and `cvt_fp32_fp` and takes
  `NAN_IN` from the add only).
- `MAX_F32` follows N-5.

**Order.**
- Writes from different sources reach a target in no defined order (noc.md §7). Two contributors into a +0 buffer commute exactly.
  With three or more, the FP32 sum depends on arrival order, so results are **not bit-reproducible run to run**.
- Error bound for n contributors: `|error| ≤ (n − 1) · 2^-24 · Σ|x_i|`.

**Deterministic mode.**
- The compiler option `deterministic_collectives` replaces the reduction step: each source writes its contribution into its own slot,
  and the owner sums the slots with `VRED` in source order.
- Cost: n slots of buffer and one `VRED`. For the `[64 tokens × 25]` hyper-connection all-reduce over 32 cores this is about 200 cycles
  (51,200 elements at 256 per cycle).
- E2E regressions (Phase 5) run in deterministic mode so that RTL and golden results can be compared exactly. Performance runs use
  write-add.

## N-7. Experiments and decisions

Synthetic inputs with fixed seeds. Full tables are in [`numerics_experiments.md`](../reports/numerics_experiments.md).

**E1 / D1: NVFP4 fold vs exact BF16 path.**

Measured (MiniMax expert shape, three weight distributions):

| Path | GEMM error |
|---|---|
| NVFP4 fold, weights only | 2.1-2.3% |
| E4M3 per-row activations, which every FP8 GEMM already pays | 2.4-2.5% |
| Fold + E4M3 activations, the MXU FP8 path | 3.1-3.4% |
| NVFP4 activations (W4A4, as MiniMax-M3.1 is served on Blackwell) | 8.5-9.2% |
| Exact BF16 path | 2e-7 |

Decision D1:
- **Decode and other memory-bound GEMMs use the exact path** (`cvt NVFP4_BF16`). Decode is HBM-bound, and the stages of the weight
  path before the MXU keep a 1.60x margin over a core's HBM share at 0.85 with NVFP4 expanded to BF16 (1.36x at peak share): the
  DMA converter (256 elements/cycle) carries 256 BF16 elements per cycle, and the DMA SPM write port (512 B/cycle) carries 248,
  because it also writes the block scales into the staging ring before the data (review finding V-1; numerics_experiments.md E1).
  - **The MXU is the narrowest stage on this path** (review finding S-5, mxu.md M-17). The stationary-load port could carry 256
    BF16 elements per cycle (512 B / 32 cycles), but the tile period at `M = 1` (two shadow buffers) is **measured: 39.5 cycles at
    `DIM` 64** (21.5 at `DIM` 32, 13 at `DIM` 16; `tb_mxu` with a 1R-per-bank SPM model, mxu.md §7, M-21). That gives
    16 KiB / 39.5 = 415 B/cycle, **207 BF16 elements per cycle**: **1.15x** the core's peak HBM share (181 elements/cycle for
    NVFP4) and 1.35x at 0.85 (154); 1.14x / 1.34x with the A, W and scale ports sharing the banks at a conflict-free pitch (3 KiB,
    4.5 KiB). At the colliding 2.5 KiB pitch (memmap.md §4) the period is 43 (not the 48 of the load alone): **1.05x** and 1.24x,
    so the margin survives a bad pitch but only just. A third shadow buffer (period 32, 1.41x, mxu.md §9) would close the gap; it
    is not simulated.
  - The FP8 paths (always the fold for MXFP4 / MXFP8) keep 415 B/cycle against 193 elements/cycle at peak share: 2.15x (2.5x at 0.85).
  - Review finding I-3: the margin was first checked at the stationary-load port only. The DMA SPM write port was then 256 B/cycle,
    which held the exact path to 0.83x of the HBM share. It is now 512 B/cycle (microarch.md §2.5).
  - The fold paths are also held to 256 elements per cycle by the converter: 1.56x (MXFP4) and 1.65x (NVFP4) over the share.
- **Compute-bound GEMMs (prefill, large verify batches) use the fold** (`cvt NVFP4_E4M3`).
  - Its error is about the same as the FP8 activation quantization every FP8 deployment already accepts.
  - It is less than half the error of the served W4A4 path.
  - The exact path would cost +44% on MiniMax prefill (32K prompt) and +7% on DeepSeek's NVFP4 global-KV attention.
- The compiler picks per GEMM from the roofline, and option `nvfp4_exact` forces the exact path everywhere.

MXFP4 and MXFP8 always fold: the fold is exact for MXFP4 and below 1e-6 for MXFP8.

**E2 / D2: hyper-connection mixer precision (HC-2).** Toy 80-sublayer mHC network; KL of the final distribution against an FP32 mixer.

| Mixer | KL, own contribution (FP32 stream) | KL with the BF16 stream | Verdict |
|---|---|---|---|
| reorder-only FP32 ("null") | 1e-11 | 2-5e-4 | the BF16 stream's own noise floor |
| BF16 hi/lo (= `BF16X3`) | 2-6e-11 | 3-7e-4 | indistinguishable from FP32 |
| BF16 | 2e-6 to 2e-5 | 5-9e-4 | meets the proposed KL < 1e-3, about 2x the floor |
| E4M3 | 5e-4 to 5e-3 | 1.3-6.2e-3 | rejected |

Decision D2:
- **The mHC mixer runs as two BF16 MXU passes over pre-split `W_lo`, `W_hi`** (`BF16X3` with the zero `A_lo` pass dropped, N-3).
- The weights are stored as hi/lo BF16 pairs, the same 4 bytes per element as the published FP32.
- Cost: 2 × 0.49 M MACs per sublayer, below 1% of a layer.
- Plain BF16 is the documented fallback.
- Qwen's gated mixer has BF16 weights and runs in plain BF16.

**E3 / D3: BF16 linear-attention state.** Gated delta rule (KDA form), 128 × 128 heads, 8192 tokens; output error against float64.

| State storage | Output error |
|---|---|
| FP32 | 3-6e-7 |
| BF16, rounded every token (decode) | 0.7-2.1% |
| BF16, rounded once per 64-token chunk | 0.23-0.26% |
| reference: rounding the output itself to BF16 | 0.17% |

- The BF16 per-token error does not grow over 8K tokens (the decay and the delta rule bound it), but it is 4-12x the BF16 output rounding.
- Stochastic rounding is worse (1.1-3.0%).
- The gain would be +3% decode tokens/s for GLM-5.3-Flash and Ling, and +6% for Qwen, at the serving point.

Decision D3: **the recurrent state stays FP32** in HBM and SPM, as published (arch.md row unchanged). A 3-6% gain does not justify a
recurrent error 4-12x the activation rounding without a full-model evaluation.

**E4 / D4: accumulation.** FP32 accumulation with the PE order of N-3 is within 2-4 ulp at K = 16384. No wider accumulator and no
Kahan-style compensation are needed.

## N-8. Golden model and test tolerances

- **Arithmetic blocks (Phase 3), bit-exact** against `numerics.py`:
  - FP8 PE: all 65,536 E4M3 × E4M3 products, plus random and corner 4-way sums with incoming partials.
  - BF16 PE: random inputs plus corners (zeros, max, min normal, NaN, Inf).
  - Converters: every 8-bit and 4-bit source code. For FP32 → FP8/FP4/BF16: random values, plus every rounding midpoint, `max + ½ ulp`,
    and the subnormal and normal boundaries.
  - Fold: random MXFP4, MXFP8 and NVFP4 tiles, plus tiles with a 14-binade and a 15-binade block-exponent spread.
  - **Exemption: MX fold tiles with `amax < 2^-118`.** Their column scale `2^c` (`c < −126`) is an FP32 subnormal. `fold_c`
    writes it exactly, as the golden model does, but the ACC's column-scale multiplier reads it as 0 (DAZ) while
    `numerics.fold_e4m3` / `mxu_mm` multiply by it (N-2 step 4). From the ACC on, golden and RTL differ for these tiles, so
    bit-exact tests of the ACC and the MXU keep tiles out of that range. No real weight reaches it.
- Reference libraries: `ml_dtypes` agrees with the golden model on FP32 inputs. It rounds float64 inputs twice (through float32), so
  tests compare on FP32 values, which is all the hardware ever converts.
- **MXU, bit-exact** against `mxu_mm(…, dim=DIM)` for the config's `DIM` and the same tiling, except the fold tiles exempted above
  (review finding S-2). The model takes a `dim` parameter (default 64): PE rows `0..dim-1`, K tile `4·dim` / `2·dim`, `cs[t // R]`.
  `TINY` (8) and `MID` (16) tests compare with `dim` 8 and 16 and the ACC image has `dim` FP32 per row; they do not compare with
  the `FULL` order, which rounds differently. *(Implemented in `numerics.py`: not yet. mxu.md M-12 / M-13 list the model changes.)*
- **SFU:** the ulp bounds of N-4.
- **Fused VPU instructions:**
  - FP32 outputs within the composed bound: the sum of the SFU bounds used plus 1 ulp per lane operation, against a golden model that
    uses correctly rounded functions.
  - FP8/FP4 outputs: at most one code apart, and only where the FP32 value before rounding lies within that bound of a rounding boundary.
- **E2E layers (Phase 5)** run in deterministic-collective mode and need two checks:
  1. Against the kernel-level golden model, which uses `numerics.py` and the same tiling: bit-exact except for the SFU-dependent outputs,
     where the per-tensor relative Frobenius error must be ≤ 1e-5 (FP32 outputs) or ≤ 0.1% of elements one code apart (FP8 outputs).
  2. Against a float64 NumPy reference of the layer, to catch systematic errors. Relative Frobenius error ≤ 5% for FP8 GEMM paths, ≤ 1%
     for BF16 paths, ≤ 1e-4 for FP32 paths.

## N-9. Decisions and alternatives

| Decision | Alternative rejected | Reason |
|---|---|---|
| RNE everywhere, saturating FP8/FP4 conversions | stochastic rounding; overflow to NaN (`ml_dtypes` default) | Inference needs no stochastic rounding (it was worse for the recurrent state, E3); NaNs from a scale slightly too small would poison a whole tile |
| DAZ/FTZ for FP32/BF16 arithmetic | full IEEE subnormals | saves the subnormal normalization logic in 256 FMAs, the ACC adders and the write-add ALUs; values below 2^-126 never matter in these models; FP8/FP4 subnormals, which do matter, are kept |
| Power-of-two column scale per 256-deep K tile (fold) | per-block scales inside the array | a weight-stationary array would need a scale multiply per PE per K step; the fold is exact for MX formats |
| NVFP4 exact BF16 path when memory-bound, fold when compute-bound | always fold; always BF16; E4M3 hi + lo split | always-fold costs 2% in decode for nothing; always-BF16 costs +44% MiniMax prefill; hi + lo costs the same as BF16 and is less exact |
| mHC mixer in BF16 hi/lo | FP32 on the VPU (`VMATVEC`); plain BF16 | equal to FP32 at negligible cost; `VMATVEC` would spend VPU time in the latency-critical decode step |
| FP32 recurrent state | BF16 state | +3-6% decode does not justify a 0.7-2% recurrent output error |
| Fixed reduction trees, deterministic-collective mode for verification | arrival-order reductions only | bit-exact RTL and golden comparison; write-add stays the fast path |

**Open items:**
- Confirm D2 on real weights: the DeepSeek V4.1 Flash `hc_fn` tensors are 2 MB per sublayer and can be fetched by HTTP range requests.
  A full-model KL is out of reach on this machine.
- Revisit D3 if a model's published code itself keeps a BF16 state, or if a full-model evaluation becomes possible.
- `VQUANT NVFP4` for KV follows the NVIDIA recipe, not necessarily DeepSeek's own FP4 KV quantizer. Bit-exact agreement with a served
  cache is not needed, because this NPU reads only the KV it writes.
- Phase 3 measures the SFU microcode error bounds; Phase 6 measures the BF16 multiplier area cost (N-3).
