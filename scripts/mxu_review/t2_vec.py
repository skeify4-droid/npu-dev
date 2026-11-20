#!/usr/bin/env python3
"""Review part TR2: variant vector directories made by a patched copy of scripts/gen_mxu_vectors.py (the author's file is not edited;
the copy is written to build/mxu_review_t2/gen/<variant>.py and run into build/mxu_review_t2/vec/<variant>/). Variants:

  shift<S>     S bytes of A space are allocated just before the M = 1 period instruction of the dense programs (T-10 / TR-7), so its
               A operand (256 B aligned) and possibly its W (2 KiB aligned) move: other placements that follow the mxu.md 7 rules
  wpad<P>      that instruction's W pitch padded by P bytes (pad_w), e.g. 3008 / 1984 so that the pitch is a mxu.md 7 table value
  shift<S>_wpad<P>  both
  a5           a fifth sparse window (second A window at 0x1E0000 / 0x2C0000 style: A address bits 20 != 21) for the TR survivor
               tr_a_swap2021: see the A5 constants below; needs tb_mxu with 5 windows (t2_tb.py --tbm win5)

Usage: python3 scripts/mxu_review/t2_vec.py VARIANT [...]
"""
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GEN = os.path.join(ROOT, "scripts", "gen_mxu_vectors.py")
WORK = os.path.join(ROOT, "build", "mxu_review_t2")
CHKP_LINE = "    case(1, 8 * D, 4 * D, False, True, 0, False, False, 70, maxp=MAXP[dim], a_align=256, w_align=2048)\n"


def patch(src, old, new):
    assert src.count(old) == 1, old[:80]
    return src.replace(old, new)


def variant_src(v):
    src = open(GEN).read()
    src = patch(src, 'ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))',
                'ROOT = %r' % ROOT)
    if v == "big":
        # three more dense instructions (TR2-6): non-trans FP8 with NT = 9, K tiles 18 (K = 68 DIM + 1), M = 130 (> 128). The cycle count
        # changes, so run without CHKC
        anchor = "    # several N tiles and K passes: the last pass of the instruction (j = 1, t = 1) is followed by M = 1 on its last row\n"
        return patch(src, anchor,
                     "    case(2, 4 * D, 8 * D + 3, False, False, 0, False, False, 0)\n"
                     "    case(1, 17 * 4 * D + 1, D, False, True, 0, False, False, 30)\n"
                     "    case(130, 4 * D, D, False, True, 0, False, False, 100)\n" + anchor)
    m = re.fullmatch(r"(?:shift(\d+))?_?(?:wpad(\d+))?", v)
    if v == "a5":
        # second A window with bits 20 / 21 different (0x1C0000: b21 = 0, b20 = 1, b19 = 1, b18 = 1); the sparse cases 0 .. 3 keep A in
        # window "a", cases 4 .. 7 take it from "a2" (one more exp.hex row: 15 rows, row 14 = window 5)
        src = patch(src, 'SPARSE_WIN = {"a": (0x3E0000, 1 << 16),', 'SPARSE_WIN = {"a": (0x3E0000, 1 << 16), "a2": (0x1C0000, 1 << 16),')
        src = patch(src, '        cur = {k: [b, b, b + n] for k, (b, n) in wins.items()}',
                    '        cur = {k: [b, b, b + n] for k, (b, n) in wins.items()}\n        a5_n = [0]')
        src = patch(src, '        a_base = alloc(M * a_pitch, align=a_align, kind="a") if a_at is None',
                    '        a5_k = "a"\n        if sparse:\n            a5_n[0] += 1\n            a5_k = "a2" if a5_n[0] > 4 else "a"\n'
                    '        a_base = alloc(M * a_pitch, align=a_align, kind=a5_k) if a_at is None')
        src = patch(src, '    wins = list(tc["wins"]) + [(0, 0)] * (4 - len(tc["wins"]))', '    wins = list(tc["wins"]) + [(0, 0)] * (5 - len(tc["wins"]))')
        src = patch(src, '       [format((b << 32) | n, "x") for b, n in wins] +\n       [format(IDEAL_CYCLES.get(pre, 0), "x"), format((pres[1][0] << 32) | pres[1][1], "x")])',
                    '       [format((b << 32) | n, "x") for b, n in wins[:4]] +\n       [format(IDEAL_CYCLES.get(pre, 0), "x"), format((pres[1][0] << 32) | pres[1][1], "x")] +\n'
                    '       [format((b << 32) | n, "x") for b, n in wins[4:]])')
        return src
    assert m and v, v
    s, p = m.group(1), m.group(2)
    new = CHKP_LINE
    if p:
        new = new.replace("w_align=2048)", "w_align=2048, pad_w=%s)" % p)
    if s:
        new = "    alloc(%s, kind=\"a\")\n" % s + new
    return patch(src, CHKP_LINE, new)


def main():
    for v in sys.argv[1:]:
        os.makedirs(os.path.join(WORK, "gen"), exist_ok=True)
        g = os.path.join(WORK, "gen", v + ".py")
        open(g, "w").write(variant_src(v))
        out = os.path.join(WORK, "vec", v)
        os.makedirs(out, exist_ok=True)
        r = subprocess.run([sys.executable, g, out], capture_output=True, text=True)
        print("VEC %s rc=%d %s" % (v, r.returncode, (r.stderr.strip().splitlines() or [""])[-1][:200]))


if __name__ == "__main__":
    main()
