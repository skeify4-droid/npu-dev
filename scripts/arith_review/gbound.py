"""Targeted vectors for the fp_addu guard-bit bound: one-bit cancellation (big = 1.000..x, opposite sign), exponent
differences 2..60, small mantissas that put the exact result on / next to a rounding tie. Expected from indep.py.
Writes OUT/arith/fp32_add.hex, OUT/arith/bf16_dot2.hex and OUT/arith/counts.svh (the row counts the testbenches require).
They are not the locked vectors: run them through run_tb.sh with ARITH_LOCK=0."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import indep as I  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build", "arith_review", "gb")
rnd = random.Random(7)
os.makedirs(os.path.join(OUT, "arith"), exist_ok=True)


def pk(*fields):
    v = 0
    for val, w in fields:
        v = (v << w) | val
    return format(v, "0%dx" % ((sum(w for _, w in fields) + 3) // 4))


# ---- fp32_add
rows = []
bigm = [0, 1, 2, 3, 0x7FFFFF, 0x400000, 0x000100]
smallm = [0, 1, 2, 0x7FFFFF, 0x7FFFFE, 0x400000, 0x400001, 0x3FFFFF, 0x200000, 0x000001 << 10, 0x7FFC00]
for d in range(1, 61):
    for bm in bigm:
        for sm in smallm + [rnd.getrandbits(23) for _ in range(6)]:
            for e in (130, 2 + d if 2 + d < 254 else 130):
                es = e - d
                if es < 1:
                    continue
                for sa in (0, 1):
                    for sb in ((sa ^ 1, sa) if rnd.random() < 0.3 else (sa ^ 1,)):
                        a = sa << 31 | e << 23 | bm
                        b = sb << 31 | es << 23 | sm
                        y, f = I.fp32_add(a, b)
                        rows.append(pk((a, 32), (b, 32), (y, 32), (f, 4)))
                        y, f = I.fp32_add(b, a)
                        rows.append(pk((b, 32), (a, 32), (y, 32), (f, 4)))
rnd.shuffle(rows)
rows = rows[:150000]
open(os.path.join(OUT, "arith", "fp32_add.hex"), "w").write("\n".join(rows) + "\n")
print("fp32_add rows", len(rows))
COUNTS = {"FP32_ADD": len(rows)}


# ---- bf16_dot2: p0 = 1.x * 1.y * 2^E (x, y few bits), p1 = -(1.u * 1.v) * 2^(E - d)
def bf(s, e, m):
    return s << 15 | (e + 127) << 7 | m


rows = []
mm = [0, 1, 0x7F, 0x7E, 0x40, 0x41, 0x3F]
for d in range(1, 60):
    for _ in range(1500):
        s0 = rnd.getrandbits(1)
        a0, w0 = bf(s0, rnd.randint(-10, 10), rnd.choice([0, 0, 1, 0x40])), bf(0, rnd.randint(-10, 10), rnd.choice([0, 0, 1]))
        e0 = ((a0 >> 7) & 0xFF) - 127 + ((w0 >> 7) & 0xFF) - 127
        ew1 = rnd.randint(-20, 20)
        ea1 = e0 - d - ew1
        if not (-126 <= ea1 <= 127):
            continue
        s1 = s0 ^ (1 if rnd.random() < 0.8 else 0)
        a1 = bf(s1, ea1, rnd.choice(mm + [rnd.getrandbits(7)]))
        w1 = bf(0, ew1, rnd.choice(mm + [rnd.getrandbits(7)]))
        for A, W in (([a0, a1], [w0, w1]), ([a1, a0], [w1, w0])):
            y, f = I.bf16_dot2(A, W)
            rows.append(pk((A[0] | A[1] << 16, 32), (W[0] | W[1] << 16, 32), (y, 32), (f, 4)))
rnd.shuffle(rows)
rows = rows[:140000]
open(os.path.join(OUT, "arith", "bf16_dot2.hex"), "w").write("\n".join(rows) + "\n")
print("bf16_dot2 rows", len(rows))
COUNTS["BF16_DOT2"] = len(rows)
with open(os.path.join(OUT, "arith", "counts.svh"), "w") as f:
    for k, v in COUNTS.items():
        f.write("`define CNT_%s %d\n" % (k, v))
