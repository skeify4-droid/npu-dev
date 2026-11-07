"""Independent oracle for the rtl/arith review, written from docs/spec/numerics.md (N-1..N-6), not from arith_ref.py.

Values are (sign, Fraction magnitude). Interpretations where numerics.md is silent are marked INTERP.
Flags: INVALID 1, OVERFLOW 2, SATURATE 4, NAN_IN 8.
"""
from fractions import Fraction as F

INV, OVF, SAT, NANIN = 1, 2, 4, 8


class Fmt:
    def __init__(s, name, eb, mb, bias, kind, maxv, sat):
        s.name, s.eb, s.mb, s.bias, s.kind, s.max, s.sat = name, eb, mb, bias, kind, F(maxv), sat
        s.emin = 1 - bias


# N-1 table
E4M3 = Fmt("E4M3", 4, 3, 7, "fn", 448, True)
E5M2 = Fmt("E5M2", 5, 2, 15, "ieee", 57344, True)
E2M1 = Fmt("E2M1", 2, 1, 1, "none", 6, True)
BF16 = Fmt("BF16", 8, 7, 127, "ieee", F(2) ** 127 * (2 - F(1, 128)), False)
FP16 = Fmt("FP16", 5, 10, 15, "ieee", 65504, False)
FP32 = Fmt("FP32", 8, 23, 127, "ieee", F(2) ** 127 * (2 - F(1, 2 ** 23)), False)
CANON = {"E4M3": 0x7F, "E5M2": 0x7E, "BF16": 0x7FC0, "FP16": 0x7E00, "FP32": 0x7FC00000}


def ilog2(x: F) -> int:
    """floor(log2 x), x > 0, exact."""
    e = x.numerator.bit_length() - x.denominator.bit_length()
    if F(2) ** e > x:
        e -= 1
    if F(2) ** (e + 1) <= x:
        e += 1
    return e


def decode(code, f: Fmt):
    """-> ('nan',s) | ('inf',s) | ('fin', s, Fraction)"""
    s = (code >> (f.eb + f.mb)) & 1
    ef = (code >> f.mb) & ((1 << f.eb) - 1)
    mf = code & ((1 << f.mb) - 1)
    allone = ef == (1 << f.eb) - 1
    if f.kind == "ieee" and allone:
        return ("nan", s) if mf else ("inf", s)
    if f.kind == "fn" and allone and mf == (1 << f.mb) - 1:
        return ("nan", s)
    if ef == 0:
        v = F(mf, 1 << f.mb) * F(2) ** f.emin
    else:
        v = (1 + F(mf, 1 << f.mb)) * F(2) ** (ef - f.bias)
    return ("fin", s, v)


def encode_val(s, v: F, f: Fmt):
    """v on f's grid, finite, <= max."""
    if v == 0:
        return s << (f.eb + f.mb)
    if v < F(2) ** f.emin:
        q = v / F(2) ** (f.emin - f.mb)
        assert q.denominator == 1
        return (s << (f.eb + f.mb)) | int(q)
    e = ilog2(v)
    mant = (v / F(2) ** e - 1) * (1 << f.mb)
    assert mant.denominator == 1
    return (s << (f.eb + f.mb)) | ((e + f.bias) << f.mb) | int(mant)


def rne(s, v: F, f: Fmt, sat=None, ftz=False):
    """RNE of the exact value (-1)^s v to f. -> (code, ovf, satf). Subnormal grid first; ftz flushes a result still below
    2^emin after rounding (numerics.round_to(ftz=True) semantics = arith.md decision 3)."""
    sat = f.sat if sat is None else sat
    if v == 0:
        return s << (f.eb + f.mb), False, False
    e = max(ilog2(v), f.emin)
    q = F(2) ** (e - f.mb)
    n = v / q
    r = n.numerator // n.denominator
    fr = n - r
    if fr > F(1, 2) or (fr == F(1, 2) and r % 2 == 1):
        r += 1
    val = r * q
    if val > f.max:
        if sat:
            return encode_val(s, f.max, f), False, True
        if f.kind == "ieee":
            return (s << (f.eb + f.mb)) | (((1 << f.eb) - 1) << f.mb), True, False
        return (s << (f.eb + f.mb)) | ((1 << (f.eb + f.mb)) - 1), True, False
    if ftz and val < F(2) ** f.emin:
        val = F(0)
    return encode_val(s, val, f), False, False


# ------------------------------------------------------------------ FP32 arithmetic (DAZ / FTZ)
def d32(x):
    """FP32 bits with DAZ -> decode tuple"""
    if ((x >> 23) & 0xFF) == 0:
        return ("fin", x >> 31, F(0))
    return decode(x, FP32)


def add_signed(a, b):
    """exact sum of (s, v) pairs with IEEE RNE zero-sign rules"""
    (sa, va), (sb, vb) = a, b
    t = (-va if sa else va) + (-vb if sb else vb)
    if t == 0:
        if va == 0 and vb == 0:
            return (sa & sb, F(0))
        return (0, F(0))
    return (1 if t < 0 else 0, abs(t))


def fp32_add(a, b):
    A, B = d32(a), d32(b)
    if A[0] == "nan" or B[0] == "nan":
        return CANON["FP32"], NANIN
    if A[0] == "inf" and B[0] == "inf":
        if A[1] != B[1]:
            return CANON["FP32"], INV
        return (A[1] << 31) | 0x7F800000, 0
    if A[0] == "inf":
        return (A[1] << 31) | 0x7F800000, 0
    if B[0] == "inf":
        return (B[1] << 31) | 0x7F800000, 0
    s, v = add_signed((A[1], A[2]), (B[1], B[2]))
    c, o, _ = rne(s, v, FP32, ftz=True)
    return c, OVF if o else 0


def fp32_mul(a, b):
    A, B = d32(a), d32(b)
    s = (a >> 31) ^ (b >> 31)
    if A[0] == "nan" or B[0] == "nan":
        return CANON["FP32"], NANIN
    az = A[0] == "fin" and A[2] == 0
    bz = B[0] == "fin" and B[2] == 0
    if (A[0] == "inf" and bz) or (B[0] == "inf" and az):
        return CANON["FP32"], INV
    if A[0] == "inf" or B[0] == "inf":
        return (s << 31) | 0x7F800000, 0
    c, o, _ = rne(s, A[2] * B[2], FP32, ftz=True)
    return c, OVF if o else 0


def fp32_max(a, b):
    A, B = d32(a), d32(b)
    if A[0] == "nan" or B[0] == "nan":
        return CANON["FP32"], NANIN

    def num(X):
        if X[0] == "inf":
            return F(10) ** 50 * (-1 if X[1] else 1)
        return -X[2] if X[1] else X[2]

    na, nb = num(A), num(B)

    def out(X, x):
        if X[0] == "fin" and X[2] == 0:
            return x & 0x80000000          # DAZ'd zero, sign kept
        return x
    if na == 0 and nb == 0:
        return (0 if (a >> 31) == 0 or (b >> 31) == 0 else 0x80000000), 0
    return (out(A, a), 0) if na >= nb else (out(B, b), 0)


def acc_update(acc, part, rs, cs, rescale, use_cs, overwrite):
    fl = 0
    a = acc
    if rescale:
        a, f = fp32_mul(acc, rs)
        fl |= f
    p = part
    if use_cs:
        p, f = fp32_mul(part, cs)
        fl |= f
    if overwrite:
        return p, fl
    y, f = fp32_add(a, p)
    return y, fl | f


# ------------------------------------------------------------------ PE
def fp8_dot4(a4, w4):
    prods = []
    for a, w in zip(a4, w4):
        A, W = decode(a, E4M3), decode(w, E4M3)
        if A[0] == "nan" or W[0] == "nan":
            return CANON["FP32"], NANIN
        prods.append((A[1] ^ W[1], A[2] * W[2]))
    t = sum(((-v if s else v) for s, v in prods), F(0))
    if t == 0:
        allneg0 = all(s == 1 and v == 0 for s, v in prods)    # INTERP: IEEE sequential sum of -0s
        return (0x80000000 if allneg0 else 0), 0
    c, o, _ = rne(1 if t < 0 else 0, abs(t), FP32, ftz=True)
    return c, 0


def dbf(x):
    if ((x >> 7) & 0xFF) == 0:
        return ("fin", x >> 15 & 1, F(0))
    return decode(x, BF16)


def bf16_dot2(a2, w2):
    P = []
    nan_in = False
    for a, w in zip(a2, w2):
        A, W = dbf(a), dbf(w)
        s = A[1] ^ W[1]
        if A[0] == "nan" or W[0] == "nan":
            nan_in = True
            P.append(("nan", s))
        elif (A[0] == "inf" and W[0] == "fin" and W[2] == 0) or (W[0] == "inf" and A[0] == "fin" and A[2] == 0):
            P.append(("nan", s))
        elif A[0] == "inf" or W[0] == "inf":
            P.append(("inf", s))
        else:
            P.append(("fin", s, A[2] * W[2]))
    if any(p[0] == "nan" for p in P):
        return CANON["FP32"], (NANIN if nan_in else INV)     # INTERP: INVALID only if no NaN input
    if P[0][0] == "inf" and P[1][0] == "inf":
        if P[0][1] != P[1][1]:
            return CANON["FP32"], INV
        return (P[0][1] << 31) | 0x7F800000, 0
    for p in P:
        if p[0] == "inf":
            return (p[1] << 31) | 0x7F800000, 0
    s, v = add_signed((P[0][1], P[0][2]), (P[1][1], P[1][2]))
    c, o, _ = rne(s, v, FP32, ftz=True)
    return c, OVF if o else 0


# ------------------------------------------------------------------ conversions
FMTS = {"BF16": BF16, "FP16": FP16, "E4M3": E4M3, "E5M2": E5M2, "E2M1": E2M1}


def cvt_fp32_fp(x, fmt):
    f = FMTS[fmt]
    X = decode(x, FP32)                                   # no DAZ: a conversion, not arithmetic
    if X[0] == "nan":
        return (CANON[fmt] if fmt != "E2M1" else 0), NANIN  # INTERP: E2M1 has no NaN; block rule upstream
    if X[0] == "inf":
        if f.sat:
            return encode_val(X[1], f.max, f), 0           # spec: Inf -> max; SATURATE only for finite inputs
        return (X[1] << (f.eb + f.mb)) | (((1 << f.eb) - 1) << f.mb), 0
    c, o, sa = rne(X[1], X[2], f, ftz=(fmt == "BF16"))     # N-2: BF16 results below 2^-126 flush
    return c, (OVF if o else 0) | (SAT if sa else 0)


def cvt_fp32_int(x, bits, signed):
    X = decode(x, FP32)
    if X[0] == "nan":
        return 0, NANIN
    lo, hi = ((-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1))
    if X[0] == "inf":
        return (lo if X[1] else hi) & ((1 << bits) - 1), 0
    v = X[2]
    r = v.numerator // v.denominator
    fr = v - r
    if fr > F(1, 2) or (fr == F(1, 2) and r % 2):
        r += 1
    if X[1]:
        r = -r
    c = min(max(r, lo), hi)
    return c & ((1 << bits) - 1), (SAT if c != r else 0)   # INTERP: SATURATE for integer clamps too


def cvt_dec(code, fmt, to_bf16):
    X = decode(code, FMTS[fmt])
    if X[0] == "nan":
        return (0x7FC0 if to_bf16 else 0x7FC00000), NANIN
    if X[0] == "inf":
        return ((X[1] << 15) | 0x7F80) if to_bf16 else ((X[1] << 31) | 0x7F800000), 0
    c, _, _ = rne(X[1], X[2], BF16 if to_bf16 else FP32, sat=False, ftz=False)
    return c, 0


# ------------------------------------------------------------------ MX
def mx_val(el, sc, elem, scale):
    """-> ('nan',) | ('fin', sign, Fraction)"""
    E = decode(el & (0xF if elem == 0 else 0xFF), E2M1 if elem == 0 else E4M3)
    if scale == 0:
        S = ("nan", 0) if sc == 0xFF else ("fin", 0, F(2) ** (sc - 127))
    else:
        S = decode(sc, E4M3)
    if E[0] == "nan" or S[0] == "nan":
        return ("nan",)
    return ("fin", E[1] ^ S[1], E[2] * S[2])


def mx_dequant_bf16(el, sc, elem, scale):
    V = mx_val(el, sc, elem, scale)
    if V[0] == "nan":
        return 0x7FC0, NANIN
    c, o, _ = rne(V[1], V[2], BF16, sat=False, ftz=False)   # N-2: kept on the subnormal grid
    return c, OVF if o else 0


def fold(els, scs, elem, scale):
    """-> (amax Fraction, c, cs bits, [v], [flags])"""
    vals = [mx_val(e, s, elem, scale) for e, s in zip(els, scs)]
    amax = max((v[2] for v in vals if v[0] == "fin"), default=F(0))   # INTERP: NaN does not take part
    if amax == 0:
        c = 0
    else:
        c = -300
        while not (amax <= 448 * F(2) ** c):
            c += 1
    cs, _, _ = rne(0, F(2) ** c, FP32, sat=False, ftz=False)
    vs, fl = [], []
    for v in vals:
        if v[0] == "nan":
            vs.append(0x7F)
            fl.append(NANIN)
            continue
        code, _, sa = rne(v[1], v[2] / F(2) ** c, E4M3, sat=True)
        vs.append(code)
        fl.append(SAT if sa else 0)
    return amax, c, cs, vs, fl
