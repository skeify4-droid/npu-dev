#!/usr/bin/env python3
"""Review part H: control hazards and stalls of the MXU (docs/reports/mxu_review.md part H).

Random programs of MXU_MM instructions (different shapes back to back, M = 0, M from m_reg, OVERWRITE / ACCUM / RESCALE,
column scales, overlapping ACC row ranges, semaphore waits on earlier instructions and on other engines, an illegal
instruction followed by a reset), run on tb_mxu_hz.sv with independent random stalls of the three SPM ports, random MQ / DTAB
gaps, delayed acc_done, at several DIM / SPM_LAT / ACC_LAT / CS_DEPTH.

The data are exactly representable (A, W halves in [-8, 8], scales 0.5 / 1 / 2, ACC values multiples of 1/8), so every
partial sum and every ACC update is exact in FP32 whatever the order or rounding: the expected values come from plain
Python arithmetic, no arith_ref.py, numerics.py or gen_mxu_vectors.py. Every byte the MXU must not use is NaN (0xFF).

Checks (per segment; a segment cut by a reset or ending in a fault must be a prefix):
  * the ACC stream event by event: row, ncols, rescale / overwrite / use_cs / last, rowscale (rescale rows), column scales and
    partial sums of the columns < ncols, in the order of mxu.md 4.2;
  * the ACC row spacing: two updates of a row >= ACC_LAT cycles apart; and a timed ACC model (read at the acc_valid cycle,
    write visible ACC_LAT later) applied to the RTL's stream gives the expected image;
  * completion: sem_inc in program order, one per instruction (with ssem, sinc != 0), each at or after the cycle in which the
    last write of its instruction (an M = 0 instruction: of every earlier instruction) is visible; final semaphores;
  * waits: the semaphore condition holds at the take, and a wait on an earlier instruction's semaphore takes after its sem_inc;
  * faults: cause and FAULT_ADDR, no take and no ACC row of the faulting or a later instruction, in-flight instructions complete;
  * no hang (the run ends idle with everything done), no X on the control outputs, no fp_flags, no SPM lane outside / unaligned.
Usage: hz_run.py [--cfg NAME ...] [--seeds N] [--seed0 S] [--jobs J] [--rtl DIR] [--keep] [--quiet]
"""
import argparse
import os
import random
import struct
import subprocess
import sys
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
BUILD = os.path.join(ROOT, 'build', 'mxu_review_hz')
SPM_BYTES = 262144
SPM_BYTES_D = {8: 262144, 16: 262144, 32: 524288, 64: 1048576}
STUB = False
OP_MXU_MM = 0x60
DT_BF16, DT_E4M3, SP_SPM, SP_BAD = 1, 3, 1, 2
C_FIELD, C_SPACE, C_RANGE = 2, 3, 7

# name: DIM, SPM_LAT, ACC_LAT, CS_DEPTH
CFGS = {
    'd8_l2_a8': (8, 2, 8, 4),
    'd8_l1_a8': (8, 1, 8, 4),
    'd8_l1_a1': (8, 1, 1, 2),
    'd8_l1_a2': (8, 1, 2, 2),
    'd8_l3_a3': (8, 3, 3, 3),
    'd8_l5_a13': (8, 5, 13, 2),
    'd8_l2_a24': (8, 2, 24, 4),
    'd8_l1_a5': (8, 1, 5, 3),
    'd16_l2_a8': (16, 2, 8, 4),
    'd16_l1_a3': (16, 1, 3, 2),
    'd16_l4_a17': (16, 4, 17, 3),
    'd32_l2_a8': (32, 2, 8, 4),
    'd64_l2_a8': (64, 2, 8, 4),      # --stub only
    'd64_l1_a1': (64, 1, 1, 2),      # --stub only
    'd32_l1_a2': (32, 1, 2, 2),      # --stub
}


# ------------------------------------------------------------------------------------------------ number formats
def f32_bits(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


def bits_f32(b):
    return struct.unpack('<f', struct.pack('<I', b))[0]


def is_f32(x):
    try:
        return bits_f32(f32_bits(x)) == x and abs(x) < 3.0e38 and (x == 0 or abs(x) >= 2.0 ** -126)
    except OverflowError:
        return False


def enc_small(x, mbits, bias):
    """Exact encoding of a normal value with `mbits` mantissa bits (E4M3: 3 / 7, BF16: 7 / 127)."""
    if x == 0:
        return 0
    s = 1 if x < 0 else 0
    a = abs(x)
    e = 0
    while a >= 2.0:
        a /= 2
        e += 1
    while a < 1.0:
        a *= 2
        e -= 1
    m = (a - 1.0) * (1 << mbits)
    assert m == int(m), x
    eb = e + bias
    assert 1 <= eb, x
    ebits = 4 if mbits == 3 else 8
    return (s << (ebits + mbits)) | (eb << mbits) | int(m)


def enc_e4m3(x):
    c = enc_small(x, 3, 7)
    assert c & 0x7F != 0x7F
    return c


def enc_bf16(x):
    return enc_small(x, 7, 127)


VALS = [k / 2 for k in range(-16, 17)]


def val(r):
    if r.random() < 0.15:
        return 0.0
    if r.random() < 0.7:
        return r.choice([-2.0, -1.5, -1.0, -0.5, 0.5, 1.0, 1.5, 2.0])
    return r.choice(VALS)


def ceil(a, b):
    return -(-a // b)


def rup64(x):
    return ceil(x, 64) * 64


# ------------------------------------------------------------------------------------------------ program
class Seg:
    def __init__(self):
        self.ins = []
        self.ext = []         # (cycle, sem, amt)
        self.sem0 = [0] * 64
        self.acc0 = None


class Prog:
    def __init__(self, cfg, seed):
        self.name = '%s_s%d' % (cfg, seed)
        self.dim, self.spm_lat, self.acc_lat, self.cs_depth = CFGS[cfg]
        self.acc_rows = {8: 256, 16: 512, 32: 1024, 64: 2048}[self.dim]
        self.r = random.Random(seed * 7919 + zlib.crc32(cfg.encode()))
        self.spm_bytes = SPM_BYTES_D[self.dim]
        self.spm = bytearray(b'\xff' * self.spm_bytes)
        self.lo, self.hi = 0, self.spm_bytes
        self.dtab = {}
        self.ntd = 1
        self.segs = [Seg(), Seg()]
        self.knobs = {}

    # two-ended bump allocator
    def alloc(self, nbytes, top=False):
        nbytes = max(nbytes, 1)
        if top:
            base = (self.hi - nbytes) // 64 * 64
            if base < self.lo:
                return None
            self.hi = base
            return base
        base = rup64(self.lo + self.r.choice([0, 0, 0, 64, 128]))
        if base + nbytes > self.hi:
            return None
        self.lo = base + nbytes
        return base

    def put(self, addr, v, nb):
        self.spm[addr:addr + nb] = v.to_bytes(nb, 'little')

    def td(self, dt, n0, n1, s1, space=SP_SPM):
        idx = self.ntd
        assert idx < 256
        self.ntd += 1
        junk = self.r.getrandbits(144) << 112
        self.dtab[idx] = junk | dt | (space << 5) | (n0 << 16) | (n1 << 32) | (1 << 48) | (1 << 64) | ((s1 & 0xFFFFFFFF) << 80)
        return idx

    def shape(self, focus):
        r, D = self.r, self.dim
        bf16 = r.random() < 0.35
        kte = 2 * D if bf16 else 4 * D
        K = r.choice([1, 2, 3, kte - 1, kte, kte + 1, 2 * kte - 1, 2 * kte + 3, r.randint(1, 3 * kte), r.randint(1, kte)])
        if focus == 'cs_small_k':
            K = r.randint(1, kte - 1)
        if focus == 'rsfull':
            K = r.randint(1, kte)
            N = r.randint(2 * D + 1, 4 * D)
        N = r.choice([1, 2, 7, 8, D - 1, D, D + 1, 2 * D + 3, r.randint(1, 2 * D + D // 2), r.randint(1, D)])
        L = self.acc_lat
        G = D // 4
        x = r.random()
        if x < 0.12:
            M = 0
        elif x < 0.45:
            M = r.choice([1, 1, 2, 3])
        elif x < 0.75:
            M = max(1, r.choice([L - 2, L - 1, L, L + 1, G - 1, G, G + 1, 2 * G + 1]))
        elif x < 0.93:
            M = r.randint(1, 3 * D)
        elif D >= 32:
            M = r.randint(1, 2 * D)
        else:
            M = r.randint(3 * D, 6 * D)      # long passes: the loader runs ahead
        if focus == 'rsfull':
            M = r.randint(G + 1, max(G + 2, min(3 * G, D)))
        return bf16, r.random() < 0.5, K, N, M

    def add(self, si, prev, focus=None, bad=None):
        r, D = self.r, self.dim
        bf16, trans, K, N, M = self.shape(focus)
        eb = 2 if bf16 else 1
        NT = ceil(N, D)
        while NT * M > self.acc_rows:
            M //= 2
        accm = r.choice([0, 0, 1, 1, 1, 2, 2])
        cs = r.random() < 0.45 or focus == 'cs_small_k'
        if focus in ('rescale', 'rsfull'):
            accm = 2
        rows = NT * M
        if prev is not None and r.random() < 0.7:
            pb, pr = prev
            if r.random() < 0.35:
                base = pb + pr - 1 - r.choice([0, 0, 0, 1, 2])  # the first row on (or next to) the last row of the previous one (R-1)
            else:
                base = pb + r.randint(-max(rows, 1), max(pr, 1))
        else:
            base = r.randint(0, self.acc_rows)
        base = max(0, min(base, self.acc_rows - rows))
        a_s1 = rup64(K * eb) + r.choice([0, 0, 0, 64, 128])
        if trans:
            w_s1 = rup64(K * eb) + r.choice([0, 0, 64])
            wrows, wlen = N, K * eb
        else:
            w_s1 = rup64(N * eb) + r.choice([0, 0, 64])
            wrows, wlen = K, N * eb
        top = r.random() < 0.15
        A = [[val(r) for _ in range(K)] for _ in range(M)]
        W = [[val(r) for _ in range(N)] for _ in range(K)]     # logical [K][N]
        enc = enc_bf16 if bf16 else enc_e4m3
        if M > 0:
            a_base = self.alloc((M - 1) * a_s1 + K * eb, top)
        else:
            a_base = self.alloc(64)
        w_base = self.alloc((wrows - 1) * w_s1 + wlen, top and r.random() < 0.5)
        if a_base is None or w_base is None:
            return None
        for m in range(M):
            for k in range(K):
                self.put(a_base + m * a_s1 + k * eb, enc(A[m][k]), eb)
        for k in range(K):
            for n in range(N):
                self.put(w_base + (n * w_s1 + k * eb if trans else k * w_s1 + n * eb), enc(W[k][n]), eb)
        CS = None
        cs_base = rs_base = 0
        if cs:
            ktf = ceil(K, 128 if bf16 else 256)
            csp = rup64(4 * N)
            cs_base = self.alloc((ktf - 1) * csp + 4 * N, top and r.random() < 0.5)
            if cs_base is None:
                return None
            CS = [[r.choice([0.5, 1.0, 1.0, 2.0]) for _ in range(N)] for _ in range(ktf)]
            for t in range(ktf):
                for n in range(N):
                    self.put(cs_base + t * csp + 4 * n, f32_bits(CS[t][n]), 4)
        RS = None
        if accm == 2:
            rs_base = self.alloc(max(4 * M, 4), top and r.random() < 0.5)
            if rs_base is None:
                return None
            RS = [r.choice([0.5, 1.0, 1.0, 2.0]) for _ in range(M)]
            for m in range(M):
                self.put(rs_base + 4 * m, f32_bits(RS[m]), 4)
        m_reg = r.random() < 0.3
        dt = DT_BF16 if bf16 else DT_E4M3
        n1 = r.randint(0, 3 * D) if m_reg else M
        a_td = self.td(dt, K, n1, a_s1)
        w_td = self.td(dt, K, N, w_s1) if trans else self.td(dt, N, K, w_s1)
        ins = dict(bf16=bf16, trans=trans, K=K, N=N, M=M, A=A, W=W, CS=CS, RS=RS, accm=accm, cs=cs, acc_base=base,
                   a_base=a_base, w_base=w_base, a_s1=a_s1, w_s1=w_s1, cs_base=cs_base, rs_base=rs_base, m_reg=m_reg,
                   a_td=a_td, w_td=w_td, ssem=0, sinc=0, wsem=0, wval=0, wait_on=None, bad=None, cause=None, faddr=None)
        # semaphores: instructions use 1..47, other engines 48..63
        if r.random() < 0.9:
            ins['ssem'] = r.randint(1, 47)
            ins['sinc'] = r.randint(1, 15)
        if bad is not None:
            self.make_bad(ins, bad)
        self.segs[si].ins.append(ins)
        return ins

    def make_bad(self, ins, kind):
        ins['bad'] = kind
        if kind == 'align_a':
            ins['a_base'] += 8
            ins['cause'], ins['faddr'] = C_FIELD, ins['a_base']
        elif kind == 'k_mismatch':
            dt = DT_BF16 if ins['bf16'] else DT_E4M3
            ins['w_td'] = self.td(dt, ins['K'] + 1, ins['N'], ins['w_s1']) if ins['trans'] else self.td(dt, ins['N'], ins['K'] + 1, ins['w_s1'])
            ins['cause'], ins['faddr'] = C_FIELD, 0
        elif kind == 'acc_range':
            NT = ceil(ins['N'], self.dim)
            M = max(ins['M'], 1)
            ins['M'] = M
            ins['m_reg'] = True
            ins['acc_base'] = self.acc_rows - NT * M + 1
            ins['cause'], ins['faddr'] = C_RANGE, ins['acc_base'] | (1 << 63)
        elif kind == 'w_space':
            dt = DT_BF16 if ins['bf16'] else DT_E4M3
            K, N = ins['K'], ins['N']
            ins['w_td'] = self.td(dt, K, N, ins['w_s1'], SP_BAD) if ins['trans'] else self.td(dt, N, K, ins['w_s1'], SP_BAD)
            ins['cause'], ins['faddr'] = C_SPACE, ins['w_base']
        elif kind == 'word':
            ins['cause'], ins['faddr'] = C_FIELD, 0

    def encode(self, ins):
        a_reg, w_reg, acc_reg = 1, 2, 3
        m_reg = 4 if ins['m_reg'] else 0
        rs_reg = 5 if ins['accm'] == 2 else 0
        cs_reg = 6 if ins['cs'] else 0
        w = (OP_MXU_MM << 120) | (ins['wsem'] << 114) | (ins['ssem'] << 95) | (ins['sinc'] << 91)
        w |= (a_reg << 86) | (ins['a_td'] << 78) | (w_reg << 73) | (ins['w_td'] << 65) | (acc_reg << 60)
        w |= (m_reg << 55) | (rs_reg << 50) | (cs_reg << 45) | (ins['accm'] << 43) | (int(ins['trans']) << 39)
        if ins['bad'] == 'word':
            w |= 1 << 7
        regs = [ins['a_base'], ins['w_base'], ins['acc_base'], ins['M'] if m_reg else 0, ins['rs_base'], ins['cs_base']]
        rv = 0
        for i, v in enumerate(regs):
            rv |= (v & (2 ** 64 - 1)) << (64 * i)
        return (ins['wval'] << 512) | (rv << 128) | w

    # ---------------------------------------------------------------- whole program
    def build(self):
        r = self.r
        D = self.dim
        k = self.knobs
        # environment knobs
        style = r.choice(['calm', 'stall', 'stall', 'burst', 'astall', 'mixed', 'rdyv'])
        k['style'] = style
        k['pa'] = k['pw'] = k['ps'] = 0
        k['ba'] = k['bw'] = k['bs'] = 0
        k['bla'] = k['blw'] = k['bls'] = 1
        k['rdyv'] = 0
        if style in ('stall', 'mixed', 'rdyv'):
            k['pa'], k['pw'], k['ps'] = r.randint(0, 600), r.randint(0, 600), r.randint(0, 600)
        if style in ('burst', 'mixed'):
            k['ba'], k['bw'], k['bs'] = r.randint(0, 40), r.randint(0, 40), r.randint(0, 40)
            k['bla'], k['blw'], k['bls'] = r.randint(1, 60), r.randint(1, 60), r.randint(1, 60)
        if style == 'astall':           # long A stalls only: the loader runs ahead of the stream
            k['ba'], k['bla'] = r.randint(20, 120), r.randint(20, 200)
        if style == 'rdyv':
            k['rdyv'] = 1
        k['accp'] = r.choice([0, 0, 300, 1000])
        k['accx'] = r.choice([0, 3, 20, 150, 400])
        k['qgp'] = r.choice([0, 100, 500])
        k['qgap'] = r.choice([0, 2, 10, 60])
        k['dtd'] = r.choice([0, 0, 1, 3, 8])
        focus = r.choice([None, None, None, 'cs_small_k', 'rescale', 'rsfull'])
        k['focus'] = focus
        # segment 0: maybe ends in a fault, maybe reset mid-run; segment 1 after the reset
        fault = r.random() < 0.3
        mid_reset = (not fault) and r.random() < 0.15
        k['rstd'] = r.choice([0, 5, 50, 400, 3000]) if fault else -1
        k['rst_at'] = r.randint(200, 4000) if mid_reset else -1
        nseg = 2 if (fault or mid_reset) else 1
        for si in range(nseg):
            seg = self.segs[si]
            seg.sem0 = [r.randint(0, 3) if r.random() < 0.3 else 0 for _ in range(64)]
            seg.acc0 = [[r.randint(-64, 64) / 8 for _ in range(D)] for _ in range(self.acc_rows)]
            n = r.randint(6, 30)
            prev = None
            for i in range(n):
                ins = self.add(si, prev, focus if r.random() < 0.6 else None)
                if ins is None:
                    break
                prev = (ins['acc_base'], ceil(ins['N'], D) * ins['M'])
            if si == 0 and fault and len(seg.ins) > 0:
                bad = r.choice(['align_a', 'k_mismatch', 'acc_range', 'w_space', 'word'])
                ins = self.add(si, prev, bad=bad)
                if ins is not None:
                    for _ in range(r.randint(1, 3)):          # never taken
                        self.add(si, prev)
            self.waits(si)
        return self

    def waits(self, si):
        """Semaphore waits: on an earlier instruction's sem_inc (cumulative value) or on another engine's increment."""
        r = self.r
        seg = self.segs[si]
        cum = list(seg.sem0)
        after = []                       # value of every semaphore after instruction i completes
        for ins in seg.ins:
            if ins['bad']:
                break
            if ins['ssem'] and ins['sinc']:
                cum[ins['ssem']] = (cum[ins['ssem']] + ins['sinc']) & 0xFFFFFFFF
            after.append(list(cum))
        ecum = list(seg.sem0)
        for i, ins in enumerate(seg.ins):
            x = r.random()
            if i >= 1 and x < 0.15:
                j = r.randrange(max(0, i - 4), i)
                if j < len(after) and seg.ins[j]['ssem'] and seg.ins[j]['sinc']:
                    s = seg.ins[j]['ssem']
                    ins['wsem'], ins['wval'], ins['wait_on'] = s, after[j][s], j
            elif x < 0.22:
                s = r.randint(48, 63)
                amt = r.randint(1, 5)
                t = r.randint(0, 6000)
                seg.ext.append((t, s, amt))
                ecum[s] += amt
                ins['wsem'], ins['wval'] = s, ecum[s]
        # the external increments of one semaphore must happen in the order of the values waited for
        bys = {}
        for t, s, a in seg.ext:
            bys.setdefault(s, []).append((t, a))
        ext = []
        for s, lst in bys.items():
            ts = sorted(t for t, _ in lst)
            ext += [(t, s, a) for t, (_, a) in zip(ts, lst)]
        seg.ext = sorted(ext)

    def write(self, d):
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'spm.hex'), 'w') as f:
            f.write('\n'.join('%02x' % b for b in self.spm) + '\n')
        with open(os.path.join(d, 'dtab.hex'), 'w') as f:
            f.write('\n'.join('%064x' % self.dtab.get(i, 0) for i in range(256)) + '\n')
        for si, seg in enumerate(self.segs):
            with open(os.path.join(d, 'prog%d.hex' % si), 'w') as f:
                f.write(''.join('%0136x\n' % self.encode(ins) for ins in seg.ins))
            with open(os.path.join(d, 'sem%d.hex' % si), 'w') as f:
                f.write(''.join('%08x\n' % v for v in seg.sem0))
            with open(os.path.join(d, 'ext%d.txt' % si), 'w') as f:
                f.write(''.join('%d %d %d\n' % e for e in seg.ext))

    def plusargs(self, d, seed):
        k = self.knobs
        a = ['+dir=%s' % d, '+seed=%d' % seed, '+maxcyc=100000']
        for n in ('pa', 'pw', 'ps', 'ba', 'bw', 'bs', 'bla', 'blw', 'bls', 'rdyv', 'accx', 'accp', 'qgap', 'qgp', 'dtd', 'rstd', 'rst_at'):
            a.append('+%s=%d' % (n, k[n]))
        nr = sum(len(expect_ins(self, ins)) for seg in self.segs for ins in seg.ins if not ins['bad'])
        a.append('+maxrows=%d' % (2 * nr + 2000))           # a runaway stream (e.g. a pass of 65535 rows) ends the run
        a += ['+n0=%d' % len(self.segs[0].ins), '+n1=%d' % len(self.segs[1].ins),
              '+ne0=%d' % len(self.segs[0].ext), '+ne1=%d' % len(self.segs[1].ext)]
        return a


# ------------------------------------------------------------------------------------------------ expectation
def expect_ins(p, ins):
    """The ACC events of one instruction in mxu.md 4.2 order: (row, ncols, ctl 'rocl', rowscale, cs[:ncols], part[:ncols])."""
    D = p.dim
    K, N, M = ins['K'], ins['N'], ins['M']
    kte = 2 * D if ins['bf16'] else 4 * D
    KT, NT = ceil(K, kte), ceil(N, D)
    sh = {8: 3, 16: 2, 32: 1, 64: 0}[D]
    ev = []
    for j in range(NT):
        nc = min(D, N - j * D)
        for t in range(KT):
            ks = range(t * kte, min(K, (t + 1) * kte))
            for m in range(M):
                resc = ins['accm'] == 2 and t == 0
                ovw = ins['accm'] == 0 and t == 0
                last = j == NT - 1 and t == KT - 1 and m == M - 1
                part = [sum(ins['A'][m][k] * ins['W'][k][j * D + c] for k in ks) for c in range(nc)]
                cs = [ins['CS'][t >> sh][j * D + c] for c in range(nc)] if ins['cs'] else None
                rs = ins['RS'][m] if resc else None
                ev.append((ins['acc_base'] + j * M + m, nc, resc, ovw, ins['cs'], last, rs, cs, part))
    return ev


def acc_apply(row, e, exact):
    """One ACC lane update on `row` (list of floats) for event e; columns >= ncols unchanged. exact: list to note inexact steps."""
    _, nc, resc, ovw, ucs, _, rs, cs, part = e
    out = list(row)
    for c in range(nc):
        p = part[c] * cs[c] if ucs else part[c]
        a = row[c] * rs if resc else row[c]
        y = p if ovw else a + p
        for v in (p, a, y):
            if not is_f32(v) and v != 0:
                exact.append(v)
        out[c] = y
    return out


# ------------------------------------------------------------------------------------------------ log parsing / checking
def h2words(h, n):
    """Words of a %h dump, LSB word first; a word with an x / z digit is None."""
    h = h.rjust(8 * n, '0')
    out = []
    for i in range(n):
        w = h[len(h) - 8 * (i + 1):len(h) - 8 * i]
        out.append(None if any(ch in 'xXzZ' for ch in w) else int(w, 16))
    return out


def parse(path, D):
    segs = [dict(Q=[], A=[], D=[], S=[], F=[], E=[], X=[], FL=[]), dict(Q=[], A=[], D=[], S=[], F=[], E=[], X=[], FL=[])]
    si = 0
    end = None
    sems = {}
    rcyc = None
    cov = {}
    for line in open(path):
        t = line.split()
        if not t:
            continue
        if t[0] == 'SEM':
            sems[int(t[1])] = int(t[2], 16)
            continue
        if t[0] == 'COV':
            cov = {kv.split('=')[0]: int(kv.split('=')[1]) for kv in t[1:]}
            continue
        cyc, kind = int(t[0]), t[1]
        s = segs[si]
        if kind == 'R':
            si, rcyc = 1, cyc
        elif kind == 'Q':
            s['Q'].append((cyc, int(t[2]), int(t[3]), int(t[4])))
        elif kind == 'A':
            ctl = t[4]
            s['A'].append(dict(c=cyc, row=int(t[2]), nc=int(t[3]), resc=ctl[0] == '1', ovw=ctl[1] == '1', ucs=ctl[2] == '1',
                               last=ctl[3] == '1', rs=h2words(t[5], 1)[0], cs=h2words(t[6], D), part=h2words(t[7], D)))
        elif kind == 'D':
            s['D'].append(cyc)
        elif kind == 'S':
            s['S'].append((cyc, int(t[2]), int(t[3])))
        elif kind == 'F':
            s['F'].append((cyc, int(t[2]), int(t[3], 16)))
        elif kind == 'E':
            s['E'].append(line.strip())
        elif kind == 'X':
            s['X'].append(line.strip())
        elif kind == 'FL':
            s['FL'].append(line.strip())
        elif kind == 'END':
            end = line.strip()
    return segs, end, sems, rcyc, cov


def feq(bits, x):
    if bits is None:
        return False
    v = bits_f32(bits)
    return v == x                       # +0 == -0


def check(p, path):
    D, L = p.dim, p.acc_lat
    segs, end, sems, rcyc, cov = parse(path, D)
    errs = []
    if end is None:
        return ['no END line (simulation crashed?)'], {}
    if 'TIMEOUT' in end:
        errs.append('timeout: ' + end)
    stats = dict(rows=0, ins=0, cut=0)
    stats.update(cov)
    nseg = 2 if (p.knobs['rstd'] >= 0 or p.knobs['rst_at'] >= 0) else 1
    reset_seen = rcyc is not None
    if nseg == 2 and not reset_seen and segs[0]['F']:
        errs.append('fault but no reset logged')
    for si in range(nseg):
        seg, log = p.segs[si], segs[si]
        tag = 'seg%d: ' % si
        # segment 0 ended by a reset: a prefix is enough, unless the reset came long after a fault (then all must complete)
        cut = si == 0 and reset_seen and not (log['F'] and p.knobs['rstd'] >= 3000)
        for kname in ('E', 'X', 'FL'):
            for x in log[kname][:5]:
                errs.append(tag + x)
        # instructions that should run: up to the first illegal one
        good = []
        bad = None
        for i, ins in enumerate(seg.ins):
            if ins['bad']:
                bad = i
                break
            good.append(i)
        # takes
        took = [q[2] for q in log['Q']]
        if took != list(range(len(took))):
            errs.append(tag + 'takes out of order %s' % took[:10])
        lim = len(seg.ins) if bad is None else bad + 1
        if len(took) > lim:
            errs.append(tag + 'instruction taken after the fault: %s' % took[lim:])
        if not cut and len(took) < lim:
            errs.append(tag + 'only %d of %d instructions taken (hang?) %s' % (len(took), lim, end))
        # fault
        if bad is not None and (len(took) > bad):
            ins = seg.ins[bad]
            if len(log['F']) != 1:
                errs.append(tag + 'expected one fault, got %s' % log['F'])
            elif (log['F'][0][1], log['F'][0][2]) != (ins['cause'], ins['faddr']):
                errs.append(tag + 'fault %s, expected cause %d addr %x (%s)' % (log['F'][0], ins['cause'], ins['faddr'], ins['bad']))
        elif log['F']:
            errs.append(tag + 'unexpected fault %s' % log['F'])
        # wait conditions at the take
        for (c, _, k, v) in log['Q']:
            ins = seg.ins[k]
            if ins['wsem'] and ((v - ins['wval']) & 0xFFFFFFFF) >= 0x80000000:
                errs.append(tag + 'instruction %d taken at %d with sem %d = %d < %d' % (k, c, ins['wsem'], v, ins['wval']))
        # expected ACC stream
        exp, owner = [], []
        for i in good:
            ev = expect_ins(p, seg.ins[i])
            exp += ev
            owner += [i] * len(ev)
        got = log['A']
        if len(got) > len(exp):
            errs.append(tag + '%d ACC rows, expected %d' % (len(got), len(exp)))
        if not cut and len(got) < len(exp) and not errs:
            errs.append(tag + 'only %d of %d ACC rows (hang?) %s' % (len(got), len(exp), end))
        nbad = 0
        for n, (g, e) in enumerate(zip(got, exp)):
            row, nc, resc, ovw, ucs, last, rs, cs, part = e
            what = []
            if (g['row'], g['nc'], g['resc'], g['ovw'], g['ucs'], g['last']) != (row, nc, resc, ovw, ucs, last):
                what.append('ctl got %s exp %s' % ((g['row'], g['nc'], g['resc'], g['ovw'], g['ucs'], g['last']), (row, nc, resc, ovw, ucs, last)))
            if resc and not feq(g['rs'], rs):
                what.append('rowscale %s exp %s' % (g['rs'], rs))
            if ucs and any(not feq(g['cs'][c], cs[c]) for c in range(nc)):
                what.append('cs %s exp %s' % ([None if x is None else hex(x) for x in g['cs'][:nc]], cs))
            if not STUB and any(not feq(g['part'][c], part[c]) for c in range(min(nc, D))):
                what.append('part %s exp %s' % ([None if x is None else bits_f32(x) for x in g['part'][:nc]], part))
            if what:
                nbad += 1
                if nbad <= 3:
                    errs.append(tag + 'ACC row #%d (instruction %d, cycle %d): %s' % (n, owner[n], g['c'], '; '.join(what)))
        if nbad > 3:
            errs.append(tag + '%d bad ACC rows in all' % nbad)
        stats['rows'] += len(got)
        # spacing
        lastc, lasto = {}, {}
        nsp = 0
        for n, g in enumerate(got):
            o = owner[n] if n < len(owner) else -1
            if g['row'] in lastc:
                dc = g['c'] - lastc[g['row']]
                if dc < L:
                    nsp += 1
                    if nsp <= 3:
                        errs.append(tag + 'ACC row %d updated at %d and %d (< ACC_LAT %d)' % (g['row'], lastc[g['row']], g['c'], L))
                elif dc == L:                       # coverage: a pair exactly at the limit (same / different instruction)
                    k = 'tight_x' if lasto[g['row']] != o else 'tight_in'
                    stats[k] = stats.get(k, 0) + 1
            lastc[g['row']] = g['c']
            lasto[g['row']] = o
        # timed ACC model on the RTL's stream vs the sequential expectation
        if not cut and not STUB:
            inexact = []
            img = [list(x) for x in seg.acc0]
            for e in exp:
                img[e[0]] = acc_apply(img[e[0]], e, inexact)
            timed = [list(x) for x in seg.acc0]
            pend = []
            for g in got:
                while pend and pend[0][0] <= g['c']:
                    _, rr, v = pend.pop(0)
                    timed[rr] = v
                f = lambda x: float('nan') if x is None else bits_f32(x)
                cs = [f(x) for x in g['cs']]
                part = [f(x) for x in g['part']]
                e = (g['row'], g['nc'], g['resc'], g['ovw'], g['ucs'], g['last'], f(g['rs']), cs, part)
                pend.append((g['c'] + L, g['row'], acc_apply(timed[g['row']], e, [])))
                pend.sort(key=lambda x: x[0])
            for _, rr, v in pend:
                timed[rr] = v
            if inexact:
                errs.append(tag + 'generator: %d inexact ACC steps (test data problem, not an RTL finding)' % len(inexact))
            diff = [rr for rr in range(p.acc_rows) if any(not (timed[rr][c] == img[rr][c]) for c in range(D))]
            if diff and not errs:
                errs.append(tag + 'ACC image differs in %d rows (first %d)' % (len(diff), diff[0]))
        # completion
        exp_s, s_ins = [], []
        for i in good:
            ins = seg.ins[i]
            if ins['ssem'] and ins['sinc']:
                exp_s.append((ins['ssem'], ins['sinc']))
                s_ins.append(i)
        got_s = [(x[1], x[2]) for x in log['S']]
        if got_s != exp_s[:len(got_s)] or len(got_s) > len(exp_s):
            errs.append(tag + 'sem_inc sequence %s, expected %s' % (got_s[:8], exp_s[:8]))
        elif not cut and len(got_s) < len(exp_s):
            errs.append(tag + 'only %d of %d sem_inc (hang / lost completion) %s' % (len(got_s), len(exp_s), end))
        # sem_inc after the visible last write of its instruction (M = 0: of every earlier one)
        lastrow = {}
        for n, g in enumerate(got):
            lastrow[owner[n]] = g['c']
        for (c, _, _), i in zip(log['S'], s_ins):
            need = max([lastrow.get(j, -10 ** 9) for j in good if j <= i] or [-10 ** 9]) + L
            own = lastrow.get(i)
            if seg.ins[i]['M'] > 0 and own is None:
                errs.append(tag + 'sem_inc of instruction %d at %d before any of its rows' % (i, c))
            elif c < need:
                errs.append(tag + 'sem_inc of instruction %d at %d, a write is visible only at %d' % (i, c, need))
        # wait on an earlier instruction: taken after its sem_inc
        sem_at = {i: c for (c, _, _), i in zip(log['S'], s_ins)}
        for (c, _, k, _) in log['Q']:
            j = seg.ins[k]['wait_on']
            if j is not None and (j not in sem_at or c <= sem_at[j]):
                errs.append(tag + 'instruction %d taken at %d before the sem_inc of instruction %d (%s)' % (k, c, j, sem_at.get(j)))
        # acc_done count = acc_last rows
        if len(log['D']) > sum(1 for g in got if g['last']):
            errs.append(tag + 'more acc_done than last rows')
        # final semaphores (only the last segment is dumped)
        if si == nseg - 1 and not cut:
            fin = list(seg.sem0)
            for i in good:
                ins = seg.ins[i]
                if ins['ssem'] and ins['sinc']:
                    fin[ins['ssem']] = (fin[ins['ssem']] + ins['sinc']) & 0xFFFFFFFF
            for t, s, a in seg.ext:
                fin[s] += a
            bad_s = [s for s in range(64) if sems.get(s) != fin[s]]
            if bad_s and not errs:
                errs.append(tag + 'final semaphores differ at %s' % bad_s[:5])
        # in-flight depth reached (completions pending at the same time)
        stats['ins'] += len(took)
        if cut:
            stats['cut'] += 1
    return errs, stats


# ------------------------------------------------------------------------------------------------ build and run
def build(cfg, rtl):
    D, SL, AL, CD = CFGS[cfg]
    tagr = os.path.basename(os.path.normpath(rtl)) if rtl != ROOT else 'rtl'
    out = os.path.join(BUILD, 'vvp', '%s_%s_spm%d.vvp' % (tagr, cfg, SPM_BYTES_D[D] // 1024))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    srcs = [os.path.join(HERE, 'tb_mxu_hz.sv'), os.path.join(ROOT, 'isa/gen/isa_pkg.sv')]
    for d in ('rtl/core', 'rtl/arith'):
        srcs += sorted(os.path.join(rtl, d, f) for f in os.listdir(os.path.join(rtl, d)) if f.endswith('.sv'))
    if STUB:
        srcs = [x for x in srcs if os.path.basename(x) != 'mxu_pe.sv'] + [os.path.join(HERE, 'hz_pe_stub.sv')]
        out = out.replace('.vvp', '_stub.vvp')
    newest = max(os.path.getmtime(s) for s in srcs)
    if os.path.exists(out) and os.path.getmtime(out) > newest:
        return out
    P = dict(DIM=D, SPM_LAT=SL, ACC_LAT=AL, CS_DEPTH=CD, ACC_ROWS={8: 256, 16: 512, 32: 1024, 64: 2048}[D], SPM_BYTES=SPM_BYTES_D[D])
    cmd = ['iverilog', '-g2012', '-o', out] + sum([['-P', 'tb_mxu_hz.%s=%d' % kv] for kv in P.items()], []) + srcs
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit('compile failed (%s):\n%s' % (cfg, r.stderr[-3000:]))
    return out


def run_one(cfg, seed, vvp, keep, rtl_tag):
    p = Prog(cfg, seed).build()
    d = os.path.join(BUILD, 'cases', rtl_tag + ('_stub' if STUB else ''), p.name)
    p.write(d)
    r = subprocess.run(['vvp', '-n', vvp] + p.plusargs(d, seed), capture_output=True, text=True, timeout=3600)
    logp = os.path.join(d, 'log.txt')
    if not os.path.exists(logp):
        return p, ['no log; vvp rc %d: %s' % (r.returncode, r.stderr[-500:])], {}
    errs, st = check(p, logp)
    if not errs and not keep:
        for f in os.listdir(d):
            os.remove(os.path.join(d, f))
        os.rmdir(d)
    return p, errs, st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cfg', nargs='*', default=list(CFGS))
    ap.add_argument('--seeds', type=int, default=10)
    ap.add_argument('--seed0', type=int, default=1)
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--rtl', default=ROOT)
    ap.add_argument('--keep', action='store_true')
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('--stop', action='store_true', help='stop at the first failing case')
    ap.add_argument('--stub', action='store_true', help='control only: stub PE (hz_pe_stub.sv), no partial sums checked')
    a = ap.parse_args()
    global STUB
    STUB = a.stub
    rtl = os.path.abspath(a.rtl)
    rtl_tag = os.path.basename(os.path.normpath(rtl)) if rtl != ROOT else 'rtl'
    vvps = {c: build(c, rtl) for c in a.cfg}
    jobs = [(c, s) for s in range(a.seed0, a.seed0 + a.seeds) for c in a.cfg]
    nfail = 0
    tot = dict(rows=0, ins=0, cut=0)
    with ThreadPoolExecutor(a.jobs) as ex:
        futs = [ex.submit(run_one, c, s, vvps[c], a.keep, rtl_tag) for c, s in jobs]
        for f in (as_completed(futs) if a.stop else futs):
            if f.cancelled():
                continue
            p, errs, st = f.result()
            for kx, v in st.items():
                tot[kx] = tot.get(kx, 0) + v
            if errs:
                nfail += 1
                print('FAIL %s %s' % (p.name, {k: v for k, v in p.knobs.items()}))
                for e in errs[:8]:
                    print('   ', e)
                sys.stdout.flush()
                if a.stop:
                    ex.shutdown(cancel_futures=True)
                    break
            elif not a.quiet:
                print('ok   %s style=%s focus=%s rows=%d ins=%d' % (p.name, p.knobs['style'], p.knobs['focus'], st['rows'], st['ins']))
                sys.stdout.flush()
    print('%d runs, %d failed; %d instructions taken, %d ACC rows, %d segments cut by a reset' %
          (len(jobs), nfail, tot['ins'], tot['rows'], tot['cut']))
    print('coverage (cycles, all runs): ' + ' '.join('%s=%d' % (k, v) for k, v in tot.items() if k not in ('rows', 'ins', 'cut')))
    sys.exit(1 if nfail else 0)


if __name__ == '__main__':
    main()
