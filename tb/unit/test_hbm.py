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


@pytest.mark.parametrize("seq, bad", [
    ([(0, "ACT", B0, 5)], (C["RCDRD"] - 1, "RD", B0, 5)),                       # ACT -> RD
    ([(0, "ACT", B0, 5)], (C["RCDWR"] - 1, "WR", B0, 5)),                       # ACT -> WR
    ([(0, "ACT", B0, 5)], (C["RAS"] - 1, "PRE", B0, None)),                     # ACT -> PRE
    ([(0, "ACT", B0, 5), (C["RAS"], "PRE", B0, None)], (C["RAS"] + C["RP"] - 1, "ACT", B0, 6)),     # PRE -> ACT
    ([(0, "ACT", B0, 5)], (C["RRDL"] - 1, "ACT", B1, 5)),                       # tRRD_L
    ([(0, "ACT", B0, 5)], (C["RRDS"] - 1, "ACT", B2, 5)),                       # tRRD_S
    ([(0, "ACT", B0, 5), (10, "ACT", B1, 5), (100, "RD", B0, 5)], (100 + C["CCDL"] - 1, "RD", B1, 5)),   # tCCD_L
    ([(0, "ACT", B0, 5), (10, "ACT", B3, 5), (100, "RD", B0, 5)], (100 + C["CCDR"] - 1, "RD", B3, 5)),   # SID switch
    ([(0, "ACT", B0, 5), (10, "ACT", B2, 5), (100, "WR", B0, 5)], (100 + C["WTR_S"] - 1, "RD", B2, 5)),  # tWTR_S
    ([(0, "ACT", B0, 5), (10, "ACT", B1, 5), (100, "WR", B0, 5)], (100 + C["WTR_L"] - 1, "RD", B1, 5)),  # tWTR_L
    ([(0, "ACT", B0, 5), (10, "ACT", B2, 5), (100, "RD", B0, 5)], (100 + C["RTW"] - 1, "WR", B2, 5)),    # tRTW
    ([(0, "ACT", B0, 5), (100, "WR", B0, 5)], (100 + C["WRP"] - 1, "PRE", B0, None)),                   # write recovery
    ([(0, "ACT", B0, 5)], (100, "RD", B0, 6)),                                  # wrong row
    ([(0, "ACT", B0, 5)], (100, "ACT", B0, 6)),                                 # ACT to an open bank
    ([], (100, "RD", B0, 5)),                                                   # RD to a closed bank
    ([(0, "ACT", B0, 5)], (500, "REFPB", B0, None)),                            # refresh of an open bank
    ([(0, "REFPB", B0, None)], (C["RFCPB"] - 1, "ACT", B0, 5)),                 # tRFCpb
    ([(0, "REFPB", B0, None)], (C["RREFD"] - 1, "REFPB", B2, None)),            # REFpb spacing
    ([(0, "REFPB", B0, None), (300, "REFPB", B2, None)], (600, "REFPB", B0, None)),     # same bank twice in a round
    ([(0, "ACT", B0, 5)], (0, "ACT", B2, 5)),                                   # two row commands in a cycle
])
def test_checker_catches(seq, bad):
    d = fresh()
    for t, cmd, b, row in seq:
        d.issue(t, cmd, b, row)
    with pytest.raises(H.Violation):
        t, cmd, b, row = bad
        d.issue(t, cmd, b, row)


def test_checker_accepts_at_exact_gap():
    d = fresh()
    d.issue(0, "ACT", B0, 5)
    d.issue(C["RRDL"], "ACT", B1, 5)
    t1 = C["RRDL"] + C["RCDRD"]                     # B1 becomes readable after its own tRCD
    d.issue(C["RCDRD"], "RD", B0, 5)
    d.issue(t1, "RD", B1, 5)
    d.issue(t1 + C["CCDL"], "RD", B0, 5)            # same bank group again after exactly tCCD_L
    d.issue(t1 + C["CCDL"] + C["RTW"], "WR", B1, 5)  # read to write turnaround


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
    """hbm.md H-1: one-to-one, and a 2 KiB block (8 consecutive granules, one leaf in the full map) stays on one leaf."""
    seen = set()
    for pc in range(32):
        for row in range(4):
            leaf, phys, xrow = H.fold_pc(pc, row, pcs, leaves)
            assert 0 <= phys < pcs and leaf == phys // (pcs // leaves)
            seen.add((phys, xrow))
    assert len(seen) == 32 * 4
    hf = H._hbm_fields()
    for base in range(0, 1 << 22, 2048 * 37):
        blk = base & ~2047
        f = [hf(blk + 256 * g) for g in range(8)]
        assert len({H.fold_pc(x["pc"], x["row"], pcs, leaves)[0] for x in f}) == 1
    if (pcs, leaves) == (32, 4):
        assert all(H.fold_pc(pc, 9) == (pc >> 3, pc, 9) for pc in range(32))


def test_report_exists():
    p = os.path.join(ROOT, "docs", "reports", "hbm_experiments.md")
    s = open(p).read()
    for h in ("H1.", "H2.", "H3.", "H4.", "H5."):
        assert h in s
