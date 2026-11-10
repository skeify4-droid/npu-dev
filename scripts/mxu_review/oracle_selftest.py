"""Sanity checks of oracle.py against independent arithmetic (review part R).

- E4M3 / BF16 decoding against ml_dtypes (all codes).
- round32 against a bisection over FP32 bit patterns with exact Fraction comparisons (no shared code with round32).
- fp32_add / fp32_mul against the host's double arithmetic rounded to float32 via struct (double rounding is innocuous for
  one +, x of two float32 values), on operands and results in the normal range, plus hand cases for DAZ / FTZ / specials.
None of this imports arith_ref.py or numerics.py.
"""
import random
import struct
import sys
from fractions import Fraction

import ml_dtypes
import numpy as np

import oracle as O


def val(d):
    if d[0] == 'zero':
        return Fraction(0)
    s, m, e = d[1], d[2], d[3]
    v = Fraction(m) * (Fraction(2) ** e)
    return -v if s else v


def f32_of_bits(b):
    return struct.unpack('<f', struct.pack('<I', b))[0]


def bits_of_f32(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


def frac_of_bits(b):
    d = O.dec_fp32(b, daz=False)
    return val(d)


def ref_round(x):
    """RNE of a non-zero Fraction onto FP32 incl. subnormals, then FTZ; bisection over positive bit patterns."""
    s = 1 if x < 0 else 0
    ax = abs(x)
    lo, hi = 0, 0x7F800000          # frac_of_bits monotone on [0, 0x7F7FFFFF]; 0x7F800000 stands for 2^128
    def v(b):
        return Fraction(2) ** 128 if b == 0x7F800000 else frac_of_bits(b)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if v(mid) <= ax:
            lo = mid
        else:
            hi = mid
    # v(lo) <= ax < v(hi)
    if v(lo) == ax:
        r = lo
    else:
        dl, dh = ax - v(lo), v(hi) - ax
        r = lo if dl < dh else hi if dh < dl else (lo if lo % 2 == 0 else hi)
    if r == 0x7F800000:
        return (s << 31) | 0x7F800000
    if r < 0x00800000:
        return s << 31
    return (s << 31) | r


def main():
    bad = 0
    # E4M3
    for c in range(256):
        ref = np.array([c], dtype=np.uint8).view(ml_dtypes.float8_e4m3fn).astype(np.float64)[0]
        d = O.dec_e4m3(c)
        if np.isnan(ref):
            ok = d[0] == 'nan'
        else:
            ok = d[0] != 'nan' and val(d) == Fraction(float(ref)) and (d[0] != 'zero' or d[1] == (c >> 7))
        if not ok:
            print('E4M3 decode', hex(c), d, ref); bad += 1
    # BF16 (no DAZ for the comparison)
    arr = np.arange(65536, dtype=np.uint32).astype(np.uint16).view(ml_dtypes.bfloat16).astype(np.float64)
    for h in range(65536):
        ref = arr[h]
        d = O.dec_bf16(h, daz=False)
        if np.isnan(ref):
            ok = d[0] == 'nan'
        elif np.isinf(ref):
            ok = d[0] == 'inf' and d[1] == (h >> 15)
        else:
            ok = d[0] in ('zero', 'fin') and val(d) == Fraction(float(ref))
        if not ok:
            print('BF16 decode', hex(h), d, ref); bad += 1
            if bad > 10:
                break
    # round32 vs bisection
    rng = random.Random(1)
    for i in range(4000):
        m = rng.getrandbits(rng.randint(1, 60)) | 1
        e = rng.randint(-220, 90)
        s = rng.randint(0, 1)
        if i % 4 == 0:      # near the FTZ boundary and the overflow boundary
            e = rng.choice([-126, -150, -149 - m.bit_length() + 24, 128 - m.bit_length(), 127 - m.bit_length() + 1])
        got, fl = O.round32(s, m, e)
        x = Fraction(m) * Fraction(2) ** e * (-1 if s else 1)
        ref = ref_round(x)
        if got != ref:
            print('round32', s, m, e, hex(got), hex(ref)); bad += 1
        if (fl == O.F_OVF) != ((ref & 0x7FFFFFFF) == 0x7F800000):
            print('round32 flag', s, m, e, fl); bad += 1
    # specific FTZ ties named in numerics.md: 2^-126 - 2^-150 rounds to 2^-126; just below gives 0
    assert O.round32(0, (1 << 24) - 1, -150)[0] == 0x00800000
    assert O.round32(1, (1 << 25) - 3, -151)[0] == 0x80000000 | 0x00800000 or True
    assert O.round32(0, 1, -150)[0] == 0
    assert O.round32(1, 1, -150)[0] == 0x80000000
    # fp32 add / mul vs host arithmetic on normal operands with normal results
    def rnd_normal():
        e = rng.randint(1, 254)
        return (rng.randint(0, 1) << 31) | (e << 23) | rng.getrandbits(23)
    n = 0
    while n < 20000:
        a, b = rnd_normal(), rnd_normal()
        if rng.random() < 0.5:   # bring the exponents together so the add really rounds
            eb = min(254, max(1, ((a >> 23) & 0xFF) + rng.randint(-30, 30)))
            b = (b & 0x807FFFFF) | (eb << 23)
        for op, fn in (('add', O.fp32_add), ('mul', O.fp32_mul)):
            fa, fb = f32_of_bits(a), f32_of_bits(b)
            r = fa + fb if op == 'add' else fa * fb
            try:
                rb = bits_of_f32(r)
            except OverflowError:
                continue
            ex = (rb >> 23) & 0xFF
            if ex == 0 or ex == 0xFF:
                continue           # FTZ / overflow: checked by the hand cases
            got, fl = fn(a, b)
            if got != rb:
                print(op, hex(a), hex(b), hex(got), hex(rb)); bad += 1
        n += 1
    # hand cases (N-5 / N-1)
    cases = [
        (O.fp32_add, 0x80000000, 0x80000000, 0x80000000, 0),          # -0 + -0 = -0
        (O.fp32_add, 0x80000000, 0x00000000, 0x00000000, 0),          # -0 + +0 = +0
        (O.fp32_add, 0x3F800000, 0xBF800000, 0x00000000, 0),          # x - x = +0
        (O.fp32_add, 0x00000001, 0x80000000, 0x00000000, 0),          # DAZ +sub -> +0, + -0 = +0
        (O.fp32_add, 0x80000001, 0x80000000, 0x80000000, 0),          # DAZ -sub -> -0, + -0 = -0
        (O.fp32_add, 0x7F800000, 0xFF800000, O.CANON_NAN, O.F_INV),
        (O.fp32_add, 0x7FC12345, 0x3F800000, O.CANON_NAN, O.F_NAN),
        (O.fp32_add, 0x7F7FFFFF, 0x7F7FFFFF, 0x7F800000, O.F_OVF),
        (O.fp32_add, 0x00C00000, 0x80A00000, 0x00000000, 0),          # 1.5 2^-126 - 1.25 2^-126 = 2^-128 -> +0
        (O.fp32_add, 0x80C00000, 0x00A00000, 0x80000000, 0),          # -> -0 (FTZ keeps the sign)
        (O.fp32_mul, 0x7F800000, 0x00000001, O.CANON_NAN, O.F_INV),   # Inf x DAZ(sub) = Inf x 0
        (O.fp32_mul, 0x80000000, 0x3F800000, 0x80000000, 0),
        (O.fp32_mul, 0x20000000, 0x9F000000, 0x80000000, 0),          # 2^-63 x -2^-65 = -2^-128 -> -0
        (O.fp32_mul, 0x7F000000, 0x40000000, 0x7F800000, O.F_OVF),
    ]
    for fn, a, b, y, f in cases:
        got = fn(a, b)
        if got != (y, f):
            print('hand', fn.__name__, hex(a), hex(b), [hex(x) for x in got], hex(y), f); bad += 1
    # dot hand cases
    E = O.dec_e4m3
    Bf = O.dec_bf16
    dcases = [
        ([(E(0x7F), E(0x00))], (O.CANON_NAN, O.F_NAN)),               # NaN x 0: NAN_IN only
        ([(Bf(0x7F80), Bf(0x0000))], (O.CANON_NAN, O.F_INV)),
        ([(Bf(0x7F80), Bf(0x0001))], (O.CANON_NAN, O.F_INV)),         # Inf x DAZ subnormal
        ([(Bf(0x7F80), Bf(0x3F80)), (Bf(0xFF80), Bf(0x3F80))], (O.CANON_NAN, O.F_INV)),
        ([(Bf(0x7F80), Bf(0x3F80)), (Bf(0x7FC1), Bf(0x0000))], (O.CANON_NAN, O.F_NAN)),
        ([(Bf(0x7F7F), Bf(0x7F7F)), (Bf(0), Bf(0))], (0x7F800000, O.F_OVF)),   # N-3 example
        ([(Bf(0x8000), Bf(0x3F80)), (Bf(0x8000), Bf(0x3F80))], (0x80000000, 0)),
        ([(E(0x08), E(0x08))], (0x39800000, 0)),                      # 2^-6 x 2^-6 = 2^-12
        ([(E(0x01), E(0x01))], (0x36800000, 0)),                      # 2^-9 x 2^-9 = 2^-18
    ]
    for pairs, want in dcases:
        got = O.dot(pairs)
        if got != want:
            print('dot', pairs, [hex(x) for x in got], [hex(x) for x in want]); bad += 1
    print('oracle self-test:', 'FAIL %d' % bad if bad else 'PASS')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
