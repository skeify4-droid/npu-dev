#!/usr/bin/env python3
"""Test vectors of the MXU testbenches (tb/unit/tb_mxu_*.sv) -> build/mxu/*.hex and build/mxu/counts.svh.

`mxu_array` (rtl/core/mxu_array.sv): a cycle program for an array of DIM x DIM PEs (DIM = 8 and 16): per cycle the
inputs (shadow window write, swap, A row) and the expected outputs (y_valid, y, y_meta, flags). The model is independent
of the RTL: it applies the window mapping of the mxu_array header to a shadow tile, copies the shadow to the active tile at
a swap, and evaluates every wave with the exact reference of sw/npumodel/arith_ref.py (dot, then FP32 add, PE by PE down
the column), with the flags of each PE row at the cycle the array reports them.

`tb_mxu` (whole instructions): every case is computed twice and the two must agree bit for bit (`build_top` raises
otherwise): (1) the arith_ref PE chain per K tile + `acc_update` per ACC row (the way the RTL is built), and (2) the golden
model `numerics.mxu_mm(A, W, mode, cs, acc, rowscale, dim=DIM)` on the logical A x W. The column scales are generated per
fold tile (256 FP8 / 128 BF16 deep, mxu.md M-13) and the SPM image replicates each one for the `64 / DIM` MXU passes it covers.

Files per program (build/mxu/<pre>*.hex, <pre> = mxu_<dim>_ for the dense programs, mxu_<dim>x_ for the sparse one):
  spm acc0 acc1 dtab prog   the images and the instruction stream
  exp   [0] OR of the PE flags, [1..5] final semaphores 1..5 (also in sem.hex), [6] ordering pair {A, B}, [7] semaphore preload {idx, value},
        [8..11] SPM windows {base, size} (the spm.hex rows are the windows back to back, the fifth one [14] last; dense programs: one window at 0),
        [12] {ACC_LAT, CS_DEPTH, SPM_LAT, total cycles} of the ideal instance (bits 55:48, 47:40, 39:32, 31:0; 0: none; tb_mxu fails on more
        than 2 % either way, and only in an instance with exactly that configuration), [13] a second semaphore preload, [14] a fifth SPM window
  sem   the final value of semaphores 0 .. 63 (64 rows; 0 for one that is never touched, preload + increments otherwise)
  ins   one row per instruction: {maxp, nrows, ssem, sinc} (tb_mxu checks the sem_inc sequence, the acc_last row, the period
        of an M = 1 instruction with maxp > 0, and that no sem_inc comes before the acc_done of its rows)
  flg   one row per ACC row (wave) in stream order: the flags of the PE rows 0 .. DIM-1 (4 bits each, row 0 lowest), already
        masked by the column mask; tb_mxu checks fp_flags in every cycle against them (mxu.md 3: PE row i at T + i + 3)

Usage: gen_mxu_vectors.py [--lock] [OUTDIR]   (default build/mxu)
  --lock   after generating, (re)write scripts/mxu_vectors.lock (row count and SHA-256 of every file, scripts/mxu_lock.py).
           Do this after an intended change of the generator, arith_ref.py, numerics.py or NumPy, and commit the lock.
  --perf   instead of the unit vectors, write the tile-period measurement cases (one M = 1 instruction on a DIM x DIM MXU, W at a
           given pitch and 2 KiB-aligned, A 256 B aligned) to DIR/d<DIM>_p<PITCH>/ (default build/mxu_perf; each directory has its own
           counts.svh). Not locked, not part of `make unit`; see `scripts/mxu_perf.sh`. Cannot be combined with --lock.
"""
import os
import sys

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "sw"))
from npumodel import arith_ref as R  # noqa: E402
from npumodel import numerics as nm  # noqa: E402

_ARGS = [a for a in sys.argv[1:] if a not in ("--lock", "--perf")]
WRITE_LOCK = "--lock" in sys.argv[1:]
PERF = "--perf" in sys.argv[1:]
OUT = _ARGS[0] if _ARGS else os.path.join(ROOT, "build", "mxu_perf" if PERF else "mxu")
os.makedirs(OUT, exist_ok=True)
RNG = np.random.default_rng(20261108)
WL_WORDS = 128
MAXP = {8: 9, 16: 13}                                        # period bound of the M = 1 instruction of the main programs (cycles between ACC rows): the measured maximum of the ideal instance, no margin (mxu.md 7: 13 at DIM 16). Valid for SPM_LAT 2, ACC_LAT 8, ideal ports only (TR-7)
MAXP_X = {8: 9, 16: 14}                                     # the same for the sparse programs: their ideal instances are t8x (SPM_LAT 2) and t16x (SPM_LAT 3, +1 cycle at DIM 16)
# Total cycle count of the ideal instance of each program (tb_mxu's "ACC rows in N cycles", exp.hex row 12; the testbench fails on more than
# 2 % either way, TR-7 / TR2-2) and the configuration it was measured in (IDEAL_CONF: SPM_LAT, CS_DEPTH, ACC_LAT of the instance; ideal ports
# otherwise): tb_mxu checks the count only in an instance with exactly that configuration, so t16x's SPM_LAT 3 is recorded, not implied.
# Re-measure after any change of a program: run tb_mxu, read the "ACC rows in N cycles" line of t8 / t16 / t32 / t8x / t16x, put the number
# here, `--lock`.
IDEAL_CYCLES = {"mxu_8_": 2386, "mxu_16_": 2464, "mxu_32_": 420, "mxu_8x_": 874, "mxu_16x_": 1174}
IDEAL_CONF = {"mxu_8_": (2, 4, 8), "mxu_16_": (2, 4, 8), "mxu_32_": (2, 4, 8), "mxu_8x_": (2, 4, 8), "mxu_16x_": (3, 4, 8)}
PERF_SPM = 1 << 20                                          # SPM size of the --perf cases
SPM_BYTES = {8: 1 << 18, 16: 1 << 18, 32: 1 << 17}     # SPM size of the tb_mxu models (a power of two)
META_W = 16
NANIN, OVF, INV = R.NANIN, R.OVF, R.INV


def hexw(fields):
    """fields: list of (value, bits), MSB first -> hex string"""
    v, n = 0, 0
    for val, bits in fields:
        assert 0 <= val < (1 << bits), (val, bits)
        v = (v << bits) | val
        n += bits
    return format(v, "0%dx" % ((n + 3) // 4))


# ------------------------------------------------------------------ operand generators
def fp8_code():
    r = RNG.random()
    if r < 0.02:
        return int(RNG.choice([0x7F, 0xFF]))                         # NaN
    if r < 0.10:
        return int(RNG.choice([0x00, 0x80, 0x01, 0x81, 0x7E, 0xFE])) # zeros, subnormal, max
    return int(RNG.integers(0, 256))


def bf16_code(special=0.03):
    r = RNG.random()
    if r < special:
        return int(RNG.choice([0x7F80, 0xFF80, 0x7FC0, 0x7F7F, 0xFF7F, 0x0000, 0x8000, 0x0001]))
    sign = int(RNG.integers(0, 2))
    exp = int(RNG.integers(110, 150))
    man = int(RNG.integers(0, 128))
    return (sign << 15) | (exp << 7) | man


def word(mode_bf16, kind="rand"):
    if mode_bf16:
        if kind == "zero":
            return 0
        if kind == "clean":
            return bf16_code(0) | (bf16_code(0) << 16)
        return bf16_code() | (bf16_code() << 16)
    if kind == "zero":
        return 0
    if kind == "clean":
        c = lambda: int(RNG.choice([x for x in range(256) if (x & 0x7F) != 0x7F]))  # noqa: E731
        return c() | (c() << 8) | (c() << 16) | (c() << 24)
    return fp8_code() | (fp8_code() << 8) | (fp8_code() << 16) | (fp8_code() << 24)


# ------------------------------------------------------------------ the array model
class Prog:
    def __init__(self, dim):
        self.d = dim
        self.grp = dim // 4
        self.ng = dim // self.grp
        self.lat = dim + 2
        self.cyc = {}                                   # cycle -> dict of input fields
        self.shadow = [[None] * dim for _ in range(dim)]
        self.active = None
        self.swaps = []                                 # (cycle, tile snapshot)
        self.waves = []                                 # (cycle, mode, ncols, meta, words)
        self.prev_swap = [None, None]      # per shadow buffer
        self.n_meta = 0

    def at(self, c):
        return self.cyc.setdefault(c, {})

    # --- window writes (the mapping under test, written from the header text)
    def window(self, g, idx, trans, tile):
        d, grp = self.d, self.grp
        w = [0] * WL_WORDS
        if trans:
            for c in range(8):
                for r in range(grp):
                    w[c * 16 + r] = tile[g * grp + r][8 * idx + c]
        else:
            for r in range(2):
                for c in range(d):
                    w[r * d + c] = tile[g * grp + 2 * idx + r][c]
        return w

    def load_tile(self, tile, trans, start, buf=0):
        """Write all windows of a tile at cycles >= start, group g not before the previous swap has passed its last row.
        Returns the last cycle used."""
        d, grp = self.d, self.grp
        cur = start
        nidx = d // 8 if trans else grp // 2
        for g in range(self.ng):
            if self.prev_swap[buf] is not None:
                cur = max(cur, self.prev_swap[buf] + g * grp + grp - 1)  # the tap cycle itself is allowed
            for idx in range(nidx):
                f = self.at(cur)
                assert "wl" not in f
                f["wl"] = (trans, g, idx, self.window(g, idx, trans, tile), buf)
                cur += 1
        self._apply(tile, trans)
        return cur - 1

    def _apply(self, tile, trans):
        # the model applies the whole tile to the shadow (windows only ever cover distinct PEs)
        self.shadow = [row[:] for row in tile]

    def stream(self, s, tile, mode_bf16, ncols, rows, bubbles=(), buf=0):
        """swap at cycle s with the first row; rows: list of A rows (lists of d words); bubbles: set of row indices
        after which one idle cycle is inserted. Returns the next free cycle."""
        self.at(s)["swap"] = 1
        self.at(s)["swap_buf"] = buf
        self.swaps.append((s, [row[:] for row in tile]))
        self.prev_swap[buf] = s
        c = s
        for k, a in enumerate(rows):
            f = self.at(c)
            assert "a" not in f
            f["a"] = (mode_bf16, ncols, self.n_meta, a)
            self.waves.append((c, mode_bf16, ncols, self.n_meta, a))
            self.n_meta += 1
            c += 1
            if k in bubbles:
                c += 1
        return c

    # --- expected outputs
    def expected(self):
        d, lat = self.d, self.lat
        exp = {}                                         # cycle -> (yv, y, meta, flags)
        flg = {}
        si = -1
        swaps = sorted(self.swaps)
        for (T, mode, nc, meta, a) in self.waves:
            while si + 1 < len(swaps) and swaps[si + 1][0] <= T:
                si += 1
            W = swaps[si][1]
            ys = []
            rowflags = [0] * d
            for j in range(d):
                ps = 0
                for i in range(d):
                    if mode:
                        dd, fd = R.bf16_dot2([a[i] & 0xFFFF, a[i] >> 16], [W[i][j] & 0xFFFF, W[i][j] >> 16])
                    else:
                        dd, fd = R.fp8_dot4([(a[i] >> (8 * k)) & 255 for k in range(4)],
                                            [(W[i][j] >> (8 * k)) & 255 for k in range(4)])
                    y, fa = R.fp32_add(ps, dd)
                    f = (fd & NANIN) | (fa & (OVF | INV)) | (fd & (OVF | INV))
                    if j < nc:
                        rowflags[i] |= f
                    ps = y
                ys.append(ps)
            for i in range(d):
                flg[T + i + 3] = flg.get(T + i + 3, 0) | rowflags[i]
            exp[T + lat] = (1, ys, meta)
        return exp, flg


def build(dim):
    P = Prog(dim)
    grp = P.grp
    d = dim

    def tile(mode, kind="rand", dead_cols=0, dead_rows=0):
        t = [[word(mode, kind) for _ in range(d)] for _ in range(d)]
        for i in range(d):
            for j in range(d):
                if j >= d - dead_cols or i >= d - dead_rows:
                    t[i][j] = 0
        return t

    def rows(n, mode, kind="rand", zero_tail=0):
        out = []
        for _ in range(n):
            r = [word(mode, kind) for _ in range(d)]
            for i in range(d - zero_tail, d):
                r[i] = 0
            out.append(r)
        return out

    c = 4
    # 1. first tile, trans windows, clean operands, a short and a long stream
    t = tile(0, "clean")
    last = P.load_tile(t, True, c, 0)
    c = P.stream(last + 1, t, 0, d, rows(5, 0, "clean"), buf=0)
    # 2. next tile loaded while the stream is still going, non-trans windows, back to back
    t2 = tile(0, "clean")
    last = P.load_tile(t2, False, c - 5 + grp - 1, 1)
    c = P.stream(max(last + 1, c), t2, 0, d, rows(3 * d, 0, "clean"), buf=1)
    # 3. random operands incl. NaN, zero tail rows, bubbles
    t3 = tile(0, "rand")
    last = P.load_tile(t3, True, c - 3 * d + grp, 0)
    c = P.stream(max(last + 1, c), t3, 0, d, rows(2 * d + 3, 0, "rand", zero_tail=d // 4), bubbles={1, 7, 8}, buf=0)
    # 4. BF16 tile, columns past ncols zero but A may hold NaN: flags of dead columns are masked
    t4 = tile(1, "rand", dead_cols=d // 4)
    last = P.load_tile(t4, False, c + 2, 1)
    c = P.stream(last + 1, t4, 1, d - d // 4, rows(2 * d, 1, "rand"), buf=1)
    # 5. FP8 right after BF16 (mode change on consecutive waves), back to back
    t5 = tile(0, "rand", dead_cols=3, dead_rows=2)
    last = P.load_tile(t5, True, c - 2 * d + grp + 1, 1)
    c = P.stream(max(last + 1, c), t5, 0, d - 3, rows(d + 2, 0, "rand"), buf=1)
    # 6. BF16 clean, many rows, then idle gap and a single row tile
    t6 = tile(1, "clean")
    last = P.load_tile(t6, True, c - d + grp, 0)
    c = P.stream(max(last + 1, c), t6, 1, d, rows(4 * d, 1, "clean"), buf=0)
    t7 = tile(0, "clean")
    last = P.load_tile(t7, False, c + 3, 0)
    c = P.stream(last + 1 + 5, t7, 0, d, rows(1, 0, "clean"), buf=0)
    # 7. BF16 with specials everywhere (Inf, NaN, overflow products)
    t8 = tile(1, "rand")
    last = P.load_tile(t8, False, c + d, 1)
    c = P.stream(last + 1, t8, 1, d, rows(3 * d, 1, "rand"), buf=1)
    n_cycles = c + P.lat + 6
    return P, n_cycles


# ------------------------------------------------------------------ whole-instruction cases (tb_mxu)
def chain(a_words, w_words, bf16):
    """one PE column over DIM PE rows: (partial sum, flags of each PE row)"""
    ps, fls = 0, []
    for a, w in zip(a_words, w_words):
        if bf16:
            dd, fd = R.bf16_dot2([a & 0xFFFF, a >> 16], [w & 0xFFFF, w >> 16])
        else:
            dd, fd = R.fp8_dot4([(a >> (8 * k)) & 255 for k in range(4)], [(w >> (8 * k)) & 255 for k in range(4)])
        y, fa = R.fp32_add(ps, dd)
        fls.append((fd & NANIN) | (fa & (OVF | INV)) | (fd & (OVF | INV)))
        ps = y
    return ps, fls


def f32bits(x):
    return int(np.float32(x).view(np.uint32))


def fp8_clean():
    return int(RNG.choice([c for c in range(256) if (c & 0x7F) != 0x7F]))


def bf16_clean():
    return bf16_code(0)


def f32_from_bits(words):
    return np.array(words, dtype=np.uint32).view(np.float32).astype(np.float64)


def golden_check(dim, bf16, accm, M, K, N, A, W, cs_fold, rs_vals, acc_init, acc, acc_row):
    """The ACC rows the arith_ref chain produced (`acc`) must equal numerics.mxu_mm(..., dim=dim) on the logical A x W
    (bit for bit; any NaN equals any NaN: the two models may differ in the payload only)."""
    fmt = nm.BF16 if bf16 else nm.E4M3
    a = nm.decode(np.array(A, dtype=np.int64), fmt)
    w = nm.decode(np.array(W, dtype=np.int64), fmt)
    cs = f32_from_bits(cs_fold) if cs_fold is not None else None
    init = f32_from_bits(acc_init) if accm else None
    rs = f32_from_bits(rs_vals) if accm == 2 else None
    y = nm.mxu_mm(a, w, "BF16" if bf16 else "FP8", cs=cs, acc=init, rowscale=rs, dim=dim).astype(np.float32).view(np.uint32)
    NT = (N + dim - 1) // dim
    bad = []
    for j in range(NT):
        for m in range(M):
            for c in range(min(dim, N - j * dim)):
                got, want = acc[acc_row + j * M + m][c], int(y[m, j * dim + c])
                nan = lambda v: (v & 0x7F800000) == 0x7F800000 and (v & 0x007FFFFF) != 0
                if got != want and not (nan(got) and nan(want)):
                    bad.append((m, j * dim + c, got, want))
    if bad:
        m, n, got, want = bad[0]
        raise AssertionError("dim %d %s accm %d M %d K %d N %d: arith_ref chain and numerics.mxu_mm differ at %d of %d outputs, "
                             "first out[%d,%d] 0x%08X vs 0x%08X" % (dim, "BF16" if bf16 else "FP8", accm, M, K, N, len(bad),
                                                                   M * N, m, n, got, want))


class Mem:
    """The SPM image as windows (base, bytearray); a dense program is one window at 0. Slice assignment maps to the window."""
    def __init__(self, wins):
        self.w = [(b, bytearray(b"\xff\x7f" * (n // 2))) for b, n in wins]   # NaN-valued garbage (E4M3 and BF16), base even

    def __setitem__(self, sl, data):
        for b, buf in self.w:
            if b <= sl.start and sl.stop <= b + len(buf):
                buf[sl.start - b: sl.stop - b] = data
                return
        raise AssertionError("write outside every window: %x" % sl.start)

    def image(self):
        return b"".join(bytes(buf) for _, buf in self.w)


# SPM windows of the sparse program (T-7): every SPM address bit 17 .. 21 is set in one operand window and clear in another
SPARSE_WIN = {"a": (0x3E0000, 1 << 16), "w": (0x2A0000, 1 << 17), "cs": (0x150000, 1 << 16), "rs": (0x3FF000, 1 << 12),
              "a2": (0x1C0000, 1 << 16)}               # TR2-8: a second A window, address bits 21 / 20 = 0 / 1 (the first has 1 / 1)
SPARSE_SPM = 1 << 22                                    # the RTL's SPM_BYTES default (22-bit lane addresses)
SEM_PRE = [(41, 0xFFFFFFFD),                            # semaphore 41 starts 3 below 2^32 (the wait across the wrap, T-9)
           (42, 0x7FFFFFFE)]                            # semaphore 42 starts 2 below 2^31 (the wait across 2^31, TR-5: a signed compare starts at once)
CARRY_A = (0xFFC0, 0x10200)                             # TR-2 / TR2-7: the SPM range of the dense A operand whose pass start (a_base + t * 4 DIM) and rows cross 0x10000; the allocator skips it
# TR2-7: dense W operands whose lane addresses cross a multiple of 0x10000: a trans one across 0x20000 (carry into bit 17; its row n = 3 starts at
# 0x20000) and a non-trans one across 0x30000 (carry into bit 16; its k-row 5 starts at 0x30000), pinned like CARRY_A. The ranges are the
# operand and, for the non-trans one, its guard rows (K x pitch + 4 DIM x pitch); DIM-specific, built in build_top
W_TR_AT = 0x20000 - 3 * 128
W_NT_AT = 0x30000 - 5 * 64


def build_top(dim, variant="main", perf=None):
    """A list of MXU_MM instructions on a DIM x DIM MXU with their SPM image, DTAB, initial / expected ACC and flags.
    variant: "main" (DIM 8 / 16: the full set; DIM 32: the M = 1 period instruction and a few of every kind), "sparse" (SPM of 4 MiB
    as four windows, 2048 ACC rows, operands above 2 MiB and ACC bases above 1024).
    perf = (K, N, pitch, trans, bf16): only the tile-period measurement instruction (--perf), see PERF_CASES."""
    LB = dim
    KB = 4 * dim
    sparse = variant == "sparse"
    if perf:
        SPM = PERF_SPM
    elif sparse:
        SPM = SPARSE_SPM
    else:
        SPM = SPM_BYTES[dim]
    if sparse:
        wins = {k: v for k, v in SPARSE_WIN.items()}
        mem = Mem(list(wins.values()))
        cur = {k: [b, b, b + n] for k, (b, n) in wins.items()}       # kind -> [window base, top, end]
    else:
        wins = {"a": (0, SPM)}
        mem = Mem([(0, SPM)])
        one = [0, 0, SPM]
        cur = {k: one for k in ("a", "w", "cs", "rs")}
    spm = mem

    # TR2-7 operands: trans W FP8 K = 100, N = 2 DIM (pitch 128), non-trans W FP8 K = 8 DIM, N = DIM (pitch 64, guard rows behind it)
    w_tr_n = 2 * dim * 128
    w_nt_n = 8 * dim * 64 + 4 * dim * 64
    pins = [] if (sparse or perf or dim == 32) else [CARRY_A, (W_TR_AT, W_TR_AT + w_tr_n), (W_NT_AT, W_NT_AT + w_nt_n)]    # ranges only alloc_at may use (TR-2)

    def alloc_at(addr, nbytes):
        assert any(p0 <= addr and addr + nbytes <= p1 for p0, p1 in pins), "alloc_at outside every pin"
        return addr

    def alloc(nbytes, at_end=False, align=64, kind="a"):
        """64 B aligned base (the alignment rule of every config, M-10); at_end: the extent ends in the last 64 B granule."""
        c = cur[kind]
        if at_end:
            base = (c[2] - nbytes) // 64 * 64
            c[2] = base
            assert c[1] <= c[2], "SPM full (%s, %d bytes)" % (kind, nbytes)
            return base
        base = (c[1] + align - 1) // align * align
        for p0, p1 in pins:                                           # skip a pinned range
            if base < p1 and base + nbytes > p0:
                base = (p1 + align - 1) // align * align
        c[1] = base + nbytes
        assert c[1] <= c[2], "SPM full (%s, %d bytes, top %d, end %d)" % (kind, nbytes, c[1], c[2])
        return base

    ACC_ROWS = 2048 if sparse else 256
    acc = [[f32bits(RNG.uniform(-8, 8)) for _ in range(dim)] for _ in range(ACC_ROWS)]
    acc0 = [r[:] for r in acc]
    tds, instrs, flags_or = [], [], 0
    sem_cnt = {}
    waves = []                  # per ACC row in stream order: the flags of PE rows 0 .. dim-1 (column mask applied)
    meta = []                   # per instruction: (maxp, nrows, ssem, sinc)

    def td(dtype, n0, n1, s1):
        v = dtype | (1 << 5) | (n0 << 16) | (n1 << 32) | (1 << 48) | (1 << 64) | (s1 << 80)
        tds.append(v)
        return len(tds) - 1

    def case(M, K, N, bf16, trans, accm, cs, rs, acc_row, pad_a=0, pad_w=0, wsem=0, wval=0, ssem=0, sinc=0, special=False,
             m_reg=False, cs_end=False, decoy=None, a_align=64, w_align=64, rs_end=False, maxp=0, inj=None, a_at=None, w_at=None,
             a_kind="a"):
        nonlocal flags_or
        eb = 2 if bf16 else 1
        gen = bf16_clean if bf16 else fp8_clean
        spec = bf16_code if bf16 else fp8_code
        g = spec if special else gen
        A = [[g() for _ in range(K)] for _ in range(M)]
        for (m_, k_), v_ in (inj or {}).items():
            A[m_][k_] = v_                                              # operand values the test needs (Inf, NaN, overflow)
        W = [[gen() for _ in range(N)] for _ in range(K)]               # logical K x N
        a_pitch = (K * eb + 63) // 64 * 64 + pad_a
        a_base = alloc(M * a_pitch, align=a_align, kind=a_kind) if a_at is None else alloc_at(a_at, (M - 1) * a_pitch + K * eb)
        for m in range(M):
            for k in range(K):
                spm[a_base + m * a_pitch + k * eb: a_base + m * a_pitch + (k + 1) * eb] = A[m][k].to_bytes(eb, "little")
        if trans:
            w_pitch = (K * eb + 63) // 64 * 64 + pad_w
            w_base = alloc(N * w_pitch, align=w_align, kind="w") if w_at is None else alloc_at(w_at, N * w_pitch)
            for n in range(N):
                for k in range(K):
                    spm[w_base + n * w_pitch + k * eb: w_base + n * w_pitch + (k + 1) * eb] = W[k][n].to_bytes(eb, "little")
            w_td = td(1 if bf16 else 3, K, N, w_pitch)
        else:
            w_pitch = (N * eb + 63) // 64 * 64 + pad_w
            # a guard of one K tile of rows behind W stays NaN-valued garbage: a lane requested for a k-row >= K reads NaN there (and
            # 0 x NaN flags), whatever the next operand is
            w_nb = K * w_pitch + (2 if bf16 else 4) * dim * w_pitch
            w_base = alloc(w_nb, align=w_align, kind="w") if w_at is None else alloc_at(w_at, w_nb)
            for k in range(K):
                for n in range(N):
                    spm[w_base + k * w_pitch + n * eb: w_base + k * w_pitch + (n + 1) * eb] = W[k][n].to_bytes(eb, "little")
            w_td = td(1 if bf16 else 3, N, K, w_pitch)
        a_td = td(1 if bf16 else 3, K, M, a_pitch)
        kte = (2 if bf16 else 4) * dim
        KT = (K + kte - 1) // kte
        NT = (N + dim - 1) // dim
        cs_vals = rs_vals = cs_fold = None
        cs_base = rs_base = 0
        if cs:
            fold = (128 if bf16 else 256)                # the DMA's fold tile, the same in every config (M-13)
            rep_ = fold // kte                          # = 64 / DIM MXU passes per scale row
            KTF = (K + fold - 1) // fold
            CSP = (4 * N + 63) // 64 * 64               # row pitch of the scale array (M-11)
            cs_fold = [[f32bits(2.0 ** int(RNG.integers(-3, 4)) * RNG.uniform(0.75, 1.25)) for _ in range(N)] for _ in range(KTF)]
            cs_vals = [cs_fold[t // rep_] for t in range(KT)]        # the scale row of MXU pass t (what the MXU must apply)
            cs_base = alloc((KTF - 1) * CSP + 4 * N, at_end=cs_end, kind="cs")  # exactly the bytes the DMA writes: nothing behind the last row
            for r_ in range(KTF):
                for n in range(N):
                    spm[cs_base + CSP * r_ + 4 * n: cs_base + CSP * r_ + 4 * n + 4] = cs_fold[r_][n].to_bytes(4, "little")
        if rs:
            rs_vals = [f32bits(RNG.uniform(0.25, 1.5)) for _ in range(M)]
            rs_base = alloc(4 * M, at_end=rs_end, kind="rs")
            for m in range(M):
                spm[rs_base + 4 * m: rs_base + 4 * m + 4] = rs_vals[m].to_bytes(4, "little")
        assert acc_row + NT * M <= ACC_ROWS, "ACC rows of this case run past ACC_ROWS"
        acc_init = [[acc[acc_row + (n // dim) * M + m][n % dim] for n in range(N)] for m in range(M)]
        # expected: per N tile j, K tile t in order; one wave per (j, t, m)
        nw0 = len(waves)
        for j in range(NT):
            nc = min(dim, N - j * dim)
            for t in range(KT):
                for m in range(M):
                    wf = [0] * dim
                    for c in range(nc):
                        n = j * dim + c
                        aw, ww = [], []
                        epw = 2 if bf16 else 4
                        for i in range(dim):
                            av = [A[m][t * kte + epw * i + q] if t * kte + epw * i + q < K else 0 for q in range(epw)]
                            wv = [W[t * kte + epw * i + q][n] if t * kte + epw * i + q < K else 0 for q in range(epw)]
                            sh = 16 if bf16 else 8
                            aw.append(sum(v << (sh * q) for q, v in enumerate(av)))
                            ww.append(sum(v << (sh * q) for q, v in enumerate(wv)))
                        ps, fls = chain(aw, ww, bf16)
                        for i in range(dim):
                            wf[i] |= fls[i]
                        r = acc_row + j * M + m
                        y, _ = R.acc_update(acc[r][c], ps, rs_vals[m] if (rs and accm == 2) else 0,
                                            cs_vals[t][n] if cs else 0, accm == 2 and t == 0, bool(cs), accm == 0 and t == 0)
                        acc[r][c] = y
                    waves.append(wf)
                    for f in wf:
                        flags_or |= f
        assert rs or accm != 2, "RESCALE needs a rowscale vector"
        if M:
            golden_check(dim, bf16, accm, M, K, N, A, W, cs_fold, rs_vals, acc_init, acc, acc_row)
        regs = [a_base, w_base, acc_row, M if m_reg else 0, rs_base, cs_base]
        ins = (0x60 << 120) | (wsem << 114) | (ssem << 95) | (sinc << 91) | (1 << 86) | (a_td << 78) | (2 << 73) | (w_td << 65) | \
              (3 << 60) | ((4 if m_reg else 0) << 55) | ((5 if (rs and accm == 2) else 0) << 50) | ((6 if cs else 0) << 45) | \
              (accm << 43) | (int(trans) << 39)
        if m_reg:
            # the TD's n1 is a decoy: the register wins
            tds[a_td] = (tds[a_td] & ~(0xFFFF << 32)) | (((M + 7 if decoy is None else decoy) & 0xFFFF) << 32)
        instrs.append((ins, regs, wval))
        meta.append((maxp, len(waves) - nw0, ssem, sinc))
        if ssem and sinc:
            sem_cnt[ssem] = sem_cnt.get(ssem, 0) + sinc

    def result(order, sem_pre=()):
        sc = dict(sem_cnt)
        for i_, v_ in sem_pre:
            sc[i_] = (sc.get(i_, 0) + v_) & 0xFFFFFFFF
        return dict(dim=dim, spm=mem.image(), wins=[(b, len(buf)) for b, buf in mem.w], tds=tds, instrs=instrs, acc0=acc0, acc1=acc,
                    acc_rows=ACC_ROWS, flags=flags_or & 0xF, sem=sc, spm_bytes=SPM, order=order, waves=waves, meta=meta,
                    sem_pre=list(sem_pre))

    D = dim
    if perf:
        K, N, pitch, trans, bf16 = perf
        eb = 2 if bf16 else 1
        pad = pitch - ((K if trans else N) * eb + 63) // 64 * 64
        assert pad >= 0, "pitch %d is smaller than the row" % pitch
        case(1, K, N, bf16, trans, 0, False, False, 100, pad_w=pad, ssem=1, sinc=1, a_align=256, w_align=2048)
        return result((0, 0))
    if sparse:
        # T-7: operands above 2 MiB (A 0x3E0000, W 0x2A0000, scales 0x150000, rowscale 0x3FF000 up to the end of the 4 MiB SPM) and ACC rows
        # above 1024, the last case ends exactly at ACC_ROWS = 2048
        case(5, 4 * D + 3, D + D // 2, False, True, 0, True, False, 1030, ssem=1, sinc=1)                    # FP8 trans OVERWRITE + cs, 2 N tiles
        case(3, 4 * D, D, False, False, 1, False, False, 1032, wsem=1, wval=1)                              # FP8 non-trans ACCUM onto rows of #1
        case(D // 4 + 1, 2 * D + 3, D + D // 4, True, True, 2, True, True, 1500, ssem=2, sinc=3, pad_a=64)   # BF16 RESCALE + cs + rowscale
        case(16, 2 * D + 1, D + 2, True, False, 0, False, False, 2048 - 16 * 2)                              # BF16 non-trans, rows end at ACC_ROWS
        # TR2-8: the cases from here on read A from the second window (0x1C0000: address bits 21 / 20 = 0 / 1; the first has 1 / 1), so that a
        # port that swaps address bits 20 and 21 reads the wrong window
        case(16, 4 * D, D, False, True, 2, False, True, 1100, rs_end=True, ssem=3, sinc=1, a_kind="a2")        # RESCALE, rowscale 64 B ending at the SPM end
        # TR-2: the ACC row counter and the pass start carry into bit 8: one pass whose rows cross 1024 (1020 .. 1027), and passes at 1272 + 4 j
        # (pass 2 starts at 1280, a multiple of 256, with bits 8+ different from acc_base)
        case(8, 4 * D, D, False, True, 0, False, False, 1020, a_kind="a2")
        case(4, 4 * D + 1, 2 * D + 1, False, True, 0, False, False, 1272, a_kind="a2")                      # NT = 3, M = 4: rows 1272 .. 1283
        # TR2-7: a pass start across a multiple of 512 (bit 9): NT = 3, M = 4 from 1532: pass 1 starts at 1536 = 0x600 (0x5FC + 4)
        case(4, 4 * D + 1, 2 * D + 1, False, True, 0, False, False, 1532, a_kind="a2")
        # TR-7: an M = 1 instruction with a period bound (the ideal instances t8x / t16x check it, CHKP)
        case(1, 8 * D, 4 * D, False, True, 0, False, False, 1700, maxp=MAXP_X[dim], a_align=256, w_align=2048, a_kind="a2")
        # TR2-6: counters wider than any case before: M = 130 (more than 128 rows in a pass), 18 K tiles (more than 16), a non-trans FP8 operand
        # with 9 N tiles (more than 4); each on ACC rows no other case touches
        case(130, 4 * D, D, False, True, 0, False, False, 1800)                                              # rows 1800 .. 1929
        case(1, 68 * D + 1, D, False, True, 0, False, False, 1940)                                           # KT = 18: 17 full K tiles and one of one element
        case(2, 4 * D, 8 * D + 3, False, False, 0, False, False, 1950)                                       # non-trans FP8, NT = 9, M = 2: rows 1950 .. 1967
        return result((0, 0))
    if dim == 32:
        # one M = 1 instruction, 4 passes: the tile period measurement on a larger array (maxp: 26 cycles, against 21.5 measured, mxu.md 7)
        case(1, 8 * D, 2 * D, False, True, 0, False, False, 100, ssem=1, sinc=1, maxp=26)
        # T-6: one of every kind at DIM 32 (they cost simulation time: Icarus takes about 0.06 s per cycle here)
        case(1, 4 * D, D, False, False, 0, False, False, 110)                                                # FP8 non-trans
        case(2, 2 * D, D, True, True, 0, False, False, 120)                                                  # BF16 trans
        case(1, 2 * D, D, True, False, 1, False, False, 120)                                                 # BF16 non-trans ACCUM
        case(1, 16 * D, D, False, True, 0, True, False, 130)                                                 # column scales, K = 512: two fold tiles (two scale rows), 2 passes on each (TR-5: CS_SHIFT 1 vs 2)
        case(2, 4 * D, D, False, True, 2, False, True, 140, ssem=2, sinc=1)                                  # RESCALE with rowscale
        case(1, 4 * D, D + 4, False, True, 0, True, False, 150)                                              # TR2-7: column scales with NT = 2 (the scale column offset of tile j = 1), 2 passes
        return result((0, 0))
    case(0, 4 * D, D, False, True, 0, False, False, 20, ssem=5, sinc=1)                                     # M = 0 from the TD, first in the queue (S-7)
    case(5, 256 + 6 * D + 3, D + D // 2, False, True, 0, True, False, 0, ssem=1, sinc=1)                   # FP8 trans OVERWRITE + cs, 2 fold tiles (2 scale rows)
    case(3, 4 * D, D, False, False, 1, False, False, 2, wsem=1, wval=1)                                     # FP8 non-trans ACCUM onto rows of #1
    case(D // 4 + 1, 128 + 2 * D + 5, D + D // 4, True, True, 2, True, True, 0, ssem=2, sinc=3, pad_a=64)             # BF16 trans RESCALE + cs + rowscale, 2 fold tiles
    case(2 * D + 3, D + 1, D, True, False, 0, False, False, 40)                                              # BF16 non-trans OVERWRITE, many rows
    case(1, 4 * D, 2 * D, False, True, 0, True, False, 100, pad_w=128)                                       # decode-like M = 1
    case(6, 5 * D + 1, D + 3, False, False, 1, False, False, 100, special=True, m_reg=True)                  # specials, M from register, ACCUM
    # RESCALE whose rowscale reaches the result: one K pass (a later pass would add partial sums that swamp the rescaled old value),
    # onto rows an earlier instruction filled with values as large as the partial sums; 3 rowscale lanes, 2 N tiles (rowscale re-read)
    case(2 * (D // 4) + 1, 4 * D, D + 1, False, True, 0, False, False, 140)
    case(2 * (D // 4) + 1, 4 * D, D + 1, False, True, 2, False, True, 140)
    # ---- one case per rule changed by M-10 .. M-18 (rtl follows review S-1, S-7, S-10)
    case(2, 128 + 3, 40, True, True, 0, True, False, 120, ssem=3, sinc=1)                                    # S-1: trans_w = 1, N = 40, cs_reg != 0, 2 scale rows, CSP = 192
    case(0, 4 * D, D, False, True, 1, False, False, 120, m_reg=True, decoy=5, ssem=4, sinc=2)                # S-7: R[m_reg] = 0 (TD n1 is a decoy): a no-op that completes after the case above
    case(3, 200, 16, False, True, 0, True, False, 200, wsem=4, wval=2, cs_end=True, ssem=5, sinc=1)          # S-10: K = 200 is one fold tile (one 64 B scale row, at the very end of the SPM) and 7 / 4 MXU passes; waits for the M = 0 above
    case(0, 2 * D, 2 * D + 1, True, False, 1, True, False, 120, ssem=4, sinc=1)                              # S-7: M = 0 from the TD again, with cs: its checks run, nothing is read
    # ---- R-1 (review part R, M-22): ACCUM instructions back to back (no semaphore wait) on shifted ACC row ranges. The next instruction's
    # row m' lands on the previous pass's row m, with m - m' up to M - 1, and its tile is already loaded when the pass ends: the old
    # gap (ACC_LAT - M, 0 for M >= ACC_LAT) let that row be updated 2 + gap < ACC_LAT cycles after the previous update (tb_mxu's
    # "ACC hazard" check). K = 10 FP8 trans: one short pass per N tile, so the passes are as short as the load allows.
    for Mb, B in ((D + 1, 150), (2 * D + 1, 150), (3 * D, 150)):            # 9 / 17 / 24 rows at DIM 8, 17 / 33 / 48 at DIM 16
        case(Mb, 10, D, False, True, 1, False, False, B)                       # long pass
        case(1, 10, D, False, True, 1, False, False, B + Mb - 1)               # row 0 on the previous last row (m - m' = M - 1)
        case(Mb, 10, D, False, True, 1, False, False, B + Mb - 1)              # again a long pass, row 0 on the previous (M = 1) row
        case(2, 10, D, False, True, 1, False, False, B + Mb - 2)               # M = 2 on rows Mb-2, Mb-1 of the long pass
        case(3, 10, D, False, True, 1, False, False, B + Mb - 4)               # M = 3 starting 2 rows before the previous start
    # T-4 (review part T): an M between 3 and ACC_LAT - 1 (7 = ACC_LAT - 1 at ACC_LAT 8, 23 at the ACC_LAT 24 instance) followed by M = 1
    # on its last row: the boundary gap ACC_LAT - 2 holds for every M >= 2, a gap one cycle short for M = 3 .. ACC_LAT - 1 is a hazard
    for Mb in (3, 7, 23):
        case(Mb, 10, D, False, True, 1, False, False, 150)
        case(1, 10, D, False, True, 1, False, False, 150 + Mb - 1)
    # ---- review part T
    # T-8: BF16 A with Inf / NaN / overflow and an N tail (trans and non-trans): INV / OVF at the MXU port, dead columns masked
    # +Inf and -Inf in one PE column (INV in the sum), max-finite (OVF against W with a large exponent), NaN (NAN_IN); Inf against the zero W of a
    # dead column is INV there, which the column mask must drop (microarch.md: columns < N only)
    inj = {(0, 0): 0x7F80, (0, 2): 0xFF80, (1, 0): 0x7F7F, (2, 1): 0x7FC0, (2, 3): 0x7F80}
    case(4, 2 * D + 3, D + 3, True, True, 0, False, False, 60, special=True, inj=inj)
    case(3, 2 * D + 3, D + 3, True, False, 1, False, False, 60, special=True, inj=inj)
    # M = 1 period of the ideal instances (T-10): 8 passes, bound MAXP[D] (measured max + 0), only tb_mxu instances with CHKP = 1 check it. A 256 B aligned,
    # W 2 KiB aligned (the conflict-free placement of mxu.md 7), so that the bound also holds with the bank models (+BANK=1 / 2, TR-7)
    case(1, 8 * D, 4 * D, False, True, 0, False, False, 70, maxp=MAXP[dim], a_align=256, w_align=2048)
    # T-3: K = n KTILE + 1 (a last K pass of one element) and K = 1, FP8 and BF16, trans and non-trans; T-9: sinc 8 .. 15, ssem 32 .. 63
    case(2, 4 * D + 1, D + 2, False, True, 0, False, False, 160, ssem=40, sinc=9)
    case(2, 2 * D + 1, D, True, False, 0, False, False, 170, ssem=63, sinc=15)
    case(2, 1, D + 1, True, True, 0, False, False, 190, ssem=33, sinc=11)
    case(3, 4 * D + 1, D, True, True, 0, False, False, 195)                                                 # BF16 trans, K = 4 D + 1 (n = 2 tiles of 2 D)
    # T-9: a wait across 2^32. Semaphore 41 starts at 2^32 - 3 (exp.hex row 7); three producers add 2 each (2^32 - 1, 1, 3) and the consumer
    # waits for 2: an unsigned compare starts it at once, the serial-number compare only after the third
    case(1, 1, D, False, True, 0, False, False, 180, ssem=41, sinc=2)
    case(1, 1, D, False, True, 0, False, False, 181, ssem=41, sinc=2)
    case(1, 1, D, False, True, 0, False, False, 182, ssem=41, sinc=2)
    # TR2-5: a wait with wval 0 on the same semaphore: 2^32 - 3 is 3 behind 0 (serial numbers), so it waits for the second producer (-1, then 1)
    case(1, 1, D, False, True, 0, False, False, 183, wsem=41, wval=0, ssem=35, sinc=1)
    case(2, 8 * D + 1, D, False, True, 0, False, False, 185, wsem=41, wval=2, ssem=32, sinc=8)
    # T-2: rows end exactly at ACC_ROWS (NT = 2, M = 3: rows 250 .. 255)
    case(3, 4 * D, 2 * D, False, True, 0, False, False, 250)
    # review part TR
    # TR-2 / TR2-7: an A operand across 0x10000 (the pinned range CARRY_A, FP8 K = 100, 128 B pitch, M = 4: rows at 0xFFC0, 0x10040, 0x100C0, 0x10140).
    # Two different adders carry into bit 16: the pass start a_base + t * 4 DIM (DIM 16: t = 1 starts at 0x10000, DIM 8: t = 2 and 3 at 0x10000 and
    # 0x10020) and the row adder (0xFFC0 + 128 = 0x10040)
    case(4, 100, D, False, True, 0, False, False, 210, a_at=CARRY_A[0], pad_a=0)
    # TR2-7: W lane addresses across 0x20000 (trans, FP8 K = 100, N = 2 DIM: the rows n >= 3 start above 0x20000, the carry goes into bit 17) and
    # across 0x30000 (non-trans, K = 8 DIM k-rows of 64 B: the k-rows >= 5 are above 0x30000, the carry goes into bit 16); free ACC rows 10 .. 13, 14 .. 15
    case(2, 100, 2 * D, False, True, 0, False, False, 10, w_at=W_TR_AT)
    case(2, 8 * D, D, False, False, 0, False, False, 14, w_at=W_NT_AT)
    # TR-5: BF16 column scales with K = 128 n + 1 (two fold tiles: KTF = 2, 129 elements), the scale array ends at the end of the SPM
    case(2, 129, D + 3, True, True, 0, True, False, 215, cs_end=True)
    # TR-5: a wait across 2^31. Semaphore 42 starts at 2^31 - 2; two producers add 1 and 2 (2^31 - 1, 2^31 + 1); the consumer waits for 2^31: a signed
    # compare starts it at once (the testbench's serial-number check fails), the serial-number compare only after the second producer
    case(1, 1, D, False, True, 0, False, False, 220, ssem=42, sinc=1)
    case(1, 1, D, False, True, 0, False, False, 221, ssem=42, sinc=2)
    case(1, 1, D, False, True, 0, False, False, 222, wsem=42, wval=0x80000000, ssem=34, sinc=3)
    # TR2-5: a wait that is met with a lead of 2^31 - 1 (0x80000001 - 2 once both producers are done; 0x7FFFFFFC from the start): no compare on
    # fewer than 32 bits may call it "not yet"
    case(1, 1, D, False, True, 0, False, False, 223, wsem=42, wval=2, ssem=36, sinc=1)
    # several N tiles and K passes: the last pass of the instruction (j = 1, t = 1) is followed by M = 1 on its last row
    case(D + 1, 4 * D + 5, D + 3, False, True, 1, False, False, 150)           # NT = 2, KT = 2
    case(1, 4 * D + 5, D + 3, False, True, 1, False, False, 150 + 2 * (D + 1) - 1)
    case(1, 4 * D + 5, D + 3, False, True, 1, False, False, 150 + 2 * (D + 1) - 1, ssem=5, sinc=1)    # the testbench waits for the last one (semaphore 5)
    # the first increment of semaphore 3 (the case before the M = 0) must come before the first one of semaphore 4
    return result((3, 4), SEM_PRE)


def ideal_row(pre):
    """exp.hex row 12: {ACC_LAT, CS_DEPTH, SPM_LAT, cycles} of the ideal instance of the program (0: none)"""
    n = IDEAL_CYCLES.get(pre, 0)
    if n == 0:
        return 0
    spm_lat, cs_depth, acc_lat = IDEAL_CONF[pre]
    return n | (spm_lat << 32) | (cs_depth << 40) | (acc_lat << 48)


def write_top(tc, counts, outdir=None, pre=None):
    dim = tc["dim"]
    outdir = outdir or OUT
    pre = "mxu_%d_" % dim if pre is None else pre
    def wr(name, lines):
        with open(os.path.join(outdir, pre + name + ".hex"), "w") as fh:
            fh.write("\n".join(lines) + "\n")
        counts.append(((pre + name).upper(), len(lines)))
    wr("spm", ["%02x" % b for b in tc["spm"]])
    wr("dtab", [format(v, "064x") for v in tc["tds"]] + [format(0, "064x")])
    prog = []
    for ins, regs, wval in tc["instrs"]:
        prog.append(hexw([(ins, 128)] + [(r, 64) for r in reversed(regs)] + [(wval, 32)]))
    wr("prog", prog)
    wr("acc0", [format(sum(v << (32 * c) for c, v in enumerate(r)), "0%dx" % (dim * 8)) for r in tc["acc0"]])
    wr("acc1", [format(sum(v << (32 * c) for c, v in enumerate(r)), "0%dx" % (dim * 8)) for r in tc["acc1"]])
    # misc: [flags, sem1 .. sem5, ordering pair {A, B}: the first increment of sem[A] comes before the first one of sem[B] (0: none),
    # semaphore preload {idx, value} (idx 0: none), four SPM windows {base, size} (size 0: unused)]
    # row 12: the cycle count of the ideal instance and its configuration (IDEAL_CYCLES / IDEAL_CONF, 0 = none), row 13: a second semaphore
    # preload (TR-5, TR-7), row 14: the fifth SPM window (TR2-8)
    pres = list(tc["sem_pre"]) + [(0, 0)] * (2 - len(tc["sem_pre"]))
    wins = list(tc["wins"]) + [(0, 0)] * (5 - len(tc["wins"]))
    wr("exp", [format(tc["flags"], "x")] + [format(tc["sem"].get(i, 0), "x") for i in range(1, 6)] +
       [format(tc["order"][0] * 64 + tc["order"][1], "x"), format((pres[0][0] << 32) | pres[0][1], "x")] +
       [format((b << 32) | n, "x") for b, n in wins[:4]] +
       [format(ideal_row(pre), "x"), format((pres[1][0] << 32) | pres[1][1], "x"), format((wins[4][0] << 32) | wins[4][1], "x")])
    # the final value of every semaphore (TR-8: all 64, the untouched ones 0, the preloaded ones preload + increments)
    wr("sem", [format(tc["sem"].get(i, 0), "08x") for i in range(64)])
    # per instruction {maxp, nrows, ssem, sinc} and per ACC row the flags of the PE rows (see the module doc)
    wr("ins", [hexw([(mp, 8), (nr, 24), (ss if si else 0, 6), (si if ss else 0, 4), (0, 22)]) for mp, nr, ss, si in tc["meta"]])
    wr("flg", [format(sum(f << (4 * i) for i, f in enumerate(w)), "0%dx" % dim) for w in tc["waves"]])


def build_fault(dim):
    """Illegal MXU_MM instructions: (instr, regs, wval, td_a, td_w, expected cause, expected FAULT_ADDR). Valid base: FP8 trans M = 2,
    K = 4 DIM, N = DIM, s1 = 64. Every rule of mxu.md 4.1 and the priority of M-18 has a case; the expected address is
    R[reg] of the offending operand (TD_SPACE, alignment, SPM RANGE), R[acc_reg] | 1 << 63 (ACC RANGE), 0 (every other fault)."""
    LB = dim
    SPM = SPM_BYTES[dim]
    ACCR = 256
    B63 = 1 << 63
    BIG = 0x7F00_0000_0000_0000          # a 49-bit-GPA-like value that does not fit 32 bits

    def td(dtype=3, n0=0, n1=0, s1=0, space=1, n2=1, n3=1, flags=0):
        return dtype | (space << 5) | (flags << 8) | (n0 << 16) | (n1 << 32) | (n2 << 48) | (n3 << 64) | (s1 << 80)

    def base(**kw):
        d = dict(op=0x60, accm=0, mode=0, trans=1, rs_idx=0, cs_idx=0, m_idx=0, a_base=0, w_base=1024, acc=0, m=0, rs=0, cs=0, ins_or=0,
                 a=dict(n0=4 * dim, n1=2, s1=64), w=dict(n0=4 * dim, n1=dim, s1=64))
        for k, v in kw.items():
            if k in ("a", "w"):
                d[k] = dict(d[k], **v)
            else:
                d[k] = v
        ins = (d["op"] << 120) | (1 << 86) | (2 << 73) | (3 << 60) | (d["m_idx"] << 55) | (d["rs_idx"] << 50) | (d["cs_idx"] << 45) | \
              (d["accm"] << 43) | (d["mode"] << 40) | (d["trans"] << 39) | d["ins_or"]
        regs = [d["a_base"], d["w_base"], d["acc"], d["m"], d["rs"], d["cs"]]
        return ins, regs, d

    out = []

    def add(cause, addr, a_over=None, w_over=None, **kw):
        ins, regs, d = base(**kw)
        ad, wd = dict(d["a"], **(a_over or {})), dict(d["w"], **(w_over or {}))
        if cause == 0:
            # a legal line: the address field holds the number of ACC rows the instruction must present, N tiles x K tiles x M (TR2-6: the
            # testbench counts them, so a pass that is cut short or an instruction that ends early is seen)
            bf16 = ad.get("dtype", 3) == 1
            kte = (2 if bf16 else 4) * dim
            M_ = d["m"] if d["m_idx"] else ad["n1"]
            N_ = wd["n1"] if d["trans"] else wd["n0"]
            addr = ((N_ + dim - 1) // dim) * ((ad["n0"] + kte - 1) // kte) * M_
        ta = td(**ad)
        tw = td(**wd)
        out.append((ins, regs, 0, ta, tw, cause, addr))
    FIELD, SPACE, RANGE, OP = 2, 3, 7, 1
    # ---- word fields (first; FAULT_ADDR 0)
    add(FIELD, 0, ins_or=1 << 0)                                      # reserved bit 0 of the word
    add(FIELD, 0, ins_or=1 << 38)                                     # reserved bit 38
    add(FIELD, 0, mode=1)                                             # BF16X3 is reserved in v1 (M-15)
    add(FIELD, 0, mode=2)
    add(FIELD, 0, mode=7)
    add(FIELD, 0, accm=3)
    add(FIELD, 0, accm=2)                                             # RESCALE without rs_reg
    add(FIELD, 0, accm=0, rs_idx=5, rs=4096)                          # rs_reg without RESCALE (M-18)
    add(FIELD, 0, accm=1, rs_idx=5, rs=4096)
    add(FIELD, 0, mode=1, a_over=dict(space=2), a_base=BIG + 0x1000)   # a word field beats TD_SPACE
    # ---- TD_SPACE (A before W; address = R[reg], 64 bits)
    add(SPACE, 0, a_over=dict(space=2))
    add(SPACE, 1024, w_over=dict(space=0))
    add(SPACE, BIG + 0x1000, a_over=dict(space=2), a_base=BIG + 0x1000)
    add(SPACE, BIG + 0x2000, w_over=dict(space=0), w_base=BIG + 0x2000)
    add(SPACE, 0x40, a_over=dict(space=2), w_over=dict(space=0), a_base=0x40, w_base=0x80)           # both: A first
    add(SPACE, 0, a_over=dict(space=2, dtype=0), w_over=dict(dtype=1))                                # beats the descriptor rows
    # ---- descriptor ILLEGAL_FIELD that carry no address
    add(FIELD, 0, a_over=dict(dtype=3), w_over=dict(dtype=1))
    add(FIELD, 0, a_over=dict(dtype=0), w_over=dict(dtype=0))
    add(FIELD, 0, w_over=dict(n0=4 * dim + 4))                        # K mismatch
    add(FIELD, 0, a_over=dict(n2=2))
    add(FIELD, 0, w_over=dict(n3=0))
    add(FIELD, 0, a_over=dict(flags=1))
    add(FIELD, 0, a_over=dict(flags=4))                               # WRAP is DMA-only (M-16)
    add(FIELD, 0, w_over=dict(flags=4))
    add(FIELD, 0, a_over=dict(n0=0), w_over=dict(n0=0))               # K = 0
    add(FIELD, 0, w_over=dict(n1=0))                                  # N = 0
    add(FIELD, 0, w_over=dict(n1=0), a_base=LB, acc=ACCR)             # N = 0 beats alignment and RANGE
    add(FIELD, 0, a_over=dict(dtype=3), w_over=dict(dtype=1), a_base=LB)                    # dtype beats alignment
    add(FIELD, 0, a_over=dict(dtype=3), w_over=dict(dtype=1), acc=ACCR)                     # ... and RANGE
    # ---- alignment: 64 B in every config (address = R[reg] of the operand)
    add(FIELD, LB, a_base=LB)                                         # aligned for the lane, not for the bank (M-10)
    add(FIELD, 32, a_base=32)
    add(FIELD, BIG + 8, a_base=BIG + 8)
    add(FIELD, 1024 + LB, w_base=1024 + LB)
    add(FIELD, 0, a_over=dict(s1=64 + LB))                            # s1 of A: R[a_reg] = 0 here
    add(FIELD, 1024, w_over=dict(s1=64 + LB))                         # s1 of W: R[w_reg]
    add(FIELD, 0, a_over=dict(s1=(1 << 31) | 64))                     # negative pitch
    add(FIELD, 1024, w_over=dict(s1=(1 << 31) | 64))
    add(FIELD, 2048 + LB, cs_idx=6, cs=2048 + LB)
    add(FIELD, 4096 + LB, accm=2, rs_idx=5, rs=4096 + LB)
    add(FIELD, LB, a_base=LB, w_base=1024 + LB)                       # A base before W base
    add(FIELD, 1024 + LB, w_base=1024 + LB, a_over=dict(s1=64 + LB))  # W base before s1 of A
    add(FIELD, 0, a_over=dict(s1=64 + LB), w_over=dict(s1=64 + LB))   # s1 of A before s1 of W (R[a_reg] = 0)
    add(FIELD, 2048 + LB, cs_idx=6, cs=2048 + LB, accm=2, rs_idx=5, rs=4096 + LB)   # cs before rs
    add(FIELD, 1024, w_over=dict(s1=64 + LB), cs_idx=6, cs=2048 + LB)               # s1 of W before cs
    # alignment beats RANGE (ACC and SPM), a word field beats both
    add(FIELD, LB, a_base=LB, acc=ACCR)
    add(FIELD, 2048 + LB, cs_idx=6, cs=2048 + LB, w_base=SPM)
    add(FIELD, 0, mode=1, a_base=LB, acc=ACCR)
    # ---- RANGE: ACC first (address | 1 << 63), then the SPM operands A, W, cs, rs (address = R[reg])
    add(RANGE, (ACCR - 1) | B63, acc=ACCR - 1)                        # 2 rows from the last ACC row
    add(RANGE, ACCR | B63, acc=ACCR)
    add(RANGE, (ACCR - 5 * 2 + 1) | B63, acc=ACCR - 5 * 2 + 1, w_over=dict(n1=4 * dim + dim // 2))   # NT * M rows: N = 4 DIM + DIM / 2 (NT = 5, not a multiple of DIM), M = 2: 5 * 2 rows from ACCR - 9 (T-1, T-2)
    add(RANGE, ((1 << 64) - 1) | B63, acc=(1 << 64) - 1)              # the sum must not wrap
    add(RANGE, B63, m_idx=4, m=(1 << 16) + 1)                         # M from the register is not truncated to 16 bits (S-7)
    add(RANGE, B63, m_idx=4, m=1 << 63)
    add(RANGE, B63, m_idx=4, m=ACCR + 1)
    add(RANGE, B63, m_idx=4, m=1 << 63, w_over=dict(n1=2 * dim))      # NT * M = 2^64 wraps to 0 in 64 bits: M alone must fault
    add(RANGE, (ACCR + 1) | B63, m_idx=4, m=0, acc=ACCR + 1)          # M = 0 still checks the ACC base
    add(RANGE, 1024 + SPM, m_idx=4, m=0, w_base=1024 + SPM)                 # M = 0: W is still checked (no A rows, no rowscale)
    add(FIELD, LB, m_idx=4, m=0, a_base=LB)                           # M = 0: alignment is still checked
    add(FIELD, 0, m_idx=4, m=0, mode=1)
    add(RANGE, SPM - 64, a_base=SPM - 64)                             # A: second row ends past the end
    add(RANGE, SPM, a_base=SPM)
    add(RANGE, SPM, w_base=SPM)                                       # base outside
    add(RANGE, BIG, w_base=BIG)
    add(RANGE, 1024, w_over=dict(n1=dim, s1=1 << 20))                 # W rows run off the end
    add(RANGE, SPM - 64, cs_idx=6, cs=SPM - 64, w_over=dict(n1=17))   # 4 N = 68 B of scales from 64 B before the end
    add(RANGE, SPM - 64, cs_idx=6, cs=SPM - 64, a_over=dict(n0=257, s1=320), w_over=dict(n0=257, s1=320))   # second scale row (K tile 257) past the end
    add(RANGE, SPM - 64, accm=2, rs_idx=5, rs=SPM - 64, m_idx=4, m=17)    # 17 rowscale values, 68 B
    add(RANGE, ACCR | B63, acc=ACCR, a_base=SPM)                      # ACC before A
    add(RANGE, SPM, a_base=SPM, w_base=SPM + 64)                      # A before W
    add(RANGE, SPM, w_base=SPM, cs_idx=6, cs=SPM + 64)                # W before cs
    add(RANGE, SPM + 64, cs_idx=6, cs=SPM + 64, accm=2, rs_idx=5, rs=SPM + 128, m_idx=4, m=1)   # cs before rs
    add(RANGE, SPM - 64, a_base=SPM - 64, a_over=dict(dtype=1, n0=48, n1=1, s1=128), w_over=dict(dtype=1, n0=48, s1=128))   # T-2: BF16 K = 48 is 96 B from 64 B before the end (the same in FP8 is legal, below)
    # ---- legal instructions (cause 0: no fault, a context is pushed, every lane inside the SPM) whose extents end exactly at the end of
    # the SPM / the ACC (T-2): the comparators of the checks must not move onto the boundary
    add(0, 0, a_base=SPM - 64, a_over=dict(n0=64, n1=1, s1=64), w_over=dict(n0=64))                    # A: FP8 K = 64, one row ends at SPM
    add(0, 0, a_base=SPM - 64, a_over=dict(n0=48, n1=1, s1=64), w_over=dict(n0=48))                    # A: K = 48 FP8 (96 B in BF16 faults above)
    add(0, 0, a_base=SPM - 128, a_over=dict(n0=64, n1=2, s1=64), w_over=dict(n0=64))                   # A: two rows, the second ends at SPM
    add(0, 0, w_base=SPM - 64 * dim, w_over=dict(n0=64, n1=dim), a_over=dict(n0=64))                   # W trans: dim rows of 64 B end at SPM
    add(0, 0, trans=0, w_base=SPM - 4096, a_over=dict(n0=64), w_over=dict(n0=64, n1=64))               # W non-trans: K = 64 rows x N = 64 B end at SPM
    add(0, 0, accm=2, rs_idx=5, rs=SPM - 64, m_idx=4, m=16)                                            # rowscale: 16 values = 64 B end at SPM
    add(0, 0, cs_idx=6, cs=SPM - 64, w_over=dict(n1=16))                                               # scales: one row of 64 B (N = 16) ends at SPM
    add(0, 0, acc=ACCR - 2)                                                                            # ACC: M = 2, NT = 1: rows ACCR - 2, ACCR - 1
    add(0, 0, acc=ACCR - 5 * 2, w_over=dict(n1=4 * dim + dim // 2))                                    # ACC: NT = 5 (ragged N), M = 2: ends at ACCR
    add(0, 0, m_idx=4, m=ACCR, acc=0, w_over=dict(n1=dim))                                             # ACC: M = ACCR rows, NT = 1
    # ---- review part TR (TR-1): illegal cases just past the boundary (the legal controls end 32 B / 48 B before it) and, since TR-3, every legal
    # case above runs until its context has drained (acc_done driven, acc_row < ACCR and every lane inside the SPM checked)
    wb = SPM - (dim - 1) * 128 - 64                                                  # trans W, dim rows of 96 B at pitch 128: the last row starts 64 B before the end
    add(RANGE, wb, a_over=dict(n0=96, n1=2, s1=128), w_over=dict(n0=96, s1=128), w_base=wb)                                        # FP8: the last row runs 32 B past the end
    add(0, 0, a_over=dict(n0=96, n1=2, s1=128), w_over=dict(n0=96, s1=128), w_base=wb - 64)                                         # control: 32 B before the end
    add(RANGE, wb, a_over=dict(dtype=1, n0=48, n1=2, s1=128), w_over=dict(dtype=1, n0=48, s1=128), w_base=wb)                      # BF16 K = 48: 96 B rows, 32 B past (48 counted as bytes: 16 B before the end)
    add(0, 0, a_over=dict(dtype=1, n0=48, n1=2, s1=128), w_over=dict(dtype=1, n0=48, s1=128), w_base=wb - 64)                       # control
    add(RANGE, SPM - 64, a_base=SPM - 64, a_over=dict(n0=80, n1=1, s1=128), w_over=dict(n0=80, s1=128))                           # A: FP8 K = 80 from 64 B before the end: 16 B past
    add(RANGE, SPM - 64, a_base=SPM - 64, a_over=dict(n0=65, n1=1, s1=128), w_over=dict(n0=65, s1=128))                           # A: 1 B past
    add(0, 0, a_base=SPM - 128, a_over=dict(n0=80, n1=1, s1=128), w_over=dict(n0=80, s1=128))                                       # control: K = 80 ends 48 B before the end
    # BF16 K = 129: two fold tiles (KTF = 2), N = 16: CSP = 64, 4 N = 64 -> 128 B of scales
    add(RANGE, SPM - 64, cs_idx=6, cs=SPM - 64, a_over=dict(dtype=1, n0=129, n1=2, s1=320), w_over=dict(dtype=1, n0=129, n1=16, s1=320))   # 2 scale rows from 64 B before the end
    add(0, 0, cs_idx=6, cs=SPM - 128, a_over=dict(dtype=1, n0=129, n1=2, s1=320), w_over=dict(dtype=1, n0=129, n1=16, s1=320))     # control: they end exactly at the end
    # ---- review part TR2 (TR2-4): the operands and overruns that TR-1 did not reach (each illegal line with its legal control)
    add(RANGE, wb, a_over=dict(n0=65, n1=2, s1=128), w_over=dict(n0=65, s1=128), w_base=wb)                                       # trans FP8 W: K = 65, the last row runs 1 B past the end
    add(0, 0, a_over=dict(n0=64, n1=2, s1=128), w_over=dict(n0=64, s1=128), w_base=wb)                                             # control: K = 64 ends exactly at the end
    nb = SPM - 128 - 64                                                              # non-trans BF16 W: K = 2 rows at pitch 128, the last row starts 64 B before the end
    add(RANGE, nb, trans=0, w_base=nb, a_over=dict(dtype=1, n0=2, n1=2, s1=128), w_over=dict(dtype=1, n0=48, n1=2, s1=128))      # N = 48: 96 B rows, 32 B past (N counted as bytes: 16 B before the end)
    add(0, 0, trans=0, w_base=nb, a_over=dict(dtype=1, n0=2, n1=2, s1=128), w_over=dict(dtype=1, n0=32, n1=2, s1=128))            # control: N = 32, 64 B rows end exactly at the end
    add(RANGE, SPM - 4096 + 64, trans=0, w_base=SPM - 4096 + 64, a_over=dict(n0=64), w_over=dict(n0=64, n1=64))                   # non-trans FP8 W: 64 rows of 64 B, the last one is a whole row past the end
    add(RANGE, SPM - 4096, trans=0, w_base=SPM - 4096, a_over=dict(n0=65, s1=128), w_over=dict(n0=64, n1=65))                      # K = 65 rows: the 65th is past the end
    ab = SPM - 15 * 64                                                               # A with M = 16 from R[m_reg] (the TD's n1 = 1 is a decoy): the 16th row starts at the end
    add(RANGE, ab, m_idx=4, m=16, a_base=ab, a_over=dict(n1=1))
    add(0, 0, m_idx=4, m=16, a_base=SPM - 16 * 64, a_over=dict(n1=1))                                                              # control: the 16th row ends within the SPM
    add(OP, 0)                                                        # patched below
    out[-1] = (out[-1][0] & ~(0xFF << 120) | (0x61 << 120),) + out[-1][1:]
    return out


def write_fault(dim, counts):
    pre = "mxu_fault_%d" % dim
    lines = []
    for ins, regs, wval, ta, tw, cause, addr in build_fault(dim):
        lines.append(hexw([(cause, 4), (addr, 64), (tw, 256), (ta, 256), (ins, 128)] + [(r, 64) for r in reversed(regs)] + [(wval, 32)]))
    with open(os.path.join(OUT, pre + ".hex"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    counts.append((pre.upper(), len(lines)))


# --perf: the M = 1 tile-period cases. (K, N per DIM so that every case has 32..64 passes; FP8, trans W (the group-ordered beat of
# mxu.md 4.3 with the most collisions) and the same pitches for every DIM; the pitch is the row pitch s1 of W in bytes.)
PERF_PITCHES = (2560, 4608, 3072)
PERF_DIMS = (16, 32, 64)


def perf_case(dim, pitch):
    K = {2560: 2048, 4608: 4096, 3072: 3072}[pitch]               # one row = K bytes of FP8 (+ the padding of the pitch rule)
    kt = K // (4 * dim)
    nt = max(1, 32 // kt)                                         # N tiles: at least 32 passes
    return (K, nt * dim, pitch, True, False)


def main_perf():
    for dim in PERF_DIMS:
        for pitch in PERF_PITCHES:
            d = os.path.join(OUT, "d%d_p%d" % (dim, pitch))
            os.makedirs(d, exist_ok=True)
            counts = []
            tc = build_top(dim, perf=perf_case(dim, pitch))
            write_top(tc, counts, outdir=d, pre="")
            with open(os.path.join(d, "counts.svh"), "w") as fh:
                for n, c in counts:
                    fh.write("`define CNT_PERF_%s %d\n" % (n, c))
            print("perf d%d_p%d: K, N, pitch, trans, bf16 = %s, %d passes" % (dim, pitch, perf_case(dim, pitch),
                  (perf_case(dim, pitch)[0] // (4 * dim)) * ((perf_case(dim, pitch)[1] + dim - 1) // dim)))


def main():
    if PERF:
        assert not WRITE_LOCK, "--perf and --lock cannot be combined (the perf vectors are not locked)"
        return main_perf()
    counts = []
    for dim in (8, 16):
        write_fault(dim, counts)
    for dim in (8, 16):
        write_top(build_top(dim), counts)
    write_top(build_top(32), counts)
    for dim in (8, 16):
        write_top(build_top(dim, "sparse"), counts, pre="mxu_%dx_" % dim)
    for dim in (8, 16):
        P, n = build(dim)
        exp, flg = P.expected()
        lines = []
        for k in range(n):
            f = P.cyc.get(k, {})
            wl = f.get("wl")
            a = f.get("a")
            wl_en, wl_tr, wl_g, wl_i, wl_d, wl_b = (1, int(wl[0]), wl[1], wl[2], wl[3], wl[4]) if wl else (0, 0, 0, 0, [0] * WL_WORDS, 0)
            a_v, a_m, a_nc, a_meta, a_w = (1, a[0], a[1], a[2], a[3]) if a else (0, 0, 0, 0, [0] * dim)
            e = exp.get(k)
            yv, ys, ym = (1, e[1], e[2]) if e else (0, [0] * dim, 0)
            fl = [(sum(((w & 0xFFFFFFFF) << (32 * i)) for i, w in enumerate(wl_d)), WL_WORDS * 32)]
            fields = [(flg.get(k, 0), 4), (yv, 1), (ym, META_W),
                      (sum(y << (32 * j) for j, y in enumerate(ys)), dim * 32),
                      (a_meta, META_W), (a_nc, 8), (a_m, 1), (a_v, 1), (f.get("swap", 0), 1), (f.get("swap_buf", 0), 1),
                      (wl_i, 8), (wl_g, 8), (wl_tr, 1), (wl_b, 1), (wl_en, 1)] + fl + \
                     [(sum(w << (32 * i) for i, w in enumerate(a_w)), dim * 32)]
            lines.append(hexw(fields))
        name = "mxu_array_%d" % dim
        with open(os.path.join(OUT, name + ".hex"), "w") as fh:
            fh.write("\n".join(lines) + "\n")
        counts.append((name.upper(), len(lines)))
        print("%s: %d cycles, %d waves, %d flagged cycles" % (name, len(lines), len(P.waves), sum(1 for v in flg.values() if v)))
    with open(os.path.join(OUT, "counts.svh"), "w") as fh:
        for n, c in counts:
            fh.write("`define CNT_%s %d\n" % (n, c))
    if WRITE_LOCK:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import mxu_lock
        mxu_lock.write(OUT)


if __name__ == "__main__":
    main()
