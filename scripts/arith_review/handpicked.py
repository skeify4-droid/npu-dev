"""The 265 hand-derived cases (plus the ACC cases of F3), checked against arith_ref.py and the independent oracle indep.py.

The literals (inputs and the expected bits worked out by hand) live in scripts/arith_hand_cases.py, which imports neither
model. gen_arith_vectors.py folds them into build/arith/*.hex (expected values from arith_ref) and tb/unit/test_arith.py
asserts them against arith_ref; this script is the reviewer's check that the literals also equal the independent oracle.
Usage: python3 scripts/arith_review/handpicked.py     (exit 1 on a disagreement)
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "sw"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, HERE)
from npumodel import arith_ref as R  # noqa: E402
import indep as I  # noqa: E402
from arith_hand_cases import (ACC, ADD, CINT, CVT, DEC, DOT8, DOT16, FOLD, MAX, MUL, MXD, tile_of)  # noqa: E402,F401

bad = []


def cmp(tag, hand, ref, ind):
    if hand != ref or hand != ind:
        bad.append((tag, "hand", hand, "ref", ref, "indep", ind))


def main():
    for a, b, y, f in ADD:
        cmp(("add", hex(a), hex(b)), (y, f), R.fp32_add(a, b), I.fp32_add(a, b))
    for a, b, y, f in MUL:
        cmp(("mul", hex(a), hex(b)), (y, f), R.fp32_mul(a, b), I.fp32_mul(a, b))
    for a, b, y, f in MAX:
        cmp(("max", hex(a), hex(b)), (y, f), R.fp32_max(a, b), I.fp32_max(a, b))
    for a, w, y, f in DOT8:
        cmp(("dot8", a, w), (y, f), R.fp8_dot4(a, w), I.fp8_dot4(a, w))
    for a, w, y, f in DOT16:
        cmp(("dot16", [hex(x) for x in a], [hex(x) for x in w]), (y, f), R.bf16_dot2(a, w), I.bf16_dot2(a, w))
    for fmt, x, y, f in CVT:
        cmp(("cvt", fmt, hex(x)), (y, f), R.cvt_fp32_fp(x, fmt), I.cvt_fp32_fp(x, fmt))
    for bits, sg, x, y, f in CINT:
        cmp(("int", bits, sg, hex(x)), (y, f), R.cvt_fp32_int(x, bits, bool(sg)), I.cvt_fp32_int(x, bits, bool(sg)))
    for fmt, tb, c, y, f in DEC:
        cmp(("dec", fmt, tb, hex(c)), (y, f), R.cvt_dec(c, fmt, bool(tb)), I.cvt_dec(c, fmt, bool(tb)))
    for e, s, el, sc, y, f in MXD:
        cmp(("mxd", e, s, hex(el), hex(sc)), (y, f), R.mx_dequant_bf16(el, sc, e, s), I.mx_dequant_bf16(el, sc, e, s))
    for acc, part, rs, cs, rsc, ucs, ow, y, f in ACC:
        cmp(("acc", hex(acc), hex(part), rsc, ucs, ow), (y, f), R.acc_update(acc, part, rs, cs, rsc, ucs, ow),
            I.acc_update(acc, part, rs, cs, rsc, ucs, ow))
    for spec in FOLD:
        elem, scale, d, dsc, c_h, cs_h, vh = spec
        els, scs = tile_of(spec)
        keys = [R.fold_key(e, s, elem, scale) for e, s in zip(els, scs)]
        c_r = R.fold_c(max(keys))
        cs_r = R.fold_cs(c_r)
        q = [R.fold_quant(e, s, c_r, elem, scale) for e, s in zip(els, scs)]
        amax_i, c_i, cs_i, vs_i, fl_i = I.fold(els, scs, elem, scale)
        tag = ("fold", elem, scale, sorted(d.items())[:2])
        cmp(tag + ("c",), (c_h, cs_h), (c_r, cs_r), (c_i, cs_i))
        for i, (v, f) in vh.items():
            cmp(tag + (i,), (v, f), q[i], (vs_i[i], fl_i[i]))
    n = sum(len(x) for x in (ADD, MUL, MAX, DOT8, DOT16, CVT, CINT, DEC, MXD, ACC, FOLD))
    print(f"hand cases: {n}; disagreements: {len(bad)}")
    for b in bad:
        print("  ", b)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
