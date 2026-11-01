"""Die NoC model (docs/spec/noc.md): topology, routing, link loads, and a channel-dependency-graph deadlock check.

Topology (die coordinates, x = column, y = row):
- core routers (x, y), x = 1..8, y = 1..4, one per core tile, 2D mesh, `PLANES` identical planes of `LINK_B` B/cycle/direction;
- leaf (edge) routers hang off one boundary core router each and never forward:
  north (x, 0) and south (x, 5): HBM, 4 leaves per stack (stack 0 = north x 1-4, 1 = north x 5-8, 2 = south x 1-4, 3 = south x 5-8);
  east (9, y): D2D sub-link y;  west (0, y): C2C ports (2 per leaf).
Routing: dimension order, REQ (and multicast) X then Y, RSP Y then X. Packets that arrived over a D2D or C2C link use
class X virtual channels, all others class L. A packet crosses at most one link (cross-rail traffic is two DMA operations).
"""
from collections import defaultdict

COLS, ROWS = 8, 4
PLANES = 2
LINK_B = 128            # bytes per cycle per direction per plane
FLIT_B = 128
GRANULE = 256           # bytes per packet payload (2 flits); every read response and write packet carries at most this
BURST = 2048            # one read request or one write ack covers up to 2 KB (8 packets to one HBM leaf or one SRAM target)
C2C_PORTS = 8           # per die, 2 per west leaf
CLOCK = 1.5e9
STACK_BPC = 1024 * 9.6e9 / 8 / CLOCK     # 819.2 B/cycle per HBM3e stack
STACKS = 4

CORES = [(x, y) for y in range(1, ROWS + 1) for x in range(1, COLS + 1)]


def hbm_leaves(stack):
    xs = range(1, 5) if stack in (0, 2) else range(5, 9)
    y = 0 if stack in (0, 1) else ROWS + 1
    return [(x, y) for x in xs]


def d2d_leaf(row):
    return (COLS + 1, row)


def c2c_leaf(row):
    return (0, row)


def is_leaf(n):
    x, y = n
    return x in (0, COLS + 1) or y in (0, ROWS + 1)


def attach(n):
    """Core router a node hangs off (itself for core routers)."""
    x, y = n
    return (min(max(x, 1), COLS), min(max(y, 1), ROWS))


def stack_of_core(c):
    """Quadrant affinity: the stack whose leaves are nearest (memmap.md 'stack-affine placement')."""
    x, y = c
    return (0 if x <= 4 else 1) + (0 if y <= 2 else 2)


def _dor(a, b, order):
    path = [a]
    x, y = a
    for dim in order:
        if dim == "X":
            while x != b[0]:
                x += 1 if b[0] > x else -1
                path.append((x, y))
        else:
            while y != b[1]:
                y += 1 if b[1] > y else -1
                path.append((x, y))
    return path


def route(src, dst, order):
    """Router nodes visited from src to dst (leaves included at the ends)."""
    p = [src] if is_leaf(src) else []
    p += _dor(attach(src), attach(dst), order)
    if is_leaf(dst):
        p.append(dst)
    return p


ORDER = {"REQ": "XY", "RSP": "YX", "MC": "XY"}


# ---------------------------------------------------------------- link loads
def _add(load, path, bpc):
    for u, v in zip(path, path[1:]):
        load[(u, v)] += bpc


def read_traffic(load, core, leaf, data_bpc):
    """A read stream: data on RSP (leaf -> core) plus one header-only REQ flit per 2 KB burst (core -> leaf)."""
    _add(load, route(leaf, core, ORDER["RSP"]), data_bpc)
    _add(load, route(core, leaf, ORDER["REQ"]), data_bpc * FLIT_B / BURST)


def write_traffic(load, core, leaf, data_bpc):
    """A write stream: data on REQ (core -> leaf) plus one ack flit per 2 KB burst on RSP."""
    _add(load, route(core, leaf, ORDER["REQ"]), data_bpc)
    _add(load, route(leaf, core, ORDER["RSP"]), data_bpc * FLIT_B / BURST)


def hbm_read(load, affine=True, frac=1.0):
    """Every core reads its share of full HBM bandwidth (x frac). affine: from its quadrant's stack, else from all stacks."""
    per_core = STACKS * STACK_BPC * frac / len(CORES)
    for c in CORES:
        stacks = [stack_of_core(c)] if affine else range(STACKS)
        leaves = [l for s in stacks for l in hbm_leaves(s)]
        for l in leaves:
            read_traffic(load, c, l, per_core / len(leaves))


def d2d_bidir(load, die_bpc):
    """Every core sends and receives die_bpc / 32 to / from the other die through the D2D sub-link of its own row."""
    per = die_bpc / len(CORES)
    for c in CORES:
        l = d2d_leaf(c[1])
        write_traffic(load, c, l, per)                    # outgoing writes (collective payload)
        _add(load, route(l, c, ORDER["REQ"]), per)        # incoming writes from the peer die (class X, REQ order)
        _add(load, route(c, l, ORDER["RSP"]), per * FLIT_B / BURST)     # their acks


def c2c_bidir(load, die_bpc):
    per = die_bpc / len(CORES)
    for c in CORES:
        l = c2c_leaf(c[1])
        write_traffic(load, c, l, per)
        _add(load, route(l, c, ORDER["REQ"]), per)
        _add(load, route(c, l, ORDER["RSP"]), per * FLIT_B / BURST)


def utilization(load):
    cap = PLANES * LINK_B
    return {k: v / cap for k, v in load.items()}


def max_util(load):
    u = utilization(load)
    k = max(u, key=u.get)
    return u[k], k


def scenarios():
    """Peak-rate scenarios used in noc.md. Returns {name: (max utilization, busiest link)}."""
    out = {}
    for name, fn in [
        ("hbm_read_affine", lambda L: hbm_read(L, True)),
        ("hbm_read_uniform", lambda L: hbm_read(L, False)),
        ("d2d_1.5TBs", lambda L: d2d_bidir(L, 1.5e12 / CLOCK)),
        ("c2c_0.8TBs", lambda L: c2c_bidir(L, 0.8e12 / CLOCK)),
        ("d2d_1.4TBs", lambda L: d2d_bidir(L, 1.4e12 / CLOCK)),
        ("hbm_affine+d2d_1.5TBs", lambda L: (hbm_read(L, True), d2d_bidir(L, 1.5e12 / CLOCK))),
    ]:
        load = defaultdict(float)
        fn(load)
        out[name] = max_util(load)
    return out


def d2d_max_bpc():
    """Largest per-direction D2D rate (bytes/cycle per die) the mesh carries with no other traffic."""
    load = defaultdict(float)
    d2d_bidir(load, 1.0)
    return 1.0 / max_util(load)[0]


# ---------------------------------------------------------------- deadlock: channel dependency graph
def _chan(sysnode, u, v, vn, cls):
    return (sysnode, u, v, vn, cls)


def _hops(sysnode, path, vn, cls):
    return [_chan(sysnode, u, v, vn, cls) for u, v in zip(path, path[1:])]


def c2c_port_rows(own, peer, packages):
    """West-leaf rows that hold the ports from package `own` to package `peer` (ports trunked evenly over the peers,
    port j on leaf row 1 + j // 2). With 2 packages every row reaches the peer; with 4 or more each peer is on one row."""
    peers = [q for q in range(packages) if q != own]
    trunk = max(1, C2C_PORTS // len(peers))
    i = peers.index(peer)
    return sorted({1 + j // 2 for j in range(i * trunk, min((i + 1) * trunk, C2C_PORTS))})


def _c2c_rows(p1, p2, row, packages):
    out_rows = c2c_port_rows(p1, p2, packages)
    out = row if row in out_rows else out_rows[0]
    in_rows = c2c_port_rows(p2, p1, packages)
    k = out_rows.index(out)
    return out, in_rows[min(k, len(in_rows) - 1)]


def system_path(src, dst, vn, classes=True, allow_cross_rail=False, packages=2):
    """Channels from endpoint src to endpoint dst; endpoints are (pkg, die, node). None if the route is not allowed.

    Rules (noc.md): at most one link per packet (D2D inside a package, C2C between same-index dies); the D2D
    sub-link is the one on the source's row, the C2C port is one of the trunk to that peer (the source's row if
    possible); packets after a link use class X. allow_cross_rail=True models the rejected alternative: a packet
    to the other rail of another package crosses D2D and is then forwarded over C2C, still in class X."""
    (p1, d1, n1), (p2, d2, n2) = src, dst
    order = ORDER[vn]
    L, X = ("L", "X") if classes else ("L", "L")
    if (p1, d1) == (p2, d2):
        return _hops((p1, d1), route(n1, n2, order), vn, L)
    row = attach(n1)[1]
    if p1 == p2:                                   # D2D: dies face each other rotated 180 degrees
        out, inn = d2d_leaf(row), d2d_leaf(ROWS + 1 - row)
        return (_hops((p1, d1), route(n1, out, order), vn, L) + [_chan(("D2D", p1, d1, row), out, inn, vn, "-")]
                + _hops((p2, d2), route(inn, n2, order), vn, X))
    if d1 == d2:                                   # C2C on the same rail
        ro, ri = _c2c_rows(p1, p2, row, packages)
        out, inn = c2c_leaf(ro), c2c_leaf(ri)
        return (_hops((p1, d1), route(n1, out, order), vn, L) + [_chan(("C2C", p1, p2, d1, ro), out, inn, vn, "-")]
                + _hops((p2, d2), route(inn, n2, order), vn, X))
    if not allow_cross_rail:
        return None
    r2 = ROWS + 1 - row                            # arrive in the sibling die, then forward over its C2C port
    out1, inn1 = d2d_leaf(row), d2d_leaf(r2)
    ro, ri = _c2c_rows(p1, p2, r2, packages)
    out2, inn2 = c2c_leaf(ro), c2c_leaf(ri)
    return (_hops((p1, d1), route(n1, out1, order), vn, L) + [_chan(("D2D", p1, d1, row), out1, inn1, vn, "-")]
            + _hops((p1, d2), route(inn1, out2, order), vn, X) + [_chan(("C2C", p1, p2, d2, ro), out2, inn2, vn, "-")]
            + _hops((p2, d2), route(inn2, n2, order), vn, X))


def endpoints(packages=2, dies=2):
    eps = []
    for p in range(packages):
        for d in range(dies):
            eps += [(p, d, c) for c in CORES]
            eps += [(p, d, l) for s in range(STACKS) for l in hbm_leaves(s)]
    return eps


def build_cdg(packages=2, classes=True, split_vn=True, allow_cross_rail=False, multicast=True, hbm_forwards=False,
              dma_forwards=False, link_vn=True, scales_after_data=False):
    """Channel dependency graph of the whole system: REQ and RSP routes for every allowed endpoint pair,
    protocol edges (a request is consumed only by emitting its response), and XY-tree multicasts.

    split_vn=False puts requests and responses on the same channels (to show protocol deadlock).
    hbm_forwards=True makes HBM leaves forward remote requests (to show why endpoints must not chain requests).
    dma_forwards=True lets a DMA sink read data only by sending it on as a write (a GLOBAL -> GLOBAL copy with no local
    buffer, review finding I-1): the response channel into a core then waits on that core's request injection.
    link_vn=False gives each D2D / C2C link one queue for both VNs (review finding I-7): the link layer then dequeues in
    arrival order, so a blocked REQ holds the RSP behind it.
    scales_after_data=True is the converter rule before review finding V-1: a block-format data response cannot be
    converted (so cannot eject) until its scale response arrives, and the scale is another response that may be anywhere
    on a response route into the same core, also behind the data in the same channel."""
    g = defaultdict(set)
    eps = endpoints(packages)
    cores = [e for e in eps if not is_leaf(e[2])]
    rsp = "RSP" if split_vn else "REQ"

    def link(path):
        for a, b in zip(path, path[1:]):
            g[a].add(b)

    rsp_in, req_out = defaultdict(set), defaultdict(set)
    for s in cores:                       # requests come from cores (DMA); HBM leaves only respond
        for t in eps:
            if s == t:
                continue
            q = system_path(s, t, "REQ", classes, allow_cross_rail, packages)
            if not q:
                continue
            r = system_path(t, s, "RSP", classes, allow_cross_rail, packages)
            if r is None:
                continue
            if not split_vn:
                r = [c[:3] + ("REQ",) + c[4:] for c in r]
            link(q)
            link(r)
            g[q[-1]].add(r[0])            # protocol edge: consuming the request needs the response channel
            if scales_after_data:         # rejected (V-1): ejecting the data waits for a scale on this route
                g[r[-1]].update(r)
            rsp_in[s].add(r[-1])
            req_out[s].add(q[0])
    if dma_forwards:                      # rejected (I-1): read data leaves the core again as a write before it can sink
        for s in cores:
            for a in rsp_in[s]:
                g[a].update(req_out[s])
    if hbm_forwards:                      # rejected: a leaf consumes a request by issuing one to the sibling die
        for p in range(packages):
            for d in range(2):
                for st in range(STACKS):
                    for l in hbm_leaves(st):
                        fwd = system_path((p, d, l), (p, 1 - d, (1, 1)), "REQ", classes, False, packages)
                        link(fwd)
                        for s in cores:
                            q = system_path(s, (p, d, l), "REQ", classes, False, packages)
                            if q:
                                g[q[-1]].add(fwd[0])
    if multicast:
        for p in range(packages):
            for d in range(2):
                for s in CORES:
                    # all rectangles is O(n^4); the union over 'whole die' and 'own row/column' covers every edge type
                    for rect in ([(1, COLS), (1, ROWS)], [(1, COLS), (s[1], s[1])], [(s[0], s[0]), (1, ROWS)]):
                        dsts = [(x, y) for x in range(rect[0][0], rect[0][1] + 1) for y in range(rect[1][0], rect[1][1] + 1)]
                        for t in dsts:
                            if t != s:
                                link(_hops((p, d), route(s, t, "XY"), "REQ", "L"))
    if not link_vn:                       # rejected (I-7): REQ and RSP share each link's TX / RX queue
        def m(c):
            return (c[0], c[1], c[2], "ANY", c[4]) if isinstance(c[0][0], str) else c
        g2 = defaultdict(set)
        for a, bs in g.items():
            g2[m(a)].update(m(b) for b in bs)
        g = g2
    return g


# ---------------------------------------------------------------- write-ack coalescing (noc.md section 2, review X-3)
def ack_coalescer(arrivals, table=8, flush=64, use_blen=True):
    """Write-ack coalescing at one target. arrivals: (cycle, source, burst id, bytes, burst total bytes) per WR packet.
    Rules: one entry per (source, burst id) counts accepted bytes. The entry is acked when its count reaches the burst
    total (use_blen), when the table is full and a new burst needs an entry (the oldest entry is acked early), and when it
    has been idle for `flush` cycles (flush=None: never). Accepting a packet never waits for table state.
    use_blen=False, flush=None is the rule before the fix (ack only on eviction). Returns the acks as
    (cycle, source, burst id, bytes) and the entries still open at the end (bytes accepted but never acknowledged)."""
    arrivals = sorted(arrivals)
    entries = {}                          # (src, burst) -> [bytes, last arrival cycle, allocation order]
    acks = []
    order = 0
    end = (arrivals[-1][0] if arrivals else 0) + (flush or 0) + 1
    i = 0
    for t in range(end + 1):
        if flush is not None:
            for k in [k for k, e in entries.items() if t - e[1] >= flush]:
                acks.append((t, *k, entries.pop(k)[0]))
        while i < len(arrivals) and arrivals[i][0] == t:
            _, src, bid, n, blen = arrivals[i]
            i += 1
            k = (src, bid)
            if k not in entries:
                if len(entries) >= table:                     # evict the oldest entry with its partial count
                    old = min(entries, key=lambda x: entries[x][2])
                    acks.append((t, *old, entries.pop(old)[0]))
                entries[k] = [0, t, order]
                order += 1
            e = entries[k]
            e[0] += n
            e[1] = t
            if use_blen and e[0] >= blen:
                acks.append((t, *k, entries.pop(k)[0]))
    return acks, entries


def find_cycle(g):
    """A dependency cycle as a list of channels, or None (iterative DFS)."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = defaultdict(int)
    parent = {}
    for root in list(g):
        if color[root]:
            continue
        stack = [(root, iter(g[root]))]
        color[root] = GREY
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                color[node] = BLACK
                stack.pop()
                continue
            if color[nxt] == GREY:
                cyc = [nxt]
                for n, _ in reversed(stack):
                    cyc.append(n)
                    if n == nxt:
                        break
                return cyc[::-1]
            if color[nxt] == WHITE:
                color[nxt] = GREY
                parent[nxt] = node
                stack.append((nxt, iter(g.get(nxt, ()))))
    return None


if __name__ == "__main__":
    for k, (u, link) in scenarios().items():
        print(f"{k:24s} max link utilization {u:5.2f}  at {link}")
    print(f"D2D max {d2d_max_bpc():.0f} B/cycle = {d2d_max_bpc() * CLOCK / 1e12:.2f} TB/s per direction")
    for kw in [dict(), dict(packages=4), dict(classes=False), dict(split_vn=False), dict(allow_cross_rail=True, packages=4),
               dict(hbm_forwards=True), dict(dma_forwards=True), dict(link_vn=False), dict(scales_after_data=True)]:
        g = build_cdg(**kw)
        c = find_cycle(g)
        print(kw or "baseline", "channels", len(g), "edges", sum(map(len, g.values())), "cycle" if c else "acyclic",
              (c[:4] if c else ""))
