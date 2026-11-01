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
| Spec reviewed | done, as a self-review (this document), and an independent review (below). The independent review's findings are resolved at the spec level (see Resolutions at the end), pending a verification session |
| `isa.yaml` generator emits SV / C++ / Python | done (R-6) |
| Every block has an interface table | done (R-13) |

## Not done

- **Independent review** (done 2026-11-01, see below; its findings were resolved by a fix session, see Resolutions at the end). Recommended before Phase 3 RTL starts on `core/dma`, `core/seq` and `hbm`; `common` and `arith` do not
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
| `hbm` | I-9, I-10, I-12(b), X-1, X-2(b), X-3 |
| `common`, `arith` | none (as the self-review said) |
| Phase 4 `link`, `noc` | I-7, I-12(b), X-4, X-5 |
| compiler / runtime | I-6, I-8, I-14, X-6 |

X-2 gates `hbm` only in part:
- **(b) gates it:** the channel-scope command-bus rule in the checker and the arbiter between a channel's two PCs in H-3 can be
  specified now. The RTL must not assume the PCs are independent.
- **(a), (c), (d), (e) do not:** they need JESD238 or a vendor datasheet, which is not available. Resolving them means adding them to
  the H-9 JESD238 checklist. The RTL keeps them as timing-table parameters, so the values change without an RTL change.

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

# Resolutions (fix session, 2026-11-01)

**Who:** a fix session (Opus 5.5), neither the spec author nor the reviewer. Each suggested resolution was checked against the specs
and taken, changed or rejected; the note says which and why. The reviewer's tables above are unchanged. Commits are listed by fix
group; the group column of the table maps to them.

| Group | Commit | Findings |
|---|---|---|
| 1 | 72fd92c | I-1, I-2, I-3, X-3, I-12(b) |
| 2 | 2a62587 | I-4, I-5, I-11, I-12(a) |
| 3 | 4a21399 | I-9, I-10, X-1, X-2 |
| 4 | fad67ca | I-6, I-7, I-8, I-14, X-4, X-5, X-6 |
| 5 | 18752f0 | I-13, I-15 |

| # | Status | Group | What was done |
|---|---|---|---|
| I-1 | fixed | 1 | Took the reviewer's second option: a DMA has at most one `GLOBAL` operand (`TD_SPACE`), so read data always sinks in local SPM. Global-to-global moves are two transfers through SPM, or the consumer pulls. Rejected the hardware bounce buffer: it needs a hidden SPM region managed by the DMA for a rare case. `noc.build_cdg(dma_forwards=True)` is the rejected variant in `test_noc.py` (cycle between two cores reading each other). |
| I-2 | fixed | 1 | Pages are counted in rows (`page_shift` = log2 rows per page), rows never cross a page, and a page spans whole 4 KiB frames, so 576 B rows work. Each page carries its rows' block scales in a scale region (`sc_off`, stride `aux0`), so one PTE translates both. TD fields split: `aux0` (scale stride), `row_bytes`, `sc_off`, `page_shift`, flag `SCALE_REGION`. `T_DMA` gains `pt_reg` (page table) and `ts_reg` (NVFP4 tensor scale); every operand has one meaning. A PAGED base register is a `paged_ptr` (node, first row, byte in the row). Changed from the suggestion: rows-per-page instead of a byte offset `>> page_shift`, which needs no division by the row size. |
| I-3 | fixed | 1 | DMA SPM write port 512 B/cycle and converter 256 elements/cycle. `ni_rsp` is valid/ready with a 4-flit buffer, and ready depends only on the converter and SPM port (still a guaranteed sink, noc.md §4). Rejected conversion in the MXU load path: arch.md §1 freezes dequantization in the DMA path. D1 re-checked in `make numerics` per stage: 1.65x at 0.85 share for NVFP4 → BF16 (was 0.83x at the old write port); no arch.md number changes. |
| X-3 | fixed | 1 | Every `WR` / `SEM` carries `blen` (burst total). The target acks when the count reaches it, early on a full table, and after `ACK_FLUSH` idle cycles; the flush is needed because a burst whose entry was evicted can never reach `blen` in its new entry (the reviewer's proposal had both triggers; this is why both are kept). SRAM targets ack after the write is visible. `noc.ack_coalescer` models it; tests check exact completion at table sizes 1-8 and two loads, and that the pre-fix rule hangs. |
| I-12(b) | fixed | 1 | `isa.yaml` `noc_header`: 128 bits with `type` as selector and type-dependent fields sharing bits (tag 9, len 12, addr 49, blen 12, gidx, cause, multicast rectangle). VN and class are not header fields (type and VC give them); a `hop` target that bridges rewrite plus initiator fields replace the separate destination and source fields. Generated into SV, C++, Python and isa_ref.md; tests check that fields fit, never share bits within a type, and cover their uses. |
| I-4 | fixed | 2 | Took the serial-number compare (`(int32)(sem - T) >= 0`, `isa.sem_ready`, tested across the wrap) and "never reset network-facing semaphores". Added what the suggestion left open: the two semaphore kinds (local: `SSET` allowed; network-facing: zeroed by the CP only when quiescent), and kernel bases carried forward from the previous kernel's final target, because reading the live value races with adds of the next kernel. Rejected the kernel-boundary barrier (a system-wide quiescence point per kernel). |
| I-5 | fixed | 2 | Took "completion = injected": `FENCE` / `HALT` / `SIGNAL` cover a multicast only up to injection; `rsem` is required on `DMA_MCAST` (`ILLEGAL_FIELD`) and is its only ordering; each destination waits for its full multicast byte count before halting, so all data has landed once every core has halted. Rejected per-destination acks: 32 ack flits per 16-flit burst would halve the multicast rate at the source. |
| I-11 | fixed | 2 | (a) `q_push` carries 8 x 64 captured registers for `T_DMA` (which gained `pt_reg` and `ts_reg` in group 1), 6 x 64 for MQ / VQ, plus the 32-bit wait value. (b) MXU `dtab_rd` added; a `core/dtab` table with one arbitrated read port; MXU `scale_rd` added to the SPM clients. (c) TDs are read at instruction start and DTAB updates follow the IMEM-overlay rule (FENCE before, completion wait after), checked by the golden model. Rejected capturing TDs at issue: about 15 KiB more queue per core and 4 DTAB reads per cycle. |
| I-12(a) | fixed | 2 | `isa.yaml` `dma_list_entry` (256 bits, `op` = COPY / GATHER / SCATTER / MCAST / SIGNAL): absolute addresses, TDs, `wop`, `rsem`, count, index list, multicast rectangle, SIGNAL value and mode. Decided that entries can wait (`wsem`, `wval`), which L-7's per-chunk forward needs; entries start in order. No conversion or paging in entries. Generated like the TD, with fit tests. |
| I-9 | fixed | 3 | Took all three: PREA (the PRE rules of every bank, closes every bank, RP to each), REFpb → ACT of any bank (`tRREFDA`, a new (A) table entry = tRREFD, so a different JESD238 value changes no RTL), REFpb → REFpb same bank (RFCPB). The new gap shifts single-PC results by at most 0.02 (random 64 B 0.42 → 0.40); `make hbm` regenerated, hbm.md H-6 updated. |
| I-10 | fixed | 3 | Took the suggestion: `fold_pc` XORs the folded bits into `{ba, bg}`, bank group first; still one-to-one. Measured (H6): MID 0.68 / 0.72 → 0.99, TINY 0.55 → 0.98, FULL 0.99. DFI `row_addr` is `ROW_BITS` = 15 + log2(32 / PCS). Test: MID and TINY within 0.02 of FULL, and the old fold more than 0.2 below. |
| X-1 | fixed | 3 | REFab → REFab (RFCAB) added; `Dram.issue` rejects unknown commands. `test_hbm.py` now has one violating and one exact-gap sequence per rule, and a rule-removal test: every non-redundant rule is caught; the four redundant ones (RC, CCDS x2, REFpb → ACT in the bank group) are asserted redundant under the current table and kept for other device tables. tRAS max and refresh pull-in stay unchecked (scheduler-bounded), as the reviewer rated them. |
| X-2 | (b) fixed, (a) (c) (d) (e) deferred | 3 | (b): `hbm.Channel` checks a channel-scope rule (an ACT holds the shared row bus for `ACT_CK` = 2 CK slots); H-3 step 7 specifies the phase-order arbiter between the pair, with a `chan_row` port; `simulate_channel` measures a cost of at most 0.001 (H7), and a test shows the checker fires without the arbiter. (a), (c), (d), (e): on the hbm.md H-9 JESD238 checklist, each as a table parameter. |
| I-6 | fixed | 4 | (a) Took "destination buffers sized for the whole exchange", and added that the forwarder's staging ring may stay small because a slot is freed by its forward's ack, which needs no consumption. (b) Chose the completion step over defining `rsem` on HBM: `rsem` with an HBM destination is `ILLEGAL_FIELD`, and HBM-buffered transfers end with a `SIGNAL` of their byte count; defining `rsem` at the HBM controller would make a target emit a packet to a third party. (c) One semaphore per buffer parity (per chunk slot when forwarding). Text in d2d_c2c.md L-6, L-7 and `isa.yaml` `DMA_COPY`. |
| I-7 | fixed | 4 | Took the suggestion: `ll_rx` keeps one queue per VN over the shared pool and `ll_tx` arbitrates per VN (d2d_c2c.md L-3, L-9; noc.md §4 rule 3). `noc.build_cdg(link_vn=False)` is the rejected variant in `test_noc.py` (cycle through two C2C links). |
| I-8 | fixed (decided by the user: staging) | 4 | Both options costed with the analytical model, now reproducible as `make links` L7. Chosen B: other-rail rows are gathered by the requester's sibling over C2C and pushed over D2D, one gather per owner node bucketed by the CP. DeepSeek: no capacity or arch.md change, +38 KB D2D per die per step, +0.5 µs per gather layer (hidden). Rejected A (a copy per rail): +203 GB, arch.md §3 "weights only" for DeepSeek 2 → 4. hyper_connections.md G-4, memmap.md §1, d2d_c2c.md L-7. |
| I-14 | fixed | 4 | Took the tail-block rewrite: scale rows keep their natural stride; the compiler keeps the current 32 B block in SPM and rewrites it each step (28 B of extra write per token, no extra read), instead of padding (+41% on DeepSeek index-scan bytes). hbm.md H-2, memmap.md §8. |
| X-4 | fixed (tests: Phase 4) | 4 | Took cumulative counters: `credit_req` / `credit_rsp` are 12-bit per-VN totals of freed slots on every flit, stale values ignored by serial order; the sender applies returned credits to the VN's reserve first, so no pool bit is needed. Flit stays 1,248 bits (10 reserved bits left). `links.py` updated, `make links` unchanged in L1-L6. Phase 4 tests listed in PLAN. |
| X-5 | fixed (test: Phase 4) | 4 | Took the suggestion: after link down the bridge keeps sinking, answers requests with `ERR` (`LINK`), drops responses and discards its buffers; the DMA timeout stays as the backstop (d2d_c2c.md L-4, noc.md §7). The `PKG_TINY` link-down test is listed in PLAN Phase 4. |
| X-6 | fixed (detector: Phase 7) | 4 | Took the schedule rule (local puts before any remote wait) and added two the suggestion implied: remote waits in a queue nothing else depends on, grouped into one `DMA_LIST` with per-entry waits (group 2), and no credit wait ahead of the same core's forwards. The "every engine blocked on a semaphore" detector is listed in PLAN Phase 7 and for the golden model. |
| I-13 | fixed; optional hash change rejected | 5 | (a) Text corrected with the measured spread, and a compiler rule: pad power-of-two SPM pitches by 512 B (1 KiB at 16 KiB), conflict-free for two-row loads at every pitch 2-128 KiB; `isa.spm_bank` and a test check these claims. Rejected the optional `addr[20:16]` fold: it still collides at 2 and 4 KiB; a full bit-spreading hash fixes powers of two but regresses 6 and 7 KiB pitches. (b) NI target (128) and MXU scales (64) added; the read budget is 2,240 B/cycle, 9% over the port, stated as such. |
| I-15 | fixed | 5 | (a) PLAN Phase 3: `core/mxu` weight-stationary, `core/seq` without scoreboard, `core/dma` full conversion set, new `core/acc` and `core/ni` rows. (b) isa.md §7 combine uses `ADD_BF16` for BF16 rows. (c) Top-k k ≤ 2048, `ILLEGAL_FIELD` above. (d) Block → BF16 keeps subnormals, matching N-5 and the golden model (`bf16(ftz=False)`). (e) `memmap.diectl` table (block, leaf) with 4 D2D and 4 C2C bridges, the CP `MAILBOX`, `START` / `STOP` in CORECTL; validator checks overlaps. (f) `SEM_ADD` adds an unsigned 32-bit integer, float wops fault. (g) Resolved with I-6(b). (h) New `fault_cause` `TIMEOUT`; `LINK` only from a bridge's `ERR`. |

**Proposals not taken as written** (the note in each row says why):
- I-1: forbid the pair instead of a hardware bounce buffer. I-2: pages counted in rows, not a byte offset shifted by `page_shift`.
  I-3: no conversion in the MXU load path (arch.md §1 puts it in the DMA). I-4: no kernel-boundary barrier; bases carried forward.
  I-5: no per-destination multicast acks. I-6(b): no `rsem` on HBM; `SIGNAL` instead. I-11: TDs read at start, not captured.
  I-13: the optional hash change rejected after measurement.

**Still open:**
- X-2 (a), (c), (d), (e) and the `ACT_CK` and `tRREFDA` values: hbm.md H-9 JESD238 checklist.
- Phase 4: the link tests of X-4, X-5 and I-7 and the SV checker's rule-removal test (PLAN Phase 4). Phase 7 and the golden model:
  the X-6 deadlock detector (PLAN Phase 7).

**Frozen numbers:** no arch.md frozen number changed, and arch.md needs no edit beyond its change-log row. I-8 option A would have
changed DeepSeek's "weights only" count; the user chose B.

**For the verification session, check first:**
1. I-2 end to end: one NVFP4 paged KV gather and one KV append (data rows, scale rows by `SCALE_REGION`, tail-block rewrite) through
   the TD, `paged_ptr` and `T_DMA` fields, against memmap.md §6.
2. X-3 with I-3: `ni_rsp` backpressure and the ack rules (noc.md §2) together keep noc.md §4 rule 2; `noc.ack_coalescer` matches
   the text.
3. I-4: the kernel-base rule in isa.md §3 covers a producer that starts the next kernel before the consumer finishes this one.
4. The rule-removal test in `test_hbm.py` and the `Channel` slot-order rule (commands must be presented in slot order).
5. I-5 + I-6: every multicast and HBM-buffered transfer still has a completion that the consumer can wait on.

# Verification (2026-11-01)

**Who:** a verification session (Opus 5.5), not the author, reviewer or fix session. No spec, model or test was changed; this
section is the only edit.

**Method:**
- Read the five fix commits (72fd92c, 2a62587, 4a21399, fad67ca, 18752f0) and the two record commits (23522a1, f607326). For each
  finding: does the new text close the reviewer's failure scenario, and does it open a new one? The five "check first" items above
  were walked end to end.
- Executable checks:
  - `make pytest`: 190 passed (rc 0, 18 s, 65 MB peak), as the status note says.
  - `hbm.report()`, `links.report()` and `numerics_exp.main()` regenerated into a scratch directory: identical to the committed
    reports. `isa/isa.py` rerun: generated files unchanged.
- Probes (scratch, not committed):
  - register operands per format, counted from `isa.yaml`;
  - `isa.spm_bank` two-row loads at the padded pitches, over every 256 B-aligned tile base below 128 KiB.

Verdicts: **resolved** (scenario closed, nothing new); **resolved, gap** (scenario closed, but a residual or new case is listed
below as V-n); **not resolved**.

| # | Verdict | Notes |
|---|---|---|
| I-1 | resolved | `TD_SPACE` on two `GLOBAL` operands removes the RSP → REQ edge; `dma_forwards=True` is a cycle in `test_noc.py`. `DMA_LIST` entries are not said to obey the rule (V-6). |
| I-2 | resolved, gap | (a)-(f) each closed: separate TD fields, `pt_reg`, an in-page scale region, `ts_reg`, and row-granular pages (576 B rows: 64 rows = 9 frames). The examples match the descriptors: MiniMax 4 x 128 NVFP4 KV, DeepSeek 512-wide NVFP4 KV and a 128-wide MXFP4 index key. Two gaps on item 1: the scale address ignores dims 2 and 3 (V-4), and the scale-row append is not expressible as specified (V-5). |
| I-3 | rate resolved; sink **not resolved** | Rate: the regenerated D1 table gives 1.65x at every stage for NVFP4 → BF16 (need 155 elements/cycle, capacity 256). Sink: the new `ni_rsp` ready waits on the converter, and the converter waits on scale responses that arrive on the same channel (V-1). |
| X-3 | resolved | `blen` plus the idle flush completes every burst. The flush is needed: after an eviction, the burst's second entry can never reach `blen`. `noc.ack_coalescer` matches noc.md §2 rules 1-4 ("oldest" = first allocated, idle = since the last arrival). The burst-to-target scope is not stated (V-10 b). |
| I-12(b) | resolved | Fields fit and never share bits within a type (validator and test). The 4-bit `cause` holds `TIMEOUT` = 14. ERR detail: V-10 (a). |
| I-4 | resolved, gap | The serial compare and the never-reset rule close the wrap and the reset race. The kernel-base rule conflicts with I-5's CP barrier, and without that barrier it does not protect multi-producer semaphores (V-2). The window is stated one way only (V-11). |
| I-5 | resolved | Completion = injected; `rsem` required; destinations wait on counts. So every multicast has a count to wait on (item 5). Its "the CP waits for every core to halt" is the barrier V-2 is about. |
| I-11 | resolved | `isa.yaml` has 8 register operands for T_DMA, 6 for T_MXU and T_VPU, and 2 for T_DMAMC, T_DMAL and T_SIG, which matches `q_push`. A DQ entry is 672 bits; queue storage is 7.4 KiB at 16 entries. The rule that TDs are read at instruction start is sound. The port tables do not show the arbitration (V-9). |
| I-12(a) | resolved, gap | The layout fits, and the per-entry wait is present. The spec does not say whether a waiting entry holds the shared DMA engine (V-3). |
| I-9 | resolved | PREA, `tRREFDA` and same-bank RFCPB across a round each have a violating and an exact-gap case. |
| I-10 | resolved | The XOR keeps the map one-to-one (test over 32 x 4 x 16). H6 regenerated identical: MID 0.99, TINY 0.98. `row_bits` 17 / 18 matches H-5. |
| X-1 | resolved | `test_rule_cases_cover_rules` shows every rule is either a case or listed as redundant with its condition asserted. The rule-removal test catches every non-redundant rule (item 4). Unknown commands are rejected. |
| X-2 | (b) resolved; (a), (c)-(e) deferred | Channel rule and H-3 step 7 arbiter. `simulate_channel` presents PC 0, then PC 1, each cycle, and a PC's row and column commands share a slot. So the checker's slot-order precondition holds (item 4). The rest is on the H-9 checklist. |
| I-6 | resolved | (a) Full-size destinations, and staging slots freed by forward acks, so nothing waits on consumption. (b) HBM buffers end with a `SIGNAL` after the acks. HBM acks when the write is queued, and later reads see queued writes (H-2), so the release holds (item 5). (c) One semaphore per buffer parity. List entries: V-6. |
| I-7 | resolved | Per-VN RX queues and per-VN TX arbitration are required. `link_vn=False` is a cycle in `test_noc.py`. |
| I-8 | resolved | The user's decision; L7 regenerated identical. Both steps have one local operand: a C2C gather by the sibling (on the owner's rail) into its SPM, then a D2D put with `rsem`. |
| I-14 | resolved, gap | The intent closes the 41%, but the transfer it needs cannot be written as specified (V-5). |
| X-4 | resolved (by argument) | The sender does the accounting and refills the reserve first, so in-flight flits stay ≤ pool + 8 and RSP always progresses on its reserve. 2^12 > 2 x 520. A lost idle flit or a stale replayed one is absorbed by the cumulative value. The tests are in Phase 4. |
| X-5 | resolved, gap | The sinking bridge drains the mesh. But not every affected core faults with `LINK` as the text and the Phase 4 test say, and the drain needs faulted cores to keep sinking (V-7). |
| X-6 | resolved, gap | Rules 1-3 break the reviewer's cycle, provided the four DQs progress independently. The spec does not say they do (V-3). |
| I-13 | resolved, gap | (a) The measured spread is confirmed. The padding is conflict-free at base 0 for every pitch, but not at 8 KiB + 512 B for tile bases that are not 2 KiB aligned (V-8). (b) The 2,240 B/cycle budget is stated. |
| I-15 | resolved | (a)-(h) are done as listed; the DIECTL validator and test pass. The subnormal claim holds: MXFP4 products are at least 2^-128 with 2 significant bits, so they are exact in BF16, and NVFP4 products never reach the subnormal range. (h) leads to the wording conflict in V-7. |

## Verification findings

| # | Sev | From | Finding | Failure scenario | Suggested resolution |
|---|---|---|---|---|---|
| V-1 | H | I-3 | **The `ni_rsp` sink depends on the network.** Ready "depends only on the converter and the SPM write port". But the converter cannot convert a block-format granule until its scales arrive. The scales are separate read responses (the `scale_reg` array, or the page's scale region) on the same RSP channel, returning in any order. The same holds for a fold tile split over two granules (rows whose byte length is not a multiple of 128 B). Nothing orders scales before data. | A paged NVFP4 KV gather: the 4-flit buffer fills with data granules whose scale responses are queued behind them in the RSP VC. Ready stays low, so the scales never eject. The RSP VC then blocks every packet routed through it. noc.md §4 rule 2 fails, and `noc.py` cannot see this (no protocol edge for it). | Scales must be local before any data that needs them can arrive. Either the DMA reads a tile's scales into a scale buffer reserved at issue and issues the data reads only after they land, or data sinks raw into its reserved SPM space and is converted from SPM. Apply the same rule to fold tiles that straddle granules. State it in microarch.md §2.5 and noc.md §4 rule 2. |
| V-2 | M | I-4, I-5 | **Kernel boundaries are specified two ways.** I-4 (isa.md §3) says adds of the next kernel "from a faster producer may already have arrived", which means no barrier. I-5 says the CP waits for every core to halt before it loads the next kernel, which is a barrier. Without the barrier, the carried base gives the right target but does not separate kernels. | Reduce-scatter: 32 contributors write-add into an owner's buffer with `rsem` on one semaphore. Contributor A halts kernel k, starts k + 1 and adds to the same semaphore while B's kernel-k write is still in flight. The owner reaches its kernel-k target early and multicasts an incomplete sum. A single producer is safe, because its `HALT` waits for its acks. | Choose one. (a) State the barrier: a kernel starts on a core only after every core that shares a network-facing semaphore with it has halted. Then drop the "faster producer" text. (b) Or forbid next-kernel adds to a semaphore until its consumer has passed its last kernel-k wait (a credit), or alternate semaphore sets by kernel parity. The golden model checks the chosen rule. |
| V-3 | M | I-12(a), X-6 | **A waiting list entry and the shared DMA engine.** "Four queues share one engine" (microarch.md §2.5) does not say whether a started `DMA_LIST` whose entry waits on a semaphore holds the engine. X-6 rule 2 relies on the other DQs still progressing. | DQ1 holds a list of chunk forwards waiting on remote counts. DQ0 holds this core's local puts, which the peer forwarder waits for. If the engine serves one instruction at a time, DQ0 never starts and the X-6 cycle returns. | State that the engine serves the four DQs independently: an instruction or list entry waiting on a semaphore holds nothing another DQ needs (tag table, address generator, SPM ports). Add this to the `core/dma` interface table. |
| V-4 | M | I-2 | **The paged scale address ignores dims 2 and 3.** memmap.md §6 takes `e` from `byte` plus `i0` only, although dims 2 and 3 may also address inside a row. | An NVFP4 KV row of 4 groups read with n0 = 128, n2 = 4, s2 = 64 B: groups 1-3 are converted with group 0's scales. The data is wrong and nothing faults. | Use the whole in-row offset: `e = (byte + i0 * esize + i2 * s2 + i3 * s3) / esize`. Or forbid n2, n3 > 1 on block formats. |
| V-5 | M | I-2, I-14 | **The tail-block rewrite cannot be expressed as specified.** A `SCALE_REGION` TD addresses rows of `aux0` bytes (4 or 8 B), and dims 0, 2 and 3 must stay within one row. So an aligned 32 B block is 4-8 rows, and nothing says the DMA packs consecutive paged rows into one write. | A DeepSeek index-key append writes 8 scale rows of 4 B. That is either 8 writes of 4 B (`HBM_ALIGN`) or one 32 B row (`RANGE`). Item 1's KV append fails at this step. | Specify that a PAGED transfer writes rows that are contiguous in one page as one write per 32 B-aligned span (the DMA already knows the page). Or let a `SCALE_REGION` TD address a whole aligned block of rows. Add the append to the `core/dma` tests. |
| V-6 | L | I-1, I-5, I-6 | `dma_list_entry` and the `DMA_LIST` text do not say that entries obey the one-`GLOBAL` rule, the required `rsem` on `MCAST`, or the SRAM-only `rsem`. | A list `COPY` entry with two `GLOBAL` addresses brings back I-1 if the RTL checks only `DMA_COPY`. | One sentence in isa.md §5 and the yaml description: entries are checked like the instruction they encode (`TD_SPACE`, `ILLEGAL_FIELD`). |
| V-7 | L | X-5, I-15(h) | **Link-down fault cause and draining.** Some initiators never get an `ERR`: those with requests in the discarded replay buffer, and those whose responses the bridge drops. They fault with `TIMEOUT` after 100 µs. Yet L-4 and the PLAN Phase 4 test say every core with a transfer through the link faults with `LINK`. The drain also assumes that a faulted core's DMA keeps sinking its outstanding responses and its NI keeps serving target requests. isa.md §8 ("a fault stops the core") does not say this. | The Phase 4 `PKG_TINY` test fails on a core whose request was already in flight. Or a faulted core stops accepting `ni_rsp` and blocks RSP on its die. | Answer request heads in the discarded replay buffer with `ERR` (`LINK`), or let the test accept `LINK` or `TIMEOUT`. State that a fault stops the front end and the engines but not the NI: responses are sunk and dropped, and targets keep serving. |
| V-8 | L | I-13 | The padding claim holds at base 0, which is all the test checks. At an 8 KiB + 512 B pitch, half of the 256 B-aligned tile bases collide (none of the 2 KiB-aligned ones do). Every other padded pitch from 2 to 128 KiB is conflict-free at every 256 B-aligned base. | A W tile at SPM offset 0x200 with an 8.5 KiB pitch loses half of its stationary-load rate. | Require 2 KiB-aligned tile bases for padded pitches (or pad 8 KiB by 1 KiB), and make the test cover several bases. |
| V-9 | L | I-11 | `core/dtab` has one read port, round-robin over DMA, MXU and VPU. Only the MXU `dtab_rd` row says "arbitrated"; the DMA and VPU rows still say "1-cycle". | RTL built from the DMA or VPU table assumes a fixed-latency read. | Make all three `dtab_rd` rows request/grant. |
| V-10 | L | I-12(b), X-3 | (a) `ERR` carries `tag`, but nothing says whether it is a read tag or a write burst id; these are two separate 9-bit spaces. (b) A write burst is "≤ 2 KiB" but is not required to stay on one target. | (a) The DMA cannot tell which table the tag indexes, so it cannot set `FAULT_ADDR` from the right one. (b) A burst split over two targets (a 264 B scatter row across a 2 KiB boundary) reaches `blen` at neither and completes only by the idle flush: correct, but 64 cycles late. | (a) A read/write bit in `ERR` (the `rsv` bits are free) or one shared tag space. (b) State that a burst stays inside one 2 KiB-aligned block (the DMA splits there), as reads do. |
| V-11 | L | I-4 | isa.md §3 states the serial window one way only (the target less than 2^31 ahead). The compare also fails when the value is more than 2^31 past a target. | A wait on a stale target of a byte counter blocks. Buffer sizes bound this in practice. | Write `|value - target| < 2^31`, as `isa.sem_ready` already says. |

## Before Phase 3, after verification

| Block | Still open |
|---|---|
| `core/dma` | V-1, V-3, V-4, V-5; text only: V-6, V-9, V-10 (a) |
| `core/seq` | V-2 (a decision on the kernel launch rule) |
| `hbm` | none. I-9, I-10, I-12(b), X-1, X-2(b) and X-3 are verified; X-2 (a), (c)-(e) stay on the H-9 checklist |
| `common`, `arith` | none |
| Phase 4 `link`, `noc` | V-7 |
| compiler / runtime | V-2, V-8 |

**Not checked:**
- the JESD238 items (still unavailable);
- the generated SV and C++ beyond the lint in `make isa`;
- the L7 inputs (lookups per token, table dimensions) beyond regenerating the table;
- X-4 against an executable model (checked by argument; the tests are in Phase 4).


# Resolutions (verification fixes, 2026-11-01)

**Who:** a second fix session (Opus 5.5). It is not the author, the reviewer, the first fix session or the verifier. Each suggested
resolution was checked against the specs and then taken, changed or rejected; each row says which and why. The reviewer's and the
verifier's tables above are unchanged. Branch `spec-verification-fixes` from 312f927, one commit per group, `make pytest` green
before each.

| Group | Commit | Findings |
|---|---|---|
| 1 | 7575888 | V-1, V-3, V-4, V-5 (`core/dma`) |
| 2 | 2bd39fa | V-2 (`core/seq`, decided by the user) |
| 3 | eb1f153 | V-6 to V-11 |

| # | Status | Group | What was done |
|---|---|---|---|
| V-1 | fixed | 1 | Took the first option ("scales are local before the data"), changed where they land, and extended it to every split unit. Out-of-line scales (`scale_reg`, page scale region) are read into an SPM staging ring (CSR `DMA_STAGE`, runtime-reserved, 16 KiB = two round trips of NVFP4 scales) before any data read that needs them. A conversion unit whose bytes span responses (a `SCALE_INLINE` row such as a 264 B Engram row, or a fold tile across a 256 B granule) gets a staging slot when its reads are issued and is converted from SPM. Rejected a scale SRAM inside the DMA: 16 KiB per core adds 512 KiB per die to the frozen SRAM row. Rejected converting all data from SPM (the second option): raw plus converted bytes on the 512 B/cycle write port would cut NVFP4 → BF16 to 1.29x. Cost: the staged scales share the write port, so D1 for NVFP4 → BF16 is now 1.60x at 0.85 share (was 1.65x; fold margins unchanged; `make numerics` regenerated, only the D1 table changed), and the first data of a converting transfer arrives one round trip later. microarch.md §2.5 and `core/dma` table, noc.md §4 rule 2, isa.md §5, numerics.md D1, arch.md change log. `noc.build_cdg(scales_after_data=True)` is the old rule: a cycle on the response route into a core (`test_noc.py`). Left as a Phase 3 option: choosing the fold exponent from the block scales alone, which removes the split-tile case but changes N-2. |
| V-3 | fixed | 1 | Took it. Each DQ has its own sequencer (head, its wait, address-generator state). Tags, staging ring, SPM ports and NI injection are taken per packet, round-robin over the DQs that have a packet ready, and only after the head's wait is satisfied. microarch.md §2.5 and the `core/dma` `q_pop` row, isa.md §3, d2d_c2c.md L-7 rule 2. A Phase 3 test is listed in PLAN. |
| V-4 | fixed | 1 | Took the formula: the scale comes from the whole in-row offset `o`. Added that `byte`, `s2` and `s3` are multiples of the block's bytes (`ILLEGAL_FIELD`), so every dim-0 run starts on a block. Rejected forbidding n2, n3 > 1: the MiniMax 4-group KV read needs n2 = 4. The same rule is now stated for non-paged operands (`scale_reg` = the scale of the base element's block). `isa.paged_addr` is the reference, with `isa.yaml` `dtype_info` (element bits, block, scale dtype). `test_paged_scale_uses_whole_in_row_offset` shows that the old formula gives groups 1-3 group 0's scales. |
| V-5 | fixed | 1 | Took the first option, generalized: a write packet holds contiguous destination bytes. Adjacent runs merge, consecutive paged rows of one page included, up to a 256 B granule, and `HBM_ALIGN` is checked per packet. Rejected a whole-block `SCALE_REGION` mode: it is a second addressing mode for what merging already gives. Also stated that `cvt = NONE` moves no scales, which the two-transfer append assumed. memmap.md §6 worked example: DeepSeek MXFP4 index key (1,024-row pages = 17 frames; a 64 B data packet and 8 scale rows merged into one 32 B packet) and MiniMax NVFP4 KV (128-row pages = 9 frames; 256 B + 32 B packets, no tail block needed), with the `T_DMA` fields and the read-back scale addresses. `test_paged_append_walk` computes the example from packed TD and `paged_ptr` fields, including MiniMax's 8 B index-key rows. PLAN Phase 3 `core/dma` lists the tests. |
| V-2 | fixed (decided by the user: credit) | 2 | Costed the three options and asked. (a) Barrier per kernel: +1 system-wide sync per boundary, about 0.6-4.5 µs across 8 dies, which is 0.4-2.7% of a DeepSeek decode step at one kernel per layer, and 0 semaphores. (b1) The L-6 parity plus consumer credit, carried across kernels: 0 extra sync points and 0 extra semaphores. (b2) Kernel-parity sets: twice the network-facing semaphores, and kernel k + 2 still needs a credit or a lagged barrier. Chosen: (b1). isa.md §3 now says there is no kernel barrier, that every network-facing semaphore follows L-6, and that parities, credits and bases carry over. The I-5 text now says the CP waits for halts before it reads results, not before a core's next kernel. d2d_c2c.md L-6 "Across kernels"; PLAN: the `core/seq` row and the golden-model check. |
| V-6 | fixed | 3 | Took it: list entries are checked like the instruction they encode (`TD_SPACE`, `rsem` rules). Added that `FAULT_ADDR` holds the entry's SPM offset. isa.md §5, `isa.yaml` `DMA_LIST` and `dma_list_entry`. |
| V-7 | fixed | 3 | Took both halves, with a change. A down bridge answers the request heads in its replay buffer with `ERR` (`LINK`) before it discards the buffer. Requests the far side had already accepted cannot get an `ERR`, because bridges keep no table of outstanding requests. They fault with `TIMEOUT`, and the Phase 4 test accepts `LINK` or `TIMEOUT`. isa.md §8: a fault stops the front end and the engines, not the NI (responses are sunk and dropped, and targets keep serving). d2d_c2c.md L-4, L-11, noc.md §7, PLAN Phase 4. |
| V-8 | fixed | 3 | Took the alignment option: padded stationary-load tiles start on a 2 KiB boundary. Rejected padding 8 KiB by 1 KiB. Measured over every base below 128 KiB: it fixes the 256 B-aligned bases, but at bases that are only 64 B aligned (which memmap.md §4 allows) most padded pitches collide anyway. So an alignment rule is needed either way, and 2 KiB alone covers every pitch. `test_spm_bank_strides_over_tile_bases`: every padded pitch is conflict-free at all 2 KiB-aligned bases, and at all 256 B-aligned bases except 8 KiB + 512 B, where 256 of 512 collide (0x200 is one). |
| V-9 | fixed | 3 | Took it: the DMA, MXU and VPU `dtab_rd` rows and the `core/dtab` `rd` row are request/grant, with data 1 cycle after the grant. microarch.md §5. |
| V-10 | fixed | 3 | (a) Took the bit: `noc_header` `err_wr` (bit 58, `ERR` only) says whether `tag` is a read tag or a write burst id. Rejected one shared tag space, which would tie the sizes of the two tables. The field is generated, and the header test checks it. (b) Took it: a write burst stays inside one 2 KiB-aligned block, so it has one target, as reads do. noc.md §2, microarch.md §2.6, memmap.md §4. |
| V-11 | fixed | 3 | Took it: isa.md §3 states `|value - target| < 2^31` in both directions, and that the carried base and the credits bound the stale side. |

**Proposals not taken as written** (the row says why): V-1 (an SPM ring instead of a buffer in the DMA, extended to inline scales
and split fold tiles), V-4 (formula plus block alignment, n2 > 1 kept), V-5 (a general merge rule instead of a block mode), V-7
(`TIMEOUT` accepted for requests the far side had accepted), V-8 (alignment instead of a 1 KiB pad), V-10 (a) (a bit, not a shared
tag space).

**Regenerated:**
- `make numerics`: D1 feed table only. NVFP4 → BF16 1.65x → 1.60x; the fold rows' SPM write column is now 482-496 B/cycle, margins
  unchanged.
- `make isa`: CSR `DMA_STAGE`, `noc_header.err_wr`, and the `dtype_info` table, which is validated.
- `make hbm` and `make links`: their inputs did not change (text only in hbm.md and d2d_c2c.md), so they were not rerun.

**Frozen numbers:** none changed. arch.md has a change-log row. A dedicated scale SRAM would have changed the SRAM row, and V-1
avoids it.

**Tests:** `make pytest` 197 passed (190 before). `test_isa.py` 18 → 23: three append walks, the in-row scale offset, SPM banks over
tile bases. `test_noc.py` 17 → 19: the V-1 rejected variant and its cycle's location.

## Before Phase 3, after the verification fixes

| Block | Still open |
|---|---|
| `core/dma` | none at the spec level (V-1, V-3, V-4, V-5, V-6, V-9, V-10 fixed); the Phase 3 tests are listed in PLAN |
| `core/seq` | none (V-2 decided: credit) |
| `hbm` | none; X-2 (a), (c)-(e) stay on the H-9 checklist |
| `common`, `arith` | none |
| Phase 4 `link`, `noc` | V-7: text fixed, test in Phase 4 |
| compiler / runtime | rules to implement: V-2 (credits across kernels, golden-model check), V-8 (2 KiB-aligned padded W tiles) |

**For the verification pass, check first:**
1. **V-1 end to end.** A paged NVFP4 KV gather and a weight stream through `scale_reg`: every response must convert from ring-local
   state only. Then the ring itself: space is allocated at read issue and freed at conversion, and a split unit's reads are issued
   together. Can the ring fill with scales whose data can never be issued?
2. **V-1 with V-3.** The ring is shared by the four DQs. Check that no DQ can hold ring space while its head waits, and that X-6
   rule 2 still holds.
3. **V-7 duplicates.** A request in a down D2D sub-link's replay buffer may have reached the far side, and its response may come
   back over another sub-link. Then the initiator gets both the response and an `ERR` (`LINK`), possibly for a reused tag. The core
   faults either way, but `FAULT_ADDR` may be wrong. Is that acceptable?
4. **V-2.** The credit rule must cover every network-facing semaphore: collectives, pipeline hand-off, HBM `SIGNAL` buffers,
   multicast destinations. The I-5 text must no longer imply a barrier. Check V-11's stale bound under the carried base.
5. **V-4 / V-5 worked example** against `isa.paged_addr` and `test_paged_append_walk`. A tail block never crosses a page (rows per
   block divides rows per page). Check how the merge rule interacts with HBM write merging (H-2) when the block is rewritten every
   step.
6. **D1 consistency.** Nothing outside the arch.md history row should still quote 1.65x for NVFP4 → BF16.


# Verification 2 (2026-11-01)

**Who:** a second verification session (Opus 5.5). It is not the author, the reviewer, either fix session or the first verifier. No
spec, model or test was changed; this section is the only edit.

**Method:**
- Read the four commits of the second fix session (7575888, 2bd39fa, eb1f153, a95d18f). For each V-n: does the new text close the
  verifier's failure scenario, and does it open a new one? The six "check first" items above were walked. Group 1 was also checked
  against what was verified before (I-1, I-2, I-3, X-3, X-6 rule 2, D1).
- Executable checks:
  - `make pytest`: 197 passed (rc 0, 20.5 s, 67 MB peak).
  - `numerics_exp.main()`, `hbm.report()` and `links.report()` regenerated into a scratch directory: all three identical to the
    committed reports (144 s, 394 MB peak). `isa/isa.py` rerun: `git status` clean afterwards, and `verilator --lint-only -Wall`
    on `isa_pkg.sv` passes.
- Probes (scratch, not committed):
  - `noc.build_cdg(scales_after_data=True)`: the cycle `find_cycle` returns is a self-loop on one RSP channel (the variant adds 784).
    Without self-loops a 3-channel cycle remains on one RSP route in die (0, 0). So the rejected-variant test does not rest on the
    self-loop alone. Baseline: acyclic, 1,872 channels.
  - Staging ring, a cycle model of microarch.md §2.5. Scale reads run ahead as far as the ring allows, the data read waits for its
    scales, round trip L, HBM share 87 B/cycle, converter 256 elements/cycle. "As written": scale space is taken at scale-read
    issue and the raw slot at data-read issue. "Atomic": both are taken at scale-read issue.

    | stream | as written | atomic |
    |---|---|---|
    | NVFP4 → BF16 weights (32 B of scales per 256 B granule), L = 500 / 800 / 1,000 | 151 / 149 / 125 elements/cycle | same |
    | MXFP4 fold, every 128 B tile split (8 B of scales each), L = 500 | **deadlock at cycle 687**: 16 KiB of scales for 2,048 tiles whose data is not issued | 30 elements/cycle |

    The need at 0.85 share is 155 elements/cycle. With an 8 KiB ring the NVFP4 stream drops to 75 at L = 845.
  - `isa.paged_addr` and `isa.write_packets` against memmap.md §6 by hand (DeepSeek index key, t = 6027, q = 907; MiniMax KV,
    t = 300). Data `0x12345e2c0` and one 32 B scale packet at `0x123460e20`, both equal to the hand values. The DeepSeek read-back
    formula and the MiniMax gather formula (all g, i0) match `paged_addr`. A `DMA_SCATTER` append of 4 sequences with one index
    each gives four 4 B packets (`HBM_ALIGN`). With 8 consecutive indices per sequence it gives four aligned 32 B packets. 8 scale
    rows from q = 1020 give two 16 B packets, one per page. With `page_shift` 2 and `aux0` 4, every block spans two pages.
  - `isa.spm_bank` over all 64 B-, 256 B- and 2 KiB-aligned bases below 128 KiB, every padded pitch: no 2 KiB-aligned base
    collides. Every pitch has 64 B-aligned bases that collide (384-1,536 of 2,048).
  - `grep 1.65`: the only NVFP4 → BF16 quotes are the arch.md history row and status.md's dated first-fix entry. numerics.md D1
    and numerics_experiments.md still give 1.65x, but for the NVFP4 → E4M3 fold, which is unchanged and correct.

| # | Verdict | Notes |
|---|---|---|
| V-1 | resolved, gap | Check-first item 1. A paged NVFP4 KV gather (page table, then the page's 32 B scale row, then the 256 B data row once the scale has landed) and a `scale_reg` weight stream (the scales of each ≤ 2 KiB data read first) convert every response from the ring, registers (`ts_reg`) and the response itself. That closes the scenario. The 16 KiB ring fits the stated rate: 16,384 / 16 B = 1,024 cycles at the converter's rate, and at the 0.85 need (9.7 B/cycle of scales) it covers round trips up to about 845 cycles (probe). The ring can deadlock, though. A split unit with out-of-line scales takes ring space twice, and greedy scale read-ahead can fill the ring before any raw slot is taken (W-1). A split unit's own remaining reads always issue: they need only tags, and tags are freed by responses that sink unconditionally. The cost of "first data one round trip later" was not costed. It is about 0.33 µs per dependent converting transfer (index scan, then top-k, then KV gather), at most about 2% of a batch-1 decode step for DeepSeek or MiniMax. noc.py: see W-1. |
| V-2 | resolved, gap | Check-first item 4. Under the credit, the reduce-scatter scenario is closed: a parity's semaphore can never hold another use's bytes. Collectives, pipeline hand-offs and HBM `SIGNAL` buffers are named. Multicast destinations are covered once the source counts one credit per destination, which the text does not say. No remaining text implies a barrier: isa.md §3 (I-5), d2d_c2c.md L-6 and PLAN were checked. V-11's stale bound holds: a parity's semaphore never passes its current target before the wait, because the next add to it needs this wait's credit, and a credit semaphore is at most one use ahead. Gaps: semaphores without a buffer (credits, SIGNAL-only handshakes), reuse of a semaphore index by another call site, and data-dependent totals (W-3). |
| V-3 | resolved | Check-first item 2. Tags, ring, SPM ports and injection are taken per packet, and only after the head's wait is satisfied. What a started transfer holds is freed by sinking, which is core-local. So a waiting head holds none of them. L-7 rule 2 holds independently of the ring: `DMA_LIST` entries carry no conversion (isa.md §5), so forwards never use it. Even the W-1 deadlock stops only its own DQ (it holds ring space, no tags), not the forwards. |
| V-4 | resolved, gap | The paged formula and the `byte`/`s2`/`s3` alignment rule are correct (probe, test). The non-paged formula leaves out the base element's position inside its block, and nothing requires the base to be block-aligned (W-5). |
| V-5 | resolved, gap | The merge rule makes the DeepSeek and MiniMax appends one aligned packet each (probe = memmap.md table = test). Not covered: the batched append (`DMA_SCATTER`), blocks that span a page, and the H-2 safety argument for rewriting a block every step (W-4). |
| V-6 | resolved | isa.md §5, the `DMA_LIST` description and the `dma_list_entry` comment agree. The comment also lists `HBM_ALIGN`. |
| V-7 | resolved, gap | Check-first item 3. The replay-buffer `ERR` closes the drain. It can be stale, though, and it can arrive after the real response, with the tag already reused (W-2). The fault outcome is acceptable, but the DMA's handling of an `ERR` for a tag that is not outstanding is unspecified. |
| V-8 | resolved | The rule and the test match the probe. The claim about 64 B-aligned bases holds (every padded pitch has some that collide). |
| V-9 | resolved | All three `dtab_rd` rows and `core/dtab` `rd` are request/grant. |
| V-10 | resolved | `err_wr` is bit 58, `ERR` only. The validator and the header test pass. The burst rule is in noc.md §2, microarch.md §2.6 and memmap.md §4. |
| V-11 | resolved | The text matches `isa.sem_ready`. For the bound under the carried base, see V-2. |

**Earlier verdicts, re-checked after group 1:** I-1 (the ring is local SPM, so scale reads keep one local operand), I-2 (the new
`ILLEGAL_FIELD` rule accepts every worked example: `byte` 0, s2 = 64 B), I-3 (rate 1.60x; sink: V-1 above), X-3 (merged packets stay
inside a burst and a granule), X-6 rule 2 (V-3 above) and D1 (1.60x = 512 / (2 + 1/16) / 155; 1.36x at peak) are not reopened.

## Verification 2 findings

| # | Sev | From | Finding | Failure scenario | Suggested resolution |
|---|---|---|---|---|---|
| W-1 | M | V-1 | **The staging ring can fill with scales whose data can never be issued.** Space is "allocated when a read is issued". So a split unit with out-of-line scales (a fold tile across a granule from `scale_reg`) takes scale space at its scale read and its raw slot later, at its data read. Nothing bounds the scale read-ahead except ring space. | A 4-bit weight stream whose rows are not a multiple of 128 B, folded to E4M3. The read-ahead fills 16 KiB with the scales of 2,048 tiles. No data read can get its 128 B raw slot, nothing is converted, and nothing is freed (probe: deadlock at cycle 687). The DQ hangs. No surveyed model has such rows (expert K = 2,048-6,144 and 2,304: multiples of 256), so it is reachable only through other shapes or an unaligned K slice. When all tiles are split, the atomic rule still limits the ring to 30 elements/cycle, against a need of 164. | Reserve a unit's raw slot together with its scales at scale-read issue (or cap scale read-ahead so that the largest raw unit always fits). State that 16 KiB is sized for scales only, so the compiler pads 4-bit rows to 128 B, or accepts the rate. noc.py works at channel level and cannot see ring allocation. It can model scales-first as a `req_out(core) → rsp_in(core)` edge, which must stay acyclic. The allocation rule belongs in a DMA-level reference model (like `isa.write_packets`) with a mixed split / non-split stream and greedy read-ahead, plus a `core/dma` formal property: every allocated ring entry needs only tags to complete. |
| W-2 | L | V-7 | **A replay-buffer `ERR` can be stale and can follow the real response.** When the reverse path fails, the far side has accepted every flit still in the replay buffer, because only the ACKs are lost. Link-down then needs more than 8 timeout-driven replays at 64 cycles each, about 576 cycles. The far side's response returns by another sub-link (any sub-link may be used, noc.md §6) in roughly 100-300 cycles, and the tag is freed and can be reused before the `ERR` arrives. | A core's read completes normally and its tag is reused by a request to local HBM. The bridge's `ERR` (`LINK`) then hits the new request, and the core faults with `LINK` and a `FAULT_ADDR` that names a request that never used the link. Or the tag is free, and the DMA's behavior is unspecified. The Phase 4 test as written ("every core with a transfer through the link faults with `LINK` or `TIMEOUT`") misses these spurious faults. The data outcome is acceptable: a link-down restarts the step, and the CP zeroes network-facing semaphores at the quiescent restart (isa.md §3). So executed-but-`ERR`'d writes and their `rsem` counts do not survive. | State three things. (a) An `ERR` (`LINK`) always faults the core, whether or not its tag is outstanding. (b) A response for a tag that is not outstanding is dropped. (c) During a link-down event `FAULT_ADDR` is advisory, and the CP restarts every core of the step regardless of which cores faulted. Let the Phase 4 test allow `LINK` on cores whose transfers completed. |
| W-3 | M | V-2 | **The credit rule covers semaphores that belong to a buffer, and not all network-facing semaphores do.** Credit semaphores and SIGNAL-only handshakes (count exchanges, ready flags) have no receive buffer. The rule also does not say what happens when a semaphore index serves another call site in a later kernel. With 63 semaphores per core and many call sites per step, indices will be reused. "Totals per use are fixed at compile time" is false for the MoE all-to-all: its byte totals come from all-gathered counts, so the CP cannot carry them with the launch arguments. A multicast buffer needs one credit from each destination. | Kernel k uses S20 for a SIGNAL-only "counts ready" handshake from 8 dies. Kernel k + 1 (another binary) maps S20 to a pipeline hand-off. A fast producer that has already halted k adds to S20 before the consumer's kernel-k wait. The wait passes early and reads stale counts. Nothing in isa.md §3 forbids this. | State that every network-facing semaphore, credits and handshakes included, is allocated to one call site for the whole program, like the buffers. It moves to another call site only at a quiescent point, or after a final credit from its last consumer. The program, not the CP, carries data-dependent bases (in SPM). A credit wait counts one credit per consumer (n for a multicast). Have the golden-model check cover the reassignment case. |
| W-4 | L | V-5, I-14 | **Rewriting a tail block: ordering, batching and page bounds.** (a) hbm.md H-2 says the rewrite "is safe because" writes merge in the queue. Merging keeps arrival order, but the network does not keep issue order: planes are chosen per packet. Decode is safe only because the next step's rewrite depends on this step's attention, which waited for this write's ack. (b) The batched append is a `DMA_SCATTER`, and one index per sequence gives sub-32 B packets. It needs 32 / `aux0` consecutive indices per sequence, which no text or test shows. (c) Nothing requires rows per page ≥ 32 / `aux0`, so a block can span two pages. | (a) MTP or chunked prefill appends two tokens of one block in one step as two transfers. The older block lands last and loses a token's scales, silently. (b) A batch-64 decode append written as in the worked example faults `HBM_ALIGN` (probe: four 4 B packets). (c) `page_shift` 2 with 4 B rows: every block is two 16 B packets (probe). | (a) Correct the H-2 reason. Two writes to one block need the first one's ack before the second is issued, or must be one write. Name the cases (several tokens in one step, more than one writing core). (b) Add the scatter form to memmap.md §6 and to the PLAN `core/dma` tests. (c) Require `page_shift` ≥ log2(32 / `aux0`) for block-format pages (`ILLEGAL_FIELD`). |
| W-5 | L | V-4 | **The non-paged scale formula assumes a block-aligned base.** `R[scale_reg]` is "the scale of the base element's block", and `o` leaves out the base's offset inside that block. The block-multiple rule names `byte`, s2 and s3 only. | An NVFP4 weight slice that starts at element 8 of a row: its element 8 is element 16 of the row (block 1), but it is converted with block 0's scale. The data is wrong and nothing faults. | Require the base address and s1 of a non-paged block-format operand to be multiples of `block_bytes` (`ILLEGAL_FIELD`), as for `byte`/s2/s3. Or add the base's in-block offset to `o`. Add a non-paged reference next to `isa.paged_addr`. |
| W-6 | L | V-1, V-3 | Text. (a) noc.md §4 rule 2: the I-1 paragraph ("In particular a DMA never sends read data on...") now reads as a continuation of the new V-1 sub-bullet. (b) microarch.md §2.5 says a started transfer holds ring slots "only until its responses sink". A split unit's slot and its scales are held until conversion. | A reader takes I-1's rule for part of V-1. RTL frees a raw slot at the first response. | (a) Make it its own bullet. (b) Say "until its data has been converted", as the ring bullet does. |

## Before Phase 3, after verification 2

| Block | Still open |
|---|---|
| `core/dma` | W-1 (the ring allocation rule; one sentence plus a reference model); text only: W-4 (b), (c), W-5, W-6 |
| `core/seq` | none. W-3 is a compiler and runtime rule; the semaphore hardware (serial compare, never-reset rule) is unchanged |
| `hbm` | none; W-4 (a) corrects H-2's wording, not the controller. X-2 (a), (c)-(e) stay on the H-9 checklist |
| `common`, `arith` | none |
| Phase 4 `link`, `noc` | W-2 (text and test expectation) |
| compiler / runtime | W-3, W-4 (a), (b); V-2 credits and V-8 alignment as before |

**Not checked:**
- the JESD238 items (still unavailable);
- the generated SV and C++ beyond the lint in `make isa`;
- X-4 against an executable model (Phase 4);
- the V-2 barrier cost (0.6-4.5 µs), which was not recomputed;
- step-restart semantics after a link-down beyond the semaphore zeroing (for example, re-zeroing write-add buffers);
- the ring's allocation granularity (CSR fields are 64 B aligned): if slots are 64 B, a KV gather's 32 B scale rows use half the
  ring;
- `SCALE_INLINE` scale location (Engram rows), which memmap.md does not define and V-4 did not touch.


# Resolutions (verification 2 fixes, 2026-11-01)

**Who:** a third fix session (Opus 5.5). It is not the author, the reviewer, the earlier fix sessions or either verifier. Each
suggested resolution was checked against the specs and then taken, changed or rejected; each row says which and why. The
tables above are unchanged. Branch `spec-verification2-fixes` from d8f4f23, one commit per group, `make pytest` green before
each.

| Group | Commit | Findings |
|---|---|---|
| 1 | b62bbc4 | W-1, W-4 (b, c), W-5, W-6 (`core/dma`) |
| 2 | 0e23f87 | W-3, W-4 (a) (compiler and runtime rules) |
| 3 | b8b38b8 | W-2 (Phase 4 text and test) |

| # | Status | Group | What was done |
|---|---|---|---|
| W-1 | fixed | 1 | Took the first option: **an entry's scales and the raw slots of all its split units are one allocation, taken at scale-read issue** (at the first data read if there is no scale read). An entry is one scale read and the data reads its scales cover, at most a quarter of the ring; the ring is circular, in 16 B units, reclaimed in allocation order. Every allocated entry then needs only tags, which responses free unconditionally, so the oldest entry always completes. Rejected the capped read-ahead: in a ring it also deadlocks, because a raw slot freed behind newer scale entries comes back only in allocation order. It works only with a free-list allocator, and even then it carries 16.5 elements/cycle on the all-split fold stream against 27.6 for the atomic rule (probe, not committed). `isa.staging_ring` is the DMA-level reference (next to `isa.write_packets`): a cycle model of one core's ring, tags, HBM share and converter, with greedy scale read-ahead and 1-4 DQs. Tests: mixed split / non-split streams in one DQ and four, at 4 and 16 KiB and round trips of 100-1,000 cycles, plus 25 random mixes, always complete; the old rule ("split") and the capped rule deadlock, and the old rule deadlocks at cycle 687 on the all-split fold, the verifier's number. **16 KiB is sized for scales:** NVFP4 → BF16 at the 87 B/cycle share runs at 154.7 / 152.9 / 127 elements/cycle at L = 500 / 845 / 1,000 (the verifier had 151 / 149 / 125), 8 KiB gives 76 at 845. **4-bit rows that are not whole tiles:** the compiler pads the HBM row pitch of fold-converted weights to whole 256-element tiles (128 B at 4 bits, 256 B for MXFP8), places them on 256 B, and cuts K slices on tile boundaries, so no fold tile is split (no surveyed model needs padding); an unpadded layout is legal at the ring-limited rate (about 28 elements/cycle if every tile is split, against 164). Engram rows (inline scales, always split) get about 0.12 rows/cycle per core, about 2 µs per DeepSeek batch-256 step, hidden by G-1. Took the optional `noc.py` part: the baseline now has the scales-first `req_out(core) → rsp_in(core)` edges (6,992 edges, was 4,336; 4 packages 13,560, was 8,352) and stays acyclic. The `core/dma` formal property (every allocated ring entry needs only tags) is in the PLAN Phase 3 row. microarch.md §2.5, noc.md §4, isa.md §5, `isa.yaml` (`DMA_STAGE`, `DMA_COPY`). |
| W-2 | fixed, extended | 3 | Took (a)-(c) and added (d). (a) An `ERR` (`LINK`) always faults the core, and it frees nothing, because it may name a tag a newer request holds. Other `ERR` causes come from the target and free their tag. (b) A response or ack for a tag or burst id that is not outstanding is dropped. (c) `FAULT_ADDR` is advisory during a link-down event, and the CP restarts every core of the step. (d) is new: a faulted core's tags are freed only by their responses or by the timeout, `STATUS` shows the DMA busy until then, and the CP restarts a core only after that, at most 100 µs after the fault. Without (d), a stale `ERR` would free a reused tag whose real response is still in flight, and that response could match a tag of the restarted step. The Phase 4 test now covers a failed reverse path: `LINK` faults on cores whose transfers completed, no other cause, and an injected duplicate response for a reused tag rejected after the restart. d2d_c2c.md L-4, L-11, isa.md §8, noc.md §7, `isa.yaml` (`STATUS`, `LINK`), PLAN Phase 4. |
| W-3 | fixed | 2 | Took it. Checked first whether it needs a design choice beyond the suggestion; it does not, so I did not ask. Every network-facing semaphore (buffer counts, credits, `SIGNAL`-only handshakes) belongs to one call site for the whole program, and the compiler records the map. Refinement: a credit or flag counts events, so with an exact base its own waits never pass early, and it needs no credit of its own. Its only hazard is adds from another call site. **Reassignment** from X to Y happens at a quiescent point, or after the owner's last wait at X with every add of Y causally after that wait. Remote adders wait for a **final credit** from the owner. For a credit semaphore, the owner issues Y's first write after its last X credit wait (same DQ or a local semaphore), because those adds are caused by its own writes. Cost: one `SIGNAL` per adder per move, usually off the critical path. **Bases:** the CP may write compile-time bases. Data-dependent ones (the MoE all-to-all) are carried by the program in an SPM base table (64 x 4 B, persistent across kernels, zeroed by the CP with the semaphores). **Multicast:** a credit wait counts one credit per consumer. The golden-model check (PLAN Phase 7) now covers reassignment, the bases and multicast credits. Rejected: more semaphores per core. The 6-bit fields are in every sync block, the NoC header and list entries, so it would be an encoding change that only moves the limit. isa.md §3, d2d_c2c.md L-6. |
| W-4 | fixed | 1 (b, c), 2 (a) | (a) Took it: hbm.md H-2's reason is corrected. The controller keeps arrival order and the network does not keep issue order, so two writes of one block are one write (several tokens in one step: MTP, speculative decoding, chunked prefill) or the second waits for the first one's ack, issued from one owner core per block. The compiler emits the step-to-step wait even when it is implied, because a writer whose attention reads the current token from SPM has no such dependency. The write-after-write rule in H-2 and memmap.md §6 and §8 says the same. (b) Took it: memmap.md §6 "Batched append": the scale scatter lists 32 / `aux0` consecutive indices per sequence from the block's first row, and one index per sequence gives `aux0`-byte packets (`HBM_ALIGN`). `test_paged_scatter_append` covers four DeepSeek sequences in their own pages, with the failure case. `isa.yaml` `DMA_SCATTER`, PLAN Phase 3. (c) Took it, generalized: a page's scale rows must tile whole aligned 32 B blocks. That means `aux0` divides 32 or is a multiple of it, and `page_shift` ≥ log2(32 / `aux0`), else `ILLEGAL_FIELD`, for every PAGED block-format or `SCALE_REGION` TD. The bound alone would allow a 12 B row that straddles two blocks. Checked in `isa.paged_addr` and tested (`test_paged_scale_rows_tile_whole_blocks`). |
| W-5 | fixed | 1 | Took the first option: a non-paged block-format operand's base, `s1`, `s2` and `s3` are multiples of `block_bytes` (`ILLEGAL_FIELD`), and `s1` is added to the paged rule. Rejected adding the in-block offset to `o`: a dim-0 run that starts inside a block can also split a conversion unit across granules (W-1), and no layout needs it. `isa.block_addr` is the non-paged reference. `test_block_addr_requires_block_aligned_base` checks slices at elements 16 and 512 against the full-row block index and shows the element-8 slice faulting. memmap.md §6, isa.md §5, `isa.yaml` `DMA_COPY`. |
| W-6 | fixed | 1 | Took both. (a) noc.md §4 rule 2: the I-1 rule is its own bullet. (b) microarch.md §2.5: a started transfer holds its tags until its responses sink and its ring space until its data has been converted. |

**Proposals not taken as written** (the row says why): W-1 (the atomic rule and its reference model, as suggested; the cap
rejected with a reason), W-2 (rule (d) added), W-3 (credits and flags need no credit of their own), W-4 (c) (generalized to
whole 32 B blocks), W-5 (`s1` also on the paged side).

**Regenerated:**
- `make isa`: comments only (`DMA_STAGE`, `STATUS`, `LINK`, TD `page_shift`, `DMA_COPY`, `DMA_SCATTER`); `isa_ref.md` changed,
  the lint of `isa_pkg.sv` is clean.
- `make numerics` not rerun: no D1 input changed. The non-split NVFP4 → BF16 path and the fold rows are as before, and split
  fold tiles are now avoided by layout rather than given a rate.
- `make hbm` and `make links`: text-only changes to hbm.md and d2d_c2c.md, inputs unchanged.

**Frozen numbers:** none changed. arch.md has a change-log row.

**Tests:** `make pytest` 212 passed (197 before). `test_isa.py` 23 → 37: the ring (3 stream sets, random mixes, 6 rejected-rule
cases, sizing), the non-paged block slice, the batched scatter append, the page bound. `test_noc.py` 19 → 20: the scales-first
edge.

## Before Phase 3, after the verification 2 fixes

| Block | Still open |
|---|---|
| `core/dma` | none at the spec level (W-1, W-4 (b, c), W-5, W-6 fixed); Phase 3 tests in PLAN, including the ring against `isa.staging_ring` and its formal property |
| `core/seq` | none. W-3 is a compiler and runtime rule; the semaphore hardware is unchanged |
| `hbm` | none; W-4 (a) corrected H-2's reasoning, not the controller. X-2 (a), (c)-(e) stay on the H-9 checklist |
| `common`, `arith` | none |
| Phase 4 `link`, `noc` | W-2: text fixed, test in Phase 4 |
| compiler / runtime | rules to implement: V-2 and W-3 (credits, call-site semaphore map, final credit, SPM base table, one credit per consumer), V-8 (2 KiB-aligned padded W tiles), W-1 (fold-weight pitch padding), W-4 (a) (one write or an ack between two writes of a block, one owner core) and (b) (scatter index lists) |

**For the next verification pass, check first:**
1. **Re-run the ring deadlock probe against the chosen rule.** Use your own cycle model, not `isa.staging_ring`, with the atomic
   rule, greedy read-ahead, four DQs and the all-split fold. Does any stream stop? Also check that `isa.staging_ring` itself
   matches microarch.md §2.5: entry definition, allocation point, in-order reclaim, scales before data reads.
2. **Entry boundaries.** An entry must hold every split unit it starts. Check a fold tile that crosses a 2 KiB read boundary, and
   an inline-scaled row gather where one row spans two leaves. Is "at most a quarter of the ring" enough for every entry the DMA
   can form at the smallest ring the runtime may set?
3. **W-3 reassignment.** Walk the verifier's S20 scenario and a credit semaphore moved between two collectives in different
   DQs. Is "causally after the owner's last wait" implementable by the compiler from the call-site map alone? Is the SPM base
   table enough for the MoE all-to-all, whose consumer learns its total only after the counts handshake?
4. **W-2 rule (d).** Does freeing nothing on `ERR` (`LINK`) leave any tag that is never freed, for example a request dropped in
   the down bridge's receive buffer? Its timeout should free it. Is 100 µs inside the recovery budget?
5. **W-4 (c) generalization.** Does any surveyed layout have `aux0` that neither divides nor is a multiple of 32?
6. **Fold-weight padding.** Check that padding the row pitch, rather than K, leaves `MXU_MM` and the column-scale layout
   (`aux_reg`) unchanged for a short last tile.


# Verification 3 (2026-11-01)

**Who:** a third verification session (Opus 5.5). It is not the author, the reviewer, any of the fix sessions or the earlier
verifiers. No spec, model or test was changed; this section is the only edit (branch `spec-verification3` from efb2cff).

**Method.** I read the three fix commits and the record commit (b62bbc4, 0e23f87, b8b38b8, efb2cff), the Verification 2 section,
the resolutions above and every spec they cite. For each W-n I asked two questions: does the new text close the verifier's failure
scenario, and does it open a new one? I walked all six check-first items. I then re-checked the earlier verdicts the changes touch
(V-1, V-3, V-4, V-5, X-6 rule 2, D1). I wrote my own cycle model of the staging ring from microarch.md §2.5 alone, before reading
`isa.staging_ring`, and compared the two only afterwards. In my model, entries carry HBM byte ranges, so a granule can belong to
two entries, and the HBM share can be modeled two ways: as an issue budget (paced) or as a FIFO server with a minimum round trip
(greedy issue up to 512 tags). I argued W-3 and W-2 (d) against isa.md §3 and §8, d2d_c2c.md L-4 and L-6, and noc.md §7.
- Executable checks:
  - `make pytest`: 212 passed (rc 0, 20.6 s, 67 MB peak).
  - `make isa`: the generators rewrote identical files, `git status` was clean afterwards, and `verilator --lint-only -Wall` on
    `isa_pkg.sv` passed.
  - `make numerics`, `make hbm` and `make links` were not rerun: the only model file these commits changed is `sim/analytic/noc.py`.
- Probes (scratch directory, not committed):
  - **Staging ring, rates** (elements/cycle between 25% and 75% of the stream; HBM share 87 B/cycle, converter 256/cycle):

    | stream (16 KiB ring unless noted) | own model, paced | own model, greedy, FIFO share | `isa.staging_ring` | microarch.md §2.5 |
    |---|---|---|---|---|
    | NVFP4 → BF16 weights, L = 500 / 845 / 1,000 | 154.7 / 153.3 / 128.7 | 129.6 / 110.3 / 103.4 | 154.7 / 152.9 / 127.4 | 154.7 / 152.9 / 127 |
    | same, 8 KiB ring, L = 845 | 75.9 | 67.9 | 76.3 | 76 |
    | same, 32 KiB ring, L = 500 / 845 / 1,000 | | 154.7 / 154.7 / 154.7 | | |
    | paged NVFP4 KV gather (32 B scale read + 256 B row), L = 500 / 845 | 154.7 / 154.7 | 134.9 / 110.6 (32 KiB: same) | (no addresses) | |
    | MXFP4 fold, every tile split, atomic rule, L = 500 | 29.9 | 29.3 | 27.6 | about 28 |
    | same, old rule (raw slot at data read) | deadlock at 684 | deadlock at 688 | deadlock at 687 | 687 |
    | Engram rows (inline, 264 B), rows/cycle | 0.123 | | 0.123 | 0.12 |
    | 4 DQs, all-split fold, 4 / 16 KiB, L = 100 / 500 / 1,000 | all complete | all complete | | |
    | 4 DQs mixed (NVFP4, all-split fold, both alternating, Engram) | complete | complete | complete | |

    The model asserts that a non-split response never arrives before its entry's scales. That held in every run (0 violations).
  - **Staging ring, entry boundaries** (atomic rule, greedy, paced). "Cut": a data read never serves two entries. "Shared": a
    granule that holds the end of one entry and the start of the next is one read, issued once the scales of both entries are in
    the ring, which "the scales of all its granules" allows.

    | stream | reads | 1 DQ (4 / 16 KiB) | 2 DQs | 4 DQs |
    |---|---|---|---|---|
    | contiguous MXFP4 fold tiles from byte 64 (half of them split) | cut | ok / ok | ok / ok | ok / ok |
    | same | shared | ok / ok | ok / ok | **deadlock at 1,051 / 1,298** |
    | DeepSeek index-key rows (64 B, non-split), starting at row 2 of a page | cut | ok / ok | ok / ok | ok / ok |
    | same | shared | ok / ok | ok / ok | **deadlock at 1,715 / 5,976** |

    At the 16 KiB index-key deadlock the ring is full (head = tail). Its oldest entry is DQ 1's only entry, with all its reads
    issued and 2 rows unconverted. Those 2 rows share a granule with DQ 1's next entry, which cannot be allocated. The seven newer
    entries behind it are converted or waiting, and in-order reclaim cannot pass the oldest one.
  - **Fold tile across a 2 KiB read boundary:** in the all-split stream every eighth tile spans two reads in two 2 KiB blocks.
    Both reads are in the tile's entry, and every run completes. **Inline row across two leaves:** a 264 B row that crosses a
    2 KiB block (one leaf each, memmap.md §3) is two reads. The atomic rule holds if both reads are in the row's entry, but
    the spec does not define an inline entry (Z-3). **Smallest ring:** `DMA_STAGE` accepts any nonzero multiple of 64 B (Z-3).
  - **`noc.build_cdg`:** baseline 1,872 channels with dependencies, 6,992 edges (4,336 with `scales_first=False`); 4 packages
    3,624 / 13,560 (8,352). All four graphs are acyclic, and the counts equal noc.md §4. The baseline has no RSP → REQ edge, so the
    added REQ → RSP edges cannot close a cycle by construction. The check guards against a future RSP → REQ edge (such as
    `dma_forwards`) rather than testing today's graph.
  - **Addresses by hand** against `isa.paged_addr`, `isa.block_addr` and `isa.write_packets`, all equal:
    - DeepSeek index key, t = 6027 (q = 907, frame 0x100000): data `0x10000e2c0`, scale block `0x100010e20`, and the scan read of
      i0 = 100 at `0x10000e2f2` with its scale at `0x100010e2f`.
    - MiniMax KV gather, group 3, i0 = 77, t = 300 (q = 44, frame 0x2000): `0x2002ce6`, scale `0x200859c`.
    - Batched scatter, 4 DeepSeek sequences (t = 6027, 300, 1023, 8), 8 indices each: one 32 B packet per sequence at
      `0x100010e20`, `0x1000214a0`, `0x100032fe0`, `0x100043020`. Two adjacent blocks of one page merge into one aligned 64 B
      packet.
    - Page bound: accepted (`aux0`, `page_shift`) = (4, 3), (8, 2), (16, 1), (32, 0), (64, 0); rejected (4, 2), (8, 1), (16, 0),
      (12, 4), (24, 5), (36, 3).
    - `block_addr`: an NVFP4 slice at element 16, at i1 = 2 and i0 = 5, gives `0x2000100a` and scale `0x30000201`; a slice at
      element 8 faults. An MXFP8 `SCALE_INLINE` row gather with `s1` = 264 faults `ILLEGAL_FIELD` at row 0 (Z-4).

Verdicts: **resolved** (the scenario is closed and nothing new is open); **resolved, gap** (closed, but a residual or new case is
listed below as Z-n); **not resolved**.

| # | Verdict | Notes |
|---|---|---|
| W-1 | resolved, gap | Check-first items 1 and 2. **The verifier's scenario is closed.** In my model, which shares no code with `isa.staging_ring`, the old rule deadlocks at cycle 688 on the all-split fold, and the atomic rule never stops: 1 or 4 DQs, mixed streams, 4 and 16 KiB, L = 100-1,000. **`isa.staging_ring` against §2.5:** the allocation point (scales and raw slots together at the scale read, at the data turn for an inline entry), in-order reclaim, wrap-skip, 16 B units and "data only after its scales" match the text. Four differences matter: (a) its entries have no addresses, and a contiguous fold entry counts its boundary granule in both entries (`tiles // 2 + 1` reads), so it assumes reads are cut at entry boundaries, which the spec does not require (Z-1); (b) it spends the share as an issue budget, with every response exactly `rt` cycles after issue, so a scale read never queues behind the DMA's own data, and the quoted 154.7 / 152.9 / 127 hold only under that pacing (Z-2); (c) a scale read goes first only within its DQ's round-robin turn, where the text says "before data reads"; (d) a scale read takes one tag whatever its size (up to 2 KiB, which is 8 granules). The "quarter of the ring" bound is not enforced in the model, and the 4 KiB tests already use 2,176 B fold entries (Z-3). `noc.py` matches noc.md. Check-first item 6: padding the pitch leaves n0, the tile count ceil(K / 256) and `cs[t][r]` (numerics.md N-2 already allows a short last tile) unchanged, so `MXU_MM` and `aux_reg` are unchanged; 128 B-aligned 128 B tiles never cross a granule, even with an odd tile count per row. |
| W-2 | resolved, gap | Check-first item 4. Rule (d) leaves no tag that is never freed. A replay-buffer request gets a `LINK` `ERR` that frees nothing, and then its response (if the far side served it) or its timeout. A request discarded in the down bridge's receive buffer has its initiator on the far side, which times out. So do responses the bridge drops. Write bursts and `SIGNAL`s are freed by their ack or the timeout; `MC_WR` holds no burst id. The DMA stops issuing at the fault, so every timeout ends at most 100 µs after it, as (d) says. Gaps: a `LINK` `ERR` still in flight when `STATUS` goes idle can fault the restarted step, and (d) names faulted cores only (Z-7). No recovery budget is stated anywhere, so "100 µs is inside it" cannot be checked. It is small next to a link retrain plus a step restart. |
| W-3 | resolved, gap | Check-first item 3. The S20 scenario is closed: the producer's first hand-off add waits for the consumer's final credit, which follows the consumer's last kernel-k wait. The rule can be implemented from the call-site map: Y's adders and the owner's last wait at X are static program points (the last iteration of X's loop), and the final credit is one `SIGNAL` per adder after that point. With X's credit wait and Y's first write in different DQs, a local semaphore orders them. In-order start of a queue is enough in one DQ, because the wait is a start condition. That local edge makes Y's first put depend on remote credits, so under L-7 rule 1 it is not a local-only put; the compiler must place it accordingly. The SPM base table suffices for the MoE all-to-all: the consumer computes `base + total` from the gathered counts in SPM, waits with that register as its target, and stores the new base. A table indexed by semaphore carries the base across a move. **Gap:** a credit semaphore's last wait at X does not cover the credits of X's last two uses, which arrive after the move and count toward Y (Z-5). The "flags need no credit" refinement holds for credits and single-adder flags, not for a reused multi-adder flag (Z-6). |
| W-4 | resolved | (a) H-2, memmap.md §6 and §8, and the PLAN Phase 7 check now give the same reason: arrival order at the controller, an ack between two writes of a block, one owner core. (b) The batched scatter matches by hand (four aligned 32 B packets at the computed addresses), and one index per sequence gives 4 B packets (test). (c) The bound and the divisibility rule have the effect stated: a power-of-two `aux0` below 32 with at least 32 / `aux0` rows per page tiles whole 32 B blocks from a 256 B-aligned `sc_off`. Check-first item 5: every surveyed paged block-format layout has `aux0` of 4, 8 or 32 (DeepSeek NVFP4 KV, 512 wide: 32; DeepSeek MXFP4 index key: 4; MiniMax NVFP4 KV, 4 x 128: 32, or 8 per group; MiniMax NVFP4 index key: 8), so none fails. |
| W-5 | resolved, gap | The element-16 slice gets block 1's scales and the element-8 slice faults, by hand and in the test. But the rule also covers `s1` of `SCALE_INLINE` rows, which makes the Engram gather illegal (Z-4). |
| W-6 | resolved | (a) The I-1 rule is its own bullet in noc.md §4 rule 2. (b) microarch.md §2.5 frees tags when responses sink and ring space after conversion. |

**Earlier verdicts, re-checked after these changes:**
- V-1 (sink): holds. No run of my model, shared reads included, had a non-split response arrive before its scales. Ring liveness is
  W-1 above.
- V-3 and X-6 rule 2: hold. A ring entry stuck as in Z-1 holds ring space but no tags (its reads were not issued), and `DMA_LIST`
  forwards use no ring, so the other DQs keep running.
- V-4: the paged formula is unchanged, and the hand values match. W-5's `s1` addition is Z-4.
- V-5: merging, the worked example and the batched form all match by hand.
- D1: not reopened. The 1.60x is converter and port capacity against the need, and no D1 input changed. Z-2 asks whether the
  delivered rate reaches the share that D1 assumes, which is a separate question.

## Verification 3 findings

| # | Sev | From | Finding | Failure scenario | Suggested resolution |
|---|---|---|---|---|---|
| Z-1 | M | W-1 | **A data read can need the scales of two entries.** "An entry is one scale read and the data reads its scales cover", and "the DMA sizes a scale read on unit boundaries". But nothing says a data read stays inside one entry, and "a data read is issued only after the scales of all its granules are in the ring" allows a granule shared by two entries. Then the older entry cannot complete until the younger one is allocated. With several DQs, the oldest entry in the ring can be the newest of its DQ, and in-order reclaim lets nothing past it. A unit whose scales cross a 2 KiB boundary of the scale array has the same dependency. That happens when a fold tile's 8 or 16 B of scales start at an offset that is not a multiple of their size, i.e. published `aux0` = K / block with K not a multiple of 256, which pitch padding does not change. | Four DQs read DeepSeek index-key rows (64 B, 4 per granule) from row 2 of a page, with whole-granule reads: deadlock at cycle 5,976 with 16 KiB (probe; contiguous fold tiles: 1,298). By the same argument (not run), MXFP4 rows of 1,152 B (K = 2,304, a surveyed shape) with one entry per row are exposed, because odd rows start mid-granule. `isa.staging_ring` cannot show it: its entries have no addresses. | State that **every data read and every conversion unit belongs to exactly one entry**. The DMA cuts a read at an entry boundary (two partial reads of the shared granule) or ends entries on granule boundaries. An entry may hold more than one scale read when a unit's scales cross a 2 KiB boundary, all allocated together. Give `isa.staging_ring` byte ranges, or a test with shared granules (shared must deadlock, cut must not), and extend the Phase 3 formal property: no data read waits for the scales of an entry other than its own. |
| Z-2 | M | W-1 | **The quoted ring rates assume the DMA paces its reads at the share.** `isa.staging_ring` spends 87 B/cycle as an issue budget, and every response arrives exactly `rt` cycles after issue. The DMA in §2.5 issues greedily, up to 512 tags (2 x share x 500 cycles). When the share is a server, requests issued beyond it queue, so a scale read waits behind the core's own data, and both round trips of a ring entry lengthen. | NVFP4 → BF16 weights with 512 greedy tags, 16 KiB: 129.6 / 110.3 / 103.4 elements/cycle at L = 500 / 845 / 1,000, against the 155 need (paced: 154.7 / 153.3 / 128.7). The paged NVFP4 KV gather: 134.9 / 110.6, and a 32 KiB ring does not help it (the share idles 14% of the time at L = 500). A converting decode stream (NVFP4 experts, NVFP4 KV) would run 13-16% below its HBM share at L = 500 and about 29% below at 845, if real queues behave like this FIFO. A tag cap is no fix: it helps the weight stream (best 154.7 / 150.6 / 128.1 at 384 / 288 / 320 tags) but hurts the KV gather (103 at 256), and capping the tags held by scale reads made it worse. | Either specify pacing (an in-flight byte limit for converting reads, as a CSR with a default near share x round trip; the rule `isa.staging_ring` assumes), or state the 154.7 / 152.9 / 127 as paced upper bounds and size the ring for the queueing (32 KiB recovers the weight stream at all three L). Add a delivery-limited mode to `isa.staging_ring` now. The Phase 7 performance simulator, with real PC queues, is the executable check. |
| Z-3 | L | W-1 | **Smallest ring, the quarter bound and inline entries are undefined.** `DMA_STAGE` takes any nonzero multiple of 64 B. A split unit cannot be shortened: an Engram row is 272 B in 16 B units, a split MXFP8 fold tile 256 + 8 B, a split NVFP4 tile 128 + 16 B. So under "at most a quarter" an Engram gather needs a ring of at least 1,088 B, and an NVFP4 granule's 32 B of scales needs 128 B (or a sub-granule data read). Liveness needs only entry ≤ ring. The reference raises only above the full ring, and the PLAN-listed 4 KiB tests use 2,176 B fold entries, over the quarter. Nor is an inline entry defined ("one scale read and the data reads its scales cover" has no scale read). | The runtime sets a 512 B ring. An Engram gather either hangs or forms an entry over the bound, depending on the RTL. Or the RTL makes each data read its own inline entry, so a 264 B row across a 2 KiB leaf boundary spans two entries. | State a minimum ring (for example 2 KiB, which covers 4 x 272 B) or `ILLEGAL_FIELD` when a unit's entry exceeds a quarter of the ring. Say that the quarter is a rate target. Define an inline entry as whole rows, with all reads of a row in it. Make the tests use entries within the stated bound. |
| Z-4 | M | W-5 | **W-5's `s1` rule makes the Engram gather illegal.** memmap.md §6, isa.md §5 and the `DMA_COPY` description require the base and `s1` of every non-paged block-format operand to be multiples of the block's bytes. Engram rows are MXFP8 with `SCALE_INLINE`, 264 B each (block 32 B), and the gather's row pitch is the source TD `s1`. `DMA_GATHER` says row size is "any size, no alignment needed". Where an inline row keeps its scales is still undefined (Verification 2, Not checked). | `DMA_GATHER` of DeepSeek Engram rows (a `required` feature, `engram_tables`): `isa.block_addr` raises `ILLEGAL_FIELD` at row 0 (probe), and RTL built to the PLAN `ILLEGAL_FIELD` tests faults every n-gram gather. | Exempt `SCALE_INLINE` from the base and `s1` rule, and apply the block rule to the in-row offset instead. Define the inline row: `row_bytes` = n0 elements and then n0 / block scales, the scale of element i at the row start + n0 x esize + floor(i / block). Add an inline reference next to `isa.block_addr` and an Engram gather test. |
| Z-5 | M | W-3 | **A credit semaphore moved to another call site still receives the credits of X's last uses.** A producer waits for the credits of use u - 2 before it writes use u. So its last credit wait at X covers credits up to use N - 2, and the credits for uses N - 1 and N (n each for a multicast) arrive whenever those buffers are consumed. "Y's base is X's final target" leaves them out. "Every add of Y causally after that wait" does not stop late adds of X. | Producer P uses S30 as the credit semaphore of a collective X with 8 consumers. After X's last use, S30 moves to Y with base = the target of P's last X wait. The 16 late credits arrive after Y's first write. Y's first credit wait passes 16 credits early, and P overwrites a buffer its consumers are still reading. Nothing faults. | Before a move, the owner waits for every add that X will ever receive (a drain wait to X's full count, all uses), and Y's base is that count. Add "no add of X arrives after the move" to the golden-model check (Phase 3 golden model, Phase 7 detector). |
| Z-6 | L | W-3 | **"A credit or flag needs no credit of its own" holds only for credits and single-adder flags.** A credit is causally safe: a consumer's credit for use u + 2 needs the producer's write of u + 2, which waited for every credit of use u. But a flag with several adders, reused every step and guarding no buffer, can pass early: a fast adder's add for use u + 1 can land before a slow adder's add for use u. Also, the golden-model check ("every add causally after its owner's wait for that semaphore's previous use") flags legal L-6 programs if one credit semaphore counts both parities. A consumer's credit for use u needs only the write of u, which waited for the credits of u - 2, not u - 1. | An 8-core ready flag reused each step: the owner's step-s wait completes with 7 step-s adds and 1 step-(s + 1) add. Whether that is harmful depends on what the slow adder released before its add, which the text does not discuss. A golden-model run reports a violation on a correct shared-credit collective. | Require one credit semaphore per buffer parity (d2d_c2c.md L-6), and say that a multi-adder flag follows the parity-and-credit rule like a buffer semaphore. Keep the golden-model check as it is, which is then exact. |
| Z-7 | L | W-2 | **A `LINK` `ERR` in flight across the restart, and stopped cores that did not fault.** A `LINK` `ERR` frees nothing, so `STATUS` going idle does not show that every `ERR` has arrived. (d) also speaks of faulted cores only, while the CP restarts every core of the step. | The bridge's `ERR`s for its replay buffer are still on the mesh when the last tag of a core is freed. The CP restarts the step, and a stale `ERR` (`LINK`) faults the new step: another recovery round. Or the CP restarts a core it stopped but which had not faulted, while one of that core's responses is still in flight. | Restart after `STATUS` idle on every core of the step (faulted or stopped by the CP), plus a bridge status bit "link-down `ERR`s sent" and a mesh drain time. Add a Phase 4 case with a delayed `LINK` `ERR` at the restart. State a recovery time budget (Phase 4 / runtime). |
| Z-8 | L | W-1 | **`isa.staging_ring` and §2.5 differ in two small details,** besides Z-1 and Z-2: a scale read goes first only within its DQ's round-robin turn (the text says before data reads), and a scale read takes one tag whatever its size. | An RTL that gives scale reads global priority, as the text says, is compared in Phase 3 against a reference that does not. | Align the text or the model, and count `ceil(scale bytes / 256)` tags per scale read. |

## Before Phase 3, after verification 3

| Block | Still open |
|---|---|
| `core/dma` | Z-1 (one rule: no read or unit in two entries; a reference with byte ranges), Z-2 (a pacing rule or a larger ring; a delivery-limited reference mode), Z-4 (the `SCALE_INLINE` exemption and inline scale location); text: Z-3, Z-8. The Phase 3 ring tests and the formal property should follow Z-1 |
| `core/seq` | none. Z-5 and Z-6 are compiler and runtime rules; the semaphore hardware is unchanged |
| `hbm` | none; X-2 (a), (c)-(e) stay on the H-9 checklist |
| `common`, `arith` | none |
| Phase 4 `link`, `noc` | Z-7 (restart condition and test); W-2 tests as listed in PLAN |
| compiler / runtime | Z-5 (drain wait before a move), Z-6 (per-parity credits, multi-adder flags); V-2 / W-3 credits and map, V-8, W-1 padding, W-4 (a), (b) as before; the minimum `DMA_STAGE` size (Z-3) |
| Phase 7 | Z-2: the converting rate with real HBM queues |

Where an executable check is needed beyond these probes:
- W-3, Z-5 and Z-6: the golden-model semaphore check (Phase 3, `sw/compiler`), with a reassignment of a credit semaphore and a
  reused multi-adder flag, and the Phase 7 detector.
- W-2 (d) and Z-7: the `PKG_TINY` link-down tests (Phase 4), including a late `LINK` `ERR` at the restart, and a runtime
  recovery-time measurement.
- Z-1 and Z-2: `isa.staging_ring` with byte ranges and a delivery-limited share (now, before `core/dma` RTL), then the
  `core/dma` formal property (Phase 3) and the performance simulator (Phase 7).

**Not checked:**
- the JESD238 items (still unavailable);
- the generated SV and C++ beyond the lint in `make isa`;
- X-4 against an executable model (Phase 4);
- whether my FIFO model of the share is realistic: it serves the core's requests in issue order, while real HBM has per-PC
  queues and other cores' traffic (Phase 7);
- the SPM port load of staged scales and raw split units (neither ring model includes the ports);
- the recovery time budget and link retrain times, which no spec states;
- the golden-model checks themselves, which do not exist yet (W-3 was argued, not run);
- `make numerics`, `make hbm`, `make links`: not rerun, since their inputs did not change.


# Resolutions (verification 3 fixes, 2026-11-01)

**Who:** a fourth fix session (Opus 5.5). It is not the author, the reviewer, the earlier fix sessions or any verifier. Each
suggested resolution was checked against the specs and then taken, changed or rejected; each row says which and why. I
re-derived every failure scenario before fixing it: Z-1 and Z-2 in `isa.staging_ring` (extended for the purpose) and a
separate minimal FIFO model, the others by argument. The tables above are unchanged. Branch `spec-verification3-fixes` from
d015351, one commit per group, `make pytest` green before each.

| Group | Commit | Findings |
|---|---|---|
| 1 | 8f6cf62 | Z-1, Z-3, Z-4, Z-8 (`core/dma`) |
| 2 | 4c0cb8b | Z-2 (`core/dma` rate; option chosen by the user) |
| 3 | 27184c4 | Z-5, Z-6 (compiler and runtime rules), Z-7 (Phase 4) |

| # | Status | Group | What was done |
|---|---|---|---|
| Z-1 | fixed | 1 | **Re-derived:** `isa.staging_ring` entries now carry HBM byte ranges, and their reads are the granule pieces of the range, grouped into requests inside one 2 KiB block. Under the rejected reading (`boundary="shared"`: one read of a boundary granule, issued once both entries' scales are in the ring), four DQs deadlock. Index-key rows from row 2 of a page, with 1-2 KiB scale reads, stop at cycle 1,716 / 5,977 at 4 / 16 KiB (the verifier: 1,715 / 5,976); fold tiles from byte 64 stop at 1,037 / 1,292 (1,051 / 1,298). One or two DQs complete. Entries of only 32 rows did not deadlock at all, and neither did 1,152 B MXFP4 rows with one row per entry (the verifier's "not run" case). The dependency needs large entries, so a DQ can be left with a single entry in the ring; the rule must hold for any sizing anyway. **Took it:** every data read and every conversion unit belongs to exactly one entry. An entry boundary is a unit boundary, and inside a granule the DMA cuts the read (two partial reads; a request's `len` is in bytes). Of the two options in the suggestion, "entries end on granule boundaries" alone is impossible for contiguous fold tiles from byte 64, whose unit boundaries never meet a granule boundary. So cutting is the rule, and the DMA ends an entry on a granule boundary when a unit boundary falls on one. An entry holds two scale reads, allocated together, when its scales cross a 2 KiB block. Cost: one request and at most one 32 B burst per cut. Test `test_staging_ring_entry_boundaries`: shared deadlocks with four DQs (and lands within 5 cycles of the verifier's 1,715), cut completes, one DQ completes. The Phase 3 formal property in PLAN now also says that no data read waits for the scales of an entry other than its own. microarch.md §2.5, isa.md §5, noc.md §4, `isa.yaml` `DMA_STAGE`, `DMA_COPY`. |
| Z-2 | fixed (user's choice) | 2 | **Re-derived:** `isa.staging_ring` has a delivery-limited mode, `hbm="server"`. Requests issue greedily up to 512 tags and reach a FIFO served at 87 B/cycle half a round trip out, so the core's own backlog adds to the round trip. First I modelled the queue as overlapping the round trip (response at max(issue + L, served)): 151 / 150 / 126. That does not reproduce the verifier, and it is not physical, because a request cannot be served before it reaches the controller. With the server in the path: weights 142 / 119 / 111 at L = 500 / 845 / 1,000 (138 / 116 / 108 with the 1,200-entry probe stream), and the KV gather 136 / 111 / 104 (the verifier: 134.9 / 110.6). The verifier's weight numbers (129.6 / 110.3 / 103.4) reproduce within 1.5% with per-granule data requests (`max_req=1`: 128.2 / 108.7 / 101.7); the spec's 2 KiB requests do somewhat better. The tag-cap optimum also matches (150.3 at 288 tags and L = 845, theirs 150.6). A separate minimal FIFO model (scratch) gives the same. **Cause:** the weight stream becomes ring-bound (both round trips of every entry lengthen). The KV gather becomes tag-bound (one tag per 32 B scale read), so a larger ring does not help it. **Options costed** (microarch.md §2.5 table): (a) an in-flight limit on converting reads, (b) a 24 or 32 KiB ring, (c) both; I asked the user, who chose **(a)**. **New CSR `DMA_PACE`** (0x022): at most 72 KiB of converting reads (their scale and data reads) in flight, reset value 72 KiB, 0 = off; plain reads use tags only. 72 KiB is about the share times the 845 cycles the 16 KiB ring covers. Rates with it: weights 155 / 155 / 150 / 127 and KV 155 / 155 / 154 / 130 at L = 500 / 700 / 845 / 1,000, which are the paced bounds (155 / 155 / 153 / 129 and 155 / 155 / 155 / 130). The quoted 154.7 / 152.9 / 127 therefore stand, now "with the default `DMA_PACE`". Rejected: limiting all reads (a plain stream would drop to 72 B/cycle at L = 1,000) and a tag cap (verifier). The ring stays 16 KiB, so the SPM reservation and the SRAM row are unchanged. `test_staging_ring_delivery_limited`. The Phase 7 check with real PC queues is in PLAN. microarch.md §2.5, isa.md §5, `isa.yaml` `DMA_PACE`. |
| Z-3 | fixed | 1 | Took it. **Minimum ring:** `DMA_STAGE` below 2 KiB (0 included) makes a block-format conversion fault `ILLEGAL_FIELD` (`isa.RING_MIN`). That is 4 x 272 B with margin, so the largest single unit's entry (an Engram row, 272 B in 16 B units; a split MXFP8 fold tile is the same) fits a quarter. I chose the minimum over an `ILLEGAL_FIELD` per entry above a quarter, which would fault on a rate target. **The quarter is a rate target:** the DMA shortens runs to a quarter; liveness needs only entry ≤ ring. The model enforces the quarter by default and checks liveness without it (`quarter=False`: 2,176 B entries in a 4 KiB ring complete). **Inline entry:** whole `SCALE_INLINE` rows, each with all its reads (a row across a 2 KiB block is two reads, both in the row's entry), no scale read, 272 B of raw slot per row. **Tests** keep every entry within a quarter (fold entries of 7 tiles at 4 KiB); `test_staging_ring_size_and_entry_bounds`. At the 2 KiB minimum the Engram gather completes at about 0.014 rows/cycle (16 KiB: 0.12). The minimum gives liveness, not rate. |
| Z-4 | fixed | 1 | Took it. `SCALE_INLINE` is exempt from the block-aligned base / `s1` rule. **Inline row:** n0 elements, then their n0 / block one-byte scales (Engram MXFP8: 256 + 8 = 264 B); the scale of element i0 is at the row start + n0 x esize + floor(i0 / block), the row at base + i1 (or index[i]) x `s1`. Instead of "the block rule on the in-row offset", which is vacuous here, I state the conditions it stands for: a `SCALE_INLINE` operand is non-paged and in a block format, a row is one dim-0 run of whole blocks (n2 = n3 = 1), `s1` is at least the row, and a gather's `row_bytes` equals it (`ILLEGAL_FIELD` otherwise). `isa.inline_addr` sits next to `isa.block_addr`, which uses it for an inline TD; `paged_addr` rejects inline. `test_engram_inline_gather`: 264 B rows from an unaligned table base, five indices, the five `ILLEGAL_FIELD` cases, the separate-array W-5 fault kept, and a row across a 2 KiB block as two reads of one inline ring entry. memmap.md §6, isa.md §5, `isa.yaml` (`SCALE_INLINE`, `MXFP8`, `DMA_COPY`, `DMA_GATHER`), PLAN Phase 3. |
| Z-5 | fixed | 3 | **Re-derived** by argument. Adjusted to Z-6: with one credit semaphore per parity, the late credits are those of the parity's last use (n), not the last two uses (2n, the verifier's shared-semaphore case). Took it: a semaphore moves only after its owner's **drain wait** for X's full count over all uses; Y's base is that count. For a buffer semaphore that is the last use's wait; a credit semaphore needs an extra wait. The cost is that wait, normally satisfied long before Y's first use (the compiler picks another index when Y follows X at once). Golden-model check: "no add of X arrives after the move" (Phase 3 golden model and Phase 7 detector, PLAN). Not run: the golden model does not exist yet. isa.md §3 (with the S30 example), d2d_c2c.md L-6. |
| Z-6 | fixed, refined | 3 | **Re-derived:** the multi-adder flag passes early as described. A shared-parity credit semaphore is worse than "flagged by the check": with several consumers, early credits of use u - 1 can complete the wait meant for u - 2, so it is unsafe, not merely reported. Took it: **one credit semaphore per buffer parity**, and a multi-adder flag follows the parity-and-credit rule, unless its add is already causally after the owner's wait. Refinement: the unchanged check would flag a legal single-adder flag that runs ahead (its adds come in order because each `SIGNAL` waits for the earlier ones' acks). So the check names that one exception: a single-adder flag's adds must come from one DQ. With it the check is exact. PLAN lists the test programs (an 8-consumer credit move, an 8-adder flag with and without parities, a shared-parity credit semaphore that must be flagged). isa.md §3, d2d_c2c.md L-6, PLAN Phase 7. |
| Z-7 | fixed | 3 | Took it. Rule (d) now covers cores the CP stopped without a fault. **(e) Restart condition:** `STATUS` idle on every core of the step (faulted or stopped), `err_done` from every down bridge (a new bit in the link-state CSR: all replay-buffer `ERR`s injected and the buffer discarded), then `T_DRAIN` (runtime parameter, default 10 µs = 15,000 cycles, far above an intra-die RSP transit; Phase 4 measures the worst case). Requests that reach the down bridge after condition 1 cannot exist, because no core of the step sends any. **Phase 4 test:** the replay-buffer `ERR`s are held on the mesh past the last tag's release; no restarted core faults; with `T_DRAIN` too short the restarted core faults `LINK`, which shows the wait is needed; a CP-stopped core with a response in flight is not restarted early. **Recovery time budget: not a spec number.** A link-down is a failure, not normal operation, so its recovery sets availability, not throughput, and no hardware size or default depends on it. The hardware parts are bounded in the spec (timeout 100 µs, `T_DRAIN` 10 µs; retrain from vendor data). The runtime states the budget with its availability target, and Phase 4 measures it. d2d_c2c.md L-4, L-9, L-11, isa.md §8, noc.md §7, `isa.yaml` `STATUS`, PLAN Phase 4. |
| Z-8 | fixed | 1 | Took both parts. **Text aligned to the model:** a DQ's scale read goes before its own data reads; across DQs, scale and data reads are arbitrated per packet, round-robin, like every shared resource. Global priority needs a second arbitration level, and the read-ahead needs only per-DQ order. **Tags:** a scale read takes ceil(scale bytes / 256), in the model and the text (`test_staging_ring_scale_read_tags`: a 2 KiB scale read needs 8). The paced rates are unchanged, because every scale read in them is at most 256 B. microarch.md §2.5. |

**Proposals not taken as written** (the row says why):
- Z-1: cutting the read is the rule, and ending entries on granule boundaries is only the DMA's preference, since it cannot work alone.
- Z-2: the queue sits in the path, not overlapped with the round trip; the user chose option (a).
- Z-3: a minimum ring rather than an `ILLEGAL_FIELD` per entry.
- Z-4: explicit inline conditions instead of "the block rule on the in-row offset".
- Z-5: n late credits per parity, not 2n.
- Z-6: shared-parity credits are unsafe, and the check has a single-adder exception.
- Z-7: no recovery budget in the spec.

**Regenerated:**
- `make isa`, groups 1 and 2: `isa_ref.md` (comments), and for `DMA_PACE` also `isa_pkg.sv`, `isa.h` and `isa_defs.py`. The lint of `isa_pkg.sv` is clean, and `git status` was clean after a rerun.
- `make numerics`, `make hbm`, `make links`, `make sweep`, `make models`: not rerun. No input changed: `numerics.py`, `hbm.py`, `links.py`, `noc.py`, `opgraph.py` and `ingest.py` are untouched. The D1 margin (1.60x) is converter and port capacity, which `DMA_PACE` does not change.

**Frozen numbers:** none changed. The ring stays 16 KiB of SPM, and `DMA_PACE` is a counter, so the SRAM row is unchanged. arch.md has a change-log row.

**Tests:** `make pytest` 218 passed (212 before). `test_isa.py` 37 → 43: entry boundaries (2 cases), ring and entry bounds, scale-read tags, the Engram inline gather, the delivery-limited rates. The existing ring tests now keep their entries within a quarter of the ring.

## Before Phase 3, after the verification 3 fixes

| Block | Still open |
|---|---|
| `core/dma` | none at the spec level (Z-1 to Z-4, Z-8 fixed); Phase 3 tests in PLAN: the ring against `isa.staging_ring` with byte ranges, its two formal properties, `DMA_PACE`, the Engram inline gather |
| `core/seq` | none. Z-5 and Z-6 are compiler and runtime rules; the semaphore hardware is unchanged |
| `hbm` | none; X-2 (a), (c)-(e) stay on the H-9 checklist |
| `common`, `arith` | none |
| Phase 4 `link`, `noc` | Z-7: the bridge's `err_done` bit and the restart test; W-2 tests as listed in PLAN |
| compiler / runtime | rules to implement: V-2, W-3, Z-5, Z-6 (credits per parity, call-site map, drain wait and final credit before a move, multi-adder flags, SPM base table, one credit per consumer), V-8, W-1 padding, W-4 (a), (b), `DMA_STAGE` ≥ 2 KiB, `DMA_PACE` with the ring, `T_DRAIN` and the restart condition (Z-7) |
| Phase 7 | Z-2: the converting rates with real PC queues and other cores' traffic, at the default `DMA_PACE` and with it off |

**For the next verification pass, check first:**
1. **The server mode of `isa.staging_ring`.** Write your own queue model and check the table in microarch.md §2.5, mainly 72 KiB at L = 845 (weights 150, KV 154). Is "server half a round trip out" the right abstraction, or does HBM's per-PC parallelism change it? Does a plain stream in another DQ (not paced) push a paced converting stream back into the queueing regime?
2. **`DMA_PACE` default.** It is tuned to the ring's 845-cycle coverage and is fairly sharp (64 KiB: 138 at 845). Is 72 KiB right with other cores' traffic, and does it need to scale with `DMA_STAGE` in hardware rather than by the runtime?
3. **Cut reads.** Check that two partial reads of one granule are legal end to end: tag-table granule index, response `len`, the HBM burst read twice, and the SPM destination of each half.
4. **Inline rows.** Check the `SCALE_INLINE` conditions against the Engram table layout as published (hyper_connections.md §4 still mentions "the separate scale array" in its bytes-per-token note), and Qwen's BF16 n-gram rows (no inline scales).
5. **Drain wait (Z-5).** Walk an index moved from a credit semaphore to a buffer semaphore and back. Is the drain target always computable from the call-site map and the base table?
6. **Restart (Z-7).** Is 10 µs above the worst RSP transit from a bridge to a core while faulted cores are still sinking converter-paced responses? Should `T_DRAIN` be a hardware bound instead (an `ERR` age counter at the bridge)?
