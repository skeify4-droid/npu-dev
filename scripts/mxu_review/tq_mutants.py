#!/usr/bin/env python3
"""Review part T (test quality): the reviewer's own one-change mutants of rtl/core/mxu*.sv, run against `make unit`.

Written without reading the author's mutant list (scripts/mxu_mutants/mxu_mutants.py), from the RTL and the testbenches only.
Each mutant is a copy of rtl/ in build/mxu_review_tq/mut/<name>/rtl (core copied, arith and common symlinked) with exactly
one textual change (`old` must occur exactly once), and is judged by

    make unit RTL_DIR=<copy>/rtl UNIT_TB="tb/unit/tb_mxu_array.sv tb/unit/tb_mxu.sv"

i.e. the real `make unit` recipe (vector locks, compile in build/mxu, the grep for PASS and for vector-file warnings), restricted
to the two testbenches that compile rtl/core (no other testbench reads it). Killed = make unit fails; survived = it passes.
`pred` is the reviewer's prediction written before the run (kill / survive / equiv); the result table says where it was wrong.

Usage (tb_mxu peaks at about 1.5 GB per worker):
  python3 scripts/mxu_review/tq_mutants.py check            every `old` occurs exactly once (no simulation)
  python3 scripts/mxu_review/tq_mutants.py compile          every mutated copy compiles with tb_mxu (iverilog, about 2 min)
  scripts/memrun tq_mutants python3 scripts/mxu_review/tq_mutants.py run [--jobs N] [NAME ...]
    --w0 K: first worker index (default 0); --no-baseline: skip the unmutated run (a second instance next to a running one).
    --jobs N: N workers, each with its own BUILD copy (build/mxu_review_tq/w<k>); about 1.5 GB per worker. Without NAMEs, mutants
    already in results.tsv are skipped (resume).
Results: build/mxu_review_tq/results.tsv (name, area, pred, result, seconds, first failure line).
"""
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WORK = os.path.join(ROOT, "build", "mxu_review_tq")
TBS = "tb/unit/tb_mxu_array.sv tb/unit/tb_mxu.sv"
TIMEOUT = 1500
D, X, P, A = "mxu_dec.sv", "mxu.sv", "mxu_wpack.sv", "mxu_array.sv"
M = []


def mut(name, area, f, old, new, pred, why):
    M.append(dict(name=name, area=area, file=f, old=old, new=new, pred=pred, why=why))


# ------------------------------------------------------------------ decode and legality (mxu_dec)
mut("dec_acc_ge", "decode", D, "(acc_sum > (NTW+RW+1)'(ACC_ROWS))", "(acc_sum >= (NTW+RW+1)'(ACC_ROWS))", "survive",
    "a legal instruction whose rows end exactly at ACC_ROWS faults; no legal case ends there")
mut("dec_a_range_ge", "decode", D, "f_r_a   = !m_zero && (a_ext > 96'(SPM_BYTES));", "f_r_a   = !m_zero && (a_ext >= 96'(SPM_BYTES));",
    "survive", "A ending exactly at the end of the SPM faults; no legal A there")
mut("dec_w_range_ge", "decode", D, "f_r_w   = (w_ext > 96'(SPM_BYTES));", "f_r_w   = (w_ext >= 96'(SPM_BYTES));", "survive",
    "W ending exactly at the end of the SPM faults")
mut("dec_cs_range_ge", "decode", D, "f_r_cs  = cs_en && (cs_ext > 96'(SPM_BYTES));", "f_r_cs  = cs_en && (cs_ext >= 96'(SPM_BYTES));",
    "kill", "control: the S-10 case puts the cs extent at the very end")
mut("dec_rs_range_ge", "decode", D, "f_r_rs  = rs_en && !m_zero && (rs_ext > 96'(SPM_BYTES));",
    "f_r_rs  = rs_en && !m_zero && (rs_ext >= 96'(SPM_BYTES));", "survive", "rowscale ending exactly at the end of the SPM faults")
mut("dec_nt_floor", "decode", D, "(NTW+RW+1)'(vNT * vM[RW-1:0]);", "(NTW+RW+1)'(NTW'(vN >> LD) * vM[RW-1:0]);", "survive",
    "ACC RANGE uses floor(N / DIM): misses the overflow of a ragged last N tile; the NT*M fault cases run only at DIM 8 with N % 8 == 0")
mut("dec_kt_ceil", "decode", D, "17'(4 * DIM - 1)", "17'(4 * DIM - 2)", "survive",
    "FP8 K tiles = ceil off by one when K = n*4*DIM + 1: the one-element last pass is dropped; no test has K % (4 DIM) == 1")
mut("dec_ka_bf16", "decode", D, "ka_b = bf16 ? {vK, 1'b0} : {1'b0, vK};", "ka_b = {1'b0, vK};", "survive",
    "the A extent of BF16 counts K bytes instead of 2 K: a BF16 A overrunning the SPM is not a RANGE fault; fault cases are FP8 only")
mut("dec_csp_floor", "decode", D, "csp_q = 13'((19'(vN) * 19'd4 + 19'd63) >> 6);", "csp_q = 13'((19'(vN) * 19'd4) >> 6);", "kill",
    "CSP not rounded up: second scale row read at the wrong pitch (S-1 case, N = 40)")
mut("dec_wait_unsigned", "decode", D, "(32'($signed(sem_rd_val - q_wval)) < 32'h8000_0000)", "(sem_rd_val >= q_wval)", "survive",
    "the semaphore wait compares unsigned instead of serial-number: wrong after a wrap; no test wraps")
mut("dec_wait_strict", "decode", D, "(32'($signed(sem_rd_val - q_wval)) < 32'h8000_0000)",
    "(32'($signed(sem_rd_val - q_wval - 32'd1)) < 32'h8000_0000)", "kill", "control: a wait for exactly the value never starts")
mut("dec_rs_only_resc", "decode", D, "(i_accm == 2'd2 ? !rs_en : rs_en)", "(i_accm == 2'd2 && !rs_en)", "kill",
    "control: rs_reg without RESCALE accepted (fault cases)")
mut("dec_kw_trans", "decode", D, "vKw  = i_trans ? w_n0 : w_n1;", "vKw  = w_n0;", "kill", "control: K of a non-trans W read from n0")
mut("dec_w3_check", "decode", D, "(w_n2 != 16'd1) || (w_n3 != 16'd1) ||", "(w_n2 != 16'd1) ||", "kill", "control: W n3 not checked")
mut("dec_m_tdonly", "decode", D, "vM   = m_en ? r_m : 64'(a_n1);", "vM   = 64'(a_n1);", "kill", "control: M from the TD decoy")
mut("dec_a_pitch_w", "decode", D, "c_a_pitch  <= 22'(a_s1);", "c_a_pitch  <= 22'(w_s1);", "kill", "control: A pitch taken from W")
mut("dec_flag_wrap", "decode", D, "(a_fl != 8'd0) || (w_fl != 8'd0)", "(a_fl[1:0] != 2'd0) || (w_fl != 8'd0)", "kill",
    "A TD flag bit 2 (WRAP) accepted (M-16 fault case on A)")
# ------------------------------------------------------------------ address generation (mxu)
mut("ag_a_addr_17b", "address", X, "s_addr <= tk_a_base + 22'(64'(tk_t) * 64'(KB));",
    "s_addr <= 22'(17'(tk_a_base + 22'(64'(tk_t) * 64'(KB))));", "survive",
    "A start address truncated to 17 bits: the testbench SPM is 128 KiB, so address bits 17..21 are never non-zero")
mut("ag_accrow_8b", "address", X, "s_accrow <= s_accrow + 11'd1;", "s_accrow <= {3'd0, s_accrow[7:0] + 8'd1};", "survive",
    "ACC row wraps at 256: the testbench ACC has 256 rows (real: 2048), bits 8..10 of acc_row are never set")
mut("ag_accrow_jdim", "address", X, "11'(64'(tk_j) * 64'(tk_M))", "11'(64'(tk_j) * 64'(DIM))", "kill",
    "control: N tile j at acc_base + j DIM instead of j M")
mut("ag_cs_row_t", "address", X, "64'(c_csp) * (64'(l_t) >> CS_SHIFT)", "64'(c_csp) * 64'(l_t)", "kill",
    "control: one scale row per MXU pass (the rule before M-13)")
mut("ag_cs_shift_tiny", "address", X, "localparam int CS_SHIFT = 6 - $clog2(DIM),", "localparam int CS_SHIFT = 3,", "kill",
    "control for DIM coverage: correct at DIM 8 only; DIM 16 must kill it")
mut("ag_nontrans_idx", "address", X, "kbeat = ke0 + 64'd8 * (64'(l_g) * 64'(GRP / 2) + 64'(l_idx)) + 64'(l);",
    "kbeat = ke0 + 64'd8 * (64'(l_g) * 64'(GRP / 2)) + 64'(l);", "kill",
    "FP8 non-trans k-row ignores the beat index: equivalent at DIM 8 (one beat per group), DIM 16 must kill it")
mut("ag_bf16_half", "address", X, "64'(l % 2) * 64'(LB));", "64'(l % 2) * 64'(LB / 2));", "kill",
    "control: BF16 non-trans second lane half at LB / 2")
mut("ag_trans_g", "address", X, "64'(l_g) * 64'(LB));\n      end\n    end else if", "64'(l_g) * 64'(LB / 2));\n      end\n    end else if",
    "kill", "control: trans lane offset of group g")
mut("ag_wbase_t", "address", X, "64'(l_j) * 64'(DIM) * 64'(c_w_pitch) + 64'(l_t) * 64'(KB))", "64'(l_j) * 64'(DIM) * 64'(c_w_pitch) + 64'(l_t) * 64'(LB))",
    "kill", "control: trans W of K tile t at t LB")
# ------------------------------------------------------------------ tail masks
mut("tm_a_kb_le", "tail", X, "(b < int'(ar_kb)) ? a_rsp_data", "(b <= int'(ar_kb)) ? a_rsp_data", "kill",
    "control: one A byte past K is not zeroed")
mut("tm_a_lane_extra", "tail", X, "assign a_lane_v[l] = (l * LB) < int'(s_kb);", "assign a_lane_v[l] = (l * LB) <= int'(s_kb);",
    "survive", "an A lane past the tile's K is requested (data masked): an over-read no check sees")
mut("tm_w_lane_k_trans", "tail", X, "wl_lane_v[l] = (col_idx < 64'(c_N)) && (nb_calc != 64'd0);", "wl_lane_v[l] = (col_idx < 64'(c_N));",
    "survive", "trans W lanes of groups past K are requested (data zeroed by nbytes): over-read, and empty beats become requests")
mut("tm_w_k_nontrans", "tail", X, "wl_lane_v[l] = kbeat < 64'(c_K);\n        wl_lane_addr[l] = 22'(64'(c_w_base) + kbeat * 64'(c_w_pitch) + 64'(l_j) * 64'(DIM));",
    "wl_lane_v[l] = 1'b1;\n        wl_lane_addr[l] = 22'(64'(c_w_base) + kbeat * 64'(c_w_pitch) + 64'(l_j) * 64'(DIM));", "kill",
    "control: FP8 non-trans k-rows past K are packed (NaN guard rows)")
mut("tm_tkb_ge", "tail", X, "t_kb = 9'(((ctx_Kbytes - 64'(l_t) * 64'(KB)) > 64'(KB))", "t_kb = 9'(((ctx_Kbytes - 64'(l_t) * 64'(KB)) >= 64'(KB))",
    "equiv", "clamp at KB: both branches give KB when equal")
mut("tm_ncols_full", "tail", X, "t_ncols = 8'((64'(c_N) - 64'(l_j) * 64'(DIM)) > 64'(DIM)", "t_ncols = 8'((64'(c_N) - 64'(l_j) * 64'(DIM)) > 64'(0)",
    "kill", "control: every N tile reports DIM columns (dead columns written; OVERWRITE N tail)")
# ------------------------------------------------------------------ packer (mxu_wpack)
mut("pk_trans_nbytes_le", "packer", P, "(4*w + q) < int'(nbytes)", "(4*w + q) <= int'(nbytes)", "kill", "control: trans K tail byte kept")
mut("pk_trans_lanev", "packer", P, "if (lane_v[l] && (4*w + q) < int'(nbytes))", "if ((4*w + q) < int'(nbytes))", "survive",
    "trans: unrequested lanes (columns >= N) packed as garbage; dead columns are invisible at the ACC and in the flags")
mut("pk_fp8_ncols", "packer", P, "if (lane_v[4*h + q] && c < int'(ncols))", "if (lane_v[4*h + q])", "survive",
    "FP8 non-trans: columns >= N not zeroed; same reason")
mut("pk_bf16_ncols", "packer", P, "if (lane_v[2*(2*h + q) + c/(DIM/2)] && c < int'(ncols))", "if (lane_v[2*(2*h + q) + c/(DIM/2)])",
    "survive", "BF16 non-trans: columns >= N not zeroed (and BF16 non-trans never has an N tail in tb_mxu)")
mut("pk_bf16_lo", "packer", P, "= byt[2*(2*h + q) + c/(DIM/2)][2*(c % (DIM/2))];", "= byt[2*(2*h + q) + c/(DIM/2)][2*(c % (DIM/2)) + 1];",
    "kill", "control: BF16 low byte taken from the high byte")
mut("pk_trans_order", "packer", P, "= byt[l][4*w + q];", "= byt[l][4*w + 3 - q];", "kill", "control: trans byte order in the word")
mut("pk_fp8_col", "packer", P, "= byt[4*h + q][c];", "= byt[4*h + q][c ^ 1];", "kill", "control: FP8 non-trans columns swapped in pairs")
mut("pk_bf16_q", "packer", P, "win[(h*DIM + c)*32 + 16*q + 8 +: 8]", "win[(h*DIM + c)*32 + 16*(1-q) + 8 +: 8]", "kill",
    "control: BF16 high bytes of the two k-rows swapped")
# ------------------------------------------------------------------ buffer logic (load FSM, shadow buffers, FIFOs)
mut("bf_free_noclear", "buffer", X, "else if (w_gnt && (int'(l_buf) * NG + int'(l_g)) == g) free_g[g] <= 1'b0;",
    "else if (1'b0) free_g[g] <= 1'b0;", "kill", "a group is never marked busy: the loader may overwrite a shadow its swap has not copied")
mut("bf_free_any_buf", "buffer", X, "if (swap_tap[g]) free_g[g] <= 1'b1;", "if (swap_tap[g % NG] || swap_tap[NG + g % NG]) free_g[g] <= 1'b1;",
    "kill", "the tap of either buffer frees both")
mut("bf_free_every_beat", "buffer", X, "(free_g[int'(l_buf) * NG + int'(l_g)] || l_idx != '0)", "(free_g[int'(l_buf) * NG + int'(l_g)])",
    "kill", "every beat waits for free: deadlock when a group has 2+ beats (DIM 16)")
mut("bf_one_buffer", "buffer", X, "l_buf <= !l_buf;", "l_buf <= l_buf;", "kill",
    "one shadow buffer: still correct (free_g protects it), slower; only the DIM 32 period bound (26) can see it")
mut("bf_tok_overwrite", "buffer", X, "L_PUSH: if (!tok_v || tok_take) begin", "L_PUSH: if (1'b1) begin", "kill",
    "control: the load FSM moves on while the token is still full")
mut("bf_cs_full_off", "buffer", X, "if (!t_cs_en || cs_cnt < CS_DEPTH[$clog2(CS_DEPTH):0]) lst <= L_RUN;", "lst <= L_RUN;", "survive",
    "the column-scale FIFO full check removed: does any test fill CS_DEPTH = 4?")
mut("bf_rs_res_off", "buffer", X, "(rs_res < RS_DEPTH[$clog2(RS_DEPTH):0])", "1'b1", "survive",
    "the rowscale reservation limit removed (review H-1: never binds)")
mut("bf_inf_room_off", "buffer", X, ".inf_room(inf_cnt < 3'd4)", ".inf_room(1'b1)", "survive",
    "the in-flight FIFO full check removed (review H-2: never full in tb_mxu)")
mut("bf_swap_buf0", "buffer", X, ".swap_buf(ar_buf)", ".swap_buf(1'b0)", "kill", "control: every swap from buffer 0")
mut("bf_spm_lat_hard", "buffer", X, "assign s_rtag = s_tag[SPM_LAT-1];", "assign s_rtag = s_tag[1];", "survive",
    "scale tag latency hard-coded to 2: equivalent at SPM_LAT = 2, the only latency make unit simulates")
# ------------------------------------------------------------------ exit stage and completion
mut("ex_pop_at_last_row", "exit", X, "assign inf_pop = acc_done && (inf_cnt != 3'd0);",
    "assign inf_pop = acc_valid && acc_last && (inf_cnt != 3'd0);", "survive",
    "sem_inc at the last row instead of acc_done: completion before the write is visible; the testbench never checks the cycle")
mut("ex_last_every_pass", "exit", X, "assign acc_last      = y_valid && x_lasti;", "assign acc_last      = y_valid && x_lastp;", "survive",
    "acc_last on the last row of every pass: extra acc_done pulses retire later instructions early")
mut("ex_flags_late", "exit", X, "assign fp_flags      = arr_flags;",
    "logic [32*4-1:0] fl_sr;\n  always_ff @(posedge clk or negedge rst_n) if (!rst_n) fl_sr <= '0; else fl_sr <= {fl_sr[31*4-1:0], arr_flags};\n"
    "  assign fp_flags      = fl_sr[32*4-1 -: 4];", "survive",
    "fp_flags 32 cycles late (after acc_done): only the OR over the whole run is checked")
mut("ex_flags_sticky", "exit", X, "assign fp_flags      = arr_flags;",
    "logic [3:0] fl_st;\n  always_ff @(posedge clk or negedge rst_n) if (!rst_n) fl_st <= '0; else fl_st <= fl_st | arr_flags;\n"
    "  assign fp_flags      = fl_st;", "survive", "fp_flags sticky: every later instruction reports the flags of an earlier one")
mut("ex_flags_nomask", "exit", X, ".a_ncols(ar_nc),", ".a_ncols(NCW'(DIM)),", "survive",
    "flags of the dead columns (>= N) not masked at the array input of mxu (the array's mask itself is tested by tb_mxu_array)")
mut("ex_cs_pop_lasti", "exit", X, "assign cs_free_ev    = y_valid && x_lastp && x_ucs;", "assign cs_free_ev    = y_valid && x_lasti && x_ucs;",
    "kill", "control: column scales popped once per instruction")
mut("ex_rs_lastp", "exit", X, "((rs_idx == ($clog2(GRP+1))'(GRP - 1)) || x_lastp)", "(rs_idx == ($clog2(GRP+1))'(GRP - 1))", "kill",
    "control: a partial last rowscale lane is not dropped at the end of a pass")
mut("ex_ovw_all_t", "exit", X, "assign a_ovw   = (s_accm == 2'd0) && (s_t == 16'd0);", "assign a_ovw   = (s_accm == 2'd0);", "kill",
    "control: OVERWRITE on every K pass")
mut("ex_sem_amt3", "exit", X, "assign sem_inc_amt   = sem_inc_w[3:0];", "assign sem_inc_amt   = {1'b0, sem_inc_w[2:0]};", "survive",
    "bit 3 of sinc lost: every sinc in tb_mxu is <= 3")
mut("ex_sem_idx5", "exit", X, "assign sem_inc_idx   = sem_inc_w[9:4];", "assign sem_inc_idx   = {1'b0, sem_inc_w[8:4]};", "survive",
    "bit 5 of ssem lost: every ssem in tb_mxu is <= 5")
mut("ex_zinc_head", "exit", X, "assign sem_inc_w     = z_inc ? ctx_sync : inf_head;", "assign sem_inc_w     = inf_head;", "kill",
    "control: an M = 0 completion increments the semaphore of a stale FIFO entry")
mut("ex_gap_x_m3", "exit", X, "if (s_last && s_Mrows >= 16'd2) gap_cnt <= 8'(GAP_X);", "if (s_last && s_Mrows >= 16'd3) gap_cnt <= 8'(GAP_X);",
    "equiv", "M = 2 keeps ACC_LAT - M = ACC_LAT - 2 = GAP_X")
mut("ex_gap_x_m4", "exit", X, "if (s_last && s_Mrows >= 16'd2) gap_cnt <= 8'(GAP_X);", "if (s_last && s_Mrows >= 16'd4) gap_cnt <= 8'(GAP_X);",
    "kill", "M = 3 at an instruction boundary gets ACC_LAT - 3 (R-1 has an M = 3 case starting two rows early)")
mut("ex_gap_plus1", "exit", X, ": 8'(ACC_LAT) - 8'(s_Mrows);", ": 8'(ACC_LAT) - 8'(s_Mrows) + 8'd1;", "survive",
    "one idle cycle more per pass: a 5-8 % throughput loss; the period is only bounded at DIM 32 (26 against 21.5 measured)")
# ------------------------------------------------------------------ array (also checked by tb_mxu_array)
mut("ar_flag_stage", "array", A, "assign masked[j] = (sv[i+2] && (NCW'(j) < sn[i+2]))", "assign masked[j] = (sv[i+1] && (NCW'(j) < sn[i+1]))",
    "kill", "control: flags qualified with the wave one stage early")
mut("ar_a_nozero", "array", A, "assign a_in = a_valid ? a_words[32*i +: 32] : 32'd0;", "assign a_in = a_words[32*i +: 32];", "equiv",
    "invalid waves not zeroed: their outputs and flags are masked by the valid chain")
mut("ar_swap_prio", "array", A, "else if (swp1[i])  w_ac <= w_sh1;", "if (swp1[i])  w_ac <= w_sh1;", "equiv",
    "swap from buffer 1 has priority: the two swap chains never pulse in the same row and cycle")


def check():
    ok = True
    names = set()
    for m in M:
        assert m["name"] not in names, m["name"]
        names.add(m["name"])
        n = open(os.path.join(ROOT, "rtl", "core", m["file"])).read().count(m["old"])
        if n != 1:
            print("BAD %s: `old` occurs %d times in %s" % (m["name"], n, m["file"]))
            ok = False
    print("%d mutants, %s" % (len(M), "all `old` strings unique" if ok else "ERRORS"))
    return ok


def make_copy(m):
    d = os.path.join(WORK, "mut", m["name"] if m else "none", "rtl")
    shutil.rmtree(os.path.dirname(d), ignore_errors=True)
    os.makedirs(d)
    shutil.copytree(os.path.join(ROOT, "rtl", "core"), os.path.join(d, "core"))
    for g in ("arith", "common"):
        os.symlink(os.path.join(ROOT, "rtl", g), os.path.join(d, g))
    if m:
        p = os.path.join(d, "core", m["file"])
        s = open(p).read()
        assert s.count(m["old"]) == 1
        open(p, "w").write(s.replace(m["old"], m["new"]))
    return d


def worker_build(k):
    """build/mxu_review_tq/w<k>: a copy of build/arith and build/mxu (time stamps kept, so the stamps stay fresh) for worker k;
    make unit writes its binaries and logs into BUILD, so parallel workers must not share it."""
    b = os.path.join(WORK, "w%d" % k)
    if not os.path.isdir(os.path.join(b, "mxu")):
        shutil.rmtree(b, ignore_errors=True)
        os.makedirs(b)
        for g in ("arith", "mxu"):
            subprocess.run(["cp", "-Rp", os.path.join(ROOT, "build", g), b], check=True)
        for f in os.listdir(os.path.join(b, "mxu")):
            if f.endswith(".vvp"):
                os.remove(os.path.join(b, "mxu", f))
    return b


def run_one(m, k=0):
    d = make_copy(m)
    b = worker_build(k)
    t0 = time.time()
    try:
        r = subprocess.run(["make", "unit", "RTL_DIR=" + d, "UNIT_TB=" + TBS, "BUILD=" + b], cwd=ROOT, capture_output=True, text=True,
                           timeout=TIMEOUT)
        out, rc = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        out, rc = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), "timeout"
    dt = time.time() - t0
    name = m["name"] if m else "none"
    open(os.path.join(WORK, "mut", name, "make_unit.log"), "w").write(out)
    passed = rc == 0 and "unit: ALL PASS" in out
    first = ""
    for ln in out.splitlines():
        if ln.startswith(("FAIL", "unit FAILED", "MISMATCH", "mxu_lock")) or "TIMEOUT" in ln or "error" in ln.lower():
            first = ln.strip()[:200]
            break
    if "unit FAILED (compile)" in out:
        return "compile_error", dt, first
    return ("survived" if passed else ("timeout" if rc == "timeout" else "killed")), dt, first


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "check":
        sys.exit(0 if check() else 1)
    if cmd == "compile":          # every mutated copy compiles with tb_mxu (iverilog, no simulation)
        bad = 0
        for m in M:
            d = make_copy(m)
            src = [os.path.join(ROOT, "isa/gen/isa_pkg.sv")] + sorted(
                os.path.join(d, "core", f) for f in os.listdir(os.path.join(d, "core"))) + sorted(
                os.path.join(ROOT, "rtl/arith", f) for f in os.listdir(os.path.join(ROOT, "rtl/arith")) if f.endswith(".sv"))
            r = subprocess.run(["iverilog", "-g2012", "-o", os.path.join(WORK, "cc.vvp"), os.path.join(ROOT, "tb/unit/tb_mxu.sv")] + src,
                               cwd=os.path.join(ROOT, "build", "mxu"), capture_output=True, text=True)
            if r.returncode:
                bad += 1
                print("COMPILE ERROR", m["name"], r.stderr.strip().splitlines()[:3])
        print("compile: %d of %d failed" % (bad, len(M)))
        sys.exit(1 if bad else 0)
    if cmd != "run":
        sys.exit(__doc__)
    if not check():
        sys.exit(1)
    args = sys.argv[2:]
    jobs = 1
    if "--jobs" in args:
        i = args.index("--jobs")
        jobs = int(args[i + 1])
        del args[i:i + 2]
    w0, base = 0, True
    if "--w0" in args:                 # first worker index (a second instance next to a running one uses other BUILD copies)
        i = args.index("--w0")
        w0 = int(args[i + 1])
        del args[i:i + 2]
    if "--no-baseline" in args:
        args.remove("--no-baseline")
        base = False
    want = args
    os.makedirs(os.path.join(WORK, "mut"), exist_ok=True)
    res_path = os.path.join(WORK, "results.tsv")
    done = set(l.split("\t")[0] for l in open(res_path)) if os.path.exists(res_path) and not want else set()
    todo = [m for m in M if (not want or m["name"] in want) and m["name"] not in done]
    if base:
        res, dt, first = run_one(None, w0)
        print("baseline (unmutated copy): %s in %.0f s %s" % (res, dt, first), flush=True)
        if res != "survived":
            sys.exit("the unmutated copy does not pass make unit")
    print("%d mutants to run (%d already in results.tsv), %d jobs" % (len(todo), len(done), jobs), flush=True)
    import queue
    import threading
    q = queue.Queue()
    for m in todo:
        q.put(m)
    lock = threading.Lock()

    def work(k):
        while True:
            try:
                m = q.get_nowait()
            except queue.Empty:
                return
            res, dt, first = run_one(m, k)
            hit = {"kill": res != "survived", "survive": res == "survived", "equiv": res == "survived"}[m["pred"]]
            line = "\t".join([m["name"], m["area"], m["pred"], res, "%.0f" % dt, "" if hit else "PREDICTION WRONG", first])
            with lock:
                with open(res_path, "a") as fh:
                    fh.write(line + "\n")
                print(line, flush=True)
    th = [threading.Thread(target=work, args=(w0 + k,)) for k in range(jobs)]
    for t in th:
        t.start()
    for t in th:
        t.join()


if __name__ == "__main__":
    main()
