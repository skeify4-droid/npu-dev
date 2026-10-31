"""D2D / C2C link layer and collective schedules (docs/spec/d2d_c2c.md).

- Link flit format, payload efficiency and PHY sizing (D2D: UCIe-Advanced-class modules; C2C: 224G-class SerDes lanes).
- Replay and receive buffer sizing from the round trip.
- Error rates with and without the link CRC.
- Bottom-up latency of one collective step, against the frozen roofline values (arch.md: 0.5 us D2D, 2 us C2C).
- Collective schedules (hierarchical direct all-reduce, rail-staged all-to-all) against the roofline formulas.
- Sensitivity of decode at the serving point to those schedules and latencies (information; arch.md is not changed).
"""
import math

CLOCK = 1.5e9
# ---------------------------------------------------------------- flit
NOC_DATA_BITS, NOC_HDR_BITS = 1024, 128          # noc.md section 2: header travels in a sideband
LINK_HDR = {"valid": 1, "head": 1, "tail": 1, "vn": 1, "seq": 12, "ack_seq": 12, "ack_type": 2,
            "credit_req": 4, "credit_rsp": 4, "credit_shared": 6, "rsvd": 20}
CRC_BITS = 32                                    # CRC-32C over header + data
LINK_FLIT_BITS = NOC_DATA_BITS + NOC_HDR_BITS + sum(LINK_HDR.values()) + CRC_BITS
LINK_FLIT_B = LINK_FLIT_BITS / 8                 # 156 B
DATA_B = NOC_DATA_BITS / 8                       # 128 B
BURST_FLITS = 16                                 # data flits per 2 KiB write burst (one ack each, noc.md)
LANES_PER_SUBLINK = 2                            # one per NoC plane
D2D_SUBLINKS = 4
C2C_PORTS = 8                                    # per die


def payload_eff(kind="write"):
    """Payload bytes per link-flit byte for a stream in one direction with the reverse direction carrying the same stream
    (symmetric collectives): each 2 KiB burst = 16 data flits + 1 ack flit for the opposite stream's burst."""
    if kind == "write":
        return DATA_B * BURST_FLITS / ((BURST_FLITS + 1) * LINK_FLIT_B)
    if kind == "read":    # one header-only request per 2 KiB in the other direction; responses are data flits
        return DATA_B * BURST_FLITS / ((BURST_FLITS + 1) * LINK_FLIT_B)
    raise ValueError(kind)


# ---------------------------------------------------------------- PHYs
UCIE_A_X64 = 64 * 32e9 / 8          # one UCIe-Advanced x64 module at 32 GT/s: 256 GB/s per direction
SERDES_LANE = 200e9 / 8             # 224G-class PAM4 lane after FEC: 25 GB/s per direction (assumption)
C2C_LANES_PER_PORT = 6


def d2d_sizing():
    lane_raw = LINK_FLIT_B * CLOCK                           # one flit per cycle per lane
    sub_raw = LANES_PER_SUBLINK * lane_raw
    modules = math.ceil(sub_raw / UCIE_A_X64)
    return {"lane_raw_GBs": lane_raw / 1e9, "sublink_raw_GBs": sub_raw / 1e9, "modules_per_sublink": modules,
            "module_util": sub_raw / (modules * UCIE_A_X64), "modules_per_die": modules * D2D_SUBLINKS,
            "payload_TBs": D2D_SUBLINKS * sub_raw * payload_eff() / 1e12}


def c2c_sizing(lanes=C2C_LANES_PER_PORT):
    raw = lanes * SERDES_LANE
    return {"lanes": lanes, "port_raw_GBs": raw / 1e9, "port_payload_GBs": raw * payload_eff() / 1e9,
            "flits_per_cycle": raw / LINK_FLIT_B / CLOCK, "lanes_per_die": lanes * C2C_PORTS}


# ---------------------------------------------------------------- latency (core cycles at 1.5 GHz)
PHY_ONE_WAY_NS = {"D2D": 4.0, "C2C": 150.0}      # (A) UCIe-A adapter + PHY; SerDes + RS FEC + ~1 m of cable


def path_latency(link):
    """One-way latency of a put-with-signal from a core's DMA to the consumer core's semaphore wake-up, unloaded."""
    hops_to_edge = 5.5                           # mean over a row: core x = 1..8 to the leaf at x = 0 or 9
    parts = {
        "DMA issue, descriptor, first packet": 16,
        "NI + source mesh to the edge leaf": round(hops_to_edge * 3 + 2),
        "bridge TX (flit, sequence, CRC)": 4,
        f"{link} PHY + flight": round(PHY_ONE_WAY_NS[link] * CLOCK / 1e9),
        "bridge RX (CRC check, buffer, inject class X)": 4,
        "destination mesh": round(hops_to_edge * 3 + 2),
        "target write + semaphore add": 6,
        "consumer wake-up (semaphore wait to start)": 4,
    }
    return parts


def step_latency(link, sync_cycles=64):
    """One collective step: the one-way path plus a skew allowance for the 32 cores of a die finishing the previous step."""
    return (sum(path_latency(link).values()) + sync_cycles) / CLOCK


def rtt_cycles(link):
    """Flit round trip for replay and credits: TX bridge -> PHY -> RX bridge -> ack/credit back on a reverse flit."""
    one = 4 + round(PHY_ONE_WAY_NS[link] * CLOCK / 1e9) + 4
    return 2 * one + 4


def buffers(link):
    """Replay buffer and receive buffer (flits) per lane (D2D) or per port (C2C) for full rate on one VN."""
    if link == "D2D":
        rate = 1.0                                           # flits per cycle per lane
        units = D2D_SUBLINKS * LANES_PER_SUBLINK
    else:
        rate = c2c_sizing()["flits_per_cycle"]
        units = C2C_PORTS
    rtt = rtt_cycles(link)
    replay = 2 ** math.ceil(math.log2(rate * rtt * 1.25))
    rx_shared = replay
    rx_reserved = 2 * 4                                      # 4 flits reserved per VN (REQ, RSP): two max packets
    per = (replay + rx_shared + rx_reserved) * LINK_FLIT_B
    return {"rtt_cycles": rtt, "replay_flits": replay, "rx_shared_flits": rx_shared, "rx_reserved_flits": rx_reserved,
            "KiB_per_unit": per / 1024, "units_per_die": units, "KiB_per_die": per * units / 1024}


# ---------------------------------------------------------------- errors
def error_rates(ber=1e-15, packages=8):
    """Bit errors per second without CRC, per die and per system, at the raw PHY rate (both links fully busy)."""
    d2d_bits = D2D_SUBLINKS * d2d_sizing()["sublink_raw_GBs"] * 1e9 * 8
    c2c_bits = C2C_PORTS * c2c_sizing()["port_raw_GBs"] * 1e9 * 8
    per_die = (d2d_bits + c2c_bits) * ber
    return {"d2d_Tbps": d2d_bits / 1e12, "c2c_Tbps": c2c_bits / 1e12, "errors_per_s_die": per_die,
            "errors_per_s_system": per_die * 2 * packages, "mean_s_between_system": 1 / (per_die * 2 * packages)}


# ---------------------------------------------------------------- collectives
def frozen(hw=None):
    from . import hw as HW
    h = hw or HW.make()
    return {"d2d_bw": h["d2d_bw"], "d2d_lat": h["d2d_lat"], "c2c_bw_die": h["c2c_bw_pkg"] / h["dies_per_pkg"],
            "c2c_lat": h["c2c_lat"]}


def bottom_up(margin=1.0):
    f = frozen()
    return dict(f, d2d_lat=step_latency("D2D") * margin, c2c_lat=step_latency("C2C") * margin)


def allreduce_ring(n_bytes, g, L):
    """The roofline's formula (roofline._comm): a ring over g dies on the slowest link class."""
    if g <= 1:
        return 0.0
    if g <= 2:
        bw, lat = L["d2d_bw"], L["d2d_lat"]
    else:
        bw, lat = L["c2c_bw_die"], L["c2c_lat"]
    return 2 * (g - 1) / g * n_bytes / bw + 2 * (g - 1) * lat


def allreduce_hier(n_bytes, g, L):
    """d2d_c2c.md schedule: D2D reduce-scatter (halves), direct reduce-scatter over C2C on each rail (every peer package
    is one hop), direct all-gather over C2C, D2D all-gather. 4 latency steps for any g > 2."""
    if g <= 1:
        return 0.0
    t = 2 * (n_bytes / 2 / L["d2d_bw"] + L["d2d_lat"])
    if g > 2:
        P = g // 2
        t += 2 * ((P - 1) / P * n_bytes / 2 / L["c2c_bw_die"] + L["c2c_lat"])
    return t


def a2a_roofline(n_bytes, g, L, dpp=2):
    """roofline._comm all-to-all: on-package share over D2D, off-package over C2C in parallel, 2 latency rounds."""
    if g <= 1:
        return 0.0
    off = 0.0 if g <= dpp else 1 - dpp / g
    lat = L["d2d_lat"] if g <= dpp else L["c2c_lat"]
    on = n_bytes * (1 - 1 / g) * (1 - off)
    offb = n_bytes * (1 - 1 / g) * off
    return max(on / L["d2d_bw"], offb / L["c2c_bw_die"] if off else 0.0) + 2 * lat


def a2a_staged(n_bytes, g, L):
    """d2d_c2c.md schedule: counts first, then data; other-rail traffic goes C2C to the same-rail die of the destination
    package, which forwards it over D2D (staged, pipelined in 64 KiB chunks). n_bytes = what one die sends in total."""
    if g <= 1:
        return 0.0
    if g <= 2:
        return n_bytes / 2 / L["d2d_bw"] + 2 * L["d2d_lat"]
    P = g // 2
    share = n_bytes / g
    c2c = 2 * (P - 1) * share                     # same-rail and other-rail destinations in other packages
    d2d = share + (P - 1) * share                 # own sibling + forwarded for the sibling
    chunk = min(64 * 1024, share)
    t_bw = max(c2c / L["c2c_bw_die"], d2d / L["d2d_bw"]) + chunk / L["d2d_bw"]
    return t_bw + 2 * (L["c2c_lat"] + L["d2d_lat"])           # counts round + data round, each C2C then D2D


def table_collectives():
    """Rows for the report: message sizes that occur in decode and prefill."""
    F, B = frozen(), bottom_up()
    cases = [
        ("HC-5 mixer all-reduce, 64 tokens x 25 FP32", "allreduce", 64 * 25 * 4),
        ("TP all-reduce, decode, 64 tokens x 7168 BF16", "allreduce", 64 * 7168 * 2),
        ("TP all-reduce, prefill chunk 8192 x 7168 BF16", "allreduce", 8192 * 7168 * 2),
        ("MoE dispatch+combine, decode, 64 tokens x 8 x 7168 x 3 B", "a2a", 64 * 8 * 7168 * 3),
        ("MoE dispatch+combine, prefill 8192 tokens x 8 x 7168 x 3 B", "a2a", 8192 * 8 * 7168 * 3),
    ]
    rows = []
    for name, kind, n in cases:
        for g in (2, 4, 8, 16):
            if kind == "allreduce":
                r, h, hb = allreduce_ring(n, g, F), allreduce_hier(n, g, F), allreduce_hier(n, g, B)
            else:
                r, h, hb = a2a_roofline(n, g, F), a2a_staged(n, g, F), a2a_staged(n, g, B)
            rows.append((name, g, n, r, h, hb))
    return rows


# ---------------------------------------------------------------- decode sensitivity
def decode_sensitivity():
    """Serving-point decode (sweep.SERVE, fp8 deployment, the frozen package count) with the roofline's comm model
    replaced by these schedules, with frozen latencies and with bottom-up latencies (x1 and x2)."""
    from . import roofline as R, sweep as SW, hw as HW
    hw = HW.make()
    orig = R._comm

    def make_comm(L):
        def comm(hw_, kind, group, bytes_per_die):
            if group <= 1 or bytes_per_die <= 0:
                return 0.0
            return allreduce_hier(bytes_per_die, group, L) if kind == "allreduce" else a2a_staged(bytes_per_die, group, L)
        return comm
    rows = []
    for key, d in SW.load_all().items():
        n, base = SW.serving_packages(d, hw)
        wd = SW.deploy_dtype(d, "fp8")
        out = {"model": d["name"], "packages": n, "base": base["tok_s_user"]}
        for tag, L in (("sched_frozen", frozen(hw)), ("bottom_up_x2", bottom_up(2.0)), ("bottom_up", bottom_up(1.0))):
            R._comm = make_comm(L)
            try:
                r = R.best_decode(d, hw, n, SW.SERVE["batch"], SW.SERVE["ctx"], weight_dtype=wd)
            finally:
                R._comm = orig
            out[tag] = r["tok_s_user"]
        rows.append(out)
    return rows


# ---------------------------------------------------------------- report
def report():
    ds, cs = d2d_sizing(), c2c_sizing()
    F = frozen()
    L = ["# Phase 2 link and collective experiments", "",
         "Generated by `make links` (`sim/analytic/links.py`). Do not edit by hand. Spec and decisions: "
         "[`docs/spec/d2d_c2c.md`](../spec/d2d_c2c.md). Values marked (A) in the spec are assumptions.", "",
         "## L1. Link flit and PHY sizing", "",
         f"Link flit: {NOC_DATA_BITS} data + {NOC_HDR_BITS} NoC header + {sum(LINK_HDR.values())} link header + "
         f"{CRC_BITS} CRC = {LINK_FLIT_BITS} bits ({LINK_FLIT_B:.0f} B). Payload efficiency of a symmetric write stream "
         f"(16 data flits + 1 ack flit per 2 KiB): {payload_eff():.3f}.", "",
         "| link | unit | raw per direction | units per die | payload per die per direction |", "|---|---|---|---|---|",
         f"| D2D | sub-link = 2 lanes x 1 flit/cycle = {ds['modules_per_sublink']} UCIe-A x64 modules at 32 GT/s "
         f"({ds['module_util']:.0%} used) | {ds['sublink_raw_GBs']:.0f} GB/s | {D2D_SUBLINKS} sub-links, "
         f"{ds['modules_per_die']} modules | {ds['payload_TBs']:.2f} TB/s (frozen: {F['d2d_bw'] / 1e12:.2f}) |",
         f"| C2C | port = {cs['lanes']} lanes x {SERDES_LANE * 8 / 1e9:.0f} Gb/s | {cs['port_raw_GBs']:.0f} GB/s | "
         f"{C2C_PORTS} ports, {cs['lanes_per_die']} lanes | {C2C_PORTS * cs['port_payload_GBs'] / 1e3:.2f} TB/s "
         f"(frozen: {F['c2c_bw_die'] / 1e12:.2f}) |", "",
         "C2C lanes per port vs payload per port (frozen: 100 GB/s):", "",
         "| lanes | raw GB/s | payload GB/s |", "|---|---|---|"]
    for n in (4, 5, 6, 8):
        c = c2c_sizing(n)
        L.append(f"| {n} | {c['port_raw_GBs']:.0f} | {c['port_payload_GBs']:.0f} |")
    L += ["", "## L2. Buffers (replay + receive, per lane or port)", "",
          "| link | round trip (cycles) | replay (flits) | RX shared + reserved (flits) | KiB per unit | units per die | KiB per die |",
          "|---|---|---|---|---|---|---|"]
    for k in ("D2D", "C2C"):
        b = buffers(k)
        L.append(f"| {k} | {b['rtt_cycles']} | {b['replay_flits']} | {b['rx_shared_flits']} + {b['rx_reserved_flits']} | "
                 f"{b['KiB_per_unit']:.1f} | {b['units_per_die']} | {b['KiB_per_die']:.0f} |")
    L += ["", "## L3. Bit errors without a link CRC", "",
          "| raw BER | D2D Tb/s per die | C2C Tb/s per die | errors/s per die | mean time between errors, 8 packages |",
          "|---|---|---|---|---|"]
    for ber in (1e-15, 1e-12):
        e = error_rates(ber)
        L.append(f"| {ber:.0e} | {e['d2d_Tbps']:.1f} | {e['c2c_Tbps']:.1f} | {e['errors_per_s_die']:.3g} | "
                 f"{e['mean_s_between_system']:.3g} s |")
    L += ["", "## L4. Latency of one collective step (bottom-up, unloaded)", "",
          "| component | D2D (cycles) | C2C (cycles) |", "|---|---|---|"]
    pd, pc = path_latency("D2D"), path_latency("C2C")
    for (k1, v1), (k2, v2) in zip(pd.items(), pc.items()):
        L.append(f"| {k1.replace('D2D ', '')} | {v1} | {v2} |")
    L += [f"| one-way total | {sum(pd.values())} | {sum(pc.values())} |",
          "| + skew allowance for 32 cores | 64 | 64 |",
          f"| **step** | **{step_latency('D2D') * 1e6:.3f} us** | **{step_latency('C2C') * 1e6:.3f} us** |",
          f"| frozen roofline value (arch.md) | {F['d2d_lat'] * 1e6:.1f} us | {F['c2c_lat'] * 1e6:.1f} us |", "",
          "## L5. Collective schedules vs the roofline formulas", "",
          "Time per collective for the bytes one die contributes. `roofline`: `roofline._comm` (ring all-reduce; "
          "all-to-all with no rail staging) at frozen latencies. `schedule`: d2d_c2c.md schedules at frozen latencies. "
          "`schedule, bottom-up`: same at the L4 latencies. g = dies in the group (2 per package).", "",
          "| collective | g | bytes per die | roofline (us) | schedule (us) | schedule, bottom-up (us) |",
          "|---|---|---|---|---|---|"]
    for name, g, n, r, h, hb in table_collectives():
        L.append(f"| {name} | {g} | {n / 1e6:.3f} MB | {r * 1e6:.1f} | {h * 1e6:.1f} | {hb * 1e6:.1f} |")
    L += ["", "## L6. Decode at the serving point with these schedules (information; arch.md unchanged)", "",
          "Tokens/s/user at 64 x 128K on the frozen package count, best mapping re-searched per column.", "",
          "| model | packages | frozen model | schedules, frozen latency | schedules, 2x bottom-up latency | schedules, bottom-up latency |",
          "|---|---|---|---|---|---|"]
    for r in decode_sensitivity():
        L.append(f"| {r['model']} | {r['packages']} | {r['base']:.0f} | {r['sched_frozen']:.0f} ({r['sched_frozen'] / r['base'] - 1:+.0%}) | "
                 f"{r['bottom_up_x2']:.0f} ({r['bottom_up_x2'] / r['base'] - 1:+.0%}) | {r['bottom_up']:.0f} ({r['bottom_up'] / r['base'] - 1:+.0%}) |")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    import os
    out = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "reports", "links_experiments.md")
    with open(out, "w") as f:
        f.write(report())
    print("wrote docs/reports/links_experiments.md")
