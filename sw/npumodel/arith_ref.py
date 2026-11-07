"""Exact reference for the Phase 3 arithmetic blocks (`rtl/arith`, docs/spec/numerics.md N-1..N-5, N-8).

Values are exact dyadic numbers (sign, m, k) = (-1)^sign * m * 2^k with an integer m, so every sum and product is exact and
every rounding is one RNE rounding of the exact value (no float64 double rounding). `numerics.py` stays the golden model
of the formats; `tb/unit/test_arith.py` checks this module against it and against `ml_dtypes`, and
`scripts/gen_arith_vectors.py` turns it into the test vectors of the SystemVerilog testbenches.

Flags are the FP_STATUS bit order: INVALID = 1, OVERFLOW = 2, SATURATE = 4, NAN_IN = 8.
"""
from __future__ import annotations

import math

import numpy as np

from . import numerics as nm

INV, OVF, SAT, NANIN = 1, 2, 4, 8
NAN32 = 0x7FC00000
NAN16 = 0x7FC0

# (eb, mb, kind, sat, ftz): kind 0 IEEE-like, 1 FN (E4M3), 2 NONE (E2M1)
F32 = (8, 23, 0, False, True)
F32_NOFTZ = (8, 23, 0, False, False)
BF16 = (8, 7, 0, False, False)
BF16_FTZ = (8, 7, 0, False, True)
FP16 = (5, 10, 0, False, False)
E4M3 = (4, 3, 1, True, False)
E5M2 = (5, 2, 0, True, False)
E2M1 = (2, 1, 2, True, False)
SPEC = {"BF16": BF16_FTZ, "FP16": FP16, "E4M3": E4M3, "E5M2": E5M2, "E2M1": E2M1}
FMT_ID = {"BF16": 0, "FP16": 1, "E4M3": 2, "E5M2": 3, "E2M1": 4}


# ------------------------------------------------------------------ dyadic numbers
def dy(v) -> tuple[int, int, int]:
    """float -> (sign, m, k)."""
    v = float(v)
    s = 1 if math.copysign(1.0, v) < 0 else 0
    n, d = abs(v).as_integer_ratio()
    return s, n, -(d.bit_length() - 1)


def dy_add(a, b):
    """Exact sum with the IEEE RNE sign rules of a zero result: (-0) + (-0) = -0, anything else that cancels is +0."""
    (sa, ma, ka), (sb, mb, kb) = a, b
    k = min(ka, kb) if (ma and mb) else (ka if ma else kb)
    A = ((ma << (ka - k)) if ma else 0) * (-1 if sa else 1)
    B = ((mb << (kb - k)) if mb else 0) * (-1 if sb else 1)
    S = A + B
    if S == 0:
        return (sa & sb) if (ma == 0 and mb == 0) else 0, 0, 0
    return (1 if S < 0 else 0), abs(S), k


def dy_mul(a, b):
    return a[0] ^ b[0], a[1] * b[1], a[2] + b[2]


def rnd(sign: int, m: int, k: int, spec) -> tuple[int, bool, bool]:
    """Round (-1)^sign * m * 2^k to the format `spec` (RNE). Returns (code, ovf, sat).

    Below the smallest normal the value is rounded on the subnormal grid first; with ftz a result that is still
    subnormal after rounding becomes a signed zero. Overflow: saturate (sat) or Inf / all-ones code."""
    eb, mb, kind, sat, ftz = spec
    bias = (1 << (eb - 1)) - 1
    emin = 1 - bias
    sgn = sign << (eb + mb)
    if m == 0:
        return sgn, False, False
    e = k + m.bit_length() - 1
    sh = (max(e, emin) - mb) - k
    if sh <= 0:
        r = m << -sh
    else:
        q = m >> sh
        rem = m & ((1 << sh) - 1)
        half = 1 << (sh - 1)
        r = q + (1 if (rem > half or (rem == half and (q & 1))) else 0)
    base = (e + bias - 1) if e >= emin else 0
    code = (base << mb) + r
    maxc = ((((1 << eb) - 2) << mb) | ((1 << mb) - 1)) if kind == 0 else ((1 << (eb + mb)) - (2 if kind == 1 else 1))
    if code > maxc:
        if sat:
            return sgn | maxc, False, True
        if kind == 0:
            return sgn | (((1 << eb) - 1) << mb), True, False
        return sgn | ((1 << (eb + mb)) - 1), True, False
    if ftz and code < (1 << mb):
        code = 0
    return sgn | code, False, False


# ------------------------------------------------------------------ unpack
def dec32(x: int, daz: bool = True):
    """FP32 bits -> ('nan'|'inf'|'fin', sign, m, k). daz: subnormals read as zero."""
    s = (x >> 31) & 1
    ef = (x >> 23) & 0xFF
    fr = x & 0x7FFFFF
    if ef == 0xFF:
        return ("nan" if fr else "inf"), s, 0, 0
    if ef == 0:
        return "fin", s, (0 if daz else fr), -149
    return "fin", s, (1 << 23) | fr, ef - 150


def dec_bf16(x: int, daz: bool = True):
    s = (x >> 15) & 1
    ef = (x >> 7) & 0xFF
    fr = x & 0x7F
    if ef == 0xFF:
        return ("nan" if fr else "inf"), s, 0, 0
    if ef == 0:
        return "fin", s, (0 if daz else fr), -133
    return "fin", s, (128 | fr), ef - 134


_E4 = nm.decode_table(nm.E4M3)


def dec_e4m3(x: int):
    v = _E4[x]
    if np.isnan(v):
        return "nan", (x >> 7) & 1, 0, 0
    s, m, k = dy(v)
    return "fin", s, m, k


def inf32(s):
    return (s << 31) | 0x7F800000


# ------------------------------------------------------------------ FP32 add / mul
def fp32_add(a: int, b: int):
    A, B = dec32(a), dec32(b)
    if A[0] == "nan" or B[0] == "nan":
        return NAN32, NANIN
    if A[0] == "inf" and B[0] == "inf" and A[1] != B[1]:
        return NAN32, INV
    if A[0] == "inf":
        return inf32(A[1]), 0
    if B[0] == "inf":
        return inf32(B[1]), 0
    s, m, k = dy_add(A[1:], B[1:])
    code, ovf, _ = rnd(s, m, k, F32)
    return code, (OVF if ovf else 0)


def fp32_mul(a: int, b: int):
    A, B = dec32(a), dec32(b)
    s = A[1] ^ B[1]
    if A[0] == "nan" or B[0] == "nan":
        return NAN32, NANIN
    az = A[0] == "fin" and A[2] == 0
    bz = B[0] == "fin" and B[2] == 0
    if (A[0] == "inf" and bz) or (B[0] == "inf" and az):
        return NAN32, INV
    if A[0] == "inf" or B[0] == "inf":
        return inf32(s), 0
    code, ovf, _ = rnd(s, A[2] * B[2], A[3] + B[3], F32)
    return code, (OVF if ovf else 0)


def fp32_max(a: int, b: int):
    """MAX_F32: NaN wins, DAZ, max(-0, +0) = +0."""
    A, B = dec32(a), dec32(b)
    if A[0] == "nan" or B[0] == "nan":
        return NAN32, NANIN

    def key(x):
        mag = x & 0x7FFFFFFF
        if (mag >> 23) == 0:
            mag = 0
        return (x >> 31) & 1, mag

    (sa, ma), (sb, mb) = key(a), key(b)
    ka = -ma if sa else ma
    kb = -mb if sb else mb
    if ma == 0 and mb == 0:
        s = sa & sb
        return s << 31, 0
    if ka >= kb:
        return (sa << 31) | ma, 0
    return (sb << 31) | mb, 0


def acc_update(acc, part, rowscale, cs, rescale, use_cs, overwrite):
    fl = 0
    acc_r = acc
    if rescale:
        acc_r, f = fp32_mul(acc, rowscale)
        fl |= f
    p = part
    if use_cs:
        p, f = fp32_mul(part, cs)
        fl |= f
    if overwrite:
        return p, fl
    y, f = fp32_add(acc_r, p)
    return y, fl | f


# ------------------------------------------------------------------ PE dot products
def fp8_prod(a: int, b: int):
    """Exact E4M3 product: ('nan', ...) or ('fin', sign, m, k)."""
    A, B = dec_e4m3(a), dec_e4m3(b)
    if A[0] == "nan" or B[0] == "nan":
        return "nan", (a ^ b) >> 7 & 1, 0, 0
    return ("fin",) + dy_mul(A[1:], B[1:])


def fp8_dot4(a4, w4):
    """Four E4M3 codes each -> (FP32 bits, flags). One rounding of the exact sum."""
    prods = [fp8_prod(a, w) for a, w in zip(a4, w4)]
    if any(p[0] == "nan" for p in prods):
        return NAN32, NANIN
    acc = (1, 0, 0)                                    # start as -0 so that all(-0) stays -0
    for p in prods:
        acc = dy_add(acc, p[1:])
    code, ovf, _ = rnd(acc[0], acc[1], acc[2], F32)
    return code, 0


def bf16_dot2(a2, w2):
    """Two BF16 codes each (DAZ) -> (FP32 bits, flags). One rounding of the exact sum of two exact products."""
    P = []
    in_nan = False
    for a, w in zip(a2, w2):
        A, B = dec_bf16(a), dec_bf16(w)
        s = A[1] ^ B[1]
        if A[0] == "nan" or B[0] == "nan":
            in_nan = True
            P.append(("nan", s, 0, 0))
            continue
        az = A[0] == "fin" and A[2] == 0
        bz = B[0] == "fin" and B[2] == 0
        if (A[0] == "inf" and bz) or (B[0] == "inf" and az):
            P.append(("nan", s, 0, 0))
        elif A[0] == "inf" or B[0] == "inf":
            P.append(("inf", s, 0, 0))
        else:
            P.append(("fin", s, A[2] * B[2], A[3] + B[3]))
    nan_in = NANIN if in_nan else 0
    if any(p[0] == "nan" for p in P) or (P[0][0] == "inf" and P[1][0] == "inf" and P[0][1] != P[1][1]):
        return NAN32, (nan_in if in_nan else INV)
    for p in P:
        if p[0] == "inf":
            return inf32(p[1]), 0
    s, m, k = dy_add(P[0][1:], P[1][1:])
    code, ovf, _ = rnd(s, m, k, F32)
    return code, (OVF if ovf else 0)


# ------------------------------------------------------------------ conversions
def cvt_fp32_fp(x: int, fmt: str):
    """FP32 bits -> narrow format (cvt_fp32_fp). Returns (code, flags)."""
    spec = SPEC[fmt]
    eb, mb, kind, sat, ftz = spec
    kind_, s, m, k = dec32(x, daz=False)
    if kind_ == "nan":
        return {"BF16": 0x7FC0, "FP16": 0x7E00, "E4M3": 0x7F, "E5M2": 0x7E, "E2M1": 0}[fmt], NANIN
    if kind_ == "inf":
        sgn = s << (eb + mb)
        if kind == 0 and not sat:
            return sgn | (((1 << eb) - 1) << mb), 0
        maxc = ((((1 << eb) - 2) << mb) | ((1 << mb) - 1)) if kind == 0 else ((1 << (eb + mb)) - (2 if kind == 1 else 1))
        return sgn | maxc, 0
    code, ovf, sa = rnd(s, m, k, spec)
    return code, (OVF if ovf else 0) | (SAT if sa else 0)


def cvt_fp32_int(x: int, bits: int, signed: bool):
    kind_, s, m, k = dec32(x, daz=False)
    if kind_ == "nan":
        return 0, NANIN
    if kind_ == "inf":
        v = -(1 << 70) if s else (1 << 70)
        fin = False
    else:
        fin = True
        if k >= 0:
            v = m << k
        else:
            q = m >> -k
            rem = m & ((1 << -k) - 1)
            half = 1 << (-k - 1)
            v = q + (1 if (rem > half or (rem == half and (q & 1))) else 0)
        if s:
            v = -v
    lo, hi = ((-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1))
    c = min(max(v, lo), hi)
    return c & ((1 << bits) - 1), (SAT if (fin and c != v) else 0)


def dec_fmt(code: int, fmt: str):
    """Narrow format code -> ('nan'|'inf'|'fin', sign, m, k), exact (subnormals kept)."""
    eb, mb, kind, _, _ = SPEC[fmt]
    s = (code >> (eb + mb)) & 1
    ef = (code >> mb) & ((1 << eb) - 1)
    mf = code & ((1 << mb) - 1)
    bias = (1 << (eb - 1)) - 1
    if kind == 0 and ef == (1 << eb) - 1:
        return ("nan" if mf else "inf"), s, 0, 0
    if kind == 1 and ef == (1 << eb) - 1 and mf == (1 << mb) - 1:
        return "nan", s, 0, 0
    if ef == 0:
        return "fin", s, mf, 1 - bias - mb
    return "fin", s, (1 << mb) | mf, ef - bias - mb


def cvt_dec(code: int, fmt: str, to_bf16: bool):
    kind_, s, m, k = dec_fmt(code, fmt)
    if kind_ == "nan":
        return (NAN16 if to_bf16 else NAN32), NANIN
    if kind_ == "inf":
        return ((s << 15) | 0x7F80) if to_bf16 else inf32(s), 0
    c, _, _ = rnd(s, m, k, BF16 if to_bf16 else F32_NOFTZ)
    return c, 0


# ------------------------------------------------------------------ MX / fold
def mx_exact(el: int, sc: int, elem: int, scale: int):
    """(sign, nan, zero, sig, elsb): element x block scale. elem 0 E2M1, 1 E4M3; scale 0 E8M0, 1 E4M3."""
    if elem == 0:
        e_s = (el >> 3) & 1
        ef = (el >> 1) & 3
        se, ee = (el & 1, -1) if ef == 0 else ((2 | (el & 1)), ef - 2)
        e_nan, e_z = False, (el & 7) == 0
    else:
        e_s = (el >> 7) & 1
        ef = (el >> 3) & 15
        se, ee = ((el & 7), -9) if ef == 0 else ((8 | (el & 7)), ef - 10)
        e_nan, e_z = (el & 0x7F) == 0x7F, (el & 0x7F) == 0
    if scale == 0:
        s_s, ss, es, s_nan, s_z = 0, 1, sc - 127, sc == 0xFF, False
    else:
        s_s = (sc >> 7) & 1
        ef = (sc >> 3) & 15
        ss, es = ((sc & 7), -9) if ef == 0 else ((8 | (sc & 7)), ef - 10)
        s_nan, s_z = (sc & 0x7F) == 0x7F, (sc & 0x7F) == 0
    nan = e_nan or s_nan
    zero = e_z or s_z
    sig = 0 if (nan or zero) else se * ss
    return e_s ^ s_s, nan, zero, sig, ee + es


def mx_dequant_bf16(el: int, sc: int, elem: int, scale: int):
    s, nan, zero, sig, elsb = mx_exact(el, sc, elem, scale)
    if nan:
        return NAN16, NANIN
    code, ovf, _ = rnd(s, sig, elsb, BF16)
    return code, (OVF if ovf else 0)


def fold_key(el: int, sc: int, elem: int, scale: int) -> int:
    s, nan, zero, sig, elsb = mx_exact(el, sc, elem, scale)
    if sig == 0:
        return 0
    lz = 8 - sig.bit_length()
    e_msb = elsb + 7 - lz
    return ((e_msb + 256) << 8) | ((sig << lz) & 0xFF)


def fold_c(key: int) -> int:
    if key == 0:
        return 0
    e = (key >> 8) - 256
    return e - 7 if (key & 0xFF) > 0xE0 else e - 8


def fold_cs(c: int) -> int:
    code, _, _ = rnd(0, 1, c, F32_NOFTZ)
    return code


def fold_quant(el: int, sc: int, c: int, elem: int, scale: int):
    s, nan, zero, sig, elsb = mx_exact(el, sc, elem, scale)
    if nan:
        return 0x7F, NANIN
    code, _, sa = rnd(s, sig, elsb - c, E4M3)
    return code, (SAT if sa else 0)
