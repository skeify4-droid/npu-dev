# D2D and C2C links, collectives (Phase 2)

Status: **reviewed, 2026-11-01** ([spec review](../reports/spec_review.md)).
- Model: `sim/analytic/links.py`. `make links` writes [`links_experiments.md`](../reports/links_experiments.md) (L1-L6), and
  `tb/unit/test_links.py` tests it.
- Deadlock rules for packets that cross links: [noc.md](noc.md) §4 and §6. Write-add numerics: [numerics.md](numerics.md) N-6.

Items marked *(P2)* are decisions made here. **(A)** marks an assumption.

## L-1. Scope

- **One link layer for both links *(P2)*.** D2D (die to die in a package) and C2C (package to package on a rail) use the same
  link-layer RTL: flits, credits, CRC and replay. They differ only in their PHY parameters (bandwidth, latency) and buffer depths.
- **The PHY is behavioral** in RTL and in the simulator: latency, bandwidth, and an optional bit-error injector.
- **No collective engine.** Collectives are DMA programs built from put-with-signal, write-add, intra-die multicast and semaphores
  (microarch.md §3, isa.md §10). L-7 gives the schedules the compiler emits.
- **No forwarding.** A bridge never sends on a link a packet that arrived from a link (noc.md §4 rule 4). Cross-rail traffic is
  staged in SRAM by DMA.

## L-2. Bridges and lanes

| | D2D | C2C |
|---|---|---|
| Units per die | 4 sub-links, one per mesh row (east leaves E1-E4) | 8 ports, 2 per west leaf (W1-W4) |
| Lanes per unit | 2, one per NoC plane: a packet keeps its plane | 1 flit stream; the receiver picks the plane round-robin |
| Peer | the sibling die, rotated 180 degrees: row r meets row 5 - r | the same die index in another package (rail) |
| Routing | the source's row (or any sub-link) | CSR table: peer package -> trunk of ports. 2 packages: all 8 ports; 4: 2 per peer; 8: 1 per peer + 1 spare. Port chosen per packet within the trunk |
| PHY (A) | 2 UCIe-Advanced x64 modules per sub-link at 32 GT/s (256 GB/s each per direction) | 6 lanes of 224G-class PAM4 SerDes, 200 Gb/s each after FEC |
| Raw per unit per direction | 468 GB/s (91% of 512) | 150 GB/s |
| Payload per die per direction | **1.45 TB/s** (frozen 1.4) | **0.93 TB/s** (frozen 0.8: 8 x 100 GB/s) |

**C2C lane count *(P2)*.** With the link flit's overhead, a port needs 129 GB/s raw to carry 100 GB/s of payload (L1).
- 4 lanes (100 GB/s raw) would carry 77 GB/s, and 5 lanes 97 GB/s.
- **6 lanes per port, 48 per die**, is the smallest count that meets the frozen number. Phase 6 checks the SerDes shoreline on the
  outer short edge.

## L-3. Link flit, credits, ordering

A link flit is one NoC flit plus link fields: 1248 bits (156 B), one flit per cycle per lane (D2D) or at the port's rate (C2C).

| Bits | Field | Notes |
|---|---|---|
| 1024 | data | the NoC flit's data |
| 128 | NoC header | as on the mesh (noc.md §2); valid on head flits |
| 1 / 1 / 1 | valid, head, tail | an idle flit (valid = 0) still carries acks and credits |
| 1 | VN | REQ or RSP. Multicast never crosses a link |
| 12 | seq | flit sequence number, per lane and direction |
| 12 + 2 | ack_seq, ack_type | cumulative ACK or NAK for the reverse direction (piggybacked) |
| 12 + 12 | credit_req, credit_rsp | cumulative count of receive slots freed per VN, mod 2^12, on every flit (review finding X-4) |
| 10 | reserved | |
| 32 | CRC | CRC-32C (Castagnoli) over all the above |

**Credits *(P2)*:**
- The receive buffer of each lane or port has a **shared pool** plus **4 reserved flits per VN** (two maximal packets).
- A sender uses a VN's reserved credits only when the shared pool is empty. So a VN blocked downstream can fill the shared pool but
  never the other VN's reserved slots.
- This is what noc.md §4 needs: RSP always drains even when REQ is stuck, because RSP arrivals go to class X virtual channels that sink
  at endpoints.
- The receiving bridge moves a packet from the receive buffer into a class X VC of the leaf router, then returns its credits.
- **Per-VN queues *(review finding I-7)*:** the receive buffer is one pool of slots, but `ll_rx` keeps **one queue per VN** over it and
  the bridge dequeues each VN independently. A REQ packet blocked on a full class-X REQ VC therefore never holds an RSP packet that
  arrived behind it. `ll_tx` likewise arbitrates per VN, so a REQ waiting for credits does not block RSP. With one arrival-order queue
  the reserved RSP slots would not help: `noc.build_cdg(link_vn=False)` finds a cycle through two C2C links (a rejected variant in
  `test_noc.py`). `noc.py` always modelled per-VN link channels; this makes the spec require them.
- **Credit return *(review finding X-4)*:**
  - The receiver sends, on every flit including idle ones, the cumulative number of slots it has freed for each VN since link-up,
    mod 2^12 (`credit_req`, `credit_rsp`). It never says whether a slot was in the pool or a reserve.
  - The sender takes the difference to its last value mod 2^12 as the newly returned credits. A flit lost to a CRC error (an idle
    flit is never replayed) loses nothing: the next flit carries the newer total. A value that is not ahead of the last one in serial
    order (a replayed, older flit) is ignored.
  - The sender alone does the pool accounting: it charges each flit it sends to the VN's reserve or to the pool, and applies
    returned credits of a VN **to that VN's reserve first**, then to the pool. So the reserves cannot drift into the pool, and
    in-flight flits never exceed pool + 8, whichever slots the receiver used.
  - 2^12 = 4,096 is more than twice the largest receive buffer (520 flits), so the counter difference is never ambiguous.
  - This replaces the incremental 4 + 4 + 6-bit credit fields, which a lost idle flit would have leaked for good.

**Ordering:**
- Each lane delivers flits in order, including across a replay.
- Nothing above relies on that: the NoC has no ordering guarantee (memmap.md §9), and the sender chooses planes, sub-links and trunk
  ports per packet.

## L-4. Reliability *(P2)*

**Why both links need CRC and replay** (L3):
- At a raw bit error rate of 1e-15, a die's links carry 15 Tb/s (D2D) plus 9.6 Tb/s (C2C). That is one error every 41 s per die, and
  one every **2.5 s** in an 8-package system.
- UCIe specifies 1e-15 at its 16-32 GT/s rates, which is why its own flit modes carry CRC and retry.
- Undetected, each error is silent data corruption in a KV cache or an activation.

**Mechanism: go-back-N replay with sequence numbers:**
- **Sender:** keeps every transmitted flit in a replay buffer until it is ACKed. A NAK, or no ACK within a timeout (2 x the round
  trip), makes it resend from the oldest unacknowledged sequence number.
- **Receiver:**
  - It checks the CRC and the sequence number.
  - A CRC error or a gap makes it drop that flit and every later flit until the expected sequence number arrives, and it sends a NAK.
  - Good flits are ACKed cumulatively: on every reverse flit, and at the latest every 32 cycles by an idle flit.
- **Sizing (L2):**
  - The replay buffer and the shared receive pool each cover 1.25 x the round trip, rounded up to a power of two.
  - D2D: round trip 32 cycles, 64 + 64 + 8 flits per lane, **166 KiB per die**.
  - C2C: round trip 470 cycles (SerDes, FEC and about 1 m of cable, 150 ns one way (A)), 512 + 512 + 8 flits per port,
    **1.26 MiB per die**.
  - 12-bit sequence numbers cover twice the largest replay window.
- **Link down:** more than 8 consecutive replays of the same flit, or PHY loss of lock.
  - The bridge records the cause in its CSRs and interrupts the CP.
  - **It keeps sinking *(review finding X-5)*.** A stopped bridge would back packets up through the mesh REQ-L virtual channels and
    block unrelated traffic on them: other cores' HBM reads, the cores' fault interrupts to the CP, and the CP's recovery accesses.
    So after link down the bridge:
    - answers every request that arrives for the link with `ERR` (cause `LINK`), like a target that cannot serve the address;
    - drops responses that arrive for the link (their initiators are on the far side, and time out there);
    - discards its replay buffer and anything still in its receive buffer.
    The mesh therefore drains, and every core with a transfer through the link faults with `LINK` from the `ERR`.
  - The DMA's per-request timeout (CSR, default 100 µs) stays as the backstop for transfers whose responses were lost on the far side.
    It faults with `TIMEOUT`, not `LINK`, because a timeout alone does not show that a link failed (review finding I-15).
  - Recovery is a runtime action: retrain, then restart the step.
- **Error injection:** the behavioral PHY flips bits at a configurable rate, or on chosen flits. The Phase 4 tests require that every
  injected error is replayed and that the delivered data is unchanged.

## L-5. Latency (L4)

Bottom-up, unloaded, in core cycles at 1.5 GHz. This is one put-with-signal from a core's DMA to the consumer core waking on its
semaphore.

| Component | D2D | C2C |
|---|---|---|
| DMA issue, descriptor, first packet | 16 | 16 |
| network interface + mesh to the edge leaf (5.5 hops mean) | 18 | 18 |
| bridge TX + RX (flit, sequence, CRC; check, buffer, inject) | 8 | 8 |
| PHY + flight (A: 4 ns / 150 ns) | 6 | 225 |
| destination mesh | 18 | 18 |
| target write + semaphore add + consumer wake-up | 10 | 10 |
| skew allowance for the 32 cores of a die | 64 | 64 |
| **one collective step** | **0.09 µs** | **0.24 µs** |
| frozen roofline value (arch.md) | 0.5 µs | 2 µs |

The frozen values are 5x and 8x above this estimate, so they stay as conservative planning numbers. The Phase 7 perf simulator uses the
bottom-up path, with contention.

## L-6. Collective primitives

Collectives use only the following, all existing in ISA v0.1.

- **Put-with-signal:** a `DMA_COPY` / `DMA_LIST` write with `rsem`. The destination semaphore counts payload bytes as they land, in
  any order.
- **Write-add:** `wop = ADD_F32 / ADD_BF16` into SRAM. It is order-dependent for 3 or more contributors (numerics.md N-6).
- **Multicast:** `DMA_MCAST`, intra-die only.
- **Persistent buffers:**
  - Each collective call site owns two receive buffers per peer, used alternately.
  - **One byte-counting semaphore per buffer parity** *(review finding I-6 c)*: a producer that runs ahead into buffer B would
    otherwise complete buffer A's count early. With forwarding, one semaphore per chunk slot.
  - The consumer `SIGNAL`s the producer's credit semaphore once a buffer is consumed. Monotonic semaphores mean no reset and no
    handshake per call (isa.md §3: network-facing semaphores are never reset).
  - The producer's credit wait is normally already satisfied, so it is off the critical path.
- **SRAM destinations only for `rsem`** *(review finding I-6 b)*. An HBM write has no owning core, so `rsem` with an HBM destination
  is `ILLEGAL_FIELD`. A transfer into an HBM buffer ends with a `SIGNAL` (release after the acks) that adds its byte count to the
  consumer's semaphore. Prefill all-to-all buffers are this case.
- **Deterministic mode** (`deterministic_collectives`, numerics.md N-6):
  - Every contribution is written to its own slot, and the owner reduces with `VRED` in source order.
  - It needs the same number of steps, n slots of buffer, and one `VRED` pass. For the decode TP all-reduce at g = 8 that pass is about
    20 ns per core, under 1% of the collective.
  - E2E regressions use it. Performance runs use write-add.

## L-7. Schedules *(P2)*

Notation:
- g dies in the group, P = g / 2 packages, N bytes contributed per die.
- Rails: die d of every package forms rail d.
- Within a die, the data is already split over the 32 cores, and core c talks to core c of the peer dies. Each core's traffic leaves
  through its own row's sub-link or port.

**Intra-die all-reduce** (hyper-connection mixer, HC-5):
- reduce-scatter by write-add (or slots) to 32 owner cores, then 32 `DMA_MCAST`s back;
- 2 intra-die steps, about 0.1 µs for `[64 x 25]` FP32.

**All-reduce over g dies, hierarchical direct:**
1. **D2D reduce-scatter:** each die sends the half it does not own to its sibling, which adds it. With 2 contributors this is
   deterministic.
2. **C2C reduce-scatter on each rail:** each package sends the (P - 1) / P of its half that other packages own directly to them.
   - Up to 8 packages, every peer is one hop (noc.md §6). The P - 1 trunks run in parallel.
3. **C2C all-gather:** the reverse of step 2.
4. **D2D all-gather:** the reverse of step 1.

That is 4 latency steps for any g > 2. The roofline's ring needs 2(g - 1) steps, and its bytes are the same or more (L5):
- the decode TP all-reduce at g = 8 takes 6.5 µs instead of 30 µs at frozen latencies;
- the prefill-chunk all-reduce takes 199 µs instead of 285 µs.

**All-to-all (MoE dispatch and combine):**
1. **Counts:** `VROUTE` + `VBUCKET` give each die its per-expert token counts. The counts are all-gathered over the EP group
   (a few hundred bytes, staged like the data).
   - Every source then computes its write offsets in every destination buffer by an exclusive prefix over sources in source order.
     The layout is therefore deterministic, and no offset round trip is needed.
2. **Data:** puts with `rsem`. Each destination waits for the byte total it computed from the counts.
   - **Same rail, other package:** direct over C2C.
   - **Sibling die:** direct over D2D.
   - **Other rail, other package: staged *(P2)*.** The data goes over C2C to the same-rail die of the destination package. That die
     forwards it over D2D in 64 KiB chunks, as soon as each chunk's `rsem` count completes.
     - The forward is a new DMA transfer from SRAM into a buffer reserved in advance, so it is not a network dependency of the first
       hop (noc.md §4 rule 4). The staging slots are in the forwarder's own SPM, because a DMA has one local operand (isa.md §5).
     - The forwards are one `DMA_LIST` whose entries wait on their chunk's semaphore (isa.md §5), so they take one queue slot.
     - C2C first, then D2D, because D2D has 1.75x the bandwidth for the forwarded bytes.
   - **Buffer rules *(review finding I-6 a)*:**
     - A destination buffer whose consumer waits for a byte total is sized for that whole total (known from the counts) and is not
       recycled within the exchange. Otherwise the forwarder would wait for credits that come only after the total arrives.
     - The forwarder's staging ring may be smaller and recycled: a slot is freed when its D2D forward is acknowledged, which depends
       only on the full-size destination buffer, not on consumption.
     - Prefill volumes do not fit SPM (2,048 tokens x top-8 x 5,120 in FP8 is 84 MB per die), so those destination buffers are in
       HBM and complete by `SIGNAL` instead of `rsem` (L-6). The staging ring stays in SPM.
   - **Issue order *(review finding X-6)*:** an instruction that waits on a remote semaphore blocks its queue, and a full queue (16
     entries) blocks the in-order front end. So the compiler:
     1. issues every put that depends only on local data **before** any instruction that waits on a remote semaphore;
     2. keeps remote waits (chunk forwards, credit waits) in a queue that holds nothing other cores depend on, and puts each group of
        forwards in one `DMA_LIST` with per-entry waits, so the front end never fills a queue behind them;
     3. never puts a credit wait ahead of the same core's forwards in one queue.
     Without rule 1, two forwarders with more than 16 chunks each block on full queues while each one's forwards wait for the
     other's puts: a deadlock with no network resource involved, which `noc.py` cannot see. The golden model and the Phase 7
     simulator detect it ("every engine blocked on a semaphore", PLAN Phase 7).
3. **Combine:** the reverse direction. With write-add (`DMA_SCATTER wop = ADD_BF16/F32`) it is order-dependent for top-k ≥ 3. In
   deterministic mode it writes into `[token, k]` slots, which the VPU sums in k order.

Per die, the bytes are:
- over C2C: 2(P - 1) N / g;
- over D2D: P N / g (its own sibling share plus the forwarded share).

The roofline formula counted only N(1 - 1/g)(1 - 2/g) bytes as off-package, and did no staging. It underestimates the prefill
all-to-all by up to 33% at g = 4 (L5; review finding R-1).

**Pipeline hand-off:** one put-with-signal of the residual stream (n streams with hyper-connections) to the next stage's dies.

**Hashed-table gathers** (DeepSeek Engram, Qwen n-gram; hyper_connections.md G-4): rows on the other rail of another package are
gathered by the requester's sibling over C2C into its SPM and pushed over D2D with `rsem`, the same two-step staging as the
all-to-all. The cost is in L7: no capacity, a few tens of KB of D2D per die per step, and one D2D hop of latency that G-1 hides.

## L-8. What this changes for the frozen architecture

Nothing in arch.md §1 changes:
- D2D and C2C payloads meet the frozen 1.4 and 0.8 TB/s per die (L1).
- The link-buffer SRAM (1.4 MiB per die) is listed in microarch.md §8.

Decode sensitivity (L6) re-runs the serving point with the L-7 schedules in place of the roofline's formulas:

| Latencies | Decode change across the 11 models |
|---|---|
| frozen (0.5 / 2 µs) | -1% to +10% |
| 2x the L-5 bottom-up values | +1% to +15% |
| bottom-up | +1% to +16% |

No package count changes. The two -1% cases (GLM-5.3, Qwen) are the all-to-all byte correction. The gains come from mappings
with TP > 2:
- some already use it at the frozen point (MiniMax, MiMo-Pro and Muse at TP = 4, with 6-12% of the step in communication);
- for others it becomes worth choosing once an all-reduce takes 4 latency steps instead of 2(g - 1): DeepSeek's best mapping moves
  from TP = 2 to TP = 8.
- The roofline is kept as frozen; review finding R-1 records the difference.
- The 80 hyper-connection all-reduces per DeepSeek step cost about 16 µs at D2D bottom-up latency, 0.2% of the step.

## L-9. Interfaces

**Bridge (`link/bridge`)**, one per D2D sub-link or C2C leaf. Parameters: `LANES` (D2D 2, C2C 1 per port), `PORTS` (D2D 1, C2C 2),
`REPLAY`, `RX_SHARED`, `RX_RSV`.

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| `noc` | in/out | flit_t (2 planes) | credit per VC | leaf router; arrivals injected into class X |
| `tx[PORTS*LANES]` | out | 1248 | valid/ready (PHY pacing) | link flits to the PHY |
| `rx[PORTS*LANES]` | in | 1248 | valid | link flits from the PHY (CRC checked here) |
| `csr` | in/out | 64 | valid/ready | C2C peer routing table, replay counters, CRC error counters, link state |
| `irq` | out | 1 | level | link down |

**Link layer TX/RX (`link/ll_tx`, `link/ll_rx`)**, per lane, inside the bridge:

| Port | Dir | Width | Protocol | Purpose |
|---|---|---|---|---|
| `pkt_in[2]` / `pkt_out[2]` | in/out | flit_t | valid/ready per VN | to and from the bridge's packet side: one queue per VN over the shared pool on RX, per-VN arbitration on TX (I-7) |
| `credit_ret` | in | 12 + 12 | level | cumulative slots freed per VN by the local RX buffer, mod 2^12, sent on every TX flit (X-4) |
| `ack_ret` | in | 12 + 2 | pulse | ACK/NAK generated by the local RX, sent on the next TX flit |
| `phy` | out/in | 1248 | valid/ready | |

**Behavioral PHY (`tb/models/link_phy.sv`, testbench only):**

| Parameter / port | Meaning |
|---|---|
| `LAT_CYCLES` | one-way latency (D2D 6, C2C 225 at 1.5 GHz) |
| `FLITS_PER_KCYCLE` | bandwidth (D2D 1000 per lane; C2C 641 per port) |
| `BER`, `ERR_SEED`, `err_force` | random and directed bit flips |
| `a_tx` / `b_rx`, `b_tx` / `a_rx` | the two directions |
| `lock` | PHY up; dropping it models a link failure |

## L-10. Decisions and alternatives

| Decision | Alternative rejected | Reason |
|---|---|---|
| One link layer for D2D and C2C | separate designs | one RTL block and one verification effort; parameters differ only |
| CRC-32C + go-back-N on both links | no CRC on D2D (UCIe raw) | L3: an error every 2.5 s per 8-package system at 1e-15 |
| Go-back-N | selective repeat | errors are rare; go-back-N needs no reorder buffer |
| NoC flit + 96 bits per link flit | a denser link packing (header only on head flits) | +23% raw for a simple 1:1 mapping; the PHYs are sized for it (6 C2C lanes) |
| Shared receive pool + reserved slots per VN | static per-VN buffers | half the C2C buffer for the same full-rate single-VN streaming |
| Hierarchical direct all-reduce | ring / tree | 4 latency steps for any g ≤ 16 on fully connected rails; equal or fewer bytes |
| Rail-staged all-to-all, C2C then D2D | cross-rail forwarding in the bridges | forwarding in hardware creates a channel-dependency cycle (noc.md §4, tested) |

## L-11. Open items

- The PHY latencies (4 ns UCIe-A; 150 ns SerDes + FEC + cable) and the 224G lane rate are assumptions. Re-run `make links` with vendor
  numbers.
- The SerDes shoreline (48 lanes per die) and the 8 UCIe-A modules on the facing edge are Phase 6 floorplan checks.
- Above 8 packages, through an external rail switch: the switch must keep REQ and RSP separate and do its own link-level retry.
  Perf simulator only.
- The DMA request timeout and the runtime's recovery after a link-down event are Phase 4 (`link`, `die`) and runtime items.
- Phase 4 link tests from the review (PLAN Phase 4): CRC error on an idle flit and on a replayed flit carrying stale credits (no
  credit lost or double-counted, X-4); credit counters wrapping past 2^12; REQ blocked at the receiver while RSP keeps draining
  (I-7); link down under load on `PKG_TINY`: every core with a transfer through the link faults with `LINK` and the CP still reaches
  every core (X-5).
