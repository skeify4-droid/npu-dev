#!/usr/bin/env python3
"""Generate the test vectors of the `rtl/arith` unit testbenches (tb/unit/tb_<block>.sv) into build/arith/*.hex.

The expected values come from `sw/npumodel/arith_ref.py` (exact integer reference, itself checked against numerics.py
and ml_dtypes by tb/unit/test_arith.py). One row per line, a hex word of the fields listed in FIELDS (MSB first).
Deterministic: fixed seeds. Usage: gen_arith_vectors.py [OUTDIR]   (default build/arith)
"""
import os
import sys

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "sw"))
from npumodel import arith_ref as R  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build", "arith")
RNG = np.random.default_rng(20261107)


def pack(*fields):
    """fields: (value, width) MSB first -> hex string."""
    v, w = 0, 0
    for val, wid in fields:
        assert 0 <= val < (1 << wid), (val, wid)
        v = (v << wid) | val
        w += wid
    return format(v, "0%dx" % ((w + 3) // 4))


def write(name, rows):
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name + ".hex"), "w") as f:
        f.write("\n".join(rows) + "\n")
    print(f"{name}: {len(rows)} rows")


def rbits(n, bits):
    return [int(x) for x in RNG.integers(0, 1 << bits, n, dtype=np.uint64)]


# ------------------------------------------------------------------ FP32 operands
SPECIAL32 = [0x00000000, 0x80000000, 0x3F800000, 0xBF800000, 0x40000000, 0x7F7FFFFF, 0xFF7FFFFF, 0x00800000, 0x80800000,
             0x00800001, 0x007FFFFF, 0x807FFFFF, 0x00000001, 0x7F800000, 0xFF800000, 0x7FC00000, 0xFFC00000, 0x7F800001,
             0x3F800001, 0x3F7FFFFF, 0x4B800000, 0x33800000, 0x34000000, 0x7F000000, 0x01000000, 0x00FFFFFF, 0x3FC00000]


def f32_pool(n):
    """Random FP32 bit patterns of several flavours (exponent windows, subnormal, near 2^-126 and the maximum)."""
    out = []
    for _ in range(n):
        c = RNG.integers(0, 6)
        s = int(RNG.integers(0, 2)) << 31
        fr = int(RNG.integers(0, 1 << 23))
        if c == 0:
            out.append(int(RNG.integers(0, 1 << 32)))
        elif c == 1:
            out.append(s | (int(RNG.integers(100, 155)) << 23) | fr)
        elif c == 2:
            out.append(s | (int(RNG.integers(0, 4)) << 23) | fr)                 # subnormal and near 2^-126
        elif c == 3:
            out.append(s | (int(RNG.integers(250, 255)) << 23) | fr)             # near the maximum
        elif c == 4:
            out.append(s | (int(RNG.integers(1, 255)) << 23) | (fr & (0x7 << 20)))   # few mantissa bits
        else:
            out.append(s | (int(RNG.integers(1, 255)) << 23) | (fr & 0xFF))      # low mantissa bits only
    return out


def f32_pairs_add(n):
    rows = [(a, b) for a in SPECIAL32 for b in SPECIAL32]
    pool = f32_pool(2 * n)
    rows += list(zip(pool[:n // 2], pool[n // 2:n]))
    for _ in range(n // 4):                                    # close exponents: alignment shifts 0..40
        a = int(RNG.integers(0, 2)) << 31 | int(RNG.integers(30, 220)) << 23 | int(RNG.integers(0, 1 << 23))
        d = int(RNG.integers(-40, 41))
        eb = min(max(((a >> 23) & 0xFF) + d, 1), 254)
        b = int(RNG.integers(0, 2)) << 31 | eb << 23 | int(RNG.integers(0, 1 << 23))
        rows.append((a, b))
    for _ in range(n // 4):                                    # cancellation: b = -a +- a few ulp, exponent +-1
        a = int(RNG.integers(0, 2)) << 31 | int(RNG.integers(1, 255)) << 23 | int(RNG.integers(0, 1 << 23))
        b = (a ^ 0x80000000) + int(RNG.integers(-9, 10))
        b &= 0xFFFFFFFF
        rows.append((a, b))
    for _ in range(n // 4):                                    # power of two minus a much smaller value (borrow chain, sticky)
        ea = int(RNG.integers(50, 225))
        d = int(RNG.integers(2, 45))
        sa = int(RNG.integers(0, 2))
        a = sa << 31 | ea << 23 | (0 if RNG.integers(0, 4) else int(RNG.integers(0, 4)) << 21)
        b = (sa ^ 1) << 31 | (ea - d) << 23 | int(RNG.integers(0, 1 << 23))
        rows.append((a, b) if RNG.integers(0, 2) else (b, a))
    for _ in range(n // 8):                                    # results around 2^-126
        a = int(RNG.integers(0, 2)) << 31 | int(RNG.integers(0, 3)) << 23 | int(RNG.integers(0, 1 << 23))
        b = int(RNG.integers(0, 2)) << 31 | int(RNG.integers(0, 3)) << 23 | int(RNG.integers(0, 1 << 23))
        rows.append((a, b))
    return rows


def f32_pairs_mul(n):
    rows = [(a, b) for a in SPECIAL32 for b in SPECIAL32]
    pool = f32_pool(2 * n)
    rows += list(zip(pool[:n // 2], pool[n // 2:n]))
    for _ in range(n // 4):                                    # exponent sum near underflow / overflow
        ea = int(RNG.integers(1, 255))
        for target in (128, 381):                              # ea + eb - 127 near the smallest / largest exponent field
            eb = min(max(target - ea + int(RNG.integers(-3, 4)), 1), 254)
            a = int(RNG.integers(0, 2)) << 31 | ea << 23 | int(RNG.integers(0, 1 << 23))
            b = int(RNG.integers(0, 2)) << 31 | eb << 23 | int(RNG.integers(0, 1 << 23))
            rows.append((a, b))
    for _ in range(n // 4):                                    # mantissas that round up at the carry (all ones)
        a = int(RNG.integers(0, 2)) << 31 | int(RNG.integers(60, 190)) << 23 | (0x7FFFFF - int(RNG.integers(0, 64)))
        b = int(RNG.integers(0, 2)) << 31 | int(RNG.integers(60, 190)) << 23 | (0x7FFFFF - int(RNG.integers(0, 64)))
        rows.append((a, b))
    return rows


# ------------------------------------------------------------------ BF16 / E4M3 operands
SPECIAL16 = [0x0000, 0x8000, 0x3F80, 0xBF80, 0x7F7F, 0xFF7F, 0x0080, 0x8080, 0x0001, 0x007F, 0x7F80, 0xFF80, 0x7FC0, 0x7F81,
             0x4000, 0x3F81, 0x0100, 0x7E80]


def bf16_pool(n):
    out = []
    for _ in range(n):
        c = RNG.integers(0, 5)
        s = int(RNG.integers(0, 2)) << 15
        fr = int(RNG.integers(0, 128))
        if c == 0:
            out.append(int(RNG.integers(0, 1 << 16)))
        elif c == 1:
            out.append(s | (int(RNG.integers(110, 145)) << 7) | fr)
        elif c == 2:
            out.append(s | (int(RNG.integers(0, 4)) << 7) | fr)
        elif c == 3:
            out.append(s | (int(RNG.integers(240, 255)) << 7) | fr)
        else:
            out.append(s | (int(RNG.integers(1, 255)) << 7) | fr)
    return out


def bf16_dot_rows(n):
    rows = []
    for a0 in SPECIAL16:
        for w0 in SPECIAL16:
            rows.append(([a0, 0x3F80], [w0, 0x3F80]))
            rows.append(([0x3F80, a0], [0x3F80, w0]))
    small = [0x0000, 0x8000, 0x3F80, 0xBF80, 0x7F80, 0xFF80, 0x7FC0]      # every combination of zero, +-1, +-Inf, NaN
    for a0 in small:
        for a1 in small:
            for w0 in small:
                for w1 in small:
                    rows.append(([a0, a1], [w0, w1]))
    p = bf16_pool(4 * n)
    for i in range(n):
        rows.append((p[4 * i:4 * i + 2], p[4 * i + 2:4 * i + 4]))
    for _ in range(n):                                          # product exponents close: shifts 0..40
        ea0, ew0 = int(RNG.integers(20, 235)), int(RNG.integers(20, 235))
        d = int(RNG.integers(-40, 41))
        ea1 = min(max(ea0 + d, 1), 254)
        s = lambda: int(RNG.integers(0, 2)) << 15  # noqa: E731
        m = lambda: int(RNG.integers(0, 128))  # noqa: E731
        rows.append(([s() | ea0 << 7 | m(), s() | ea1 << 7 | m()], [s() | ew0 << 7 | m(), s() | ew0 << 7 | m()]))
    for _ in range(n):                                          # cancellation: a1 = a0, w1 = -w0 (+- one mantissa step)
        a = int(RNG.integers(0, 2)) << 15 | int(RNG.integers(1, 255)) << 7 | int(RNG.integers(0, 128))
        w = int(RNG.integers(0, 2)) << 15 | int(RNG.integers(1, 255)) << 7 | int(RNG.integers(0, 128))
        w1 = (w ^ 0x8000) ^ int(RNG.integers(0, 4) == 0) * int(RNG.integers(1, 4))
        rows.append(([a, a], [w, w1 & 0xFFFF]))
    bf = lambda sgn, e, fr=0: (sgn << 15) | ((e + 127) << 7) | fr  # noqa: E731  (1.fr * 2^e)
    for _ in range(n):                                          # power-of-two product minus a much smaller product (sticky, guard)
        ea0, ew0 = int(RNG.integers(-20, 21)), int(RNG.integers(-20, 21))
        d = int(RNG.integers(2, 40))
        s0 = int(RNG.integers(0, 2))
        ew1 = int(RNG.integers(-40, 41))
        ea1 = ea0 + ew0 - d - ew1
        rows.append(([bf(s0, ea0), bf(s0 ^ 1, ea1, int(RNG.integers(0, 128)))], [bf(0, ew0), bf(0, ew1, int(RNG.integers(0, 128)))]))
    for _ in range(n // 2):                                     # 2^-126 minus less than half a subnormal ulp: rounds up to 2^-126
        k = int(RNG.integers(0, 12))
        ea1 = int(RNG.integers(-100, -44))
        ew1 = -151 - k - ea1
        rows.append(([bf(0, -63), bf(1, ea1, int(RNG.integers(0, 128)))], [bf(0, -63), bf(0, ew1, int(RNG.integers(0, 128)))]))
    for _ in range(n):                                          # sums near 2^-126 and near the maximum
        target = int(RNG.choice([128, 381]))
        ea = int(RNG.integers(max(1, target - 254), min(254, target - 1) + 1))
        ew = min(max(target - ea + int(RNG.integers(-2, 3)), 1), 254)
        ea1 = min(max(ea + int(RNG.integers(-2, 3)), 1), 254)
        s = lambda: int(RNG.integers(0, 2)) << 15  # noqa: E731
        m = lambda: int(RNG.integers(0, 128))  # noqa: E731
        rows.append(([s() | ea << 7 | m(), s() | ea1 << 7 | m()], [s() | ew << 7 | m(), s() | ew << 7 | m()]))
    return rows


def fp8_dot_rows(n):
    rows = []
    for a in range(256):                                        # every code against a few partners, all lanes
        for w in (0x00, 0x80, 0x38, 0xB8, 0x7E, 0x01, 0x7F):
            rows.append(([a, 0, 0, 0], [w, 0, 0, 0]))
    for _ in range(n):
        a = rbits(4, 8)
        w = rbits(4, 8)
        rows.append((a, w))
    for _ in range(n):                                          # mostly large and small magnitudes, no NaN
        pick = lambda: int(RNG.choice([0x7E, 0xFE, 0x01, 0x81, 0x08, 0x88, 0x38, 0xB8, 0x00, 0x80]))  # noqa: E731
        a = [pick() if RNG.integers(0, 2) else int(RNG.integers(0, 256)) & 0xFE | 0 for _ in range(4)]
        w = [pick() if RNG.integers(0, 2) else int(RNG.integers(0, 256)) & 0xFE for _ in range(4)]
        rows.append((a, w))
    for _ in range(n):                                          # cancellation: pairs with equal magnitude, opposite sign
        a = rbits(2, 8)
        w = rbits(2, 8)
        rows.append((a + a, w + [x ^ 0x80 for x in w]))
    for _ in range(n // 4):                                     # all products zero, mixed signs
        a = [int(RNG.choice([0x00, 0x80])) for _ in range(4)]
        w = [int(RNG.choice([0x00, 0x80, 0x38, 0xB8, 0x7E])) for _ in range(4)]
        rows.append((a, w))
    return rows


def pack4(v, bits):
    r = 0
    for i, x in enumerate(v):
        r |= x << (bits * i)
    return r


def main():
    # ---- fp8_mul: exhaustive 65536 pairs. Expected from ml_dtypes' decode (independent of numerics.py's table).
    import ml_dtypes as md
    tab = np.arange(256, dtype=np.uint8).view(md.float8_e4m3fn).astype(np.float64)
    rows = []
    for a in range(256):
        for b in range(256):
            if np.isnan(tab[a]) or np.isnan(tab[b]):
                rows.append(pack((a, 8), (b, 8), ((a ^ b) >> 7 & 1, 1), (1, 1), (0, 36)))
                continue
            prod = tab[a] * tab[b]                              # exact in float64
            mag = int(abs(prod) * 2.0 ** 18)
            assert mag == abs(prod) * 2.0 ** 18
            rows.append(pack((a, 8), (b, 8), ((a ^ b) >> 7 & 1, 1), (0, 1), (mag, 36)))
    write("fp8_mul", rows)

    # ---- fp8_dot4: a[32] w[32] y[32] flags[4]
    rows = []
    for a4, w4 in fp8_dot_rows(60000):
        y, fl = R.fp8_dot4(a4, w4)
        rows.append(pack((pack4(a4, 8), 32), (pack4(w4, 8), 32), (y, 32), (fl, 4)))
    write("fp8_dot4", rows)

    # ---- bf16_dot2
    rows = []
    for a2, w2 in bf16_dot_rows(30000):
        y, fl = R.bf16_dot2(a2, w2)
        rows.append(pack((pack4(a2, 16), 32), (pack4(w2, 16), 32), (y, 32), (fl, 4)))
    write("bf16_dot2", rows)

    # ---- fp32_add / fp32_mul: a b y flags
    rows = []
    for a, b in f32_pairs_add(100000):
        y, fl = R.fp32_add(a, b)
        rows.append(pack((a, 32), (b, 32), (y, 32), (fl, 4)))
    write("fp32_add", rows)
    rows = []
    for a, b in f32_pairs_mul(100000):
        y, fl = R.fp32_mul(a, b)
        rows.append(pack((a, 32), (b, 32), (y, 32), (fl, 4)))
    write("fp32_mul", rows)

    rows = []
    for a, b in f32_pairs_add(40000):
        y, fl = R.fp32_max(a, b)
        rows.append(pack((a, 32), (b, 32), (y, 32), (fl, 4)))
    write("fp32_max", rows)

    # ---- mxu_pe_dot: mode a w d flags (both modes from the sets above); mxu_pe: mode a w psum y flags
    rows, prow = [], []
    f8 = fp8_dot_rows(3000)
    f16 = bf16_dot_rows(1500)
    psums = f32_pool(len(f8) + len(f16) + 10)
    for i, (a4, w4) in enumerate(f8):
        d, fd = R.fp8_dot4(a4, w4)
        rows.append(pack((0, 1), (pack4(a4, 8), 32), (pack4(w4, 8), 32), (d, 32), (fd, 4)))
        y, fa = R.fp32_add(psums[i], d)
        prow.append(pack((0, 1), (pack4(a4, 8), 32), (pack4(w4, 8), 32), (psums[i], 32), (y, 32),
                         ((fd & R.NANIN) | (fa & (R.OVF | R.INV)) | (fd & (R.OVF | R.INV)), 4)))
    for j, (a2, w2) in enumerate(f16):
        d, fd = R.bf16_dot2(a2, w2)
        rows.append(pack((1, 1), (pack4(a2, 16), 32), (pack4(w2, 16), 32), (d, 32), (fd, 4)))
        ps = psums[len(f8) + j]
        y, fa = R.fp32_add(ps, d)
        prow.append(pack((1, 1), (pack4(a2, 16), 32), (pack4(w2, 16), 32), (ps, 32), (y, 32),
                         ((fd & R.NANIN) | (fa & (R.OVF | R.INV)) | (fd & (R.OVF | R.INV)), 4)))
    write("mxu_pe_dot", rows)
    write("mxu_pe", prow)

    # ---- acc_update: acc part rowscale cs rescale use_cs overwrite y flags
    rows = []
    pool = f32_pool(40000) + SPECIAL32
    for i in range(20000):
        acc, part, rs, cs = (pool[RNG.integers(0, len(pool))] for _ in range(4))
        if i % 3 == 0:                                          # normal-range operands so that all paths are exercised
            mk = lambda: int(RNG.integers(0, 2)) << 31 | int(RNG.integers(100, 150)) << 23 | int(RNG.integers(0, 1 << 23))  # noqa: E731
            acc, part, rs, cs = mk(), mk(), mk(), mk()
        rsc, ucs, ow = (int(x) for x in RNG.integers(0, 2, 3))
        y, fl = R.acc_update(acc, part, rs, cs, rsc, ucs, ow)
        rows.append(pack((acc, 32), (part, 32), (rs, 32), (cs, 32), (rsc, 1), (ucs, 1), (ow, 1), (y, 32), (fl, 4)))
    write("acc_update", rows)

    # ---- cvt_fp32_fp: one file per format, x y flags
    for fmt in R.SPEC:
        eb, mb, kind, sat, ftz = R.SPEC[fmt]
        xs = list(SPECIAL32) + f32_pool(60000)
        vals = sorted({R.dec_fmt(c, fmt) for c in range(1 << (1 + eb + mb)) if R.dec_fmt(c, fmt)[0] == "fin" and R.dec_fmt(c, fmt)[1] == 0}
                      if eb + mb <= 8 else set())
        # every rounding midpoint of the adjacent finite non-negative codes, +- one FP32 ulp, both signs; plus max + 1/2 ulp
        pos = sorted((m * 2.0 ** k) for (_, _, m, k) in vals)
        if not pos:
            codes = [c for c in range(1 << (eb + mb)) if R.dec_fmt(c, fmt)[0] == "fin"]
            pos = sorted(R.dec_fmt(c, fmt)[2] * 2.0 ** R.dec_fmt(c, fmt)[3] for c in codes)
        pos = sorted(set(pos))
        for lo, hi in zip(pos[:-1], pos[1:]):
            mid = np.float32((lo + hi) / 2)
            for t in (mid, np.nextafter(mid, np.float32(0)), np.nextafter(mid, np.float32(np.inf))):
                b = int(np.float32(t).view(np.uint32))
                xs += [b, b | 0x80000000]
        top = np.float32(pos[-1] + (pos[-1] - pos[-2]) / 2) if len(pos) > 1 else np.float32(pos[-1])
        for t in (top, np.nextafter(top, np.float32(0)), np.nextafter(top, np.float32(np.inf)), np.float32(min(2.0 * pos[-1], 3.0e38))):
            b = int(np.float32(t).view(np.uint32))
            xs += [b, b | 0x80000000]
        # random values at the format's own scale (exponent window around its range)
        lo_e = 127 + (1 - ((1 << (eb - 1)) - 1)) - mb - 3
        hi_e = 127 + ((1 << (eb - 1)) - 1) + 3
        for _ in range(30000):
            e = int(RNG.integers(max(lo_e, 1), min(hi_e, 254) + 1))
            xs.append(int(RNG.integers(0, 2)) << 31 | e << 23 | int(RNG.integers(0, 1 << 23)))
        rows = []
        for x in xs:
            y, fl = R.cvt_fp32_fp(x, fmt)
            rows.append(pack((x, 32), (y, 16), (fl, 4)))
        write(f"cvt_fp32_{fmt.lower()}", rows)

    # ---- cvt_fp32_int: x y flags for (bits, signed)
    for bits, signed, tag in ((8, True, "int8"), (8, False, "uint8"), (16, True, "int16"), (32, True, "int32"), (32, False, "uint32")):
        xs = list(SPECIAL32) + f32_pool(20000)
        for v in list(range(-300, 300)) + [2 ** 15 - 1, 2 ** 15, -2 ** 15, 2 ** 31 - 1, 2 ** 31, -2 ** 31, 2 ** 32, 2 ** 32 - 1]:
            for off in (0.0, 0.5, -0.5, 0.25, 0.75, 1.5, 2.5):
                f = np.float32(v + off)
                for t in (f, np.nextafter(f, np.float32(0)), np.nextafter(f, np.float32(np.inf))):
                    xs.append(int(np.float32(t).view(np.uint32)))
        for _ in range(10000):                                  # exponents around the integer range
            xs.append(int(RNG.integers(0, 2)) << 31 | int(RNG.integers(120, 160)) << 23 | int(RNG.integers(0, 1 << 23)))
        rows = []
        for x in xs:
            y, fl = R.cvt_fp32_int(x, bits, signed)
            rows.append(pack((x, 32), (y, 32), (fl, 4)))
        write(f"cvt_fp32_{tag}", rows)

    # ---- cvt_dec: every code of each format, both destinations: x y flags
    for fmt in R.SPEC:
        eb, mb, *_ = R.SPEC[fmt]
        for to_bf16 in (0, 1):
            rows = []
            for c in range(1 << (1 + eb + mb)):
                y, fl = R.cvt_dec(c, fmt, bool(to_bf16))
                rows.append(pack((c, 16), (y, 32), (fl, 4)))
            write(f"cvt_dec_{fmt.lower()}_{'bf16' if to_bf16 else 'fp32'}", rows)

    # ---- mx_dequant_bf16: exhaustive (el, sc): MXFP4 (0,0), MXFP8 (1,0), NVFP4 (0,1)
    for elem, scale, tag in ((0, 0, "mxfp4"), (1, 0, "mxfp8"), (0, 1, "nvfp4")):
        rows = []
        for el in range(16 if elem == 0 else 256):
            for sc in range(256):
                y, fl = R.mx_dequant_bf16(el, sc, elem, scale)
                rows.append(pack((el, 8), (sc, 8), (y, 16), (fl, 4)))
        write(f"mx_dequant_{tag}", rows)

    # ---- fold: tiles of N elements: N x (el, sc), then key[N], c, cs, v[N], flags[N]
    N = 64
    for elem, scale, tag in ((0, 0, "mxfp4"), (1, 0, "mxfp8"), (0, 1, "nvfp4")):
        rows = []
        nb = 16 if elem == 0 else 256
        for t in range(1500):
            mode = t % 6
            if scale == 0:
                if mode == 0:       # random scale codes, random elements
                    scs = [int(RNG.integers(0, 255))] * (N // 32) * 32
                elif mode == 1:     # narrow spread
                    base = int(RNG.integers(20, 230))
                    scs = [base + int(RNG.integers(0, 4)) for _ in range(N)]
                elif mode == 2:     # exactly 14 binades between the largest and smallest block exponent
                    base = int(RNG.integers(20, 200))
                    scs = [base if i < N // 2 else base + 14 for i in range(N)]
                elif mode == 3:     # 15 binades
                    base = int(RNG.integers(20, 200))
                    scs = [base if i < N // 2 else base + 15 for i in range(N)]
                elif mode == 4:     # extremes
                    scs = [int(RNG.choice([0, 1, 127, 254, 255])) for _ in range(N)]
                else:
                    scs = [int(RNG.integers(0, 256)) for _ in range(N)]
            else:
                scs = [int(RNG.integers(0, 256)) if mode != 1 else (0x30 + int(RNG.integers(0, 0x10))) for _ in range(N)]
            els = [int(RNG.integers(0, nb)) for _ in range(N)]
            if mode == 5:           # all zero elements
                els = [0] * N
            keys = [R.fold_key(e, s, elem, scale) for e, s in zip(els, scs)]
            amax = max(keys)
            c = R.fold_c(amax)
            cs = R.fold_cs(c)
            vs, fls = zip(*[R.fold_quant(e, s, c, elem, scale) for e, s in zip(els, scs)])
            fields = [(e, 8) for e in reversed(els)] + [(s, 8) for s in reversed(scs)]
            fields += [(amax, 17), (c & 0x3FF, 10), (cs, 32)]
            fields += [(v, 8) for v in reversed(vs)] + [(f, 4) for f in reversed(fls)]
            rows.append(pack(*fields))
        write(f"fold_{tag}", rows)


if __name__ == "__main__":
    main()
