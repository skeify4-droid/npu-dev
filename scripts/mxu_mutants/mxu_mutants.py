#!/usr/bin/env python3
"""One-change mutants of rtl/core/mxu*.sv, run against tb_mxu (iverilog) and, for the control mutants, against tb/formal/mxu.sby (mxu) and mxu_dec.sby (the decoder).

Usage (always under scripts/memrun, one job at a time; the vectors of `make mxu-vec` must exist in build/mxu):
  scripts/memrun mxu_mutants_tb python3 scripts/mxu_mutants/mxu_mutants.py tb [--quick] [NAME ...]
  scripts/memrun mxu_mutants_formal python3 scripts/mxu_mutants/mxu_mutants.py formal [NAME ...]
  python3 scripts/mxu_mutants/mxu_mutants.py list

Every mutant is a copy of rtl/core with exactly one textual change (`old` must occur exactly once). Expectation per mutant:
  kill   tb_mxu (full, or --quick) must not print PASS
  kill_full  killed by the full tb_mxu only (e.g. the ACC_LAT = 24 run); in --quick mode either result is fine
  formal_blind  the proofs cannot see it (M <= 2): tb_mxu must kill it, and sby bmc_a is expected to PASS (a recorded limit of the proofs)
  equiv  an equivalent change (not visible at the ACC / the fault port): tb_mxu must still PASS
  formal the control mutants of the proofs: sby task bmc_a (depth 56) must fail (or, for `equiv_f`, pass)
  formal_x  control mutants that need M >= 3 (ACC spacing across instructions, M-22): sby task bmc_d must fail, and tb_mxu must kill them
  formal_dec  mutants of the decoder's M = 0 path and checks: tb/formal/mxu_dec.sby task bmc (depth 40, the real mxu_dec) must fail
  equiv_dec  an equivalent change of the decoder: the same proof must still pass
Review part I (2026-11-12), run by `misc` mode (not by `tb` / `formal`):
  guard  an elaboration guard (I-10) removed or loosened: scripts/mxu_mutants/guard_check.py on the mutated copy must fail
  equiv_guard  a guard that another check duplicates: guard_check.py must still pass
  miter  a narrowed width of the decoder (I-3): tb_dec_miter (mutated mxu_dec against the decoder of the reviewed commit, DIM 8 and 64)
         must report a mismatch
  equiv_miter  a change of the narrowed decoder that is equivalent: the miter must still pass
  (the I-9 and I-7 mutants are ordinary `kill` mutants of `tb` mode: tb_mxu has a spurious acc_done after reset)
  scripts/memrun mxu_mutants_misc python3 scripts/mxu_mutants/mxu_mutants.py misc [NAME ...]
Exit status 0 when every mutant behaved as its expectation says.
"""
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BUILD = os.path.join(ROOT, "build", "mxu_mutants")
SBY = os.environ.get("SBY", "sby")

M = []  # (name, file, old, new, expectation, note)


MISC = ("guard", "equiv_guard", "miter", "equiv_miter")


def mut(name, file, old, new, exp="kill", note=""):
    M.append((name, file, old, new, exp, note))


# ------------------------------------------------------------------------------------------- rules of M-10 .. M-18 (mxu_dec)
D, X, W, A = "mxu_dec.sv", "mxu.sv", "mxu_wpack.sv", "mxu_array.sv"
mut("align_lane", D, "f_al_a  = (r_a[5:0] != 6'd0);", "f_al_a  = (r_a[2:0] != 6'd0);", note="A base: 8 B instead of 64 B")
mut("align_w_base", D, "f_al_w  = (r_w[5:0] != 6'd0);", "f_al_w  = 1'b0;")
mut("align_s1_a", D, "f_al_sa = a_s1[31] || (a_s1[5:0] != 6'd0);", "f_al_sa = a_s1[31] || (a_s1[2:0] != 6'd0);")
mut("align_s1_neg", D, "f_al_sw = w_s1[31] || (w_s1[5:0] != 6'd0);", "f_al_sw = (w_s1[5:0] != 6'd0);", note="negative pitch not a fault")
mut("align_cs", D, "f_al_cs = cs_en && (r_cs[5:0] != 6'd0);", "f_al_cs = cs_en && (r_cs[2:0] != 6'd0);")
mut("align_rs", D, "f_al_rs = rs_en && (r_rs[5:0] != 6'd0);", "f_al_rs = 1'b0;")
mut("n_rule_back", D, "f_al_cs = cs_en && (r_cs[5:0] != 6'd0);", "f_al_cs = cs_en && ((r_cs[5:0] != 6'd0) || ((vN[3:0]) != 4'd0));",
    note="the deleted 4 N rule, as it would act at FULL (N a multiple of 16): the S-1 case N = 40 faults")
mut("word_reserved", D, "(ins[38:0] != 39'd0) || ", "", note="reserved bits of the word")
mut("word_mode", D, "(i_mode != 3'd0) || ", "(i_mode > 3'd1) || ", note="BF16X3 accepted")
mut("word_accm3", D, "(i_accm == 2'd3) || ", "")
mut("word_rs_without_rescale", D, "(i_accm == 2'd2 ? !rs_en : rs_en)", "(i_accm == 2'd2 ? !rs_en : 1'b0)")
mut("word_rescale_no_rs", D, "(i_accm == 2'd2 ? !rs_en : rs_en)", "(i_accm == 2'd2 ? 1'b0 : rs_en)")
mut("prio_space_first", D, "    else if (f_word)   begin end\n    else if (f_sp_a)   begin bad_cause = FAULT_CAUSE_TD_SPACE; bad_addr = r_a; end",
    "    else if (f_sp_a)   begin bad_cause = FAULT_CAUSE_TD_SPACE; bad_addr = r_a; end\n    else if (f_word)   begin end",
    note="TD_SPACE of A before the word fields (the RTL's order before M-18)")
mut("prio_w_before_a", D, "    else if (f_sp_a)   begin bad_cause = FAULT_CAUSE_TD_SPACE; bad_addr = r_a; end\n    else if (f_sp_w)   begin bad_cause = FAULT_CAUSE_TD_SPACE; bad_addr = r_w; end",
    "    else if (f_sp_w)   begin bad_cause = FAULT_CAUSE_TD_SPACE; bad_addr = r_w; end\n    else if (f_sp_a)   begin bad_cause = FAULT_CAUSE_TD_SPACE; bad_addr = r_a; end")
mut("prio_range_before_align", D, "    else if (f_al_a)   bad_addr = r_a;",
    "    else if (f_r_acc)  begin bad_cause = FAULT_CAUSE_RANGE; bad_addr = r_acc | 64'h8000_0000_0000_0000; end\n    else if (f_al_a)   bad_addr = r_a;")
mut("prio_w_before_cs", D, "    else if (f_r_w)    begin bad_cause = FAULT_CAUSE_RANGE; bad_addr = r_w; end\n    else if (f_r_cs)   begin bad_cause = FAULT_CAUSE_RANGE; bad_addr = r_cs; end",
    "    else if (f_r_cs)   begin bad_cause = FAULT_CAUSE_RANGE; bad_addr = r_cs; end\n    else if (f_r_w)    begin bad_cause = FAULT_CAUSE_RANGE; bad_addr = r_w; end")
mut("addr_space_zero", D, "begin bad_cause = FAULT_CAUSE_TD_SPACE; bad_addr = r_a; end", "begin bad_cause = FAULT_CAUSE_TD_SPACE; end")
mut("addr_align_zero", D, "    else if (f_al_cs)  bad_addr = r_cs;", "    else if (f_al_cs)  bad_addr = 64'd0;")
mut("addr_acc_bit63", D, "bad_addr = r_acc | 64'h8000_0000_0000_0000;", "bad_addr = r_acc;")
mut("addr_32bit", D, "            fault_addr  <= bad_addr;", "            fault_addr  <= {32'd0, bad_addr[31:0]};", note="the first RTL's 32-bit FAULT_ADDR")
mut("m_zero_faults", D, "(vK == 16'd0) || (vK != vKw)", "(vK == 16'd0) || m_zero || (vK != vKw)", note="M = 0 illegal again (S-7)")
mut("m_trunc16", D, "vM   = m_en ? r_m : 64'(a_n1);", "vM   = m_en ? {48'd0, r_m[15:0]} : 64'(a_n1);", note="R[m_reg] truncated to a TD n1")
mut("m_from_td", D, "vM   = m_en ? r_m : 64'(a_n1);", "vM   = 64'(a_n1);", note="R[m_reg] ignored")
mut("range_acc_wrap", D, "(r_acc > 64'(ACC_ROWS)) || (vM > 64'(ACC_ROWS)) || ", "(vM > 64'(ACC_ROWS)) || ", exp="miter",
    note="since M-24 a miter mutant: the narrowed sum uses the low RW bits of R[acc_reg], so only a value with high bits set and small low bits (2^12, 2^64 - 2^12 ..) is missed; tb_mxu's wrap case has large low bits and does not see it (open item: add an aliasing case)")
mut("range_acc_m", D, "(r_acc > 64'(ACC_ROWS)) || (vM > 64'(ACC_ROWS)) || (acc_sum > (NTW+RW+1)'(ACC_ROWS))",
    "(r_acc > 64'(ACC_ROWS)) || (acc_sum > (NTW+RW+1)'(ACC_ROWS))", note="M > ACC_ROWS not compared in full: the narrowed product sees only the low RW bits of M")
mut("range_acc_nt", D, "(NTW+RW+1)'(vNT * vM[RW-1:0]);", "(NTW+RW+1)'(vM[RW-1:0]);", note="N tiles not counted")
mut("range_a_zero", D, "f_r_a   = !m_zero && (a_ext > 96'(SPM_BYTES));", "f_r_a   = 1'b0;")
mut("range_a_m0", D, "f_r_a   = !m_zero && (a_ext > 96'(SPM_BYTES));", "f_r_a   = (a_ext > 96'(SPM_BYTES));", exp="miter",
    note="A extent of an M = 0 instruction (M - 1 wraps; 2^64 rows before M-24, 2047 rows now): since M-24 a miter mutant (and formal_dec's z_range_a), tb_mxu's M = 0 case has a pitch too small for 2047 rows to leave the SPM (open item)")
mut("range_w", D, "f_r_w   = (w_ext > 96'(SPM_BYTES));", "f_r_w   = 1'b0;")
mut("range_cs_old_extent", D, "cs_ext = 96'(r_cs) + 96'(cs_prod) + 96'(vN) * 96'd4;",
    "cs_ext = 96'(r_cs) + 96'(64'd4) * 96'(vKT) * 96'(vN);", note="the pre-S-10 extent 4 KT N with KT = MXU passes (false RANGE, S-10 case)")
mut("range_cs_rows", D, "cs_ext = 96'(r_cs) + 96'(cs_prod) + 96'(vN) * 96'd4;",
    "cs_ext = 96'(r_cs) + 96'(vN) * 96'd4;", note="only the first scale row")
mut("range_cs_bytes_read", D, "cs_ext = 96'(r_cs) + 96'(cs_prod) + 96'(vN) * 96'd4;",
    "cs_ext = 96'(r_cs) + 96'(vKTF) * 96'(vCSP);", exp="equiv",
    note="whole padded rows instead of the exact bytes: with a 64 B aligned base and an SPM that is a multiple of 64 B both end in the same 64 B granule, so the verdict is the same")
mut("range_rs", D, "f_r_rs  = rs_en && !m_zero && (rs_ext > 96'(SPM_BYTES));", "f_r_rs  = 1'b0;")
mut("csp_4n", D, "vCSP = {csp_q, 6'd0};", "vCSP = 19'(vN) * 19'd4;", note="scale row pitch 4 N (S-1)")
mut("csp_32", D, "vCSP = {csp_q, 6'd0};", "vCSP = (19'(vN) * 19'd4 + 19'd31) & ~19'd31;")
mut("fold_tiles", D, "vKTF = 10'((17'(vK) + (bf16 ? 17'd127 : 17'd255)) >> (bf16 ? 7 : 8));", "vKTF = 10'((17'(vK) + (bf16 ? 17'd63 : 17'd127)) >> (bf16 ? 6 : 7));", note="wrong fold tile in the cs extent")

# ----------------------------------------------------------------------------------------------- M = 0 and completion
mut("zero_no_inc", D, "assign z_inc    = (dst == D_PUSH) && m_zero && inf_empty;", "assign z_inc    = 1'b0;", note="M = 0 never completes")
mut("zero_no_wait", D, "D_PUSH: if (m_zero ? inf_empty : ctx_ready) dst <= D_IDLE;", "D_PUSH: if (m_zero ? 1'b1 : ctx_ready) dst <= D_IDLE;",
    note="never waits for the instructions in flight: z_inc comes only if the FIFO happens to be empty, else the instruction vanishes")
mut("zero_early", D, "assign z_inc    = (dst == D_PUSH) && m_zero && inf_empty;", "assign z_inc    = (dst == D_PUSH) && m_zero;",
    note="completes before the earlier instructions (out of order)")
mut("zero_runs", D, "assign ctx_push = (dst == D_PUSH) && !m_zero && ctx_ready;", "assign ctx_push = (dst == D_PUSH) && ctx_ready;",
    note="an M = 0 instruction also enters the load FSM")
mut("zero_sem_mux", X, "assign sem_inc_w     = z_inc ? ctx_sync : inf_head;", "assign sem_inc_w     = inf_head;")

# ----------------------------------------------------------------------------------------------- column scales (mxu)
mut("cs_row_per_pass", X, "localparam int CS_SHIFT = 6 - $clog2(DIM),", "localparam int CS_SHIFT = 0,", note="one scale row per MXU pass (M-13)")
mut("cs_row_shift", X, "localparam int CS_SHIFT = 6 - $clog2(DIM),", "localparam int CS_SHIFT = 7 - $clog2(DIM),")
mut("cs_pitch_in", X, "64'(c_csp) * (64'(l_t) >> CS_SHIFT)", "64'(c_N) * 64'd4 * (64'(l_t) >> CS_SHIFT)")
mut("cs_tile_col", X, "64'd4 * 64'(DIM) * 64'(l_j));", "64'(DIM) * 64'(l_j));", note="column tile offset in elements, not bytes")

# ----------------------------------------------------------------------------------------------- data path (as in the first round)
mut("ncols_pack", W, "c < int'(ncols)) win[(h*DIM + c)*32 + 8*q +: 8] = byt[4*h + q][c];", "1'b1) win[(h*DIM + c)*32 + 8*q +: 8] = byt[4*h + q][c];",
    exp="equiv", note="the extra column is masked at the ACC")
mut("lane_valid_k", X, "wl_lane_v[l] = kbeat < 64'(c_K);\n        wl_lane_addr[l] = 22'(64'(c_w_base) + kbeat * 64'(c_w_pitch) + 64'(l_j) * 64'(DIM));",
    "wl_lane_v[l] = 1'b1;\n        wl_lane_addr[l] = 22'(64'(c_w_base) + kbeat * 64'(c_w_pitch) + 64'(l_j) * 64'(DIM));", exp="kill",
    note="lanes past K requested: reads garbage rows, whose k-row words are not zeroed by the packer")
mut("trans_k_tail", W, "(4*w + q) < int'(nbytes)) win", "1'b1) win", note="K tail of a trans lane not zeroed")
mut("a_mask_tail", X, "a_masked[8*b +: 8] = (b < int'(ar_kb)) ? a_rsp_data[8*b +: 8] : 8'd0;", "a_masked[8*b +: 8] = a_rsp_data[8*b +: 8];", note="A bytes past K")
mut("a_pitch", X, "s_addr <= s_addr + s_a_pitch;", "s_addr <= s_addr + 22'(KB);")
mut("w_pitch_trans", X, "(64'(l_idx) * 64'd8 + 64'(l)) * 64'(c_w_pitch)", "(64'(l_idx) * 64'd8 + 64'(l)) * 64'(KB)")
mut("rs_lane", X, "assign rs_addr = s_rs_base + 22'(s_lane_cnt) * 22'(LB);", "assign rs_addr = s_rs_base;")
mut("acc_row_nt", X, "s_accrow <= tk_acc_base + 11'(64'(tk_j) * 64'(tk_M));", "s_accrow <= tk_acc_base + 11'(tk_j);")
mut("overwrite_t0", X, "assign a_ovw   = (s_accm == 2'd0) && (s_t == 16'd0);", "assign a_ovw   = (s_accm == 2'd0);")
mut("rescale_t0", X, "assign rs_need = (s_accm == 2'd2) && (s_t == 16'd0);", "assign rs_need = (s_accm == 2'd2);")
mut("gap_cnt", X, "gap_cnt <= (s_Mrows >= 16'(ACC_LAT)) ? 8'd0 : 8'(ACC_LAT) - 8'(s_Mrows);", "gap_cnt <= 8'd0;", exp="kill_full", note="no gap at all: killed by the ACC_LAT = 24 run and by the R-1 chained cases")
# M-22 (review R-1): the gap at an instruction boundary. gap_x_off is the rule before the change; proofs: bmc_d (M <= 3, free acc_base)
mut("gap_x_off", X, "if (s_last && s_Mrows >= 16'd2) gap_cnt", "if (1'b0 && s_Mrows >= 16'd2) gap_cnt", exp="formal_x", note="R-1: no boundary gap (the old rule)")
mut("gap_x_short", X, "localparam int GAP_X = (ACC_LAT > 2) ? ACC_LAT - 2 : 0;", "localparam int GAP_X = (ACC_LAT > 3) ? ACC_LAT - 3 : 0;", exp="formal_x",
    note="R-1: the boundary gap one cycle short (ACC_LAT - 1 cycles between the worst pair of rows)")
mut("gap_x_every_pass", X, "if (s_last && s_Mrows >= 16'd2) gap_cnt", "if (1'b1 && s_Mrows >= 16'd2) gap_cnt", exp="equiv",
    note="the boundary gap after every pass: safe and only slower (the pass period at M = 1 is longer than ACC_LAT - 2 anyway)")
# M-23 (review R-2): an empty W beat is granted by the MXU. tb_mxu runs two instances with an SPM whose ready needs a valid lane
mut("w_gnt_wait", X, "assign w_gnt = w_want && (w_req_ready || wl_lane_v == 8'd0);", "assign w_gnt = w_want && w_req_ready;", exp="kill",
    note="R-2: the MXU waits for the grant of an empty beat (hangs on the ready-needs-valid SPM of tb_mxu)")
mut("sem_head", X, "assign sem_inc_idx   = sem_inc_w[9:4];", "assign sem_inc_idx   = sem_inc_w[3:0] + 6'd0;")

# ----------------------------------------------------------------------------------------------- control mutants of the proofs
mut("no_free_gate", X, "(free_g[int'(l_buf) * NG + int'(l_g)] || l_idx != '0)", "1'b1", exp="formal")
mut("no_gap_f", X, "gap_cnt <= (s_Mrows >= 16'(ACC_LAT)) ? 8'd0 : 8'(ACC_LAT) - 8'(s_Mrows);", "gap_cnt <= 8'd0;", exp="formal")
mut("swap_early", X, "if (l_w_done && l_cs_done) lst <= L_PUSH;", "if (l_cs_done && l_g == 2'd2) lst <= L_PUSH;", exp="formal",
    note="the swap token is pushed with the last two groups of the tile still unloaded (P2)")
mut("token_at_request", X, "if (l_w_done && l_cs_done) lst <= L_PUSH;", "if (l_w_all && l_cs_done) lst <= L_PUSH;", exp="equiv",
    note="token when the last beat is requested, not when it has landed: the first A row of the tile reaches the array SPM_LAT after the token, so after the beat (same fixed latency on both ports); not visible to the proofs or the tb")
mut("last_beat_early", X, "assign w_last_beat = (l_g == 2'(NG - 1)) && (l_idx == IDXW'(BPG - 1));", "assign w_last_beat = (l_g == 2'(NG - 2)) && (l_idx == IDXW'(BPG - 1));", exp="formal")
mut("cs_pop_first", X, "assign cs_free_ev    = y_valid && x_lastp && x_ucs;", "assign cs_free_ev    = y_valid && x_first && x_ucs;", exp="formal")
mut("rs_pop_end", X, "((rs_idx == ($clog2(GRP+1))'(GRP - 1)) || x_lastp)", "(rs_idx == ($clog2(GRP+1))'(GRP - 1))", exp="formal")
mut("cs_cnt_leak", X, "- (cs_free_ev ? 1 : 0);", "- ((cs_free_ev && cs_rd != '0) ? 1 : 0);", exp="formal")
mut("token_live", X, "assign a_lastp = (s_m == s_Mrows - 16'd1);", "assign a_lastp = (s_m == tk_M - 16'd1);", exp="formal_blind",
    note="the proofs cannot see it (M <= 2); the system test does")


# ------------------------------------------------------------------------------------------- M = 0 in the decoder: tb_mxu and mxu_dec.sby
mut("z_no_wait", D, "assign z_inc    = (dst == D_PUSH) && m_zero && inf_empty;", "assign z_inc    = (dst == D_PUSH) && m_zero;", exp="formal_dec",
    note="M = 0 completes without waiting for the FIFO to empty")
mut("z_m16", D, "assign m_zero = (vM == 64'd0);", "assign m_zero = (vM[15:0] == 16'd0);", exp="equiv_dec",
    note="equivalent: the zero test on 16 bits of M; every M >= 2^16 is already a RANGE fault of the ACC (full 64-bit M > ACC_ROWS), so none reaches D_PUSH")
mut("z_ctx_too", D, "assign ctx_push = (dst == D_PUSH) && !m_zero && ctx_ready;", "assign ctx_push = (dst == D_PUSH) && ctx_ready;", exp="formal_dec",
    note="M = 0 also pushes a context")
mut("z_leave_early", D, "D_PUSH: if (m_zero ? inf_empty : ctx_ready) dst <= D_IDLE;", "D_PUSH: if (ctx_ready) dst <= D_IDLE;", exp="formal_dec",
    note="the decoder takes the next instruction while an M = 0 waits")
mut("z_mreg_ignored", D, "vM   = m_en ? r_m : 64'(a_n1);", "vM   = 64'(a_n1);", exp="formal_dec", note="S-7: R[m_reg] not used")
mut("z_unchecked", D, "if (bad_any) begin\n            fault_valid", "if (bad_any && !m_zero) begin\n            fault_valid", exp="formal_dec",
    note="an M = 0 instruction skips the legality checks")
mut("z_range_a", D, "f_r_a   = !m_zero && (a_ext > 96'(SPM_BYTES));", "f_r_a   = (a_ext > 96'(SPM_BYTES));", exp="formal_dec",
    note="A with no bytes (M = 0) can fault RANGE")

# ------------------------------------------------------------------------------------------- review part I (2026-11-12)
# I-9: an acc_done with nothing in flight. tb_mxu pulses one right after reset; the proofs assume it never happens (f_pend), so only the tb sees it
mut("spur_pop", X, "assign inf_pop = acc_done && (inf_cnt != 3'd0);", "assign inf_pop = acc_done;", exp="kill",
    note="I-9: the unguarded pop wraps inf_cnt to 7 and the MXU never takes another instruction")
# I-7: the registers moved out of the async-reset processes must still load only at their enable
mut("s_cfg_always", X, "    if (tok_take) begin\n      s_t <= tk_t;", "    if (1'b1) begin\n      s_t <= tk_t;", exp="kill",
    note="I-7: the per-pass configuration follows the token register every cycle instead of loading at the take")
mut("inf_mem_always", X, "    if (inf_push) inf_mem[inf_wr] <= inf_in;", "    inf_mem[inf_wr] <= inf_in;", exp="kill",
    note="I-7: the in-flight FIFO entry is overwritten every cycle")
# I-10: the guards (guard_check.py)
mut("guard_dim_off", X, "if (DIM != 8 && DIM != 16 && DIM != 32 && DIM != 64) begin : g_bad_dim", "if (1'b0) begin : g_bad_dim", exp="equiv_guard",
    note="duplicated: mxu_array's own geometry check stops every DIM the guard stops, so the three tools still fail (the guard only moves the error to the top)")
mut("guard_spm_lat_off", X, "if (SPM_LAT < 1) begin : g_bad_spm_lat", "if (1'b0) begin : g_bad_spm_lat", exp="equiv_guard",
    note="duplicated: SPM_LAT = 0 gives zero-size tag arrays, a hard elaboration error of every tool")
mut("guard_cs_depth_off", X, "if (CS_DEPTH < 2) begin : g_bad_cs_depth", "if (CS_DEPTH < 1) begin : g_bad_cs_depth", exp="guard",
    note="CS_DEPTH = 1 (a [-1:0] vector) must stop")
mut("guard_acc_lat_max", X, "if (ACC_LAT < 1 || ACC_LAT > 255) begin : g_bad_acc_lat", "if (ACC_LAT < 1 || ACC_LAT > 256) begin : g_bad_acc_lat", exp="guard",
    note="ACC_LAT = 256 truncates the 8-bit gap counter")
mut("guard_acc_lat_min", X, "if (ACC_LAT < 1 || ACC_LAT > 255) begin : g_bad_acc_lat", "if (ACC_LAT < 0 || ACC_LAT > 255) begin : g_bad_acc_lat", exp="guard")
mut("guard_acc_lat_max_off", X, "if (ACC_LAT < 1 || ACC_LAT > 255) begin : g_bad_acc_lat", "if (ACC_LAT < 1) begin : g_bad_acc_lat", exp="guard")
mut("guard_acc_rows_max", D, "if (ACC_ROWS < 2 || ACC_ROWS > 2048) begin : g_bad_acc_rows", "if (ACC_ROWS < 2 || ACC_ROWS > 4096) begin : g_bad_acc_rows", exp="guard",
    note="c_acc_base is 11 bits")
mut("guard_acc_rows_min", D, "if (ACC_ROWS < 2 || ACC_ROWS > 2048) begin : g_bad_acc_rows", "if (ACC_ROWS < 1 || ACC_ROWS > 2048) begin : g_bad_acc_rows", exp="equiv_guard",
    note="duplicated: ACC_ROWS = 1 gives MW = 0, a zero-width [MW-1:0] that every tool rejects anyway (ACC_ROWS = 0 is still stopped by the mutant)")
# I-3: the narrowed widths of mxu_dec, against the decoder of the reviewed commit (tb_dec_miter)
mut("dec_a_m1_narrow", D, "a_m1   = MW'(vM - 64'd1);", "a_m1   = (MW-1)'(vM - 64'd1);", exp="miter", note="M - 1 on one bit too few: M = 1025 .. 2048 passes the A extent")
mut("dec_a_s1_bit30", D, "a_prod = a_m1 * a_s1[30:0];", "a_prod = a_m1 * a_s1[29:0];", exp="miter", note="the reviewer's candidate 's1 without bit 30': NOT equivalent (s1 = 2^30, M >= 2 is RANGE)")
mut("dec_a_m1_wide", D, "a_m1   = MW'(vM - 64'd1);", "a_m1   = RW'(vM - 64'd1);", exp="equiv_miter", note="one bit wider (RW = MW + 1 bits, truncated to MW by a_prod's operand): M <= ACC_ROWS never uses it")
mut("dec_w_rm1", D, "w_rm1  = w_rows - 16'd1;", "w_rm1  = w_rows;", exp="miter", note="W: rows, not rows - 1")
mut("dec_w_swap", D, "w_rows = i_trans ? vN : vK;", "w_rows = i_trans ? vK : vN;", exp="miter", note="W: K and N not swapped by trans_w")
mut("dec_w_bytes", D, "wk_b   = bf16 ? {w_elem, 1'b0} : {1'b0, w_elem};", "wk_b   = {1'b0, w_elem};", exp="miter", note="W: BF16 bytes counted as elements")
mut("dec_a_bytes", D, "ka_b = bf16 ? {vK, 1'b0} : {1'b0, vK};", "ka_b = {1'b0, vK};", exp="miter", note="A: BF16 bytes counted as elements")
mut("dec_cs_rm1", D, "cs_rm1  = vKTF - 10'd1;", "cs_rm1  = vKTF;", exp="miter", note="cs: rows, not rows - 1")
mut("dec_cs_narrow", D, "logic [9:0]  vKTF; ", "logic [8:0]  vKTF; ", exp="miter", note="KTF on 9 bits: BF16 K near 65535 gives 513 rows")
mut("dec_kt_shift", D, "(bf16 ? LD + 1 : LD + 2));   // K tiles", "(bf16 ? LD + 2 : LD + 2));   // K tiles", exp="miter", note="BF16 K tile as FP8 (c_KT)")
mut("dec_nt_floor", D, "vNT  = NTW'((17'(vN) + 17'(DIM - 1)) >> LD);", "vNT  = NTW'(17'(vN) >> LD);", exp="miter", note="N tiles rounded down")
mut("dec_csp_round", D, "csp_q = 13'((19'(vN) * 19'd4 + 19'd63) >> 6);", "csp_q = 13'((19'(vN) * 19'd4 + 19'd59) >> 6);", exp="miter",
    note="CSP rounding 63 -> 59: wrong for 4 N = 64 k + 4, i.e. N = 1 mod 16 (62 and 60 are equivalent: 4 N is a multiple of 4)")
mut("dec_csp_round62", D, "csp_q = 13'((19'(vN) * 19'd4 + 19'd63) >> 6);", "csp_q = 13'((19'(vN) * 19'd4 + 19'd62) >> 6);", exp="equiv_miter",
    note="rounding 63 -> 62 is equivalent: 4 N is a multiple of 4, so 4 N + 63 is never a multiple of 64")
mut("dec_rs_ext", D, "rs_ext = 96'(r_rs) + 96'({vM[RW-1:0], 2'd0});", "rs_ext = 96'(r_rs) + 96'({2'd0, vM[RW-1:0]});", exp="miter", note="rowscale extent M, not 4 M")
mut("dec_acc_sum_trunc", D, "(NTW+RW+1)'(vNT * vM[RW-1:0]);", "(NTW+RW+1)'(RW'(vNT * vM[RW-1:0]));", exp="miter", note="NT M cut to RW bits")
mut("dec_acc_sum_narrow", D, "logic [NTW+RW:0] acc_sum;", "logic [NTW+RW-1:0] acc_sum;", exp="equiv_miter",
    note="the spare top bit of the sum is never used: NT <= 8192 and M <= 2048 give at most 8192 x 2048 + 2048 < 2^26 = 2^(NTW+RW)")
mut("dec_csp_wide", D, "logic [12:0] csp_q;", "logic [13:0] csp_q;", exp="equiv_miter", note="one bit wider than the 4096 maximum of (4 N + 63) / 64")
mut("dec_ktf_wide", D, "logic [9:0]  vKTF; ", "logic [10:0] vKTF; ", exp="equiv_miter", note="vKTF one bit wider than its 513 maximum (cs_rm1 stays 10 bits)")


def build(m):
    name, file, old, new, exp, note = m
    d = os.path.join(BUILD, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    core = os.path.join(ROOT, "rtl", "core")
    for f in os.listdir(core):
        shutil.copy(os.path.join(core, f), d)
    path = os.path.join(d, file)
    txt = open(path).read()
    n = txt.count(old)
    if n != 1:
        raise SystemExit("mutant %s: the text to change occurs %d times in %s (must be 1)" % (name, n, file))
    open(path, "w").write(txt.replace(old, new))
    return d


def run_tb(m, quick):
    d = build(m)
    vec = os.path.join(ROOT, "build", "mxu")
    exe = os.path.join(d, "tb.vvp")
    srcs = [os.path.join(ROOT, "tb/unit/tb_mxu.sv"), os.path.join(ROOT, "isa/gen/isa_pkg.sv")] + \
        sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".sv")) + \
        sorted(os.path.join(ROOT, "rtl/arith", f) for f in os.listdir(os.path.join(ROOT, "rtl/arith")) if f.endswith(".sv"))
    cmd = ["iverilog", "-g2012"] + (["-DQUICK"] if quick else []) + ["-o", exe] + srcs
    c = subprocess.run(cmd, cwd=vec, capture_output=True, text=True)
    if c.returncode != 0:
        return "COMPILE-ERROR", c.stderr[-400:]
    try:
        r = subprocess.run(["vvp", "-n", exe], cwd=vec, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return "fails", "HANG: no PASS or FAIL within 1800 s (a legal run takes 3 to 40 minutes on this machine, a mutant that stalls the testbench never ends)"
    out = r.stdout
    if re.search(r"^PASS mxu", out, re.M):
        return "passes", ""
    first = [l for l in out.splitlines() if l.strip() and not l.startswith("FAIL") and "$finish" not in l and "ACC rows in" not in l][:1]
    return "fails", first[0] if first else out[-200:]


def run_formal(m, task="bmc_a"):
    d = build(m)
    sby = open(os.path.join(ROOT, "tb/formal/mxu.sby")).read()
    sby = sby.replace("../../rtl/core/", d + "/")
    sby = sby.replace("mxu_stubs.sv", os.path.join(ROOT, "tb/formal/mxu_stubs.sv")).replace("mxu_dec_stub.sv", os.path.join(ROOT, "tb/formal/mxu_dec_stub.sv"))
    sby = re.sub(r"\[files\]\n", "[files]\n", sby)
    # one task: bmc_a (M <= 2, 2 K tiles, 1 N tile) at the full depth, or bmc_d (M <= 3, 1 K tile, 1 N tile) for formal_x
    p = os.path.join(d, "mxu.sby")
    open(p, "w").write(sby)
    r = subprocess.run([SBY, "-f", "--prefix", os.path.join(d, "run"), p, task], cwd=d, capture_output=True, text=True, timeout=3600)
    out = r.stdout + r.stderr
    if "DONE (FAIL" in out:
        line = [l for l in out.splitlines() if "Assert failed" in l]
        return "fails", line[0].strip() if line else ""
    if "DONE (PASS" in out:
        return "passes", ""
    return "ERROR", out[-400:]


def run_formal_dec(m):
    d = build(m)
    sby = open(os.path.join(ROOT, "tb/formal/mxu_dec.sby")).read()
    sby = sby.replace("../../rtl/core/", d + "/").replace("../../isa/gen/", os.path.join(ROOT, "isa/gen") + "/")
    p = os.path.join(d, "mxu_dec.sby")
    open(p, "w").write(sby)
    r = subprocess.run([SBY, "-f", "--prefix", os.path.join(d, "run"), p, "bmc"], cwd=d, capture_output=True, text=True, timeout=3600)
    out = r.stdout + r.stderr
    if "DONE (FAIL" in out:
        line = [l for l in out.splitlines() if "Assert failed" in l]
        return "fails", line[0].strip() if line else ""
    if "DONE (PASS" in out:
        return "passes", ""
    return "ERROR", out[-400:]


def run_guard(m):
    d = build(m)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts/mxu_mutants/guard_check.py"), d], capture_output=True, text=True)
    first = [l for l in r.stdout.splitlines() if "UNEXPECTED" in l][:1]
    return ("passes" if r.returncode == 0 else "fails"), (first[0] if first else "")


def run_miter(m, n=100000):
    """The mutated decoder (module renamed mxu_dec_fix) against the decoder of the reviewed commit, DIM 8 and 64, one seed each."""
    d = build(m)
    old = subprocess.run(["git", "show", os.environ.get("OLD_REF", "0255c28") + ":rtl/core/mxu_dec.sv"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    open(os.path.join(d, "mxu_dec_old.sv"), "w").write(old)
    txt = open(os.path.join(d, "mxu_dec.sv")).read()
    assert txt.count("module mxu_dec #(") == 1
    open(os.path.join(d, "mxu_dec_fix.sv"), "w").write(txt.replace("module mxu_dec #(", "module mxu_dec_fix #(", 1))
    for dim in (8, 64):
        exe = os.path.join(d, "miter_%d.vvp" % dim)
        c = subprocess.run(["iverilog", "-g2012", "-P", "tb_dec_miter.DIM=%d" % dim, "-o", exe, os.path.join(ROOT, "isa/gen/isa_pkg.sv"),
                            os.path.join(d, "mxu_dec_old.sv"), os.path.join(d, "mxu_dec_fix.sv"), os.path.join(ROOT, "scripts/mxu_mutants/tb_dec_miter_x.sv")],
                           capture_output=True, text=True)
        if c.returncode != 0:
            return "COMPILE-ERROR", c.stderr[-300:]
        r = subprocess.run(["vvp", "-n", exe, "+N=%d" % n, "+SEED=1"], capture_output=True, text=True, timeout=1800)
        if "PASS tb_dec_miter" not in r.stdout:
            first = [l for l in r.stdout.splitlines() if "mismatch" in l.lower() or "MISMATCH" in l][:1]
            return "fails", "DIM %d: %s" % (dim, first[0].strip() if first else "no PASS")
    return "passes", ""


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("tb", "formal", "list", "misc"):
        print(__doc__)
        return 2
    mode, rest = args[0], args[1:]
    quick = "--quick" in rest
    names = [a for a in rest if not a.startswith("--")]
    sel = [m for m in M if (not names or m[0] in names)]
    if mode == "list":
        for m in M:
            print("%-26s %-8s %s" % (m[0], m[4], m[5]))
        return 0
    bad = 0
    for m in sel:
        name, _, _, _, exp, note = m
        if (exp in MISC) != (mode == "misc"):
            continue                                # the part I mutants run in `misc` mode only
        if mode == "misc":
            res, info = run_guard(m) if exp in ("guard", "equiv_guard") else run_miter(m)
            ok = (res == "fails") if exp in ("guard", "miter") else (res == "passes")
            verdict = ("KILLED" if res == "fails" else "SURVIVED" if res == "passes" else res) + ("" if ok else "   <-- UNEXPECTED")
        elif mode == "tb" and exp in ("formal", "formal_dec", "equiv_dec"):
            res, info = run_tb(m, quick)           # a formal mutant is also run against the system test
            ok = True                              # reported, not judged
            verdict = "tb " + res
        elif mode == "tb":
            res, info = run_tb(m, quick)
            ok = (res == "fails") if exp in ("kill", "formal_blind", "formal_x") else (res == "passes") if exp == "equiv" else (res == "fails" or quick)
            verdict = ("KILLED" if res == "fails" else "SURVIVED" if res == "passes" else res) + ("" if ok else "   <-- UNEXPECTED")
        else:
            if exp not in ("formal", "formal_x", "formal_blind", "formal_dec", "equiv_dec"):
                continue
            res, info = run_formal_dec(m) if exp in ("formal_dec", "equiv_dec") else run_formal(m, "bmc_d" if exp == "formal_x" else "bmc_a")
            ok = (res == "fails") if exp in ("formal", "formal_x", "formal_dec") else (res == "passes")
            verdict = ("CAUGHT" if res == "fails" else "SURVIVED" if res == "passes" else res) + ("" if ok else "   <-- UNEXPECTED")
        bad += 0 if ok else 1
        print("%-26s %-8s %s   %s" % (name, exp, verdict, info if res in ("fails", "COMPILE-ERROR", "ERROR") else ""), flush=True)
    print("mutants run: %d, unexpected: %d" % (len(sel), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
