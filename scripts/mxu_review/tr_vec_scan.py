#!/usr/bin/env python3
"""Review part TR (independent review of the part T test changes, commit 914ee30): decode the tb_mxu programs of a vector directory
and report what their operands reach (no import of the author's generator or of arith_ref; field positions from isa/gen/isa_defs.py).

For every instruction: A / W / column-scale / rowscale extents [start, end) in the SPM, the ACC rows [acc, acc + NT*M), and the
carry events the address and row counters of mxu.sv see:
  - an A row address (R[a] + m*s1 + t*KB) or a W lane address whose bits 16+ / 17+ change inside one operand (a carry out of bit 15 / 16),
  - an ACC row of the stream that crosses a multiple of 256 inside a pass (a carry from acc_row bit 7 into bit 8), or a pass start
    acc_base + j*M with bits 8+ different from acc_base.
Then per port the set of values each SPM address bit 16 .. 21 takes, which tells a stuck-at or swapped bit from a covered one.

Usage: tr_vec_scan.py [VECDIR] [PREFIX ...]      (default build/mxu, prefixes mxu_8_ mxu_16_ mxu_32_ mxu_8x_ mxu_16x_)
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "isa", "gen"))
import isa_defs as I  # noqa: E402


def fld(v, msb, lsb):
    return (v >> lsb) & ((1 << (msb - lsb + 1)) - 1)


def rows(path):
    return [int(l, 16) for l in open(path).read().split()]


def tfield(td, name):
    msb, lsb = I.TD_FIELDS[name]
    return fld(td, msb, lsb)


def scan(vec, pre):
    prog = rows(os.path.join(vec, pre + "prog.hex"))
    dtab = rows(os.path.join(vec, pre + "dtab.hex"))
    dim = {"8": 8, "16": 16, "32": 32}[pre.split("_")[1].rstrip("x")]
    KBY = 4 * dim
    out = []
    bits = {p: {b: set() for b in range(16, 22)} for p in ("A", "W", "S")}
    ev = []
    for i, v in enumerate(prog):
        wval = v & 0xFFFFFFFF
        regs = (v >> 32) & ((1 << 384) - 1)
        r = [(regs >> (64 * k)) & ((1 << 64) - 1) for k in range(6)]     # a, w, acc, m, rs, cs
        ins = v >> 416
        g = lambda n: fld(ins, *SYNC_OR_T[n])                              # noqa: E731
        trans, accm, a_td, w_td = g("trans_w"), g("acc_mode"), g("a_td"), g("w_td")
        m_en, rs_en, cs_en = g("m_reg") != 0, g("rs_reg") != 0, g("cs_reg") != 0
        ta, tw = dtab[a_td], dtab[w_td]
        bf16 = tfield(ta, "dtype") == 1                         # E4M3 = 3, BF16 = 1 (isa.yaml dtype enum)
        eb = 2 if bf16 else 1
        K = tfield(ta, "n0")
        M = r[3] if m_en else tfield(ta, "n1")
        N = tfield(tw, "n1") if trans else tfield(tw, "n0")
        sa, sw = tfield(ta, "s1"), tfield(tw, "s1")
        NT = (N + dim - 1) // dim
        KB = 4 * dim                                           # bytes of a K tile (FP8 4 DIM, BF16 2 DIM elements)
        KT = (K * eb + KB - 1) // KB
        rec = dict(i=i, M=M, K=K, N=N, bf16=bf16, trans=trans, accm=accm, NT=NT, KT=KT, ssem=g("ssem"), sinc=g("sinc"),
                   wsem=g("wsem"), wval=wval, acc=r[2])
        if M:
            a0, a1 = r[0], r[0] + (M - 1) * sa + K * eb
            rec["A"] = (a0, a1)
            for m in range(M):
                for t in range(KT):
                    ad = r[0] + m * sa + t * KB
                    for b in range(16, 22):
                        bits["A"][b].add((ad >> b) & 1)
            for b in (16, 17):
                if (a0 >> b) != ((r[0] + (M - 1) * sa + (KT - 1) * KB) >> b):
                    ev.append("%s#%d: A row addresses cross a 2^%d boundary (%x .. %x)" % (pre, i, b, a0, a1))
        if trans:
            w0, w1 = r[1], r[1] + (N - 1) * sw + K * eb
        else:
            w0, w1 = r[1], r[1] + (K - 1) * sw + N * eb
        rec["W"] = (w0, w1)
        for ad in range(w0 - w0 % dim, w1, dim):
            for b in range(16, 22):
                bits["W"][b].add((ad >> b) & 1)
        for b in (16, 17):
            if (w0 >> b) != ((w1 - 1) >> b):
                ev.append("%s#%d: W extent crosses a 2^%d boundary (%x .. %x)" % (pre, i, b, w0, w1))
        if cs_en:
            KTF = (K + (127 if bf16 else 255)) // (128 if bf16 else 256)
            csp = (4 * N + 63) // 64 * 64
            c0, c1 = r[5], r[5] + (KTF - 1) * csp + 4 * N
            rec["CS"] = (c0, c1)
            for ad in range(c0, c1, dim):
                for b in range(16, 22):
                    bits["S"][b].add((ad >> b) & 1)
        if rs_en and M:
            rec["RS"] = (r[4], r[4] + 4 * M)
            for ad in range(r[4], r[4] + 4 * M, dim):
                for b in range(16, 22):
                    bits["S"][b].add((ad >> b) & 1)
        if M:
            for j in range(NT):
                st = r[2] + j * M
                if (st >> 8) != (r[2] >> 8):
                    ev.append("%s#%d: pass start acc_base + j*M = %d has bits 8+ different from acc_base %d" % (pre, i, st, r[2]))
                if (st >> 8) != ((st + M - 1) >> 8):
                    ev.append("%s#%d: ACC rows %d .. %d of N tile %d cross a multiple of 256 inside a pass" % (pre, i, st, st + M - 1, j))
        out.append(rec)
    return dim, out, bits, ev


SYNC_OR_T = dict(I.SYNC, **{k: v for k, v in [("trans_w", [39, 39]), ("acc_mode", [44, 43]), ("a_td", [85, 78]), ("w_td", [72, 65]),
                                                ("m_reg", [59, 55]), ("rs_reg", [54, 50]), ("cs_reg", [49, 45])]})


def main():
    args = sys.argv[1:]
    vec = args[0] if args and os.path.isdir(args[0]) else os.path.join(ROOT, "build", "mxu")
    pres = [a for a in args if not os.path.isdir(a)] or ["mxu_8_", "mxu_16_", "mxu_32_", "mxu_8x_", "mxu_16x_"]
    for pre in pres:
        if not os.path.exists(os.path.join(vec, pre + "prog.hex")):
            continue
        dim, recs, bits, ev = scan(vec, pre)
        print("== %s (DIM %d, %d instructions)" % (pre, dim, len(recs)))
        for r in recs:
            ext = "  ".join("%s %x..%x" % (k, r[k][0], r[k][1]) for k in ("A", "W", "CS", "RS") if k in r)
            print("  #%-2d M %-4d K %-4d N %-4d %s t%d accm %d NT %d KT %d acc %d..%d ssem %d sinc %d wsem %d  %s" % (
                r["i"], r["M"], r["K"], r["N"], "bf16" if r["bf16"] else "fp8 ", r["trans"], r["accm"], r["NT"], r["KT"], r["acc"],
                r["acc"] + r["NT"] * r["M"], r["ssem"], r["sinc"], r["wsem"], ext))
        for p in ("A", "W", "S"):
            print("  port %s address bits 16..21 take: %s" % (p, " ".join("b%d=%s" % (b, "".join(str(x) for x in sorted(bits[p][b])) or "-")
                                                                       for b in range(16, 22))))
        for e in ev:
            print("  " + e)
        if not ev:
            print("  no carry events (2^16 / 2^17 address boundaries, multiples of 256 ACC rows)")


if __name__ == "__main__":
    main()
