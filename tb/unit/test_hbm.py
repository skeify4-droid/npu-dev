"""HBM controller model (docs/spec/hbm.md): the timing checker catches every rule class, the scheduler never violates it,
and the spec's timing table matches the model."""
import os
import random
import re
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "sim"), os.path.join(ROOT, "sw")]
from analytic import hbm as H  # noqa: E402

C = H.cycles()
B0 = H.bank_index(0, 0, 0)
B1 = H.bank_index(0, 0, 1)          # same bank group as B0
B2 = H.bank_index(0, 1, 0)          # other bank group, same SID
B3 = H.bank_index(1, 0, 0)          # other SID


def fresh():
    d = H.Dram(C)
    d.check_refresh = lambda t: None    # unit sequences start at t = 0 and do not refresh
    return d


def test_spec_timing_table_matches_model():
    rows = {}
    for line in open(os.path.join(ROOT, "docs", "spec", "hbm.md")):
        m = re.match(r"^\| t([A-Z]+) \| ([\d.]+) \| (\d+) \|", line)
        if m:
            rows[m.group(1)] = (float(m.group(2)), int(m.group(3)))
    assert set(rows) == set(H.T_NS), set(rows) ^ set(H.T_NS)
    for k, (ns, cyc) in rows.items():
        assert ns == H.T_NS[k] and cyc == C[k], k


# One isolating sequence per timing rule: (rule, setup, command). `rule` names the rules() entry the sequence isolates;
# the command at (gap - 1) must be rejected and at exactly the gap accepted. Commands are (cycle, cmd, bank, row); "chan"
# cases are (cycle, pc, cmd, bank, row) on a Channel. The rule-removal test below uses the same table.
REFPB_ALL_BUT_B0 = [(10 * i, "REFPB", b, None) for i, b in enumerate(range(1, H.BANKS))]
T_LAST = 10 * (H.BANKS - 1)                     # REFpb B0 completes the round here; the round restarts after it
RULE_CASES = [
    (("ACT", "ACT", "bg", "RRDL"), [(0, "ACT", B0, 5)], ("RRDL", 0, "ACT", B1, 5)),
    (("ACT", "ACT", "pc", "RRDS"), [(0, "ACT", B0, 5)], ("RRDS", 0, "ACT", B2, 5)),
    (("ACT", "RD", "bank", "RCDRD"), [(0, "ACT", B0, 5)], ("RCDRD", 0, "RD", B0, 5)),
    (("ACT", "WR", "bank", "RCDWR"), [(0, "ACT", B0, 5)], ("RCDWR", 0, "WR", B0, 5)),
    (("ACT", "PRE", "bank", "RAS"), [(0, "ACT", B0, 5)], ("RAS", 0, "PRE", B0, None)),
    (("PRE", "ACT", "bank", "RP"), [(0, "ACT", B0, 5), (44, "PRE", B0, None)], ("RP", 44, "ACT", B0, 6)),     # after tRC
    (("PRE", "REFPB", "bank", "RP"), [(0, "ACT", B0, 5), (34, "PRE", B0, None)], ("RP", 34, "REFPB", B0, None)),
    (("PRE", "REFAB", "bank", "RP"), [(0, "ACT", B0, 5), (34, "PRE", B0, None)], ("RP", 34, "REFAB", 0, None)),
    (("RD", "PRE", "bank", "RTP"), [(0, "ACT", B0, 5), (40, "RD", B0, 5)], ("RTP", 40, "PRE", B0, None)),
    (("WR", "PRE", "bank", "WRP"), [(0, "ACT", B0, 5), (100, "WR", B0, 5)], ("WRP", 100, "PRE", B0, None)),
    (("ACT", "PREA", "bank", "RAS"), [(0, "ACT", B0, 5)], ("RAS", 0, "PREA", 0, None)),
    (("RD", "PREA", "bank", "RTP"), [(0, "ACT", B0, 5), (40, "RD", B0, 5)], ("RTP", 40, "PREA", 0, None)),
    (("WR", "PREA", "bank", "WRP"), [(0, "ACT", B0, 5), (100, "WR", B0, 5)], ("WRP", 100, "PREA", 0, None)),
    (("RD", "WR", "pc", "RTW"), [(0, "ACT", B0, 5), (10, "ACT", B2, 5), (100, "RD", B0, 5)], ("RTW", 100, "WR", B2, 5)),
    (("WR", "RD", "bg", "WTR_L"), [(0, "ACT", B0, 5), (10, "ACT", B1, 5), (100, "WR", B0, 5)], ("WTR_L", 100, "RD", B1, 5)),
    (("WR", "RD", "pc", "WTR_S"), [(0, "ACT", B0, 5), (10, "ACT", B2, 5), (100, "WR", B0, 5)], ("WTR_S", 100, "RD", B2, 5)),
    (("REFPB", "ACT", "bank", "RFCPB"), [(0, "REFPB", B0, None)], ("RFCPB", 0, "ACT", B0, 5)),
    (("REFPB", "REFPB", "bank", "RFCPB"), REFPB_ALL_BUT_B0 + [(T_LAST, "REFPB", B0, None)], ("RFCPB", T_LAST, "REFPB", B0, None)),
    (("REFPB", "REFPB", "pc", "RREFD"), [(0, "REFPB", B0, None)], ("RREFD", 0, "REFPB", B2, None)),
    (("REFPB", "ACT", "pc", "RREFDA"), [(0, "REFPB", B0, None)], ("RREFDA", 0, "ACT", B2, 5)),
    (("ACT", "REFPB", "bg", "RRDL"), [(0, "ACT", B0, 5)], ("RRDL", 0, "REFPB", B1, None)),
    (("ACT", "REFPB", "pc", "RRDS"), [(0, "ACT", B0, 5)], ("RRDS", 0, "REFPB", B2, None)),
    (("REFAB", "ACT", "pc", "RFCAB"), [(0, "REFAB", 0, None)], ("RFCAB", 0, "ACT", B0, 5)),
    (("REFAB", "REFPB", "pc", "RFCAB"), [(0, "REFAB", 0, None)], ("RFCAB", 0, "REFPB", B0, None)),
    (("REFAB", "REFAB", "pc", "RFCAB"), [(0, "REFAB", 0, None)], ("RFCAB", 0, "REFAB", 0, None)),
    (("REFPB", "REFAB", "pc", "RFCPB"), [(0, "REFPB", B0, None)], ("RFCPB", 0, "REFAB", 0, None)),
    (("RD", "RD", "bg", "CCDL"), [(0, "ACT", B0, 5), (10, "ACT", B1, 5), (100, "RD", B0, 5)], ("CCDL", 100, "RD", B1, 5)),
    (("RD", "RD", "xsid", "CCDR"), [(0, "ACT", B0, 5), (10, "ACT", B3, 5), (100, "RD", B0, 5)], ("CCDR", 100, "RD", B3, 5)),
    (("WR", "WR", "bg", "CCDL"), [(0, "ACT", B0, 5), (10, "ACT", B1, 5), (100, "WR", B0, 5)], ("CCDL", 100, "WR", B1, 5)),
    (("WR", "WR", "xsid", "CCDR"), [(0, "ACT", B0, 5), (10, "ACT", B3, 5), (100, "WR", B0, 5)], ("CCDR", 100, "WR", B3, 5)),
]
# Channel bus (X-2(b)): an ACT of PC 1 in cycle 10 (CK slot 21) holds slot 22, PC 0's slot in cycle 11. PC 0's first
# legal slot is then 24 (cycle 12): the boundary case.
def _chan_args(n):
    return (n, B2, 5) if n == "ACT" else (n, B2 if n in ("PRE", "REFPB") else 0, None)


CHAN_CASES = [(("ACT", n, "chan", "ACT_CK"), [(10, 1, "ACT", B0, 5)], (11, 0, *_chan_args(n)), (12, 0, *_chan_args(n)))
              for n in H.ROW_CMDS]
# Redundant under the current timing table: implied by other rules (each condition is asserted below).
REDUNDANT = {("ACT", "ACT", "bank", C["RC"]): C["RC"] <= C["RAS"] + C["RP"],           # ACT -> PRE (tRAS) -> ACT (tRP)
             ("RD", "RD", "sid", C["CCDS"]): C["CCDS"] <= 1,                          # one column command per cycle
             ("WR", "WR", "sid", C["CCDS"]): C["CCDS"] <= 1,
             ("REFPB", "ACT", "bg", C["RRDL"]): C["RRDL"] <= C["RREFDA"]}             # wider scope, longer gap
STATE_CASES = [
    ([(0, "ACT", B0, 5)], (100, "RD", B0, 6)),                                  # wrong row
    ([(0, "ACT", B0, 5)], (100, "ACT", B0, 6)),                                 # ACT to an open bank
    ([], (100, "RD", B0, 5)),                                                   # RD to a closed bank
    ([(0, "ACT", B0, 5)], (500, "REFPB", B0, None)),                            # refresh of an open bank
    ([(0, "ACT", B0, 5)], (500, "REFAB", 0, None)),                             # all-bank refresh with a bank open
    ([(0, "REFPB", B0, None), (300, "REFPB", B2, None)], (600, "REFPB", B0, None)),     # same bank twice in a round
    ([(0, "ACT", B0, 5)], (0, "ACT", B2, 5)),                                   # two row commands in a cycle
    ([(0, "ACT", B0, 5), (40, "ACT", B2, 5), (80, "PREA", 0, None)], (200, "RD", B2, 5)),   # PREA closed every bank
    ([], (5, "MRS", 0, None)),                                                  # unknown commands are rejected
    ([], (5, "PDE", 0, None)),
]


def _issue(d, cmd):
    t, c, b, row = cmd
    d.issue(t, c, b, row)


def _raises(fn):
    try:
        fn()
    except H.Violation:
        return True
    return False


def _pc_case(setup, nxt, at, rules_=None):
    d = H.Dram(C, rules_=rules_)
    d.check_refresh = lambda t: None
    for x in setup:
        _issue(d, x)
    gap, t0, cmd, b, row = nxt
    return lambda: _issue(d, (t0 + C[gap] + at, cmd, b, row))


def _chan_case(setup, cmd, rules_=None):
    ch = H.Channel(C, rules_=rules_)
    for d in ch.pc:
        d.check_refresh = lambda t: None
    for t, p, c, b, row in setup:
        ch.issue(p, t, c, b, row)
    t, p, c, b, row = cmd
    return lambda: ch.issue(p, t, c, b, row)


def _cases_raise(rules_):
    """For each rule case, whether the violating command is rejected under rules_."""
    out = [_raises(_pc_case(setup, nxt, -1, rules_)) for _, setup, nxt in RULE_CASES]
    out += [_raises(_chan_case(setup, bad, rules_)) for _, setup, bad, _ in CHAN_CASES]
    return out


@pytest.mark.parametrize("name, setup, nxt", RULE_CASES, ids=[" ".join(r) for r, _, _ in RULE_CASES])
def test_rule_violation_and_exact_gap(name, setup, nxt):
    assert _raises(_pc_case(setup, nxt, -1)), "one cycle early must be rejected"
    _pc_case(setup, nxt, 0)()                                     # exactly at the gap is legal


@pytest.mark.parametrize("name, setup, bad, good", CHAN_CASES, ids=[r[1] for r, *_ in CHAN_CASES])
def test_channel_bus(name, setup, bad, good):
    """X-2(b): a 2-CK ACT on one PC holds the shared row bus in the other PC's next slot."""
    assert _raises(_chan_case(setup, bad))
    _chan_case(setup, good)()


@pytest.mark.parametrize("seq, bad", STATE_CASES)
def test_checker_catches_state(seq, bad):
    d = fresh()
    for x in seq:
        _issue(d, x)
    with pytest.raises(H.Violation):
        _issue(d, bad)


def test_rule_cases_cover_rules():
    """Every rule in rules() is either isolated by a case above or listed as redundant, with its condition true."""
    named = {(p, n, s, C[g]) for (p, n, s, g), *_ in RULE_CASES + CHAN_CASES}
    R = set(H.rules(C))
    assert all(REDUNDANT.values())
    assert R == named | set(REDUNDANT), (R - named - set(REDUNDANT), named - R)


def test_rule_removal_is_caught():
    """Mutation test (review X-1): deleting any non-redundant rule from rules() makes a checker case pass that must fail.
    Deleting a redundant rule changes nothing."""
    R = H.rules(C)
    assert all(_cases_raise(R))
    missed = []
    for r in R:
        caught = not all(_cases_raise([x for x in R if x != r]))
        if r in REDUNDANT:
            assert not caught, f"{r} is listed as redundant but a case depends on it"
        elif not caught:
            missed.append(r)
    assert not missed, missed


def test_checker_accepts_at_exact_gap():
    d = fresh()
    d.issue(0, "ACT", B0, 5)
    d.issue(C["RRDL"], "ACT", B1, 5)
    t1 = C["RRDL"] + C["RCDRD"]                     # B1 becomes readable after its own tRCD
    d.issue(C["RCDRD"], "RD", B0, 5)
    d.issue(t1, "RD", B1, 5)
    d.issue(t1 + C["CCDL"], "RD", B0, 5)            # same bank group again after exactly tCCD_L
    d.issue(t1 + C["CCDL"] + C["RTW"], "WR", B1, 5)  # read to write turnaround


def test_prea_then_act_waits_rp():
    d = fresh()
    d.issue(0, "ACT", B0, 5)
    d.issue(10, "ACT", B3, 5)
    tp = 10 + C["RAS"]                              # PREA waits for the later ACT's tRAS
    with pytest.raises(H.Violation):
        d.issue(tp - 1, "PREA")
    d.issue(tp, "PREA")
    with pytest.raises(H.Violation):
        d.issue(tp + C["RP"] - 1, "ACT", B0, 6)
    d.issue(tp + C["RP"], "ACT", B0, 6)


def test_four_activate_window():
    d = fresh()
    banks = [H.bank_index(s, g, 0) for s, g in ((0, 0), (0, 1), (0, 2), (0, 3), (1, 0))]
    t = 0
    for b in banks[:4]:
        d.issue(t, "ACT", b, 1)
        t += C["RRDL"]
    with pytest.raises(H.Violation):
        d.issue(t, "ACT", banks[4], 1)           # 5th ACT inside tFAW
    d.issue(C["FAW"], "ACT", banks[4], 1)


def test_auto_precharge_closes_the_bank():
    d = fresh()
    d.issue(0, "ACT", B0, 5)
    d.issue(C["RCDRD"], "RD", B0, 5, ap=True)
    tp = max(C["RCDRD"] + C["RTP"], C["RAS"])
    with pytest.raises(H.Violation):
        d.issue(tp + C["RP"] - 1, "ACT", B0, 6)
    d.issue(tp + C["RP"], "ACT", B0, 6)


def test_refresh_lag_detected():
    d = H.Dram(C)
    d.issue(0, "ACT", B0, 1)
    with pytest.raises(H.Violation):
        d.issue(int(C["REFIPB"] * 10), "PRE", B0)        # 10 REFpb due, none issued


@pytest.mark.parametrize("pattern", list(H.PATTERNS))
def test_scheduler_never_violates(pattern):
    r = H.run(pattern, cycles_total=8000, warm=1000)     # Dram.issue raises on any violation
    assert 0.3 < r["util"] <= 1.0


def test_streaming_meets_frozen_efficiency():
    """arch.md assumes 0.85 sustained HBM efficiency; the controller alone must leave margin above it."""
    assert H.run("seq_read")["util"] >= 0.95
    assert H.run("rand_256B_read")["util"] >= 0.90
    assert H.run("seq_read", refresh="ab")["util"] < H.run("seq_read")["util"] - 0.05      # why per-bank refresh


def test_per_bank_refresh_keeps_schedule():
    r = H.run("seq_read", cycles_total=20000, warm=1000)
    assert r["refreshes"] >= int(20000 / C["REFIPB"]) - 8


def test_hash_spreads_streams_over_bank_groups():
    """The scheduler needs at least 3 bank groups in its queue under tCCD_L = 3 cycles (hbm.md H-6)."""
    g = H.gen_seq(random.Random(0))
    first = [next(g) for _ in range(32)]
    assert len({b // H.BAS for _, b, _, _ in first}) >= 4


@pytest.mark.parametrize("pcs, leaves", [(32, 4), (8, 4), (8, 2), (4, 2), (2, 1)])
def test_reduced_config_fold(pcs, leaves):
    """hbm.md H-1: one-to-one, and a 2 KiB block (8 consecutive granules, one leaf in the full map) stays on one leaf.
    I-10: the granules of a 2 KiB block that share a physical PC fall in different banks, and the row fits the DFI width."""
    seen = set()
    for pc in range(32):
        for row in range(4):
            for bg in range(4):
                for ba in range(4):
                    leaf, phys, xrow, bg2, ba2 = H.fold_pc(pc, row, pcs, leaves, bg, ba)
                    assert 0 <= phys < pcs and leaf == phys // (pcs // leaves)
                    seen.add((phys, xrow, bg2, ba2))
    assert len(seen) == 32 * 4 * 16
    assert (24575 << (H.row_bits(pcs) - 15)) | ((1 << (H.row_bits(pcs) - 15)) - 1) < 1 << H.row_bits(pcs)
    hf = H._hbm_fields()
    for base in range(0, 1 << 22, 2048 * 37):
        blk = base & ~2047
        f = [H.fold_pc(x["pc"], x["row"], pcs, leaves, x["bg"], x["ba"]) for x in (hf(blk + 256 * g) for g in range(8))]
        assert len({x[0] for x in f}) == 1
        by_pc = {}
        for leaf, phys, xrow, bg, ba in f:
            by_pc.setdefault(phys, set()).add((bg, ba))
        assert all(len(v) == 8 // len(by_pc) for v in by_pc.values())        # distinct banks per physical PC
    if (pcs, leaves) == (32, 4):
        assert all(H.fold_pc(pc, 9, bg=1, ba=2) == (pc >> 3, pc, 9, 1, 2) for pc in range(32))


@pytest.mark.parametrize("pcs, leaves", [(8, 4), (8, 2), (4, 2)])
def test_reduced_config_streaming(pcs, leaves):
    """I-10: MID and TINY stream within 0.02 of FULL (without the bank XOR they reach 0.55-0.72)."""
    full = H.simulate(H.gen_seq(random.Random(1)), cycles_total=20000)["util"]
    red = H.simulate(H.gen_seq(random.Random(1), pcs=pcs, leaves=leaves), cycles_total=20000)["util"]
    old = H.simulate(H.gen_seq(random.Random(1), pcs=pcs, leaves=leaves, xor=False), cycles_total=20000)["util"]
    assert red >= full - 0.02
    assert old < full - 0.2


def test_channel_scheduler():
    """X-2(b): two PC schedulers on a shared channel never violate the channel checker with the arbiter, do without it,
    and lose little bandwidth to the shared row bus."""
    gens = lambda: [H.gen_seq(random.Random(1), pc=0), H.gen_seq(random.Random(2), pc=1)]
    shared = H.simulate_channel(gens(), cycles_total=12000, warm=2000)          # Channel.issue raises on any violation
    indep = H.simulate_channel(gens(), cycles_total=12000, warm=2000, act_ck=1)
    assert all(r["util"] >= i["util"] - 0.02 for r, i in zip(shared, indep))
    with pytest.raises(H.Violation):
        H.simulate_channel([H.gen_rand(random.Random(1), bursts=2), H.gen_rand(random.Random(2), bursts=2)],
                           cycles_total=4000, warm=500, arbiter=False)


def test_report_exists():
    p = os.path.join(ROOT, "docs", "reports", "hbm_experiments.md")
    s = open(p).read()
    for h in ("H1.", "H2.", "H3.", "H4.", "H5.", "H6.", "H7."):
        assert h in s
