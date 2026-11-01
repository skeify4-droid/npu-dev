"""HBM3e pseudo-channel controller model (docs/spec/hbm.md).

Two parts:
- `Dram`: the bank-state and timing-rule checker of one pseudo-channel (PC). `earliest()` answers when a command becomes
  legal, `issue()` raises `Violation` for any illegal command. It is the reference for the Phase 4 RTL timing checker.
- `Channel`: the two PCs of a channel and their shared row and column command buses (review finding X-2(b)).
- `simulate()`: the controller's per-PC scheduler (FR-FCFS with an age cap, read/write drain with watermarks,
  adaptive page policy, per-bank refresh) issuing into a `Dram`, cycle by cycle, at the controller clock.
  `simulate_channel()` runs two of them on one `Channel`.

Time unit: one controller cycle = 1 / 1.2 GHz = 0.833 ns = one BL8 burst (32 B) on one PC (HBM3e at 9.6 Gb/s, CK = 2.4 GHz,
DFI-like interface at CK / 2, one row and one column command per PC per cycle).
"""
import math
import random
from collections import deque

CLK_MC = 1.2e9
BURST_B = 32
GRANULE_BURSTS = 8                  # a 256 B granule is 8 bursts in one row of one bank (memmap.md section 3)
SIDS, BGS, BAS = 3, 4, 4
BANKS = SIDS * BGS * BAS            # 48 banks per PC
PCS = 32

# Representative timings in ns. Source: Ramulator 2 HBM3_6400Mbps preset (nCK at 0.625 ns; marked a "guesstimate" there),
# except the entries marked (A), which are assumptions of this spec (hbm.md H-4).
T_NS = {
    "RCDRD": 19.375, "RCDWR": 9.375, "RP": 16.25, "RAS": 28.125, "CL": 12.5, "CWL": 6.25, "WR": 20.625, "RTP": 5.625,
    "RRDS": 2.5, "RRDL": 3.125, "FAW": 15.0, "WTRS": 4.375, "WTRL": 6.25,
    "CCDL": 2.5,        # (A) 4 nCK at 6.4 Gb/s, kept in ns
    "RREFD": 8.0,       # (A) REFpb to REFpb, any bank
    "RREFDA": 8.0,      # (A) REFpb to ACT of any other bank; assumed = tRREFD (review I-9 b; JESD238 checklist, hbm.md H-9)
    "RFCPB": 220.0,     # (A) 24 Gb die
    "RFCAB": 410.0,     # (A) 24 Gb die (DDR5 24 Gb class)
    "REFI": 3900.0,     # tREFI at <= 85 C; halved above
}
CYC_FIXED = {"BL": 1, "CCDS": 1, "CCDR": 2, "PHY_RD": 10,     # cycles; CCDR: column to column across SIDs (A); PHY_RD (A)
             "ACT_CK": 2}      # (A) CK slots an ACT occupies on the channel's row bus (HBM2 class; X-2(b)); other commands 1
ROW_CMDS = ("ACT", "PRE", "PREA", "REFPB", "REFAB")
COL_CMDS = ("RD", "WR")
CMDS = ROW_CMDS + COL_CMDS


def cycles(t_ns=None, hot=False):
    t = dict(T_NS, **(t_ns or {}))
    c = {k: math.ceil(v * CLK_MC / 1e9 - 1e-9) for k, v in t.items()}
    c.update(CYC_FIXED)
    if hot:
        c["REFI"] = c["REFI"] // 2
    c["RC"] = c["RAS"] + c["RP"]
    c["RTW"] = c["CL"] + c["BL"] + 1 - c["CWL"]             # read to write turnaround (1 cycle bus bubble)
    c["WTR_S"] = c["CWL"] + c["BL"] + c["WTRS"]
    c["WTR_L"] = c["CWL"] + c["BL"] + c["WTRL"]
    c["WRP"] = c["CWL"] + c["BL"] + c["WR"]                  # write to precharge, same bank
    c["REFIPB"] = c["REFI"] / BANKS
    return c


def rules(c):
    """(previous command, next command, scope, minimum gap). Scopes: bank, bg (same SID and bank group), sid, xsid (same
    PC, other SID), pc, and chan (the channel's shared command bus, gap in CK slots: PC p issues in slot 2t + p of
    controller cycle t). RD/WR with auto-precharge are RD/WR plus an implied PRE; PREA is a PRE of every bank (see
    Dram.issue), so the PRE -> x rules cover it."""
    R = [("ACT", "ACT", "bank", c["RC"]), ("ACT", "ACT", "bg", c["RRDL"]), ("ACT", "ACT", "pc", c["RRDS"]),
         ("ACT", "RD", "bank", c["RCDRD"]), ("ACT", "WR", "bank", c["RCDWR"]), ("ACT", "PRE", "bank", c["RAS"]),
         ("PRE", "ACT", "bank", c["RP"]), ("PRE", "REFPB", "bank", c["RP"]), ("PRE", "REFAB", "bank", c["RP"]),
         ("RD", "PRE", "bank", c["RTP"]), ("WR", "PRE", "bank", c["WRP"]),
         ("ACT", "PREA", "bank", c["RAS"]), ("RD", "PREA", "bank", c["RTP"]), ("WR", "PREA", "bank", c["WRP"]),
         ("RD", "WR", "pc", c["RTW"]), ("WR", "RD", "bg", c["WTR_L"]), ("WR", "RD", "pc", c["WTR_S"]),
         ("REFPB", "ACT", "bank", c["RFCPB"]), ("REFPB", "REFPB", "bank", c["RFCPB"]), ("REFPB", "REFPB", "pc", c["RREFD"]),
         ("REFPB", "ACT", "bg", c["RRDL"]), ("REFPB", "ACT", "pc", c["RREFDA"]),
         ("ACT", "REFPB", "bg", c["RRDL"]), ("ACT", "REFPB", "pc", c["RRDS"]),
         ("REFAB", "ACT", "pc", c["RFCAB"]), ("REFAB", "REFPB", "pc", c["RFCAB"]), ("REFAB", "REFAB", "pc", c["RFCAB"]),
         ("REFPB", "REFAB", "pc", c["RFCPB"])]
    for a in ("RD", "WR"):
        for b in ("RD", "WR"):
            if a == b:
                R += [(a, b, "bg", c["CCDL"]), (a, b, "sid", c["CCDS"]), (a, b, "xsid", c["CCDR"])]
    if c["ACT_CK"] > 1:                       # a multi-CK ACT holds the channel's row bus into the next slot
        R += [("ACT", n, "chan", c["ACT_CK"]) for n in ROW_CMDS]
    return R


class Violation(Exception):
    pass


def bank_index(sid, bg, ba):
    return (sid * BGS + bg) * BAS + ba


def _key(b, scope):
    return {"bank": b, "bg": b // BAS, "sid": b // (BGS * BAS), "pc": 0}[scope]


class Dram:
    """Bank state + timing checker of one pseudo-channel. Commands: ACT(b, row), PRE(b), PREA, RD/WR(b, row, ap), REFPB(b),
    REFAB; anything else is rejected. `rules_` replaces the rule table (the rule-removal test uses it). Channel-scope
    rules are left to `Channel`."""

    def __init__(self, c, refresh="pb", rules_=None):
        self.c, self.refresh = c, refresh
        self.rules = rules(c) if rules_ is None else rules_
        self.by_next = {}
        for p, n, s, g in self.rules:
            if s != "chan":
                self.by_next.setdefault(n, []).append((p, s, g))
        self.last = {}                      # (cmd, scope, key) -> time
        self.open = [-1] * BANKS            # open row per bank, -1 = closed
        self.act_time = [-10 ** 9] * BANKS
        self.faw = deque(maxlen=4)          # times of the last 4 ACT / REFPB
        self.t_row = self.t_col = -1
        self.ref_count = 0
        self.ref_round = set()              # banks refreshed in the current round of 48
        self.ref_by_bank = [0] * BANKS
        self.log = []

    def _last(self, cmd, scope, b):
        if scope == "xsid":
            s = b // (BGS * BAS)
            return max((self.last.get((cmd, "sid", o), -10 ** 9) for o in range(SIDS) if o != s), default=-10 ** 9)
        return self.last.get((cmd, scope, _key(b, scope)), -10 ** 9)

    def earliest(self, cmd, b=0):
        t = -10 ** 9
        if cmd in ("REFAB", "PREA"):                       # all-bank commands: every bank's rules apply
            for bb in range(BANKS):
                for p, s, g in self.by_next.get(cmd, ()):
                    t = max(t, self._last(p, s, bb) + g)
        else:
            for p, s, g in self.by_next.get(cmd, ()):
                t = max(t, self._last(p, s, b) + g)
        if cmd in ("ACT", "REFPB") and len(self.faw) == 4:
            t = max(t, self.faw[0] + self.c["FAW"])
        return t

    def _rec(self, cmd, b, t):
        for s in ("bank", "bg", "sid", "pc"):
            k = (cmd, s, _key(b, s))
            self.last[k] = max(self.last.get(k, -10 ** 9), t)

    def issue(self, t, cmd, b=0, row=None, ap=False):
        if cmd not in CMDS:
            raise Violation(f"unknown command {cmd}")
        bus = "col" if cmd in COL_CMDS else "row"
        if bus == "col":
            if t <= self.t_col:
                raise Violation(f"two column commands in cycle {t}")
            self.t_col = t
        else:
            if t <= self.t_row:
                raise Violation(f"two row commands in cycle {t}")
            self.t_row = t
        if cmd == "PRE" and self.open[b] < 0:
            return                                          # precharge of a closed bank is a no-op
        need = self.earliest(cmd, b)
        if t < need:
            raise Violation(f"{cmd} bank {b} at {t}, earliest {need}")
        if cmd == "ACT":
            if self.open[b] >= 0:
                raise Violation(f"ACT to open bank {b}")
            self.open[b], self.act_time[b] = row, t
            self.faw.append(t)
        elif cmd in ("RD", "WR"):
            if self.open[b] != row:
                raise Violation(f"{cmd} bank {b} row {row}, open row {self.open[b]}")
        elif cmd == "PRE":
            self.open[b] = -1
        elif cmd == "PREA":                                 # precharge all: every bank closes, RP applies to each
            for bb in range(BANKS):
                self.open[bb] = -1
                self._rec("PRE", bb, t)
        elif cmd == "REFPB":
            if self.open[b] >= 0:
                raise Violation(f"REFPB to open bank {b}")
            if b in self.ref_round:
                raise Violation(f"REFPB bank {b} twice in one round")
            self.ref_round.add(b)
            if len(self.ref_round) == BANKS:
                self.ref_round = set()
            self.faw.append(t)
            self.ref_count += 1
            self.ref_by_bank[b] += 1
        elif cmd == "REFAB":
            if any(r >= 0 for r in self.open):
                raise Violation("REFAB with an open bank")
            self.ref_count += BANKS
        self._rec(cmd, b, t)
        if cmd in ("RD", "WR") and ap:                      # auto-precharge: an implied PRE at the first legal time
            tp = max(t + (self.c["RTP"] if cmd == "RD" else self.c["WRP"]), self.act_time[b] + self.c["RAS"])
            self.open[b] = -1
            self._rec("PRE", b, tp)
        self.check_refresh(t)

    def check_refresh(self, t):
        due = int(t / self.c["REFIPB"])
        if self.ref_count < due - 8 * (1 if self.refresh == "pb" else BANKS):
            raise Violation(f"refresh behind: {self.ref_count} issued, {due} due at {t}")


class Channel:
    """The two PCs of an HBM channel (hbm.md H-1, H-3; review X-2(b)). They share the channel's row and column command
    buses: PC p drives CK slot 2t + p of controller cycle t, and a command occupying more than one CK (an ACT, ACT_CK)
    holds the bus into the next slot. Channel-scope rules (scope "chan", gaps in CK slots) are checked here, everything
    else by each PC's `Dram`. Commands must be presented in slot order, as a bus monitor sees them."""

    def __init__(self, c, refresh="pb", rules_=None):
        R = rules(c) if rules_ is None else rules_
        self.pc = [Dram(c, refresh, R), Dram(c, refresh, R)]
        self.chan = [(p, n, g) for p, n, s, g in R if s == "chan"]
        self.last = {}                      # command -> CK slot of its last issue on this channel
        self.slot = -1

    def ok(self, p, t, cmd):
        s = 2 * t + p
        return all(s >= self.last.get(a, -10 ** 9) + g for a, n, g in self.chan if n == cmd)

    def issue(self, p, t, cmd, b=0, row=None, ap=False):
        s = 2 * t + p
        if s < self.slot:
            raise Violation(f"channel commands out of slot order at slot {s}")
        if not self.ok(p, t, cmd):
            raise Violation(f"{cmd} on PC {p} at cycle {t}: channel command bus busy")
        self.pc[p].issue(t, cmd, b, row, ap)
        self.slot = s
        self.last[cmd] = s


def fold_pc(pc, row, pcs=PCS, leaves=4, bg=0, ba=0, xor=True):
    """Reduced configurations (hbm.md H-1): map the 5-bit memmap PC index onto `pcs` PCs behind `leaves` leaves.
    The unused PC bits become the low bits of an extended row and (xor, review finding I-10) are also XORed into the
    bank group and bank, so the granules of a 2 KiB block that share a physical PC fall in different banks instead of
    being row conflicts in one. Returns (leaf, physical PC, extended row, bg, ba). The identity for 32 PCs, 4 leaves."""
    lb = leaves.bit_length() - 1
    ppl = pcs // leaves                              # PCs per leaf, a power of two <= 8
    pb = ppl.bit_length() - 1
    k = (3 - pb) + (2 - lb)                          # folded bits
    assert k <= 4, "at least 2 PCs"
    leaf = (pc >> 3) & (leaves - 1)
    within = (pc & 7) & (ppl - 1)
    extra = (((pc & 7) >> pb) << (2 - lb)) | ((pc >> 3) >> lb)
    if xor:
        bb = (bg | ba << 2) ^ extra                  # bank group first: consecutive granules also avoid tCCD_L
        bg, ba = bb & 3, bb >> 2
    return leaf, leaf * ppl + within, (row << k) | extra, bg, ba


def row_bits(pcs=PCS):
    """Width of the DFI row address (hbm.md H-5): 15 bits plus the folded PC bits of a reduced configuration."""
    return 15 + (PCS // pcs).bit_length() - 1


# ---------------------------------------------------------------- traffic
class Entry:
    __slots__ = ("arr", "wr", "b", "row", "left", "n")

    def __init__(self, arr, wr, b, row, n):
        self.arr, self.wr, self.b, self.row, self.left, self.n = arr, wr, b, row, n, n


def _hbm_fields():
    import os
    import sys
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if os.path.join(root, "isa") not in sys.path:
        sys.path.insert(0, os.path.join(root, "isa"))
    import isa as I
    spec = I.load()
    return lambda off: I.hbm_fields(spec, off)


def gen_seq(rng, streams=8, pc=0, write_frac=0.0, wr_rand=False, pcs=PCS, leaves=4, xor=True):
    """Granules of PC `pc` in stack 0 from `streams` cores each reading a contiguous region in 2 KiB requests, interleaved
    request by request (the order they reach the controller). write_frac: that fraction of granules are writes,
    sequential in their own region, or random granules (wr_rand: KV appends). pcs, leaves: a reduced configuration,
    with the memmap fields folded by `fold_pc` (xor as there)."""
    hf0 = _hbm_fields()
    if pcs == PCS and leaves == 4:
        hf = hf0
    else:
        def hf(off):
            f = dict(hf0(off))
            _, f["pc"], f["row"], f["bg"], f["ba"] = fold_pc(f["pc"], f["row"], pcs, leaves, f["bg"], f["ba"], xor)
            return f
    stack = 36 << 30
    bases = [rng.randrange(0, stack // 2 - (64 << 20)) & ~0x7FFF for _ in range(streams)]
    wbase = rng.randrange(stack // 2, stack - (64 << 20)) & ~0x7FFF
    pos = [0] * streams
    wpos = 0
    while True:
        for s in range(streams):
            for g in range(8):
                off = bases[s] + pos[s] + 256 * g
                f = hf(off)
                if f["pc"] == pc:
                    if write_frac and rng.random() < write_frac:
                        if wr_rand:
                            yield (True, rng.randrange(BANKS), rng.randrange(24576), GRANULE_BURSTS)
                            continue
                        while True:
                            fw = hf(wbase + wpos)
                            wpos += 256
                            if fw["pc"] == pc:
                                break
                        yield (True, bank_index(fw["sid"], fw["bg"], fw["ba"]), fw["row"], GRANULE_BURSTS)
                    else:
                        yield (False, bank_index(f["sid"], f["bg"], f["ba"]), f["row"], GRANULE_BURSTS)
            pos[s] += 2048


def gen_rand(rng, bursts=GRANULE_BURSTS, write_frac=0.0):
    """Uniformly random accesses of `bursts` bursts inside one granule (gathers, sparse KV blocks)."""
    while True:
        yield (rng.random() < write_frac, rng.randrange(BANKS), rng.randrange(24576), bursts)


def gen_rows(rng, row_bytes=264):
    """Random rows of a contiguous table (Engram: 264 B rows). Yields the per-granule pieces that land on PC 0: a row spans
    1-2 granules, each on a different PC, so PC 0 sees about (granules per row / 32) of the rows."""
    n_rows = 1 << 26
    while True:
        r = rng.randrange(n_rows)
        a, z = r * row_bytes, r * row_bytes + row_bytes - 1
        for g in range(a // 256, z // 256 + 1):
            lo, hi = max(a, 256 * g), min(z, 256 * g + 255)
            nb = hi // BURST_B - lo // BURST_B + 1
            if rng.randrange(PCS) == 0:                        # the hash spreads granules uniformly over PCs
                yield (False, rng.randrange(BANKS), rng.randrange(24576), nb)


# ---------------------------------------------------------------- scheduler
class PcSched:
    """The per-PC scheduler of hbm.md H-3, one controller cycle per `step(t)`. It issues through `issue(t, cmd, b, row, ap)`
    and asks `row_ok(t, cmd)` before any row command (the channel's shared row bus; always true for a lone PC)."""

    def __init__(self, gen, d, c, issue=None, row_ok=None, depth=32, wdepth=16, policy="adaptive", refresh="pb",
                 load=None, age_max=1024, warm=4000, rng=None):
        self.gen, self.d, self.c = gen, d, c
        self.issue = issue or d.issue
        self.row_ok = row_ok or (lambda t, cmd: True)
        self.depth, self.wdepth = depth, wdepth or depth
        self.hi, self.lo = self.wdepth * 3 // 4, self.wdepth // 4
        self.policy, self.refresh, self.load, self.age_max, self.warm = policy, refresh, load, age_max, warm
        self.rng = rng or random.Random(1)
        self.rq, self.wq = [], []
        self.backlog = deque()
        self.draining = False
        self.ref_credit = 0.0
        self.ref_round_done = set()
        self.closing = set()
        self.busy = self.acts = self.rd_b = self.wr_b = 0
        self.max_open = 0
        self.lat = []
        self.next_arr = 0.0
        self.pending = None
        self.mean_b = None

    def _arrivals(self, t):
        rq, wq, gen = self.rq, self.wq, self.gen
        if self.load is None:
            while True:
                if self.pending is None:
                    self.pending = next(gen)
                q = wq if self.pending[0] else rq
                if len(q) >= (self.wdepth if self.pending[0] else self.depth):
                    break
                q.append(Entry(t, *self.pending))
                self.pending = None
        else:
            if self.mean_b is None:
                self.mean_b = sum(next(gen)[3] for _ in range(256)) / 256
            while self.next_arr <= t:
                self.backlog.append(Entry(int(self.next_arr), *next(gen)))
                self.next_arr += self.rng.expovariate(self.load / self.mean_b)
            while self.backlog:
                q = wq if self.backlog[0].wr else rq
                if len(q) >= (self.wdepth if self.backlog[0].wr else self.depth):
                    break
                q.append(self.backlog.popleft())

    def _refresh_pb(self, t, b):
        self.issue(t, "REFPB", b)
        self.ref_credit -= 1
        self.ref_round_done.add(b)
        if len(self.ref_round_done) == BANKS:
            self.ref_round_done = set()

    def step(self, t):
        d, c, rq, wq = self.d, self.c, self.rq, self.wq
        ok = self.row_ok
        self._arrivals(t)
        # refresh bookkeeping
        self.ref_credit += 1.0 / (c["REFIPB"] if self.refresh == "pb" else c["REFI"])
        # read / write mode
        if self.draining and (len(wq) <= self.lo and rq or not wq):
            self.draining = False
        elif not self.draining and (len(wq) >= self.hi or (not rq and wq)):
            self.draining = True
        cur = wq if self.draining else rq
        refab_pending = self.refresh == "ab" and self.ref_credit >= 1
        urgent = self.refresh == "pb" and self.ref_credit >= 8
        # column command: oldest ready row hit (first-ready, first-come-first-served)
        if not refab_pending:
            for e in cur:
                if d.open[e.b] != e.row or e.b in self.closing:
                    continue
                cmd = "WR" if e.wr else "RD"
                if d.earliest(cmd, e.b) <= t:
                    e.left -= 1
                    ap = False
                    if e.left == 0:
                        cur.remove(e)
                        if self.policy == "close":
                            ap = True
                        elif self.policy == "adaptive":
                            ap = not any(o.b == e.b and o.row == e.row for o in rq) and \
                                not any(o.b == e.b and o.row == e.row for o in wq)
                        if not e.wr:
                            if t >= self.warm:
                                self.lat.append(t + c["CL"] + c["BL"] + c["PHY_RD"] - e.arr)
                    self.issue(t, cmd, e.b, e.row, ap)
                    if t >= self.warm:
                        self.busy += 1
                        if e.wr:
                            self.wr_b += 1
                        else:
                            self.rd_b += 1
                    break
        self.max_open = max(self.max_open, sum(1 for e in rq if e.left < e.n))
        # row command
        done = False
        if refab_pending:
            if any(r >= 0 for r in d.open):
                for b in range(BANKS):
                    if d.open[b] >= 0 and d.earliest("PRE", b) <= t and ok(t, "PRE"):
                        self.issue(t, "PRE", b)
                        break
            elif d.earliest("REFAB") <= t and ok(t, "REFAB"):
                self.issue(t, "REFAB")
                self.ref_credit -= 1
            done = True
        if not done and urgent:
            cand = [b for b in range(BANKS) if b not in self.ref_round_done]
            b = min(cand, key=lambda x: (d.open[x] >= 0, x))
            if d.open[b] >= 0:
                self.closing.add(b)
                if d.earliest("PRE", b) <= t and ok(t, "PRE"):
                    self.issue(t, "PRE", b)
                    done = True
            elif d.earliest("REFPB", b) <= t and ok(t, "REFPB"):
                self._refresh_pb(t, b)
                self.closing.discard(b)
                done = True
        if not done and cur:
            old = cur[0]
            starving = t - old.arr > self.age_max
            if starving and d.open[old.b] >= 0 and d.open[old.b] != old.row:
                self.closing.add(old.b)
            for e in cur:                           # oldest first; a starving entry's bank is closing (no new hits)
                b = e.b
                if d.open[b] < 0:
                    if d.earliest("ACT", b) <= t and ok(t, "ACT"):
                        self.issue(t, "ACT", b, e.row)
                        self.closing.discard(b)
                        if t >= self.warm:
                            self.acts += 1
                        done = True
                        break
                elif d.open[b] != e.row:
                    hit = b in self.closing or not any(o.b == b and o.row == d.open[b] for o in cur)
                    if hit and d.earliest("PRE", b) <= t and ok(t, "PRE"):
                        self.issue(t, "PRE", b)
                        done = True
                        break
        if not done and self.refresh == "pb" and self.ref_credit >= 1:
            busy_banks = {e.b for e in rq} | {e.b for e in wq}
            for b in range(BANKS):
                if b not in self.ref_round_done and d.open[b] < 0 and b not in busy_banks and \
                        d.earliest("REFPB", b) <= t and ok(t, "REFPB"):
                    self._refresh_pb(t, b)
                    break

    def result(self, cycles_total):
        n = cycles_total - self.warm
        lat = sorted(self.lat)
        return {"util": self.busy / n, "rd": self.rd_b / n, "wr": self.wr_b / n,
                "acts_per_kburst": 1000 * self.acts / max(self.busy, 1),
                "lat_mean": sum(lat) / len(lat) if lat else float("nan"),
                "lat_p99": lat[int(0.99 * (len(lat) - 1))] if lat else float("nan"),
                "refreshes": self.d.ref_count, "cycles": cycles_total, "max_open_granules": self.max_open}


def simulate(gen, cycles_total=40000, warm=4000, depth=32, wdepth=16, policy="adaptive", refresh="pb", hot=False,
             load=None, age_max=1024, t_ns=None, seed=1):
    """Run the per-PC scheduler. gen yields (is_write, bank, row, bursts). load=None: closed loop (queues kept full,
    saturation bandwidth); load=f: open loop, Poisson arrivals at f x the PC's peak burst rate.
    Returns utilization (data-bus busy fraction), latency stats (controller cycles, queue entry to last data), counters."""
    c = cycles(t_ns, hot)
    d = Dram(c, refresh)
    sch = PcSched(gen, d, c, depth=depth, wdepth=wdepth, policy=policy, refresh=refresh, load=load, age_max=age_max,
                  warm=warm, rng=random.Random(seed))
    for t in range(cycles_total):
        sch.step(t)
    return sch.result(cycles_total)


def simulate_channel(gens, cycles_total=40000, warm=4000, act_ck=None, refresh="pb", seed=1, arbiter=True, **kw):
    """Two PC schedulers on one `Channel` (review X-2(b)). Each cycle PC 0 (CK phase 0) decides first, then PC 1 sees
    whether PC 0's command holds the row bus; an ACT of PC 1 holds PC 0's next slot (hbm.md H-3, channel arbiter).
    act_ck overrides ACT_CK (1 = independent PCs). arbiter=False schedules each PC as if it were alone (the checker
    then raises). Returns the two per-PC results."""
    c = cycles(kw.pop("t_ns", None), kw.pop("hot", False))
    if act_ck is not None:
        c["ACT_CK"] = act_ck
    ch = Channel(c, refresh)
    sch = [PcSched(gens[p], ch.pc[p], c, issue=(lambda t, cmd, b=0, row=None, ap=False, p=p: ch.issue(p, t, cmd, b, row, ap)),
                   row_ok=(lambda t, cmd, p=p: ch.ok(p, t, cmd)) if arbiter else None, refresh=refresh, warm=warm,
                   rng=random.Random(seed + p), **kw) for p in (0, 1)]
    for t in range(cycles_total):
        for p in (0, 1):
            sch[p].step(t)
    return [x.result(cycles_total) for x in sch]


PATTERNS = {
    "seq_read": lambda rng: gen_seq(rng),
    "seq_read_5pct_kv_write": lambda rng: gen_seq(rng, write_frac=0.05, wr_rand=True),
    "seq_read_write_3to1": lambda rng: gen_seq(rng, write_frac=0.25),
    "rand_256B_read": lambda rng: gen_rand(rng),
    "rand_64B_read": lambda rng: gen_rand(rng, bursts=2),
    "engram_264B_rows": lambda rng: gen_rows(rng),
}


def run(pattern, seed=1, **kw):
    rng = random.Random(seed)
    return simulate(PATTERNS[pattern](rng), seed=seed, **kw)


def engram_useful(rng=None, row_bytes=264, n=20000):
    """Fraction of transferred bytes that are row bytes, for random 264 B rows read as whole 32 B bursts."""
    rng = rng or random.Random(0)
    tot = 0
    for _ in range(n):
        r = rng.randrange(1 << 26)
        tot += (r * row_bytes + row_bytes - 1) // BURST_B - (r * row_bytes) // BURST_B + 1
    return row_bytes * n / (BURST_B * tot)


def report():
    """docs/reports/hbm_experiments.md (deterministic: fixed seeds)."""
    c = cycles()
    ns = 1e9 / CLK_MC
    L = ["# Phase 2 HBM controller experiments", "",
         "Generated by `make hbm` (`sim/analytic/hbm.py`). Do not edit by hand. Spec and decisions: "
         "[`docs/spec/hbm.md`](../spec/hbm.md).", "",
         "One pseudo-channel (PC) is simulated cycle by cycle at the controller clock (1.2 GHz, one 32 B burst per cycle), "
         "with every command checked by the timing checker `Dram` (a violation aborts the run). "
         f"{(40000 - 4000) / CLK_MC * 1e6:.0f} us per run after a {4000 / CLK_MC * 1e6:.1f} us warm-up, seed 1. "
         "Utilization = fraction of cycles the PC data bus carries a burst (1.0 = 38.4 GB/s per PC, 1.23 TB/s per stack). "
         "Closed loop: the controller queues are kept full (saturation). Defaults: read queue 32, write queue 16 granules, "
         "adaptive page policy, per-bank refresh, <= 85 C.", "",
         "Patterns (addresses through the memmap.md hash, `isa.hbm_fields`):", "",
         "- `seq_read`: 8 cores stream contiguous regions in 2 KiB requests (decode weights, KV scans).",
         "- `seq_read_5pct_kv_write`: the same plus 5% random 256 B writes (decode KV appends).",
         "- `seq_read_write_3to1`: sequential reads and writes 3:1 (prefill activations, spills).",
         "- `rand_256B_read`: random 256 B granules (paged KV gathers, sparse-attention blocks).",
         "- `rand_64B_read`: random 64 B reads (worst case for activations).",
         "- `engram_264B_rows`: random 264 B rows of a contiguous table (DeepSeek Engram; 1-2 granule pieces per row).", ""]
    pats = list(PATTERNS)
    L += ["## H1. Page policy (saturation utilization)", "",
          "| pattern | adaptive | open | close | ACT per 1k bursts (adaptive) |", "|---|---|---|---|---|"]
    for p in pats:
        r = {pol: run(p, policy=pol) for pol in ("adaptive", "open", "close")}
        L.append(f"| {p} | {r['adaptive']['util']:.3f} | {r['open']['util']:.3f} | {r['close']['util']:.3f} | "
                 f"{r['adaptive']['acts_per_kburst']:.0f} |")
    L += ["", f"`engram_264B_rows`: {engram_useful():.3f} of the transferred bytes are row bytes (whole 32 B bursts), "
          "so useful bandwidth is that times the utilization.", "",
          "`rand_64B_read` is bound by the four-activate window: 4 ACTs per tFAW "
          f"({c['FAW']} cycles) x 2 bursts = {8 / c['FAW']:.2f}.", ""]
    L += ["## H2. Refresh", "", "| refresh | temperature | seq_read | rand_256B_read | refresh commands per PC per run |",
          "|---|---|---|---|---|"]
    for ref in ("pb", "ab"):
        for hot in (False, True):
            a, b = run("seq_read", refresh=ref, hot=hot), run("rand_256B_read", refresh=ref, hot=hot)
            L.append(f"| {'per bank (REFpb)' if ref == 'pb' else 'all bank (REFab)'} | {'> 85 C (tREFI / 2)' if hot else '<= 85 C'} | "
                     f"{a['util']:.3f} | {b['util']:.3f} | {a['refreshes'] if ref == 'pb' else a['refreshes'] // BANKS} |")
    L += ["", f"All-bank refresh blocks the PC for tRFCab = {c['RFCAB']} cycles every tREFI = {c['REFI']} cycles "
          f"({c['RFCAB'] / c['REFI']:.1%}, {c['RFCAB'] / (c['REFI'] // 2):.1%} hot) plus the precharge-all drain.", ""]
    L += ["## H3. Queue depth", "", "| read queue | seq_read | rand_256B_read | max read granules in transfer at once |",
          "|---|---|---|---|"]
    for d in (8, 16, 32, 64):
        a, b = run("seq_read", depth=d), run("rand_256B_read", depth=d)
        L.append(f"| {d} | {a['util']:.3f} | {b['util']:.3f} | {max(a['max_open_granules'], b['max_open_granules'])} |")
    L += ["", "| write queue (read queue 32) | seq_read_5pct_kv_write | seq_read_write_3to1 |", "|---|---|---|"]
    for w in (8, 16, 32):
        L.append(f"| {w} | {run('seq_read_5pct_kv_write', wdepth=w)['util']:.3f} | {run('seq_read_write_3to1', wdepth=w)['util']:.3f} |")
    L += ["", "## H4. Latency under load (open loop, `seq_read`)", "",
          "Poisson arrivals at the given fraction of the PC's peak. Latency: granule enters the PC queue -> last burst "
          "out of the PHY (CL + PHY read path included; NoC, CDC and the response packet not included).", "",
          "| offered load | utilization | mean (ns) | p99 (ns) |", "|---|---|---|---|"]
    for ld in (0.5, 0.7, 0.85, 0.95):
        r = run("seq_read", load=ld)
        L.append(f"| {ld:.2f} | {r['util']:.3f} | {r['lat_mean'] * ns:.0f} | {r['lat_p99'] * ns:.0f} |")
    L += ["", "## H5. Sensitivity to tCCD_L (assumed 2.5 ns)", "",
          "| tCCD_L (ns) | cycles | seq_read | rand_256B_read | ceiling 4 bank groups / tCCD_L |", "|---|---|---|---|---|"]
    for v in (1.6, 2.5, 3.3, 4.1):
        cc = cycles({"CCDL": v})["CCDL"]
        L.append(f"| {v:.2f} | {cc} | {run('seq_read', t_ns={'CCDL': v})['util']:.3f} | "
                 f"{run('rand_256B_read', t_ns={'CCDL': v})['util']:.3f} | {min(1.0, BGS / cc):.2f} |")
    L += ["", "## H6. Reduced configurations (review finding I-10)", "",
          "`seq_read` on PC 0 of a reduced stack. The memmap.md PC index is folded onto the PCs present (`fold_pc`): the R-11 fold "
          "moves the unused PC bits into the row only, the I-10 fold also XORs them into the bank group and bank.", "",
          "| config | PCs | leaves | DFI row bits | R-11 fold (row only) | I-10 fold (row + bank XOR) |", "|---|---|---|---|---|---|"]
    for name, pcs, lv in (("FULL", 32, 4), ("MID", 8, 4), ("MID", 8, 2), ("TINY", 4, 2)):
        u = [simulate(gen_seq(random.Random(1), pcs=pcs, leaves=lv, xor=x))["util"] for x in (False, True)]
        L.append(f"| {name} | {pcs} | {lv} | {row_bits(pcs)} | {u[0]:.3f} | {u[1]:.3f} |")
    L += ["", "## H7. Shared channel command bus (review finding X-2(b))", "",
          "Two PCs of one channel with independent traffic, scheduled together (`simulate_channel`): PC p drives CK phase p, "
          "and an ACT that occupies ACT_CK = 2 CK holds the other PC's next slot. ACT_CK = 1 is the independent-PC "
          "assumption. Mean utilization of the two PCs.", "",
          "| pattern | ACT_CK = 1 | ACT_CK = 2 | ACT per 1k bursts |", "|---|---|---|---|"]
    chan_pats = {"seq_read": lambda p: gen_seq(random.Random(1 + p), pc=p), "rand_256B_read": lambda p: gen_rand(random.Random(1 + p)),
                 "rand_64B_read": lambda p: gen_rand(random.Random(1 + p), bursts=2)}
    for name, g in chan_pats.items():
        r = {ck: simulate_channel([g(0), g(1)], act_ck=ck) for ck in (1, 2)}
        L.append(f"| {name} | {sum(x['util'] for x in r[1]) / 2:.3f} | {sum(x['util'] for x in r[2]) / 2:.3f} | "
                 f"{sum(x['acts_per_kburst'] for x in r[2]) / 2:.0f} |")
    L += ["", "## Timing table used (controller cycles at 1.2 GHz)", "",
          "| parameter | ns | cycles |", "|---|---|---|"]
    for k, v in T_NS.items():
        L.append(f"| t{k} | {v:g} | {c[k]} |")
    for k in ("BL", "CCDS", "CCDR", "PHY_RD", "RC", "RTW", "WTR_S", "WTR_L", "WRP"):
        L.append(f"| {k} (derived or fixed) | {c[k] * ns:.2f} | {c[k]} |")
    L.append(f"| ACT_CK (fixed, CK slots on the channel row bus) | {c['ACT_CK'] * ns / 2:.2f} | {c['ACT_CK']} CK |")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    import os
    import sys
    import time
    if "--report" in sys.argv:
        out = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "reports", "hbm_experiments.md")
        with open(out, "w") as f:
            f.write(report())
        print("wrote docs/reports/hbm_experiments.md")
        sys.exit(0)
    for p in PATTERNS:
        t0 = time.time()
        r = run(p)
        print(f"{p:26s} util {r['util']:.3f} acts/kburst {r['acts_per_kburst']:6.1f} lat {r['lat_mean']:6.1f} "
              f"p99 {r['lat_p99']:6.0f}  {time.time() - t0:.1f}s")
