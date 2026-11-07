"""Recompute every row of build/arith/*.hex with the independent oracle (indep.py) and report disagreements."""
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(__file__))
import indep as I  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
D = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build", "arith")


def lanes(v, n, bits):
    return [(v >> (bits * i)) & ((1 << bits) - 1) for i in range(n)]


def fields(h, widths):
    v = int(h, 16)
    out = []
    for w in reversed(widths):
        out.append(v & ((1 << w) - 1))
        v >>= w
    return list(reversed(out))


def chk_row(args):
    name, line = args
    if name in ("fp8_dot4", "bf16_dot2"):
        a, w, y, fl = fields(line, [32, 32, 32, 4])
        got = I.fp8_dot4(lanes(a, 4, 8), lanes(w, 4, 8)) if name == "fp8_dot4" else I.bf16_dot2(lanes(a, 2, 16), lanes(w, 2, 16))
        return None if got == (y, fl) else (line, got)
    if name in ("fp32_add", "fp32_mul", "fp32_max"):
        a, b, y, fl = fields(line, [32, 32, 32, 4])
        got = getattr(I, name)(a, b)
        return None if got == (y, fl) else (line, got)
    if name == "mxu_pe_dot":
        m, a, w, d, fl = fields(line, [1, 32, 32, 32, 4])
        got = I.bf16_dot2(lanes(a, 2, 16), lanes(w, 2, 16)) if m else I.fp8_dot4(lanes(a, 4, 8), lanes(w, 4, 8))
        return None if got == (d, fl) else (line, got)
    if name == "mxu_pe":
        m, a, w, ps, y, fl = fields(line, [1, 32, 32, 32, 32, 4])
        d, fd = I.bf16_dot2(lanes(a, 2, 16), lanes(w, 2, 16)) if m else I.fp8_dot4(lanes(a, 4, 8), lanes(w, 4, 8))
        yy, fa = I.fp32_add(ps, d)
        f = (fd & I.NANIN) | (fd & (I.OVF | I.INV)) | (fa & (I.OVF | I.INV))
        return None if (yy, f) == (y, fl) else (line, (yy, f))
    if name == "acc_update":
        acc, part, rs, cs, r, u, o, y, fl = fields(line, [32, 32, 32, 32, 1, 1, 1, 32, 4])
        got = I.acc_update(acc, part, rs, cs, r, u, o)
        return None if got == (y, fl) else (line, got)
    if name.startswith("cvt_fp32_") and name[9:].upper() in I.FMTS:
        x, y, fl = fields(line, [32, 16, 4])
        got = I.cvt_fp32_fp(x, name[9:].upper())
        return None if got == (y, fl) else (line, got)
    if name.startswith("cvt_fp32_"):
        t = name[9:]
        bits = int(t.lstrip("uint"))
        x, y, fl = fields(line, [32, 32, 4])
        got = I.cvt_fp32_int(x, bits, not t.startswith("u"))
        return None if got == (y, fl) else (line, got)
    if name.startswith("cvt_dec_"):
        _, _, fmt, dst = name.split("_")
        c, y, fl = fields(line, [16, 32, 4])
        got = I.cvt_dec(c, fmt.upper(), dst == "bf16")
        return None if got == (y, fl) else (line, got)
    if name.startswith("mx_dequant_"):
        es = {"mxfp4": (0, 0), "mxfp8": (1, 0), "nvfp4": (0, 1)}[name[11:]]
        el, sc, y, fl = fields(line, [8, 8, 16, 4])
        got = I.mx_dequant_bf16(el, sc, *es)
        return None if got == (y, fl) else (line, got)
    if name.startswith("fold_") or name.startswith("fold256_"):
        N = 256 if name.startswith("fold256_") else 64
        es = {"mxfp4": (0, 0), "mxfp8": (1, 0), "nvfp4": (0, 1)}[name.split("_", 1)[1]]
        f = fields(line, [8] * N + [8] * N + [17, 10, 32] + [8] * N + [4] * N)
        els = list(reversed(f[0:N]))
        scs = list(reversed(f[N:2 * N]))
        c_exp = f[2 * N + 1]
        c_exp = c_exp - 1024 if c_exp >= 512 else c_exp
        cs_exp = f[2 * N + 2]
        v_exp = list(reversed(f[2 * N + 3:3 * N + 3]))
        fl_exp = list(reversed(f[3 * N + 3:4 * N + 3]))
        amax, c, cs, vs, fl = I.fold(els, scs, *es)
        ok = c == c_exp and cs == cs_exp and vs == v_exp and fl == fl_exp
        return None if ok else (line[:40], (c, c_exp, cs, cs_exp, [i for i in range(N) if vs[i] != v_exp[i] or fl[i] != fl_exp[i]]))
    if name.startswith("foldq_"):               # fold_quant alone: el sc c v flags (c is 10-bit two's complement)
        es = {"mxfp4": (0, 0), "mxfp8": (1, 0), "nvfp4": (0, 1)}[name[6:]]
        el, sc, c, v, fl = fields(line, [8, 8, 10, 8, 4])
        c = c - 1024 if c >= 512 else c
        val = I.mx_val(el, sc, *es)
        if val[0] == "nan":
            got = (0x7F, I.NANIN)
        else:
            code, _, sa = I.rne(val[1], val[2] / I.F(2) ** c, I.E4M3, sat=True)
            got = (code, I.SAT if sa else 0)
        return None if got == (v, fl) else (line, got)
    if name == "fp8_mul":
        a, b, neg, nan, mag = fields(line, [8, 8, 1, 1, 36])
        A, B = I.decode(a, I.E4M3), I.decode(b, I.E4M3)
        if A[0] == "nan" or B[0] == "nan":
            return None if (nan == 1 and neg == ((a ^ b) >> 7 & 1)) else (line, "nan")
        p = A[2] * B[2] * 2 ** 18
        return None if (nan == 0 and neg == A[1] ^ B[1] and p == mag) else (line, p)
    return ("?", name)


def main():
    names = sorted(f[:-4] for f in os.listdir(D) if f.endswith(".hex"))
    if len(sys.argv) > 2:
        names = [n for n in names if n in sys.argv[2:]]
    tot_bad = 0
    with Pool(4) as pool:
        for n in names:
            rows = [l.strip() for l in open(os.path.join(D, n + ".hex")) if l.strip()]
            res = pool.map(chk_row, [(n, r) for r in rows], chunksize=2000)
            bad = [r for r in res if r is not None]
            tot_bad += len(bad)
            print(f"{n:24s} rows {len(rows):7d}  disagree {len(bad)}", flush=True)
            for b in bad[:5]:
                print("   ", b)
    print("TOTAL disagreements:", tot_bad)


if __name__ == "__main__":
    main()
