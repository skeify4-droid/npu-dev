"""Bit-accurate numerics reference for the NPU (docs/spec/numerics.md).

This is the golden model for the arithmetic units (Phase 3 exhaustive tests), the DMA conversions and the
MXU accumulation order. Values are carried as float64 arrays that lie exactly on the target format's grid;
`encode` / `decode` map them to and from bit patterns.

Rules implemented here (numerics.md N-1..N-5):
- every rounding is round-to-nearest-even (RNE);
- conversions to FP8 / FP4 saturate (PTX .satfinite: |x| > max -> max, +-Inf -> +-max, NaN -> NaN);
- conversions to BF16 / FP16 / FP32 are IEEE (overflow -> Inf);
- FP8 / FP4 subnormals are honoured; BF16 / FP32 arithmetic results are flushed to zero when subnormal (`ftz`).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Fmt:
    name: str
    ebits: int
    mbits: int
    bias: int
    inf: bool          # has an Inf encoding
    nan: str           # "ieee" (all-ones exponent, mantissa != 0), "fn" (only S.1..1.1..1), "none"
    sat: bool          # conversions into this format saturate

    @property
    def emin(self) -> int:                      # exponent of the smallest normal
        return 1 - self.bias

    @property
    def max(self) -> float:
        emax_field = (1 << self.ebits) - 1 - (1 if self.nan == "ieee" else 0)
        mant = (1 << self.mbits) - 1 - (1 if self.nan == "fn" else 0)
        return float((1 + mant / (1 << self.mbits)) * 2.0 ** (emax_field - self.bias))

    @property
    def min_sub(self) -> float:
        return float(2.0 ** (self.emin - self.mbits))

    @property
    def bits(self) -> int:
        return 1 + self.ebits + self.mbits


E4M3 = Fmt("E4M3", 4, 3, 7, False, "fn", True)
E5M2 = Fmt("E5M2", 5, 2, 15, True, "ieee", True)
E2M1 = Fmt("E2M1", 2, 1, 1, False, "none", True)
BF16 = Fmt("BF16", 8, 7, 127, True, "ieee", False)
FP16 = Fmt("FP16", 5, 10, 15, True, "ieee", False)
FP32 = Fmt("FP32", 8, 23, 127, True, "ieee", False)
FORMATS = {f.name: f for f in (E4M3, E5M2, E2M1, BF16, FP16, FP32)}

E8M0_BIAS = 127            # MX shared scale: value 2^(code - 127), code 0xFF = NaN
FP32_MIN_NORMAL = 2.0 ** -126


def round_to(x, f: Fmt, sat: bool | None = None, ftz: bool = False) -> np.ndarray:
    """Round float64 values to format f with RNE. Returns float64 values on f's grid."""
    sat = f.sat if sat is None else sat
    x = np.asarray(x, dtype=np.float64)
    a = np.abs(x)
    _, e2 = np.frexp(a)                                   # a = m * 2^e2, m in [0.5, 1)
    e = np.maximum(e2.astype(np.int64) - 1, f.emin)        # floor(log2 a), clamped to the subnormal exponent
    q = np.ldexp(1.0, (e - f.mbits).astype(np.int64))
    with np.errstate(invalid="ignore", over="ignore"):
        r = np.round(a / q) * q                           # np.round is half-to-even; a / q is exact
    big = r > f.max
    if sat:
        r = np.where(big | np.isinf(a), f.max, r)
    elif f.inf:
        r = np.where(big, np.inf, r)
    else:
        r = np.where(big, np.nan, r)
    if ftz:
        r = np.where(r < 2.0 ** f.emin, 0.0, r)
    r = np.where(np.isnan(x), np.nan, r)
    return np.copysign(r, x)


def f32(x, ftz: bool = True) -> np.ndarray:
    """FP32 rounding of an exact (float64) value, as the FP32 datapaths do (RNE, FTZ)."""
    return round_to(x, FP32, ftz=ftz)


def bf16(x, ftz: bool = False) -> np.ndarray:
    return round_to(x, BF16, ftz=ftz)


def cvt_fp32_bf16(x) -> np.ndarray:
    """DMA `FP32_BF16` (N-2): RNE, IEEE overflow to Inf, subnormals kept (RNE onto the BF16 subnormal grid 2^-133; an FP32
    subnormal input is converted, not read as zero)."""
    return round_to(x, BF16, ftz=False)


# ------------------------------------------------------------------ bit patterns
def decode_table(f: Fmt) -> np.ndarray:
    """Value of every code of an 8-bit-or-smaller format (index = code)."""
    n = 1 << f.bits
    codes = np.arange(n)
    s = (codes >> (f.ebits + f.mbits)) & 1
    ef = (codes >> f.mbits) & ((1 << f.ebits) - 1)
    mf = codes & ((1 << f.mbits) - 1)
    val = np.where(ef == 0, mf * 2.0 ** (f.emin - f.mbits),
                   (1 + mf / 2.0 ** f.mbits) * np.exp2(ef.astype(np.float64) - f.bias))
    allone = ef == (1 << f.ebits) - 1
    if f.nan == "ieee":
        val = np.where(allone & (mf == 0), np.inf, val)
        val = np.where(allone & (mf != 0), np.nan, val)
    elif f.nan == "fn":
        val = np.where(allone & (mf == (1 << f.mbits) - 1), np.nan, val)
    return np.where(s == 1, -val, val)


def decode(codes, f: Fmt) -> np.ndarray:
    codes = np.asarray(codes)
    if f is FP32:
        return codes.astype(np.uint32).view(np.float32).astype(np.float64)
    if f is BF16:
        return (codes.astype(np.uint32) << 16).view(np.float32).astype(np.float64)
    if f is FP16:
        return codes.astype(np.uint16).view(np.float16).astype(np.float64)
    return decode_table(f)[codes.astype(np.int64)]


def canonical_nan(f: Fmt) -> int:
    return {"E4M3": 0x7F, "E5M2": 0x7E, "BF16": 0x7FC0, "FP16": 0x7E00, "FP32": 0x7FC00000}[f.name]


def encode(vals, f: Fmt) -> np.ndarray:
    """Bit pattern of values already on f's grid (use round_to first). NaN -> canonical NaN."""
    v = np.asarray(vals, dtype=np.float64)
    if f is FP32:
        out = v.astype(np.float32).view(np.uint32).astype(np.int64)
    elif f is BF16:
        out = (v.astype(np.float32).view(np.uint32) >> 16).astype(np.int64)
    elif f is FP16:
        out = v.astype(np.float16).view(np.uint16).astype(np.int64)
    else:
        tab = decode_table(f)
        half = 1 << (f.bits - 1)
        pos = tab[:half]
        order = np.argsort(np.where(np.isnan(pos), np.inf, pos), kind="stable")
        mags = pos[order]
        idx = np.searchsorted(mags, np.abs(np.nan_to_num(v, nan=0.0)))
        idx = np.minimum(idx, half - 1)
        out = order[idx].astype(np.int64) | np.where(np.signbit(v), half, 0)
    if f.nan != "none":
        out = np.where(np.isnan(v), canonical_nan(f), out)
    return out


# ------------------------------------------------------------------ block formats
EMAX_ELEM = {"E2M1": 2, "E4M3": 8}          # exponent of the largest normal element (OCP MX v1.0 emax_elem)


def _blocks(x, block):
    x = np.asarray(x, dtype=np.float64)
    assert x.shape[-1] % block == 0
    return x.reshape(*x.shape[:-1], x.shape[-1] // block, block)


def mx_quant(x, elem: Fmt, block: int = 32):
    """OCP MX quantization along the last axis. Returns (elements [.., nb, block], scale exponent [.., nb])."""
    xb = _blocks(x, block)
    bad = ~np.all(np.isfinite(xb), axis=-1)               # NaN or Inf in the block -> NaN scale, zero elements
    amax = np.max(np.abs(np.where(np.isfinite(xb), xb, 0.0)), axis=-1)
    _, e2 = np.frexp(amax)
    X = np.where(amax > 0, e2.astype(np.int64) - 1 - EMAX_ELEM[elem.name], -E8M0_BIAS)
    X = np.clip(X, -E8M0_BIAS, E8M0_BIAS)
    el = round_to(np.where(np.isfinite(xb), xb, 0.0) / np.ldexp(1.0, X)[..., None], elem, sat=True)
    el = np.where(bad[..., None], 0.0, el)
    return el, np.where(bad, E8M0_NAN, X)


E8M0_NAN = 0xFF - E8M0_BIAS          # scale exponent standing for the E8M0 NaN code


def mx_dequant(el, X):
    X = np.asarray(X)
    sc = np.where(X == E8M0_NAN, np.nan, np.ldexp(1.0, np.minimum(X, E8M0_BIAS)))
    return el * sc[..., None]


def nvfp4_tensor_scale(x) -> float:
    """FP32 tensor (global) scale, NVIDIA recipe: amax / (448 * 6)."""
    amax = float(np.max(np.abs(x)))
    return float(np.float32(amax / (E4M3.max * E2M1.max))) if amax > 0 else 1.0


def nvfp4_quant(x, tscale: float | None = None, block: int = 16):
    """NVFP4: E2M1 elements, E4M3 scale per 16, FP32 tensor scale. VQUANT arithmetic is FP32.

    Returns (elements [.., nb, 16], block scales [.., nb] (E4M3 values), tensor scale)."""
    T = nvfp4_tensor_scale(x) if tscale is None else tscale
    xb = _blocks(x, block).astype(np.float32)
    bad = ~np.all(np.isfinite(xb), axis=-1)               # NaN or Inf in the block -> NaN scale, zero elements
    xb = np.where(bad[..., None], np.float32(0.0), xb)
    amax = np.max(np.abs(xb), axis=-1)
    s = round_to(np.float32(amax / np.float32(E2M1.max)) / np.float32(T), E4M3, sat=True)
    denom = np.float32(s) * np.float32(T)
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = np.where(denom > 0, np.float32(1.0) / denom.astype(np.float32), np.float32(0.0)).astype(np.float32)
    el = round_to((xb * inv[..., None]).astype(np.float32), E2M1, sat=True)
    return el, np.where(bad, np.nan, s), T


def nvfp4_dequant(el, s, T):
    return el * s[..., None] * T


def e4m3_row_quant(x):
    """VQUANT E4M3_ROW: per-row FP32 scale amax / 448, elements RNE + saturate. Returns (elements, scale [rows, 1])."""
    x = np.asarray(x, dtype=np.float32)
    amax = np.max(np.abs(x), axis=-1, keepdims=True)
    sc = np.where(amax > 0, (amax / np.float32(E4M3.max)).astype(np.float32), np.float32(1.0))
    inv = (np.float32(1.0) / sc).astype(np.float32)
    return round_to((x * inv).astype(np.float32), E4M3), sc.astype(np.float64)


# ------------------------------------------------------------------ DMA fold (cvt *_E4M3), numerics.md N-2
KTILE_FP8 = 256
KTILE_BF16 = 128


def fold_exponent(amax) -> np.ndarray:
    """Smallest c with amax <= 448 * 2^c (c = 0 for an all-zero tile)."""
    amax = np.asarray(amax, dtype=np.float64)
    m, e2 = np.frexp(np.where(amax > 0, amax / E4M3.max, 1.0))
    c = e2.astype(np.int64) - (m == 0.5)                   # exact powers of two need one less
    return np.where(amax > 0, c, 0)


def fold_e4m3(w_exact, ktile: int = KTILE_FP8):
    """Fold block-scaled weights (exact values, tensor scale excluded) into E4M3 + one FP32 power-of-two column
    scale per (row, K tile). w_exact: [N, K] (HF [out, in] layout). Returns (E4M3 values [N, K], cs [N, K/ktile])."""
    t = _blocks(w_exact, ktile)
    a = np.abs(t)
    c = fold_exponent(np.max(np.where(np.isnan(a), 0.0, a), axis=-1))     # NaN elements do not take part in amax (N-2)
    v = round_to(t / np.ldexp(1.0, c)[..., None], E4M3, sat=True)             # and stay NaN (code 0x7F)
    return v.reshape(np.shape(w_exact)), np.ldexp(1.0, c)


def unfold(v, cs, ktile: int = KTILE_FP8):
    return (_blocks(v, ktile) * cs[..., None]).reshape(np.shape(v))


# ------------------------------------------------------------------ MXU, numerics.md N-3
def mxu_mm(A, W, mode: str = "FP8", cs=None, acc=None, rowscale=None):
    """ACC (+)= A x W with the PE accumulation order of the weight-stationary array.

    A: [M, K] on the E4M3 (mode FP8) or BF16 grid. W: [K, N] on the same grid. cs: FP32 column scales
    [K tiles, N] or None. acc: existing ACC [M, N] (FP32 values) or None (OVERWRITE). rowscale: [M] (RESCALE).
    Per K tile: the partial sum enters the top PE as +0 and flows down 64 PE rows; each PE adds the exact sum of
    its g products (g = 4 FP8 or 2 BF16), rounded once to FP32, to the incoming partial (FP32 add, RNE).
    The tile partial is multiplied by its column scale (FP32) and added to ACC (FP32). All FP32 results FTZ.
    """
    A = np.asarray(A, dtype=np.float64)
    W = np.asarray(W, dtype=np.float64)
    M, K = A.shape
    N = W.shape[1]
    g = 4 if mode == "FP8" else 2
    kt = 64 * g
    out = np.zeros((M, N)) if acc is None else np.asarray(acc, dtype=np.float64).copy()
    if rowscale is not None:
        out = f32(out * np.asarray(rowscale, dtype=np.float64)[:, None])
    for t, k0 in enumerate(range(0, K, kt)):
        part = np.zeros((M, N))
        for k in range(k0, min(k0 + kt, K), g):
            s = A[:, k:k + g] @ W[k:k + g, :]      # exact in float64 for E4M3 / BF16 products (see tests)
            part = f32(part + f32(s))
        if cs is not None:
            part = f32(part * np.asarray(cs, dtype=np.float64)[t][None, :])
        out = f32(out + part)
    return out


def bf16_split(x):
    """hi + lo BF16 split of FP32 values (BF16X3)."""
    hi = bf16(x)
    lo = bf16(np.asarray(x, dtype=np.float64) - hi)
    return hi, lo


def mxu_mm_bf16x3(A, W):
    """mode BF16X3: per K tile the passes lo*hi, hi*lo, hi*hi accumulate into ACC in that order."""
    Ah, Al = bf16_split(A)
    Wh, Wl = bf16_split(W)
    K = np.shape(A)[1]
    out = np.zeros((np.shape(A)[0], np.shape(W)[1]))
    for k0 in range(0, K, KTILE_BF16):
        sl = slice(k0, k0 + KTILE_BF16)
        for a, w in ((Al, Wh), (Ah, Wl), (Ah, Wh)):
            out = mxu_mm(a[:, sl], w[sl, :], "BF16", acc=out)
    return out
