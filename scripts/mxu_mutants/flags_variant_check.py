#!/usr/bin/env python3
"""The fp_flags port contract of mxu.md 5 (review TR2-9): the flags belong to the wave's own output row, so a register stage on the whole
output (acc_* stream and fp_flags together) is legal and `tb_mxu` must pass it, while registering only one side breaks the relation and must fail
the flag check. Not RTL mutants (the first variant is a legal one), so they are not in mxu_mutants.py.

  both_reg   acc_* stream and fp_flags registered once               -> PASS
  acc_reg    only the acc_* stream registered (flags one cycle early) -> FAIL with fp_flags errors
  flags_reg  only fp_flags registered (flags one cycle late)          -> FAIL with fp_flags errors

tb_mxu -DQUICK (the 8 x 8 instance with CHKP and the DIM 8 faults, CHKC off by construction: +1 cycle is within its 2 %). Needs build/mxu.
Usage: scripts/memrun flags_variant python3 scripts/mxu_mutants/flags_variant_check.py      (about 1 minute, 0.2 GB)
"""
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VEC = os.path.join(ROOT, "build", "mxu")
WORK = os.path.join(ROOT, "build", "mxu_mutants", "flags_variant")

ACC = """  assign acc_valid     = y_valid;
  assign acc_row       = x_row;
  assign acc_part      = y;
  assign acc_ncols     = x_ncols;
  assign acc_rescale   = x_resc;
  assign acc_overwrite = x_ovw;
  assign acc_use_cs    = x_ucs;
  assign acc_rowscale  = rs_head[32*rs_idx +: 32];
  assign acc_cs        = cs_mem[cs_rd];
  assign acc_last      = y_valid && x_lasti;
  assign fp_flags      = arr_flags;
"""
ACC_REG = """  always_ff @(posedge clk or negedge rst_n) if (!rst_n) begin acc_valid <= 1'b0; acc_last <= 1'b0; end
    else begin acc_valid <= y_valid; acc_last <= y_valid && x_lasti; end
  always_ff @(posedge clk) begin
    acc_row <= x_row; acc_part <= y; acc_ncols <= x_ncols; acc_rescale <= x_resc; acc_overwrite <= x_ovw; acc_use_cs <= x_ucs;
    acc_rowscale <= rs_head[32*rs_idx +: 32]; acc_cs <= cs_mem[cs_rd];
  end
"""
FLG_REG = """  always_ff @(posedge clk or negedge rst_n) if (!rst_n) fp_flags <= 4'd0; else fp_flags <= arr_flags;
"""
FLG = "  assign fp_flags      = arr_flags;\n"

VARIANTS = [("both_reg", ACC_REG + FLG_REG, True), ("acc_reg", ACC_REG + FLG, False), ("flags_reg", ACC.replace(FLG, "") + FLG_REG, False)]


def main():
    bad = 0
    for name, new, passes in VARIANTS:
        d = os.path.join(WORK, name)
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        core = os.path.join(ROOT, "rtl", "core")
        for f in os.listdir(core):
            shutil.copy(os.path.join(core, f), d)
        txt = open(os.path.join(d, "mxu.sv")).read()
        assert txt.count(ACC) == 1
        open(os.path.join(d, "mxu.sv"), "w").write(txt.replace(ACC, new))
        srcs = [os.path.join(ROOT, "tb/unit/tb_mxu.sv"), os.path.join(ROOT, "isa/gen/isa_pkg.sv")] + \
            sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".sv")) + \
            sorted(os.path.join(ROOT, "rtl/arith", f) for f in os.listdir(os.path.join(ROOT, "rtl/arith")) if f.endswith(".sv"))
        exe = os.path.join(d, "tb.vvp")
        c = subprocess.run(["iverilog", "-g2012", "-DQUICK", "-o", exe] + srcs, cwd=VEC, capture_output=True, text=True)
        if c.returncode != 0:
            sys.exit("compile failed (%s): %s" % (name, c.stderr[-500:]))
        out = subprocess.run(["vvp", "-n", exe], cwd=VEC, capture_output=True, text=True, timeout=1800).stdout
        ok_pass = bool(re.search(r"^PASS mxu", out, re.M))
        nflag = len(re.findall(r"fp_flags .* at cycle", out))
        ok = ok_pass == passes and (passes or nflag > 0)
        bad += 0 if ok else 1
        print("%-10s %s   fp_flags errors shown: %d   %s" % (name, "PASS" if ok_pass else "FAIL", nflag, "as expected" if ok else "<-- UNEXPECTED"), flush=True)
    print("variants: %d, unexpected: %d" % (len(VARIANTS), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
