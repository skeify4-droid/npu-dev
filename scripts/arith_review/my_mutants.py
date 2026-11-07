"""Reviewer's mutants of rtl/arith, run against a vector directory VEC holding arith/*.hex (default: build, i.e. the
author's vectors from `make arith-vec`). Each: copy rtl/arith, apply
edits (all occurrences when the 4th field is True), run the listed testbenches. 'kill' = some testbench fails."""
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
S = os.path.dirname(os.path.abspath(__file__))
VEC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build")
WORK = os.path.join(ROOT, "build", "arith_review", "mut")

M = [
    # rounding / sticky (fp_round)
    ("rnd_sticky1", [("fp_round.sv", "|norm[WP-2-K:0]}", "norm[WP-2-K]}")], ["fp32_add", "fp32_mul", "cvt_fp32_fp"], "kill"),
    ("rnd_over_ge", [("fp_round.sv", "over    = (code_w > CW'(MAXC));", "over    = (code_w >= CW'(MAXC));")], ["fp32_mul", "cvt_fp32_fp"], "kill"),
    ("rnd_ftz_le", [("fp_round.sv", "(code_w < CW'(1 << MB))", "(code_w <= CW'(1 << MB))")], ["fp32_mul", "bf16_dot2", "cvt_fp32_fp"], "kill"),
    ("rnd_subn1_st", [("fp_round.sv", "r0[1] | r0[0]}", "r0[0]}")], ["fp32_mul", "bf16_dot2"], "equiv"),
    # alignment / swap / sign (fp_addu, fp8_dot4)
    ("swap_nomant", [("fp_addu.sv", "swap = (a_e < b_e) || ((a_e == b_e) && (a_m < b_m));", "swap = (a_e < b_e);")], ["fp32_add", "bf16_dot2"], "kill"),
    ("swap_le", [("fp_addu.sv", "(a_m < b_m)", "(a_m <= b_m)")], ["fp32_add", "bf16_dot2"], "equiv"),
    ("zsign_or", [("fp_addu.sv", "(a_s & b_s & a_z & b_z)", "(a_s | b_s)")], ["fp32_add", "bf16_dot2"], "kill"),
    ("zsign_nz", [("fp_addu.sv", "(a_s & b_s & a_z & b_z)", "(a_s & b_s)")], ["fp32_add", "bf16_dot2"], "equiv"),
    ("dot_zsign_any", [("fp8_dot4.sv", "(!any_nz & all_zero_neg)", "(!any_nz & |neg)")], ["fp8_dot4"], "kill"),
    ("dot_zsign_nz", [("fp8_dot4.sv", "(!any_nz & all_zero_neg)", "all_zero_neg")], ["fp8_dot4"], "equiv"),
    ("dot_width38", [("fp8_dot4.sv", "logic signed [38:0] sum;", "logic signed [37:0] sum;"),
                     ("fp8_dot4.sv", "sum[38]", "sum[37]", True)], ["fp8_dot4"], "kill"),
    ("dot_width37", [("fp8_dot4.sv", "logic signed [38:0] sum;", "logic signed [36:0] sum;"),
                     ("fp8_dot4.sv", "sum[38]", "sum[36]", True), ("fp8_dot4.sv", "sum[37:0]", "38'(sum[35:0])")], ["fp8_dot4"], "kill"),
    # flag wiring
    ("cvt_sat_inf", [("cvt_fp32_fp.sv", "sat & !is_inf & !is_nan", "sat & !is_nan")], ["cvt_fp32_fp"], "kill"),
    ("cvt_ovf_inf", [("cvt_fp32_fp.sv", "ovf & !is_inf & !is_nan", "ovf & !is_nan")], ["cvt_fp32_fp"], "kill"),
    ("add_ovf_inf", [("fp32_add.sv", "ovf & !a_inf & !b_inf & !nan_r", "ovf & !nan_r")], ["fp32_add"], "kill"),
    ("mul_ovf_inf", [("fp32_mul.sv", "ovf & !a_inf & !b_inf & !nan_r", "ovf & !nan_r")], ["fp32_mul"], "kill"),
    ("pe_dot_ovf", [("mxu_pe.sv", "f_add[1] | f_dot_q[1]", "f_add[1]")], ["mxu_pe"], "kill"),
    ("pe_dot_inv", [("mxu_pe.sv", "f_add[0] | f_dot_q[0]", "f_add[0]")], ["mxu_pe"], "kill"),
    ("int_sat_inf", [("cvt_fp32_int.sv", "clamp & !is_inf & !is_nan", "clamp & !is_nan")], ["cvt_fp32_int"], "kill"),
    ("bf16_inv_nanin", [("bf16_dot2.sv", "inv     = !(|in_nan) &&", "inv     = 1'b1 &&")], ["bf16_dot2"], "kill"),
    ("dq_ovf_off", [("mx_dequant_bf16.sv", "{nan, 1'b0, ovf & !nan, 1'b0}", "{nan, 1'b0, 1'b0, 1'b0}")], ["mx_dequant_bf16"], "kill"),
    ("foldq_sat_off", [("fold_quant.sv", "sat & !nan", "1'b0")], ["fold_quant"], "kill"),     # F10: fold_quant alone, c below the element's own
    # mxu_pe pipeline alignment and reset
    ("pe_noalign", [("mxu_pe.sv", ".b(d_q)", ".b(d)")], ["mxu_pe"], "kill"),
    ("pe_flag_skew", [("mxu_pe.sv", "{f_dot_q[3], 1'b0,", "{f_dot[3], 1'b0,")], ["mxu_pe"], "kill"),
    ("pe_flags_norst", [("mxu_pe.sv", "if (!rst_n) flags <= '0;", "if (!rst_n) flags <= f_new;")], ["mxu_pe"], "kill"),
    ("pe_fdot_norst", [("mxu_pe.sv", "if (!rst_n) f_dot_q <= '0;", "if (!rst_n) f_dot_q <= f_dot;")], ["mxu_pe"], "kill"),
    ("pe_mode_dot_q", [("mxu_pe_dot.sv", "assign flags = mode_bf16 ? f16 : f8;", "assign flags = f16 | f8;")], ["mxu_pe_dot", "mxu_pe"], "kill"),
    # acc_update control combinations
    ("acc_ow_part", [("acc_update.sv", "assign y     = overwrite ? p : sum;", "assign y     = overwrite ? part : sum;")], ["acc_update"], "kill"),
    ("acc_ow_flags", [("acc_update.sv", "(overwrite ? 4'd0 : {f_add[3] & nan_add, f_add[2:0]})", "{f_add[3] & nan_add, f_add[2:0]}")], ["acc_update"], "kill"),
    ("acc_rs_flags", [("acc_update.sv", "(rescale ? f_rs : 4'd0)", "f_rs")], ["acc_update"], "kill"),
    ("acc_rs_mux_ow", [("acc_update.sv", "assign acc_r = rescale ? acc_m : acc;", "assign acc_r = (rescale | overwrite) ? acc_m : acc;")], ["acc_update"], "equiv"),
    ("acc_rs_ow_flags", [("acc_update.sv", "(rescale ? f_rs : 4'd0)", "((rescale & !overwrite) ? f_rs : 4'd0)")], ["acc_update"], "kill"),
    # integer saturation limits
    ("int_smin", [("cvt_fp32_int.sv", "neg ? (64'd1 << (BITS - 1))", "neg ? ((64'd1 << (BITS - 1)) - 64'd1)")], ["cvt_fp32_int"], "kill"),
    ("int_smax", [("cvt_fp32_int.sv", ": ((64'd1 << (BITS - 1)) - 64'd1);", ": (64'd1 << (BITS - 1));")], ["cvt_fp32_int"], "kill"),
    ("int_umax", [("cvt_fp32_int.sv", "((64'd1 << BITS) - 64'd1);", "((64'd1 << BITS) - 64'd2);")], ["cvt_fp32_int"], "kill"),
    ("int_uneg", [("cvt_fp32_int.sv", "lim = neg ? 64'd0", "lim = neg ? 64'd1")], ["cvt_fp32_int"], "kill"),
    ("int_rs25", [("cvt_fp32_int.sv", "if (rs > 24)", "if (rs > 25)")], ["cvt_fp32_int"], "equiv"),     # the pre-F9 RTL (reads frac_m[24])
    ("int_sh40", [("cvt_fp32_int.sv", "(sh > 36)", "(sh > 44)")], ["cvt_fp32_int"], "kill"),
    # BF16 PE details
    ("bf16_daz", [("bf16_dot2.sv", "x_z   = (x[14:7] == 8'h00);", "x_z   = (x[14:0] == 15'h0);")], ["bf16_dot2"], "kill"),
    ("bf16_inv0", [("bf16_dot2.sv", "(x_inf & v_z) | (v_inf & x_z)", "(x_inf & v_z)")], ["bf16_dot2"], "kill"),
    # misc
    ("max_gt", [("fp32_max.sv", "a_ge = (am >= bm);", "a_ge = (am > bm);")], ["fp32_max"], "equiv"),
    ("nv_scale_sign", [("mx_exact.sv", "s_s   = sc[7];", "s_s   = 1'b0;")], ["mx_dequant_bf16", "fold_quant"], "kill"),
    ("fold_nan_amax", [("mx_exact.sv", "sig  = (nan | zero) ? 8'd0 : (se * ss);", "sig  = zero ? 8'd0 : (se * ss);")], ["fold_quant"], "kill"),
    ("addu_dws", [("fp_addu.sv", "else if (d >= WS) sx = WS'(1);", "else if (d > WS) sx = WS'(1);")], ["fp32_add", "bf16_dot2"], "equiv"),
]


def run(name, edits, tbs):
    d = os.path.join(WORK, name)
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "rtl", "arith"), d)
    for e in edits:
        f, old, new = e[:3]
        allocc = len(e) > 3 and e[3]
        p = os.path.join(d, f)
        s = open(p).read()
        if old not in s:
            return "NOMATCH", f
        s = s.replace(old, new) if allocc else s.replace(old, new, 1)
        open(p, "w").write(s)
    res = []
    for tb in tbs:
        out = subprocess.run([os.path.join(S, "run_tb.sh"), d, VEC, tb], capture_output=True, text=True,
                             env=dict(os.environ, TMPDIR=os.path.dirname(WORK))).stdout
        res.append(out.splitlines()[0] if out else tb + " ?")
    shutil.rmtree(d, ignore_errors=True)
    killed = any(not r.endswith("PASS") for r in res)
    return ("kill" if killed else "survive"), "; ".join(res)


def main():
    os.makedirs(WORK, exist_ok=True)
    sel = set(sys.argv[2:])
    for name, edits, tbs, pred in M:
        if sel and name not in sel:
            continue
        got, detail = run(name, edits, tbs)
        print(f"{name:16s} predicted {pred:11s} got {got:8s} {detail}", flush=True)


if __name__ == "__main__":
    main()
