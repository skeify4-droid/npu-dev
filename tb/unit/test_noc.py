"""NoC model (docs/spec/noc.md): routes, peak link loads, and deadlock freedom of the channel dependency graph."""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "sim"), os.path.join(ROOT, "sw")]
from analytic import noc as N  # noqa: E402


def test_routes_are_minimal_and_mirrored():
    for s in N.CORES:
        for t in N.CORES + [l for st in range(4) for l in N.hbm_leaves(st)]:
            q = N.route(s, t, "XY")
            r = N.route(t, s, "YX")
            dist = abs(N.attach(s)[0] - N.attach(t)[0]) + abs(N.attach(s)[1] - N.attach(t)[1]) + N.is_leaf(t)
            assert len(q) - 1 == dist
            assert r == q[::-1]                  # a response retraces its request
            assert not any(N.is_leaf(n) for n in q[1:-1])   # leaves never forward


def test_peak_loads():
    sc = N.scenarios()
    assert sc["hbm_read_affine"][0] <= 0.81
    assert sc["hbm_read_uniform"][0] <= 0.90
    assert sc["c2c_0.8TBs"][0] <= 0.60
    assert sc["d2d_1.4TBs"][0] <= 1.0
    assert N.d2d_max_bpc() * N.CLOCK >= 1.4e12


@pytest.mark.parametrize("packages", [2, 4])
def test_baseline_deadlock_free(packages):
    assert N.find_cycle(N.build_cdg(packages=packages)) is None


@pytest.mark.parametrize("kw", [dict(classes=False), dict(split_vn=False), dict(allow_cross_rail=True, packages=4),
                                dict(hbm_forwards=True),
                                dict(dma_forwards=True),           # review I-1: GLOBAL -> GLOBAL copy without a local sink
                                dict(link_vn=False),               # review I-7: one link queue for REQ and RSP
                                dict(scales_after_data=True)])     # review V-1: data waits for its scale response
def test_rejected_designs_deadlock(kw):
    assert N.find_cycle(N.build_cdg(**kw)) is not None


def test_scale_wait_cycle_is_on_the_response_route():
    """Review V-1: with the pre-fix converter rule the cycle runs through RSP channels into one core (the data waits at the
    last channel for a scale behind it). Scales staged before the data remove those edges: the baseline is acyclic."""
    c = N.find_cycle(N.build_cdg(scales_after_data=True))
    assert c and all(ch[3] == "RSP" for ch in c)


def _bursts(rng, n_src, bursts_per_src, spread):
    """WR packets of 2 KiB-or-shorter bursts, 256 B packets, arriving in random order within `spread` cycles."""
    arr, totals = [], {}
    for s in range(n_src):
        for b in range(bursts_per_src):
            blen = 256 * rng.randint(1, 8) - rng.choice([0, 0, 32])     # some bursts end on a partial packet
            totals[(s, b)] = blen
            t0 = rng.randrange(spread)
            left = blen
            while left:
                n = min(256, left)
                arr.append((t0 + rng.randrange(40), s, b, n, blen))
                left -= n
    return arr, totals


@pytest.mark.parametrize("table", [1, 2, 8])
@pytest.mark.parametrize("load", ["low", "high"])
def test_ack_coalescing_completes(table, load):
    """Review X-3: with the burst total in every WR (`blen`) and an idle flush, every burst is acknowledged exactly, at
    any table size and load, and no accepted byte waits longer than the flush interval after the last packet."""
    import random
    rng = random.Random(table * 7 + (load == "high"))
    arr, totals = _bursts(rng, n_src=3 if load == "low" else 24, bursts_per_src=2 if load == "low" else 12,
                          spread=2000 if load == "low" else 200)
    acks, left = N.ack_coalescer(arr, table=table, flush=64)
    assert not left
    got = {}
    for t, s, b, n in acks:
        got[(s, b)] = got.get((s, b), 0) + n
        assert t <= max(a[0] for a in arr if (a[1], a[2]) == (s, b)) + 64
    assert got == totals


def test_ack_coalescing_without_trigger_hangs():
    """The rule before the fix (ack only when the full table evicts): at low load the bursts are never acknowledged."""
    import random
    arr, totals = _bursts(random.Random(3), n_src=2, bursts_per_src=2, spread=500)
    acks, left = N.ack_coalescer(arr, table=8, flush=None, use_blen=False)
    assert not acks and set(left) == set(totals)


def test_scales_first_edge_stays_acyclic():
    """Review W-1: the DMA's own scales-first wait (a core's data read waits for its scale response to sink) is in the
    baseline as request-out -> response-in edges of every core. It adds edges and stays acyclic, because no response
    waits on a request (the rejected I-1 forward is the case where one does)."""
    g0 = N.build_cdg(scales_first=False)
    g1 = N.build_cdg()
    assert sum(map(len, g1.values())) > sum(map(len, g0.values()))
    assert N.find_cycle(g1) is None
    assert any(a[3] == "REQ" and b[3] == "RSP" for a, bs in g1.items() for b in bs if b not in g0.get(a, ()))
