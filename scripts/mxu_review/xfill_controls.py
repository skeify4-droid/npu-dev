#!/usr/bin/env python3
"""Implementability review: positive controls for the X monitor of tb_mxu_rev.sv (+XFILL=1).

Each control is a one-change copy of the RTL (build/mxu_review/xctl/<name>/rtl/...) that should make an X visible: if the monitor
or the comparison does not flag it, the clean +XFILL result of run_review.py --xfill says nothing. Exits 1 if a control is missed.
Usage: xfill_controls.py   (DIM 8, two programs, serial, stall 0 and 30)
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_review as RR  # noqa: E402

ROOT = RR.ROOT
CONTROLS = {
    # the load FSM state loses its reset: X until something assigns it
    'lst_noreset': ('rtl/core/mxu.sv', "      lst <= L_IDLE;\n      l_j <= '0;", "      l_j <= '0;"),
    # the packer forwards a k-row lane that was not requested (FP8 non-trans, K tail): X bytes reach the shadow, then every column.
    # (The same change in the trans branch is not a control: an unrequested trans lane is a column >= N, which the contract masks.)
    'wpack_no_lanev': ('rtl/core/mxu_wpack.sv', "if (lane_v[4*h + q] && c < int'(ncols))", "if (c < int'(ncols))"),
    # the A bytes past the tile's K are not masked: X from lanes that were not requested
    'a_nomask': ('rtl/core/mxu.sv', "(b < int'(ar_kb)) ? a_rsp_data[8*b +: 8] : 8'd0", "a_rsp_data[8*b +: 8]"),
}


def make(name):
    f, old, new = CONTROLS[name]
    d = os.path.join(RR.BUILD, 'xctl', name)
    if os.path.isdir(d):
        shutil.rmtree(d)
    for sub in ('rtl/core', 'rtl/arith'):
        shutil.copytree(os.path.join(ROOT, sub), os.path.join(d, sub))
    p = os.path.join(d, f)
    s = open(p).read()
    assert s.count(old) == 1, (name, old)
    open(p, 'w').write(s.replace(old, new))
    return d


def main():
    progs = [p for p in RR.cases(8, 0) if p.name in ('k1_fp8_trans', 'fp8_nt_ragged')]
    missed = 0
    for name in CONTROLS:
        vvp = RR.build(8, rtl_root=make(name), tag='xctl_' + name)
        hits = []
        for p in progs:
            for stall in (0, 30):
                errs, _ = RR.run_case(vvp, p, 'serial', stall, seed=1, sub='xctl_cases', xfill=1)
                hits += errs
        xp = [e for e in hits if e.startswith('XPROP')]
        print('%-16s %s  (%d XPROP lines, %d other errors)%s' % (
            name, 'flagged' if hits else 'MISSED', len(xp), len(hits) - len(xp), ('  e.g. ' + hits[0][:110]) if hits else ''))
        missed += not hits
    return 1 if missed else 0


if __name__ == '__main__':
    sys.exit(main())
