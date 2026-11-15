#!/usr/bin/env python3
"""Review part TR: run tb_mxu's checkers (mxu_tc / mxu_fault_tc of tb/unit/tb_mxu.sv) in other legal environments, with one of its
checks broken (testbench mutants), or with diagnostics, without editing the author's file.

A patched copy of tb/unit/tb_mxu.sv is written to build/mxu_review_tr/tb/<run>/tb_mxu_tr.sv. Base patches (each must apply exactly
once), which do not change any check:
  TOUT  parameter (default 40000): the cycle limit of the program (the original has 40000, 6000 with -DQUICK)
  DLAT  parameter (default -1 = ACC_LAT): the delay of acc_done after the acc_last row; mxu.md 5 allows the MXU's ACC_LAT to be above
        the ACC's own delay ("idle cycles only"), so DLAT < ACC_LAT is a legal environment
  the fp_flags mismatch message also prints the rows seen so far and the instruction of the last row (attribution)
A testbench mutant (--tbm NAME, TBM below) adds one more patch that disables or breaks one check.
The top is generated (tr_top): one mxu_tc per instance spec, optionally the two mxu_fault_tc runs, and "TR <label> errors <n>" per
instance, then "PASS tr_top" / "FAIL tr_top". The vectors are build/mxu (or --vec DIR); `P8`, `P16`, `P32`, `P8X`, `P16X` are the
author's parameter macros (vector sizes from counts.svh).

RTL: --rtl rtl (default, unmutated) | tr:<name> (tr_mutants.py) | tq:<name> (tq_mutants.py) | au:<name> (scripts/mxu_mutants).

Usage: tr_tb.py --run NAME [--rtl SPEC] [--tbm NAME] [--faults] [--plus +BANK=2] [--vec DIR] INST ...
       INST = label=params, e.g.  t8=`P8,.STALL_PCT(0),.ACC_LAT(24),.TOUT(400000)
Prints the result line "RESULT <run> <PASS|FAIL> <seconds>" and the per-instance lines; output in build/mxu_review_tr/tb/<run>/.
"""
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WORK = os.path.join(ROOT, "build", "mxu_review_tr", "tb")
TB = os.path.join(ROOT, "tb", "unit", "tb_mxu.sv")

BASE = [
    ("  parameter string PRE    = \"\"\n) (",
     "  parameter string PRE    = \"\",\n  parameter int TOUT      = 40000,\n  parameter int DLAT      = -1\n) ("),
    ("repeat (`ifdef QUICK 6000 `else 40000 `endif) @(posedge clk);", "repeat (TOUT) @(posedge clk);"),
    ("done_at[dq_wr % 1024] = cyc + ACC_LAT + (DONE_EXTRA", "done_at[dq_wr % 1024] = cyc + ((DLAT >= 0) ? DLAT : ACC_LAT) + (DONE_EXTRA"),
    ("$display(\"%s: fp_flags %h at cycle %0d, expected %h\", PRE, fobs[fx], cyc - DIM + 1, fexp[fx]);",
     "$display(\"%s: fp_flags %h at cycle %0d, expected %h (rows seen %0d, instruction of the last row %0d)\", PRE, fobs[fx], cyc - DIM + 1, fexp[fx], rows_seen, row_ins);"),
]

# testbench mutants: one check disabled or broken (Q2 of part TR)
TBM = {
    "fcheck_off": [("    fd = $fopen({PRE, name, \".hex\"}, \"r\");", "    return;\n    fd = $fopen({PRE, name, \".hex\"}, \"r\");"),
                   ("    fd = $fopen(FILE, \"r\");\n    n = 0;", "    return;\n    fd = $fopen(FILE, \"r\");\n    n = 0;")],
    "retire_seq_off": [("if (sem_inc_idx != ins_sidx[sem_ins[sem_ptr]] || sem_inc_amt != ins_samt[sem_ins[sem_ptr]]) begin", "if (1'b0) begin")],
    "retire_early_off": [("if (done_cnt < need_done[sem_ins[sem_ptr]]) begin", "if (1'b0) begin")],
    "flag_ring_off": [("if (fobs[fx] !== fexp[fx]) begin", "if (1'b0) begin")],
    "done_early1": [("done_at[dq_wr % 1024] = cyc + ((DLAT >= 0) ? DLAT : ACC_LAT) + (DONE_EXTRA",
                     "done_at[dq_wr % 1024] = cyc + ((DLAT >= 0) ? DLAT : ACC_LAT) - 1 + (DONE_EXTRA")],
    "done_noextra": [("(DONE_EXTRA > 0 ? $urandom_range(0, DONE_EXTRA) : 0)", "0")],
    "done_noorder": [("if (done_at[dq_wr % 1024] <= last_sched) done_at[dq_wr % 1024] = last_sched + 1;", "")],
    "chkp_off": [("if (CHKP != 0 && cyc - last_row_cyc > ins_maxp[row_ins]) begin", "if (1'b0) begin")],
    "preload_off": [("if (expv[7][37:32] != 6'd0) begin pre_idx", "if (1'b0) begin pre_idx")],
    "acclast_off": [("if (acc_last !== (row_in == ins_rows[row_ins] - 1)) begin", "if (1'b0) begin")],
    "legal_off": [("if (cause_exp == 4'd0) begin\n        if (fault_valid)", "if (cause_exp == 4'd0) begin\n        if (1'b0)"),
                  ("else if (!saw_ctx) begin errors++;", "else if (1'b0) begin errors++;"),
                  ("if (bad_lane != 0) begin errors++;", "if (1'b0) begin errors++;")],
    "flag_or_off": [("if (flags_or !== expv[0][3:0]) begin", "if (1'b0) begin")],
    # diagnostic, not a break: per legal fault-file case, the end of the highest lane requested on each port within the case's 40
    # cycles, and the ACC rows presented (does the window reach the boundary the case is about?)
    "legal_diag": [
        ("  int spm_touch = 0, bad_lane = 0;",
         "  int spm_touch = 0, bad_lane = 0;\n  int mx_a = 0, mx_w = 0, mx_s = 0, n_acc = 0, mx_acc = 0;\n"
         "  always @(posedge clk) if (rst_n) begin\n"
         "    for (int l = 0; l < 4; l++) if (a_req_v[l] && int'(a_req_addr[22*l +: 22]) + LB > mx_a) mx_a = int'(a_req_addr[22*l +: 22]) + LB;\n"
         "    for (int l = 0; l < 8; l++) if (w_req_v[l] && int'(w_req_addr[22*l +: 22]) + LB > mx_w) mx_w = int'(w_req_addr[22*l +: 22]) + LB;\n"
         "    if (s_req_v && int'(s_req_addr) + LB > mx_s) mx_s = int'(s_req_addr) + LB;\n"
         "    if (acc_valid) begin n_acc++; if (int'(acc_row) + 1 > mx_acc) mx_acc = int'(acc_row) + 1; end\n"
         "  end"),
        ("      rst_n = 0; q_valid = 0; spm_touch = 0; bad_lane = 0; saw_ctx = 1'b0;",
         "      rst_n = 0; q_valid = 0; spm_touch = 0; bad_lane = 0; saw_ctx = 1'b0; mx_a = 0; mx_w = 0; mx_s = 0; n_acc = 0; mx_acc = 0;"),
        ("        if (bad_lane != 0) begin errors++; $display(\"legal case %0d (%s): %0d lane requests outside the SPM\", k, FILE, bad_lane); end",
         "        if (bad_lane != 0) begin errors++; $display(\"legal case %0d (%s): %0d lane requests outside the SPM\", k, FILE, bad_lane); end\n"
         "        $display(\"LEGALDIAG %s case %0d: highest lane end A %0d W %0d S %0d (SPM %0d, i.e. A -%0d W -%0d S -%0d), ACC rows presented %0d, highest row + 1 %0d (ACCR %0d)\",\n"
         "                 FILE, k, mx_a, mx_w, mx_s, SPMB, SPMB - mx_a, SPMB - mx_w, SPMB - mx_s, n_acc, mx_acc, ACCR);"),
    ],
}


def patch(src, pairs):
    for old, new in pairs:
        n = src.count(old)
        if n != 1:
            raise SystemExit("patch: %r occurs %d times" % (old[:70], n))
        src = src.replace(old, new)
    return src


def rtl_files(spec, d):
    """the rtl/core files of the RTL spec (a fresh copy for a mutant), plus isa_pkg and rtl/arith"""
    core = os.path.join(ROOT, "rtl", "core")
    files = {f: open(os.path.join(core, f)).read() for f in os.listdir(core) if f.endswith(".sv")}
    if spec != "rtl":
        kind, name = spec.split(":", 1)
        sys.path.insert(0, os.path.join(ROOT, "scripts", "mxu_review"))
        sys.path.insert(0, os.path.join(ROOT, "scripts", "mxu_mutants"))
        if kind == "tr":
            import tr_mutants as T
            m = [x for x in T.M if x["name"] == name][0]
            f, old, new = m["file"], m["old"], m["new"]
        elif kind == "tq":
            import tq_mutants as T
            m = [x for x in T.M if x["name"] == name][0]
            f, old, new = m["file"], m["old"], m["new"]
        else:
            import mxu_mutants as T
            m = [x for x in T.M if x[0] == name][0]
            f, old, new = m[1], m[2], m[3]
        assert files[f].count(old) == 1, (spec, f)
        files[f] = files[f].replace(old, new)
    cd = os.path.join(d, "core")
    shutil.rmtree(cd, ignore_errors=True)
    os.makedirs(cd)
    for f, s in files.items():
        open(os.path.join(cd, f), "w").write(s)
    arith = sorted(os.path.join(ROOT, "rtl", "arith", f) for f in os.listdir(os.path.join(ROOT, "rtl", "arith")) if f.endswith(".sv"))
    return [os.path.join(ROOT, "isa", "gen", "isa_pkg.sv")] + sorted(os.path.join(cd, f) for f in files) + arith


def top(insts, faults):
    L = ["`timescale 1ns/1ps", "module tr_top;"]
    n = len(insts)
    for k, (lab, par) in enumerate(insts):
        L.append("  logic d%d; int e%d;" % (k, k))
        L.append("  mxu_tc #(%s) %s (.done(d%d), .errors(e%d));" % (par, lab, k, k))
    if faults:
        L.append("  logic df8, df16; int ef8, ef16;")
        L.append("  mxu_fault_tc #(.DIM(8), .N(`CNT_MXU_FAULT_8), .SPMB(`CNT_MXU_8_SPM), .ACCR(`CNT_MXU_8_ACC0), .FILE(\"mxu_fault_8.hex\")) tf8 (.done(df8), .errors(ef8));")
        L.append("  mxu_fault_tc #(.DIM(16), .N(`CNT_MXU_FAULT_16), .SPMB(`CNT_MXU_16_SPM), .ACCR(`CNT_MXU_16_ACC0), .FILE(\"mxu_fault_16.hex\")) tf16 (.done(df16), .errors(ef16));")
    L.append("  int tot;")
    L.append("  initial begin")
    w = " && ".join(["d%d" % k for k in range(n)] + (["df8", "df16"] if faults else [])) or "1'b1"
    L.append("    wait (%s);" % w)
    L.append("    tot = 0;")
    for k, (lab, _) in enumerate(insts):
        L.append("    $display(\"TR %s errors %%0d\", e%d); tot += e%d;" % (lab, k, k))
    if faults:
        L.append("    $display(\"TR tf8 errors %0d\", ef8); $display(\"TR tf16 errors %0d\", ef16); tot += ef8 + ef16;")
    L.append("    if (tot == 0) $display(\"PASS tr_top\"); else $display(\"FAIL tr_top: %0d errors\", tot);")
    L.append("    $finish;")
    L.append("  end")
    L.append("endmodule")
    return "\n".join(L) + "\n"


def main():
    a = sys.argv[1:]

    def opt(k, dflt=None):
        if k in a:
            i = a.index(k)
            v = a[i + 1]
            del a[i:i + 2]
            return v
        return dflt
    run = opt("--run")
    spec = opt("--rtl", "rtl")
    tbm = opt("--tbm")
    plus = opt("--plus")
    vec = os.path.abspath(opt("--vec", os.path.join(ROOT, "build", "mxu")))
    faults = "--faults" in a
    if faults:
        a.remove("--faults")
    if not run:
        sys.exit(__doc__)
    insts = []
    for s in a:
        lab, par = s.split("=", 1)
        insts.append((lab, par))
    d = os.path.join(WORK, run)
    os.makedirs(d, exist_ok=True)
    src = patch(open(TB).read(), BASE + (TBM[tbm] if tbm else []))
    tbp = os.path.join(d, "tb_mxu_tr.sv")
    open(tbp, "w").write(src)
    tp = os.path.join(d, "tr_top.sv")
    open(tp, "w").write(top(insts, faults))
    files = rtl_files(spec, d)
    exe = os.path.join(d, "sim.vvp")
    t0 = time.time()
    c = subprocess.run(["iverilog", "-g2012", "-s", "tr_top", "-o", exe, tbp, tp] + files, cwd=vec, capture_output=True, text=True)
    if c.returncode:
        print(c.stderr[-2000:])
        print("RESULT %s COMPILE-ERROR 0" % run)
        sys.exit(2)
    cmd = ["vvp", "-n", exe] + ([plus] if plus else [])
    r = subprocess.run(cmd, cwd=vec, capture_output=True, text=True)
    out = r.stdout + r.stderr
    open(os.path.join(d, "sim.log"), "w").write(out)
    dt = time.time() - t0
    for ln in out.splitlines():
        if not re.match(r"^(VCD|.*\$finish)", ln):
            print(ln)
    bad_vec = re.search(r"Unable to open|Not enough words|Too many words", out)
    ok = re.search(r"^PASS tr_top", out, re.M) and not bad_vec
    print("RESULT %s %s %.0f rtl=%s tbm=%s plus=%s" % (run, "PASS" if ok else "FAIL", dt, spec, tbm or "-", plus or "-"))


if __name__ == "__main__":
    main()
