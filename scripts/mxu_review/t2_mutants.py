#!/usr/bin/env python3
"""Review part TR2 (independent review of the part TR test changes, commit 929d820, mxu_review.md part TR2): new one-change mutants of
rtl/core/mxu*.sv aimed at what TR added to the tests: fault rows just past a boundary (and the operands TR did not touch: rowscale,
cs at DIM 8 / 16, BF16 non-trans W, M from m_reg), the A / W / ACC carries, CS_DEPTH 3 / SPM_LAT 4-5 (every FIFO and tag pipeline whose
depth is not a power of two or exceeds 4), DIM 32 scale rows, waits across 2^31 / 2^32 (strict / non-strict, equal values, wval 0),
the total cycle count (slowdowns of 1-2 % against CHKC's +2 %), and the drained legal fault cases.

Written after reading the diff eb2afdf..929d820 and the RTL, before any run; `pred` is the prediction made then (kill / survive /
equiv), `why` the reasoning. No mutant of tq_mutants.py, tr_mutants.py or scripts/mxu_mutants is repeated (the TR fixes added the
trf_* ones to mxu_mutants.py). Same machinery as tr_mutants.py (imported): a copy of rtl/ in build/mxu_review_t2/mut/<name>/rtl with
exactly one textual change, judged by the real recipe

    make unit RTL_DIR=<copy>/rtl UNIT_TB="tb/unit/tb_mxu_array.sv tb/unit/tb_mxu.sv" BUILD=<worker copy>

Usage (a worker peaks at about 2.3 GB; one make unit takes about 9 min):
  python3 scripts/mxu_review/t2_mutants.py check | list
  scripts/memrun t2_mutants python3 scripts/mxu_review/t2_mutants.py run [--jobs N] [--no-baseline] [NAME ...]
Results: build/mxu_review_t2/results.tsv (name, area, pred, result, seconds, verdict, checks that fired per instance, first line).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tr_mutants as T  # noqa: E402

T.WORK = os.path.join(T.ROOT, "build", "mxu_review_t2")
D, X, A = T.D, T.X, T.A
M = []


def mut(name, area, f, old, new, pred, why):
    M.append(dict(name=name, area=area, file=f, old=old, new=new, pred=pred, why=why, tbs=T.TBS))


# ------------------------------------------------------------------ legality: rows just past a boundary, other operands (mxu_dec)
mut("t2_w_nt_bf16_elem", "legal", D, "wk_b   = bf16 ? {w_elem, 1'b0} : {1'b0, w_elem};", "wk_b   = (bf16 && i_trans) ? {w_elem, 1'b0} : {1'b0, w_elem};",
    "survive", "the last row of a BF16 NON-trans W counted in elements (N, not 2 N bytes): the only BF16 W RANGE cases (TR-1) are trans")
mut("t2_w_nt_norow", "legal", D, "w_rows = i_trans ? vN : vK;", "w_rows = i_trans ? vN : vK - 16'd1;", "survive",
    "a non-trans W extent one k-row short: no fault case has a non-trans W past the end (only the legal K = 64 x N = 64 control)")
mut("t2_w_range_tol31", "legal", D, "f_r_w   = (w_ext > 96'(SPM_BYTES));", "f_r_w   = (w_ext > 96'(SPM_BYTES) + 96'd31);", "survive",
    "a W overrun of 1 .. 31 B is not a fault: the TR-1 W cases overrun by exactly 32 B (FP8 96 B rows, BF16 K = 48), A has 1 B and 16 B")
mut("t2_cs_norows16", "legal", D, "cs_ext = 96'(r_cs) + 96'(cs_prod) + 96'(vN) * 96'd4;",
    "cs_ext = 96'(r_cs) + ((DIM == 16) ? 96'd0 : 96'(cs_prod)) + 96'(vN) * 96'd4;", "kill",
    "control at DIM 16 only: the earlier scale rows forgotten; tf16 has the FP8 K = 257 and the BF16 K = 129 cases (64 B past)")
mut("t2_ktf_fp8", "legal", D, "(bf16 ? 17'd127 : 17'd255)", "(bf16 ? 17'd127 : 17'd254)", "kill",
    "control: FP8 scale rows one short at K = 256 n + 1 (the K = 257 case from SPM - 64)")
mut("t2_a_m_td", "legal", D, "a_m1   = MW'(vM - 64'd1);", "a_m1   = MW'(64'(a_n1) - 64'd1);", "survive",
    "the A extent uses the TD's n1 even when M comes from m_reg (M-14): no A RANGE case has M from the register, and the decoys of tb_mxu "
    "(M + 7, 5) stay far from the SPM end")
mut("t2_rs_m_td", "legal", D, "rs_ext = 96'(r_rs) + 96'({vM[RW-1:0], 2'd0});", "rs_ext = 96'(r_rs) + 96'({a_n1[RW-1:0], 2'd0});", "kill",
    "control: rowscale extent from the TD's n1: the 17-value rowscale case takes M from m_reg (TD n1 = 2)")
# ------------------------------------------------------------------ carries (mxu.sv)
mut("t2_a_start_carry16", "carry", X, "s_addr <= tk_a_base + 22'(64'(tk_t) * 64'(KB));",
    "s_addr <= {tk_a_base[21:16], 16'(tk_a_base[15:0] + 16'(64'(tk_t) * 64'(KB)))};", "survive",
    "the pass start a_base + t KB drops the carry into bit 16: CARRY_A starts at 0xFF40 and t KB <= 96 (DIM 8) / 64 (DIM 16), so the "
    "start never carries; the carries of CARRY_A are the row adder's (s_addr + s1), which is a different adder in this RTL")
mut("t2_a_lane_carry16", "carry", X, "assign a_lane_addr[22*l +: 22] = s_addr + 22'(l * LB);",
    "assign a_lane_addr[22*l +: 22] = {s_addr[21:16], 16'(s_addr[15:0] + 16'(l * LB))};", "survive",
    "equivalent at DIM 8 / 16 by construction (s_addr is KB-aligned, the lane offsets stay in bits 0 .. 5); at DIM 32 a carry needs an A "
    "row at 0x..FFC0 with lanes 2 / 3, which no DIM 32 case has")
mut("t2_w_tr_carry17", "carry", X,
    "wl_lane_addr[l] = 22'(64'(t_w_base) + (64'(l_idx) * 64'd8 + 64'(l)) * 64'(c_w_pitch) + 64'(l_g) * 64'(LB));",
    "wl_lane_addr[l] = {t_w_base[21:17], 17'(64'(t_w_base[16:0]) + (64'(l_idx) * 64'd8 + 64'(l)) * 64'(c_w_pitch) + 64'(l_g) * 64'(LB))};", "kill",
    "trans W lane address drops the carry into bit 17: the dense images fill 256 KiB linearly, so some trans W straddles 0x20000 "
    "(guess: not designed by any test)")
mut("t2_w_nt_carry16", "carry", X, "wl_lane_addr[l] = 22'(64'(c_w_base) + kbeat * 64'(c_w_pitch) + 64'(l_j) * 64'(DIM));",
    "wl_lane_addr[l] = {c_w_base[21:16], 16'(64'(c_w_base[15:0]) + kbeat * 64'(c_w_pitch) + 64'(l_j) * 64'(DIM))};", "survive",
    "non-trans FP8 W lane address drops the carry into bit 16: few, small (2 KiB) non-trans FP8 operands, no designed crossing")
mut("t2_accrow_carry9", "carry", X, "s_accrow <= s_accrow + 11'd1;", "s_accrow <= {s_accrow[10:9], 9'(s_accrow[8:0] + 9'd1)};", "kill",
    "the ACC row counter drops the carry into bit 9: the sparse pass 1020 .. 1027 goes 1023 -> 512")
mut("t2_accbase_carry9", "carry", X, "s_accrow <= tk_acc_base + 11'(64'(tk_j) * 64'(tk_M));",
    "s_accrow <= {tk_acc_base[10:9], 9'(tk_acc_base[8:0] + 9'(64'(tk_j) * 64'(tk_M)))};", "survive",
    "the pass start acc_base + j M drops the carry into bit 9: the TR-2 case 1272 + 4 j carries into bit 8 only (0x4F8 + 8 = 0x500); "
    "no pass start crosses a multiple of 512")
# ------------------------------------------------------------------ FIFOs / tag pipelines: depths that are not a power of two or exceed 4
mut("t2_cs_wr_pow2", "fifo", X, "cs_wr <= (cs_wr == $bits(cs_wr)'(CS_DEPTH - 1)) ? '0 : cs_wr + 1'b1;", "cs_wr <= cs_wr + 1'b1;", "kill",
    "the cs write pointer wraps at 2^bits (the author mutated only the read pointer): t8r runs CS_DEPTH 3")
mut("t2_rs_rd_pow2", "fifo", X, "if (rs_free_ev) rs_rd <= (rs_rd == $bits(rs_rd)'(RS_DEPTH - 1)) ? '0 : rs_rd + 1'b1;",
    "if (rs_free_ev) rs_rd <= rs_rd + 1'b1;", "kill",
    "RS_DEPTH is never a power of two (12 / 9 / 15 ...): the rowscale read pointer reaches entries past the depth once a run uses more "
    "lanes than RS_DEPTH (DIM 8: 4 + 6 + 8 lanes)")
mut("t2_rs_wr_pow2", "fifo", X, "if (s_rtag[4] && s_rtag[3]) rs_wr <= (rs_wr == $bits(rs_wr)'(RS_DEPTH - 1)) ? '0 : rs_wr + 1'b1;",
    "if (s_rtag[4] && s_rtag[3]) rs_wr <= rs_wr + 1'b1;", "kill", "the rowscale write pointer wraps at 2^bits")
mut("t2_wtag_cap4", "fifo", X, "assign w_rtag = w_tag[SPM_LAT-1];", "assign w_rtag = w_tag[(SPM_LAT > 4) ? 3 : SPM_LAT-1];", "kill",
    "the W response tag at latency 4 when SPM_LAT >= 5 (the author mutated only the A tag): t8r runs SPM_LAT 5")
mut("t2_stag_cap4", "fifo", X, "assign s_rtag = s_tag[SPM_LAT-1];", "assign s_rtag = s_tag[(SPM_LAT > 4) ? 3 : SPM_LAT-1];", "kill",
    "the scale-port tag (cs and rowscale) at latency 4 when SPM_LAT >= 5")
mut("t2_rs_depth_small", "perf", X, "localparam int RS_DEPTH = (2 * SPM_LAT + DIM + 6) / GRP + 3,", "localparam int RS_DEPTH = (2 * SPM_LAT + DIM + 6) / GRP,",
    "survive", "a rowscale FIFO 3 lanes smaller: rs_want waits for room, so it is correct; slower at most in the RESCALE passes, below CHKC's 2 %")
# ------------------------------------------------------------------ DIM 32 scale rows
mut("t2_cs_shift32_m1", "dim32", X, "64'(c_csp) * (64'(l_t) >> CS_SHIFT)", "64'(c_csp) * (64'(l_t) >> ((DIM == 32) ? CS_SHIFT - 1 : CS_SHIFT))",
    "kill", "DIM 32 takes a new scale row every pass (rows 0 1 2 3 instead of 0 0 1 1 for K = 512): rows 2, 3 are past the array")
mut("t2_cs_col32", "dim32", X, "64'd4 * 64'(DIM) * 64'(l_j));", "64'd4 * 64'((DIM == 32) ? 16 : DIM) * 64'(l_j));", "survive",
    "DIM 32 column tile j > 0 of the scales at the wrong offset: the DIM 32 cs case has N = 32 (NT = 1). A DIM-specific probe of what "
    "DIM 32 runs, not a likely defect")
# ------------------------------------------------------------------ semaphore wait (isa.md 3: (int32)(sem - target) >= 0)
mut("t2_wait_le", "wait", D, "(32'($signed(sem_rd_val - q_wval)) < 32'h8000_0000)", "(32'($signed(sem_rd_val - q_wval)) <= 32'h8000_0000)",
    "equiv", "differs only at sem - target = 2^31 exactly, outside isa.md's guarantee |value - target| < 2^31 (isa.sem_ready says not ready)")
mut("t2_wait_strict", "wait", D, "assign sem_ok = (f_wsem == 6'd0) || (32'($signed(sem_rd_val - q_wval)) < 32'h8000_0000);",
    "assign sem_ok = (f_wsem == 6'd0) || ((sem_rd_val != q_wval) && (32'($signed(sem_rd_val - q_wval)) < 32'h8000_0000));", "kill",
    "control: a wait met with equal values never starts (wsem 1 / wval 1 and wsem 4 / wval 2 are met exactly): TIMEOUT")
mut("t2_wait_wval0", "wait", D, "assign sem_ok = (f_wsem == 6'd0) ||", "assign sem_ok = (f_wsem == 6'd0) || (q_wval == 32'd0) ||", "survive",
    "wval 0 is taken as 'no wait': wrong while sem is in [2^31, 2^32) (0xFFFFFFFD is 3 behind 0); no wait has wval 0")
mut("t2_wait_31bit", "wait", D, "(32'($signed(sem_rd_val - q_wval)) < 32'h8000_0000)", "(31'(sem_rd_val - q_wval) < 31'h4000_0000)", "survive",
    "a serial compare on 31 bits: right for both the 2^31 and the 2^32 case (differences 1 .. 5 and 2^32 - 1 .. 2^32 - 5), wrong for a "
    "semaphore 2^30 .. 2^31 ahead of its target (legal by isa.md 3, e.g. a byte counter); no wait has a large lead")
# ------------------------------------------------------------------ total cycle count (CHKC, +2 %): slowdowns
mut("t2_gapx_plus1", "perf", X, "localparam int GAP_X = (ACC_LAT > 2) ? ACC_LAT - 2 : 0;", "localparam int GAP_X = (ACC_LAT > 2) ? ACC_LAT - 1 : 0;",
    "survive", "one idle cycle more after the last pass of every instruction with M >= 2: at most ~40 cycles of 2281 / 2367 (< 2 %)")
mut("t2_gap_bigm_plus1", "perf", X, "gap_cnt <= (s_Mrows >= 16'(ACC_LAT)) ? 8'd0 :", "gap_cnt <= (s_Mrows >= 16'(ACC_LAT)) ? 8'd1 :", "survive",
    "one idle cycle after every pass with M >= ACC_LAT (stream-bound passes): a few tens of cycles, about 1-2 %")
mut("t2_push_no_bypass", "perf", X, "L_PUSH: if (!tok_v || tok_take) begin", "L_PUSH: if (!tok_v) begin", "kill",
    "the loader hands the next token one cycle after the stream took the previous one (correct: the token is filled again): one cycle per "
    "stream-bound pass, more than 2 % (guess)")
mut("t2_ctx_after_tok", "perf", X, "assign ctx_ready = !ctx_v && (lst == L_IDLE);", "assign ctx_ready = !ctx_v && (lst == L_IDLE) && !tok_v;", "kill",
    "the next instruction's context only after the stream took the last token of the current one: less overlap per instruction (guess: > 2 %). "
    "COMPILE ERROR in the run (tok_v is declared after this line); not replaced: gating ctx_take alone is not a slowdown (L_IDLE tests ctx_v "
    "directly, so the context would be executed twice), and a correct version needs two edits")
# ------------------------------------------------------------------ late defects of the drained legal fault cases, long counters
mut("t2_lastp_7bit", "drain", X, "assign a_lastp = (s_m == s_Mrows - 16'd1);", "assign a_lastp = (s_m[6:0] == 7'(s_Mrows - 16'd1));", "survive",
    "a pass ends at row 127 when M >= 128: only the legal fault case with M = ACCR = 256 has such an M; mxu_fault_tc checks one acc_last, "
    "rows < ACCR and lanes in the SPM, but not the row count (the pass is half as long, still in range)")
mut("t2_lasttile_4bit", "drain", X, "assign last_tile = (l_j == c_NT - 16'd1) && (l_t == c_KT - 16'd1);",
    "assign last_tile = (l_j == c_NT - 16'd1) && (l_t[3:0] == 4'(c_KT - 16'd1));", "survive",
    "the instruction ends early when KT >= 17 (K tile counter compared on 4 bits): no instruction of any test has more than 10 K tiles")
mut("t2_wnt_j2bit", "drain", X, "kbeat * 64'(c_w_pitch) + 64'(l_j) * 64'(DIM));", "kbeat * 64'(c_w_pitch) + 64'(l_j[1:0]) * 64'(DIM));", "survive",
    "non-trans FP8 W column tile j taken mod 4: only the legal fault case (N = 64, NT = 8 at DIM 8) has NT > 4 in non-trans FP8, and "
    "mxu_fault_tc judges only that the addresses are inside the SPM (they alias downwards)")

T.M = M

if __name__ == "__main__":
    T.main()
