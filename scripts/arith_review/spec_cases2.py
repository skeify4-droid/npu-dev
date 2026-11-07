"""Second review (51f8cbe), part 3: hand-worked cases of the numerics.md rules that 51f8cbe changed (N-2, N-3, N-5, N-6 and
the principles), derived from the spec text alone (the expected literals were written before arith_ref.py or rtl/arith was
consulted for them). Each case is then
  - looked up in scripts/arith_hand_cases.py (same inputs present? same expected bits?),
  - evaluated with arith_ref (called as a black box) and with the first review's oracle indep.py.
Where the text admits two readings, `alt` holds the other one; the case is reported as AMBIGUOUS with both values.

At the fixes of arith_review2.md the ambiguous rules A1-A7 were decided (docs/spec/numerics.md N-2, N-5, N-6 and the
integration contract in docs/spec/arith.md state them), so the cases that carried an `alt` are agreed cases now: the
expected value is the decision and `alt` is None (the old reading is kept in the note). The report's count was 70 agreeing and
7 ambiguous over 77 cases; it is 78 agreeing and 0 ambiguous now (the A3 case became two). A7 (whose FP_STATUS a remote write-add sets) is a core-level rule and has no case here.

Usage: python3 scripts/arith_review/spec_cases2.py
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "sw"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from npumodel import arith_ref as R  # noqa: E402
import arith_hand_cases as H  # noqa: E402
import indep as I  # noqa: E402

INV, OVF, SAT, NANIN = 1, 2, 4, 8
NAN = 0x7FC00000

# (rule, block, args, expected (y, flags), alt or None, note)
C = [
    # R1  N-2 / N-3: FP16 -> BF16 is RNE (11 -> 8 significand bits), cannot overflow, FP16 subnormals kept
    ("R1 FP16->BF16", "dec", ("FP16", 1, 0x3C01), (0x3F80, 0), None, "1+2^-10: below half ulp"),
    ("R1 FP16->BF16", "dec", ("FP16", 1, 0x3C04), (0x3F80, 0), None, "1+2^-8: tie, even"),
    ("R1 FP16->BF16", "dec", ("FP16", 1, 0x3C0C), (0x3F82, 0), None, "1+3*2^-8: tie, odd -> up"),
    ("R1 FP16->BF16", "dec", ("FP16", 1, 0x7BFF), (0x4780, 0), None, "65504 -> 2^16, no OVERFLOW"),
    ("R1 FP16->BF16", "dec", ("FP16", 1, 0x03FF), (0x3880, 0), None, "largest FP16 subnormal (10 bits) rounds up to 2^-14"),
    ("R1 FP16->BF16", "dec", ("FP16", 1, 0x0001), (0x3380, 0), None, "2^-24 kept (a BF16 normal)"),
    # R2  N-2 / N-5: FP32 -> BF16 keeps subnormals, grid 2^-133; FP32 subnormal inputs are converted
    ("R2 FP32->BF16 subn", "cvt", ("BF16", 0x00080000), (0x0008, 0), None, "2^-130 = 8 * 2^-133"),
    ("R2 FP32->BF16 subn", "cvt", ("BF16", 0x00010000), (0x0001, 0), None, "2^-133, min BF16 subnormal"),
    ("R2 FP32->BF16 subn", "cvt", ("BF16", 0x00008000), (0x0000, 0), None, "2^-134: tie between 0 and 1, even 0"),
    ("R2 FP32->BF16 subn", "cvt", ("BF16", 0x80018000), (0x8002, 0), None, "-1.5 * 2^-133: tie -> 2"),
    ("R2 FP32->BF16 subn", "cvt", ("BF16", 0x007FFFFF), (0x0080, 0), None, "2^-126 - 2^-149 rounds up to the min normal"),
    ("R2 FP32->BF16 subn", "cvt", ("BF16", 0x00400000), (0x0040, 0), None, "2^-127"),
    # R3  principles: FTZ is round, then flush
    ("R3 round-then-flush", "mul", (0x1FFFFFFF, 0x20000000), (0x00800000, 0), None, "the spec's example: 2^-126 - 2^-150 -> 2^-126"),
    ("R3 round-then-flush", "mul", (0x1FFFFFFE, 0x20000000), (0x00000000, 0), None, "2^-126 - 2^-149: exact subnormal -> +0"),
    ("R3 round-then-flush", "mul", (0x9FFFFFFF, 0x20000000), (0x80800000, 0), None, "negative: -2^-126"),
    ("R3 round-then-flush", "mul", (0x9FFFFFFE, 0x20000000), (0x80000000, 0), None, "negative exact subnormal -> -0"),
    ("R3 round-then-flush", "add", (0x00800001, 0x80800000), (0x00000000, 0), None, "+2^-149 -> +0"),
    ("R3 round-then-flush", "mul", (0x3F000000, 0x00800000), (0x00000000, 0), None, "0.5 * 2^-126 = 2^-127 -> +0"),
    # R4  N-2 `sat`: RNE with unbounded exponent, then above max -> max with SATURATE; Inf -> max without
    ("R4 sat", "cvt", ("E4M3", 0x43E80000), (0x7E, 0), None, "464: tie, even code 448, no flag"),
    ("R4 sat", "cvt", ("E4M3", 0x43E80001), (0x7E, SAT), None, "464+: rounds to 480 > max"),
    ("R4 sat", "cvt", ("E4M3", 0x7F800000), (0x7E, 0), None, "+Inf -> max, no flag"),
    ("R4 sat", "cvt", ("E5M2", 0x47700000), (0x7B, SAT), None, "61440: tie, even is 65536 > max -> SATURATE (unlike E4M3)"),
    ("R4 sat", "cvt", ("E5M2", 0x476FFFFF), (0x7B, 0), None, "just below the E5M2 tie"),
    ("R4 sat", "cvt", ("E5M2", 0xFF800000), (0xFB, 0), None, "-Inf -> -max, no flag"),
    ("R4 sat", "cvt", ("E2M1", 0x40E00000), (0x7, SAT), None, "7: tie, even is 8 > 6 -> SATURATE"),
    ("R4 sat", "cvt", ("E2M1", 0x40D00000), (0x7, 0), None, "6.5 -> 6"),
    ("R4 sat", "cvt", ("E2M1", 0x40A00000), (0x6, 0), None, "5: tie 4 / 6 -> 4"),
    # R5  N-2 FP32 -> INT: RNE, clamp; SATURATE for a clamped finite input; Inf clamps silently
    ("R5 int", "int", (8, True, 0x42FF0000), (0x7F, SAT), None, "127.5 -> 128 -> 127"),
    ("R5 int", "int", (8, True, 0xC3008000), (0x80, 0), None, "-128.5 -> -128 (even), no clamp"),
    ("R5 int", "int", (8, False, 0xBF000000), (0x00, 0), None, "-0.5 -> -0 -> 0, not a clamp"),
    ("R5 int", "int", (8, False, 0xBF028F5C), (0x00, SAT), None, "-0.51 -> -1 -> clamp 0"),
    ("R5 int", "int", (8, False, 0x437F8000), (0xFF, SAT), None, "255.5 -> 256 -> 255"),
    ("R5 int", "int", (32, True, 0x4F000000), (0x7FFFFFFF, SAT), None, "2^31"),
    ("R5 int", "int", (8, True, 0x7F800000), (0x7F, 0), None, "+Inf, no flag"),
    ("R5 int", "int", (8, True, 0x7FC00000), (0x00, NANIN), None, "A6: NaN -> 0 with NAN_IN (N-2 now names it; before: no flag named)"),
    # R6  N-2 fold step 1 (256-element MXFP8 / MXFP4 tiles): NaN out of amax, NaN-scale blocks, all-NaN tile, padding
    ("R6 fold", "fold", (1, 0, {0: (0x7F, 127)}, 127), (0, 0x3F800000, {0: (0x7F, NANIN), 1: (0, 0)}), None, "NaN and zeros only: c = 0"),
    ("R6 fold", "fold", (1, 0, {0: (0x7F, 127), 1: (0x7E, 128)}, 127), (1, 0x40000000, {0: (0x7F, NANIN), 1: (0x7E, 0)}), None, "NaN + 896"),
    ("R6 fold", "fold", (1, 0, {**{i: (0x38, 0xFF) for i in range(32)}, **{i: (0x38, 127) for i in range(32, 64)}}, 127),
     (-8, 0x3B800000, {0: (0x7F, NANIN), 31: (0x7F, NANIN), 32: (0x78, 0)}), None, "NaN-scale block of 1.0s, then 1.0s"),
    ("R6 fold", "fold", (1, 0, {0: (0x00, 0xFF), 32: (0x38, 127)}, 127), (-8, 0x3B800000, {0: (0x7F, NANIN), 32: (0x78, 0)}), None,
     "a ZERO element of a NaN-scale block is written NaN"),
    ("R6 fold", "fold", (1, 0, {99: (0x7E, 127), **{i: (0x00, 127) for i in range(100, 256)}}, 127),
     (0, 0x3F800000, {99: (0x7E, 0), 100: (0x00, 0), 255: (0x00, 0)}), None,
     "A3: short tile (100 elements) padded with element 0 and a finite scale (E8M0 127): the padding is a clean zero"),
    ("R6 fold", "fold", (1, 0, {99: (0x7E, 127), **{i: (0x00, 0xFF) for i in range(100, 256)}}, 127),
     (0, 0x3F800000, {99: (0x7E, 0), 100: (0x7F, NANIN), 255: (0x7F, NANIN)}), None,
     "A3 (why the scale must be finite): padding with a NaN (0xFF) scale is written NaN with NAN_IN"),
    ("R6 fold", "fold", (0, 0, {0: (0x7, 127)}, 127), (-6, 0x3C800000, {0: (0x7C, 0)}), None, "MXFP4 amax 6 -> c = -6, 384"),
    # R7  N-2 fold step 4: NVFP4 cs = fp32_mul(2^c, T) (RNE, DAZ / FTZ) in the DMA engine
    ("R7 nvfp4 cs", "mul", (0x41000000, 0x3F800000), (0x41000000, 0), None, "c = 3, T = 1"),
    ("R7 nvfp4 cs", "mul", (0x36800000, 0x08800000), (0x00000000, 0), None, "c = -18, T = 2^-110: 2^-128 flushed, the tile's column is 0"),
    ("R7 nvfp4 cs", "mul", (0x41000000, 0x7F7FFFFF), (0x7F800000, OVF), None, "c = 3, T = max: Inf (it never 'rounds')"),
    ("R7 nvfp4 cs", "mul", (0x40000000, 0x00400000), (0x00000000, 0), None, "T subnormal: DAZ"),
    ("R7 nvfp4 cs", "mul", (0x40000000, 0xBF800000), (0xC0000000, 0), None, "negative T"),
    ("R7 nvfp4 cs", "mul", (0x40000000, 0x7FC00001), (NAN, NANIN), None, "NaN T"),
    # R8  N-5 INVALID: not set when the same operation also has a NaN input
    ("R8 inv-supp", "dot16", ([0x7FC0, 0x7F80], [0x3F80, 0x0000]), (NAN, NANIN), None, "the spec's example"),
    ("R8 inv-supp", "dot16", ([0x7F80, 0x3F80], [0x0000, 0x3F80]), (NAN, INV), None, "Inf x 0 alone"),
    ("R8 inv-supp", "add", (0x7F800000, 0xFF800000), (NAN, INV), None, "Inf - Inf"),
    ("R8 inv-supp", "mul", (0x7FC00000, 0x00000000), (NAN, NANIN), None, "NaN x 0: NAN_IN only"),
    ("R8 inv-supp", "acc", (0x7FC00000, 0x7F800000, 0x3F800000, 0x00000000, 0, 1, 0), (NAN, INV | NANIN), None,
     "A1: ACC lane, NaN acc + (Inf x 0 in cs): an operation is one multiply or add, so INVALID stays (the 'one unit' reading, NAN_IN only, is rejected)"),
    ("R8 inv-supp", "acc", (0x7F800000, 0x7FC00000, 0x00000000, 0x3F800000, 1, 0, 0), (NAN, INV | NANIN), None,
     "A1: ACC lane, (Inf x 0 rowscale) + NaN part"),
    # R9  N-5 NAN_IN: a NaN among the external operands; a NaN made inside is INVALID only
    ("R9 nan-in", "acc", (0x3F800000, 0x7F800000, 0x3F800000, 0x00000000, 0, 1, 0), (NAN, INV), None, "Inf x 0 in cs: INVALID only"),
    ("R9 nan-in", "acc", (0x7F800000, 0x3F800000, 0x00000000, 0x3F800000, 1, 0, 0), (NAN, INV), None, "Inf x 0 in rowscale"),
    ("R9 nan-in", "acc", (0x7FC00001, 0x3F800000, 0x3F800000, 0x3F800000, 0, 0, 0), (NAN, NANIN), None, "NaN acc accumulated"),
    ("R9 nan-in", "acc", (0x3F800000, 0x7FC00001, 0x3F800000, 0x3F800000, 0, 0, 1), (0x7FC00001, 0), None,
     "A2: OVERWRITE copies a NaN part (no cs): no arithmetic consumes it, so no NAN_IN (the PE column reported it)"),
    ("R9 nan-in", "acc", (0x3F800000, 0x7FC00001, 0x3F800000, 0x40000000, 0, 1, 1), (NAN, NANIN), None, "OVERWRITE, NaN part scaled"),
    ("R9 nan-in", "acc", (0x7FC00000, 0x3F800000, 0x3F800000, 0x3F800000, 1, 0, 0), (NAN, NANIN), None, "NaN acc rescaled"),
    ("R9 nan-in", "dot8", ([0x7F, 0, 0, 0], [0x38, 0, 0, 0]), (NAN, NANIN), None, "E4M3 NaN operand of the PE"),
    # R10 N-5 MAX_F32: canonical NaN, DAZ, max(-0, +0) = +0
    ("R10 max", "max", (0x7FC00001, 0x3F800000), (NAN, NANIN), None, "NaN -> canonical"),
    ("R10 max", "max", (0x3F800000, 0xFF800001), (NAN, NANIN), None, "-NaN -> +canonical"),
    ("R10 max", "max", (0x80000000, 0x00000000), (0x00000000, 0), None, "max(-0, +0)"),
    ("R10 max", "max", (0x00000001, 0x80000000), (0x00000000, 0), None, "A4: DAZ, the result is +0 (not the subnormal's bits)"),
    ("R10 max", "max", (0xFF800000, 0xFF7FFFFF), (0xFF7FFFFF, 0), None, "ordered"),
    ("R10 max", "max", (0x80000001, 0x80000000), (0x80000000, 0), None, "-sub (DAZ -0) vs -0"),
    # R11 N-6 ADD_BF16 = RNE_BF16(RNE_FP32(old + new)), NAN_IN from the add only
    ("R11 add_bf16", "addbf", (0x7F80, 0xFF80), (0x7FC0, INV), None, "Inf - Inf: INVALID, no NAN_IN from the converter"),
    ("R11 add_bf16", "addbf", (0x7F7F, 0x7F7F), (0x7F80, OVF), None, "FP32 overflow"),
    ("R11 add_bf16", "addbf", (0x7F7F, 0x7B00), (0x7F80, OVF), None, "FP32 finite 2^128 - 2^119, BF16 tie -> Inf: OVERFLOW from the converter"),
    ("R11 add_bf16", "addbf", (0x7FC1, 0x3F80), (0x7FC0, NANIN), None, "NaN old: NAN_IN once"),
    ("R11 add_bf16", "addbf", (0x0001, 0x0080), (0x0080, 0), None, "A5: write-add operands are DAZ (BF16 subnormal old reads as 0)"),
    ("R11 add_bf16", "addbf", (0x3F80, 0x3B80), (0x3F80, 0), None, "1 + 2^-8: BF16 tie -> even"),
    # R12 N-3: in BF16 mode Inf comes from operands and from a dot overflow too
    ("R12 bf16 inf", "dot16", ([0x7F7F, 0], [0x7F7F, 0]), (0x7F800000, OVF), None, "the spec's example"),
    ("R12 bf16 inf", "dot16", ([0x7F80, 0], [0x3F80, 0]), (0x7F800000, 0), None, "Inf operand, no flag"),
    ("R12 bf16 inf", "dot16", ([0x7F7F, 0x7F7F], [0x7F7F, 0xFF7F]), (0x00000000, 0), None, "max^2 - max^2: exact, +0"),
    ("R12 bf16 inf", "dot16", ([0x7F80, 0x7F80], [0x3F80, 0xBF80]), (NAN, INV), None, "Inf - Inf"),
    ("R12 bf16 inf", "dot8", ([0x7E] * 4, [0x7E] * 4), (0x49440000, 0), None, "FP8 mode: 4 * 448^2 is finite"),
]


def tile(spec):
    elem, scale, d, dsc = spec
    els, scs = [0] * 256, [dsc] * 256
    for i, (e, s) in d.items():
        els[i], scs[i] = e, s
    return elem, scale, els, scs


def ev(block, args, M):
    """Evaluate one case with model M (R = arith_ref or I = indep)."""
    if block == "dec":
        return M.cvt_dec(args[2], args[0], bool(args[1]))
    if block == "cvt":
        return M.cvt_fp32_fp(args[1], args[0])
    if block == "int":
        y, f = M.cvt_fp32_int(args[2], args[0], args[1])
        return y & ((1 << args[0]) - 1), f
    if block == "mul":
        return M.fp32_mul(*args)
    if block == "add":
        return M.fp32_add(*args)
    if block == "max":
        return M.fp32_max(*args)
    if block == "acc":
        return M.acc_update(*args)
    if block == "dot16":
        return M.bf16_dot2(*args)
    if block == "dot8":
        return M.fp8_dot4(*args)
    if block == "addbf":                     # core/ni composition: fp32_add on BF16 << 16, then FP32 -> BF16, NAN_IN from the add only
        s, fa = M.fp32_add(args[0] << 16, args[1] << 16)
        y, fc = M.cvt_fp32_fp(s, "BF16")
        return y, fa | (fc & ~NANIN)
    if block == "fold":
        elem, scale, els, scs = tile(args)
        if M is R:
            keys = [R.fold_key(e, s, elem, scale) for e, s in zip(els, scs)]
            c = R.fold_c(max(keys))
            vf = [R.fold_quant(e, s, c, elem, scale) for e, s in zip(els, scs)]
            return c, R.fold_cs(c), vf
        _, c, cs, vs, fl = I.fold(els, scs, elem, scale)
        return c, cs, list(zip(vs, fl))
    raise ValueError(block)


def match(block, got, exp):
    if block == "fold":
        c, cs, vf = got
        return c == exp[0] and cs == exp[1] and all(vf[i] == v for i, v in exp[2].items())
    return tuple(got) == tuple(exp)


def in_hand(block, args):
    """Expected (y, flags) of the same inputs in arith_hand_cases.py, or None."""
    t = {"add": H.ADD, "mul": H.MUL, "max": H.MAX}.get(block)
    if t:
        return next(((y, f) for a, b, y, f in t if (a, b) == tuple(args)), None)
    if block == "cvt":
        return next(((y, f) for fm, x, y, f in H.CVT if (fm, x) == args), None)
    if block == "dec":
        return next(((y, f) for fm, tb, c, y, f in H.DEC if (fm, tb, c) == args), None)
    if block == "int":
        return next(((y, f) for b, s, x, y, f in H.CINT if (b, bool(s), x) == (args[0], args[1], args[2])), None)
    if block == "acc":
        return next(((y, f) for *a, y, f in H.ACC if tuple(a) == tuple(args)), None)
    if block == "dot16":
        return next(((y, f) for a, w, y, f in H.DOT16 if (a, w) == tuple(args)), None)
    if block == "dot8":
        return next(((y, f) for a, w, y, f in H.DOT8 if (a, w) == tuple(args)), None)
    return None


def main():
    n = {"ok": 0, "amb": 0, "bad": 0}
    for rule, block, args, exp, alt, note in C:
        r, i = ev(block, args, R), ev(block, args, I)
        ok_r, ok_i = match(block, r, exp), match(block, i, exp)
        h = in_hand(block, args)
        hs = "-" if h is None else ("same" if tuple(h) == tuple(exp) else f"DIFFERS {h}")
        if alt is not None:
            which = "first" if ok_r else ("alt" if match(block, r, alt) else "neither")
            wi = "first" if ok_i else ("alt" if match(block, i, alt) else "neither")
            st = f"AMBIGUOUS (arith_ref: {which} reading, indep: {wi})"
            n["amb"] += 1
        elif ok_r and ok_i:
            st = "agree"
            n["ok"] += 1
        else:
            st = f"MISMATCH ref={'ok' if ok_r else r if block != 'fold' else r[:2]} indep={'ok' if ok_i else i if block != 'fold' else i[:2]}"
            n["bad"] += 1
        shown = args if block != "fold" else f"tile {len(args[2])} set"
        print(f"{rule:20s} {block:6s} {str(shown):48.48s} hand:{hs:6s} {st}  # {note}")
    print(f"cases {len(C)}: agree {n['ok']}, ambiguous {n['amb']}, mismatch {n['bad']}")


if __name__ == "__main__":
    main()
