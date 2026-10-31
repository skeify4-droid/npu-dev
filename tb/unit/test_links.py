"""Link layer and collective schedules (docs/spec/d2d_c2c.md)."""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "sim"), os.path.join(ROOT, "sw")]
from analytic import links as K, hw as HW  # noqa: E402


def test_flit_format():
    assert sum(K.LINK_HDR.values()) == 64
    assert K.LINK_FLIT_BITS == 1248 and K.LINK_FLIT_B == 156
    assert 2 ** K.LINK_HDR["seq"] >= 2 * max(K.buffers(k)["replay_flits"] for k in ("D2D", "C2C"))   # go-back-N window


def test_bandwidth_meets_frozen_numbers():
    hw = HW.make()
    assert K.d2d_sizing()["payload_TBs"] * 1e12 >= hw["d2d_bw"]
    assert K.c2c_sizing()["port_payload_GBs"] * 1e9 >= hw["c2c_bw_pkg"] / 16
    assert K.c2c_sizing(5)["port_payload_GBs"] * 1e9 < hw["c2c_bw_pkg"] / 16       # why 6 lanes


def test_buffers_cover_round_trip():
    for k in ("D2D", "C2C"):
        b = K.buffers(k)
        rate = 1.0 if k == "D2D" else K.c2c_sizing()["flits_per_cycle"]
        assert b["replay_flits"] >= rate * b["rtt_cycles"]
        assert b["rx_shared_flits"] >= rate * b["rtt_cycles"]
        assert b["rx_reserved_flits"] >= 2 * 2          # one maximal packet (2 flits) per VN, always


def test_bottom_up_latency_below_frozen():
    hw = HW.make()
    assert K.step_latency("D2D") < hw["d2d_lat"]
    assert K.step_latency("C2C") < hw["c2c_lat"]


def test_schedules():
    F = K.frozen()
    for n in (6400, 1 << 20, 1 << 27):
        assert K.allreduce_hier(n, 2, F) == K.allreduce_ring(n, 2, F)          # same algorithm on one package
        for g in (4, 8, 16):
            assert K.allreduce_hier(n, g, F) <= K.allreduce_ring(n, g, F)
            # the staged all-to-all moves at least the bytes that must leave the package, in two C2C + D2D rounds
            assert K.a2a_staged(n, g, F) >= n * (g - 2) / g / F["c2c_bw_die"]
            assert K.a2a_staged(n, g, F) >= 2 * (F["c2c_lat"] + F["d2d_lat"])


def test_error_rate_needs_crc():
    assert K.error_rates(1e-15)["mean_s_between_system"] < 60


def test_report_exists():
    s = open(os.path.join(ROOT, "docs", "reports", "links_experiments.md")).read()
    for h in ("L1.", "L2.", "L3.", "L4.", "L5.", "L6."):
        assert h in s
