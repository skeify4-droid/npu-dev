# Network on chip and deadlock strategy (Phase 2)

Status: **reviewed 2026-11-01** (Phase 2 self-review, [spec_review.md](../reports/spec_review.md)).
- Model and checks: `sim/analytic/noc.py` (`python -m analytic.noc`) and `tb/unit/test_noc.py`.
- Floorplan and blocks: [microarch.md](microarch.md) §1. Addresses: [memmap.md](memmap.md).

## 1. Topology

- **Core mesh:** 8 x 4 routers, one per core tile at (x, y), x = 1..8, y = 1..4.
  - Six ports: N, S, E, W, core, GSRAM.
  - Boundary ports connect to leaf routers.
- **Leaves:** 24 per die. Each has one mesh port plus endpoints, and a leaf never forwards traffic.

  | Leaves | Location | Endpoints |
  |---|---|---|
  | N1-N8, S1-S8 | north and south edges | HBM: 4 per stack, 8 pseudo-channels each |
  | E1-E4 | east edge | D2D sub-links |
  | W1-W4 | west edge | C2C, 2 ports each; W2 also the CP, W3 the host interface |

- **Two planes *(P2)*:** two identical, independent meshes with 128 B/cycle links in each direction.
  - Each network interface and leaf has a port on both planes.
  - A packet travels entirely on one plane, chosen per packet by the sender (round-robin with credits).
  - Why two: one stack injects 820 B/cycle through 4 leaves. A single 128 B/cycle plane would need 205 B/cycle per leaf link.
- **Router pipeline:** 2 stages (route computation and VC/switch allocation with look-ahead, then switch and link traversal),
  1-cycle links. Unloaded latency is about 3 cycles per hop. The corner-to-corner core path is 10 hops, about 30 cycles.

## 2. Packets

| Type | VN | Payload | Flits | Notes |
|---|---|---|---|---|
| RD_REQ | REQ | none | 1 | up to 2 KiB, not crossing a 2 KiB boundary (one HBM leaf or one SRAM target) |
| RD_RSP | RSP | 256 B | 2 | one per 256 B granule: tag + granule index; destination space reserved at request time |
| WR | REQ | ≤ 256 B | ≤ 2 | op: write / add FP32 / add BF16 / max FP32; optional `rsem` byte count; burst id and the burst's total bytes (`blen`); a burst is ≤ 2 KiB and inside one 2 KiB-aligned block, so it has one target (the DMA splits there, as for reads; review V-10) |
| WR_ACK | RSP | none | 1 | burst id + byte count; coalesced per write burst (≤ 2 KiB) at the target (rules below) |
| SEM | REQ | 8 B | 1 | `SIGNAL`: add or set at a semaphore address (a WR to the add window) |
| MC_WR | REQ (MC VC) | ≤ 256 B | ≤ 2 | intra-die multicast to a mesh rectangle; posted, no ack; `rsem` applies at each destination |
| ERR | RSP | none | 1 | address fault / unsupported operation; `err_wr` says whether `tag` is a read tag or a write burst id (review V-10) |

A flit is 1024 data bits plus a 128-bit header sideband. The header is valid on the head flit. Its bit layout is `isa.yaml`
`noc_header`, generated into `isa_pkg`, `isa.h`, `isa_defs.py` and [isa_ref.md](isa_ref.md) (review finding I-12(b)):
- `type` (3 bits, enum `noc_type`). The VN follows from the type, and the class (L, X, MC) is the virtual channel, so neither is a
  header field.
- `hop_x`, `hop_y`, `hop_port`: where the packet is routed on the current die. That is the final endpoint, or the link leaf on the
  way to it. The receiving bridge rewrites it: from the address for a request, from the `ini_*` fields for a response.
- `ini_node`, `ini_x`, `ini_y`, `ini_port`: the initiator. Requests carry it, and responses are delivered to it.
- `tag` (9 bits: a read tag, or a write burst id), `len` (12 bits, bytes), `addr` (49 bits, requests only).
- Type-specific fields share bits: `gidx` (granule index, `RD_RSP`), `cause` and `err_wr` (`ERR`), `wop` and `rsem` (`WR`, `MC_WR`), `blen`
  (total bytes of the burst, `WR`, `SEM`), and the multicast rectangle (`MC_WR`).

Header in the sideband means a 256 B write is 2 flits, not 3.

**Write acknowledgements** (reviews R-3, X-3). A target keeps a small coalescing table with one entry per (initiator, burst id):
1. Every accepted `WR` or `SEM` adds its `len` to its entry. The packets of a burst arrive in any order.
2. When the count reaches the packet's `blen`, the target sends one `WR_ACK` with the count.
3. When the table is full and a new burst arrives, the oldest entry is acked early with its partial count. Accepting a write never
   waits for ack state.
4. An entry idle for `ACK_FLUSH` cycles (default 64) is acked with its partial count. This completes a burst whose entry was evicted
   by rule 3: its remaining packets open a new entry that can never reach `blen`.
5. An SRAM target acks only after the data, including a write-add result, is visible to later reads, and after the `rsem` add. The
   HBM controller acks when the write is queued (hbm.md H-2).

The initiator counts acknowledged bytes per burst until they reach `blen`. `noc.ack_coalescer` is the executable form; it is tested
for exact completion at any table size and load, and the rule before X-3 (rules 2 and 4 missing) is shown to hang at low load.

## 3. Virtual networks and channels

| VN | Class | VC | Buffer per input port (flits) | Carries |
|---|---|---|---|---|
| REQ | L (local) | 0 | 6 | requests and writes injected on this die |
| REQ | X (from a link) | 1 | 6 | requests and writes that arrived over D2D or C2C |
| REQ | MC | 2 | 4 | multicast writes (this die only) |
| RSP | L | 3 | 6 | responses injected on this die |
| RSP | X | 4 | 6 | responses that arrived over D2D or C2C |

- Flow control is credit-based, per VC, with virtual cut-through: a VC accepts a packet only if it can hold all of it (≤ 2 flits).
  A blocked packet therefore holds exactly one buffer.
- 6 flits cover the credit round trip (about 5 cycles), so one VC alone can stream at the full link rate.
- Per router: 28 flits x 128 B x 6 ports x 2 planes, about 43 KiB of buffers.
- Arbitration: round-robin between VCs and inputs. Responses are not prioritized over requests (both VNs drain independently).
  Starvation-freedom comes from round-robin.

## 4. Routing and the deadlock strategy

**Routing (deterministic, minimal):**
- **REQ and MC: XY**, X first then Y.
- **RSP: YX.** A response retraces its request in reverse, and HBM read data spreads over the core's own row instead of the leaf's row.
- Leaves are only sources and sinks.
- Multicast is an XY tree: along the source row, then down each destination column. A router forks a packet only when every
  branch has buffer space for it (atomic allocation).

Four mechanisms remove the four kinds of deadlock:

1. **Routing deadlock inside a die:** dimension-order routing on a mesh has an acyclic channel dependency graph, for XY and YX
   separately. Leaves add only sources and sinks. A multicast tree uses the same XY channel order, and it allocates branches
   atomically, so it waits only on channels later in that order.
2. **Protocol deadlock (request needs a response):**
   - Requests and responses use separate VNs.
   - Every response is guaranteed to sink: the requester reserved the destination SPM space (reads) or the ack counter (writes)
     before sending the request.
   - Sinking may take a bounded time (the DMA converter paces `ni_rsp`, microarch.md §2.5), but it never waits for the network.
     - A response never waits for another response *(review finding V-1)*. Block scales are read into the DMA's staging ring
       before the data that needs them. A conversion unit whose bytes span several responses (an inline-scaled row, a fold
       tile across a granule) is received raw into a staging slot, reserved together with its scales when the scale read is
       issued (review finding W-1). If the converter waited for scales that arrive as separate responses, a data granule at the
       head of the RSP channel into a core would wait for its scale queued behind it (tested below). The DMA's own wait, a
       data read issued only after its scales have sunk, is a request-on-response edge, which cannot close a cycle (tested
       below). Whether the ring itself can fill with entries that cannot complete is below channel level: microarch.md §2.5
       and `isa.staging_ring` (every data read belongs to one ring entry, review finding Z-1).
   - A DMA never sends read data on as a write *(review finding I-1)*: one operand of every DMA transfer is local (`TD_SPACE`
     otherwise). If it did, the response channel into a core would wait on that core's request injection, which closes a cycle
     (tested below). (Separated from the V-1 bullet above, review finding W-6.)
   - So RSP always drains, and every target can eventually consume a request.
   - Rule: **no endpoint generates a request in order to consume a request.**
     - Bridges never forward a packet.
     - Write-add is done inside the target.
     - Command queues written remotely are flow-controlled by the sender, through credit semaphores.
     - Collectives receive into buffers announced beforehand by semaphores.
     - A target never waits for write-ack coalescing state before accepting a write: when its table is full it acks early with a
       partial byte count (§2, hbm.md H-2). Otherwise partial bursts could hold every table entry while their remaining packets wait
       behind new bursts in the same VC. Liveness (every burst eventually acked) comes from `blen` and the idle flush (§2).
3. **Deadlock across links:**
   - A die's mesh carries packets heading to a link and packets that arrived from one.
   - If those shared VCs, the cycle `D2D(0->1) -> die-1 column-8 channel -> D2D(1->0) -> die-0 column-8 channel -> D2D(0->1)` is
     possible, even though no single packet crosses twice.
   - So packets that arrived over a link use **class X** VCs and go only to endpoints. The dependency order is then
     L channels -> link -> X channels -> sink, which is acyclic.
   - The link itself is two channels, one per VN: its receive buffer has a queue per VN and its sender arbitrates per VN
     (d2d_c2c.md L-3, review finding I-7). One arrival-order queue for both would let a blocked REQ hold an RSP (tested below).
4. **Multi-hop link paths:** a packet crosses **at most one link**.
   - D2D connects the two dies of a package.
   - C2C connects die d of one package only to die d of others: **rail-aligned**, as in GPU rail-optimized networks.
   - Traffic to the other die index of another package is two DMA operations (D2D, then C2C, staged in SRAM). The staging endpoint
     fully receives the first transfer before it starts the second, so the second transfer is not a dependency of the first.
   - Hierarchical collectives do this anyway: intra-package exchange, then per-rail exchange.

**Machine check.** `noc.build_cdg` enumerates every allowed route of a full system for REQ and RSP:
- every core to every core and HBM leaf, on both dies of 2 or 4 packages, through D2D and C2C;
- the C2C ports trunked over peers on fixed leaf rows;
- protocol edges (request ejection -> response injection), and each core's scales-first wait (every request channel out of a
  core -> every response channel into it, an over-approximation; review finding W-1);
- multicast trees.

`noc.find_cycle` then searches the channel dependency graph:

| Variant | Result |
|---|---|
| Baseline, 2 packages | acyclic (1,872 channels with dependencies, 6,992 edges; 4,336 without the scales-first edges) |
| Baseline, 4 packages | acyclic (3,624 / 13,560; 8,352 without) |
| No class X (link arrivals share VCs with local traffic) | **cycle** (along a D2D row and back) |
| REQ and RSP on one VN | **cycle** (protocol: request into a core, response out of it) |
| Cross-rail forwarding in hardware (D2D then C2C, both in class X), 4 packages | **cycle** (through a C2C port row) |
| An HBM leaf consumes a request by issuing one to the sibling die | **cycle** (D2D and back) |
| A DMA forwards read data as a write (global-to-global copy without a local sink, review I-1) | **cycle** (two cores reading each other) |
| One link queue for REQ and RSP (no per-VN queues in the link layer, review I-7) | **cycle** (through two C2C links) |
| The converter waits for scale responses that arrive with or after the data (review V-1) | **cycle** (the last RSP channel into a core, waiting on the channels of its own route) |

Each rejected design is a test case (`tb/unit/test_noc.py`), so a change that reintroduces one of them fails `make pytest`.
- Coverage: the check is at channel-class granularity. More VCs per class with free allocation inside the class keep it valid.
- For the RTL: Phase 5 adds the formal properties (no drop, no duplication, bounded liveness per router), and the e2e tests on
  `PKG_TINY` / `MULTI_TINY` exercise the bridges.

## 5. Bandwidth

These are peak-rate scenarios at 1.5 GHz with 2 KiB read requests and coalesced acks. The table gives the busiest link's utilization
across both planes (`noc.scenarios()`).

| Scenario | Busiest link | Utilization |
|---|---|---|
| Every core reads its HBM share (102 B/cycle) from its quadrant's stack (memmap.md §3) | HBM leaf -> mesh | **0.80** |
| Every core reads its share from all 4 stacks | row 1, x 4/5 (mesh bisection) | **0.85** |
| D2D 1.4 TB/s per direction, both directions, from all cores | D2D leaf link | **0.97** |
| C2C 0.8 TB/s per direction per die | C2C leaf link | **0.55** |
| Stack-affine HBM at full rate + D2D 1.5 TB/s at the same time | row 1 near the D2D edge | 1.23 (cannot be sustained together) |

- **D2D ceiling:** 4 leaves x 2 planes x 128 B/cycle = 1,024 B/cycle raw. After acks that is **964 B/cycle = 1.45 TB/s per direction**.
  The D2D link is sized to that, at 1.4 TB/s payload *(P2)*. A 5 TB/s link could not be fed: reaching it would need about 26
  leaf-plane links (13 leaves) on a 4-row edge.
  - Phase 1 roofline at the serving point: dropping D2D from 5 to 1.4 TB/s changes decode throughput by ≤ 0.2%, and no package count
    changes. The decode all-reduces are latency-bound.
  - Prefill with TP = 2 forced across D2D slows by ≤ 7%, and the best prefill mappings do not use it.
- **Header and ack overhead:**
  - With a request per 256 B granule, the fully interleaved case reached 1.20 (overload), because a header-only flit costs a full link
    cycle.
  - 2 KiB requests and per-burst acks bring the overhead to 1/16. That is why read requests may span 2 KiB, and why the HBM map keeps
    a 2 KiB block on one leaf.
- **Concurrent streams:** HBM streaming and D2D traffic share row links near the east edge. At full rate together they exceed 1.0.
  The roofline already serializes communication with compute. The perf simulator (Phase 7) measures the real overlap.

## 6. Links and system topology

- **D2D:** 4 sub-links, one per row; the sibling die is rotated 180 degrees, so row r meets row 5 - r.
  - The sender picks the sub-link on its own row (no Y hops), or any sub-link: nothing depends on order.
  - Per-VN credits (REQ, RSP) on the link. The receiving bridge injects into class X.
- **C2C:** 8 ports per die, 100 GB/s per direction each, trunked evenly over the peer packages of the same rail:
  - 2 packages: all 8 ports to the peer;
  - 4 packages: 2 per peer;
  - 8 packages: 1 per peer, plus 1 spare.

  So up to 8 packages, which is the largest system any target model needs (arch.md §3), are fully connected with no forwarding.
  - The port for a peer sits on a fixed west leaf. The routing table is a CSR in the C2C bridge.
- **More than 8 packages (perf-simulator studies only):** use an external switch per rail, still one hop from the NPU's view. The
  switch must keep the two VNs separate. Multi-hop torus forwarding through NPUs is out of scope for v1. It would need dateline VCs
  and is not in the RTL.

## 7. Ordering, QoS, errors

- **No ordering guarantee.** The plane and sub-link are chosen per packet. memmap.md §9 gives the program-level ordering tools: acks,
  byte-counting semaphores, `SIGNAL`.
- **QoS:** none beyond round-robin in v1. The perf simulator reports per-class latency, to decide whether HBM responses need priority.
- **Errors:** a target that cannot serve an address returns `ERR`. The initiating DMA records it, stops the core and interrupts the CP.
  Links carry CRC-32C with go-back-N replay ([d2d_c2c.md](d2d_c2c.md) L-4). A link that goes down keeps sinking: its bridge answers
  requests with `ERR` (`LINK`), including the requests in its replay buffer, and drops everything else, so the mesh drains and
  the cores with transfers through it fault, with `LINK` or, for requests the far side had accepted, `TIMEOUT` (review findings
  X-5, V-7). A faulted core's network interface keeps sinking and serving (isa.md §8). A replay-buffer `ERR` can be stale, so
  it may also fault a core whose transfer completed; it frees no tag, and the CP restarts the whole step (W-2), once every
  core of the step is idle, the bridge has sent its last `ERR` and a drain time has passed (Z-7).

## 8. Open items

- Router RTL micro-pipeline (look-ahead routing, speculative switch allocation) is Phase 4 work against the 3-cycle-per-hop
  assumption.
- Whether multicast is worth its router complexity. It is used for activation broadcast and collectives. The fallback is unicast
  copies from GSRAM, about 32x the injection traffic for a broadcast. To be measured in the perf simulator.
- An external rail switch (above 8 packages) is modeled only.
