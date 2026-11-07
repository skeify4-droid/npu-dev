"""Second review (51f8cbe): 35 new mutants of rtl/arith, beyond the author's 39 and the first review's 44. Focus: acc_update
flags (F3 fix), cvt_fp32_fp BF16 subnormals (F6 fix), the fold with 256-element tiles (F10), tb_mxu_pe's reset test (F4),
the top of the 39-bit FP8 sum (F5). Same runner as my_mutants.py (copy rtl/arith, apply the edits, run the listed
testbenches through run_tb.sh, which fails on a missing / short / long vector file).

predicted: kill, equiv (cannot change any output; reason in the comment), gap (a real behaviour change that the current
vectors are expected not to reach), cfg (only reachable in a parameter configuration that no testbench simulates).
At the fixes of arith_review2.md every gap and the cfg mutant became a kill (the comment names the new vector), and
acc2_inv_supp is a kill by the decision on A1; the equivalent ones are unchanged.

Usage: scripts/memrun arith_review_mutants2 python3 scripts/arith_review/my_mutants2.py [VECDIR] [name ...]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import my_mutants as MM  # noqa: E402  (VEC is taken from sys.argv[1] there, as here)

FL_OLD = ("assign flags = (rescale ? f_rs : 4'd0) | (use_cs ? f_cs : 4'd0) |\n"
          "                 (overwrite ? 4'd0 : {f_add[3] & nan_add, f_add[2:0]});")
FL_INV_SUPP = ("logic [3:0] fl0;\n  assign fl0 = (rescale ? f_rs : 4'd0) | (use_cs ? f_cs : 4'd0) |\n"
               "                 (overwrite ? 4'd0 : {f_add[3] & nan_add, f_add[2:0]});\n"
               "  assign flags = fl0[3] ? {fl0[3:1], 1'b0} : fl0;")
RO_OLD = ("    always_ff @(posedge clk) psum_out <= sum;\n    always_ff @(posedge clk or negedge rst_n) begin\n"
          "      if (!rst_n) flags <= '0;\n      else        flags <= f_new;\n    end")
RO_MASK1 = ("    logic rq;\n    always_ff @(posedge clk) psum_out <= sum;\n    always_ff @(posedge clk or negedge rst_n) begin\n"
            "      if (!rst_n) begin flags <= '0; rq <= 1'b0; end\n      else begin flags <= rq ? f_new : '0; rq <= 1'b1; end\n    end")

M = [
    # ---- acc_update: the F3 NAN_IN rule
    ("acc2_inf_as_nan", [("acc_update.sv", "assign acc_nan  = (acc[30:23]  == 8'hFF) && (acc[22:0]  != 23'd0);",
                          "assign acc_nan  = (acc[30:23]  == 8'hFF);")], ["acc_update"], "kill"),       # hand row: acc = Inf, part Inf, cs 0 (second-review fix)
    ("acc2_pinf_as_nan", [("acc_update.sv", "assign part_nan = (part[30:23] == 8'hFF) && (part[22:0] != 23'd0);",
                           "assign part_nan = (part[30:23] == 8'hFF);")], ["acc_update"], "kill"),     # hand row: acc = Inf, rowscale 0, RESCALE, part Inf
    ("acc2_nanadd_norsc", [("acc_update.sv", "(!rescale && acc_nan)", "acc_nan")], ["acc_update"], "equiv"),   # rescaled NaN acc: f_rs has NAN_IN
    ("acc2_nanadd_nocs", [("acc_update.sv", "(!use_cs && part_nan)", "part_nan")], ["acc_update"], "equiv"),   # scaled NaN part: f_cs has NAN_IN
    ("acc2_nan_direct", [("acc_update.sv", "{f_add[3] & nan_add, f_add[2:0]}", "{nan_add, f_add[2:0]}")], ["acc_update"], "equiv"),  # nan_add implies f_add[3]
    ("acc2_and", [("acc_update.sv", "(!rescale && acc_nan) || (!use_cs && part_nan)", "(!rescale && acc_nan) && (!use_cs && part_nan)")],
     ["acc_update"], "kill"),
    ("acc2_cs_nan_unused", [("acc_update.sv", "(use_cs ? f_cs : 4'd0)", "(use_cs ? f_cs : {f_cs[3], 3'b000})")], ["acc_update"], "kill"),
    ("acc2_rs_nan_unused", [("acc_update.sv", "(rescale ? f_rs : 4'd0)", "(rescale ? f_rs : {f_rs[3], 3'b000})")], ["acc_update"], "kill"),
    ("acc2_ow_nan", [("acc_update.sv", "(overwrite ? 4'd0 : {f_add[3] & nan_add, f_add[2:0]})",
                      "(overwrite ? {!use_cs && part_nan, 3'b000} : {f_add[3] & nan_add, f_add[2:0]})")], ["acc_update"], "kill"),  # N-5 literal: OVERWRITE copy of a NaN part is NAN_IN
    ("acc2_inv_supp", [("acc_update.sv", FL_OLD, FL_INV_SUPP)], ["acc_update"], "kill"),     # "one unit" reading of A1 rejected: hand row with flags 9
    # ---- cvt_fp32_fp: BF16 subnormals kept (F6)
    ("cvt2_sub_elsb", [("cvt_fp32_fp.sv", "elsb = -12'sd149;", "elsb = -12'sd150;")], ["cvt_fp32_fp"], "kill"),
    ("cvt2_sub_daz", [("cvt_fp32_fp.sv", "mag  = {1'b0, x[22:0]};", "mag  = 24'd0;")], ["cvt_fp32_fp"], "kill"),
    ("cvt2_sub_hidden", [("cvt_fp32_fp.sv", "mag  = {1'b0, x[22:0]};", "mag  = {1'b1, x[22:0]};")], ["cvt_fp32_fp"], "kill"),
    ("cvt2_exp1_as_sub", [("cvt_fp32_fp.sv", "end else if (x[30:23] == 8'h00) begin", "end else if (x[30:23] <= 8'h01) begin")], ["cvt_fp32_fp"], "kill"),
    ("cvt2_bf16_daz_in", [("cvt_fp32_fp.sv", "y = 16'(r);", "y = (FMT == 0 && x[30:23] == 8'h00) ? {x[31], 15'd0} : 16'(r);")], ["cvt_fp32_fp"], "kill"),
    ("cvt2_bf16_minnorm", [("cvt_fp32_fp.sv", "y = 16'(r);",
                            "y = (FMT == 0 && x[30:23] == 8'h00 && r[14:7] == 8'h01) ? {x[31], 15'd0} : 16'(r);")], ["cvt_fp32_fp"], "kill"),
    ("cvt2_zero_sign", [("cvt_fp32_fp.sv", "y = 16'(r);", "y = (r[WO-2:0] == '0) ? 16'd0 : 16'(r);")], ["cvt_fp32_fp"], "kill"),
    # ---- fold: fold_amax / fold_key / fold_c / fold_quant / mx_exact, 64- and 256-element tiles
    ("fold2_hi_eq_lo", [("fold_amax.sv", "assign hi = g_lvl[s-1].v[(2*i+1)*KW +: KW];", "assign hi = g_lvl[s-1].v[(2*i)*KW +: KW];")], ["fold_quant"], "kill"),
    ("fold2_top_half", [("fold_amax.sv", "assign amax = g_lvl[L].v;", "assign amax = g_lvl[L-1].v[KW-1:0];")], ["fold_quant"], "kill"),
    ("fold2_n256_half", [("fold_amax.sv", "assign amax = g_lvl[L].v;", "assign amax = (N == 256) ? g_lvl[L-1].v[KW-1:0] : g_lvl[L].v;")], ["fold_quant"], "kill"),  # only fold256 sees it
    ("fold2_leaf255", [("fold_amax.sv", "assign v = keys;", "assign v = (N == 256) ? {{KW{1'b0}}, keys[(N-1)*KW-1:0]} : keys;")], ["fold_quant"], "kill"),
    ("fold2_pad_ones", [("fold_amax.sv", "assign v = {{((NP - N)*KW){1'b0}}, keys};", "assign v = {{((NP - N)*KW){1'b1}}, keys};")], ["fold_quant"], "kill"),  # amax_chk N = 3, 5, 48, 100, 255 (tb_fold_quant)
    ("fold2_signed_cmp", [("fold_amax.sv", "assign v[i*KW +: KW] = (lo >= hi) ? lo : hi;", "assign v[i*KW +: KW] = ($signed(lo) >= $signed(hi)) ? lo : hi;")], ["fold_quant"], "kill"),
    ("fold2_key_msb", [("fold_amax.sv", "(lo >= hi)", "(lo[KW-2:0] >= hi[KW-2:0])")], ["fold_quant"], "kill"),
    ("fold2_amax_gt", [("fold_amax.sv", "(lo >= hi)", "(lo > hi)")], ["fold_quant"], "equiv"),     # equal keys: same value either way
    ("fold2_nan_zero_el", [("mx_exact.sv", "nan  = e_nan | s_nan;", "nan  = e_nan | (s_nan & !e_z);")], ["fold_quant"], "kill"),  # zero element of a NaN-scale block
    ("fold2_q_c_unsigned", [("fold_quant.sv", "assign e2 = 12'(elsb) - 12'(c);", "assign e2 = 12'(elsb) - 12'($unsigned(c));")], ["fold_quant"], "kill"),
    ("fold2_c_ge", [("fold_c.sv", "else if (key[7:0] > 8'hE0) c = e - 10'sd7;", "else if (key[7:0] >= 8'hE0) c = e - 10'sd7;")], ["fold_quant"], "kill"),
    # ---- fp8_dot4: the top of the 39-bit sum (F5 corner sums)
    ("dot2_sign37", [("fp8_dot4.sv", "s_out   = sum[38] |", "s_out   = sum[37] |")], ["fp8_dot4"], "kill"),
    ("dot2_amag37", [("fp8_dot4.sv", "amag    = sum[38] ? 38'(-sum) : sum[37:0];", "amag    = sum[38] ? 38'(-sum) : {1'b0, sum[36:0]};")], ["fp8_dot4"], "kill"),
    # ---- mxu_pe: reset of the flag registers (F4)
    ("pe2_fdot_ovf_norst", [("mxu_pe.sv", "if (!rst_n) f_dot_q <= '0;", "if (!rst_n) f_dot_q <= {2'b00, f_dot[1], 1'b0};")], ["mxu_pe"], "kill"),  # reset case DOT_OVERFLOW (tb_mxu_pe)
    ("pe2_flags_b2", [("mxu_pe.sv", "if (!rst_n) flags <= '0;", "if (!rst_n) flags <= {1'b0, f_new[2], 2'b00};")], ["mxu_pe"], "equiv"),  # f_new[2] is constant 0
    ("pe2_flags_sync", [("mxu_pe.sv", "always_ff @(posedge clk or negedge rst_n) begin\n      if (!rst_n) flags <= '0;",
                         "always_ff @(posedge clk) begin\n      if (!rst_n) flags <= '0;")], ["mxu_pe"], "kill"),
    ("pe2_fdot_sync", [("mxu_pe.sv", "always_ff @(posedge clk or negedge rst_n) begin\n      if (!rst_n) f_dot_q <= '0;",
                        "always_ff @(posedge clk) begin\n      if (!rst_n) f_dot_q <= '0;")], ["mxu_pe"], "kill"),
    ("pe2_mask_first", [("mxu_pe.sv", RO_OLD, RO_MASK1)], ["mxu_pe"], "kill"),     # FIRST_CYCLE case (tb_mxu_pe)
]


def main():
    os.makedirs(MM.WORK, exist_ok=True)
    sel = set(sys.argv[2:])
    for name, edits, tbs, pred in M:
        if sel and name not in sel:
            continue
        got, detail = MM.run(name, edits, tbs)
        print(f"{name:20s} predicted {pred:6s} got {got:8s} {detail}", flush=True)


if __name__ == "__main__":
    main()
