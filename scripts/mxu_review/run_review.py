#!/usr/bin/env python3
"""Review part R: MXU_MM bit-exact against an independent oracle (docs/reports/mxu_review.md).

Builds programs, predicts them with oracle.py (no arith_ref.py, no numerics.py, no gen_mxu_vectors.py), runs the review's own
testbench tb_mxu_rev.sv on the RTL at DIM 8 and 16, and compares:
  * the ACC input stream row by row (row, ncols, rescale / overwrite / use_cs, last, rowscale, column scales of the columns < ncols,
    partial sums of the columns < ncols), bit for bit, NaN payloads and signed zeros included;
  * the ACC image after the oracle's ACC lane is applied to the RTL's stream, and the testbench's ACC image (acc_update RTL lanes),
    both against the oracle's image (every word of every row, untouched words included);
  * fp_flags: the OR of the cycles in which an instruction runs (serial runs: per instruction) against the OR of the PE flags the
    oracle predicts for rows m < M and columns n < N only; the ACC-lane flags likewise;
  * the log: no ACC row updated twice within ACC_LAT cycles, no lane outside the SPM, no fault, no timeout, one sem_inc per instruction.
Usage: run_review.py [--dim 8 16] [--only SUBSTR] [--fuzz N] [--keep]
"""
import argparse
import re
import os
import random
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
import oracle as O  # noqa: E402

BUILD = os.path.join(ROOT, 'build', 'mxu_review')
SPM_BYTES = 65536
ACC_ROWS = {8: 256, 16: 512}
ACC_LAT = 8
OP_MXU_MM = 0x60
DT_BF16, DT_E4M3, SP_SPM = 1, 3, 1


def build(dim, rtl_root=ROOT, tag='', defines=()):
    out = os.path.join(BUILD, 'tb_mxu_rev_%d%s.vvp' % (dim, ('_' + tag) if tag else ''))
    srcs = [os.path.join(HERE, 'tb_mxu_rev.sv'), os.path.join(ROOT, 'isa/gen/isa_pkg.sv')]
    for d in ('rtl/core', 'rtl/arith'):
        srcs += sorted(os.path.join(rtl_root, d, f) for f in os.listdir(os.path.join(rtl_root, d)) if f.endswith('.sv'))
    cmd = ['iverilog', '-g2012'] + ['-D' + d for d in defines] + ['-P', 'tb_mxu_rev.DIM=%d' % dim, '-P', 'tb_mxu_rev.ACC_ROWS=%d' % ACC_ROWS[dim],
           '-P', 'tb_mxu_rev.SPM_BYTES=%d' % SPM_BYTES, '-o', out] + srcs
    r = subprocess.run(cmd, capture_output=True, text=True)
    err = '\n'.join(l for l in r.stderr.splitlines() if 'constant selects' not in l)
    if r.returncode:
        sys.exit('compile failed:\n' + err)
    return out


# ---------------------------------------------------------------- value generators
def fp8_any(r):
    while True:
        c = r.randrange(256)
        if c & 0x7F != 0x7F:
            return c


def fp8_mix(r):
    x = r.random()
    if x < 0.06:
        return r.choice([0x00, 0x80])
    if x < 0.12:
        return r.randrange(1, 8) | r.choice([0, 0x80])          # subnormal
    if x < 0.15:
        return r.choice([0x7E, 0xFE])                            # +-448
    return fp8_any(r)


def bf16_norm(r, lo=-12, hi=12):
    return (r.randrange(2) << 15) | ((127 + r.randint(lo, hi)) << 7) | r.randrange(128)


def bf16_mix(r):
    x = r.random()
    if x < 0.05:
        return r.choice([0x0000, 0x8000])
    if x < 0.08:
        return r.randrange(1, 128) | r.choice([0, 0x8000])     # subnormal: DAZ
    if x < 0.12:
        return bf16_norm(r, -126, -100)                          # tiny normals: FTZ of products
    return bf16_norm(r)


def f32_norm(r, lo=-8, hi=8):
    return (r.randrange(2) << 31) | ((127 + r.randint(lo, hi)) << 23) | r.getrandbits(23)


def f32_mix(r):
    x = r.random()
    if x < 0.03:
        return r.choice([0x7FC00000, 0x7FA00001, 0xFFC12345, 0x7F800001])   # NaNs with payloads
    if x < 0.05:
        return r.choice([0x7F800000, 0xFF800000])
    if x < 0.09:
        return r.choice([0x00000000, 0x80000000])
    if x < 0.12:
        return r.randrange(1, 1 << 23) | r.choice([0, 1 << 31])            # subnormal: DAZ
    return f32_norm(r)


def cs_pow2(r):
    return (127 + r.randint(-6, 6)) << 23


# ---------------------------------------------------------------- a program
class Prog:
    def __init__(self, name, dim, seed):
        self.name, self.dim = name, dim
        self.r = random.Random(seed)
        r = self.r
        pat = [0x7F, 0xFF, 0x7F, 0xFF, 0x80]
        self.spm = bytearray(r.choice(pat) if r.random() < 0.8 else r.randrange(256) for _ in range(SPM_BYTES))
        self.dtab = {}
        self.ntd = 1
        self.ins = []
        self.acc = {row: [f32_mix(r) for _ in range(dim)] for row in range(ACC_ROWS[dim])}
        self.free = 0
        self.serial = False

    def alloc(self, nbytes, gap=None):
        gap = self.r.choice([0, 0, 64, 192]) if gap is None else gap
        base = (self.free + gap + 63) // 64 * 64
        self.free = base + nbytes
        assert self.free <= SPM_BYTES, 'SPM full'
        return base

    def put(self, addr, val, nb):
        self.spm[addr:addr + nb] = val.to_bytes(nb, 'little')

    def td(self, dtype, n0, n1, s1):
        idx = self.ntd
        self.ntd += 1
        junk = self.r.getrandbits(144) << 112
        self.dtab[idx] = junk | dtype | (SP_SPM << 5) | (n0 << 16) | (n1 << 32) | (1 << 48) | (1 << 64) | ((s1 & 0xFFFFFFFF) << 80)
        return idx

    def mm(self, bf16, trans, K, N, M, accm=0, acc_base=0, cs=False, a_s1=None, w_s1=None, m_reg=None, decoy=None,
           af=None, wf=None, csf=None, rsf=None, a_at=None, w_at=None, cs_at=None, rs_at=None, acc_reg=3):
        r, dim = self.r, self.dim
        eb = 2 if bf16 else 1
        af = af or ((lambda m, k: bf16_mix(r)) if bf16 else (lambda m, k: fp8_mix(r)))
        wf = wf or ((lambda k, n: bf16_mix(r)) if bf16 else (lambda k, n: fp8_mix(r)))
        csf = csf or (lambda t, n: cs_pow2(r) if r.random() < 0.7 else f32_norm(r, -3, 3))
        rsf = rsf or (lambda m: f32_norm(r, -2, 2) if r.random() < 0.9 else f32_mix(r))
        row_a = -(-K * eb // 64) * 64
        a_s1 = row_a if a_s1 is None else a_s1
        if trans:
            row_w = -(-K * eb // 64) * 64
            wrows, wlen = N, K * eb
        else:
            row_w = -(-N * eb // 64) * 64
            wrows, wlen = K, N * eb
        w_s1 = row_w if w_s1 is None else w_s1
        if M > 0:
            a_base = a_at if a_at is not None else self.alloc((M - 1) * a_s1 + K * eb)
            for m in range(M):
                for k in range(K):
                    self.put(a_base + m * a_s1 + k * eb, af(m, k), eb)
        else:
            a_base = a_at if a_at is not None else self.alloc(64)
        w_base = w_at if w_at is not None else self.alloc((wrows - 1) * w_s1 + wlen)
        for k in range(K):
            for n in range(N):
                addr = w_base + (n * w_s1 + k * eb if trans else k * w_s1 + n * eb)
                self.put(addr, wf(k, n), eb)
        cs_base = rs_base = None
        if cs:
            ktf = -(-K // (128 if bf16 else 256))
            csp = -(-4 * N // 64) * 64
            cs_base = cs_at if cs_at is not None else self.alloc((ktf - 1) * csp + 4 * N)
            for t in range(ktf):
                for n in range(N):
                    self.put(cs_base + t * csp + 4 * n, csf(t, n), 4)
        if accm == 2:
            rs_base = rs_at if rs_at is not None else self.alloc(max(4 * M, 4))
            for m in range(M):
                self.put(rs_base + 4 * m, rsf(m), 4)
        # only legal programs (isa_ref MXU_MM): ACC range, 64 B alignment, operand extents inside the SPM
        assert acc_base + -(-N // dim) * M <= ACC_ROWS[dim], ('ACC range', self.name)
        for x in (a_base, w_base, a_s1, w_s1, cs_base or 0, rs_base or 0):
            assert x % 64 == 0 and x >= 0, ('alignment', self.name)
        ext = [w_base + ((N - 1) * w_s1 + K * eb if trans else (K - 1) * w_s1 + N * eb)]
        if M:
            ext.append(a_base + (M - 1) * a_s1 + K * eb)
        if cs_base is not None:
            ext.append(cs_base + (-(-K // (128 if bf16 else 256)) - 1) * (-(-4 * N // 64) * 64) + 4 * N)
        if rs_base is not None and M:
            ext.append(rs_base + 4 * M)
        assert max(ext) <= SPM_BYTES, ('SPM range', self.name)
        dt = DT_BF16 if bf16 else DT_E4M3
        n1 = decoy if (m_reg is not None and decoy is not None) else M
        a_td = self.td(dt, K, n1, a_s1)
        w_td = self.td(dt, K, N, w_s1) if trans else self.td(dt, N, K, w_s1)
        self.ins.append(dict(bf16=bf16, trans=trans, K=K, N=N, M=M, a_base=a_base, a_s1=a_s1, w_base=w_base, w_s1=w_s1,
                             acc_base=acc_base, accm=accm, cs_base=cs_base, rs_base=rs_base,
                             a_td=a_td, w_td=w_td, m_reg=m_reg, acc_reg=acc_reg))
        return self.ins[-1]

    def encode(self, k, ins):
        f = dict(a_reg=1, w_reg=2, acc_reg=ins['acc_reg'], m_reg=4 if ins['m_reg'] is not None else 0,
                 rs_reg=5 if ins['accm'] == 2 else 0, cs_reg=6 if ins['cs_base'] is not None else 0)
        wsem = 1 if (self.serial and k > 0) else 0
        w = (OP_MXU_MM << 120) | (wsem << 114) | (1 << 95) | (1 << 91)
        w |= (f['a_reg'] << 86) | (ins['a_td'] << 78) | (f['w_reg'] << 73) | (ins['w_td'] << 65) | (f['acc_reg'] << 60)
        w |= (f['m_reg'] << 55) | (f['rs_reg'] << 50) | (f['cs_reg'] << 45) | (ins['accm'] << 43) | (ins['trans'] << 39)
        regs = [ins['a_base'], ins['w_base'], ins['acc_base'] if f['acc_reg'] else 0,
                ins['M'] if f['m_reg'] else 0, ins['rs_base'] or 0, ins['cs_base'] or 0]
        if f['acc_reg'] == 0:
            assert ins['acc_base'] == 0
        rv = 0
        for i, v in enumerate(regs):
            rv |= (v & (2**64 - 1)) << (64 * i)
        wval = k if wsem else 0
        return (wval << 512) | (rv << 128) | w

    def write(self, d):
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'spm.hex'), 'w') as f:
            f.write('\n'.join('%02x' % b for b in self.spm) + '\n')
        with open(os.path.join(d, 'dtab.hex'), 'w') as f:
            f.write('\n'.join('%064x' % self.dtab.get(i, 0) for i in range(256)) + '\n')
        with open(os.path.join(d, 'prog.hex'), 'w') as f:
            f.write('\n'.join('%0136x' % self.encode(k, ins) for k, ins in enumerate(self.ins)) + '\n')
        with open(os.path.join(d, 'acc.hex'), 'w') as f:
            f.write('\n'.join('%08x' % w for row in range(ACC_ROWS[self.dim]) for w in self.acc[row]) + '\n')


# ---------------------------------------------------------------- run and compare
def predict(p):
    acc = {k: list(v) for k, v in p.acc.items()}
    per_ins = []
    for ins in p.ins:
        st, pf, af = O.mxu_mm(p.spm, ins, acc, p.dim)
        per_ins.append((st, pf, af))
    return acc, per_ins


def hx(v):
    return -1 if re.search('[xXzZ]', v) else int(v, 16)


def parse_stream(path, dim):
    rows = []
    for line in open(path):
        x = line.split()
        if not x or x[0] != 'R':
            continue
        cyc, row, ncols, resc, ovw, ucs, last = (int(v) for v in x[1:8])
        rs = hx(x[8])
        cs = [hx(v) for v in x[9:9 + dim]]
        part = [hx(v) for v in x[9 + dim:9 + 2 * dim]]
        rows.append(dict(cyc=cyc, row=row, ncols=ncols, rescale=bool(resc), overwrite=bool(ovw), use_cs=bool(ucs),
                         last=bool(last), rowscale=rs, cs=cs, part=part))
    return rows


def h(x):
    return 'XXXXXXXX' if x < 0 else '%08x' % x


def compare(p, d, acc_exp, per_ins, mode):
    errs = []
    log = [l.split() for l in open(os.path.join(d, 'rtl_log.txt'))]
    for l in log:
        if l[0] in ('HAZARD', 'OOB', 'TIMEOUT', 'FAULT', 'DONEDLY'):
            errs.append('log: ' + ' '.join(l))
    sems = [l for l in log if l[0] == 'SEM']
    if len(sems) != len(p.ins):
        errs.append('sem_inc count %d, want %d' % (len(sems), len(p.ins)))
    got = parse_stream(os.path.join(d, 'rtl_stream.txt'), p.dim)
    exp = [row for st, _, _ in per_ins for row in st]
    if len(got) != len(exp):
        errs.append('stream rows %d, want %d' % (len(got), len(exp)))
    for i, (g, e) in enumerate(zip(got, exp)):
        bad = []
        for k in ('row', 'ncols', 'rescale', 'overwrite', 'use_cs', 'last'):
            if g[k] != e[k]:
                bad.append('%s %s want %s' % (k, g[k], e[k]))
        if e['rescale'] and g['rowscale'] != e['rowscale']:
            bad.append('rowscale %s want %s' % (h(g['rowscale']), h(e['rowscale'])))
        for c in range(min(e['ncols'], p.dim)):
            if e['use_cs'] and g['cs'][c] != e['cs'][c]:
                bad.append('cs[%d] %s want %s' % (c, h(g['cs'][c]), h(e['cs'][c])))
            if g['part'][c] != e['part'][c]:
                bad.append('part[%d] %s want %s' % (c, h(g['part'][c]), h(e['part'][c])))
        if bad:
            errs.append('stream row %d (acc row %d, cycle %d): %s' % (i, e['row'], g['cyc'], '; '.join(bad[:6])))
            if len(errs) > 25:
                break
    # ACC image: oracle lane on the RTL stream, and the testbench's acc_update image
    acc_l = {k: list(v) for k, v in p.acc.items()}
    for g in got:
        if g['row'] in acc_l:
            for c in range(min(g['ncols'], p.dim)):
                acc_l[g['row']][c], _ = O.acc_lane(acc_l[g['row']][c], g['part'][c] & 0xFFFFFFFF,
                                                   g['rowscale'] & 0xFFFFFFFF, g['cs'][c] & 0xFFFFFFFF,
                                                   g['rescale'], g['use_cs'], g['overwrite'])
    words = [hx(l.strip()) for l in open(os.path.join(d, 'rtl_acc.hex')) if l.strip() and not l.startswith('//')]
    nbad_l = nbad_t = 0
    for row in range(ACC_ROWS[p.dim]):
        for c in range(p.dim):
            e = acc_exp[row][c]
            if acc_l[row][c] != e:
                nbad_l += 1
                if nbad_l <= 5:
                    errs.append('ACC(oracle lane on RTL stream) row %d col %d %s want %s' % (row, c, h(acc_l[row][c]), h(e)))
            t = words[row * p.dim + c]
            if t != e:
                nbad_t += 1
                if nbad_t <= 5:
                    errs.append('ACC(tb acc_update) row %d col %d %s want %s' % (row, c, h(t), h(e)))
    if nbad_l or nbad_t:
        errs.append('ACC words wrong: %d (oracle lane), %d (acc_update)' % (nbad_l, nbad_t))
    # flags
    fp = {l[0]: int(l[1], 16) for l in log if l[0] in ('FPFLAGS', 'ACCFLAGS')}
    want_pe = want_acc = 0
    for _, pf, af in per_ins:
        want_pe |= pf
        want_acc |= af
    if fp.get('FPFLAGS', -1) != want_pe:
        errs.append('fp_flags OR %x want %x' % (fp.get('FPFLAGS', -1), want_pe))
    if fp.get('ACCFLAGS', -1) != want_acc:
        errs.append('ACC lane flags OR %x want %x' % (fp.get('ACCFLAGS', -1), want_acc))
    if p.serial:
        pops = {int(l[2]): int(l[1]) for l in log if l[0] == 'POP'}
        semc = [int(l[1]) for l in sems]
        for k, (_, pf, _) in enumerate(per_ins):
            if k not in pops or k >= len(semc):
                continue
            lo, hi = pops[k], semc[k]
            fl = 0
            for l in log:
                if l[0] == 'FLAGS' and lo <= int(l[1]) <= hi:
                    fl |= int(l[2], 16)
            if fl != pf:
                errs.append('fp_flags of instruction %d: %x want %x' % (k, fl, pf))
    return errs


def run_case(vvp, p, mode, stall, seed, rdyv=0, keep=False, maxcyc=200000, sub='cases', rmw=0, xfill=0):
    d = os.path.join(BUILD, sub, str(p.dim), '%s_%s_s%d%s' % (p.name, mode, stall, '_rdyv' if rdyv else ''))
    if os.path.isdir(d):
        shutil.rmtree(d)
    p.serial = (mode == 'serial')
    p.write(d)
    acc_exp, per_ins = predict(p)
    cmd = ['vvp', '-n', vvp, '+DIR=' + d, '+NI=%d' % len(p.ins), '+STALL=%d' % stall, '+SEED=%d' % seed,
           '+RDYV=%d' % rdyv, '+MAXCYC=%d' % maxcyc, '+RMW=%d' % rmw, '+XFILL=%d' % xfill]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode or 'Unable to open' in r.stdout or 'Not enough words' in r.stdout:
        return ['vvp: ' + r.stdout[-400:] + r.stderr[-400:]], d
    errs = compare(p, d, acc_exp, per_ins, mode)
    with open(os.path.join(d, 'rtl_log.txt')) as f:            # X monitor of the testbench (implementability review)
        errs += [l.strip() for l in f if l.startswith('XPROP')][:10]
    if not errs and not keep:
        shutil.rmtree(d)
    return errs, d


# ---------------------------------------------------------------- cases
def sz_bf16(dim, K):
    """BF16 values that make a partial sum -0 in PE row dim-2 (FTZ of a cancellation) and give -0 products in row dim-1."""
    def a(m, k):
        if k == 2 * (dim - 3):
            return 0xA040            # -1.5 * 2^-63
        if k == 2 * (dim - 2):
            return 0x2020            # +1.25 * 2^-63
        return 0x8000                # -0
    def w(k, n):
        if k in (2 * (dim - 3), 2 * (dim - 2)):
            return 0x2000            # 2^-63
        return 0x3F80                # 1.0
    return a, w


def cases(dim, nfuzz):
    D = dim
    out = []

    def P(name, seed):
        p = Prog(name, dim, seed)
        out.append(p)
        return p

    # ---- shapes the author did not use
    p = P('k1_fp8_trans', 1);        p.mm(False, 1, K=1, N=5, M=3, accm=0, acc_base=0)
    p = P('k1_bf16_nontrans', 2);    p.mm(True, 0, K=1, N=1, M=1, accm=1, acc_base=7)
    p = P('n1_fp8_nontrans', 3);     p.mm(False, 0, K=70, N=1, M=4, accm=1, acc_base=3)
    p = P('n1_bf16_trans', 4);       p.mm(True, 1, K=37, N=1, M=2, accm=2, acc_base=11)
    p = P('m_dimp1_fp8_ktile', 5);   p.mm(False, 1, K=4 * D, N=2 * D, M=D + 1, accm=2, acc_base=1, cs=True)
    p = P('k2tiles_bf16', 6);        p.mm(True, 1, K=2 * 2 * D, N=D, M=3, accm=0, acc_base=0, cs=True)
    p = P('k2tiles_fp8_nt', 7);      p.mm(False, 0, K=2 * 4 * D, N=D + 1, M=D + 1, accm=1, acc_base=5)
    p = P('bf16_nt_ragged', 8);      p.mm(True, 0, K=2 * D + 1, N=D + 3, M=5, accm=0, acc_base=2)
    p = P('bf16_nt_ragged2', 9);     p.mm(True, 0, K=3, N=2 * D - 1, M=D + 1, accm=1, acc_base=0, cs=True)
    p = P('bf16_nt_n_half', 10);     p.mm(True, 0, K=2 * D + 3, N=D // 2 + 1, M=2, accm=0, acc_base=0)
    p = P('fp8_nt_ragged', 11);      p.mm(False, 0, K=4 * D + 5, N=D + 1, M=2, accm=2, acc_base=9)
    p = P('pitch_pad', 12)
    p.mm(False, 1, K=70, N=D + 2, M=3, accm=0, acc_base=0, a_s1=128 + 192, w_s1=128 + 128)
    p.mm(True, 0, K=9, N=D + 5, M=4, accm=1, acc_base=0, a_s1=64 * 5, w_s1=64 * 3)
    p = P('pitch_zero', 13)
    p.mm(False, 1, K=40, N=3, M=4, accm=0, acc_base=0, a_s1=0, w_s1=0)
    p.mm(True, 0, K=7, N=D + 1, M=3, accm=1, acc_base=0, a_s1=0, w_s1=0)
    p = P('pitch_overlap', 14)
    p.mm(False, 1, K=200, N=D + 1, M=3, accm=0, acc_base=0, a_s1=64, w_s1=64)
    p.mm(True, 0, K=5, N=40, M=2, accm=1, acc_base=0, w_s1=64)
    p = P('mreg_override', 15)
    p.mm(False, 1, K=33, N=D, M=5, accm=0, acc_base=0, m_reg=True, decoy=37)
    p.mm(True, 1, K=20, N=D, M=0, accm=1, acc_base=0, m_reg=True, decoy=3)
    p.mm(True, 1, K=20, N=D, M=5, accm=1, acc_base=0, m_reg=True, decoy=1)
    p = P('chain_same_rows', 16)
    p.mm(False, 1, K=70, N=2 * D, M=4, accm=0, acc_base=10, cs=True)
    p.mm(True, 0, K=2 * D + 1, N=2 * D - 3, M=4, accm=1, acc_base=10)
    p.mm(False, 0, K=13, N=D + 2, M=4, accm=2, acc_base=10, cs=True)
    p.mm(True, 1, K=5, N=D + 1, M=6, accm=1, acc_base=8)
    p.mm(False, 1, K=3, N=D, M=0, accm=0, acc_base=10, m_reg=True, decoy=4)
    p.mm(False, 1, K=300, N=2 * D, M=4, accm=1, acc_base=10, cs=True)
    p = P('cs_fold_fp8', 17)
    p.mm(False, 1, K=300, N=13, M=3, accm=0, acc_base=0, cs=True,
         csf=lambda t, n: (127 + (t + 1) * 2 + (n % 5)) << 23 | (n << 10))
    p = P('cs_fold_fp8_nt', 18)
    p.mm(False, 0, K=513, N=D + 3, M=2, accm=1, acc_base=0, cs=True, csf=lambda t, n: (127 - t * 3 + n % 3) << 23)
    p = P('cs_fold_bf16', 19)
    p.mm(True, 1, K=131, N=40, M=2, accm=0, acc_base=0, cs=True, csf=lambda t, n: (127 + 4 * t - (n % 7)) << 23 | 0x1234)
    p.mm(True, 0, K=129, N=21, M=3, accm=1, acc_base=10, cs=True, csf=lambda t, n: (126 + t + (n % 3)) << 23)
    p = P('end_of_spm', 20)
    K, N, M = 200, 13, 2
    p.free = SPM_BYTES - 4096
    ins = p.mm(False, 1, K=K, N=N, M=M, accm=0, acc_base=0, cs=True,
               cs_at=SPM_BYTES - 64 - (4 * N + 63) // 64 * 64 + 64 - 64,
               a_at=SPM_BYTES - 1024 - 64 * 8, w_at=SPM_BYTES - 1024 * 3 - 256 * N)
    p = P('acc_top_reg0', 21)
    NT = 2
    p.mm(False, 1, K=10, N=2 * D, M=3, accm=0, acc_base=0, acc_reg=0)
    p.mm(True, 1, K=10, N=2 * D, M=3, accm=1, acc_base=ACC_ROWS[dim] - NT * 3)
    # ---- special values
    nanpos = {(1, 3), (0, 4 * D + 1)}
    p = P('fp8_nan_in', 22)
    p.mm(False, 1, K=4 * D + 4, N=D + 2, M=3, accm=0, acc_base=0,
         af=lambda m, k: 0x7F if (m, k) in nanpos else fp8_mix(p.r))
    p.mm(False, 0, K=9, N=5, M=2, accm=1, acc_base=20, wf=lambda k, n: 0xFF if (k, n) == (8, 4) else fp8_mix(p.r))
    p.mm(False, 1, K=9, N=5, M=2, accm=1, acc_base=30)
    pos = lambda: bf16_norm(p.r, -3, 3) & 0x7FFF
    p = P('bf16_colmask', 23)          # Inf in A, every in-range W positive: in range +Inf without a flag, pad columns Inf x 0
    p.mm(True, 0, K=5, N=D + 3, M=2, accm=0, acc_base=0,
         af=lambda m, k: 0x7F80 if (m, k) == (0, 0) else pos(), wf=lambda k, n: pos())
    p.mm(True, 1, K=2 * D + 1, N=3, M=2, accm=0, acc_base=40,
         af=lambda m, k: 0x7F80 if (m, k) == (1, 2 * D) else pos(), wf=lambda k, n: pos())
    p.mm(True, 1, K=3, N=1, M=1, accm=1, acc_base=60, af=lambda m, k: 0xFF80 if k == 1 else pos(), wf=lambda k, n: pos())
    for i, (nm, af, wf, K, N) in enumerate([
        ('dot_inf_x_0', lambda m, k: 0x7F80 if (m, k) == (0, 0) else pos(), lambda k, n: 0 if (k, n) == (0, 1) else pos(), 4, 3),
        ('dot_inf_m_inf', lambda m, k: 0x7F80 if k < 2 else pos(), lambda k, n: 0xBF80 if k == 1 else 0x3F80, 4, 2),
        ('dot_ovf', lambda m, k: 0x7F7F if k == 0 else 0, lambda k, n: 0x7F7F if k == 0 else 0x3F80, 4, 2),
        ('add_ovf', lambda m, k: 0x7F40 if k % 2 == 0 else 0, lambda k, n: 0x3F80, 6, 2),
        ('add_inf_m_inf', lambda m, k: 0x7F80 if k in (0, 2) else 0, lambda k, n: 0xBF80 if k == 2 else 0x3F80, 4, 2),
        ('nan_payload', lambda m, k: 0x7F81 if k == 1 else (0xFFC1 if k == 2 else pos()), lambda k, n: pos(), 5, 3),
        ('sub_x_inf', lambda m, k: 0x0001 if k == 0 else pos(), lambda k, n: 0x7F80 if k == 0 else pos(), 3, 2),
        ('sub_daz', lambda m, k: 0x8003 if k % 2 else 0x0042, lambda k, n: 0x7F00, 6, 2),
        ('tiny_ftz', lambda m, k: 0x0080 + k, lambda k, n: 0x8080 + n, 6, 2),
    ]):
        p = P('bf16_' + nm, 30 + i)
        p.mm(True, 1, K=K, N=N, M=2, accm=0, acc_base=0, af=af, wf=wf)
    p = P('bf16_signed_zero', 40)
    for K, accm, base in ((2 * D, 0, 0), (2 * D - 1, 0, 8), (2 * D, 1, 16), (4 * D, 0, 24), (4 * D - 1, 1, 32)):
        a, w = sz_bf16(D, K)
        p.mm(True, 1, K=K, N=3, M=2, accm=accm, acc_base=base, af=lambda m, k, a=a: a(m, k % (2 * D)),
             wf=lambda k, n, w=w: w(k % (2 * D), n))
    for r0 in range(16, 40):
        p.acc[r0] = [0x80000000] * D
    p = P('fp8_signed_zero_cs', 41)
    for accm, sgn in ((0, 0x80), (1, 0x80), (0, 0x00), (1, 0x00)):
        p.mm(False, 1, K=1, N=2, M=2, accm=accm, acc_base=0 if accm == 0 else 0, cs=True,
             af=lambda m, k, s=sgn: 0x08 | s, wf=lambda k, n: 0x08, csf=lambda t, n: 0x03800000)
    p = P('acc_lane_specials', 42)
    p.mm(False, 1, K=20, N=D, M=6, accm=2, acc_base=0, cs=True, csf=lambda t, n: f32_mix(p.r),
         rsf=lambda m: [0x7FC12345, 0x00000001, 0x7F800000, 0x80000000, 0x00000000, 0x3F800000][m])
    p.mm(True, 1, K=20, N=D, M=6, accm=1, acc_base=0, cs=True, csf=lambda t, n: [0x7F800000, 0, 0x7FA00001, 0x00000005,
                                                                               0x80000000, 0x7F7FFFFF][n % 6])
    p.mm(False, 0, K=8, N=D, M=6, accm=0, acc_base=20, cs=True, csf=lambda t, n: 0x7F7FFFFF)
    p = P('rescale_bigM', 43)
    p.mm(False, 1, K=40, N=2 * D + 1, M=37, accm=2, acc_base=0, cs=True)
    p.mm(True, 0, K=2 * D + 3, N=D + 1, M=70, accm=2, acc_base=100)
    p = P('m1_chain_same_row', 44)
    for i in range(5):
        p.mm(i % 2 == 1, 1, K=3 * (2 if i % 2 else 4) * D + 1, N=D, M=1, accm=0 if i == 0 else 1, acc_base=5, cs=(i == 3))
    p = P('acc_spacing_x_ins', 46)     # ACCUM, then ACCUM of a shorter M whose row 0 is the previous pass's row M-1
    p.mm(False, 1, K=10, N=D, M=3, accm=1, acc_base=0)
    p.mm(False, 1, K=10, N=D, M=1, accm=1, acc_base=2)
    p.mm(False, 1, K=10, N=D, M=9, accm=1, acc_base=20)
    p.mm(False, 1, K=10, N=D, M=1, accm=1, acc_base=25)
    p = P('m2_chain_rescale', 45)
    for i in range(4):
        p.mm(False, i % 2, K=4 * D + 1, N=D + 1, M=2, accm=2 if i else 0, acc_base=5)
    # ---- fuzz
    for i in range(nfuzz):
        r = random.Random(1000 + i * 7 + dim)
        p = P('fuzz%03d' % i, 1000 + i * 7 + dim)
        nins = r.choice([1, 1, 2, 3])
        base = r.randrange(0, 40)
        placed = tries = 0
        while placed < nins and tries < 200:
            tries += 1
            bf16 = r.random() < 0.5
            kte = (2 if bf16 else 4) * D
            K = r.choice([1, 2, 3, kte - 1, kte, kte + 1, 2 * kte, r.randint(1, 3 * kte + 3)])
            if not bf16 and r.random() < 0.2:
                K = r.choice([256, 257, 300, 511])
            if bf16 and r.random() < 0.2:
                K = r.choice([128, 129, 131])
            N = r.choice([1, 2, D - 1, D, D + 1, 2 * D - 1, r.randint(1, 2 * D + 5)])
            M = r.choice([0, 1, 2, D, D + 1, r.randint(1, D + 3)]) if r.random() < 0.95 else 0
            trans = r.randrange(2)
            accm = r.randrange(3)
            eb = 2 if bf16 else 1
            rowa = -(-K * eb // 64) * 64
            rw = -(-(K if trans else N) * eb // 64) * 64
            a_s1 = r.choice([rowa, rowa, rowa + 64 * r.randint(1, 3), 0, 64])
            w_s1 = r.choice([rw, rw, rw + 64 * r.randint(1, 3), 64, 0])
            use_cs = r.random() < 0.5
            mreg = r.random() < 0.3
            nt = -(-N // D)
            if nt * M > ACC_ROWS[dim] - 1:
                continue
            accb = r.randrange(0, max(1, min(base + 1, ACC_ROWS[dim] - nt * M)))
            ktf = -(-K // (128 if bf16 else 256))
            need = (max(M - 1, 0) * a_s1 + K * eb) + ((N if trans else K) - 1) * w_s1 + (K if trans else N) * eb
            need += ((ktf - 1) * (-(-4 * N // 64) * 64) + 4 * N if use_cs else 0) + 4 * M + 6 * 256
            if p.free + need > SPM_BYTES - 256:
                continue
            p.mm(bf16, trans, K=K, N=N, M=M, accm=accm, acc_base=accb, cs=use_cs, a_s1=a_s1, w_s1=w_s1,
                 m_reg=True if mreg else None, decoy=r.randint(1, 60) if mreg else None,
                 csf=(lambda t, n, r=r: f32_mix(r) if r.random() < 0.1 else cs_pow2(r)),
                 rsf=(lambda m, r=r: f32_mix(r)))
            placed += 1
        assert placed, p.name
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dim', type=int, nargs='+', default=[8, 16])
    ap.add_argument('--only', default='')
    ap.add_argument('--fuzz', type=int, default=40)
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('--modes', nargs='+', default=['serial', 'pipe'])
    ap.add_argument('--stalls', type=int, nargs='+', default=[0, 30])
    ap.add_argument('--oracle-pad', default='zero', choices=['zero', 'absent'])
    ap.add_argument('--oracle-zero', default='ieee', choices=['ieee', 'plus'])
    ap.add_argument('--xfill', action='store_true', help='ungranted lanes and idle DTAB data are X (implementability review)')
    args = ap.parse_args()
    O.PAD_MODE, O.ZERO_SIGN = args.oracle_pad, args.oracle_zero
    nfail = nrun = 0
    for dim in args.dim:
        vvp = build(dim)
        for p in cases(dim, args.fuzz):
            if args.only and args.only not in p.name:
                continue
            for mode in args.modes:
                for stall in args.stalls:
                    nrun += 1
                    errs, d = run_case(vvp, p, mode, stall, seed=nrun, keep=args.keep, xfill=int(args.xfill))
                    tag = 'DIM %2d %-22s %-6s stall %2d (%d ins)' % (dim, p.name, mode, stall, len(p.ins))
                    if errs:
                        nfail += 1
                        print('FAIL', tag, '->', d)
                        for e in errs[:12]:
                            print('   ', e)
                    else:
                        print('ok  ', tag)
                    sys.stdout.flush()
    print('%d runs, %d failed' % (nrun, nfail))
    return 1 if nfail else 0


if __name__ == '__main__':
    sys.exit(main())
