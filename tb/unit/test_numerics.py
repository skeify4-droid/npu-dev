"""Golden numerics model (sw/npumodel/numerics.py) against ml_dtypes and the rules of docs/spec/numerics.md."""
import os
import sys
from fractions import Fraction

import ml_dtypes as md
import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "sw"))
from npumodel import numerics as nm  # noqa: E402

ML = {"E4M3": md.float8_e4m3fn, "E5M2": md.float8_e5m2, "E2M1": md.float4_e2m1fn, "BF16": md.bfloat16}
RNG = np.random.default_rng(7)


def ml_table(f):
    d = ML[f.name]
    if f.bits == 8:
        return np.arange(256, dtype=np.uint8).view(d).astype(np.float64)
    return np.array([float(np.array([i], dtype=np.uint8).view(d)[0]) for i in range(16)])


@pytest.mark.parametrize("f", [nm.E4M3, nm.E5M2, nm.E2M1])
def test_code_tables_match_ml_dtypes(f):
    assert np.array_equal(nm.decode_table(f), ml_table(f), equal_nan=True)
    assert f.max == float(md.finfo(ML[f.name]).max)
    assert f.min_sub == float(md.finfo(ML[f.name]).smallest_subnormal)


@pytest.mark.parametrize("f", [nm.E4M3, nm.E5M2, nm.E2M1, nm.BF16])
def test_rne_matches_ml_dtypes_including_midpoints(f):
    # FP32 inputs, as in hardware. (ml_dtypes rounds float64 through float32, i.e. twice; the golden rounds once.)
    tab = nm.decode_table(f) if f.bits <= 8 else None
    x = (RNG.standard_normal(100000) * np.exp2(RNG.uniform(-20, 12, 100000))).astype(np.float32)
    if tab is not None:                                    # every midpoint between adjacent finite codes, and its FP32 neighbours
        pos = np.unique(tab[np.isfinite(tab) & (tab >= 0)])
        mids = ((pos[1:] + pos[:-1]) / 2).astype(np.float32)
        x = np.concatenate([x, mids, -mids, np.nextafter(mids, np.float32(0)), np.nextafter(mids, np.float32(np.inf))])
    x = x[np.abs(x) <= f.max]
    assert np.array_equal(nm.round_to(x, f), x.astype(ML[f.name]).astype(np.float64))


def test_single_rounding_from_float64():
    x = 1.0 + 2.0 ** -8 + 2.0 ** -40                        # just above a BF16 midpoint
    assert nm.round_to(x, nm.BF16) == 1.0078125


def test_saturation_and_specials():
    for f in (nm.E4M3, nm.E5M2, nm.E2M1):
        r = nm.round_to([f.max * 1.01, -f.max * 4, np.inf, -np.inf, np.nan], f)
        assert list(r[:4]) == [f.max, -f.max, f.max, -f.max] and np.isnan(r[4])
    assert nm.round_to(464.0, nm.E4M3) == 448.0                # would be NaN without saturation
    b = nm.round_to([3.5e38, np.inf, 1e-39], nm.BF16, ftz=True)
    assert b[0] == np.inf and b[1] == np.inf and b[2] == 0.0      # IEEE overflow; FTZ
    assert list(nm.round_to([1e5, 6.0e-8], nm.FP16)) == [np.inf, float(np.float16(6.0e-8))]   # IEEE overflow, subnormal kept


@pytest.mark.parametrize("f", [nm.E4M3, nm.E5M2, nm.E2M1])
def test_encode_decode_roundtrip(f):
    codes = np.arange(1 << f.bits)
    vals = nm.decode(codes, f)
    enc = nm.encode(vals, f)
    ok = ~np.isnan(vals)
    assert np.array_equal(enc[ok], codes[ok])
    if f.nan != "none":
        assert set(enc[~ok]) == {nm.canonical_nan(f)}
    if f.bits == 8:
        assert np.array_equal(enc[ok], vals[ok].astype(ML[f.name]).view(np.uint8)[...].astype(np.int64))


def test_bf16_fp32_codes():
    x = nm.bf16(RNG.standard_normal(1000))
    assert np.array_equal(nm.decode(nm.encode(x, nm.BF16), nm.BF16), x)
    assert np.array_equal(nm.encode(x, nm.BF16), x.astype(md.bfloat16).view(np.uint16).astype(np.int64))


def test_mx_quant_follows_ocp():
    x = RNG.standard_normal((4, 64)) * np.exp2(RNG.integers(-10, 10, (4, 1)))
    for f in (nm.E2M1, nm.E4M3):
        el, X = nm.mx_quant(x, f)
        amax = np.max(np.abs(x.reshape(4, 2, 32)), -1)
        assert np.array_equal(X, np.floor(np.log2(amax)).astype(int) - nm.EMAX_ELEM[f.name])
        assert np.array_equal(el, nm.round_to(x.reshape(4, 2, 32) / np.exp2(X)[..., None], f))
    el, X = nm.mx_quant(np.zeros((1, 32)), nm.E2M1)
    assert X[0, 0] == -127 and not el.any()
    bad = np.ones((1, 32)); bad[0, 3] = np.nan
    el, X = nm.mx_quant(bad, nm.E2M1)
    assert X[0, 0] == nm.E8M0_NAN and not el.any() and np.isnan(nm.mx_dequant(el, X)).all()
    el, s, T = nm.nvfp4_quant(np.where(np.arange(32) == 20, np.inf, 1.0)[None, :], tscale=1.0)
    assert np.isnan(s[0, 1]) and not np.isnan(s[0, 0]) and not el[0, 1].any()


def test_fold_mxfp4_exact_up_to_14_binades():
    e2m1 = np.array([0.5, 1, 1.5, 2, 3, 4, 6])
    for spread, exact in ((14, True), (15, False)):
        w = np.zeros((1, 256))
        w[0, :32] = 6.0 * 2.0 ** 3                              # block 0: X_max = 3 with a 6 present
        w[0, 32:64] = 0.5 * 2.0 ** (3 - spread)                 # block 1: smallest element, `spread` binades lower
        w[0, 64:] = RNG.choice(e2m1, 192) * 2.0 ** 3
        v, cs = nm.fold_e4m3(w)
        assert np.all(np.abs(nm.unfold(v, cs)) <= np.abs(w).max()) and cs[0, 0] == 2.0 ** (3 - 6)
        assert np.array_equal(nm.unfold(v, cs), w) == exact
    W = RNG.standard_normal((32, 1024))
    e, X = nm.mx_quant(W, nm.E2M1)
    Wm = nm.mx_dequant(e, X).reshape(W.shape)
    v, cs = nm.fold_e4m3(Wm)
    assert np.array_equal(nm.unfold(v, cs), Wm)
    assert np.array_equal(nm.round_to(v, nm.E4M3), v) and np.all(np.abs(v) <= 448)
    assert np.all(np.log2(cs) == np.round(np.log2(cs)))


def test_fold_nvfp4_rounds_but_never_saturates():
    W = RNG.standard_normal((64, 512)) * 0.02
    el, s, T = nm.nvfp4_quant(W)
    Wq = nm.nvfp4_dequant(el, s, T).reshape(W.shape)
    v, cs = nm.fold_e4m3(Wq / T)
    t = (Wq / T).reshape(64, 2, 256)
    assert np.all(np.max(np.abs(v.reshape(64, 2, 256)), -1) <= 448)
    assert cs.shape == (2, 64)                                 # [K tiles, N], the ISA orientation (mxu.md M-12)
    assert np.all(np.max(np.abs(t), -1) / cs.T > 224)          # c is the smallest exponent that fits
    err = np.linalg.norm(nm.unfold(v, cs) - Wq / T) / np.linalg.norm(Wq / T)
    assert 0 < err < 0.04


def test_bf16_path_is_exact_for_all_block_values():
    e2m1 = nm.decode_table(nm.E2M1)
    e4m3 = nm.decode_table(nm.E4M3)
    scales = e4m3[np.isfinite(e4m3) & (e4m3 > 0)]
    prod = np.outer(e2m1, scales).ravel()                       # every NVFP4 element x block scale
    assert np.array_equal(nm.bf16(prod), prod)
    mx8 = np.outer(e4m3[np.isfinite(e4m3)], np.exp2(np.arange(-100, 100))).ravel()
    assert np.array_equal(nm.bf16(mx8), mx8)


def test_mxu_inner_sums_are_exact_in_float64():
    for mode, f, g in (("FP8", nm.E4M3, 4), ("BF16", nm.BF16, 2)):
        lo, hi = (-12, 9) if mode == "FP8" else (-60, 60)
        a = nm.round_to(RNG.standard_normal((200, g)) * np.exp2(RNG.uniform(lo, hi, (200, g))), f)
        w = nm.round_to(RNG.standard_normal((200, g)) * np.exp2(RNG.uniform(lo, hi, (200, g))), f)
        for i in range(200):
            exact = sum(Fraction(float(x)) * Fraction(float(y)) for x, y in zip(a[i], w[i]))
            fsum = float(a[i] @ w[i])
            if mode == "FP8":
                assert Fraction(fsum) == exact
            else:                                               # rounds once to FP32 like the PE
                assert nm.f32(fsum) == nm.f32(float(exact))


def test_mxu_semantics_and_order():
    A = nm.round_to(RNG.standard_normal((8, 512)), nm.E4M3)
    W = nm.round_to(RNG.standard_normal((512, 64)) * 0.1, nm.E4M3)
    y = nm.mxu_mm(A, W)
    assert np.array_equal(y, nm.f32(y)) and np.array_equal(y, nm.mxu_mm(A, W))
    # explicit order: two K tiles, 64 PE rows each, two roundings per PE
    ref = np.zeros((8, 64))
    for k0 in (0, 256):
        part = np.zeros((8, 64))
        for k in range(k0, k0 + 256, 4):
            part = nm.f32(part + nm.f32(A[:, k:k + 4] @ W[k:k + 4]))
        ref = nm.f32(ref + part)
    assert np.array_equal(y, ref)
    cs = np.stack([np.full(64, 0.5), np.full(64, 2.0)])
    rs = np.full(8, 3.0)
    y2 = nm.mxu_mm(A, W, cs=cs, acc=y, rowscale=rs)
    p0 = nm.mxu_mm(A[:, :256], W[:256])
    p1 = nm.mxu_mm(A[:, 256:], W[256:])
    assert np.array_equal(y2, nm.f32(nm.f32(nm.f32(y * 3.0) + nm.f32(p0 * 0.5)) + nm.f32(p1 * 2.0)))
    assert np.max(np.abs(y - A @ W)) <= 4 * np.spacing(np.float32(np.max(np.abs(A @ W))))


def _tiled_ref(A, W, rows, g, cs=None, cs_rep=1, acc=None, rs=None):
    """Independent loop in float32: K tile = rows * g, chain of `rows` PEs from +0 per tile, tiles added in K order.
    acc None = OVERWRITE; rs = RESCALE (ACC * rowscale first)."""
    M, K = A.shape
    acc = np.zeros((M, W.shape[1]), dtype=np.float32) if acc is None else np.asarray(acc, dtype=np.float32)
    if rs is not None:
        acc = (acc * np.asarray(rs, dtype=np.float32)[:, None]).astype(np.float32)
    for t, k0 in enumerate(range(0, K, rows * g)):
        p = np.zeros_like(acc)
        for k in range(k0, min(k0 + rows * g, K), g):
            p = (p + (A[:, k:k + g] @ W[k:k + g]).astype(np.float32)).astype(np.float32)
        if cs is not None:
            p = (p * cs[t // cs_rep].astype(np.float32)).astype(np.float32)
        acc = (acc + p).astype(np.float32)
    return acc


def test_mxu_mm_order_depends_on_dim_review_s2():
    """mxu_review.md S-2: M=4, K=64, N=8 FP8 (operands from the review, tb/unit/data/mxu_order_s2.npz as E4M3 codes).
    One K tile of 16 PE rows (FULL) and two tiles of 8 rows (DIM 8) round differently; out[0,0] is 0xC7FA24B8 vs 0xC7FA24B9."""
    d = np.load(os.path.join(os.path.dirname(__file__), "data", "mxu_order_s2.npz"))
    A, W = nm.decode(d["A"], nm.E4M3), nm.decode(d["W"], nm.E4M3)
    assert A.shape == (4, 64) and W.shape == (64, 8)
    bits = lambda y: np.asarray(y, dtype=np.float32).view(np.uint32)
    full, tiny, mid = (nm.mxu_mm(A, W, dim=n) for n in (64, 8, 16))
    assert np.array_equal(bits(nm.mxu_mm(A, W)), bits(full))                 # FULL is the default
    assert bits(full)[0, 0] == 0xC7FA24B8 and bits(tiny)[0, 0] == 0xC7FA24B9
    assert int((bits(full) != bits(tiny)).sum()) == 15                       # 15 of the 32 outputs, as in the review
    # DIM 16 has K tile 64 = the whole K here: same order as FULL
    assert np.array_equal(bits(mid), bits(full))
    for n, y in ((64, full), (8, tiny), (16, mid)):                          # and each one is the independent loop for its DIM
        assert np.array_equal(bits(y), bits(_tiled_ref(A, W, n, 4)))


@pytest.mark.parametrize("dim", [8, 16, 32, 64])
@pytest.mark.parametrize("mode", ["FP8", "BF16"])
def test_mxu_mm_dim_cs_rows_follow_the_fold_tile(dim, mode):
    """M-13: the fold tile (256 FP8 / 128 BF16) is the same in every config, so pass t uses cs[t // (64 / dim)]."""
    g, fmt = (4, nm.E4M3) if mode == "FP8" else (2, nm.BF16)
    K, N = 64 * g * 2 + 3 * g, 5                                            # 2.05 fold tiles, ragged
    A = nm.round_to(RNG.standard_normal((3, K)), fmt)
    W = nm.round_to(RNG.standard_normal((K, N)) * 0.1, fmt)
    cs = 2.0 ** RNG.integers(-3, 4, (3, N))
    got = nm.mxu_mm(A, W, mode, cs=cs, dim=dim)
    want = _tiled_ref(A, W, dim, g, cs=cs, cs_rep=64 // dim)
    assert np.array_equal(np.asarray(got, dtype=np.float32).view(np.uint32), want.view(np.uint32))
    with pytest.raises(ValueError):
        nm.mxu_mm(A, W, mode, cs=cs[:2], dim=dim)                           # too few fold rows
    # ACCUM / RESCALE compose with dim: acc + part, rowscale first
    acc = nm.f32(RNG.standard_normal((3, N)))
    rs = np.array([0.5, 1.5, 3.0])
    y = nm.mxu_mm(A, W, mode, cs=cs, acc=acc, rowscale=rs, dim=dim)
    want = _tiled_ref(A, W, dim, g, cs=cs, cs_rep=64 // dim, acc=acc, rs=rs)
    assert np.array_equal(np.asarray(y, dtype=np.float32).view(np.uint32), want.view(np.uint32))


def test_mxu_mm_rejects_bad_dim():
    for bad in (0, 3, 128):
        with pytest.raises(ValueError):
            nm.mxu_mm(np.zeros((1, 8)), np.zeros((8, 1)), dim=bad)


def test_fold_cs_orientation_is_the_isa_one():
    """M-12: fold_e4m3 returns cs [K tiles, N] (what mxu_mm indexes cs[t] and the ISA stores as [KT x N]); unfold takes it back."""
    N, K = 6, 512
    W = RNG.standard_normal((N, K)) * 2.0 ** RNG.integers(-4, 4, (N, 1))     # a different scale per output row
    v, cs = nm.fold_e4m3(W)
    assert cs.shape == (K // 256, N)
    for t in range(2):
        for n in range(N):
            amax = np.abs(W[n, t * 256:(t + 1) * 256]).max()
            assert cs[t, n] == 2.0 ** int(nm.fold_exponent(amax))
    assert np.array_equal(nm.unfold(v, cs).reshape(N, 2, 256), v.reshape(N, 2, 256) * cs.T[..., None])
    # the consumer takes it unchanged: A x (folded W)^T with cs equals A x unfold(W)^T up to FP32 rounding, at FULL and at DIM 8
    A = nm.round_to(RNG.standard_normal((3, K)), nm.E4M3)
    ref = A @ nm.unfold(v, cs).T
    for dim in (64, 8):
        y = nm.mxu_mm(A, v.T, cs=cs, dim=dim)
        assert np.max(np.abs(y - ref)) <= 1e-4 * np.max(np.abs(ref))


def test_bf16x3_and_two_pass_hc_form():
    A = RNG.standard_normal((4, 256)).astype(np.float32).astype(np.float64)
    W = RNG.standard_normal((256, 24)).astype(np.float32).astype(np.float64)
    y = nm.mxu_mm_bf16x3(A, W)
    assert np.linalg.norm(y - A @ W) / np.linalg.norm(A @ W) < 2e-5
    Ab = nm.bf16(A)                                            # stream exact in BF16: the A_lo pass is zero
    hi, lo = nm.bf16_split(W)
    two = np.zeros((4, 24))
    for k0 in range(0, 256, nm.KTILE_BF16):
        sl = slice(k0, k0 + nm.KTILE_BF16)
        two = nm.mxu_mm(Ab[:, sl], lo[sl], "BF16", acc=two)
        two = nm.mxu_mm(Ab[:, sl], hi[sl], "BF16", acc=two)
    assert np.array_equal(two, nm.mxu_mm_bf16x3(Ab, W))


def test_report_is_generated():
    path = os.path.join(ROOT, "docs", "reports", "numerics_experiments.md")
    s = open(path).read()
    assert s.startswith("# Phase 2 numerics experiments") and "make numerics" in s
    for sec in ("## E1", "## E2", "## E3", "## E4"):
        assert sec in s
