#!/usr/bin/env python3
"""Review part TR: directed cases for the legality survivors of tr_mutants.py, written by hand from mxu.md 4.1 (expected cause and
FAULT_ADDR), not from gen_mxu_vectors.py. They run on the whole MXU through the author's checker `mxu_fault_tc` (tb/unit/tb_mxu.sv,
unchanged: cause + FAULT_ADDR + no SPM / ACC / semaphore action for a fault; no fault + a context + lanes inside the SPM for cause 0),
at DIM 8 and 16, SPM 256 KiB, 256 ACC rows; plus a check of the decoder's semaphore wait (mxu_dec alone, q_ready right after reset)
on serial-number pairs across 2^31 and 2^32 (mxu.md 5: start when (int32)(val - wval) >= 0).

Usage: python3 scripts/mxu_review/tr_dec_cases.py [RTL]     RTL as in tr_tb.py: rtl (default) | tr:<name> | tq:<name> | au:<name>
Prints one line per failing case and "RESULT tr_dec_cases <rtl> PASS|FAIL".
"""
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(__file__))
import tr_tb  # noqa: E402

SPM, ACCR = 1 << 18, 256
RANGE, OK = 7, 0


def td(dtype=3, n0=0, n1=0, s1=0, space=1, n2=1, n3=1, flags=0):
    return dtype | (space << 5) | (flags << 8) | (n0 << 16) | (n1 << 32) | (n2 << 48) | (n3 << 64) | (s1 << 80)


def row(dim, cause, addr, a, w, trans=1, accm=0, a_base=0, w_base=1024, acc=0, m_idx=0, m=0, rs_idx=0, rs=0, cs_idx=0, cs=0):
    ins = (0x60 << 120) | (1 << 86) | (2 << 73) | (3 << 60) | (m_idx << 55) | (rs_idx << 50) | (cs_idx << 45) | (accm << 43) | (trans << 39)
    regs = [a_base, w_base, acc, m, rs, cs]
    rg = sum(r << (64 * k) for k, r in enumerate(regs))
    v = (cause << 1120) | (addr << 1056) | (td(**w) << 800) | (td(**a) << 544) | (ins << 416) | (rg << 32) | 0
    return format(v, "0281x")


def cases(dim):
    """(name, row); every base and s1 is 64 B aligned, so the only possible fault is RANGE (or none)"""
    C = []
    # tr_w_ext_norow: trans W, dim rows of 96 B (K = 96 FP8) at pitch 128 from SPM - (dim - 1) 128 - 64: the last row starts 64 B
    # before the end and runs 32 B past it -> RANGE at R[w]; without the row length the extent ends at SPM - 64
    wb = SPM - (dim - 1) * 128 - 64
    C.append(("w_last_row_over", row(dim, RANGE, wb, dict(n0=96, n1=2, s1=128), dict(n0=96, n1=dim, s1=128), w_base=wb)))
    # control: the same shape 64 B lower ends 32 B before the end (legal)
    C.append(("w_last_row_in", row(dim, OK, 0, dict(n0=96, n1=2, s1=128), dict(n0=96, n1=dim, s1=128), w_base=wb - 64)))
    # tr_w_bf16_elem: the same with BF16 K = 48 (96 B rows): counted in elements the last row would end 16 B before the end
    C.append(("w_bf16_over", row(dim, RANGE, wb, dict(dtype=1, n0=48, n1=2, s1=128), dict(dtype=1, n0=48, n1=dim, s1=128), w_base=wb)))
    # tr_a_range_tol31: FP8 A, M = 1, K = 80 from SPM - 64: ends 16 B past the end -> RANGE at R[a]
    C.append(("a_over16", row(dim, RANGE, SPM - 64, dict(n0=80, n1=1, s1=128), dict(n0=80, n1=dim, s1=128), a_base=SPM - 64)))
    # tr_ktf_bf16: BF16 K = 129 -> 2 rows of the scale array (fold tile 128), N = 16 -> CSP = 64 and 4 N = 64: 128 B from SPM - 64
    C.append(("cs_bf16_k129_over", row(dim, RANGE, SPM - 64, dict(dtype=1, n0=129, n1=2, s1=320), dict(dtype=1, n0=129, n1=16, s1=320),
                                       cs_idx=6, cs=SPM - 64)))
    # control: the same scales from SPM - 128 end exactly at the end (legal)
    C.append(("cs_bf16_k129_end", row(dim, OK, 0, dict(dtype=1, n0=129, n1=2, s1=320), dict(dtype=1, n0=129, n1=16, s1=320),
                                      cs_idx=6, cs=SPM - 128)))
    return C


WAIT_TB = r"""
module tr_wait;
  logic clk = 0, rst_n = 1;          // no clock edge: the decoder stays in D_IDLE, q_ready is combinational (async reset)
  logic q_valid = 1, q_ready;
  logic [127:0] q_instr;
  logic [31:0] q_wval, sem_rd_val;
  logic [5:0] sem_rd_idx;
  logic dtab_req; logic [7:0] dtab_idx;
  logic ctx_push, z_inc, fault_valid; logic [9:0] ctx_sync; logic [3:0] fault_cause; logic [63:0] fault_addr;
  logic c_trans, c_bf16, c_cs_en; logic [1:0] c_accm; logic [15:0] c_K, c_M, c_N, c_KT, c_NT;
  logic [21:0] c_a_base, c_a_pitch, c_w_base, c_w_pitch, c_cs_base, c_rs_base, c_csp; logic [10:0] c_acc_base;
  mxu_dec #(.DIM(8), .ACC_ROWS(256), .SPM_BYTES(262144)) u (.clk, .rst_n, .q_valid, .q_ready, .q_instr, .q_regs('0), .q_wval,
    .sem_rd_idx, .sem_rd_val, .dtab_req, .dtab_idx, .dtab_gnt(1'b0), .dtab_rvalid(1'b0), .dtab_data('0), .inf_room(1'b1),
    .inf_empty(1'b1), .ctx_ready(1'b1), .ctx_push, .z_inc, .ctx_sync, .c_trans, .c_bf16, .c_cs_en, .c_accm, .c_K, .c_M, .c_N, .c_KT,
    .c_NT, .c_a_base, .c_a_pitch, .c_w_base, .c_w_pitch, .c_cs_base, .c_rs_base, .c_csp, .c_acc_base, .fault_valid, .fault_cause, .fault_addr);
  int errs = 0;
  task automatic chk(input logic [31:0] v, input logic [31:0] w);
    logic exp_go;
    sem_rd_val = v; q_wval = w; #1;
    exp_go = $signed(v - w) >= 0;
    if (q_ready !== exp_go) begin errs++; $display("wait case sem %h wval %h: q_ready %b, expected %b", v, w, q_ready, exp_go); end
  endtask
  initial begin
    q_instr = '0; q_instr[119:114] = 6'd7;            // wsem 7
    #2 rst_n = 0; #10 rst_n = 1; #1;              // a negedge for the asynchronous reset
    chk(32'h7FFF_FFFF, 32'h8000_0000);              // one below across 2^31: wait
    chk(32'h8000_0001, 32'h8000_0000);              // go
    chk(32'h8000_0000, 32'h7FFF_FFFF);              // one above across 2^31: go
    chk(32'h7FFF_FFF0, 32'h8000_0010);              // 32 below: wait
    chk(32'hFFFF_FFFF, 32'h0000_0001);              // across 2^32: wait
    chk(32'h0000_0003, 32'hFFFF_FFFF);              // go
    chk(32'h0000_0005, 32'h0000_0005);              // equal: go
    if (errs == 0) $display("PASS tr_wait"); else $display("FAIL tr_wait: %0d", errs);
  end
endmodule
"""


def main():
    spec = sys.argv[1] if len(sys.argv) > 1 else "rtl"
    d = os.path.join(ROOT, "build", "mxu_review_tr", "dec", spec.replace(":", "_"))
    os.makedirs(d, exist_ok=True)
    names = {}
    for dim in (8, 16):
        C = cases(dim)
        names[dim] = [n for n, _ in C]
        open(os.path.join(d, "tr_cases_%d.hex" % dim), "w").write("".join(r + "\n" for _, r in C))
    n = len(names[8])
    top = ["`timescale 1ns/1ps", "module tr_dec_top;", "  logic d8, d16; int e8, e16;"]
    for dim in (8, 16):
        top.append("  mxu_fault_tc #(.DIM(%d), .N(%d), .SPMB(%d), .ACCR(%d), .FILE(\"%s\")) f%d (.done(d%d), .errors(e%d));" % (
            dim, n, SPM, ACCR, os.path.join(d, "tr_cases_%d.hex" % dim), dim, dim, dim))
    top += ["  tr_wait w ();", "  initial begin wait (d8 && d16); #100;",
            "    if (e8 + e16 == 0) $display(\"PASS tr_dec_cases\"); else $display(\"FAIL tr_dec_cases: %0d / %0d\", e8, e16);",
            "    $finish; end", "endmodule"]
    open(os.path.join(d, "top.sv"), "w").write("\n".join(top) + "\n" + WAIT_TB)
    files = tr_tb.rtl_files(spec, d)
    exe = os.path.join(d, "sim.vvp")
    c = subprocess.run(["iverilog", "-g2012", "-s", "tr_dec_top", "-o", exe, tr_tb.TB, os.path.join(d, "top.sv")] + files,
                       cwd=os.path.join(ROOT, "build", "mxu"), capture_output=True, text=True)
    if c.returncode:
        print(c.stderr[-1500:])
        sys.exit(2)
    r = subprocess.run(["vvp", "-n", exe], cwd=os.path.join(ROOT, "build", "mxu"), capture_output=True, text=True)
    out = r.stdout
    for ln in out.splitlines():
        if "case" in ln or "tr_wait" in ln or "tr_dec_cases" in ln:
            for dim in (8, 16):
                if "tr_cases_%d.hex" % dim in ln:
                    k = int(ln.split("case ")[1].split()[0])
                    ln += "   [%s, DIM %d]" % (names[dim][k], dim)
            print(ln)
    ok = "PASS tr_dec_cases" in out and "PASS tr_wait" in out
    print("RESULT tr_dec_cases %s %s" % (spec, "PASS" if ok else "FAIL"))


if __name__ == "__main__":
    main()
