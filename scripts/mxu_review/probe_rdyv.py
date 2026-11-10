#!/usr/bin/env python3
"""Review part R, probe of the SPM handshake: does `mxu` need `ready` on a port whose request has no valid lane?

mxu.sv counts a W beat as granted on `w_want && w_req_ready` even when `w_req_v` is all zero (a beat whose lanes are all past
K or N). With a valid/ready SPM side that raises `ready` only for a request (+RDYV=1 in tb_mxu_rev), such a beat is never
granted. This runs the directed cases of run_review.py with +RDYV=1 (no random stalls) and reports which hang, next to an
independent count of the empty W beats of each program (from the beat order of mxu.md 4.3, not from the RTL).
Usage: probe_rdyv.py [--dim 8 16]
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_review as RR  # noqa: E402


def empty_w_beats(ins, dim):
    """W beats with no valid lane, from mxu.md 4.3: per pass NG = 4 groups x DIM/8 beats of 8 lanes."""
    D = dim
    bf16, trans, K, N = ins['bf16'], ins['trans'], ins['K'], ins['N']
    eb = 2 if bf16 else 1
    kte = (2 if bf16 else 4) * D
    KB = 4 * D
    LB = D
    GRP = D // 4
    KT, NT = -(-K // kte), -(-N // D)
    n = 0
    if ins['M'] == 0:
        return 0
    for j in range(NT):
        for t in range(KT):
            kb = min(KB, K * eb - t * KB)
            for g in range(4):
                for idx in range(D // 8):
                    if trans:
                        cols = [j * D + 8 * idx + l for l in range(8)]
                        v = [c < N and kb > g * LB for c in cols]
                    elif not bf16:
                        v = [t * kte + 8 * (g * GRP // 2 + idx) + l < K for l in range(8)]
                    else:
                        v = [t * kte + 4 * (g * GRP // 2 + idx) + l // 2 < K for l in range(8)]
                    n += not any(v)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dim', type=int, nargs='+', default=[8, 16])
    args = ap.parse_args()
    hung = ok = 0
    for dim in args.dim:
        vvp = RR.build(dim)
        for p in RR.cases(dim, 0):
            nempty = sum(empty_w_beats(i, dim) for i in p.ins)
            errs, d = RR.run_case(vvp, p, 'serial', 0, seed=3, rdyv=1, maxcyc=30000, sub='rdyv')
            to = [e for e in errs if 'TIMEOUT' in e]
            tag = 'DIM %2d %-22s empty W beats %4d' % (dim, p.name, nempty)
            if to:
                hung += 1
                print('HANG', tag, to[0])
            elif errs:
                print('FAIL', tag, errs[0])
            else:
                ok += 1
                print('ok  ', tag)
            if (nempty > 0) != bool(to):
                print('     ^ the count of empty beats does not predict the hang')
            sys.stdout.flush()
    print('%d hang, %d complete' % (hung, ok))


if __name__ == '__main__':
    main()
