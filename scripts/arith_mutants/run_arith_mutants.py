#!/usr/bin/env python3
"""Mutation check of the rtl/arith unit tests: copy rtl/arith to build/arith_mutants/<name>, apply one change, run the
unit testbench that should notice, and compare with the expected outcome.

  kill     the testbench must FAIL (the vectors catch the bug)
  survive  the testbench must PASS: the change is equivalent (reason given), so a failure would mean the vectors reject a
           correct design

Usage (one at a time, a few minutes, no VM): scripts/memrun arith_mutants python3 scripts/arith_mutants/run_arith_mutants.py [name ...]
Needs build/arith equal to scripts/arith_vectors.lock (make arith-vec; the run stops otherwise); a mutant run that finds a vector file missing, short or long is an ERROR, not a kill. Exit code 1 if any outcome differs from the expectation.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WORK = os.path.join(ROOT, "build", "arith_mutants")
JAM = ("fp_addu.sv", "sx[0] = sx[0] | lost;", "sx[0] = sx[0];")

# (name, [(file, old, new), ...], testbench, expectation, note)
MUTANTS = [
    # guard bits and sticky of the aligner: SW + G >= 27 is the bound (fp_addu.sv)
    ("g_fp32_2", [("fp32_add.sv", ".G(4)", ".G(2)")], "fp32_add", "kill", "SW+G = 26, below the bound"),
    ("g_fp32_3", [("fp32_add.sv", ".G(4)", ".G(3)")], "fp32_add", "survive", "SW+G = 27 is the bound"),
    ("g_fp32_3_nojam", [("fp32_add.sv", ".G(4)", ".G(3)"), JAM], "fp32_add", "kill", "no sticky at the bound"),
    ("g_fp32_4_nojam", [JAM], "fp32_add", "kill", "no sticky OR"),
    ("g_bf16_10", [("bf16_dot2.sv", ".G(12)", ".G(10)")], "bf16_dot2", "kill", "SW+G = 26"),
    ("g_bf16_11", [("bf16_dot2.sv", ".G(12)", ".G(11)")], "bf16_dot2", "survive", "SW+G = 27 is the bound"),
    ("g_bf16_11_nojam", [("bf16_dot2.sv", ".G(12)", ".G(11)"), JAM], "bf16_dot2", "kill", "no sticky at the bound"),
    ("g_bf16_12_nojam", [JAM], "bf16_dot2", "kill", "no sticky OR"),
    # subnormal handling of fp_round
    ("bf16_subn0", [("bf16_dot2.sv", ".SUBN_MAX(1)", ".SUBN_MAX(0)")], "bf16_dot2", "kill", "sum rounding up to 2^-126"),
    ("mul_subn0", [("fp32_mul.sv", ".SUBN_MAX(1)", ".SUBN_MAX(0)")], "fp32_mul", "kill", "product rounding up to 2^-126"),
    ("add_subn_full", [("fp32_add.sv", ".SUBN_MAX(0)", ".SUBN_MAX(1000)")], "fp32_add", "survive", "sums below 2^-126 are exact"),
    ("subn_nojam", [("fp_round.sv", "r1[0] = r1[0] | (|(r0 & ((((K+2)'(1)) << sh) - (K+2)'(1))));", "r1[0] = r1[0];")],
     "cvt_fp32_fp", "kill", "sticky of the subnormal shift"),
    # rounding
    ("rne_tie", [("fp_round.sv", "inc  = g & (st | kept[0]);", "inc  = g & st;")], "fp32_mul", "kill", "ties rounded down"),
    ("rne_up", [("fp_round.sv", "inc  = g & (st | kept[0]);", "inc  = g;")], "cvt_fp32_fp", "kill", "ties rounded up"),
    ("int_tie", [("cvt_fp32_int.sv", "|| m[0])) m = m + 64'd1;", "|| 1'b0)) m = m + 64'd1;")], "cvt_fp32_int", "kill", "ties not to even"),
    # PE and dot products
    ("fp8mul_shift", [("fp8_mul.sv", "- 5'd2;", "- 5'd1;")], "fp8_mul", "kill", "product exponent"),
    ("dot_negzero", [("fp8_dot4.sv", "s_out   = sum[38] | (!any_nz & all_zero_neg);", "s_out   = sum[38];")], "fp8_dot4", "kill", "-0 sum"),
    ("bf16_inv", [("bf16_dot2.sv", "(p_inf[0] && p_inf[1] && (p_s[0] != p_s[1]))", "(p_inf[0] && p_inf[1] && (p_s[0] == p_s[1]))")],
     "bf16_dot2", "kill", "Inf - Inf flag"),
    ("bf16_prod_norm", [("bf16_dot2.sv", "(prod[15] ? prod : {prod[14:0], 1'b0})", "(prod[15] ? prod : prod)")], "bf16_dot2", "kill", "product normalization"),
    ("bf16_ovf_flag", [("bf16_dot2.sv", "ovf & !(|p_inf) & !nan_r", "1'b0")], "bf16_dot2", "kill", "overflow flag"),
    ("pe_mode", [("mxu_pe_dot.sv", "assign d     = mode_bf16 ? d16 : d8;", "assign d     = mode_bf16 ? d8 : d16;")], "mxu_pe_dot", "kill", "mode mux"),
    ("pe_flags", [("mxu_pe.sv", "{f_dot_q[3], 1'b0, f_add[1] | f_dot_q[1], f_add[0] | f_dot_q[0]}", "{f_add[3], 1'b0, f_add[1] | f_dot_q[1], f_add[0] | f_dot_q[0]}")],
     "mxu_pe", "kill", "NAN_IN of the add instead of the dot"),
    # FP32 add / mul / max / ACC
    ("add_no_daz", [("fp32_add.sv", "a_z   = (a[30:23] == 8'h00);", "a_z   = (a == 32'h0) || (a == 32'h80000000);")], "fp32_add", "kill", "DAZ"),
    ("mul_elsb", [("fp32_mul.sv", "- 12'sd300;", "- 12'sd299;")], "fp32_mul", "kill", "exponent"),
    ("acc_flags", [("acc_update.sv", "(use_cs ? f_cs : 4'd0)", "(use_cs ? f_rs : 4'd0)")], "acc_update", "kill", "flag source"),
    ("acc_nan_made", [("acc_update.sv", "{f_add[3] & nan_add, f_add[2:0]}", "f_add")], "acc_update", "kill", "NAN_IN for a NaN the lane's own multiplier made (F3)"),
    ("acc_nan_part", [("acc_update.sv", "(!rescale && acc_nan) || (!use_cs && part_nan)", "(!use_cs && part_nan)")], "acc_update", "kill", "NaN acc added without NAN_IN"),
    ("int_rs24_off", [("cvt_fp32_int.sv", "if (rs > 24)", "if (rs > 23)")], "cvt_fp32_int", "kill", "values in [0.25, 0.5) rounding (rs = 24 is a real rounding case)"),
    ("max_zero", [("fp32_max.sv", "a_ge = ~a[31] | b[31];", "a_ge = 1'b1;")], "fp32_max", "kill", "max(-0, +0)"),
    ("max_nan", [("fp32_max.sv", "if (a_nan | b_nan) y = 32'h7FC00000;", "if (a_nan) y = 32'h7FC00000; else if (b_nan) y = b;")], "fp32_max", "kill", "NaN result"),
    ("max_daz", [("fp32_max.sv", "am    = (a[30:23] == 8'h00) ? 31'd0 : a[30:0];", "am    = a[30:0];")], "fp32_max", "kill", "DAZ"),
    # converters, dequantizer, fold
    ("bf16_ftz", [("cvt_fp32_fp.sv", "localparam bit FTZ  = 1'b0;", "localparam bit FTZ  = (FMT == 0);")], "cvt_fp32_fp", "kill", "BF16 subnormals flushed (F6: kept)"),
    ("sat_off_e4m3", [("cvt_fp32_fp.sv", "localparam bit SAT  = (FMT >= 2);", "localparam bit SAT  = (FMT >= 3);")], "cvt_fp32_fp", "kill", "E4M3 saturation"),
    ("dec_bias", [("cvt_dec.sv", "- 12'(BIAS + MB);", "- 12'(BIAS + MB + 1);")], "cvt_dec", "kill", "decode exponent"),
    ("mx_exp", [("mx_exact.sv", "- 10'sd10;\n      e_nan = (el[6:0] == 7'h7F);", "- 10'sd9;\n      e_nan = (el[6:0] == 7'h7F);")], "mx_dequant_bf16", "kill", "E4M3 element exponent"),
    ("fold_key_off", [("fold_key.sv", "e_msb + 10'sd256", "e_msb + 10'sd255")], "fold_quant", "kill", "key offset"),
    ("foldc_lo", [("fold_c.sv", "key[7:0] > 8'hE0", "key[7:0] >= 8'hE0")], "fold_quant", "kill", "1.75 * 2^e exactly"),
    ("foldc_hi", [("fold_c.sv", "key[7:0] > 8'hE0", "key[7:0] > 8'hE1")], "fold_quant", "survive", "a normalized significand of exactly 0xE1 cannot occur (E2M1 / E4M3 elements x E8M0 / E4M3 scales)"),
    ("amax_tree", [("fold_amax.sv", "(lo >= hi) ? lo : hi", "(lo >= hi) ? hi : lo")], "fold_quant", "kill", "min instead of max"),
]


def run(name, edits, tb, expect, note):
    d = os.path.join(WORK, name)
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "rtl", "arith"), d)
    for f, old, new in edits:
        p = os.path.join(d, f)
        s = open(p).read()
        if old not in s:
            return "NOMATCH (the RTL text changed: update the mutant)"
        open(p, "w").write(s.replace(old, new, 1))
    exe = os.path.join(d, "sim")
    srcs = sorted(os.path.join(d, x) for x in os.listdir(d) if x.endswith(".sv"))
    # compiled inside build/arith: Icarus finds `include "counts.svh"` in the compile directory first, i.e. the locked file
    r = subprocess.run(["iverilog", "-g2012", "-o", exe, os.path.join(ROOT, "tb", "unit", f"tb_{tb}.sv")] + srcs,
                       cwd=os.path.join(ROOT, "build", "arith"), capture_output=True, text=True)
    if r.returncode:
        return "COMPILE-ERROR " + r.stderr[-300:]
    r = subprocess.run(["vvp", "-n", exe], cwd=os.path.join(ROOT, "build"), capture_output=True, text=True)
    shutil.rmtree(d, ignore_errors=True)
    if any(w in r.stdout + r.stderr for w in ("Unable to open", "Not enough words", "Too many words")):
        return "ERROR vector file missing, short or long (make arith-vec; make unit)"
    return "survive" if any(l.startswith("PASS") for l in r.stdout.splitlines()) else "kill"


def main():
    if subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "arith_lock.py"), "verify",
                       os.path.join(ROOT, "build", "arith")]).returncode:
        sys.exit("build/arith differs from scripts/arith_vectors.lock: run `make arith-vec` first")
    sel = set(sys.argv[1:])
    bad = 0
    for name, edits, tb, expect, note in MUTANTS:
        if sel and name not in sel:
            continue
        got = run(name, edits, tb, expect, note)
        ok = got == expect
        bad += not ok
        print(f"{'ok  ' if ok else 'DIFF'} {name:16s} {tb:16s} expected {expect:8s} got {got:8s} {note}", flush=True)
    print("arith mutants:", "all as expected" if not bad else f"{bad} differ")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
