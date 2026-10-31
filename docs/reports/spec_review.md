# Phase 2 spec review (2026-11-01)

**Scope:**
- [arch.md](../spec/arch.md) (frozen numbers);
- [microarch.md](../spec/microarch.md), [isa.md](../spec/isa.md) and `isa/isa.yaml` with the generated [isa_ref.md](../spec/isa_ref.md);
- [memmap.md](../spec/memmap.md), [noc.md](../spec/noc.md), [numerics.md](../spec/numerics.md), [hbm.md](../spec/hbm.md),
  [d2d_c2c.md](../spec/d2d_c2c.md);
- [hyper_connections.md](../spec/hyper_connections.md) where it states requirements on them.

**Who reviewed:** the authoring session (Opus 5.5, high effort). This is a **self-review**.
- PLAN §5 asks for an Opus / xhigh review of the ISA and HBM specs. That independent pass has not been done, and is listed under
  Not done below.

**Method:**
1. **Numbers.** Every number one spec quotes from another was traced to its source (arch.md §1, `sim/analytic/*`, `isa.yaml`), and
   recomputed where a formula gives it.
2. **Interfaces.** Every block in microarch.md §1/§3 and the new specs needs an interface table (the Phase 2 exit criterion).
3. **Protocols.** Each request/response path was walked end to end against the deadlock rules of noc.md §4: core → HBM → core,
   core → link → remote SRAM, and staged cross-rail transfers.
4. **Executable checks.** `make pytest` (now 137 tests), `make isa`, `make hbm`, `make links`.

## Findings

| # | Kind | Finding | Resolution | Where |
|---|---|---|---|---|
| R-1 | model error | The roofline's all-to-all counts off-package bytes as N(1-1/g)(1-2/g), but they are N(g-2)/g. It also ignores rail staging. Its ring all-reduce charges 2(g-1) latency steps where a hierarchical direct schedule needs 4. | Measured, not changed. With the d2d_c2c.md schedules, decode at the serving point changes by -1% to +10% (frozen latencies), and no package count changes. The roofline stays frozen; the Phase 7 simulator uses the L-7 schedules. | d2d_c2c.md L-7, L-8; links_experiments.md L5, L6; arch.md §5 |
| R-2 | conservative assumption | The frozen collective-step latencies (0.5 µs D2D, 2 µs C2C) are 5x and 8x a bottom-up estimate (0.09 / 0.24 µs). | Kept as planning values. With bottom-up latencies decode gains +1% to +16%. | d2d_c2c.md L-5 |
| R-3 | deadlock hazard | Write acks "coalesced per 2 KiB burst at the target" require per-burst state. A target whose table is full of partial bursts would stop accepting writes, including the packets that would complete those bursts. | `WR_ACK` carries a byte count. A full table acks early with a partial count, and a target never waits for ack state before accepting a write. | noc.md §2, §4; hbm.md H-2; microarch.md §2.6 |
| R-4 | gap | HBM writes smaller than a burst were undefined. HBM has no read-modify-write path, and the DFI-like interface no data mask. | HBM writes must be 32 B aligned in address and size, else fault `HBM_ALIGN`. The compiler pads per-token items (example: NVFP4 KV scales grouped over heads). | hbm.md H-2; memmap.md §8; isa.md §8 |
| R-5 | gap | Fault causes were listed only in prose. `FAULT_CAUSE` had no encoding. | `fault_cause` enum in `isa.yaml` (14 values, including the new `HBM_ALIGN`, `PTE_INVALID`, `LINK`, `ERR_RSP`). Generated into SV, C++, Python and isa_ref.md. | isa.yaml; isa.md §8 |
| R-6 | exit criterion | The generator emitted SV, C++ and Markdown, but not Python. | `isa/gen/isa_defs.py` (plain-Python constants). The staleness test covers it, and a test checks it against the YAML. | isa/isa.py; test_isa.py |
| R-7 | gap | noc.md §7 said "links have no CRC or replay in v1". At 1e-15 BER, an 8-package system would see a silent bit error every 2.5 s. | CRC-32C + go-back-N replay on both links. Link-down faults the affected cores (`LINK`). | d2d_c2c.md L-4; noc.md §7 |
| R-8 | under-specified | "C2C 100 GB/s per port" left the PHY open. With link-flit overhead (payload efficiency 0.772), 4 lanes of 200 Gb/s carry 77 GB/s. | 6 lanes per port (116 GB/s payload), 48 per die. Phase 6 checks the SerDes shoreline. | d2d_c2c.md L-2; microarch.md §1, §8 |
| R-9 | accounting | SRAM outside the cores was not counted: HBM controller queues and link replay/receive buffers. | 1 MiB (HBM) + 1.4 MiB (links) per die, listed in microarch.md §8. Not a frozen-number change (arch.md's SRAM row is compute SRAM). | hbm.md H-1; d2d_c2c.md L-4 |
| R-10 | gap | Clock domains were not stated. The HBM controller needs CK/2 = 1.2 GHz for one burst per cycle. | `clk_mc` domain with async FIFOs (`common/afifo`, added to the Phase 3 `common` list). `MC_SYNC = 1` for single-clock unit tests. | hbm.md H-1; microarch.md §5; PLAN Phase 3 |
| R-11 | gap | The memmap hash is defined for 32 PCs and 4 leaves. `TINY` (4 PCs, 2 leaves) and `MID` (8 PCs) had no map. | PC fold: leaf from `pc[4:3]`, PC within the leaf from `pc[2:0]`, the unused bits into the row. Tested one-to-one, and 2 KiB blocks stay on one leaf. | hbm.md H-1; memmap.md §3; test_hbm.py |
| R-12 | device risk | The scheduler hides tCCD_L only up to 4 bank groups per SID / tCCD_L cycles. At 4.1 ns, streaming falls to 0.79. | Requirement tCCD_L ≤ 2.5 ns (3.3 ns at the limit), checked against the vendor datasheet. Fallback: SID-alternating address map. Added to the PLAN risk table. | hbm.md H-6; hbm_experiments.md H5 |
| R-13 | exit criterion | Interface tables were missing for the ACC, leaf router, host interface, async FIFO, HBM PC engine and DFI-like boundary, link layer and PHY models. | Added to microarch.md §5 or the owning spec (hbm.md H-5/H-7, d2d_c2c.md L-9). | |
| R-14 | stale text | hyper_connections.md HC-5 required a "collective engine". microarch.md said "still to come: hbm.md, d2d_c2c.md". memmap.md deferred timing to hbm.md. PLAN Phase 4 still had a collective engine. | Updated to the distributed schedules and the new specs. | |
| R-15 | gap | The frozen 0.85 HBM efficiency had no controller-level evidence. | The controller model reaches 0.95-0.99 on decode traffic. The remaining margin covers PC imbalance, retraining and the NoC. | hbm.md H-6 |

## Checked with no change needed

- **D2D:**
  - The NoC edge ceiling (964 B/cycle, noc.md §5) equals the link payload model (1.45 TB/s, links_experiments.md L1).
  - 2 UCIe-A x64 modules per sub-link are 91% used.
- **HBM port:** 8 PCs x 32 B x 1.2 GHz = 205 B per NoC cycle against 256 B/cycle per leaf (80%). This matches the leaf utilization in
  noc.md §5.
- **Leaf invariant:** the 2 KiB-on-one-leaf property of memmap.md §3 is what lets one `RD_REQ` feed 8 PCs of one controller port
  (hbm.md H-2).
- **DMA sizing:** 512 granules in flight (microarch.md §2.5) against the HBM loaded round trip (about 640 core cycles at p99, 85% load;
  hbm.md H-6): 2x margin.
- **Deadlock:**
  - Rail-staged all-to-all (d2d_c2c.md L-7) is two DMA transfers through pre-reserved SRAM, as noc.md §4 rule 4 requires.
  - Link credits keep 4 reserved flits per VN, so RSP drains even if REQ fills the shared pool.
- **Numerics:** write-add determinism (numerics.md N-6) and the collective schedules agree. Deterministic mode uses slots and `VRED`;
  performance runs use write-add.
- **Hyper-connection all-reduces:** 80 per DeepSeek step cost about 16 µs across D2D at bottom-up latency, 0.2% of the step.
  The HC-5 "sub-microsecond" requirement is met (0.1 µs intra-die).
- **ISA coverage** (isa.md §6) needs no new instruction for the link and HBM specs: collectives are `DMA_LIST` / `rsem` / `SIGNAL` /
  `DMA_MCAST`.
- **Package counts** (arch.md §3) are unchanged by every finding above.

## Exit criteria (PLAN Phase 2)

| Criterion | Status |
|---|---|
| Spec reviewed | done, as a self-review (this document). The independent ISA + HBM pass is still open |
| `isa.yaml` generator emits SV / C++ / Python | done (R-6) |
| Every block has an interface table | done (R-13) |

## Not done

- **Independent review.** PLAN §5 asks for an Opus / xhigh review of the ISA and HBM specs; it is recommended before Phase 3 RTL starts
  on `core/dma`, `core/seq` and `hbm`.
- **Real device numbers.** No JESD238 / vendor HBM3e timing, UCIe PHY latency, or SerDes latency was available. The model tables are
  the inputs to replace: `make hbm` and `make links` regenerate everything from them.
