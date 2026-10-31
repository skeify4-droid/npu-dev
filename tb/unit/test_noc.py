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
                                dict(hbm_forwards=True)])
def test_rejected_designs_deadlock(kw):
    assert N.find_cycle(N.build_cdg(**kw)) is not None
