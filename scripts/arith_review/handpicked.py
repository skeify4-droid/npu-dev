"""Hand-derived cases (expected bits worked out by hand from numerics.md; see comments), checked against arith_ref.py and
the independent oracle; then written as hex (padded with existing rows to satisfy the testbench count checks) so the RTL
testbenches can run them from OUT (cwd OUT, files OUT/arith/*.hex)."""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "sw"))
sys.path.insert(0, HERE)
from npumodel import arith_ref as R  # noqa: E402
import indep as I  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build", "arith_review", "hp")
INV, OVF, SAT, NANIN = 1, 2, 4, 8
NAN = 0x7FC00000

# ---------------------------------------------------------------- FP32 add: (a, b, y, flags)
ADD = [
    (0x3F800000, 0x33800000, 0x3F800000, 0),    # 1 + 2^-24: tie, 1.0 even
    (0x3F800001, 0x33800000, 0x3F800002, 0),    # (1+2^-23) + 2^-24: tie, odd -> up
    (0x3F800000, 0x33800001, 0x3F800001, 0),    # 1 + 2^-24(1+2^-23): above tie
    (0x80000000, 0x80000000, 0x80000000, 0),    # -0 + -0 = -0
    (0x00000000, 0x80000000, 0x00000000, 0),    # +0 + -0 = +0
    (0x3F800000, 0xBF800000, 0x00000000, 0),    # x - x = +0
    (0x00800000, 0x80800001, 0x80000000, 0),    # exact -2^-149: FTZ -> -0
    (0x00000001, 0x3F800000, 0x3F800000, 0),    # DAZ
    (0x80000001, 0x80000000, 0x80000000, 0),    # -sub (DAZ -0) + -0 = -0
    (0x00000001, 0x80000001, 0x00000000, 0),    # +0 + -0 after DAZ
    (0x7F7FFFFF, 0x7F7FFFFF, 0x7F800000, OVF),  # overflow
    (0x7F7FFFFF, 0x73000000, 0x7F800000, OVF),  # max + 1/2 ulp: tie, max odd -> Inf
    (0x7F7FFFFF, 0x72FFFFFF, 0x7F7FFFFF, 0),    # max + just under 1/2 ulp
    (0x7F800000, 0xFF800000, NAN, INV),         # Inf - Inf
    (0x7F800000, 0x7F800000, 0x7F800000, 0),
    (0x7F800001, 0x3F800000, NAN, NANIN),       # sNaN-like payload in -> canonical
    (0xFFC00000, 0x7F800000, NAN, NANIN),
    (0x7F800000, 0x7F7FFFFF, 0x7F800000, 0),    # Inf + max: no OVERFLOW (Inf operand)
    (0x01000000, 0x80C00000, 0x00000000, 0),    # 2^-125 - 1.5*2^-126 = 2^-127 -> flushed +0
    (0x3F800000, 0xBF7FFFFF, 0x33800000, 0),    # 1 - (1 - 2^-24) = 2^-24
    (0x3F800000, 0xB3000001, 0x3F7FFFFF, 0),    # 1 - 2^-25(1+2^-23): sticky below the tie -> down
    (0x3F800000, 0xB3000000, 0x3F800000, 0),    # 1 - 2^-25: exact tie -> 1.0 (even)
    (0x3F800000, 0x8D800000, 0x3F800000, 0),    # 1 - 2^-100
    (0x00C00000, 0x80800000, 0x00000000, 0),    # 1.5*2^-126 - 2^-126 = 2^-127 -> +0
    (0xBF800000, 0x33000001, 0xBF7FFFFF, 0),    # -1 + 2^-25(1+2^-23): mirrored sticky
]

# ---------------------------------------------------------------- FP32 mul
MUL = [
    (0x20000000, 0x20000000, 0x00800000, 0),    # 2^-63 * 2^-63 = 2^-126
    (0x1F800000, 0x20000000, 0x00000000, 0),    # 2^-127 -> FTZ +0
    (0x9F800000, 0x20000000, 0x80000000, 0),    # -2^-127 -> -0
    (0x1FFFFFFF, 0x20000000, 0x00800000, 0),    # 2^-126 - 2^-150: subnormal-grid tie -> 2^-126 kept (round, then flush)
    (0x1FFFFFFE, 0x20000000, 0x00000000, 0),    # 2^-126 - 2^-149: exact subnormal -> flushed
    (0x3FC00000, 0x3FC00000, 0x40100000, 0),    # 1.5 * 1.5
    (0x3F800001, 0x3F800001, 0x3F800002, 0),    # (1+u)^2 = 1 + 2u + u^2
    (0x3F800001, 0x3F7FFFFF, 0x3F800000, 0),    # 1 + 2^-24 - 2^-47: just below tie -> 1.0
    (0x3FC00000, 0x3F800001, 0x3FC00002, 0),    # 1.5 + 1.5*2^-23: tie, odd -> up
    (0x7F800000, 0x00000000, NAN, INV),
    (0x7F800000, 0x00000001, NAN, INV),         # Inf * DAZ zero
    (0xFF800000, 0x40000000, 0xFF800000, 0),
    (0x7FC00000, 0x00000000, NAN, NANIN),
    (0x7F7FFFFF, 0x40000000, 0x7F800000, OVF),
    (0xFF7FFFFF, 0x40000000, 0xFF800000, OVF),
    (0x00000000, 0xBF800000, 0x80000000, 0),    # +0 * -1 = -0
    (0x80000001, 0x3F800000, 0x80000000, 0),    # -sub DAZ -> -0
    (0x7F000000, 0x40000000, 0x7F800000, OVF),  # 2^127 * 2
    (0x7F7FFFFF, 0x3F800000, 0x7F7FFFFF, 0),
    (0x7F7FFFFF, 0x3F800001, 0x7F800000, OVF),
    (0x7F7FFFFF, 0x3F7FFFFF, 0x7F7FFFFE, 0),    # 2 - 2^-22 + 2^-47 -> 2 - 2^-22
]

# ---------------------------------------------------------------- FP32 max
MAX = [
    (0x80000000, 0x00000000, 0x00000000, 0), (0x00000000, 0x80000000, 0x00000000, 0),
    (0x80000000, 0x80000000, 0x80000000, 0),
    (0x7FC00000, 0x3F800000, NAN, NANIN), (0x3F800000, 0xFFC00001, NAN, NANIN),
    (0xFF800000, 0xFF7FFFFF, 0xFF7FFFFF, 0), (0x7F800000, 0x7F7FFFFF, 0x7F800000, 0),
    (0x00000001, 0x00000000, 0x00000000, 0),    # DAZ: equal zeros
    (0x00000001, 0xBF800000, 0x00000000, 0),    # +sub reads as +0 and wins as +0
    (0x80000001, 0xBF800000, 0x80000000, 0),    # -sub reads as -0
    (0xBF800000, 0xC0000000, 0xBF800000, 0), (0x3F800000, 0x3F800001, 0x3F800001, 0),
    (0x80000001, 0x00000001, 0x00000000, 0),    # -0 vs +0 after DAZ
    (0x7F800000, 0x7F800001, NAN, NANIN),
    (0xFF800000, 0x80000000, 0x80000000, 0),
    (0x00800000, 0x007FFFFF, 0x00800000, 0),    # min normal beats a (DAZ) subnormal
    (0x80800000, 0x807FFFFF, 0x80000000, 0),    # -min normal vs -sub (= -0): -0 wins
    (0x40000000, 0x3FFFFFFF, 0x40000000, 0),
    (0xC0000000, 0xBFFFFFFF, 0xBFFFFFFF, 0),
    (0x7F7FFFFF, 0xFF800000, 0x7F7FFFFF, 0),
]


# ---------------------------------------------------------------- fp8_dot4: (a[4], w[4], y, flags)
def P4(*x):
    return list(x) + [0] * (4 - len(x))


DOT8 = [
    (P4(0x38), P4(0x38), 0x3F800000, 0),
    ([0x7E] * 4, [0x7E] * 4, 0x49440000, 0),                   # 4 * 448^2 = 802816
    (P4(0x01), P4(0x01), 0x36800000, 0),                       # 2^-18
    (P4(0x7E, 0x01), P4(0x7E, 0x01), 0x48440000, 0),            # 448^2 + 2^-18: far below half ulp
    (P4(0x7E, 0x18), P4(0x7E, 0x20), 0x48440000, 0),            # 448^2 + 2^-7: tie, even
    (P4(0x7E, 0x18, 0x01), P4(0x7E, 0x20, 0x01), 0x48440001, 0),  # + 2^-18: above tie
    (P4(0x7E, 0x7E, 0x38), P4(0x7E, 0xFE, 0x38), 0x3F800000, 0),  # cancel to 1
    ([0x80] * 4, [0x00] * 4, 0x80000000, 0),                   # four -0 products
    ([0x80, 0x80, 0x80, 0x00], [0x00] * 4, 0x00000000, 0),     # three -0, one +0
    (P4(0x38, 0x38), P4(0x38, 0xB8), 0x00000000, 0),           # exact cancellation -> +0
    (P4(0x7F), P4(0x38), NAN, NANIN),
    (P4(0xFF), P4(0x00), NAN, NANIN),                          # -NaN x 0
    (P4(0xFE), P4(0x7E), 0xC8440000, 0),
    ([0x7E, 0x7E, 0x7E, 0x81], [0x7E, 0x7E, 0x7E, 0x01], 0x49130000, 0),  # 3*448^2 - 2^-18
    ([0x7E, 0xFE, 0x01, 0x00], [0x7E, 0x7E, 0x81, 0x00], 0xB6800000, 0),  # 448^2 - 448^2 - 2^-18 = -2^-18
    (P4(0x30), P4(0x30), 0x3E800000, 0),                       # 0.25
    (P4(0x08), P4(0x08), 0x39800000, 0),                       # 2^-6 * 2^-6 = 2^-12
    (P4(0x07), P4(0x07), 0x39440000, 0),                       # (7*2^-9)^2 = 49*2^-18
    ([0x39, 0x39, 0x39, 0x39], [0x39, 0x39, 0x39, 0x39], 0x40A20000, 0),   # 4 * 1.125^2 = 5.0625
    ([0x7F, 0xFF, 0x7F, 0xFF], [0x80] * 4, NAN, NANIN),
]

# ---------------------------------------------------------------- bf16_dot2: (a[2], w[2], y, flags)
DOT16 = [
    ([0x3F80, 0], [0x3F80, 0], 0x3F800000, 0),
    ([0x7F7F, 0], [0x7F7F, 0], 0x7F800000, OVF),
    ([0x7F7F, 0x7F7F], [0x7F7F, 0xFF7F], 0x00000000, 0),         # max^2 - max^2: exact, no overflow
    ([0x7F80, 0x3F80], [0x0000, 0x3F80], NAN, INV),              # Inf * 0
    ([0x7F80, 0x7F80], [0x3F80, 0xBF80], NAN, INV),              # Inf - Inf
    ([0x7F80, 0x7F80], [0x3F80, 0x3F80], 0x7F800000, 0),
    ([0x7FC0, 0x7F80], [0x3F80, 0x0000], NAN, NANIN),            # NaN in + Inf*0: NAN_IN only
    ([0x0001, 0x3F80], [0x3F80, 0x3F80], 0x3F800000, 0),         # DAZ
    ([0x7F80, 0x3F80], [0x0001, 0x3F80], NAN, INV),              # Inf * DAZ zero
    ([0x2000, 0], [0x2000, 0], 0x00800000, 0),                   # 2^-63 * 2^-63
    ([0x1F80, 0], [0x2000, 0], 0x00000000, 0),                   # 2^-127 -> 0
    ([0x2000, 0x9A00], [0x2000, 0x1980], 0x00800000, 0),         # 2^-126 - 2^-151 -> 2^-126
    ([0x2000, 0x9A00], [0x2000, 0x1A00], 0x00800000, 0),         # 2^-126 - 2^-150: tie -> 2^-126
    ([0x2000, 0x9A80], [0x2000, 0x1A00], 0x00000000, 0),         # 2^-126 - 2^-149: subnormal -> flushed
    ([0x3F80, 0xB300], [0x3F80, 0x3F81], 0x3F7FFFFF, 0),         # 1 - 2^-25 - 2^-32: sticky -> down
    ([0x3F80, 0xB300], [0x3F80, 0x3F80], 0x3F800000, 0),         # 1 - 2^-25: tie -> 1.0
    ([0x3F80, 0x3F7F], [0x3F80, 0xBF80], 0x3B800000, 0),         # 1 - (1 - 2^-8)
    ([0x8000, 0x8000], [0x3F80, 0x3F80], 0x80000000, 0),         # -0 + -0
    ([0x8000, 0x0000], [0x3F80, 0x3F80], 0x00000000, 0),
    ([0x3F80, 0x3F80], [0x3F80, 0xBF80], 0x00000000, 0),
    ([0x8001, 0x8000], [0x3F80, 0x3F80], 0x80000000, 0),         # -sub (DAZ) + -0
    ([0x3F81, 0], [0x3F81, 0], 0x3F820200, 0),                   # (1+2^-7)^2 exact
    ([0x7F7F, 0], [0x3F80, 0], 0x7F7F0000, 0),
    ([0x7F7F, 0x7F7F], [0x3F80, 0x3F80], 0x7F800000, OVF),       # 2 * max_bf16 > FP32 max
    ([0x5F80, 0x5F80], [0x5F80, 0xDF80], 0x00000000, 0),         # 2^128 - 2^128 (products beyond FP32): exact 0
    ([0x5F80, 0x3F80], [0x5F80, 0x3F80], 0x7F800000, OVF),       # 2^128 + 1 -> Inf
]

# ---------------------------------------------------------------- cvt_fp32_fp: (fmt, x, y, flags)
CVT = [
    ("BF16", 0x3F808000, 0x3F80, 0), ("BF16", 0x3F808001, 0x3F81, 0), ("BF16", 0x3F818000, 0x3F82, 0),
    ("BF16", 0x007FFFFF, 0x0080, 0), ("BF16", 0x007F8000, 0x0080, 0), ("BF16", 0x007F7FFF, 0x0000, 0),
    ("BF16", 0x807FFFFF, 0x8080, 0), ("BF16", 0x80400000, 0x8000, 0), ("BF16", 0x7F7F8000, 0x7F80, OVF),
    ("BF16", 0x7F7F7FFF, 0x7F7F, 0), ("BF16", 0x7F7FFFFF, 0x7F80, OVF), ("BF16", 0x7F800000, 0x7F80, 0),
    ("BF16", 0xFF800001, 0x7FC0, NANIN), ("BF16", 0x00800000, 0x0080, 0),
    ("FP16", 0x477FF000, 0x7C00, OVF), ("FP16", 0x477FEFFF, 0x7BFF, 0), ("FP16", 0x33000000, 0x0000, 0),
    ("FP16", 0x33000001, 0x0001, 0), ("FP16", 0xB3000000, 0x8000, 0), ("FP16", 0x387FE000, 0x0400, 0),
    ("FP16", 0xFF800000, 0xFC00, 0), ("FP16", 0x7FC00001, 0x7E00, NANIN),
    ("E4M3", 0x43E80000, 0x7E, 0), ("E4M3", 0x43E80001, 0x7E, SAT), ("E4M3", 0x43E10000, 0x7E, 0),
    ("E4M3", 0x7F800000, 0x7E, 0), ("E4M3", 0xFF800000, 0xFE, 0), ("E4M3", 0x7FC00000, 0x7F, NANIN),
    ("E4M3", 0x3A800000, 0x00, 0), ("E4M3", 0xBA800000, 0x80, 0), ("E4M3", 0x3B400000, 0x02, 0),
    ("E4M3", 0x3C700000, 0x08, 0), ("E4M3", 0x4E6E6B28, 0x7E, SAT), ("E4M3", 0xC3E80001, 0xFE, SAT),
    ("E4M3", 0x00000001, 0x00, 0), ("E4M3", 0x3A800001, 0x01, 0),
    ("E5M2", 0x47700000, 0x7B, SAT), ("E5M2", 0x476FFF00, 0x7B, 0), ("E5M2", 0x7F800000, 0x7B, 0),
    ("E5M2", 0xFF800000, 0xFB, 0), ("E5M2", 0xFFC00000, 0x7E, NANIN), ("E5M2", 0x37000000, 0x00, 0),
    ("E5M2", 0x37000001, 0x01, 0),
    ("E2M1", 0x40A00000, 0x6, 0), ("E2M1", 0x40E00000, 0x7, SAT), ("E2M1", 0x40DFFFFF, 0x7, 0),
    ("E2M1", 0x3E800000, 0x0, 0), ("E2M1", 0xBE800000, 0x8, 0), ("E2M1", 0x3F400000, 0x2, 0),
    ("E2M1", 0x3FA00000, 0x2, 0), ("E2M1", 0x40200000, 0x4, 0), ("E2M1", 0x40600000, 0x6, 0),
    ("E2M1", 0x7FC00000, 0x0, NANIN), ("E2M1", 0x7F800000, 0x7, 0), ("E2M1", 0xFF800000, 0xF, 0),
]

# ---------------------------------------------------------------- cvt_fp32_int: (bits, signed, x, y, flags)
CINT = [
    (8, 1, 0x42FF0000, 0x7F, SAT), (8, 1, 0x42FEFAE1, 0x7F, 0), (8, 1, 0xC3008000, 0x80, 0),
    (8, 1, 0xC3008001, 0x80, SAT), (8, 1, 0x3F000000, 0x00, 0), (8, 1, 0x3FC00000, 0x02, 0),
    (8, 1, 0x40200000, 0x02, 0), (8, 1, 0xBF000000, 0x00, 0), (8, 1, 0x7FC00000, 0x00, NANIN),
    (8, 1, 0x7F800000, 0x7F, 0), (8, 1, 0xFF800000, 0x80, 0), (8, 1, 0x3EFFFFFF, 0x00, 0),
    (8, 1, 0x3E99999A, 0x00, 0), (8, 1, 0x3F000001, 0x01, 0), (8, 1, 0xBF400000, 0xFF, 0),
    (8, 0, 0xBF000000, 0x00, 0), (8, 0, 0xBF028F5C, 0x00, SAT), (8, 0, 0x437F8000, 0xFF, SAT),
    (8, 0, 0x437E8000, 0xFE, 0), (8, 0, 0xFF800000, 0x00, 0),
    (16, 1, 0x46FFFF00, 0x7FFF, SAT), (16, 1, 0xC7000080, 0x8000, 0), (16, 1, 0xC7000081, 0x8000, SAT),
    (32, 1, 0x4F000000, 0x7FFFFFFF, SAT), (32, 1, 0xCF000000, 0x80000000, 0), (32, 1, 0x4EFFFFFF, 0x7FFFFF80, 0),
    (32, 1, 0x5D800000, 0x7FFFFFFF, SAT), (32, 1, 0xCF000001, 0x80000000, SAT),
    (32, 0, 0x4F800000, 0xFFFFFFFF, SAT), (32, 0, 0x4F7FFFFF, 0xFFFFFF00, 0), (32, 0, 0xBF800000, 0x00000000, SAT),
    (32, 0, 0x00000001, 0x00000000, 0), (32, 0, 0x7F800000, 0xFFFFFFFF, 0),
]

# ---------------------------------------------------------------- cvt_dec: (fmt, to_bf16, code, y, flags)
DEC = [
    ("E4M3", 0, 0x7F, NAN, NANIN), ("E4M3", 0, 0xFF, NAN, NANIN), ("E4M3", 0, 0x7E, 0x43E00000, 0),
    ("E4M3", 0, 0x01, 0x3B000000, 0), ("E4M3", 0, 0x80, 0x80000000, 0), ("E4M3", 1, 0x7E, 0x43E0, 0),
    ("E5M2", 0, 0x7C, 0x7F800000, 0), ("E5M2", 0, 0x7D, NAN, NANIN), ("E5M2", 0, 0x01, 0x37800000, 0),
    ("E5M2", 1, 0xFC, 0xFF80, 0), ("E5M2", 1, 0x7B, 0x4760, 0),
    ("E2M1", 0, 0x7, 0x40C00000, 0), ("E2M1", 0, 0x8, 0x80000000, 0), ("E2M1", 0, 0x1, 0x3F000000, 0),
    ("FP16", 0, 0x0001, 0x33800000, 0), ("FP16", 1, 0x3C01, 0x3F80, 0), ("FP16", 1, 0x3C04, 0x3F80, 0),
    ("FP16", 1, 0x3C0C, 0x3F82, 0), ("FP16", 1, 0x7BFF, 0x4780, 0), ("FP16", 1, 0x7E01, 0x7FC0, NANIN),
    ("BF16", 0, 0x0001, 0x00010000, 0), ("BF16", 1, 0x0001, 0x0001, 0), ("BF16", 1, 0xFFC1, 0x7FC0, NANIN),
]

# ---------------------------------------------------------------- mx_dequant: (elem, scale, el, sc, y, flags)
MXD = [
    (0, 0, 0x7, 254, 0x7F80, OVF), (0, 0, 0x7, 0xFF, 0x7FC0, NANIN), (0, 0, 0x1, 0, 0x0020, 0),
    (0, 0, 0x9, 0, 0x8020, 0), (0, 0, 0x8, 127, 0x8000, 0), (0, 0, 0x3, 127, 0x3FC0, 0), (0, 0, 0x5, 0, 0x00C0, 0),
    (0, 0, 0x6, 253, 0x7F80, OVF), (0, 0, 0x5, 253, 0x7F40, 0),
    (1, 0, 0x01, 0, 0x0000, 0), (1, 0, 0x04, 0, 0x0000, 0), (1, 0, 0x05, 0, 0x0001, 0), (1, 0, 0x0C, 0, 0x0002, 0),
    (1, 0, 0x7E, 254, 0x7F80, OVF), (1, 0, 0x7F, 1, 0x7FC0, NANIN), (1, 0, 0x80, 0xFF, 0x7FC0, NANIN),
    (1, 0, 0x38, 127, 0x3F80, 0), (1, 0, 0x0B, 0, 0x0001, 0), (1, 0, 0x81, 0, 0x8000, 0),
    (0, 1, 0x7, 0x7E, 0x4528, 0), (0, 1, 0x7, 0x7F, 0x7FC0, NANIN), (0, 1, 0x7, 0xFF, 0x7FC0, NANIN),
    (0, 1, 0x7, 0x00, 0x0000, 0), (0, 1, 0xF, 0x00, 0x8000, 0), (0, 1, 0x1, 0x01, 0x3A80, 0),
    (0, 1, 0x7, 0x80, 0x8000, 0), (0, 1, 0x7, 0xB8, 0xC0C0, 0),
]

# ---------------------------------------------------------------- fold tiles: (elem, scale, {idx: (el, sc)}, default_sc,
#   expected c, expected cs bits, {idx: (v, flags)})
FOLD = [
    (1, 0, {0: (0x7E, 127)}, 127, 0, 0x3F800000, {0: (0x7E, 0)}),                    # amax 448 -> c = 0
    (1, 0, {0: (0x7E, 128)}, 127, 1, 0x40000000, {0: (0x7E, 0)}),
    (1, 0, {0: (0x77, 128), 1: (0x01, 128)}, 128, 1, 0x40000000, {0: (0x77, 0), 1: (0x01, 0)}),   # amax 480
    (0, 0, {0: (0x7, 127), 1: (0x1, 127)}, 127, -6, 0x3C800000, {0: (0x7C, 0), 1: (0x60, 0)}),   # amax 6
    # 15-binade spread: block 0 scale b, block 1 scale b+15 holding a 6; c = b+15-127-6
    (0, 0, {32: (0x7, 115), 0: (0x1, 100), 1: (0x3, 100), 2: (0x2, 100)}, None, -18, 0x36800000,
     {0: (0x00, 0), 1: (0x02, 0), 2: (0x01, 0), 32: (0x7C, 0)}),
    # 14-binade spread: 0.5 lands on 2^-9 exactly
    (0, 0, {32: (0x7, 114), 0: (0x1, 100), 1: (0x3, 100)}, None, -19, 0x36000000, {0: (0x01, 0), 1: (0x03, 0), 32: (0x7C, 0)}),
    (0, 1, {0: (0x7, 0x7E)}, 0x38, 3, 0x41000000, {0: (0x7A, 0)}),                   # NVFP4 2688: 336 tie -> 320
    (0, 1, {}, 0x38, 0, 0x3F800000, {0: (0x00, 0)}),                                  # all zero
    (1, 0, {i: (0x7F, 0xFF) for i in range(32)} | {32: (0x38, 127)}, None, -8, 0x3B800000,
     {0: (0x7F, NANIN), 32: (0x78, 0)}),                                              # NaN block
    (1, 0, {0: (0x7E, 120)}, 120, -7, 0x3C000000, {0: (0x7E, 0)}),                   # amax = 448*2^-7 exactly
    (1, 0, {0: (0x77, 120)}, 120, -7, 0x3C000000, {0: (0x77, 0)}),                   # amax = 1.875 > 1.75*2^0
    (1, 0, {0: (0x7E, 254), 32: (0x01, 0)}, None, 127, 0x7F000000, {0: (0x7E, 0), 32: (0x00, 0)}),
    (1, 0, {0: (0x01, 0)}, 0, -144, 0x00000020, {0: (0x78, 0)}),                     # amax 2^-136
    (0, 1, {0: (0x7, 0x7F), 1: (0xF, 0x00), 2: (0x2, 0x38)}, 0x38, -8, 0x3B800000,
     {0: (0x7F, NANIN), 1: (0x80, 0), 2: (0x78, 0)}),                                 # NaN scale, zero scale, 1.0
    (0, 0, {0: (0xF, 127)}, 127, -6, 0x3C800000, {0: (0xFC, 0), 1: (0x00, 0)}),      # -6 -> -384
]


def tile_of(spec):
    elem, scale, d, dsc, *_ = spec
    els, scs = [0] * 64, [dsc if dsc is not None else 127] * 64
    if dsc is None:                    # per-block default: scale of the specified element of that block
        for blk in range(2):
            s = [v[1] for i, v in d.items() if i // 32 == blk]
            for i in range(32 * blk, 32 * blk + 32):
                scs[i] = s[0] if s else 127
    for i, (e, s) in d.items():
        els[i], scs[i] = e, s
    return els, scs


def pk(*fields):
    v = 0
    for val, w in fields:
        assert 0 <= val < (1 << w), (val, w)
        v = (v << w) | val
    return format(v, "0%dx" % ((sum(w for _, w in fields) + 3) // 4))


def l4(v, bits):
    return sum(x << (bits * i) for i, x in enumerate(v))


bad = []


def cmp(tag, hand, ref, ind):
    if hand != ref or hand != ind:
        bad.append((tag, "hand", hand, "ref", ref, "indep", ind))


def main():
    files = {}
    for a, b, y, f in ADD:
        cmp(("add", hex(a), hex(b)), (y, f), R.fp32_add(a, b), I.fp32_add(a, b))
        files.setdefault("fp32_add", []).append(pk((a, 32), (b, 32), (y, 32), (f, 4)))
    for a, b, y, f in MUL:
        cmp(("mul", hex(a), hex(b)), (y, f), R.fp32_mul(a, b), I.fp32_mul(a, b))
        files.setdefault("fp32_mul", []).append(pk((a, 32), (b, 32), (y, 32), (f, 4)))
    for a, b, y, f in MAX:
        cmp(("max", hex(a), hex(b)), (y, f), R.fp32_max(a, b), I.fp32_max(a, b))
        files.setdefault("fp32_max", []).append(pk((a, 32), (b, 32), (y, 32), (f, 4)))
    for a, w, y, f in DOT8:
        cmp(("dot8", a, w), (y, f), R.fp8_dot4(a, w), I.fp8_dot4(a, w))
        files.setdefault("fp8_dot4", []).append(pk((l4(a, 8), 32), (l4(w, 8), 32), (y, 32), (f, 4)))
    for a, w, y, f in DOT16:
        cmp(("dot16", [hex(x) for x in a], [hex(x) for x in w]), (y, f), R.bf16_dot2(a, w), I.bf16_dot2(a, w))
        files.setdefault("bf16_dot2", []).append(pk((l4(a, 16), 32), (l4(w, 16), 32), (y, 32), (f, 4)))
    for fmt, x, y, f in CVT:
        cmp(("cvt", fmt, hex(x)), (y, f), R.cvt_fp32_fp(x, fmt), I.cvt_fp32_fp(x, fmt))
        files.setdefault("cvt_fp32_" + fmt.lower(), []).append(pk((x, 32), (y, 16), (f, 4)))
    tags = {(8, 1): "int8", (8, 0): "uint8", (16, 1): "int16", (32, 1): "int32", (32, 0): "uint32"}
    for bits, sg, x, y, f in CINT:
        cmp(("int", bits, sg, hex(x)), (y, f), R.cvt_fp32_int(x, bits, bool(sg)), I.cvt_fp32_int(x, bits, bool(sg)))
        files.setdefault("cvt_fp32_" + tags[(bits, sg)], []).append(pk((x, 32), (y, 32), (f, 4)))
    for fmt, tb, c, y, f in DEC:
        cmp(("dec", fmt, tb, hex(c)), (y, f), R.cvt_dec(c, fmt, bool(tb)), I.cvt_dec(c, fmt, bool(tb)))
    for e, s, el, sc, y, f in MXD:
        cmp(("mxd", e, s, hex(el), hex(sc)), (y, f), R.mx_dequant_bf16(el, sc, e, s), I.mx_dequant_bf16(el, sc, e, s))
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
        name = {(0, 0): "fold_mxfp4", (1, 0): "fold_mxfp8", (0, 1): "fold_nvfp4"}[(elem, scale)]
        flds = [(e, 8) for e in reversed(els)] + [(s, 8) for s in reversed(scs)]
        flds += [(max(keys), 17), (c_r & 0x3FF, 10), (cs_r, 32)]
        flds += [(v, 8) for v, _ in reversed(q)] + [(f, 4) for _, f in reversed(q)]
        files.setdefault(name, []).append(pk(*flds))

    n = sum(len(x) for x in (ADD, MUL, MAX, DOT8, DOT16, CVT, CINT, DEC, MXD, FOLD))
    print(f"hand cases: {n}; disagreements: {len(bad)}")
    for b in bad:
        print("  ", b)

    # hex for the RTL: hand rows first, then pad from the existing vectors to satisfy the count checks
    os.makedirs(os.path.join(OUT, "arith"), exist_ok=True)
    allnames = ["fp32_add", "fp32_mul", "fp32_max", "fp8_dot4", "bf16_dot2", "fold_mxfp4", "fold_mxfp8", "fold_nvfp4"] + \
        ["cvt_fp32_" + x for x in ("bf16", "fp16", "e4m3", "e5m2", "e2m1", "int8", "uint8", "int16", "int32", "uint32")]
    for nm_ in allnames:
        rows = files.get(nm_, [])
        src = [l.strip() for l in open(os.path.join(ROOT, "build", "arith", nm_ + ".hex")) if l.strip()]
        need = 1500 if nm_.startswith("fold_") else 1200
        rows = rows + src[:need - len(rows)]
        open(os.path.join(OUT, "arith", nm_ + ".hex"), "w").write("\n".join(rows) + "\n")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
