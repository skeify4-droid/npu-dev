#!/usr/bin/env python3
"""Review part TR2: tb_mxu's checkers (tb/unit/tb_mxu.sv as of 929d820) in other environments, with one of the TR checks broken
(testbench mutants), or on a mutated RTL, without editing the author's file. A thin layer over tr_tb.py (part TR):

  - the same base patches (TOUT, DLAT, flag-message attribution) and top generator; output in build/mxu_review_t2/tb/<run>/;
  - TR's testbench mutants whose anchor text changed in 929d820 are re-anchored here (legal_off, legal_diag: the legal branch of
    mxu_fault_tc now starts with the drain loop); every other TR patch still applies unchanged;
  - new testbench mutants for the checks TR added (TBM2 below);
  - --rtl also takes t2:<name> (t2_mutants.py).

Usage: as tr_tb.py (python3 scripts/mxu_review/t2_tb.py --run NAME [--rtl SPEC] [--tbm NAME] [--faults] [--plus +BANK=2] [--vec DIR] INST ...);
INST = label=params, e.g. t8=`P8,.STALL_PCT(0),.CHKC(1). With no INST and --faults only the two fault runs are simulated (fast).
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tr_tb  # noqa: E402

ROOT = tr_tb.ROOT
tr_tb.WORK = os.path.join(ROOT, "build", "mxu_review_t2", "tb")

LEGAL_HEAD = "if (cause_exp == 4'd0) begin\n        // run to completion"
TBM2 = {
    # ---- TR's mutants re-anchored (the old anchors "if (cause_exp == 4'd0) begin\n        if (fault_valid)" and "int spm_touch = 0,
    # bad_lane = 0;" / "... saw_ctx = 1'b0;" no longer occur once each)
    "legal_off": [("        if (fault_valid) begin errors++; $display(\"legal case", "        if (1'b0) begin errors++; $display(\"legal case"),
                  ("else if (!saw_ctx) begin errors++;", "else if (1'b0) begin errors++;"),
                  ("if (bad_lane != 0) begin errors++;", "if (1'b0) begin errors++;")],
    # ---- TR-7: the cycle-count bound
    "chkc_off": [("if (CHKC != 0 && bank_mode == 0) begin", "if (1'b0) begin")],
    "chkc_10pct": [("else if (cyc * 100 > longint'(expv[12]) * 102) begin", "else if (cyc * 100 > longint'(expv[12]) * 110) begin")],
    # ---- TR-8: all 64 semaphores (back to rows 1 .. 5 of exp.hex, the pre-TR check) / the compare off
    "sem64_off": [("    for (int i = 0; i < 64; i++)\n      if (sem[i] !== semx[i]) begin",
                   "    for (int i = 1; i <= 5; i++)\n      if (sem[i] !== expv[i][31:0]) begin")],
    "semhex_off": [("      if (sem[i] !== semx[i]) begin errors++;", "      if (1'b0) begin errors++;")],
    # ---- TR-5: the second preload ignored
    "preload2_off": [("if (expv[13][37:32] != 6'd0) begin pre_idx2", "if (1'b0) begin pre_idx2")],
    # ---- TR-3: the drained legal cases back to the 40-cycle window (no drain, no drain / acc_last / row checks)
    "drain_off": [("        while (!(n_done != 0 && dut.inf_cnt == 0) && !fault_valid && tw < 20000) begin @(posedge clk); tw++; end\n"
                   "        repeat (DIM + 20) @(posedge clk);\n", ""),
                  ("else if (!(n_done == 1 && dut.inf_cnt == 0)) begin", "else if (1'b0) begin"),
                  ("if (n_last != 1 || n_rows == 0) begin errors++;", "if (1'b0) begin errors++;"),
                  ("if (bad_row != 0) begin errors++;", "if (1'b0) begin errors++;")],
    "drain_nolast": [("if (n_last != 1 || n_rows == 0) begin errors++;", "if (1'b0) begin errors++;")],
    "drain_norow": [("if (bad_row != 0) begin errors++;", "if (1'b0) begin errors++;")],
    "drain_nodone": [("else if (!(n_done == 1 && dut.inf_cnt == 0)) begin", "else if (1'b0) begin")],
    # the drain loop kept, but the legal case judged at 40 cycles as before (lanes counted only in the first 40 cycles)
    "drain_window40": [("  int spm_touch = 0, bad_lane = 0, tw;", "  int spm_touch = 0, bad_lane = 0, tw, bl40;"),
                       ("        while (!(n_done != 0 && dut.inf_cnt == 0) && !fault_valid && tw < 20000) begin @(posedge clk); tw++; end\n"
                        "        repeat (DIM + 20) @(posedge clk);\n",
                        "        bl40 = bad_lane;\n"
                        "        while (!(n_done != 0 && dut.inf_cnt == 0) && !fault_valid && tw < 20000) begin @(posedge clk); tw++; end\n"
                        "        repeat (DIM + 20) @(posedge clk);\n        bad_lane = bl40;\n")],
    # ---- TR-6: the named coverage counters; inf counter back to the encoding of 914ee30 (inf_cnt == 4)
    "cov_inf_cnt4": [("if (!dut.u_dec.inf_room) begin", "if (dut.inf_cnt == 3'd4) begin")],
    "cov_inf_off": [("if (EXP_INF_FULL != 0 && inf_diff_cyc == 0) begin", "if (1'b0) begin")],
    "cov_cs_off": [("if (EXP_CS_FULL != 0 && cs_full_cyc == 0) begin", "if (1'b0) begin")],
    # ---- TR-8 / TR-5: fcheck of the new rows (sem.hex, exp.hex row count)
    "fcheck_sem_off": [("fcheck(\"sem\", 64, 8);", "")],
    "fcheck_exp12": [("fcheck(\"exp\", 14, 0);", "fcheck(\"exp\", 12, 0);")],
    # ---- vectors of t2_vec.py a5 (a fifth SPM window in exp.hex row 14): not a break, the reader of the 15-row exp.hex
    "win5": [("  int win_base [4];\n  int win_size [4];\n  int win_off  [4];", "  int win_base [5];\n  int win_size [5];\n  int win_off  [5];"),
             ("    for (int w = 0; w < 4; w++)\n      if (win_size[w]", "    for (int w = 0; w < 5; w++)\n      if (win_size[w]"),
             ("for (int i = 0; i < 4; i++) begin win_base[i] = 0;", "for (int i = 0; i < 5; i++) begin win_base[i] = 0;"),
             ("    for (int w = 0; w < 4; w++) begin\n      win_base[w] = int'(expv[8+w][63:32]); win_size[w] = int'(expv[8+w][31:0]);",
              "    for (int w = 0; w < 5; w++) begin\n      win_base[w] = int'(expv[(w < 4) ? 8+w : 14][63:32]); win_size[w] = int'(expv[(w < 4) ? 8+w : 14][31:0]);"),
             ("logic [63:0]           expv [0:13];", "logic [63:0]           expv [0:14];"),
             ("fcheck(\"exp\", 14, 0);", "fcheck(\"exp\", 15, 0);")],
    # ---- diagnostics (not breaks): rows per legal fault case, total cycles of every instance printed anyway
    "legal_rows": [("        if (bad_row != 0) begin errors++;",
                    "        $display(\"LEGALROWS %s case %0d: %0d rows, %0d acc_last, %0d acc_done, highest row seen check %0d\", FILE, k, n_rows, n_last, n_done, bad_row);\n"
                    "        if (bad_row != 0) begin errors++;")],
}
tr_tb.TBM.update(TBM2)
# one more base patch (no check changed): parameter SEED (0 = Icarus' default stream) seeds $urandom at time 0, so the random grant
# stalls, the DONE_EXTRA delays and the DTAB stalls of an instance can be redrawn (Icarus has one global $urandom stream)
tr_tb.BASE.append(("  parameter int DLAT      = -1\n) (", "  parameter int DLAT      = -1,\n  parameter int SEED      = 0\n) ("))
tr_tb.BASE.append(("  int bank_mode = 0;\n", "  int bank_mode = 0;\n  int seed_v = SEED;\n  initial if (SEED != 0) void'($urandom(seed_v));\n"))

_orig_rtl_files = tr_tb.rtl_files

# Legal variants of the RTL (question 3: a design the spec allows that the tests may reject). var:<name>, several edits of mxu.sv.
ACC_ASSIGN = """  assign acc_valid     = y_valid;
  assign acc_row       = x_row;
  assign acc_part      = y;
  assign acc_ncols     = x_ncols;
  assign acc_rescale   = x_resc;
  assign acc_overwrite = x_ovw;
  assign acc_use_cs    = x_ucs;
  assign acc_rowscale  = rs_head[32*rs_idx +: 32];
  assign acc_cs        = cs_mem[cs_rd];
  assign acc_last      = y_valid && x_lasti;
"""
VARIANTS = {
    # sem_inc registered: one cycle after acc_done (or z_inc). mxu.md 5 gives sem_inc no timing ("completion"); isa.md 3: after the
    # ACC is written. Later is legal.
    "sem_reg": [("  assign sem_inc_valid = (inf_pop || z_inc) && (sem_inc_w[9:4] != 6'd0) && (sem_inc_w[3:0] != 4'd0);\n"
                 "  assign sem_inc_idx   = sem_inc_w[9:4];\n  assign sem_inc_amt   = sem_inc_w[3:0];\n",
                 "  logic v2_si; logic [9:0] v2_sw;\n"
                 "  always_ff @(posedge clk or negedge rst_n) if (!rst_n) v2_si <= 1'b0;\n"
                 "    else v2_si <= (inf_pop || z_inc) && (sem_inc_w[9:4] != 6'd0) && (sem_inc_w[3:0] != 4'd0);\n"
                 "  always_ff @(posedge clk) v2_sw <= sem_inc_w;\n"
                 "  assign sem_inc_valid = v2_si;\n  assign sem_inc_idx   = v2_sw[9:4];\n  assign sem_inc_amt   = v2_sw[3:0];\n")],
    # the whole ACC input stream registered once (row, payload, controls, cs, rowscale, acc_last together); fp_flags unchanged, i.e.
    # still at T + i + 3 of mxu.md 5. The ACC contract (acc.md) is relative to the stream itself, so the stream may be one cycle later.
    "acc_reg": [(ACC_ASSIGN,
                 "  always_ff @(posedge clk or negedge rst_n) if (!rst_n) begin acc_valid <= 1'b0; acc_last <= 1'b0; end\n"
                 "    else begin acc_valid <= y_valid; acc_last <= y_valid && x_lasti; end\n"
                 "  always_ff @(posedge clk) begin\n"
                 "    acc_row <= x_row; acc_part <= y; acc_ncols <= x_ncols; acc_rescale <= x_resc; acc_overwrite <= x_ovw; acc_use_cs <= x_ucs;\n"
                 "    acc_rowscale <= rs_head[32*rs_idx +: 32]; acc_cs <= cs_mem[cs_rd];\n"
                 "  end\n")],
}


def rtl_files(spec, d):
    if not (spec.startswith("t2:") or spec.startswith("var:")):
        return _orig_rtl_files(spec, d)
    import t2_mutants as T2
    name = spec.split(":", 1)[1]
    core = os.path.join(ROOT, "rtl", "core")
    files = {f: open(os.path.join(core, f)).read() for f in os.listdir(core) if f.endswith(".sv")}
    if spec.startswith("var:"):
        edits = [("mxu.sv", o, n) for o, n in VARIANTS[name]]
    else:
        m = [x for x in T2.M if x["name"] == name][0]
        edits = [(m["file"], m["old"], m["new"])]
    for f, o, n in edits:
        assert files[f].count(o) == 1, (spec, o[:60])
        files[f] = files[f].replace(o, n)
    cd = os.path.join(d, "core")
    shutil.rmtree(cd, ignore_errors=True)
    os.makedirs(cd)
    for f, s in files.items():
        open(os.path.join(cd, f), "w").write(s)
    arith = sorted(os.path.join(ROOT, "rtl", "arith", f) for f in os.listdir(os.path.join(ROOT, "rtl", "arith")) if f.endswith(".sv"))
    return [os.path.join(ROOT, "isa", "gen", "isa_pkg.sv")] + sorted(os.path.join(cd, f) for f in files) + arith


tr_tb.rtl_files = rtl_files

if __name__ == "__main__":
    tr_tb.main()
