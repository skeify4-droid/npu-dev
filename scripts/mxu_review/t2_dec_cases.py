#!/usr/bin/env python3
"""Review part TR2: directed cases for the legality and wait survivors of t2_mutants.py, written by hand from mxu.md 4.1 and isa.md 3
(expected cause, FAULT_ADDR, wait outcome), not from gen_mxu_vectors.py. Same machinery as tr_dec_cases.py (part TR): the author's
checker mxu_fault_tc (tb/unit/tb_mxu.sv, unchanged) at DIM 8 and 16, SPM 256 KiB, 256 ACC rows, plus a check of the decoder's wait
on serial-number pairs (mxu_dec alone, no clock edge).

Usage: source env.sh; python3 scripts/mxu_review/t2_dec_cases.py [RTL]     RTL: rtl | t2:<name> | tr:<name> | tq:<name> | au:<name>
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import t2_tb  # noqa: E402,F401  (t2: specs in tr_tb.rtl_files)
import tr_tb  # noqa: E402
from tr_dec_cases import row, WAIT_TB, SPM, ACCR, RANGE, OK  # noqa: E402

ROOT = tr_tb.ROOT


def cases(dim):
    C = []
    # t2_w_range_tol31: trans FP8 W, dim rows of K = 65 B at pitch 128, the last row starts at SPM - 64: 1 B past -> RANGE at R[w]
    wb = SPM - (dim - 1) * 128 - 64
    C.append(("w_over1", row(dim, RANGE, wb, dict(n0=65, n1=2, s1=128), dict(n0=65, n1=dim, s1=128), w_base=wb)))
    C.append(("w_end_exact", row(dim, OK, 0, dict(n0=64, n1=2, s1=128), dict(n0=64, n1=dim, s1=128), w_base=wb)))      # control: ends at SPM
    # t2_w_nt_bf16_elem: BF16 non-trans W (TD n0 = N elements, n1 = K rows), K = 4 rows at pitch 128, N = 48: 96 B rows, the last starts
    # at SPM - 64 -> 32 B past -> RANGE at R[w] (counted in elements it would end 16 B before the end)
    wn = SPM - 64 - 3 * 128
    C.append(("w_nt_bf16_over", row(dim, RANGE, wn, dict(dtype=1, n0=4, n1=2, s1=64), dict(dtype=1, n0=48, n1=4, s1=128), trans=0, w_base=wn)))
    # t2_w_nt_norow: FP8 non-trans K = 64 rows x N = 64 B at pitch 64 from SPM - 4096 + 64: the last row runs 64 B past -> RANGE
    C.append(("w_nt_lastrow_over", row(dim, RANGE, SPM - 4096 + 64, dict(n0=64), dict(n0=64, n1=64, s1=64), trans=0, w_base=SPM - 4096 + 64)))
    # t2_a_m_td: A with M = 16 from m_reg (TD n1 = 1, a decoy), K = 64 FP8, s1 = 64, from SPM - 16 * 64 + 64: 64 B past -> RANGE at R[a]
    ab = SPM - 16 * 64 + 64
    C.append(("a_mreg_over", row(dim, RANGE, ab, dict(n0=64, n1=1, s1=64), dict(n0=64, n1=dim, s1=64), a_base=ab, m_idx=4, m=16)))
    C.append(("a_mreg_end", row(dim, OK, 0, dict(n0=64, n1=1, s1=64), dict(n0=64, n1=dim, s1=64), a_base=ab - 64, m_idx=4, m=16)))  # control
    return C


WAIT2 = WAIT_TB.replace("module tr_wait;", "module t2_wait;").replace("PASS tr_wait", "PASS t2_wait").replace("FAIL tr_wait", "FAIL t2_wait")
WAIT2 = WAIT2.replace("    chk(32'h0000_0005, 32'h0000_0005);              // equal: go\n",
                      "    chk(32'h0000_0005, 32'h0000_0005);              // equal: go\n"
                      "    chk(32'hFFFF_FFFD, 32'h0000_0000);              // wval 0, 3 below it (across 2^32): wait   [t2_wait_wval0]\n"
                      "    chk(32'h8000_0000, 32'h0000_0000);              // wval 0, sem 2^31: wait (isa.sem_ready)  [t2_wait_wval0]\n"
                      "    chk(32'h4000_0001, 32'h0000_0001);              // 2^30 ahead: go                      [t2_wait_31bit]\n"
                      "    chk(32'h7FFF_FFFF, 32'h0000_0000);              // 2^31 - 1 ahead: go                  [t2_wait_31bit]\n"
                      "    chk(32'h8000_0000, 32'h0000_0000);              // exactly 2^31: wait (reference; outside isa.md's guarantee) [t2_wait_le]\n")
assert WAIT2.count("t2_wait_31bit") == 2


def main():
    spec = sys.argv[1] if len(sys.argv) > 1 else "rtl"
    d = os.path.join(ROOT, "build", "mxu_review_t2", "dec", spec.replace(":", "_"))
    os.makedirs(d, exist_ok=True)
    names = {}
    for dim in (8, 16):
        C = cases(dim)
        names[dim] = [n for n, _ in C]
        open(os.path.join(d, "t2_cases_%d.hex" % dim), "w").write("".join(r + "\n" for _, r in C))
    n = len(names[8])
    top = ["`timescale 1ns/1ps", "module t2_dec_top;", "  logic d8, d16; int e8, e16;"]
    for dim in (8, 16):
        top.append("  mxu_fault_tc #(.DIM(%d), .N(%d), .SPMB(%d), .ACCR(%d), .FILE(\"%s\")) f%d (.done(d%d), .errors(e%d));" % (
            dim, n, SPM, ACCR, os.path.join(d, "t2_cases_%d.hex" % dim), dim, dim, dim))
    top += ["  t2_wait w ();", "  initial begin wait (d8 && d16); #100;",
            "    if (e8 + e16 == 0) $display(\"PASS t2_dec_cases\"); else $display(\"FAIL t2_dec_cases: %0d / %0d\", e8, e16);",
            "    $finish; end", "endmodule"]
    open(os.path.join(d, "top.sv"), "w").write("\n".join(top) + "\n" + WAIT2)
    files = tr_tb.rtl_files(spec, d)
    exe = os.path.join(d, "sim.vvp")
    c = subprocess.run(["iverilog", "-g2012", "-s", "t2_dec_top", "-o", exe, tr_tb.TB, os.path.join(d, "top.sv")] + files,
                       cwd=os.path.join(ROOT, "build", "mxu"), capture_output=True, text=True)
    if c.returncode:
        print(c.stderr[-1500:])
        sys.exit(2)
    r = subprocess.run(["vvp", "-n", exe], cwd=os.path.join(ROOT, "build", "mxu"), capture_output=True, text=True)
    out = r.stdout
    for ln in out.splitlines():
        if "case" in ln or "t2_wait" in ln or "t2_dec_cases" in ln:
            for dim in (8, 16):
                if "t2_cases_%d.hex" % dim in ln:
                    k = int(ln.split("case ")[1].split()[0])
                    ln += "   [%s, DIM %d]" % (names[dim][k], dim)
            print(ln)
    ok = "PASS t2_dec_cases" in out and "PASS t2_wait" in out
    print("RESULT t2_dec_cases %s %s" % (spec, "PASS" if ok else "FAIL"))


if __name__ == "__main__":
    main()
