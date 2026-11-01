# Phase 2 spec review (2026-11-01)

**Scope:**
- [arch.md](../spec/arch.md) (frozen numbers);
- [microarch.md](../spec/microarch.md), [isa.md](../spec/isa.md) and `isa/isa.yaml` with the generated [isa_ref.md](../spec/isa_ref.md);
- [memmap.md](../spec/memmap.md), [noc.md](../spec/noc.md), [numerics.md](../spec/numerics.md), [hbm.md](../spec/hbm.md),
  [d2d_c2c.md](../spec/d2d_c2c.md);
- [hyper_connections.md](../spec/hyper_connections.md) where it states requirements on them.

**Who reviewed:** the authoring session (Opus 5.5, high effort). This is a **self-review**.
- This is the recommended setting for Phase 2 in PLAN §5; Opus / xhigh on ISA + HBM is listed there only as an alternative.
- A self-review cannot catch the author's own assumptions. An independent review of the whole spec set is recommended; see Not done.

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
| Spec reviewed | done, as a self-review (this document), and an independent review (below). The independent review's findings are open |
| `isa.yaml` generator emits SV / C++ / Python | done (R-6) |
| Every block has an interface table | done (R-13) |

## Not done

- **Independent review** (done 2026-11-01, see below; its findings are open). Recommended before Phase 3 RTL starts on `core/dma`, `core/seq` and `hbm`; `common` and `arith` do not
  depend on it.
  - **Why:** the session that wrote the specs also built the models that check them and reviewed them. Both serious findings above
    (R-3 between noc.md and hbm.md, the PC fold between memmap.md and microarch.md) sat at seams between specs, so the review
    covers the whole set, not single specs.
  - **How:** a fresh, read-only session starting from `CLAUDE.md` and the specs (about 32k words with `isa.yaml`), at Opus / high,
    the PLAN §5 setting. Findings go into a new "Independent review" section of this file; fixes are made in a later session.
  - **Depth:**

    | Depth | Specs | Why |
    |---|---|---|
    | Deepest | isa.md / `isa.yaml`, memmap.md, microarch.md interface tables | Least covered by executable checks; hardest to change once RTL exists |
    | Model assumptions | hbm.md, noc.md, d2d_c2c.md | `hbm.py`, `noc.py` and `links.py` check them; review what the models assume, not their outputs |
    | Lighter | numerics.md | Bit-accurate golden model and tests |
    | Consistency only | arch.md, hyper_connections.md, model_descriptor.md | Frozen in Phase 1; reopen only for an actual error |

  - **xhigh, only for two checks:** whether `rules()` in `sim/analytic/hbm.py` covers every DRAM timing constraint (it becomes the
    reference for the Phase 4 timing checker), and the deadlock paths (R-3 partial acks, staged all-to-all) against noc.md §4.
- **Real device numbers.** No JESD238 / vendor HBM3e timing, UCIe PHY latency, or SerDes latency was available. The model tables are
  the inputs to replace: `make hbm` and `make links` regenerate everything from them.

# Independent review (2026-11-01)

**Who reviewed:** a fresh Opus 5.5 session, not the authoring one. It started from `CLAUDE.md` and this file, read every spec in the
scope above plus `isa/isa.yaml`, and ran `sim/analytic/hbm.py` on probe inputs. No spec was changed. Fixes are left for a later session,
as the Not-done section asks.

**Method:**
- Same four checks as the self-review, at the depths in its table.
- Weighted toward the seams between specs: ISA ↔ memmap ↔ DMA, the DMA ↔ NoC sink rules, links ↔ noc.md §4, and HBM map ↔ configs.
- The two xhigh items: `hbm.rules()` coverage, and the deadlock paths.
- Probes (scratch scripts, not committed):
  - the reduced-config PC fold run through `hbm.simulate()`;
  - hand-made command sequences against `hbm.Dram`;
  - the SPM bank hash over power-of-two strides.
- `make pytest` passes (rc 0, 19 s, 65 MB peak).

## Findings

Severity: **H** = blocks or changes Phase 3 RTL of a gated block (`core/dma`, `core/seq`, `hbm`), or a deadlock; **M** = a gap or error to fix
before the block that uses it; **L** = text, consistency, or a cheap improvement.

| # | Sev | Kind | Finding | Suggested resolution | Where |
|---|---|---|---|---|---|
| I-1 | H | deadlock hazard | `DMA_COPY` is "between any spaces", so both ends can be network addresses (HBM → HBM page moves, HBM → another core's SPM). The response then has no reserved SPM sink. The DMA must hold it until its write can go out, so if REQ is blocked, RSP waits on REQ. That breaks the sink guarantee of noc.md §4 rule 2 and microarch.md §2.5 ("no staging buffer"). `noc.build_cdg` has no edge for it (its only protocol edges are request ejection → response injection). | Route GLOBAL → GLOBAL copies through a local-SPM bounce buffer reserved when the read is issued: the response sinks there, and the write is a separate later step. Or forbid the pair (`TD_SPACE`). Add the case to `test_noc.py`. | isa.md §5; microarch.md §2.5; noc.md §4 |
| I-2 | H | ISA gap | Block-scaled data under paging has no complete operand set. This is the KV and index-key path of DeepSeek (NVFP4 KV, MXFP4 index keys) and MiniMax (NVFP4 KV and index keys). (a) TD `aux0` is both `page_shift` and the scale-array row stride. (b) `DMA_GATHER` PAGED takes its page table from `scale_reg`, which leaves no operand for a separate scale array. (c) Scale arrays are never translated through a page table. (d) memmap.md §6 puts one KV group in a page, but hbm.md H-2 stores the scales of all KV heads of a token together, so data and scales sit in different pages. (e) The NVFP4 FP32 tensor scale has no operand anywhere, yet the fold (N-2 step 4) and `NVFP4_BF16` both multiply it into the column scales. (f) PAGED is defined as "translates its outermost dimension" but the page size is in bytes. Nothing says whether a row may straddle a page, and MLA 576 B tokens do not divide 4 KiB. | Give each page its own scale region (data, then the scales of the same tokens), so one PTE translates both. Move the scale layout out of `aux0` (the `rsv` bits [255:240], or a second TD). Add a tensor-scale register to T_DMA (bits [28:0] are free). Define PAGED as: logical byte offset `>> page_shift` → frame, with rows not crossing a page, or with the DMA splitting at page boundaries. | isa.yaml `td`, T_DMA, `cvt`; memmap.md §6; hbm.md H-2; numerics.md N-1, N-2 |
| I-3 | H | bandwidth | Decision D1 (decode NVFP4 uses exact `NVFP4_BF16`) checks only the 512 B/cycle stationary-load port. The DMA writes SPM at 256 B/cycle (microarch.md §2.3, §5). A core's HBM share at 0.85 (87 B/cycle) of NVFP4 expands to 309 B/cycle of BF16 (364 at peak). So the exact path caps NVFP4 streaming at 72 B/cycle per core, 0.83x the rate the roofline assumes, on MiniMax's experts and DeepSeek's NVFP4 KV. The DMA's `ni_rsp` "always accepted" contract also cannot hold under a 3.6x expansion. MXFP4/NVFP4 → E4M3 (about 193 B/cycle) fits. | Widen the DMA SPM write to 512 B/cycle (the SPM write side peaks at 768 of 2,048 B/cycle), or convert in the MXU weight-load path. Give `ni_rsp` a bounded inbound buffer with ready, or size the converter for 256 B/cycle in. Re-check D1's margin. | numerics.md D1; microarch.md §2.3, §2.5, §5 |
| I-4 | M | ISA semantics | Semaphores that receive network adds cannot be reset safely. `SSET` checks only local queued references (`SEM_BUSY`), while `rsem` / `SIGNAL` adds from other cores may be in flight, and no protocol is given for kernel boundaries. The `>=` compare is unsigned 32-bit and `rsem` counts bytes, so 4 GiB per kernel wraps. A kernel spanning a long prefill gets close: about 2.6 GB of all-reduce bytes per core over a 32K-token DeepSeek prompt (estimated). | Use a serial-number compare (`(int32)(sem - target) >= 0`) and never reset network-facing semaphores, which is what d2d_c2c.md L-6's "monotonic, no reset" needs. Or define a kernel-boundary barrier before `SSET`. | isa.md §3; isa.yaml `SSET`, `SWAIT`, sync block |
| I-5 | M | ordering gap | `DMA_MCAST` completion is undefined. MC_WR is posted with no ack (noc.md §2), but isa.md §3 defines DMA completion as "every write acknowledged", and `SIGNAL`, `FENCE` and `HALT` promise that. So `HALT`, then the CP reading the results, can miss multicast data, and a `SIGNAL` after a multicast gives no release. | Define multicast completion as "injected", and state that only destination `rsem` orders multicast data. Or count per-destination acks (the NI knows the rectangle size). | isa.md §3, §5; memmap.md §9; noc.md §2 |
| I-6 | M | protocol (program level) | **The rail-staged all-to-all is network-safe but can deadlock at the program level.** (a) Each destination waits for the byte total before consuming. If the buffer receiving forwarded data is smaller than that total and recycled by credits, the forwarder waits for credits that come only after the total arrives: a cycle. (b) Prefill volumes do not fit SPM. For example, 2048 tokens x top-8 x d = 5120 in FP8 is 84 MB per die, 2.6 MB per core, so buffers go to HBM, where `rsem` is undefined (no owning core). (c) One byte-counting semaphore over both buffers of L-6's ping-pong is unsafe: a producer running ahead into buffer B completes the count for buffer A early. | State that destination buffers are sized for the whole exchange (from the counts). Define `rsem` on HBM writes, or a separate completion step for HBM buffers. Require one semaphore per buffer parity, or per chunk slot when forwarding. | d2d_c2c.md L-6, L-7; isa.yaml `DMA_COPY` rsem |
| I-7 | M | deadlock hazard | The link receive buffer must be dequeued per VN. L-3 gives a shared pool plus 4 reserved flits per VN, and in-order lanes. If the bridge moves packets into the leaf in arrival order, a REQ blocked on a full class-X REQ VC blocks the RSP behind it, and the reserved RSP slots do not help. `noc.py` models a separate link channel per VN (`_chan(..., vn, ...)`), so it assumes per-VN queues the spec never requires. | Require per-VN queues over the shared pool on RX, and per-VN arbitration on TX. Name it in L-9 (`ll_rx`). | d2d_c2c.md L-3, L-9; noc.md §4 rule 3 |
| I-8 | M | cross-spec conflict | hyper_connections.md G-4 shards the Engram / n-gram tables over every die and package, with direct remote row gathers. memmap.md §1 faults cross-rail accesses (`RAIL`). On DeepSeek's 4 packages, 3 of 8 dies are on the other rail of another package, so 3/8 of uniformly sharded rows are unreachable. Also, one `DMA_GATHER` has one base and PTE frames are die-local, so a sharded table needs one gather per node. | Decide: replicate the tables per rail (+197 GB on DeepSeek; re-check the arch.md §3 row with `make sweep`), or stage gathers through the sibling die (G-1 makes the latency hideable). The CP buckets index lists per node (G-2). Update G-4. | hyper_connections.md G-4; memmap.md §1; arch.md §3 |
| I-9 | M | checker coverage (xhigh item) | `hbm.rules()` / `Dram` accept three illegal sequences (probed): (a) `PREA` is a DFI `row_cmd` (H-5), but no rule or state handles it. `ACT`, `PREA` one cycle later, then `RD` to the row is accepted: no tRAS check, and nothing closes. (b) REFpb → ACT to a bank in another bank group or SID has no gap; only same-BG RRDL exists, so ACT one cycle after REFpb passes. (c) REFpb → REFpb to the same bank across a round boundary is limited only by RREFD (10 cycles), not RFCPB (264). No tRAS max either (refresh bounds it in practice). This model is the reference for the Phase 4 checker. | Add `PREA` (per-bank RAS/RTP/WRP to all banks, RP to the next ACT, and the state change), REFpb → ACT any other bank (tRREFD; confirm against JESD238), and REFpb → REFpb same bank = RFCPB. Add the three sequences to `test_hbm.py`. | sim/analytic/hbm.py `rules`, `Dram`; hbm.md H-4, H-5 |
| I-10 | M | config error | The R-11 fold for `TINY` / `MID` moves the unused PC bits into the row's low bits and leaves the bank unchanged. So the granules of a 2 KiB block that share a PC are row conflicts in one bank. Measured with `hbm.simulate()` on 8 sequential streams: `FULL` 0.99, `MID` (8 PCs) 0.68-0.72, `TINY` 0.56. `MID` is the "performance windows, calibration" config. The DFI `row_addr` is also fixed at 15 bits, but the extended row needs 17 bits (`MID`, 8 PCs) or 18 (`TINY`). | Also XOR the folded bits into `bg` / `ba`. It stays one-to-one because the bits are kept in the row, and measured it restores 0.99 for both configs. Parameterize the `row_addr` width. | hbm.md H-1, H-5; memmap.md §3; `hbm.fold_pc` |
| I-11 | M | interface tables (`core/seq`, `core/mxu`) | (a) `q_push` carries 6 x 64 captured registers, but T_DMA, T_MXU and T_VPU use 6 operand registers plus `wreg`, which is captured at issue (isa.md §3): 6 x 64 + 32 bits. (b) The MXU has no `dtab_rd` port, though `MXU_MM` takes `a_td` / `w_td`, and no other block resolves TDs for it. The SPM client list also omits the MXU's `scale_rd`. (c) TD contents are not captured at issue. A DMA write to DTAB (space `DTAB`) races queued instructions that name the entry, and there is no rule for it (IMEM has one: `FENCE`). | Widen `q_push`. Add `dtab_rd` to the MXU (or resolve TDs in the front end and queue them). Add the scale-read client. State that DTAB updates are ordered like IMEM overlays. | microarch.md §2.1, §5; isa.md §3, §4 |
| I-12 | M | missing encodings | (a) The `DMA_LIST` 32 B entry has no layout in `isa.yaml` or isa_ref.md, though collectives and the MoE all-to-all are built from it. Nothing says whether an entry can wait on a semaphore, which L-7's per-chunk forward ("as soon as each chunk's rsem count completes") needs. (b) The 128-bit NoC header has a field list but no bit layout. The NI, router, HBM controller and bridges all parse it. The burst id width is not given. Partial-ack coalescing (R-3) needs the burst's total length in every packet, because packets arrive unordered, so the target knows when a burst is complete. | Add `dma_list_entry` and `noc_header` sections to `isa.yaml`, generated like the TD, with a test that every field fits. Decide whether list entries carry a wait. | isa.yaml; noc.md §2; d2d_c2c.md L-7 |
| I-13 | L | SPM banking | (a) "Power-of-two row strides of 2 KiB-64 KiB still spread over the banks" is not true. Over 64 rows the hash gives 32, 16, 8, 4, 2 and 1 distinct banks for 2, 4, 8, 16, 32 and 64 KiB, so a 2-rows-per-cycle stationary load at 64 KiB stride hits one bank set. (b) The read budget leaves out the NI target (128 B/cycle, including write-add read-modify-write) and the MXU scale reads, so "the sum equals the port capacity" is over capacity. | Correct the text. Optionally fold `addr[20:16]` into the hash. Add the missing clients to the table. | microarch.md §2.3; memmap.md §4 |
| I-14 | L | layout cost | The 32 B HBM-write rule (R-4) applied to single-head per-token appends: a DeepSeek MXFP4 index key is 64 B of elements plus 4 B of scales. Padding the separate scale array to 32 B per token adds 41% to index-scan bytes, and the index scan is up to 12% of a step (arch.md §4). | Prescribe a tail-block rewrite instead: the compiler keeps the current 32 B scale block in SPM and rewrites it each step. | hbm.md H-2; memmap.md §8 |
| I-15 | L | stale / inconsistent text | (a) PLAN Phase 3 table: `core/mxu` "output- or weight-stationary (Phase 2 decides)", `core/seq` "scoreboard" (rejected in isa.md §10), `core/dma` "MXFP4 decompress" only. There are no `core/acc` or `core/ni` rows, though both have interface tables. (b) isa.md §7 converts Y to BF16, then scatters with `wop = ADD_F32`. (c) microarch.md §2.4 says top-k k ≤ 4096, but the VTOPK `k` field is 12 bits (max 4095); the models need ≤ 2048. (d) Subnormals in block → BF16 conversions: "kept" (numerics.md principles, N-5) vs "flush" (N-2). (e) DIECTL has one block each for the 4 D2D and 4 C2C bridges, and does not say which of 4 leaves reaches an HBM controller's CSRs. The CP mailbox address and the `START` / `STOP` offsets are missing. (f) The `SEM_ADD` window says "DMA wop ADD", but `ADD_F32` is a float add; state integer semantics. (g) `rsem` on an HBM write is undefined. (h) The DMA timeout reports `LINK` even when the stall is not a link fault. | Text fixes. For (e), add a DIECTL routing table. For (h), consider a `TIMEOUT` cause. | as listed |

## Checked with no change needed

- **Numbers recomputed:**
  - D2D: 1.87 TB/s raw, 1.45 payload, 91% of 2 UCIe-A x64 modules.
  - C2C: 150 GB/s raw, 116 GB/s payload per port.
  - Link flit: 1,248 bits.
  - Link buffers: 166 KiB and 1.26 MiB. The round trip is scaled by the flit rate: 470 cycles x 0.641 flits/cycle x 1.25 → 512.
  - Latency sums: 140 and 359 cycles.
  - Per-leaf HBM: 205 B/cycle.
  - DMA: 512 granules against the 640-cycle p99 round trip.
  - Router buffers: 42 KiB.
  - HBM capacity: 32 x 16 x 3 x 24,576 x 1 KiB = 36 GiB.
  - D1 stationary-port margins: 1.65x and 3.31x.
- **HBM map:**
  - one-to-one;
  - 2 KiB stays on one leaf;
  - 8 KiB touches all 32 PCs;
  - a 64 KiB stride reaches 16 PCs over 32 accesses.
- **Numerics:**
  - The FP8 PE adder width (39 bits) is right.
  - NVFP4 → BF16 is exact (6 significant bits ≤ 8).
  - The MXFP4 fold is exact within 14 binades.
- **Deadlock (first pass; the xhigh pass below goes deeper):**
  - Class X and the one-link rule give an acyclic order.
  - R-3 partial acks are deadlock-free. Their liveness needs X-3.
  - HBM port head-of-line blocking cannot deadlock: read and write queues drain to DRAM independently of the network, and RAW
    promotion only waits on writes already queued.
  - The staged all-to-all's two hops are independent at the network level (but see I-6).
  - Multicast with atomic fork allocation waits only forward in XY order.
- **ISA encoding:**
  - No overlapping fields in any format.
  - Opcode ranges are consistent.
  - The `mc_rect` (14 bits) and `coord` widths match the 10 x 6 router grid.
  - The PTE frame field covers 4 TiB.

## Before Phase 3

| Block | Findings to resolve first |
|---|---|
| `core/dma` | I-1, I-2, I-3, I-5, I-12(a), X-3 |
| `core/seq` | I-4, I-11 |
| `hbm` | I-9, I-10, I-12(b), X-1, X-2, X-3 |
| `common`, `arith` | none (as the self-review said) |
| Phase 4 `link`, `noc` | I-7, I-12(b), X-4, X-5 |
| compiler / runtime | I-6, I-8, I-14, X-6 |

**Not checked:**
- the generated SV / C++ against a compile (`make isa` only);
- the perf numbers in `phase1_sweeps.md`;
- the HBM3 items that need JESD238 (X-2).

## Independent review: xhigh checks (2026-11-01)

These are the two items the Not-done section reserves for xhigh. Same reviewer session, now at xhigh effort. Probes are scratch scripts,
not committed; their results are quoted below.

### A. Does `hbm.rules()` cover every DRAM timing constraint?

**Method:**
1. Enumerate the constraint classes of an HBM3-style device and look each one up in `rules()` and `Dram`.
2. **Mutation test:** delete each of the 28 rules in turn and rerun the checker tests of `test_hbm.py`. The scheduler tests cannot
   catch a missing rule, because they only check that the scheduler never violates the rules that exist.
3. Hand-made sequences against `Dram` for the gaps.

| Constraint | In `rules()` / `Dram` | Isolating test | Verdict |
|---|---|---|---|
| tRC (ACT → ACT, same bank) | yes | no | redundant: implied by tRAS + tRP |
| tRRD_L, tRRD_S | yes | yes | ok |
| tFAW (ACT and REFpb) | yes | yes | ok; scope in X-2 |
| tRCDRD, tRCDWR | yes | yes | ok |
| tRAS (ACT → PRE, and the auto-precharge point) | yes | yes | ok |
| tRP (PRE → ACT) | yes | **no**: the test called "PRE -> ACT" issues the ACT inside tRC, so it catches tRC | add a test with PRE later than tRAS |
| tRP (PRE → REFpb, PRE → REFab) | yes | no | add tests |
| tRTP (RD → PRE, RD with auto-precharge) | yes | no | add a test |
| tWR (WR → PRE, WR with auto-precharge) | yes | yes | ok |
| tCCD_L, tCCD_R (RD → RD) | yes | yes | ok |
| tCCD_L, tCCD_R (WR → WR) | yes | no | add tests |
| tCCD_S (gap 1) | yes | n/a | redundant with one column command per cycle |
| tRTW, tWTR_L, tWTR_S | yes | yes | ok |
| tRFCpb (REFpb → ACT, same bank) | yes | yes | ok |
| tRFCpb (REFpb → REFpb, same bank) | **no**: only "once per round", which resets at the round boundary | - | **gap** (I-9 c) |
| tRREFD (REFpb → REFpb) | yes | yes | ok |
| REFpb → ACT, other bank | **only** RRD_L inside the bank group; nothing for another BG or SID | - | **gap** (I-9 b); scope in X-2 |
| ACT → REFpb (RRD_L, RRD_S) | yes | no | add tests |
| tRFCab (REFab → ACT, REFab → REFpb, REFpb → REFab) | yes | no | add tests: `CTRL` can select all-bank mode, so the RTL has it |
| tRFCab (REFab → REFab) | **no**: two REFab one cycle apart are accepted | - | **gap** |
| PREA | **no**: accepted as a no-op that closes nothing | - | **gap** (I-9 a) |
| any command not in the table | **accepted silently** (as a row command) | - | **gap** |
| refresh: each bank once per round, at most 8 REFpb behind | yes | yes | ok |
| tRAS max, refresh pull-in limit | no | - | low: the scheduler bounds both (refresh closes every bank once per round) |
| power-down, self-refresh, MRS, ZQ, ECS | not modeled (H-5) | - | out of scope; the checker should reject them (row above) |
| one row and one column command per PC per cycle | yes | yes | ok |
| row and column buses shared by the two PCs of a channel | **no** | - | X-2 |

The mutation test leaves 15 of 28 rules deletable with no test failing:
- 3 are redundant: tRC, and tCCD_S for RD and WR.
- The other 12 have no isolating test: tRP (3 rules), tRTP, the two ACT → REFpb rules, REFpb → ACT in the same bank group, the three
  REFab rules, and WR → WR tCCD_L and tCCD_R.

H-5 says Phase 4 runs "the same sequences against the SV model", so those 12 rules would go unexercised in RTL too.

| # | Sev | Finding | Suggested resolution |
|---|---|---|---|
| X-1 | M | Extends I-9. Beyond PREA, REFpb → ACT in another bank group, and REFpb → REFpb in the same bank: REFab → REFab has no tRFCab gap, unknown commands are accepted, and 12 rules have no isolating test (above). | Add the missing rules. Make `Dram.issue` reject unknown commands. Add one violating sequence and one exact-gap sequence per rule, and make the mutation test a unit test: every non-redundant rule must be caught. This is what the Phase 4 SV checker inherits. |
| X-2 | M | These need JESD238 or a vendor datasheet; public sources do not settle them. (a) Whether tRREFD also covers REFpb → ACT to any other bank, which the H-4 note "(A) REFpb to REFpb" leaves out. (b) Command-bus sharing: H-1 gives each PC "a fixed DFI phase" on the channel's shared bus. That works only if every command takes one CK; an ACT occupying 2 CK (as in HBM2) leaves room for one ACT per channel per `clk_mc` cycle, not per PC. The per-PC checker and `simulate()` cannot see this. (c) Whether HBM3 has a separate write-to-write same-bank-group gap (DDR5 has tCCD_L_WR because of on-die ECC). (d) The scope of tFAW and tRRD with 3 SIDs. (e) Whether tPPD (PRE → PRE) exists. | Add these to the H-9 JESD238 item as a checklist. For (b), add a channel-scope occupancy rule to the checker (a pair of PCs), and an arbiter between the pair in H-3. The bandwidth cost is small at 1 ACT per 8-29 bursts, but the RTL must not assume independence. |

### B. Deadlock paths against noc.md §4

**Method:**
- Walked R-3 (partial acks), the rail-staged all-to-all, and the link layer (L-3, L-4) end to end.
- Ran variants of `noc.build_cdg` (2 packages) for the first pass's hazards:

| Variant | Result |
|---|---|
| baseline | acyclic |
| I-1: the DMA forwards read data as a write (response sink waits on request injection) | **cycle**: RSP-L → REQ-L → D2D → REQ-X → HBM → RSP-L on the sibling → REQ-L → D2D |
| I-7: link TX/RX shared by REQ and RSP (one FIFO, not per-VN queues) | **cycle** through two C2C links |

Both rejected variants belong in `test_noc.py`, next to the existing four.

**Checked, sound:**
- **R-3 (network level).** An eviction ack may go to any source, not just the writer of the packet being accepted. That adds REQ → RSP
  edges between arbitrary channels at the target. They cannot close a cycle, because RSP has no edge back to REQ: the DMA response sink
  is unconditional, given I-1. The HBM write queue drains to DRAM, independently of the network.
- **Staged all-to-all (network level).**
  - The C2C hop sinks into SPM (class X).
  - The forward is a new local-SPM → D2D transfer (class L → link → class X).
  - Its acks return on RSP.
  - No channel of the first hop waits on the second.

| # | Sev | Finding | Suggested resolution |
|---|---|---|---|
| X-3 | H | **R-3 has no completion trigger, a liveness bug.** hbm.md H-2 and noc.md §2 coalesce an ack "while the table has room" and ack early only when it is full. Nothing says when a burst's entry is acked otherwise. Packets arrive unordered, so the target cannot tell the last one, and the header carries only the packet length. At low load the table never fills, so a burst's final ack is never sent and the DMA (and `SIGNAL`, `FENCE`, `HALT`) waits forever. That is a hang, not a CDG cycle, so `noc.py` cannot see it. | Every WR carries its burst's total bytes (12 bits). The target acks when its count reaches the total, and also flushes entries idle for N cycles. State that an SRAM target acks only after the write (including write-add) is visible, which `rsem` and `SIGNAL` rely on. |
| X-4 | M | **Link credits can leak.** (a) Credits are returned as small increments (4 + 4 + 6 bits) on any reverse flit, including idle flits (L-3). An idle flit is not in the replay buffer, so if it fails CRC, its credits are lost for good. Each loss shrinks a VN's credit pool until that VN stalls. At 1e-15 BER that is rare but cumulative over a long run. (b) No rule says whether a freed slot returns to the shared pool or to a VN's reserve, and the flit has no pool bit. If the receiver attributes it, reserve slots can drift into the shared pool, eroding the "RSP always drains" guarantee L-3 exists for. | Return credits as cumulative counters (absolute mod 2^n, idempotent like `ack_seq`), or only on sequenced flits. Have the receiver return per-VN credits only, and the sender refill its reserve before the shared pool; or add a pool bit (20 bits are reserved). Add a CRC-error-on-idle-flit case to the Phase 4 link tests. |
| X-5 | M | **Link-down blocks the die.** L-4: on link down "the bridge stops" and transfers wait for the 100 µs DMA timeout. A stopped bridge stops consuming, so packets for that link back up through the mesh REQ-L virtual channels (cut-through holds a buffer per hop). They then block unrelated traffic on those channels: HBM reads of other cores, and the cores' own fault interrupts, which are network writes to the CP (microarch.md §5 `irq`). The CP's recovery accesses can also be blocked. | On link down, the bridge keeps sinking: requests get `ERR` (cause `LINK`), and the replay buffer and arriving packets are discarded, so the mesh drains. The DMA timeout stays as the backstop. Add a `PKG_TINY` test: link down under load, then every core faults and the CP can still reach every core. |
| X-6 | M | **Program-level deadlock from in-order queues** (extends I-6). A tile instruction waiting on a remote semaphore blocks its queue, and when the queue is full (16 entries) it blocks the in-order front end. With P = 2, take the forwarder (B, 0) (package, die) and the source (A, 0), which is also a forwarder for (B, 0)'s traffic to (A, 1). Suppose each core issues its chunk forwards (each waiting on a chunk `rsem`) before its own puts. Once a core has more than 16 chunks, both front ends block on full queues. Each core's forwards then wait for the other's puts, which are never issued. No network resource is involved. Credit-limited staging rings give the same cycle within a single queue: a source's credit wait sits ahead of its own forwards. | Add a schedule rule to d2d_c2c.md L-7 and the compiler: issue every put that depends only on local data before any instruction that waits on a remote semaphore. Keep remote waits in a queue that does not hold instructions other cores depend on, and never let the front end block behind them. Add a global "every engine blocked on a semaphore" detector to the golden model and the Phase 7 simulator, next to the SPM race checker. |
