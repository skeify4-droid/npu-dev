"""Independent reference of MXU_MM (review part R, docs/reports/mxu_review.md).

Written from numerics.md N-1 / N-3 / N-5 and isa_ref.md MXU_MM only, in plain Python integers. It does not import
sw/npumodel/arith_ref.py, numerics.py or NumPy, and does not reuse scripts/gen_mxu_vectors.py.

A finite value is (sign, m, e) = (-1)^sign * m * 2^e with m > 0 an int. Decoded operands are tuples:
    ('nan',)  ('inf', s)  ('zero', s)  ('fin', s, m, e)

Flags (N-5): bit 0 INVALID, 1 OVERFLOW, 2 SATURATE, 3 NAN_IN.

Choices where N-3 / N-5 do not say it in so many words (each is reported in the review when it matters):
  * the dot product of a PE is a fused operation: NaN operand -> NaN, NAN_IN only; Inf x 0 or +Inf + -Inf among the products
    -> NaN, INVALID; an Inf product otherwise gives that Inf (no OVERFLOW); else the exact sum rounded once (RNE, FTZ).
    The sign of an exact zero sum follows IEEE 754 (-0 only when every product is -0); `ZERO_SIGN` switches that.
  * K / N tails are +0 operands (mxu.md 3, "Unused operands"): a K-tail position of a PE row contributes a +0 x +0 product, and
    a PE row with no K element at all still adds a +0 dot. `PAD_MODE = 'absent'` drops them instead (the logical A x W).
"""

CANON_NAN = 0x7FC00000
F_INV, F_OVF, F_SAT, F_NAN = 1, 2, 4, 8

ZERO_SIGN = 'ieee'      # 'ieee' or 'plus' (an exact zero dot is always +0)
PAD_MODE = 'zero'       # 'zero' (+0 operands in the tails, mxu.md 3) or 'absent'


# ---------------------------------------------------------------- decode
def dec_fp32(b, daz=True):
    s = (b >> 31) & 1
    e = (b >> 23) & 0xFF
    f = b & 0x7FFFFF
    if e == 0xFF:
        return ('nan',) if f else ('inf', s)
    if e == 0:
        if f == 0 or daz:
            return ('zero', s)
        return ('fin', s, f, -149)
    return ('fin', s, f | 0x800000, e - 150)


def dec_bf16(h, daz=True):
    return dec_fp32((h & 0xFFFF) << 16, daz)


def dec_e4m3(c):
    s = (c >> 7) & 1
    e = (c >> 3) & 0xF
    m = c & 7
    if e == 0xF and m == 7:
        return ('nan',)
    if e == 0:
        return ('zero', s) if m == 0 else ('fin', s, m, -9)          # subnormal m/8 * 2^-6, kept (N-1)
    return ('fin', s, 8 | m, e - 10)                                 # (8+m)/8 * 2^(e-7)


def is_nan32(b):
    return ((b >> 23) & 0xFF) == 0xFF and (b & 0x7FFFFF) != 0


# ---------------------------------------------------------------- rounding to FP32 (RNE, round then flush)
def zero32(s):
    return (s << 31)


def round32(s, m, e):
    """(-1)^s m 2^e, m > 0, rounded RNE onto the FP32 grid (with its subnormal grid), then flushed if still subnormal.
    Returns (bits, flags)."""
    nb = m.bit_length()
    E = nb - 1 + e                       # floor(log2 |x|)
    q = max(E, -126) - 23                # exponent of the ulp
    sh = q - e
    if sh <= 0:
        n = m << (-sh)
    else:
        n = m >> sh
        rem = m & ((1 << sh) - 1)
        half = 1 << (sh - 1)
        if rem > half or (rem == half and (n & 1)):
            n += 1
    if n == 0:
        return zero32(s), 0
    if n.bit_length() > 24:              # carried into the next binade
        n >>= 1
        q += 1
    E2 = n.bit_length() - 1 + q
    if E2 > 127:
        return (s << 31) | 0x7F800000, F_OVF
    if E2 < -126:
        return zero32(s), 0              # FTZ: still subnormal after rounding -> zero of the same sign
    mant = (n << (23 - (n.bit_length() - 1))) & 0x7FFFFF
    return (s << 31) | ((E2 + 127) << 23) | mant, 0


def exact_sum(terms):
    """terms: list of (s, m, e) finite non-zero. Returns (s, m, e) or None for an exact zero."""
    emin = min(t[2] for t in terms)
    tot = 0
    for s, m, e in terms:
        v = m << (e - emin)
        tot += -v if s else v
    if tot == 0:
        return None
    return (1 if tot < 0 else 0, abs(tot), emin)


# ---------------------------------------------------------------- FP32 add / mul (DAZ / FTZ, N-5 flags)
def fp32_add(a, b):
    x, y = dec_fp32(a), dec_fp32(b)
    if x[0] == 'nan' or y[0] == 'nan':
        return CANON_NAN, F_NAN
    if x[0] == 'inf' and y[0] == 'inf':
        if x[1] != y[1]:
            return CANON_NAN, F_INV
        return a if (a >> 23) & 0xFF == 0xFF else b, 0
    if x[0] == 'inf':
        return (x[1] << 31) | 0x7F800000, 0
    if y[0] == 'inf':
        return (y[1] << 31) | 0x7F800000, 0
    terms = [(v[1], v[2], v[3]) for v in (x, y) if v[0] == 'fin']
    if not terms:
        return zero32(x[1] & y[1]), 0                 # -0 only for -0 + -0 (RNE)
    r = exact_sum(terms)
    if r is None:
        return zero32(0), 0                           # x + (-x) = +0 under RNE
    return round32(*r)


def fp32_mul(a, b):
    x, y = dec_fp32(a), dec_fp32(b)
    if x[0] == 'nan' or y[0] == 'nan':
        return CANON_NAN, F_NAN
    s = x[1] ^ y[1]
    if (x[0] == 'inf' and y[0] == 'zero') or (x[0] == 'zero' and y[0] == 'inf'):
        return CANON_NAN, F_INV
    if x[0] == 'inf' or y[0] == 'inf':
        return (s << 31) | 0x7F800000, 0
    if x[0] == 'zero' or y[0] == 'zero':
        return zero32(s), 0
    return round32(s, x[2] * y[2], x[3] + y[3])


# ---------------------------------------------------------------- PE dot product (fused, one rounding)
def dot(pairs):
    """pairs: list of (a, w) decoded operands. Returns (bits, flags)."""
    for a, w in pairs:
        if a[0] == 'nan' or w[0] == 'nan':
            return CANON_NAN, F_NAN                   # the NaN wins: NAN_IN only
    prods = []                                       # ('inf', s) | ('zero', s) | ('fin', s, m, e)
    for a, w in pairs:
        s = a[1] ^ w[1]
        if (a[0] == 'inf' and w[0] == 'zero') or (a[0] == 'zero' and w[0] == 'inf'):
            return CANON_NAN, F_INV
        if a[0] == 'inf' or w[0] == 'inf':
            prods.append(('inf', s))
        elif a[0] == 'zero' or w[0] == 'zero':
            prods.append(('zero', s))
        else:
            prods.append(('fin', s, a[2] * w[2], a[3] + w[3]))
    infs = {p[1] for p in prods if p[0] == 'inf'}
    if len(infs) == 2:
        return CANON_NAN, F_INV
    if infs:
        return (infs.pop() << 31) | 0x7F800000, 0
    fins = [(p[1], p[2], p[3]) for p in prods if p[0] == 'fin']
    if fins:
        r = exact_sum(fins)
        if r is not None:
            return round32(*r)
    # exact zero
    if ZERO_SIGN == 'ieee' and not fins and all(p[1] == 1 for p in prods):
        return zero32(1), 0
    return zero32(0), 0


def pe(psum, pairs):
    """One PE: psum_out = fp32_add(psum_in, dot). PE flags: the dot's NAN_IN / OVERFLOW / INVALID and the add's
    OVERFLOW / INVALID (a NaN psum was reported by the PE that made or consumed it: N-5)."""
    d, fd = dot(pairs)
    y, fa = fp32_add(psum, d)
    return y, (fd & (F_NAN | F_OVF | F_INV)) | (fa & (F_OVF | F_INV))


# ---------------------------------------------------------------- ACC lane (N-3 step 4, N-5)
def acc_lane(acc, part, rowscale, cs, rescale, use_cs, overwrite):
    fl = 0
    a = acc
    if rescale:
        a, f = fp32_mul(acc, rowscale)
        fl |= f
    p = part
    if use_cs:
        p, f = fp32_mul(part, cs)
        fl |= f
    if overwrite:
        return p, fl                                 # a copy: no flag of its own
    y, f = fp32_add(a, p)
    nan_ext = ((not rescale) and is_nan32(acc)) or ((not use_cs) and is_nan32(part))
    fl |= (f & (F_OVF | F_INV)) | (F_NAN if (f & F_NAN) and nan_ext else 0)
    return y, fl


# ---------------------------------------------------------------- MXU_MM
def rd(spm, addr, n):
    return int.from_bytes(bytes(spm[addr:addr + n]), 'little')


def mxu_mm(spm, ins, acc, dim):
    """ins: dict with bf16, trans, K, N, M, a_base, a_s1, w_base, w_s1, acc_base, accm (0 OVERWRITE, 1 ACCUM, 2 RESCALE),
    cs_base (None: no column scales), rs_base (RESCALE only). acc: dict row -> list of dim u32, updated in place.
    Returns (stream, pe_flags, acc_flags): stream is the list of the rows the ACC must receive, in order, each a dict."""
    bf16, trans = ins['bf16'], ins['trans']
    K, N, M = ins['K'], ins['N'], ins['M']
    eb = 2 if bf16 else 1
    per = 2 if bf16 else 4                # K elements per PE row
    kte = per * dim                       # K tile (N-3: 4 DIM FP8, 2 DIM BF16)
    KT = -(-K // kte)
    NT = -(-N // dim)
    R = 64 // dim                         # MXU passes per fold-tile scale row (N-3 step 4)
    CSP = -(-4 * N // 64) * 64
    dec = dec_bf16 if bf16 else dec_e4m3

    def A(m, k):
        return dec(rd(spm, ins['a_base'] + m * ins['a_s1'] + k * eb, eb))

    def W(k, n):
        if trans:
            return dec(rd(spm, ins['w_base'] + n * ins['w_s1'] + k * eb, eb))
        return dec(rd(spm, ins['w_base'] + k * ins['w_s1'] + n * eb, eb))

    zero = ('zero', 0)
    stream, pe_fl, acc_fl = [], 0, 0
    if M == 0:
        return stream, 0, 0
    Ad = [[A(m, k) for k in range(K)] for m in range(M)]
    Wd = [[W(k, n) for n in range(N)] for k in range(K)]
    for j in range(NT):
        ncols = min(dim, N - j * dim)
        for t in range(KT):
            cs = None
            if ins['cs_base'] is not None:
                row = ins['cs_base'] + (t // R) * CSP
                cs = [rd(spm, row + 4 * (j * dim + c), 4) for c in range(ncols)]
            for m in range(M):
                part = []
                for c in range(ncols):
                    n = j * dim + c
                    ps = 0
                    for i in range(dim):
                        pairs = []
                        for q in range(per):
                            k = t * kte + per * i + q
                            if k < K:
                                pairs.append((Ad[m][k], Wd[k][n]))
                            elif PAD_MODE == 'zero':
                                pairs.append((zero, zero))
                        if not pairs:
                            continue      # PAD_MODE 'absent': no PE row
                        ps, f = pe(ps, pairs)
                        pe_fl |= f
                    part.append(ps)
                resc = ins['accm'] == 2 and t == 0
                ovw = ins['accm'] == 0 and t == 0
                rs = rd(spm, ins['rs_base'] + 4 * m, 4) if resc else None
                arow = ins['acc_base'] + j * M + m
                stream.append(dict(row=arow, ncols=ncols, rescale=resc, overwrite=ovw, use_cs=cs is not None,
                                   rowscale=rs, cs=cs, part=part,
                                   last=(j == NT - 1 and t == KT - 1 and m == M - 1)))
                for c in range(ncols):
                    y, f = acc_lane(acc[arow][c], part[c], rs or 0, cs[c] if cs else 0, resc, cs is not None, ovw)
                    acc[arow][c] = y
                    acc_fl |= f
    return stream, pe_fl, acc_fl
