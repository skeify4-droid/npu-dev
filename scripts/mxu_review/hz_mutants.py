#!/usr/bin/env python3
"""Review part H: is the random environment of hz_run.py strong enough? One-change copies of rtl/core run against it.

The author's control mutants (scripts/mxu_mutants/mxu_mutants.py, read for their text only) and the reviewer's own (below).
Expectation: `kill` (some run fails) or `equiv` (every run passes). Copies go to build/mxu_review_hz/mut/<name>/rtl/{core,arith}.
Usage (under scripts/memrun): hz_mutants.py [--seeds N] [--cfg ...] [NAME ...]
"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'scripts', 'mxu_mutants'))
import mxu_mutants as mm  # noqa: E402  (only its list of textual changes)

OUT = os.path.join(ROOT, 'build', 'mxu_review_hz', 'mut')
X, D = 'mxu.sv', 'mxu_dec.sv'

AUTHOR = ['no_free_gate', 'swap_early', 'last_beat_early', 'cs_pop_first', 'rs_pop_end', 'cs_cnt_leak', 'token_live',
          's_cfg_always', 'inf_mem_always', 'gap_cnt', 'no_gap_f', 'gap_x_off', 'gap_x_short', 'w_gnt_wait', 'zero_no_wait',
          'zero_early', 'zero_sem_mux', 'zero_runs', 'sem_head', 'acc_row_nt', 'rescale_t0', 'overwrite_t0', 'a_pitch',
          'token_at_request', 'gap_x_every_pass', 'spur_pop']
# the author's expectation in tb_mxu terms -> ours (spur_pop needs a spurious acc_done, which this environment never makes)
AEXP = {'token_at_request': 'equiv', 'gap_x_every_pass': 'equiv', 'spur_pop': 'equiv', 'inf_mem_always': 'kill'}

OWN = [
    # (name, file, old, new, expectation, note)
    ('h_gap_in_short1', X, "8'(ACC_LAT) - 8'(s_Mrows);", "8'(ACC_LAT) - 8'(s_Mrows) - 8'd1;", 'equiv',
     'same-instruction gap one cycle short: the spacing is M + 1 + gap, one cycle of slack (mxu.md 4.4)'),
    ('h_gap_in_short2', X, "8'(ACC_LAT) - 8'(s_Mrows);", "8'(ACC_LAT) - 8'(s_Mrows) - 8'd2;", 'kill',
     'same-instruction gap two cycles short'),
    ('h_cs_full', X, "if (!t_cs_en || cs_cnt < CS_DEPTH[$clog2(CS_DEPTH):0]) lst <= L_RUN;",
     "if (!t_cs_en || cs_cnt <= CS_DEPTH[$clog2(CS_DEPTH):0]) lst <= L_RUN;", 'kill', 'cs FIFO overfilled by one'),
    ('h_inf_room', X, ".inf_room(inf_cnt < 3'd4)", ".inf_room(inf_cnt < 3'd5)", 'kill', 'in-flight FIFO takes a fifth entry'),
    ('h_tok_over', X, "L_PUSH: if (!tok_v || tok_take) begin", "L_PUSH: if (1'b1) begin", 'kill', 'token overwritten'),
    ('h_ctx_ready', X, "assign ctx_ready = !ctx_v && (lst == L_IDLE);", "assign ctx_ready = !ctx_v;", 'kill',
     'next context written while the load FSM still runs the previous instruction'),
    ('h_zero_cnt1', X, ".inf_empty(inf_cnt == 3'd0)", ".inf_empty(inf_cnt <= 3'd1)", 'kill',
     'M = 0 completes with one instruction still in flight'),
    ('h_gap_last_live', X, "if (s_last && s_Mrows >= 16'd2) gap_cnt", "if (tk_last && s_Mrows >= 16'd2) gap_cnt", 'kill',
     'boundary gap chosen from the token register (the next pass) instead of the pass that ends'),
    ('h_cs_free_lasti', X, "assign cs_free_ev    = y_valid && x_lastp && x_ucs;", "assign cs_free_ev    = y_valid && x_lasti && x_ucs;",
     'kill', 'cs entry freed per instruction instead of per pass'),
    ('h_free_any', X, "else if (w_gnt && (int'(l_buf) * NG + int'(l_g)) == g) free_g[g] <= 1'b0;",
     "else if (w_gnt && int'(l_g) == g % NG) free_g[g] <= 1'b0;", 'kill',
     'a beat clears the free flag of its group in both buffers (the other buffer then waits for a tap that never comes)'),
    ('h_free_first_only', X, "else if (w_gnt && (int'(l_buf) * NG + int'(l_g)) == g) free_g[g] <= 1'b0;",
     "else if (w_gnt && l_idx == '0 && (int'(l_buf) * NG + int'(l_g)) == g) free_g[g] <= 1'b0;", 'equiv',
     'only the first beat of a group clears its free flag (the others do not look at it)'),
    ('h_rs_res_off', X, "rs_res <= rs_res + (rs_gnt ? 1 : 0) - (rs_free_ev ? 1 : 0);", "rs_res <= '0;", 'equiv',
     'no rowscale reservation limit: the limit is never reached (rsfull = 0 in every run)'),
    ('h_s_tag_lane', X, "cs_lane_issue == 2'd3, cs_lane_issue};", "cs_lane_issue == 2'd2, cs_lane_issue};", 'kill',
     'column scales marked complete one lane early'),
]


def make(name, file, old, new):
    d = os.path.join(OUT, name)
    if os.path.exists(d):
        shutil.rmtree(d)
    os.makedirs(os.path.join(d, 'rtl'))
    shutil.copytree(os.path.join(ROOT, 'rtl', 'core'), os.path.join(d, 'rtl', 'core'))
    shutil.copytree(os.path.join(ROOT, 'rtl', 'arith'), os.path.join(d, 'rtl', 'arith'))
    p = os.path.join(d, 'rtl', 'core', file)
    s = open(p).read()
    assert s.count(old) == 1, (name, s.count(old))
    open(p, 'w').write(s.replace(old, new))
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('names', nargs='*')
    ap.add_argument('--seeds', type=int, default=6)
    ap.add_argument('--cfg', nargs='*', default=['d8_l2_a8', 'd8_l1_a1', 'd8_l3_a3', 'd8_l5_a13', 'd16_l2_a8', 'd16_l1_a3'])
    ap.add_argument('--jobs', type=int, default=6)
    a = ap.parse_args()
    table = {}
    for n, f, o, nw, e, note in mm.M:
        if n in AUTHOR:
            table[n] = (f, o, nw, AEXP.get(n, 'kill'), 'author: ' + note)
    for n, f, o, nw, e, note in OWN:
        table[n] = (f, o, nw, e, note)
    names = a.names or list(table)
    bad = 0
    for n in names:
        f, o, nw, e, note = table[n]
        d = make(n, f, o, nw)
        cmd = [sys.executable, os.path.join(HERE, 'hz_run.py'), '--rtl', d, '--seeds', str(a.seeds), '--jobs', str(a.jobs),
               '--quiet', '--cfg'] + a.cfg
        if e == 'kill':
            cmd.append('--stop')
        r = subprocess.run(cmd, capture_output=True, text=True)
        killed = r.returncode != 0
        first = next((l.strip() for l in r.stdout.splitlines() if l.startswith('    ')), '')
        ok = killed == (e == 'kill')
        bad += not ok
        print('%-20s %-6s %-8s %s  %s' % (n, e, 'killed' if killed else 'survived', 'ok' if ok else 'UNEXPECTED', note))
        if killed:
            print('    ' + first[:300])
        sys.stdout.flush()
    print('%d mutants, %d unexpected' % (len(names), bad))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
