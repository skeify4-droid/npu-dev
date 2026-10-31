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
  arithmetic (DAZ on inputs, FTZ on results); copies and exact conversions keep them.
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
| BF16 | 1/8/7 | 127 | 3.39e38 | 2^-126 | (flushed) | IEEE | IEEE | activations, stream, BF16 weights |
| FP16 | 1/5/10 | 15 | 65504 | 2^-14 | 2^-24 | IEEE | IEEE | storage and conversion only |
| FP32 | 1/8/23 | 127 | 3.40e38 | 2^-126 | (flushed) | IEEE | IEEE | all internal arithmetic, ACC, scales, state |
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
- Scales are a separate array `[rows, K / block]`, row-major. The DMA reads it through the instruction's auxiliary descriptor.

## N-2. Conversions

Notation:
- `RNE_f(x)` is x rounded to format f.
- `sat` means |x| > max and ±Inf map to ±max, and NaN maps to the canonical NaN.

| From → to | Where | Rule |
|---|---|---|
| FP32 → BF16, FP16 | DMA `FP32_BF16`, VPU outputs | `RNE`. IEEE overflow to ±Inf. BF16 results below 2^-126 flush to ±0; FP16 keeps subnormals |
| FP32 → E4M3, E5M2 | VPU outputs, `VQUANT` | `RNE` + `sat` |
| FP32 → E2M1 | `VQUANT` | `RNE` + `sat` to ±6 (NaN: block rule below) |
| FP32 → INT* | VPU outputs | `RNE`, saturate to the integer range, NaN → 0 |
| E4M3, E5M2, E2M1, BF16, FP16 → BF16 / FP32 | DMA, VPU inputs | exact. E5M2 Inf stays Inf. A BF16/FP32 subnormal arriving at an arithmetic unit reads as ±0 |
| MXFP8, MXFP4, NVFP4 → BF16 | DMA `*_BF16` | exact: element × block scale. E2M1 × E4M3 has at most 6 significant bits, BF16 holds 8. The NVFP4 tensor scale is **not** applied to the elements; it becomes a column scale (one per 128-deep K tile). Values outside the BF16 normal range (never seen in real weights) overflow to Inf or flush |
| MXFP8, MXFP4, NVFP4 → E4M3 + column scales | DMA `*_E4M3` | **fold**, below |

**Fold (`cvt MXFP4_E4M3`, `NVFP4_E4M3`, `MXFP8_E4M3`).** For each weight row r and each 256-element K tile t (8 MX blocks or 16 NVFP4
blocks; the last tile may be shorter):
1. Compute `w = element × block scale` exactly (tensor scale excluded) and `amax = max |w|` over the tile.
   The DMA buffers one tile per row (≤ 256 elements) to know `amax` before it writes.
2. Choose `c`, the smallest integer with `amax ≤ 448 · 2^c` (`c = 0` for an all-zero tile).
3. Write the elements `v = RNE_E4M3(w · 2^-c)` with `sat`. Because of step 2 it never saturates.
4. Write the column scale `cs[t][r] = 2^c` (× the FP32 tensor scale for NVFP4, an exact FP32 product).

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

## N-3. MXU arithmetic

**Operand modes:**
- `NORMAL` with E4M3 × E4M3, or BF16 × BF16. Both operands share the mode.
- E5M2 and FP16 tensors are converted to BF16 by the DMA; the MXU has no E5M2 or FP16 datapath.
- `BF16X3` (below).

**FP8 PE.**
- Each PE holds 4 stationary E4M3 weights.
- Per cycle it forms the 4 products exactly. Each product is a multiple of 2^-18 below 2^18.
- It adds them exactly in a 39-bit fixed-point adder (36 bits of range, 2 carry bits and the sign).
- It rounds that sum once to FP32 (RNE) and adds it to the incoming partial sum with an FP32 adder (RNE, FTZ).
- So there are two roundings per PE.

**BF16 PE.**
- At half rate the PE holds 2 stationary BF16 weights and forms 2 exact products (16 significant bits each).
- It rounds their exact sum once to FP32, then does the same FP32 add as in FP8 mode.
- A BF16 stationary tile is K = 128 × N = 64. A rows (128 BF16 = 256 B) stream one per cycle, so the array does 8,192 BF16 MAC/cycle,
  half the FP8 rate (arch.md). This corrects microarch.md §2.2, which had each BF16 row taking 2 cycles (quarter rate).
- Each PE needs two BF16 multipliers next to its four FP8 ones. Phase 6 synthesis costs this, since sharing the FP8 multipliers would
  give quarter rate.

**Order** (bit-reproducible):
1. Per K tile, the partial sum enters the top PE row as +0.
2. It passes PE rows 0..63 in order. PE row i holds K elements `4i .. 4i+3` (FP8) or `2i, 2i+1` (BF16) of the tile.
3. The tile's partial P_t (FP32) leaves the bottom of the array.
4. At the ACC input:
   - `RESCALE`, once before the first tile: `ACC ← RNE(ACC × rowscale[m])`.
   - if `cs_reg` is set: `P_t ← RNE(P_t × cs[t][n])`;
   - `OVERWRITE` on the first tile: `ACC ← P_t`;
   - otherwise: `ACC ← RNE(ACC + P_t)`.
5. K tiles are taken in ascending K, starting at the instruction's K origin.
   - Splitting K differently (across instructions, or across cores followed by a reduction) changes results only at FP32 rounding level.
   - The compiler records the split, so the golden model can follow it.

**`BF16X3`.**
- The operands are split as `A = A_hi + A_lo` and `W = W_hi + W_lo`, where `x_hi = RNE_BF16(x)` and `x_lo = RNE_BF16(x − x_hi)`.
- Per 128-deep K tile there are three passes, `A_lo·W_hi`, `A_hi·W_lo`, `A_hi·W_hi`, accumulated into ACC in that order.
- The result is FP32-class: it drops `A_lo·W_lo` (about 2^-16 of each product) and the rounding of the lo parts.
- When A is exactly BF16 (`A_lo = 0`, as for the hyper-connection stream), the compiler issues two `NORMAL` BF16 passes instead:
  `W_lo` with `ACCUM`, then `W_hi`. The result is identical in 2/3 of the time.

**Accuracy (E4).**
- K = 16384, measured: the specified order is within 2.1 ulp (FP8) and 3.8 ulp (BF16) of the exact result at the largest output.
- A one-product-at-a-time FP32 loop is off by 4.0 and 41 ulp.
- With FP8 operands of normal magnitude the FP32 accumulation is often exact.

**Special values.**
- A NaN operand gives NaN.
- FP32 overflow during accumulation gives ±Inf, and `Inf − Inf` gives NaN, as IEEE.
- E4M3 has no Inf, so Inf can only come from the accumulator.

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
| DMA conversions | canonical in the destination | kept (to BF16/FP32); never produced into E4M3 | FP8/FP4 kept; BF16 kept | kept |
| MXU | propagates | from FP32 overflow only | FP8 kept; BF16 inputs DAZ; results FTZ | `+0` from an all-zero tile |
| VPU / SFU | propagates (IEEE); produced by invalid operations | IEEE | DAZ / FTZ | IEEE |
| Conversions to FP8 / FP4 | canonical NaN; FP4 → block NaN scale | saturates to ±max | kept | kept |
| Write-add (`ADD_F32`, `ADD_BF16`) | propagates | IEEE | FTZ | IEEE |
| `MAX_F32` | propagates (NaN wins) | ordered | DAZ | `max(−0, +0) = +0` |

`FP_STATUS` CSR (per core, sticky, write 1 to clear) is set by any unit of the core, including the DMA conversions:

| Bit | Name | Set when |
|---|---|---|
| 0 | `INVALID` | an operation produced NaN from non-NaN inputs |
| 1 | `OVERFLOW` | an FP32/BF16 result overflowed to Inf |
| 2 | `SATURATE` | a conversion to FP8/FP4 saturated a finite value |
| 3 | `NAN_IN` | an arithmetic unit consumed a NaN |

The CP collects the flags after each kernel in debug runs. Nothing traps (isa.md §8).

## N-6. Write-add and collectives

- `ADD_F32` computes `RNE_FP32(old + new)` per element.
- `ADD_BF16` computes `RNE_BF16(RNE_FP32(old + new))`. That is two roundings, kept so that the target ALU stays an FP32 adder; the
  golden model does the same.
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
- **Decode and other memory-bound GEMMs use the exact path** (`cvt NVFP4_BF16`). Decode is HBM-bound, and the stationary-load port
  still has a 1.65x margin over a core's HBM share with NVFP4 expanded to BF16 (3.31x for FP8).
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
- Reference libraries: `ml_dtypes` agrees with the golden model on FP32 inputs. It rounds float64 inputs twice (through float32), so
  tests compare on FP32 values, which is all the hardware ever converts.
- **MXU, bit-exact** against `mxu_mm` for the same tiling.
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
