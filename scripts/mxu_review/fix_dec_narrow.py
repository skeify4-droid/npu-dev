#!/usr/bin/env python3
"""Implementability review, finding I-4: a narrowed copy of mxu_dec (build/mxu_review/fix/mxu_dec.sv; the author's file is not touched).

Changes, each exact where the result can matter (the M-18 fault priority puts the alignment checks and the ACC RANGE check before
the operand RANGE checks, so an operand extent is only looked at with s1 >= 0 and M <= ACC_ROWS):
  * vKT, vKTF, vNT: the divisors are powers of two (2 DIM / 4 DIM, 128 / 256, DIM), so shifts instead of `/` (two 64-bit dividers);
  * a_ext: (M - 1) on clog2(ACC_ROWS + 1) bits times s1[30:0] instead of 65 x 33 bits;
  * w_ext: one 16 x 31 product with the operand muxed by trans_w, instead of two 17 x 33 products;
  * cs_ext: (KTF - 1) on 10 bits times CSP on 19 bits instead of 96 x 13;
  * f_r_acc: r_acc + NT * M on clog2(ACC_ROWS + 1) + 16 bits, under the two terms that already cover r_acc, M > ACC_ROWS, instead of
    61 x 64 bits.
Also writes mxu_dec_fix.sv (the same, module renamed) for the miter testbench tb_dec_miter.sv.
"""
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
OUT = os.path.join(ROOT, 'build', 'mxu_review', 'fix')

EDITS = [
    ("    vNT  = (vN + 64'(DIM) - 64'd1) / 64'(DIM);",
     "    vNT  = (vN + 64'(DIM) - 64'd1) >> $clog2(DIM);"),
    ("    vKT  = 16'((vK + vKTE - 64'd1) / vKTE);      // K tiles (passes) of the MXU",
     "    vKT  = 16'(bf16 ? ((vK + 64'(2 * DIM) - 64'd1) >> $clog2(2 * DIM)) : ((vK + 64'(4 * DIM) - 64'd1) >> $clog2(4 * DIM)));"),
    ("    vKTF = (vK + vKF - 64'd1) / vKF;             // rows of the column-scale array",
     "    vKTF = bf16 ? ((vK + 64'd127) >> 7) : ((vK + 64'd255) >> 8);"),
    ("    a_ext  = 96'(r_a) + 96'(vM - 64'd1) * 96'(a_s1) + 96'(vK * veb);",
     "    p_a    = (AW + 31)'(vMn - AW'(1)) * (AW + 31)'(a_s1[30:0]);\n"
     "    a_ext  = 96'(r_a) + 96'(p_a) + 96'(vK * veb);"),
    ("    w_ext  = i_trans ? (96'(r_w) + 96'(vN - 64'd1) * 96'(w_s1) + 96'(vK * veb)) : (96'(r_w) + 96'(vK - 64'd1) * 96'(w_s1) + 96'(vN * veb));",
     "    p_w    = 47'(16'(i_trans ? vN - 64'd1 : vK - 64'd1)) * 47'(w_s1[30:0]);\n"
     "    w_ext  = 96'(r_w) + 96'(p_w) + 96'(i_trans ? vK * veb : vN * veb);"),
    ("    cs_ext = 96'(r_cs) + 96'(vKTF - 64'd1) * 96'(vCSP) + 96'(64'd4 * vN);",
     "    p_cs   = 29'(10'(vKTF - 64'd1)) * 29'(vCSP[18:0]);\n"
     "    cs_ext = 96'(r_cs) + 96'(p_cs) + 96'(64'd4 * vN);"),
    ("    f_r_acc = (r_acc > 64'(ACC_ROWS)) || (vM > 64'(ACC_ROWS)) || (r_acc + vNT * vM > 64'(ACC_ROWS));",
     "    p_acc   = (AW + 17)'(r_acc[AW-1:0]) + (AW + 17)'(16'(vNT)) * (AW + 17)'(vMn);\n"
     "    f_r_acc = (r_acc > 64'(ACC_ROWS)) || (vM > 64'(ACC_ROWS)) || (p_acc > (AW + 17)'(ACC_ROWS));"),
    ("  logic        m_zero;\n",
     "  logic        m_zero;\n"
     "  localparam int AW = $clog2(ACC_ROWS + 1);\n"
     "  logic [AW-1:0]    vMn;                            // M where it matters (M <= ACC_ROWS, else ACC RANGE comes first)\n"
     "  logic [AW+30:0]   p_a;\n"
     "  logic [46:0]      p_w;\n"
     "  logic [28:0]      p_cs;\n"
     "  logic [AW+16:0]   p_acc;\n"
     "  assign vMn = vM[AW-1:0];\n"),
    # vKTE / vKF are no longer read
    ("  logic unused_td;\n", "  logic unused_td, unused_fix;\n  assign unused_fix = &{1'b0, vKTE, vKF};\n"),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    s = open(os.path.join(ROOT, 'rtl', 'core', 'mxu_dec.sv')).read()
    for old, new in EDITS:
        assert s.count(old) == 1, old
        s = s.replace(old, new)
    open(os.path.join(OUT, 'mxu_dec.sv'), 'w').write(s)
    open(os.path.join(OUT, 'mxu_dec_fix.sv'), 'w').write(re.sub(r'\bmodule mxu_dec\b', 'module mxu_dec_fix', s, count=1))
    print('wrote', os.path.join(OUT, 'mxu_dec.sv'), 'and mxu_dec_fix.sv')


if __name__ == '__main__':
    main()
