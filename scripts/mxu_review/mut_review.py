#!/usr/bin/env python3
"""Sensitivity of the review's MXU_MM harness (review part R): one-change copies of rtl/core + rtl/arith in
build/mxu_review/mut/<name>/ (the author's RTL is not edited), each run against the directed cases of run_review.py at DIM 8
(serial, no stalls; then pipelined with 30% stalls if the first run did not kill it). These are the review's own mutants,
chosen for the semantics checked here (scale rows, ACC controls, tails, flags), not the author's set.
Usage: mut_review.py [--only NAME]
"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_review as RR  # noqa: E402

ROOT = RR.ROOT
MUT = os.path.join(RR.BUILD, 'mut')

# name: (file relative to rtl/, old text, new text, expectation)
MUTANTS = {
    'cs_row_per_pass':  ('core/mxu.sv', '64\'(c_csp) * (64\'(l_t) >> CS_SHIFT)', '64\'(c_csp) * 64\'(l_t)', 'kill'),
    'cs_col_bytes':     ('core/mxu.sv', '64\'d4 * 64\'(DIM) * 64\'(l_j)', '64\'(DIM) * 64\'(l_j)', 'kill'),
    'ovw_every_pass':   ('core/mxu.sv', 'assign a_ovw   = (s_accm == 2\'d0) && (s_t == 16\'d0);',
                         'assign a_ovw   = (s_accm == 2\'d0);', 'kill'),
    'rescale_every_pass': ('core/mxu.sv', 'assign rs_need = (s_accm == 2\'d2) && (s_t == 16\'d0);',
                           'assign rs_need = (s_accm == 2\'d2);', 'kill'),
    'a_mask_one_more':  ('core/mxu.sv', '(b < int\'(ar_kb)) ? a_rsp_data', '(b <= int\'(ar_kb)) ? a_rsp_data', 'kill'),
    'w_tail_one_more':  ('core/mxu_wpack.sv', 'if (lane_v[l] && (4*w + q) < int\'(nbytes))',
                         'if (lane_v[l] && (4*w + q) <= int\'(nbytes))', 'kill'),
    'no_col_mask_flags': ('core/mxu_array.sv', '(sv[i+2] && (NCW\'(j) < sn[i+2]))', '(sv[i+2])', 'kill'),
    'add_nan_in_flag':  ('arith/mxu_pe.sv', 'assign f_new = {f_dot_q[3], 1\'b0,', 'assign f_new = {f_dot_q[3] | f_add[3], 1\'b0,',
                         'kill'),
    'acc_row_dim':      ('core/mxu.sv', 's_accrow <= tk_acc_base + 11\'(64\'(tk_j) * 64\'(tk_M));',
                         's_accrow <= tk_acc_base + 11\'(64\'(tk_j) * 64\'(DIM));', 'kill'),
    'rs_lane_half':     ('core/mxu.sv', 'assign rs_addr = s_rs_base + 22\'(s_lane_cnt) * 22\'(LB);',
                         'assign rs_addr = s_rs_base + 22\'(s_lane_cnt) * 22\'(LB / 2);', 'kill'),
    'last_every_pass':  ('core/mxu.sv', 'assign a_lasti = a_lastp && s_last;', 'assign a_lasti = a_lastp;', 'kill'),
    'ncols_plus_one':   ('core/mxu.sv', 'assign acc_ncols     = x_ncols;', 'assign acc_ncols     = x_ncols + 7\'d1;', 'kill'),
    'nt_bf16_lane_swap': ('core/mxu.sv', '64\'d2 * 64\'(l_j) * 64\'(DIM) + 64\'(l % 2) * 64\'(LB)',
                          '64\'d2 * 64\'(l_j) * 64\'(DIM) + 64\'(1 - l % 2) * 64\'(LB)', 'kill'),
    'gap_off':          ('core/mxu.sv', 'gap_cnt <= (s_Mrows >= 16\'(ACC_LAT)) ? 8\'d0 : 8\'(ACC_LAT) - 8\'(s_Mrows);',
                         'gap_cnt <= 8\'d0;', 'kill'),
}


def make(name):
    f, old, new, _ = MUTANTS[name]
    d = os.path.join(MUT, name)
    if os.path.isdir(d):
        shutil.rmtree(d)
    for sub in ('core', 'arith'):
        shutil.copytree(os.path.join(ROOT, 'rtl', sub), os.path.join(d, 'rtl', sub))
    p = os.path.join(d, 'rtl', f)
    s = open(p).read()
    assert s.count(old) == 1, (name, s.count(old))
    open(p, 'w').write(s.replace(old, new))
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default='')
    args = ap.parse_args()
    res = {}
    for name in MUTANTS:
        if args.only and args.only not in name:
            continue
        d = make(name)
        vvp = RR.build(8, rtl_root=d, tag=name)
        killed = None
        for mode, stall in (('serial', 0), ('pipe', 30)):
            for p in RR.cases(8, 0):
                errs, _ = RR.run_case(vvp, p, mode, stall, seed=7, maxcyc=60000, sub='mut_' + name)
                if errs:
                    killed = '%s (%s, stall %d): %s' % (p.name, mode, stall, errs[0][:110])
                    break
            if killed:
                break
        res[name] = killed
        want = MUTANTS[name][3]
        ok = (killed is not None) == (want == 'kill')
        print('%-4s %-20s %s' % ('ok' if ok else 'BAD', name, ('killed by ' + killed) if killed else 'SURVIVED'))
        sys.stdout.flush()
        shutil.rmtree(d)
    bad = sum(1 for n, k in res.items() if (k is not None) != (MUTANTS[n][3] == 'kill'))
    print('%d mutants, %d not as expected' % (len(res), bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
