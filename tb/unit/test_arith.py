"""The exact reference of the arith testbenches (sw/npumodel/arith_ref.py) against the golden model (numerics.py) and
ml_dtypes. The SV testbenches (tb/unit/tb_<block>.sv, `make unit`) compare the RTL with arith_ref; this file shows
arith_ref is the golden model, so the RTL is bit-exact against numerics.md N-8."""
import os
import sys

import ml_dtypes as md
import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "sw"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from npumodel import arith_ref as R  # noqa: E402
from npumodel import numerics as nm  # noqa: E402
import arith_hand_cases as H  # noqa: E402

RNG = np.random.default_rng(11)
ML = {"E4M3": md.float8_e4m3fn, "E5M2": md.float8_e5m2, "E2M1": md.float4_e2m1fn, "BF16": md.bfloat16, "FP16": np.float16}
NMF = {"E4M3": nm.E4M3, "E5M2": nm.E5M2, "E2M1": nm.E2M1, "BF16": nm.BF16, "FP16": nm.FP16}


def f32bits(x):
    return int(np.float32(x).view(np.uint32))


def bits32(b):
    return float(np.uint32(b).view(np.float32))


def same(got, want):
    """Equal values with equal sign of zero, or both NaN (-0 and +0 are different results)."""
    got, want = np.asarray(got, dtype=np.float64), np.asarray(want, dtype=np.float64)
    both_nan = np.isnan(got) & np.isnan(want)
    return bool(np.all(both_nan | ((got == want) & (np.signbit(got) == np.signbit(want)))))


def val(kind_s_m_k):
    kind, s, m, k = kind_s_m_k
    if kind == "nan":
        return np.nan
    if kind == "inf":
        return -np.inf if s else np.inf
    return float(np.copysign(m * 2.0 ** k, -1.0 if s else 1.0))


def test_dec_e4m3_matches_ml_dtypes():
    tab = np.arange(256, dtype=np.uint8).view(md.float8_e4m3fn).astype(np.float64)
    for c in range(256):
        v = val(R.dec_e4m3(c))
        assert (np.isnan(v) and np.isnan(tab[c])) or (v == tab[c] and np.signbit(v) == np.signbit(tab[c]))


@pytest.mark.parametrize("fmt", list(R.SPEC))
def test_dec_fmt_matches_ml_dtypes(fmt):
    eb, mb, *_ = R.SPEC[fmt]
    n = 1 << (1 + eb + mb)
    if fmt == "E2M1":
        ref = np.array([float(np.array([i], dtype=np.uint8).view(md.float4_e2m1fn)[0]) for i in range(16)])
    else:
        dt = ML[fmt]
        ref = np.arange(n, dtype=np.uint16 if n > 256 else np.uint8).view(dt).astype(np.float64)
    for c in range(n):
        v = val(R.dec_fmt(c, fmt))
        assert (np.isnan(v) and np.isnan(ref[c])) or (v == ref[c] and np.signbit(v) == np.signbit(ref[c])), (fmt, c)


@pytest.mark.parametrize("fmt", list(R.SPEC))
def test_cvt_dec_matches_ml_dtypes(fmt):
    eb, mb, *_ = R.SPEC[fmt]
    n = 1 << (1 + eb + mb)
    for c in range(n):
        y32, _ = R.cvt_dec(c, fmt, False)
        y16, _ = R.cvt_dec(c, fmt, True)
        if fmt == "E2M1":
            v = float(np.array([c], dtype=np.uint8).view(md.float4_e2m1fn)[0])
        else:
            v = float(np.array([c], dtype=np.uint16 if n > 256 else np.uint8).view(ML[fmt])[0])
        a32, a16 = bits32(y32), float(np.array([y16], dtype=np.uint16).view(md.bfloat16)[0])
        if np.isnan(v):
            assert y32 == R.NAN32 and y16 == R.NAN16
        else:
            assert a32 == v and np.signbit(a32) == np.signbit(v)
            assert a16 == float(np.float32(v).astype(md.bfloat16))      # exact for the 8/4-bit formats, RNE for FP16


@pytest.mark.parametrize("fmt", ["E4M3", "E5M2", "E2M1", "BF16", "FP16"])
def test_cvt_fp32_fp_matches_golden_and_ml_dtypes(fmt):
    eb, mb, kind, sat, ftz = R.SPEC[fmt]
    f = NMF[fmt]
    e = RNG.integers(max(0, 127 + f.emin - mb - 3), min(254, 127 + int(np.log2(f.max)) + 3), 40000)   # field 0: FP32 subnormals
    e[:200] = 0                                                                                         # and plenty of them
    x = np.array([(int(RNG.integers(0, 2)) << 31) | (int(ei) << 23) | int(RNG.integers(0, 1 << 23)) for ei in e], dtype=np.uint32)
    xf = x.view(np.float32)
    got = np.array([R.cvt_fp32_fp(int(b), fmt)[0] for b in x], dtype=np.int64)
    # golden: numerics.round_to + encode; BF16 through the named DMA conversion (subnormals kept, N-2 / N-5)
    rounded = nm.cvt_fp32_bf16(xf.astype(np.float64)) if fmt == "BF16" else nm.round_to(xf.astype(np.float64), f)
    want = nm.encode(rounded, f)
    assert np.array_equal(got, want)
    # ml_dtypes agrees wherever it does not overflow to NaN / Inf
    ok = np.abs(xf) <= f.max
    if fmt in ("E4M3", "E5M2", "E2M1"):
        dt = ML[fmt]
        ref = xf[ok].astype(dt)
        ref_codes = ref.view(np.uint8).astype(np.int64) if fmt != "E2M1" else None
        if ref_codes is not None:
            assert np.array_equal(got[ok], ref_codes)
        else:
            vals = nm.decode(got[ok], nm.E2M1)
            assert np.array_equal(vals, ref.astype(np.float64))
    else:
        ref = xf[ok].astype(ML[fmt]).view(np.uint16).astype(np.int64)
        assert np.array_equal(got[ok], ref)


def test_cvt_fp32_fp_specials():
    assert R.cvt_fp32_fp(0x7F800000, "E4M3") == (0x7E, 0)
    assert R.cvt_fp32_fp(0xFF800000, "E5M2") == (0xFB, 0)
    assert R.cvt_fp32_fp(0x7F800000, "BF16") == (0x7F80, 0)
    assert R.cvt_fp32_fp(f32bits(464.0), "E4M3") == (0x7E, 0)              # tie to even: 448, no saturation
    assert R.cvt_fp32_fp(f32bits(480.0), "E4M3") == (0x7E, R.SAT)          # would be the NaN code without saturation
    assert R.cvt_fp32_fp(f32bits(1e5), "FP16") == (0x7C00, R.OVF)
    assert R.cvt_fp32_fp(f32bits(7.0), "E2M1") == (0x7, R.SAT)
    assert R.cvt_fp32_fp(0x007FFFFF, "BF16")[0] == 0x0080                # rounds up to 2^-126
    assert R.cvt_fp32_fp(0x00400000, "BF16")[0] == 0x0040                # a BF16 subnormal: kept (F6)
    assert R.cvt_fp32_fp(0x80010000, "BF16")[0] == 0x8001                # the smallest BF16 subnormal, negative
    assert R.cvt_fp32_fp(0x7FC00000, "E2M1") == (0, R.NANIN)


def test_cvt_fp32_int_matches_numpy():
    xs = np.concatenate([RNG.standard_normal(20000) * np.exp2(RNG.uniform(-3, 34, 20000)), np.arange(-300, 300) / 4.0]).astype(np.float32)
    for bits, signed in ((8, True), (8, False), (16, True), (32, True), (32, False)):
        lo, hi = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)
        want = np.clip(np.rint(xs.astype(np.float64)), lo, hi).astype(np.int64) & ((1 << bits) - 1)
        got = np.array([R.cvt_fp32_int(f32bits(x), bits, signed)[0] for x in xs], dtype=np.int64)
        assert np.array_equal(got, want), (bits, signed)
    assert R.cvt_fp32_int(0x7FC00000, 8, True) == (0, R.NANIN)


def test_rnd_matches_golden_round_to():
    for spec, f, ftz in ((R.F32, nm.FP32, True), (R.BF16, nm.BF16, False), (R.E4M3, nm.E4M3, False), (R.FP16, nm.FP16, False)):
        for _ in range(3000):
            m = int(RNG.integers(1, 1 << int(RNG.integers(1, 54))))          # m < 2^53: m * 2^k is exact in float64
            k = int(RNG.integers(-190, 140))
            s = int(RNG.integers(0, 2))
            code, ovf, sat = R.rnd(s, m, k, spec)
            exact = np.float64(m) * 2.0 ** k
            assert np.isfinite(exact)
            want = nm.round_to(-exact if s else exact, f, ftz=ftz)
            got = nm.decode(np.array([code]), f)[0] if f.bits <= 16 else bits32(code)
            assert same(got, want), (spec, m, k, s)


def test_fp8_exhaustive_products_match_ml_dtypes():
    tab = np.arange(256, dtype=np.uint8).view(md.float8_e4m3fn).astype(np.float64)
    for a in range(256):
        for b in range(256):
            kind, s, m, k = R.fp8_prod(a, b)
            if np.isnan(tab[a]) or np.isnan(tab[b]):
                assert kind == "nan"
            else:
                assert kind == "fin" and val((kind, s, m, k)) == tab[a] * tab[b]
                # RNE of the exact product to FP32 equals float32 multiplication of the decoded operands
                assert R.fp8_dot4([a, 0, 0, 0], [b, 0, 0, 0])[0] == f32bits(np.float32(tab[a]) * np.float32(tab[b])) or tab[a] * tab[b] == 0


def test_fp8_dot4_chain_matches_mxu_mm():
    """One 64-row PE column = mxu_mm's per-tile order: chain of (dot rounded to FP32) + (partial) FP32 adds."""
    rows = 8
    for _ in range(300):
        a = RNG.integers(0, 0x7F, 4 * rows) | (RNG.integers(0, 2, 4 * rows) << 7)       # no NaN
        w = RNG.integers(0, 0x7F, 4 * rows) | (RNG.integers(0, 2, 4 * rows) << 7)
        part = 0
        for r in range(rows):
            d, _ = R.fp8_dot4(list(a[4 * r:4 * r + 4]), list(w[4 * r:4 * r + 4]))
            part, _ = R.fp32_add(part, d)
        A = nm.decode(a, nm.E4M3)[None, :]
        W = nm.decode(w, nm.E4M3)[:, None]
        want = nm.mxu_mm(A, W, "FP8")[0, 0]
        assert same(bits32(part), want)


def test_bf16_dot2_chain_matches_mxu_mm():
    rows = 8
    for _ in range(300):
        a = nm.bf16(RNG.standard_normal(2 * rows) * np.exp2(RNG.integers(-20, 20, 2 * rows)))
        w = nm.bf16(RNG.standard_normal(2 * rows) * np.exp2(RNG.integers(-20, 20, 2 * rows)))
        ac, wc = [int(x) for x in nm.encode(a, nm.BF16)], [int(x) for x in nm.encode(w, nm.BF16)]
        part = 0
        for r in range(rows):
            d, _ = R.bf16_dot2(list(ac[2 * r:2 * r + 2]), list(wc[2 * r:2 * r + 2]))
            part, _ = R.fp32_add(part, d)
        want = nm.mxu_mm(a[None, :], w[:, None], "BF16")[0, 0]
        assert same(bits32(part), want)


def test_fp32_add_mul_match_numpy_float32():
    n = 40000
    ea, eb = RNG.integers(60, 190, n), RNG.integers(60, 190, n)
    a = ((RNG.integers(0, 2, n) << 31) | (ea << 23) | RNG.integers(0, 1 << 23, n)).astype(np.uint32)
    b = ((RNG.integers(0, 2, n) << 31) | (eb << 23) | RNG.integers(0, 1 << 23, n)).astype(np.uint32)
    with np.errstate(all="ignore"):
        s = (a.view(np.float32) + b.view(np.float32)).view(np.uint32)
        p = (a.view(np.float32) * b.view(np.float32)).view(np.uint32)
    for i in range(n):
        ya, _ = R.fp32_add(int(a[i]), int(b[i]))
        ym, _ = R.fp32_mul(int(a[i]), int(b[i]))
        sa = int(s[i])
        if (sa & 0x7FFFFFFF) >= 0x00800000 or sa & 0x7FFFFFFF == 0:       # normal result (else FTZ differs from IEEE)
            assert ya == sa                                                  # including the sign of a zero sum
        pm = int(p[i])
        if (pm & 0x7FFFFFFF) >= 0x00800000:
            assert ym == pm


def test_fp32_add_ftz_and_specials():
    assert R.fp32_add(0x00800000, 0x80800001) == (0x80000000, 0)                    # exact difference -2^-149: flushed to -0
    assert R.fp32_add(0x00000001, 0x3F800000)[0] == 0x3F800000                       # DAZ
    assert R.fp32_add(0x80000000, 0x80000000)[0] == 0x80000000                       # (-0) + (-0) = -0
    assert R.fp32_add(0x3F800000, 0xBF800000)[0] == 0x00000000                       # x + (-x) = +0
    assert R.fp32_add(0x7F800000, 0xFF800000) == (R.NAN32, R.INV)
    assert R.fp32_add(0x7F7FFFFF, 0x7F7FFFFF) == (0x7F800000, R.OVF)
    assert R.fp32_mul(0x7F800000, 0x00000000) == (R.NAN32, R.INV)
    assert R.fp32_mul(0x00800000, 0x3F000000)[0] == 0x00000000                       # 2^-127: flushed (rounded on the subnormal grid first)


@pytest.mark.parametrize("elem,scale,kind", [(0, 0, "MXFP4"), (1, 0, "MXFP8"), (0, 1, "NVFP4")])
def test_mx_dequant_matches_golden(elem, scale, kind):
    f_el = nm.E2M1 if elem == 0 else nm.E4M3
    for el in range(16 if elem == 0 else 256):
        v_el = nm.decode(np.array([el]), f_el)[0]
        for sc in range(256):
            y, fl = R.mx_dequant_bf16(el, sc, elem, scale)
            if scale == 0:                                           # E8M0 block scale: numerics.mx_dequant (X = code - 127)
                prod = nm.mx_dequant(np.array([[v_el]]), np.array([sc - 127]))[0, 0]
            else:                                                    # E4M3 block scale, tensor scale 1: numerics.nvfp4_dequant
                prod = nm.nvfp4_dequant(np.array([[v_el]]), nm.decode(np.array([sc]), nm.E4M3), 1.0)[0, 0]
            want = nm.round_to(prod, nm.BF16)                          # subnormals kept (I-15)
            got = nm.decode(np.array([y]), nm.BF16)[0]
            assert same(got, want), (kind, el, sc)


@pytest.mark.parametrize("elem,scale", [(0, 0), (1, 0), (0, 1)])
def test_fold_matches_golden_fold_e4m3(elem, scale):
    f_el = nm.E2M1 if elem == 0 else nm.E4M3
    N = 64
    for t in range(400):
        els = RNG.integers(0, 16 if elem == 0 else 256, N)
        if scale == 0:
            base = int(RNG.integers(30, 200))
            scs = base + RNG.integers(0, 15, N)                        # spread <= 14 binades of block scale
            if t % 4 == 1:
                scs[32:] = 255                                         # a block with the E8M0 NaN scale
        else:
            scs = RNG.integers(0x08, 0x7E, N)
            if t % 4 == 1:
                scs[16:32] = 0x7F                                      # a block with the E4M3 NaN scale
        if t % 4 == 2 and elem == 1:
            els[int(RNG.integers(0, N))] = 0x7F                        # a NaN element (MXFP8; E2M1 has no NaN)
        vals = []
        for e, s in zip(els, scs):
            sg, nan, zero, sig, elsb = R.mx_exact(int(e), int(s), elem, scale)
            vals.append(np.nan if nan else np.copysign(sig * 2.0 ** elsb, -1.0 if sg else 1.0))
        w = np.array(vals)
        v_g, cs_g = nm.fold_e4m3(w[None, :], ktile=N)
        keys = [R.fold_key(int(e), int(s), elem, scale) for e, s in zip(els, scs)]
        c = R.fold_c(max(keys))
        assert 2.0 ** c == cs_g[0, 0]
        got = [R.fold_quant(int(e), int(s), c, elem, scale)[0] for e, s in zip(els, scs)]
        want = nm.encode(v_g[0], nm.E4M3)
        # signed zeros: the golden encode keeps the sign of -0 as well
        assert np.array_equal(np.array(got), want)
        assert R.fold_cs(c) == f32bits(cs_g[0, 0])
        if scale == 0 and elem == 0 and not np.isnan(w).any():         # MXFP4 with a spread <= 14 binades: exact (N-2)
            assert np.array_equal(nm.unfold(v_g, cs_g, ktile=N)[0], w)


# ------------------------------------------------------------------ hand-derived cases (scripts/arith_hand_cases.py)
# The literals were worked out by hand from numerics.md and are independent of arith_ref. gen_arith_vectors.py puts these
# inputs into the RTL vectors with the expected values of arith_ref, so this test is what ties the RTL's hand cases to the
# hand values.
def test_hand_cases_equal_arith_ref():
    n = 0
    for a, b, y, f in H.ADD:
        assert R.fp32_add(a, b) == (y, f), ("add", hex(a), hex(b))
    for a, b, y, f in H.MUL:
        assert R.fp32_mul(a, b) == (y, f), ("mul", hex(a), hex(b))
    for a, b, y, f in H.MAX:
        assert R.fp32_max(a, b) == (y, f), ("max", hex(a), hex(b))
    for a, w, y, f in H.DOT8:
        assert R.fp8_dot4(a, w) == (y, f), ("dot8", a, w)
    for a, w, y, f in H.DOT16:
        assert R.bf16_dot2(a, w) == (y, f), ("dot16", a, w)
    for fmt, x, y, f in H.CVT:
        assert R.cvt_fp32_fp(x, fmt) == (y, f), ("cvt", fmt, hex(x))
    for bits, sg, x, y, f in H.CINT:
        assert R.cvt_fp32_int(x, bits, bool(sg)) == (y, f), ("int", bits, sg, hex(x))
    for fmt, tb, c, y, f in H.DEC:
        assert R.cvt_dec(c, fmt, bool(tb)) == (y, f), ("dec", fmt, tb, hex(c))
    for e, s, el, sc, y, f in H.MXD:
        assert R.mx_dequant_bf16(el, sc, e, s) == (y, f), ("mxd", e, s, hex(el), hex(sc))
    for acc, part, rs, cs, rsc, ucs, ow, y, f in H.ACC:
        assert R.acc_update(acc, part, rs, cs, rsc, ucs, ow) == (y, f), ("acc", hex(acc), hex(part), rsc, ucs, ow)
    for spec in H.FOLD:
        elem, scale, _, _, c_h, cs_h, vh = spec
        els, scs = H.tile_of(spec)
        c = R.fold_c(max(R.fold_key(e, s, elem, scale) for e, s in zip(els, scs)))
        assert (c, R.fold_cs(c)) == (c_h, cs_h), ("fold", spec[:2], sorted(spec[2].items())[:2])
        for i, (v, f) in vh.items():
            assert R.fold_quant(els[i], scs[i], c, elem, scale) == (v, f), ("fold", spec[:2], i)
    n = sum(len(x) for x in (H.ADD, H.MUL, H.MAX, H.DOT8, H.DOT16, H.CVT, H.CINT, H.DEC, H.MXD, H.ACC, H.FOLD))
    assert n == 279          # 265 cases of the review, 8 ACC_UPDATE cases (F3), 6 BF16 subnormal conversions (F6)


def test_hand_cases_cover_the_review_findings():
    """The cases that the vector gaps of the review (F3, F5) need are among the literals."""
    assert ([0x7E] * 4, [0x7E] * 4, 0x49440000, 0) in H.DOT8                       # 4 x 448^2, the top of the 39-bit sum
    assert any(r[7:] == (R.NAN32, R.INV) and r[4:7] == (0, 1, 0) for r in H.ACC)   # NaN made by the column scale: no NAN_IN
