#!/usr/bin/env python3
"""Independent checks for the spec review of docs/spec/mxu.md (docs/reports/mxu_review.md, part S).

Shares no code with sw/npumodel/arith_ref.py or scripts/gen_mxu_vectors.py. The SPM bank hash is re-derived from
memmap.md section 4 (bank = addr[10:6] ^ addr[15:11]); the address formulas of the load beats are transcribed from
mxu.md section 4.3; the PE chain is a float64 / float32 model written here from numerics.md N-3.

    python3 scripts/mxu_review/spec_checks.py            # all checks, plain text report on stdout
"""
import sys
from collections import Counter

import numpy as np


# ---------------------------------------------------------------- SPM banks (memmap.md section 4)
def bank(a: int) -> int:
    return ((a >> 6) & 31) ^ ((a >> 11) & 31)


def beat_cycles(addrs) -> int:
    """Cycles a 1R-per-bank SPM needs for one gang of 64 B lanes: the largest number of lanes on one bank."""
    return max(Counter(bank(a) for a in addrs).values())


# ---------------------------------------------------------------- FULL load beats (mxu.md section 4.3)
DIM, LB, KB, GRP, BPG = 64, 64, 256, 16, 8


def trans_tile(base, s1, t, j):
    """32 beats of one stationary tile, trans_w = 1: lane l of beat (g, idx) = W row c = j*DIM + 8*idx + l, bytes
    [t*KB + g*LB, +LB)."""
    for g in range(4):
        for idx in range(BPG):
            yield [base + (j * DIM + 8 * idx + l) * s1 + t * KB + g * LB for l in range(8)]


def nontrans_fp8_tile(base, s1, t, j):
    """trans_w = 0, FP8: lane l = k-row ke0 + 8*(g*GRP/2 + idx) + l, bytes [j*DIM, +DIM)."""
    ke0 = t * 256
    for g in range(4):
        for idx in range(BPG):
            yield [base + (ke0 + 8 * (g * GRP // 2 + idx) + l) * s1 + j * DIM for l in range(8)]


def nontrans_bf16_tile(base, s1, t, j):
    """trans_w = 0, BF16: lane 2*kk + h = k-row ke0 + 4*(g*GRP/2 + idx) + kk, bytes [2*j*DIM + h*LB, +LB)."""
    ke0 = t * 128
    for g in range(4):
        for idx in range(BPG):
            yield [base + (ke0 + 4 * (g * GRP // 2 + idx) + kk) * s1 + 2 * j * DIM + h * LB
                   for kk in range(4) for h in range(2)]


def load_cycles(shape, s1, bases, ts, js):
    """Worst and mean cycles to load one tile (32 beats; 32 = conflict-free) over the bases and tile positions."""
    res = []
    for b in bases:
        for t in ts:
            for j in js:
                res.append(sum(beat_cycles(beat) for beat in shape(b, s1, t, j)))
    return max(res), sum(res) / len(res), min(res)


def check_banks(out):
    out.append("## S-banks: SPM bank conflicts of the FULL load beats (1R per bank, 64 B banks)")
    out.append("pitch = W row pitch s1 in bytes; bases 2 KiB aligned (memmap.md 4, the best case); cycles per tile, "
               "32 = conflict-free")
    bases = [i * 2048 for i in range(32)]
    rows = [
        ("trans FP8, K=2048 padded (2 KiB + 512)", trans_tile, 2560),
        ("trans FP8, K=4096 padded (4 KiB + 512)", trans_tile, 4608),
        ("trans FP8, K=8192 padded (8 KiB + 512)", trans_tile, 8704),
        ("trans FP8, K=16384 padded (16 KiB + 1 KiB)", trans_tile, 17408),
        ("trans FP8, K=2304 (unpadded)", trans_tile, 2304),
        ("trans FP8, K=3072 (unpadded)", trans_tile, 3072),
        ("trans FP8, K=6144 (unpadded)", trans_tile, 6144),
        ("trans FP8, K=7168 (unpadded)", trans_tile, 7168),
        ("trans BF16, K=2048 -> 4 KiB padded (4 KiB + 512)", trans_tile, 4608),
        ("trans BF16, K=1024 -> 2 KiB padded (2 KiB + 512)", trans_tile, 2560),
        ("trans BF16, K=3072 -> 6 KiB (unpadded)", trans_tile, 6144),
        ("trans BF16, K=7168 -> 14 KiB (unpadded)", trans_tile, 14336),
        ("non-trans FP8, N=2048 padded (2 KiB + 512)", nontrans_fp8_tile, 2560),
        ("non-trans FP8, N=4096 padded (4 KiB + 512)", nontrans_fp8_tile, 4608),
        ("non-trans FP8, N=640", nontrans_fp8_tile, 640),
        ("non-trans FP8, N=576", nontrans_fp8_tile, 576),
        ("non-trans BF16, N=1024 -> 2 KiB padded", nontrans_bf16_tile, 2560),
        ("non-trans BF16, N=2048 -> 4 KiB padded", nontrans_bf16_tile, 4608),
        ("non-trans BF16, N=128 (V of one head, 256 B rows)", nontrans_bf16_tile, 256),
        ("non-trans BF16, N=512 (MLA V, 1 KiB rows)", nontrans_bf16_tile, 1024),
    ]
    out.append(f"{'shape':52s} {'worst':>6s} {'mean':>6s} {'best':>5s}")
    for name, shape, s1 in rows:
        w, m, b = load_cycles(shape, s1, bases, ts=range(2), js=range(2))
        out.append(f"{name:52s} {w:6d} {m:6.1f} {b:5d}")
    # one concrete beat for the report
    beat = next(trans_tile(0, 2560, 0, 0))
    out.append("example: trans beat 0 at base 0, pitch 2560: lanes " +
               ", ".join(f"0x{a:05X}->b{bank(a)}" for a in beat))
    out.append("")


def check_a_rows(out):
    out.append("## S-arow: 256 B A-row reads (4 lanes) over every 64 B-aligned start in 64 KiB")
    hits = []
    for a in range(0, 65536, 64):
        lanes = [a + 64 * l for l in range(4)]
        if beat_cycles(lanes) > 1:
            hits.append(a)
    out.append(f"starts with a bank collision: {len(hits)} of 1024; first ones: " +
               ", ".join(f"0x{a:04X}" for a in hits[:6]))
    if hits:
        a = hits[0]
        out.append("  e.g. lanes " + ", ".join(f"0x{a + 64 * l:04X}->b{bank(a + 64 * l)}" for l in range(4)))
    hits256 = [a for a in hits if a % 256 == 0]
    out.append(f"of these, 256 B-aligned starts: {len(hits256)}")
    out.append("")


# ---------------------------------------------------------------- PE chain order (numerics.md N-3), own model
def e4m3_values():
    v = []
    for code in range(256):
        if code & 0x7F == 0x7F:
            continue                       # NaN
        s = -1.0 if code & 0x80 else 1.0
        e = (code >> 3) & 0xF
        m = code & 7
        v.append(s * (m / 8.0) * 2.0 ** -6 if e == 0 else s * (1 + m / 8.0) * 2.0 ** (e - 7))
    return np.array(v)


def f32(x):
    return np.float32(x)


def mm_tiled(A, W, rows, g=4):
    """ACC = A x W (OVERWRITE), FP8: K tile = rows * g; per tile a chain over `rows` PE rows starting at +0, each PE
    adds its exact g-product sum rounded once to FP32; tiles added into ACC in ascending K (numerics.md N-3)."""
    M, K = A.shape
    N = W.shape[1]
    kt = rows * g
    acc = None
    for k0 in range(0, K, kt):
        p = np.zeros((M, N), dtype=np.float32)
        for k in range(k0, min(k0 + kt, K), g):
            d = A[:, k:k + g] @ W[k:k + g, :]          # exact in float64 (products < 2^18, LSB 2^-18)
            p = (p + d.astype(np.float32)).astype(np.float32)
        acc = p if acc is None else (acc + p).astype(np.float32)
    return acc


def check_order(out):
    out.append("## S-order: K-tile order at DIM 8 / 16 against the FULL order of numerics.md N-3 (own model)")
    vals = e4m3_values()
    rng = np.random.default_rng(1)
    M, K, N = 4, 64, 8
    found = None
    for trial in range(200):
        A = rng.choice(vals, size=(M, K))
        W = rng.choice(vals, size=(K, N))
        full = mm_tiled(A, W, 64)
        tiny = mm_tiled(A, W, 8)
        mid = mm_tiled(A, W, 16)
        dt = np.argwhere(full.view(np.uint32) != tiny.view(np.uint32))
        if len(dt):
            found = (trial, A, W, full, tiny, mid, dt)
            break
    if not found:
        out.append("no difference found")
        return
    trial, A, W, full, tiny, mid, dt = found
    m, n = dt[0]
    out.append(f"M={M} K={K} N={N} FP8, random E4M3 operands (seed 1, trial {trial}): {len(dt)} of {M * N} outputs differ "
               f"between DIM 64 (one K tile) and DIM 8 (two K tiles of 32)")
    out.append(f"  out[{m},{n}]: FULL 0x{full.view(np.uint32)[m, n]:08X} ({full[m, n]!r}), "
               f"TINY 0x{tiny.view(np.uint32)[m, n]:08X} ({tiny[m, n]!r}), MID 0x{mid.view(np.uint32)[m, n]:08X}")
    np.save("build/mxu_review/order_A.npy", A)
    np.save("build/mxu_review/order_W.npy", W)
    # does the golden model have a DIM? (numerics.py is the golden model, not arith_ref; used only for this question)
    sys.path.insert(0, "sw")
    from npumodel import numerics
    import inspect
    sig = inspect.signature(numerics.mxu_mm)
    g = np.asarray(numerics.mxu_mm(A, W, "FP8"), dtype=np.float32)
    out.append(f"  numerics.mxu_mm{sig}: no DIM / tile parameter; equals the FULL order: "
               f"{bool((g.view(np.uint32) == full.view(np.uint32)).all())}, equals the TINY order: "
               f"{bool((g.view(np.uint32) == tiny.view(np.uint32)).all())}")
    # cs orientation of the golden producer vs the golden consumer
    w = rng.choice(vals, size=(3, 512))                 # [N=3, K=512] HF layout
    v, cs = numerics.fold_e4m3(w)
    out.append(f"  numerics.fold_e4m3 on W [N=3, K=512] returns cs of shape {np.shape(cs)} ([N, KT]); mxu_mm documents "
               f"cs as [K tiles, N] and indexes cs[t] (mxu.md and isa_ref also say [KT x N])")
    out.append("")


# ---------------------------------------------------------------- throughput arithmetic (d)
def check_rates(out):
    out.append("## S-rate: stationary-load rate at the extrapolated FULL period against the decode need")
    tile_b = 4 * 64 * 64
    need_peak, need_085 = 193, 164         # elements/cycle, microarch.md 6 (4-bit weights at a core's HBM share)
    for P, label in ((32, "port limit (microarch 2.2)"), (39, "mxu.md 7 extrapolation"), (64, "one collision per beat")):
        bps = tile_b / P
        out.append(f"  period {P:2d} ({label}): {bps:6.1f} B/cycle = {bps:6.1f} FP8 el/cycle "
                   f"({bps / need_peak:4.2f}x peak need, {bps / need_085:4.2f}x at 0.85); "
                   f"{bps / 2:6.1f} BF16 el/cycle ({bps / 2 / need_peak:4.2f}x, {bps / 2 / need_085:4.2f}x)")
    # NVFP4 need in elements/cycle: 102 B/cycle HBM share / 0.5625 B per element
    nv_peak, nv_085 = 102 / 0.5625, 0.85 * 102 / 0.5625
    out.append(f"  NVFP4 (0.5625 B/el, exact BF16 path in decode, D1): need {nv_peak:.0f} el/cycle at peak share, "
               f"{nv_085:.0f} at 0.85")
    for P in (32, 39, 64):
        e = tile_b / P / 2
        out.append(f"    period {P}: {e:.0f} BF16 el/cycle = {e / nv_peak:.2f}x peak, {e / nv_085:.2f}x at 0.85")
    # skew bound formula of mxu.md 7 at M close to 32
    for d in (16, 32, 64):
        p = ((d - 1) + d / 8 + 8) / 2
        out.append(f"  mxu.md 7 bound P >= ((DIM-1) + DIM/8 + 8)/2 at DIM {d}: {p:.1f}; port bound DIM/2 = {d // 2}")
    out.append("")


def main():
    import os
    os.makedirs("build/mxu_review", exist_ok=True)
    out = []
    check_banks(out)
    check_a_rows(out)
    check_order(out)
    check_rates(out)
    print("\n".join(out))


if __name__ == "__main__":
    main()
