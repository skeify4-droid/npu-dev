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


# Verification 4 (2026-11-01)

**Who:** a fourth verification session (Opus 5.5). It is not the author, the reviewer, any of the fix sessions or the earlier
verifiers. No spec, model or test was changed; this section is the only edit (branch `spec-verification4` from c0b635e).

**Method.** I read CLAUDE.md, Verification 3, the resolutions of the verification 3 fixes and the four commits (8f6cf62, 4c0cb8b,
27184c4, c0b635e), and every spec the rows cite: microarch.md §2.5, isa.md §3, §5 and §8, memmap.md §3 and §6, noc.md §2-4 and §7,
d2d_c2c.md L-4, L-6, L-9 and L-11, `isa.yaml` (`DMA_STAGE`, `DMA_PACE`, `SCALE_INLINE`, `STATUS`, `noc_header`), hyper_connections.md
§4 and PLAN Phases 3, 4 and 7. For each Z-n I asked two questions: does the new text close the verifier's failure scenario, and does
it open a new one? I walked the six check-first items, then re-checked W-1, W-3, W-5, V-1, V-3, X-6 rule 2 and D1.

Before I read `isa.staging_ring` I wrote my own cycle model from microarch.md §2.5 alone (scratch, not committed; no code in common).
- Entries carry HBM byte ranges. Data reads are the granule pieces of the range, grouped into requests of up to 2 KiB inside one
  2 KiB block, and cut at entry boundaries (`cut`) or shared (`shared`: one read, issued once both entries' scales are in the ring).
- Ring: 16 B units, contiguous with wrap-skip, one allocation per entry at its first scale read (at its first data read if inline),
  and in-order reclaim.
- Issue: greedy read-ahead (a DQ's next scale read before its own data reads when the space can be taken), round-robin over DQs,
  one request per cycle, 512 tags counted per granule piece, and `DMA_PACE` counted at issue and returned per response.
- HBM, three modes:
  - `paced`: an issue budget at the share, every response exactly L after issue;
  - `server`: one FIFO at 87 B/cycle, L/2 out, per piece with 32 B burst rounding;
  - `pc`: 32 per-PC FIFOs at 21.75 B/cycle each (a stack's 0.85 x 820 B/cycle), shared by 8 cores, PC from the memmap.md §3 hash.
- `ni_rsp` is one FIFO per core. A piece takes max(bytes, elements) / 256 cycles, and its tag and pace bytes are freed when it sinks.
- Rates are elements/cycle between 25% and 75% of a stream (or over the window where two streams overlap), at an 87 B/cycle share.
  The need is 154.7 for NVFP4 → BF16 and the KV gather, 163.8 for MXFP4 and 84.4 for MXFP8.

Executable checks:
- `make pytest`: 218 passed (rc 0, 22.0 s, 70 MB peak, `scripts/memrun`).
- `make isa`: the generators rewrote identical files, `git status` was clean afterwards, and `verilator --lint-only -Wall` on
  `isa_pkg.sv` passed.
- `make numerics`, `make hbm` and `make links` were not rerun. The four commits change no model input: `numerics.py`, `hbm.py`,
  `links.py`, `noc.py`, `opgraph.py` and `ingest.py` are untouched (`git diff --stat d015351 c0b635e`).

Probes (scratch, not committed):
- **Rates, own model and `isa.staging_ring` against microarch.md §2.5** (16 KiB ring; weights in entries of 8 granules, i.e.
  256 B of scales and one 2 KiB data read, as in `test_isa.py` `_nvfp4_bf16`; KV gather one entry per row):

  | L = 500 / 700 / 845 / 1,000 | own model | `isa.staging_ring` | §2.5 table |
  |---|---|---|---|
  | weights, paced | 154.7 / 154.7 / 153.8 / 129.5 | 154.7 / 154.7 / 153.3 / 128.6 | 155 / 155 / 153 / 129 |
  | weights, queue, tags only | 130.4 / 118.6 / 111.3 / 104.4 | 141.7 / 127.5 / 118.9 / 110.9 | 142 / 128 / 119 / 111 |
  | weights, queue, `DMA_PACE` 72 KiB | 154.7 / 154.7 / 150.6 / 127.8 | 154.7 / 154.7 / 150.2 / 127.1 | 155 / 155 / 150 / 127 |
  | KV gather, paced | 154.7 / 154.7 / 154.7 / 130.3 | 154.7 / 154.7 / 154.7 / 130.5 | 155 / 155 / 155 / 130 |
  | KV gather, queue, tags only | 139.7 / 117.8 / 110.4 / 103.5 | 136.0 / 117.9 / 110.6 / 103.6 | 136 / 118 / 111 / 104 |
  | KV gather, queue, `DMA_PACE` 72 KiB | 154.7 / 154.7 / 154.1 / 130.5 | 154.7 / 154.7 / 154.2 / 130.0 | 155 / 155 / 154 / 130 |

  The paced and paced-queue rows agree within 1%. My tags-only weights are 8-11 below the reference, which serves a request as one
  unit and frees tags on arrival; with 2 KiB requests mine reproduces the third verifier's 129.6 / 110.3 / 103.4. So the fix
  session's explanation of that gap (per-granule requests) is one way to get it, not the only one. Every model agrees that tags
  alone fall well short.
- **`DMA_PACE` sensitivity**, own model, queue (the reference in brackets):

  | limit | weights, L = 300 / 500 / 700 / 845 / 1,000 | KV gather, same L |
  |---|---|---|
  | 56 KiB | 154.7 / 154.7 / 140.4 / 116.6 / 98.7 | 154.7 / 154.7 / 144.3 / 119.3 / 100.7 |
  | 64 KiB | 154.7 / 154.7 / 154.7 / 133.9 (137.6) / 113.7 | 154.7 / 154.7 / 154.7 / 136.7 (136.8) / 115.8 |
  | 72 KiB | 154.7 / 154.7 / 154.7 / 150.6 (150.2) / 127.8 | 154.7 / 154.7 / 154.7 / 154.1 (154.2) / 130.5 |
  | 80 KiB | 154.7 / 154.7 / 154.7 / 145.6 (144.0) / 128.6 | 154.5 / 154.5 / 154.5 / 146.3 (146.2) / 130.2 |
  | 96 KiB | 154.7 / 154.7 / 142.4 / 132.0 / 122.5 | 154.4 / 154.4 / 142.6 / 132.0 / 122.2 |

  Why it is sharp: with the limit on, the core's queue fills to it at any L below about limit / share. At 72 KiB the mean round
  trip is 830-838 cycles at L = 300-700, not L. The effective round trip is max(L, limit / share). The ring covers NVFP4 up to
  about 845 cycles, so the best limit is share x 845 = 4.5 x `DMA_STAGE`, whatever the share. A smaller limit binds at L = 845
  itself, a larger one pushes the effective round trip past what the ring covers. That answers check-first item 2: the limit
  belongs to the ring size and the format, not to the round trip (Y-2).
- **Entry size** (weights, entries of 8 / 16 / 32 / 64 / 128 granules, i.e. 256 B to 4 KiB of scales; own model):

  | entry | paced, L = 500 / 700 / 845 / 1,000 | queue + 72 KiB, same L |
  |---|---|---|
  | 8 granules (the reference's tests) | 154.7 / 154.7 / 153.8 / 129.5 | 154.7 / 154.7 / 150.6 / 127.8 |
  | 16 | 154.7 / 154.7 / 152.3 / 129.1 | 154.7 / 154.7 / 148.6 / 126.4 |
  | 32 | 154.7 / 154.7 / 149.4 / 126.4 | 154.7 / 154.7 / 144.8 / 123.6 |
  | 64 (2 KiB of scales: one scale read) | 154.7 / 154.7 / 141.5 / 120.9 | 154.7 / 150.9 / 137.6 / 118.3 |
  | 128 (4 KiB: a quarter of the ring) | 154.7 / 142.5 / 123.1 / 107.3 | 151.9 / 136.1 / 123.8 / 108.0 |

  `isa.staging_ring` agrees: with 2 KiB of scales per entry it gives 141.7 at 845 cycles (paced) and 136.2 (72 KiB). With
  quarter-size entries it gives 126.7 (paced) and 108.1 (72 KiB), and 128.1 even at L = 500, because one 36 KiB entry is half of
  the limit. A scale must stay in the ring for two round trips plus the time its entry's data takes to stream. So the ring needs
  scale rate x (2 L + T_entry), and four quarter-size entries do not "let scales run a round trip ahead" at 845 cycles (Y-1).
- **MXFP4 and MXFP8** (non-split, 16 B / 8 B of scales per granule, entries of 8 granules), L = 500 / 700 / 845 / 1,000 / 1,200:
  - tags only: the full rate (163.8 / 84.4) at every L, in both models;
  - with 72 KiB: MXFP4 163.8 / 163.8 / 159.2 / 134.7 / 112.3, and MXFP8 84.4 / 84.4 / 82.0 / 69.0 / 57.3 (own model).
    `isa.staging_ring` gives 163.3 / 138.0 and 84.1 / 70.9 at 845 / 1,000, and 80 KiB gives 153.4 / 78.9 at 1,000;
  - with the count weighted by format (NVFP4 bytes in full, MXFP4 at 1/2, MXFP8 at 1/4, same 72 KiB): NVFP4 unchanged
    (154.7 / 150.6 / 127.8 / 107.0 at 500 / 845 / 1,000 / 1,200), MXFP4 163.8 and MXFP8 84.4 at every L. With NVFP4 and MXFP4 in
    two DQs at L = 1,000: 72.5 → 81.6 and 96.9 → 115.7.
- **A plain stream in another DQ** (own model, queue, one core, 2 KiB plain reads limited by tags only, over the window where
  both run):

  | converting stream | `DMA_PACE` | L = 500: converting / plain, B/cycle | L = 845 |
  |---|---|---|---|
  | NVFP4 weights | off or 72 KiB | 43.3 / 43.7 | 43.3 / 43.8 |
  | MXFP4 weights | off or 72 KiB | 42.2 / 44.9 | 42.2 / 44.9 |
  | NVFP4 KV gather | off | 86.4 / **0.8** | 73.0 / **1.3** (total 74.2) |
  | NVFP4 KV gather | 72 KiB | 83.6 / **3.5** | 83.1 / **3.6** |

  The plain stream does push the paced stream's round trip up (1,446 cycles), but the weight stream does not collapse: the two
  split the share and the total stays at 87 B/cycle. The KV gather instead starves the plain stream. Its requests take one tag,
  and an 8-tag request rarely finds 8 free (Y-3). `isa.staging_ring` cannot show this: it shrinks a data request to the free
  tags (`k < min(max_req, free_tags)`), which the text does not say.
- **Per-PC queues, 8 cores of one stack** (32 PC FIFOs; mean / slowest core; L = 500 / 845):

  | case | 16 KiB, 72 KiB | tags only | FIFO model, 72 KiB |
  |---|---|---|---|
  | 8 cores, weights | 154.7 (154.1) / 146.1 (145.0) | 152.7 / 127.9 | 154.7 / 150.6 |
  | 8 cores, KV gather | **144.3 (142.9) / 132.4 (131.3)** | 126.2 / 107.1 | 154.7 / 154.1 |
  | 8 cores, KV gather, 24 KiB ring | 151.8 / 141.1 | | |
  | 8 cores, KV gather, 32 KiB ring | 152.4 / 141.1 | | |
  | 8 cores, KV gather, out-of-order data issue | 144.3 / 134.5 | | |
  | 1 converting + 7 plain cores, weights | 90.4 / 91.1 (≈ 51 B/cycle; plain cores 92 B/cycle each) | 92.5 / 91.7 | |
  | 4 + 4, weights | 110.0 / 110.0 (≈ 62 B/cycle; plain 112) | 111.6 / 110.9 | |
  | 1 + 7, KV gather | 78.8 / 78.5 (≈ 44 B/cycle; plain 93) | 77.8 / 79.0 | |

  Instrumented at L = 500, the paced KV core's ring holds 16.2 KiB of 16 KiB (about 507 entries) while it uses 365 tags and
  52.7 KB of the limit. Per-PC latency varies (hbm.md H4 has a 133 ns mean and a 367 ns p99 queue wait at 85% load), and
  in-order reclaim turns the tail into ring occupancy. The single FIFO has no variance, so it hides this (Y-4). The FIFO is
  pessimistic for tags-only streams and close for paced weights.
- **Z-1 boundaries**, own model, four DQs of DeepSeek index-key rows (64 B, 4 B of scales) from row 2 of a page, entries of 256 rows
  (4 KiB ring) or 512 rows (16 KiB):
  - cut: always completes (72,779 / 97,433 cycles paced, 76,521 / 97,038 queue + 72 KiB);
  - shared: deadlocks, last progress at about cycle 2,340 / 13,790 paced and 550 at 4 KiB with queue + 72 KiB. At 16 KiB with
    queue + 72 KiB it completed, so the deadlock depends on timing, and the rule (cut) is what removes it;
  - one DQ: completes in every mode.
- **Addresses** against `isa.block_addr` / `isa.inline_addr`:
  - An Engram row from a separate scale array (MXFP8, s1 = 256, `aux0` 8; index 12345, i0 = 33) gives data `0x4000303921` and
    scale `0x50000181c9`, so that layout is legal today.
  - A Qwen BF16 n-gram row (320 B, base + 8) has no block rule, and `SCALE_INLINE` on it faults `ILLEGAL_FIELD` (needs a block
    format).
  - `inline_addr` accepts `row_bytes` = 0, while memmap.md §6 says a gather's `row_bytes` is the row's size (Y-7).

Verdicts: **resolved**; **resolved, gap** (closed, but a residual or new case is listed below as Y-n); **not resolved**.

| # | Verdict | Notes |
|---|---|---|
| Z-1 | resolved, gap | The rule ("every data read and every unit in exactly one entry; cut inside a granule") closes the scenario. In my model the shared reading deadlocks with four DQs and cut never does. **Check-first item 3 (cut reads end to end):** they work, but only by reading between the lines. `RD_REQ` has `len` in bytes, so a request can start and end mid-granule. `RD_RSP` carries `tag` and `gidx` but no `len` (`noc_header`: `len` is not an `RD_RSP` field) and is always 2 flits. The valid bytes of a half must come from the tag table's address and length, the payload must be granule-aligned, and `gidx` must count HBM granules from the request's first one. None of that is written. The HBM burst at the cut is read twice only when the cut falls inside a 32 B burst. Index-key (64 B) and fold (128 B) boundaries never do; a contiguous `SCALE_INLINE` copy (264 B rows) does. Each half has its own request, so its own tag-table entry and SPM destination; the converter needs the half's element offset (byte 128 of an NVFP4 granule is element 256) and its entry's ring pointer from that entry (Y-5). |
| Z-2 | resolved, gap | **Check-first item 1:** the §2.5 table reproduces in my model within 1% (above) for the reference's streams. Four gaps. Y-1: the rates need entries of about 8-16 granules, which §2.5 does not say, and quarter-size entries give 123-127 (paced) and 108-124 (72 KiB) at 845, own model and reference. Y-2: the 72 KiB limit costs MXFP4 and MXFP8 16-18% at L = 1,000, where tags alone keep the full rate. Y-4: with per-PC queues the KV gather gets 132, not 154, at 845, and neighbours' greedy traffic halves a converting core's share whether it is paced or not. Y-6: a limit below one request. **"Does a plain stream push a paced stream back into the queueing regime?"** Its round trip, yes (1,446 cycles). Its rate, no: weights and plain split the share and deliver 87 B/cycle together. The real exposure is tag starvation in the other direction (Y-3). |
| Z-3 | resolved, gap | The 2 KiB minimum, the 272 B largest unit and the inline entry hold. The fault is on the conversion, and the entry bounds are as stated. In the tests every entry is within a quarter. But "the quarter is a rate target: four entries in flight let scales run a round trip ahead" is wrong for scale-only entries (Y-1). |
| Z-4 | resolved, gap | The exemption and the five `ILLEGAL_FIELD` conditions match `isa.inline_addr`, the Engram test, `isa.yaml` and memmap.md §6, and the separate-array W-5 fault is kept. **Check-first item 4:** the Qwen BF16 rows are plain (no block rule, `SCALE_INLINE` faults, not paced, not staged). The Engram table "as published" is FP8 with a scale per 32 (model_features.md row 7, `ingest.py` notes). Nothing in the repo shows the scales inline, hyper_connections.md §4 still speaks of "the separate scale array" and G-3 of an "optional scale-array base", and no spec says who repacks 197 G elements into 264 B rows. A separate-array gather is legal today and non-split (Y-7). The bytes note does not add up (Y-7). |
| Z-5 | resolved, gap | The S30 scenario is closed: the drain wait covers the parity's last n credits, the final credit orders Y's first add, and Y's base is the drain target. **Check-first item 5, walked:** S on P is X's parity-0 credit semaphore (8 consumers, U uses of that parity), then Y's parity-0 buffer semaphore (4 producers, `rsem`), then Z's credit semaphore again. (1) X → Y: P's drain target is base + 8U, 8 more than its last credit wait. After it, P sends one final credit to each of the 4 producers, Y's base is base + 8U, and the producers' first Y write waits for that credit. That credit is one more count on each producer's own credit semaphore for Y's parity 0, which its later credit targets must include. (2) Y → Z: the drain wait is Y's last buffer wait (bytes, from the base table when data-dependent). Z's adds are consumer credits caused by P's own Z writes, issued after the drain in the same DQ or behind a local semaphore. Nothing reaches the old use. **Is the target computable from the map and the base table alone?** For buffer semaphores, yes. For credit semaphores only if n is fixed per call site. With a data-dependent consumer set per use (n_u), the base table as defined ("the previous use's final wait target") does not hold the last use's n, and an idle last use (no later write waits for its credits) invites a compiler to drop that credit, after which the drain wait hangs (Y-8). |
| Z-6 | resolved, gap | One credit semaphore per parity is right: a credit for u needs the write of u, which waited for u - 2. The multi-adder flag rule closes the 8-die ready flag. **Is the single-adder exception safe with more than one DQ?** Yes. The exception requires the adds to come from one DQ, so adds from two DQs are not exempt, and the check reports them (an add for s + 1 from DQ 1 can overtake an add for s from DQ 0, because a `SIGNAL` releases only its own queue). It is conservative: two-DQ adds that are each causally after the owner's wait are legal but flagged. The ordering argument holds for `SIGNAL`s only (a `SEM` packet is acked like a `WR`), not for `rsem` adds, whose transfers overlap in a queue. Flags are `SIGNAL`-only by isa.md §3, so this is wording (Y-9). |
| Z-7 | resolved, gap | Conditions 1-3 close the scenario: after `err_done` no new `ERR` is injected, and none can be created, since no core of the step sends. **Check-first item 6, is 10 µs above the worst transit?** By estimate, yes, about 16x. Faulted and stopped cores drop responses at line rate (isa.md §8), so they are not converter-paced. The slowest live sink is a converting core at about 2 cycles per 256 B. With 6-flit RSP VCs, cut-through, 3 cycles per hop and round-robin over at most 6 ports x 2 RSP VCs, a hop waits at most about 6 x 2 x 3 packets x 2 cycles = 72 cycles, and about 12 hops make about 900 cycles against 15,000. It is a bound only if no sink stalls for long; SPM port arbitration against the DMA is not bounded in the spec, and Phase 5 has "bounded liveness per router". **A hardware bound?** An `ERR` age counter at the bridge sees injection, not delivery, so it adds nothing to the CP's timer. An epoch bit would make the transit irrelevant (Y-10). **"No recovery budget in the spec" is sound:** no hardware size or default depends on it, and L-11 assigns it to the runtime with the availability target. |
| Z-8 | resolved, gap | Per-DQ order, per-packet round-robin across DQs and ceil(bytes / 256) tags per scale read now match text and model. But round-robin over DQs "that have a packet ready" lets single-tag requests starve multi-tag ones in other DQs. The text counts tags per granule while the header has one 9-bit tag per request with a 3-bit `gidx`, so what a tag is remains undefined (Y-3). |

**Earlier verdicts, re-checked after these changes:**
- W-1 (liveness): holds. Pacing adds a wait that responses release unconditionally, so an allocated entry still needs only tags and
  pace. That holds only if a converting read can always issue when none is in flight, which the reference does and the text
  does not say (Y-6).
- W-3: holds; Z-5 completes it, with Y-8 as a clarification.
- W-5: holds. The separate-array rule still faults the element-8 slice and an `s1` that is not whole blocks (`test_isa.py`).
  `SCALE_INLINE` is exempt by an explicit flag, so the exemption cannot widen silently.
- V-1 (sink): holds. In my model no non-split response arrived before its entry's scales, in any mode.
- V-3 and X-6 rule 2: hold for deadlock. Tags and pace are freed by responses, which sink unconditionally, and `DMA_LIST` forwards
  are plain (no pace, no ring). They do not hold for rate: a tag-hungry DQ can starve another DQ's multi-tag reads (Y-3).
- D1: not reopened. The 1.60x margin is converter and SPM port capacity for NVFP4 → BF16, which `DMA_PACE` does not change. Y-2
  is about the delivered rate of MXFP4 / MXFP8 streams, a separate question.

## Verification 4 findings

| # | Sev | From | Finding | Failure scenario | Suggested resolution |
|---|---|---|---|---|---|
| Y-1 | M | Z-2, Z-3 | **The §2.5 rates need small entries, and the spec does not size them.** The text says only that entries are at most a quarter of the ring and "usually" have one scale read, and it calls the quarter a rate target ("four entries in flight let scales run a round trip ahead"). A non-split entry's scales stay in the ring for two round trips plus the time its own data takes to stream, so the ring must hold scale rate x (2 L + T_entry). The reference's tests use 8-granule entries (256 B of scales), which is why they reach the table. | An RTL that forms the largest entries the text allows (4 KiB of scales, 32 KiB of data, the fewest scale requests) runs NVFP4 → BF16 at 123 / 124 elements/cycle at 845 cycles (own model, paced / 72 KiB; `isa.staging_ring` 127 / 108, and 128 with the limit even at L = 500). Entries of one 2 KiB scale read give 141 / 138 (own model). It still passes a Phase 3 comparison against `isa.staging_ring` if the test feeds it the RTL's own entry sizes. | State the entry size for scale-only entries: at most one data request (8 granules, 256 B of NVFP4 scales; 16 granules costs about 2 elements/cycle at 845). Keep the quarter as the bound for entries with raw slots, and replace the "four entries in flight" sentence with the 2 L + T_entry rule. Add a reference test with quarter-size scale-only entries that shows the loss, and a Phase 3 check that the RTL's entry size is the specified one. |
| Y-2 | M | Z-2 | **`DMA_PACE` at 72 KiB throttles MXFP4 and MXFP8 streams that the ring does not limit.** 72 KiB is NVFP4's ring coverage (4.5 x `DMA_STAGE`: 32 B of scales per 256 B granule). MXFP4 carries 16 B per granule and MXFP8 8 B, so the ring covers them far beyond 845 cycles, and the limit becomes the only bound: rate ≤ limit / L. DeepSeek V4.1's experts are FP4 with a scale per 32 (MXFP4), its largest decode weight stream, and remote (D2D / C2C) round trips are longer than local ones. The §2.5 table, the tests and the Phase 7 check cover NVFP4 only. | MXFP4 weights at L = 1,000 / 1,200: 134.7 / 112.3 elements/cycle with the default limit against 163.8 with tags only (`isa.staging_ring`: 138.0 at 1,000); MXFP8 69.0 / 57.3 against 84.4. A runtime that raises the limit for MX streams pushes the next NVFP4 kernel past the ring's coverage (96 KiB: 132 at 845). | Weight the count by format, NVFP4 bytes in full, MXFP4 at 1/2, MXFP8 at 1/4 (a shift). In my model that keeps NVFP4 unchanged and MXFP4 / MXFP8 at the full rate to 1,200 cycles. Better still, derive the limit from `DMA_STAGE` in hardware (4.5 x the ring size, so the runtime cannot detune it), keeping the CSR as an override. Add MXFP4 and MXFP8 rows to the §2.5 table, the delivery-limited test and the Phase 7 check. |
| Y-3 | M | Z-8, V-3 | **Tags are undefined per request or per granule, and multi-tag requests starve.** §2.5 and the reference count 512 tags in granules (a 2 KiB read takes 8). The `noc_header` has one 9-bit tag per request plus a 3-bit `gidx`, so either a request takes 8 consecutive tags (and allocation fragments) or a tag is per request and the 512 granules are a separate credit count. Nothing says which. Arbitration is round-robin "over the DQs that have a packet ready". If ready means its tags are free, a DQ of single-tag requests takes every freed tag. The reference hides this by shrinking a data request to the tags that are free, which the text does not say. | A decode attention step runs a long paged NVFP4 KV gather (32 B scale reads and 256 B rows, one tag each) in DQ1 and fetches the next layer's activations (2 KiB plain reads) in DQ2. DQ2 gets 0.8-3.6 B/cycle for the whole gather (own model, L = 500 / 845, pace off or on). It is not a deadlock, since tags are freed unconditionally, but "four queues served independently" fails as a rate claim, and the stall lands on the critical path. | Define the resource: one tag per request (a table of request entries) plus a count of 512 granules in flight, or consecutive granule tags with the allocation rule. Choose the round-robin winner among DQs with a packet ready regardless of tags, and let it hold its turn until its granules are free (no younger request overtakes it). Never hold the turn for ring space, which is freed by the data reads it would block. Or state the reference's shrinking. Add a Phase 3 test: a KV gather plus a plain stream in two DQs, with the plain stream getting a fair part of the share. |
| Y-4 | M | Z-2 | **The FIFO share overstates the KV gather, and other cores' traffic is outside the rule.** With 32 per-PC queues shared by 8 cores, the paced KV gather is ring-bound, not tag-bound. Per-PC latency variance, through in-order reclaim, fills the 16 KiB ring with 32 B entries while tags and pace have room. Separately, a per-PC FIFO shares bandwidth in proportion to bytes queued. A converting core's queued bytes are capped by its ring (or pace), and a plain core's only by its 128 KiB of tags. hbm.md's scheduler is FR-FCFS and oldest-first, with no fairness per initiator, and noc.md §7 has no QoS in v1. | 8 cores of one stack on the paged KV gather, 72 KiB: 144 / 132 elements/cycle at L = 500 / 845 (own per-PC model) against the table's 155 / 154. A 24 KiB ring gives 152 / 141, and out-of-order data issue does not help. One converting core among 7 cores streaming plain reads gets about 51 B/cycle (weights) or 44 B/cycle (KV) while each plain core gets 92-93, paced or not. The weights hold within 3% (155 / 146). | Mark the KV rows of the §2.5 table as single-FIFO results. Add to the Phase 7 check the 8-core per-PC case and a mixed-traffic case (one converting core, seven plain). Give the runtime two options to cost there: a 24 KiB ring for KV-heavy kernels, and per-initiator fairness in the HBM scheduler. My per-PC model has no bank timing, no FR-FCFS and no mesh, so Phase 7 decides. |
| Y-5 | L | Z-1 | **Partial-granule responses are not specified.** A cut read is a request that starts or ends mid-granule. `RD_RSP` has no `len`, so the valid bytes, the payload alignment and the meaning of `gidx` for a request that starts mid-granule are implied, not stated. | An NI that packs the payload from the request's first byte, rather than from the granule's, writes the second half of a cut granule shifted by its offset, silently. Or an HBM controller that reads whole granules for partial requests doubles the bursts of a cut. | One paragraph in noc.md §2 and microarch.md §2.5. An `RD_RSP` payload is granule-aligned, its valid bytes are the request's range within that granule (from the tag table), `gidx` counts granules from the one holding the first byte, and the controller reads only the 32 B bursts that the range touches. The tag-table entry holds the SPM destination, the element offset and the entry's ring pointer. Phase 3 test: one granule cut at byte 128 into two entries, checking both halves' data, `gidx` and SPM destinations. |
| Y-6 | L | Z-2, W-1 | **A `DMA_PACE` below one request never issues, and the counter is a bit short.** The text says "at most `DMA_PACE` bytes in flight". The CSR takes any multiple of 64 B, so a nonzero limit below 2 KiB (or below the largest scale read) blocks every request of that size. The reference avoids this by letting a converting read issue when none is in flight (`room()`: `infl == 0`). Separately, the counter is 17 bits, while converting reads can hold all 512 granules, 2^17 B, which needs 18 bits (the CSR field is 18). | The runtime writes `DMA_PACE` = 1 KiB to throttle a stream; the first 2 KiB converting read waits forever and the core hangs with no fault. Or, with the limit set at 128 KiB or above, a 17-bit counter wraps when all 512 granules are converting reads, and the next request passes a limit it should not. | State the reference's rule (a converting read may always issue when none is in flight), or fault `ILLEGAL_FIELD` for a nonzero limit below 2 KiB. Make the counter 18 bits. |
| Y-7 | L | Z-4 | **The Engram layout and its bytes are not settled.** The checkpoint has FP8 with a scale per 32, and nothing shows the scales inline. hyper_connections.md §4 keeps "the separate scale array" in the bytes note and G-3's "optional scale-array base", and no spec says who builds 264 B rows. A separate-array gather is legal now (s1 = 256, `aux0` 8) and non-split: one aligned granule plus a 32 B scale read, like the KV gather, so not staged raw (inline: 0.12 rows/cycle). The note's "about 18 KB" fits neither layout: 48 rows of 264 B are 12.7 KB, inline rows touch 9-10 32 B bursts (about 14.9 KB), the separate array 288 B per row (13.8 KB); only 6 x 64 B per row gives 18 KB. `inline_addr` accepts `row_bytes` = 0 for a gather. | The runtime loads the published table as is (256 B rows, scales in their own array), and the compiler emits the inline gather the specs describe. With s1 = 256 it faults `ILLEGAL_FIELD`. With s1 = 264, every row after row 0 is read from the wrong bytes, silently. Or a 197 GB repacking pass appears that no plan item lists. | Say in hyper_connections.md §4 and memmap.md §6 which layout the runtime stores and that it repacks at load, or choose the separate array (no raw staging, about 2.5x the ring rate, two requests per row). Correct the bytes note and G-3. Make `row_bytes` = 0 an `ILLEGAL_FIELD` for an inline gather, or say it means the row size. |
| Y-8 | L | Z-5 | **A credit semaphore's drain target needs the credits expected, not the last wait target.** The base table holds "the previous use's final wait target". A credit semaphore's last wait covers the use before the last, so the drain target is that plus the last use's n. With a data-dependent consumer set (n_u, for example only the destinations that received data), the map does not give it. A last use whose credits no later write waits for is also the one a compiler would drop, and the final credit adds one to each new adder's credit count. | An all-to-all whose consumer set varies per use moves its credit semaphore: P drains to base + (n of the last wait's use) + (a static n), passes early or hangs. Or the consumers' last credit is optimized away, and P's drain wait hangs: a program deadlock that only the X-6 detector catches. | Define the base-table entry of a credit semaphore as credits expected so far, updated when the producer writes a use (+ n_u), so the drain target is the entry. Say that every use is credited, including the last. Say that a final credit counts as the credit of use -2 of that parity of Y. Add a moved data-dependent credit semaphore to the PLAN test programs. |
| Y-9 | L | Z-6 | **The single-adder exception should name `SIGNAL`s.** Its ordering argument (each `SIGNAL` waits for the earlier ones' acks) holds for `SIGNAL` adds from one DQ, not for `rsem` byte adds, whose transfers overlap within a queue. The check flags single-adder adds from two DQs even when each is causally after the owner's wait. | A compiler that implements the exception as "single adder, one DQ" accepts a one-producer `rsem`-counted flag that runs ahead, and two overlapping transfers interleave their counts. | Write "a flag with one adder whose `SIGNAL`s all come from one DQ". Adds from several DQs fall back to the general causal rule instead of being a violation. |
| Y-10 | L | Z-7 | **`T_DRAIN` is a time constant where an epoch bit would be a guarantee.** 10 µs is about 16x the estimated worst transit, but the estimate assumes every sink keeps draining. The header has 6 reserved bits (`rsv` [127:122]), and the bridge already holds the request head it answers. | A long SPM-port stall on a live converting core (unbounded in the spec) holds a stale `ERR` on the mesh past `T_DRAIN`. The restarted step faults `LINK` and recovery runs again. The CP sees a second link-down event that is not one. | Optional: the core puts an epoch bit (a CSR the CP toggles at each restart) in every request header, the bridge copies it into its `ERR`, and the core drops a `LINK` `ERR` of the other epoch. One bit suffices, because (d) already waits a timeout between restarts. Then `err_done` and `T_DRAIN` become belt and braces. Decide before `core/ni` RTL, since it changes the header. Otherwise keep the rule and let Phase 4 measure the worst transit under SPM-port load. |

## Before Phase 3, after verification 4

| Block | Still open |
|---|---|
| `core/dma` | Y-1 (entry size), Y-2 (pace weighted by format and derived from `DMA_STAGE`), Y-3 (what a tag is; arbitration that does not starve multi-tag requests), Y-6 (a limit below one request, an 18-bit counter); text: Y-5. All are spec decisions the RTL would freeze. |
| `core/ni` | Y-5 (`RD_RSP` for partial granules); Y-10 if the epoch bit is taken (header change) |
| `core/seq` | none. Y-8 and Y-9 are compiler rules; the semaphore hardware is unchanged |
| `hbm` | none; X-2 (a), (c)-(e) stay on the H-9 checklist. Per-initiator fairness is a Phase 7 question (Y-4) |
| `common`, `arith` | none |
| Phase 4 `link`, `noc` | Y-10 (epoch bit, or the `T_DRAIN` measurement under SPM-port load); W-2 and Z-7 tests as listed in PLAN |
| compiler / runtime | as after the verification 3 fixes, plus Y-7 (the Engram layout and who builds it), Y-8 (credit base table, credit every use, the final credit's count), Y-9 (wording) |
| Phase 7 | Z-2 / Y-4: per-PC queues with 8 cores, mixed converting and plain cores, and MXFP4 / MXFP8 rows (Y-2), at the default `DMA_PACE` and with it off |

**Can `core/dma` start Phase 3 RTL?** Partly. The address generators (paged, block, inline, gather and scatter), the conversion
datapath, write packets and ack counting can start now: nothing found here touches them. The request-issue side (ring entry
sizing, the pace counter, the tag table and the DQ arbitration) should wait for Y-1, Y-2, Y-3 and Y-6. Each is a few lines of
spec and a reference test, not a redesign, but the RTL would otherwise freeze the choices that cost 16-28% at an 845-1,000-cycle
round trip or starve a DQ. `core/ni` should settle Y-5 (and Y-10, if taken) before its response path is written.

Where an executable check is needed beyond these probes:
- Y-1, Y-2 and Y-3, now, before `core/dma` RTL: `isa.staging_ring` with quarter-size scale-only entries, MXFP4 / MXFP8 streams under
  the limit (flat and weighted), and a plain DQ next to a KV gather without request shrinking (or a test of the stated shrinking).
- Y-5: the Phase 3 `core/dma` / `core/ni` test with a cut granule.
- Y-4 and Y-2: the Phase 7 performance simulator with real per-PC queues and bank timing, 8 cores per stack, mixed traffic.
- Y-8 and Y-9: the Phase 3 golden-model semaphore check, with a moved credit semaphore whose consumer set varies per use.
- Y-10: the Phase 4 late-`ERR` test, with an SPM-port stall on a live core during the drain.

**Not checked:**
- the JESD238 items (still unavailable);
- the generated SV and C++ beyond the lint in `make isa`;
- my per-PC model's fidelity: FIFO per PC, no bank or row timing, no FR-FCFS, no refresh, no mesh, 8 symmetric cores, my own
  address placement;
- the SPM port load of staged scales and raw split units, and SPM arbitration against the DMA write (it bounds Z-7's estimate);
- the RSP flit cost of 32 B scale responses and partial granules (`RD_RSP` is always 2 flits);
- the Z-7 transit bound, estimated rather than modelled;
- whether a step can share a die with another step (L-4 (e) restarts "every core of the step", and a core of another step whose
  transfer used the link also faults);
- the published Engram checkpoint itself (no network; repo notes only);
- the golden-model checks, which do not exist yet (Z-5, Z-6, Y-8 and Y-9 were argued, not run);
- `make numerics`, `make hbm`, `make links`: not rerun, since their inputs did not change.


# Resolutions (verification 4 fixes, 2026-11-01)

**Who:** a fifth fix session (Opus 5.5). It is not the author, the reviewer, any earlier fix session or any verifier. Each
suggested resolution was checked against the specs and then taken, changed or rejected; each row says which and why. The user
chose between real options for Y-2, Y-3, Y-7 and Y-10. The tables above are unchanged. Branch `spec-verification4-fixes` from
9aea46f, one commit per group, `make pytest` green before each.

**Method.** I re-derived every failure scenario before fixing it:
- Y-1 to Y-3 and Y-6 in `isa.staging_ring`, extended for the purpose (below). Y-1 to Y-4 also in a separate minimal model in
  scratch, not committed: a count-based ring, its own issue loop, paced, FIFO and per-PC HBM with N cores, and per-granule
  responses that free credits when they sink.
- Y-5 with a new reference, `isa.rd_rsp_layout`; Y-7 with the address and burst arithmetic in a test.
- Y-8 to Y-10 by argument. The golden-model semaphore check and the Phase 4 link tests do not exist yet.

| Group | Commit | Findings |
|---|---|---|
| 1 | 180d3bf | Y-1, Y-2, Y-3, Y-6 (`core/dma` issue side; Y-2 and Y-3 chosen by the user) |
| 2 | cfffee9 | Y-5, Y-10 (`core/ni`, NoC; Y-10 chosen by the user), Y-4 (table note, Phase 7) |
| 3 | efe47a5 | Y-7 (chosen by the user), Y-8, Y-9 (compiler and runtime) |

| # | Status | Group | What was done |
|---|---|---|---|
| Y-1 | fixed | 1 | **Re-derived.** Quarter-size scale-only entries (4 KiB of NVFP4 scales, 32 KiB of data) at L = 845: `isa.staging_ring` 124.2 paced and 123.2 with 72 KiB under the new defaults (123.3 / 104.1 under the old ones), my own model 123.3 / 121.1. The verifier had 123.1 / 123.8 (own model) and 126.7 / 108.1 (reference). Both pairs agree within 4%. The rule ring ≥ scale rate x 2 L + one entry's scales predicts 124 / 138 / 150 for entries of 128 / 64 / 16 granules (model: 124.2 / 141.3 / 151.1). **Took it.** An entry without raw slots covers at most one data request (≤ 2 KiB in one 2 KiB block); an entry with raw slots is at most a quarter. The "four entries in flight" sentence is replaced by the 2 L + T_entry rule. `isa.staging_ring` checks the sizes (`sizing="rate"`; `"quarter"` is the Z-3 rule), and `test_staging_ring_entry_size` shows the loss and the formula. A Phase 3 check that the RTL forms the specified entries is in PLAN. A side effect worth knowing: contiguous non-split streams now end entries on 2 KiB blocks, so Z-1 cuts occur only in entries with raw slots and in rows that share a granule. microarch.md §2.5, isa.md §5, `isa.yaml` `DMA_STAGE`. |
| Y-2 | fixed (user's choice) | 1 | **Re-derived.** MXFP4 / MXFP8 weights at L = 1,000 under a flat 72 KiB: reference 138.0 / 70.9 (old defaults) and 135.5 / 68.9 (new), my model 133.4 / 67.5. The verifier had 134.7 / 69.0 (own) and 138.0 / 70.9. **Options costed:** (a) count by format; (b) the limit derived from `DMA_STAGE`; (c) both. (b) alone is wrong. The KV gather is bound by its tags (two per row), not by the ring, so its best limit is about 72 KiB at any ring size. A 24 / 32 KiB ring with a derived 108 / 144 KiB limit drops it from 154 to 123 / 110 at 845. The user chose **weighted plus a cap**. Converting bytes count x1 (NVFP4), x1/2 (MXFP4) and x1/4 (MXFP8) against min(`DMA_PACE`, 4.5 x the ring), so the CSR stays and the derived value is a cap. MXFP4 and MXFP8 run at full rate to 1,200 cycles and NVFP4 is unchanged. NVFP4 + MXFP4 in two DQs at L = 1,000 deliver 66 → 80 B/cycle together. At an 8 KiB ring the cap gives the KV gather 130 instead of 104 at L = 500. **Cost found:** a per-row MXFP8 gather with separate scales (8 B scale read + 256 B row; Engram, Y-7) is tag-bound and loses the pacing: 0.30 → 0.22 rows/cycle at 845. It is hidden (G-1) and still 3x the inline rows. Not reproduced: the verifier's two-DQ numbers (72.5 → 81.6 and 96.9 → 115.7 elements/cycle). Over a common window they would be 92 B/cycle, above the 87 B/cycle share, so I take them as per-stream windows. `test_staging_ring_delivery_limited` (MX rows), `test_staging_ring_pace_cap`, the §2.5 table (MXFP4 and MXFP8 columns). microarch.md §2.5, isa.md §5, `isa.yaml` `DMA_PACE`. |
| Y-3 | fixed (user's choice, after a correction) | 1 | **Re-derived**, with per-DQ rates measured while both streams run (`bw_dq`). A KV gather next to a plain 2 KiB stream, requests not shrunk, a DQ that lacks credits passing its turn (`arb="greedy"`): the plain DQ gets 3.6 / 3.1 B/cycle at L = 500 / 845 with tags only and 6.2 / 4.5 with the pace. The KV gather gets 84 / 73 / 81 / 83 B/cycle with scales. The verifier had 0.8 / 1.3 / 3.5 / 3.6 and 86.4 / 73.0 / 83.6 / 83.1. The KV side is within 2%; the plain side is in the same 1-6 band, too small for a 5% comparison. **My first measurement was wrong:** over the 25-75% element window it included a tail after the gather had finished, and showed plain at about 30. I fixed the window before deciding. **Tags:** the user chose one tag per request (a 512-entry table, the 9-bit `tag`, `gidx` per response) plus 512 granule credits. **Arbitration:** the verifier's proposal, holding the turn for one request, starves the KV gather instead (5 against 81 B/cycle): round-robin per request hands out bytes in proportion to request size. I had first shown the user turn-holding numbers taken from the faulty window, corrected them, and the user then chose **credit-fair turns** (the winner keeps its turn for 8 credits, holding it for credits and pace, never for the ring; no shrinking). **New, found during the fix:** ring space starves the same way. Next to a KV gather's 32 B entries, a weight stream's 256 B entries never fit (0 against 77 B/cycle). So ring allocation also takes turns: DQs that cannot start their next entry queue, and the head allocates 256 B of ring before the turn passes. It is live: others' data reads still issue, and allocated entries need only tags. Data B/cycle at 845 under the rule: KV gather + plain 28 / 56, two KV + plain 18 / 18 / 39, weights + plain 41 / 41, weights + KV 37 / 37. `test_staging_ring_tag_arbitration`; microarch.md §2.5 (tags, credits, turns, the comparison table), isa.md §5, `isa.yaml` `dma_outstanding_reads`. |
| Y-4 | spec part fixed; rate left to Phase 7 | 2 | **Re-derived** in the scratch model (32 per-PC FIFOs of 21.75 B/cycle, 8 cores, PC from the memmap.md §3 hash). The paged KV gather on 8 cores gives 146 / 140 at L = 500 / 845, against the verifier's 144 / 132: 1.4% and 5.5%, just outside 5% at 845. The weights give 149 / 143 against 155 / 146. Tags only gives 128 / 109 against 126 / 107. One converting core among seven plain cores gets 50 B/cycle (weights) and 49 (KV) against 51 / 44, with the plain cores at 90-93. My ring has no wrap fragmentation and my address placement is my own; with a 24 KiB ring my model gains 2 at 845 where the verifier's gains 9. **Costed:** byte-unweighted round-robin per initiator at the PCs helps the weight core (50 → 85 / 74) but hurts the KV core (49 → 41 / 38), whose 32 B scale reads count as whole turns, so any fairness must be weighted by bytes. **Done:** the §2.5 table is marked as single-FIFO results, a per-PC paragraph gives both models' numbers and both options, PLAN Phase 7 adds the 8-core, mixed (1 + 7, 4 + 4) and MX cases and the two options, and hbm.md H-9 and noc.md §7 list per-initiator fairness. **Not done:** `isa.staging_ring` is not extended to per-PC queues. It is the single-core reference for the Phase 3 RTL, and a hand-made multi-core HBM model without bank timing, FR-FCFS or the mesh would claim a per-PC result that only Phase 7 can give. |
| Y-5 | fixed | 2 | **Re-derived** by argument: `RD_RSP` has no `len`, and the payload alignment and `gidx` origin were unstated. hbm.md H-2 already queued "first burst, burst count" descriptors, so the controller side existed but was not written as a rule. **Took it.** A request gets one `RD_RSP` per granule it touches; `gidx` counts from the granule of its first byte; the payload is the granule, aligned; the valid bytes are the request's range in it, from the tag table; a target reads only the bursts (HBM) or bytes (SRAM) the range touches, so a burst cut inside is read twice. Tag-table entry: SPM destination, first-byte offset, `len`, outstanding-granule mask, DQ, format, ring entry, element offset (about 80 bits, 5 KiB). **Added** `isa.rd_rsp_layout` and `test_rd_rsp_layout`: the cut at byte 128, a 264 B row from byte 8, two rows sharing a burst, and 200 random requests reassembled in SPM. Phase 3 `core/ni` test in PLAN. noc.md §2, microarch.md §2.5, hbm.md H-2, `isa.yaml` `gidx`. |
| Y-6 | fixed | 1 | **Re-derived:** under the literal limit, `DMA_PACE` = 1 KiB with 2 KiB reads makes no progress after cycle 8,058 (`pace_idle=False`). With the reference's rule the stream completes. With the limit off and a 32 KiB ring, the counter reaches exactly 2^17, so it needs 18 bits. **Took** the reference's rule (a converting read may issue when none is in flight) over a fault, because it needs no new fault case and any nonzero limit works. The counter is 18 bits. `test_staging_ring_pace_below_one_request`; microarch.md §2.5, `isa.yaml` `DMA_PACE`. |
| Y-7 | fixed (user's choice) | 3 | **Re-derived.** Per token, 48 rows (2 layers x 24). In the separate array a row is one aligned 256 B granule plus one 32 B burst holding its 8 B of scales: 288 B, 13.8 KB per token. Inline 264 B rows at a pitch of 264 from an 8 B-aligned base sit at offsets 0, 8, 16, 24 mod 32 and always touch 9 bursts, also 288 B; the verifier's 14.9 KB assumed 9-10. Ten bursts occur only from a base that is not 8 B-aligned. "About 18 KB" counted 64 B bursts, which this HBM does not have (32 B, memmap.md §3). `isa.block_addr` gives the separate-array addresses (s1 = 256, `aux0` = 8). **The user chose the separate array**: the runtime stores the table as published, with no repack. Each row is non-split, at about 0.27 rows/cycle against 0.12 inline at L = 500. The sibling staging path moves two arrays. `SCALE_INLINE` stays legal for a repacked table. A gather's `row_bytes` = 0 with `SCALE_INLINE` is now `ILLEGAL_FIELD`; `DMA_COPY` ignores the field. hyper_connections.md §4 (table, bytes note, G-3 as built, I-8 staging), memmap.md §6, microarch.md §2.5, isa.md §5, `isa.yaml`, `isa.inline_addr` (`gather`), `test_engram_separate_array_gather`, PLAN Phase 3. |
| Y-8 | fixed | 3 | **Re-derived** by argument. A credit semaphore's last wait covers the previous use of its parity, so the drain target needs the last use's n. With n_u varying per use, "last wait target + n" is wrong. **Took it.** A credit semaphore's base-table entry is the credits expected so far, raised by n_u when the producer writes use u and by 1 for a final credit. Its wait target before a write is the entry; its drain target is the entry. Consumers credit every use, the last included. The final credit counts as the credit of use -2 of that parity of Y. PLAN Phase 7 test programs: the moved data-dependent credit semaphore, and the same program with the last credit dropped, which must be reported at the drain wait. Not run: no golden model yet. isa.md §3, d2d_c2c.md L-6. |
| Y-9 | fixed | 3 | **Re-derived** by argument: the in-order argument needs each add to wait for the earlier adds' acks, which holds for `SIGNAL`s in one DQ and not for `rsem` byte adds of overlapping transfers. **Took it.** The exception is "a flag with one adder whose adds are `SIGNAL`s from one DQ". Any other single-adder flag falls back to the general causal rule instead of being a violation. PLAN test programs: an `rsem` single-adder flag and a two-DQ `SIGNAL` flag, legal when causal, flagged when one runs ahead. isa.md §3, d2d_c2c.md L-6, PLAN Phase 7. |
| Y-10 | fixed (user's choice) | 2 | **Re-derived** by argument: 10 µs is about 16x the estimated transit only if every sink keeps draining, and SPM-port arbitration against the DMA is unbounded in the spec. **The user took the epoch bit.** `noc_header` `epoch` (bit 122) on `RD_REQ`, `WR`, `SEM` and `ERR`, leaving 5 bits reserved. A new CSR `NI_EPOCH` (0x023) holds the core's epoch, and the CP toggles it on every core of the step before the restart. The bridge copies the request's bit into its `ERR`, and a core drops a `LINK` `ERR` of the other epoch. One bit suffices, because two restarts are separated by conditions 1-3 and a retrain. Other `ERR` causes free tags and are covered by condition 1. `err_done` and `T_DRAIN` stay as a backstop. `make isa` regenerated the header. d2d_c2c.md L-4 (a), (f), L-9, L-11; noc.md §2, §7; isa.md §8; `isa.yaml`; header test; PLAN Phase 3 `core/ni` and Phase 4. Not run: the Phase 4 test. |

**Proposals not taken as written** (the row says why):
- Y-2: the limit derived from `DMA_STAGE` alone was rejected (KV gather); it is a cap on the CSR instead.
- Y-3: turn-holding per request was rejected, because it starves one-granule requests. Credit-fair turns of 8 credits were taken, plus ring turns, which the finding did not ask for.
- Y-4: no per-PC rate is claimed, and `isa.staging_ring` stays single-core.
- Y-5: as suggested, plus an executable reference.
- Y-6: "issue when none is in flight" was taken rather than an `ILLEGAL_FIELD`.
- Y-7: the separate array rather than inline rows; the verifier's 14.9 KB for inline rows is 13.8 KB at 8 B alignment.

**`isa.staging_ring` changes** (the reference the `core/dma` RTL is compared against):
- New parameters, with the rule as default: `sizing="rate"`, `arb="fair"` (also `"hold"`, `"greedy"`, `"shrink"`), `ring_turn=True` with `ring_q_bytes=256`, `rsp="granule"`, `pace="format"` with `fmt` per DQ and the 4.5 x ring cap, and `pace_idle=True`.
- New outputs: `bw_dq` (per DQ, over the window where every DQ still receives data) and `peak_infl`.
- With one response per granule, the tags-only queue numbers are now the third verifier's: weights 130 / 110 / 103, against 142 / 119 / 111 with `rsp="request"`. The rule's rows of the §2.5 table hardly move (155 / 151 / 127).
- The Z-1 reproduction (cycle 1,715 ± 5, and the 16 KiB deadlocks) is pinned to the arbitration of that session (`Z1_ARB`: `arb="shrink"`, `ring_turn=False`). Under the new arbitration "shared" still deadlocks at 4 KiB (index key at cycle 2,458), and the 16 KiB index-key case completes: the deadlock depends on timing, which the verifier saw too. Cutting stays the rule.
- Liveness: every existing test, including the 25 random streams, passes under the new defaults.

**Regenerated:**
- `make isa`, groups 2 and 3: `isa_pkg.sv`, `isa.h`, `isa_defs.py`, `isa_ref.md` (the `epoch` field, `NI_EPOCH`, comments). In group 1 only YAML comments changed, and the generators wrote identical files. The lint of `isa_pkg.sv` is clean, and `git status` was clean after a rerun.
- `make numerics`, `make hbm`, `make links`, `make sweep`, `make models`: not rerun. No input changed: `numerics.py`, `hbm.py`, `links.py`, `noc.py`, `opgraph.py` and `ingest.py` are untouched (`git diff --stat 9aea46f` shows only `isa/isa.py` under `sw`, `sim` and `isa`).

**Frozen numbers:** none changed. The ring stays 16 KiB of SPM and `DMA_PACE` resets to 72 KiB. The tag table keeps 512 entries (about 5 KiB). The pace counter grows by one bit to 18, the header uses one reserved bit, and one CSR is added. D1 is unchanged. arch.md has a change-log row.

**Tests:** `make pytest` 224 passed (218 before). `test_isa.py` 43 → 49:
- new: entry size, pace cap, tag arbitration, pace below one request, `rd_rsp_layout`, the Engram separate-array gather;
- extended: the delivery-limited test (MXFP4, MXFP8, per-granule responses), the header test (`epoch`), the inline gather (`row_bytes` 0).

## Before Phase 3, after the verification 4 fixes

| Block | Still open |
|---|---|
| `core/dma` | none at the spec level (Y-1, Y-2, Y-3, Y-6 fixed). The read-issue arbiter (credit-fair turns, ring turns) is new in this session and unverified. Phase 3 tests in PLAN: entry sizes, the weighted and capped pace, tags and credits, the three arbitration cases, plus the earlier ring, inline and paged tests |
| `core/ni` | none at the spec level. Y-5: partial-granule responses and tag-table contents. Y-10: the epoch in the header and `NI_EPOCH` |
| `core/seq` | none. Y-8 and Y-9 are compiler rules; the semaphore hardware is unchanged |
| `hbm` | none; X-2 (a), (c)-(e) stay on the H-9 checklist; per-initiator fairness is an H-9 / Phase 7 item (Y-4) |
| `common`, `arith` | none |
| Phase 4 `link`, `noc` | Y-10: the bridge copies `epoch` into its `ERR`; the late-`ERR` test with the epoch (PLAN); the W-2 and Z-7 tests |
| compiler / runtime | as after the verification 3 fixes, plus: Y-7 (store the Engram table as published; the sibling stages rows and scales), Y-8 (credit base table, credit every use, the final credit as use -2), Y-9. `DMA_PACE` no longer needs rescaling with the ring (the cap) |
| Phase 7 | Y-4: 8 cores on per-PC queues, mixed converting and plain cores, and MXFP4 / MXFP8 at the default `DMA_PACE` and with it off; cost a 24 KiB ring and byte-weighted fairness per initiator |

**Can `core/dma` and `core/ni` start Phase 3 RTL?**
- `core/ni`: **yes.** The response path (partial granules, tag table) and the header (`epoch`) are fixed, with a reference and a test.
- `core/dma`: **yes, except its read-issue arbiter.** The address generators, conversion datapath, staging ring allocation, entry sizing, pace counter, tag table and write path are specified with reference tests. The arbiter that hands out credits and ring space in turns was designed in this session, after the user's first choice turned out to starve the KV gather, and no one else has checked it. It should wait for the next verification pass, items 1 and 2 below. It is a small block at the edge of the issue side, so the rest need not wait.

**For the next verification pass, check first:**
1. **Ring turns and credit-fair turns (Y-3).**
   - Liveness: a DQ at the head of the ring queue waits for the ring to drain while another DQ may hold the issue turn for credits. Is there a cycle? Only tests back the argument: the 25 random streams, the four-DQ mixes and the arbitration tests. A formal property is needed.
   - Fairness under a 1-tag / 8-tag mix: is 8 credits the right quantum, and 256 B the right ring quantum? Do the turns cost a single converting stream anything, for example Y-1's entry sizes with a plain neighbour?
2. **The pace rule across formats and mixed DQs (Y-2):**
   - the weighted count with NVFP4, MXFP4 and MXFP8 in different DQs;
   - the cap at ring sizes other than 8 and 16 KiB;
   - the MXFP8 per-row gather it gives up (Engram, 0.30 → 0.22 rows/cycle at 845);
   - whether a weight per format, rather than per entry shape, is the right key.
3. **The partial-response definition end to end (Y-5):** `isa.rd_rsp_layout` against noc.md §2, hbm.md H-2 and the tag-table entry in microarch.md §2.5, for SRAM targets as well as HBM, and the element offset of a cut fold tile (raw-staged) as well as a non-split NVFP4 granule.
4. **What changed in `isa.staging_ring`:**
   - the new defaults (`arb="fair"`, `ring_turn`, `rsp="granule"`, `pace="format"`, `sizing="rate"`);
   - the `bw_dq` overlap window, which an earlier window artifact in this session got wrong;
   - the Z-1 numbers pinned to the old arbitration (`Z1_ARB`): check that the pinning hides no regression, and that "shared" deadlocks under the new arbitration in more than the 4 KiB cases tested.
5. **Y-4 model differences:** my per-PC model and the verifier's differ by 5.5% at 845 for the KV gather, and by 2 against 9 in what a 24 KiB ring gains. A third model, or Phase 7, should say which is closer.
6. **The epoch bit (Y-10):** whether one bit is enough with back-to-back link failures, and what the CP must do if a step spans cores whose `NI_EPOCH` it cannot write (a core of another step that also faulted).
7. **The Engram layout (Y-7):** the sibling staging of two arrays (two gathers and two pushes per owner), and whether `SCALE_INLINE`, now used by no surveyed model, should stay in the ISA.


# Verification 5 (2026-11-01)

**Who:** a fifth verification session (Opus 5.5). It is not the author, the reviewer, any of the fix sessions or the earlier
verifiers. No spec, model or test was changed; this section is the only edit (branch `spec-verification5` from b27474b). New
findings are numbered from X-7, because X-1 to X-6 are taken by the xhigh checks above.

**Method.** I read CLAUDE.md, Verification 4, the resolutions of the verification 4 fixes and the four commits (180d3bf,
cfffee9, efe47a5, b27474b), and the specs their rows cite: microarch.md §2.5 and §2.6, noc.md §2, §4 and §7 (and §5 for flit
costs), isa.md §3, §5 and §8, memmap.md §3 and §6, hbm.md H-2, H-3 and H-9, d2d_c2c.md L-4, L-6, L-9 and L-11,
hyper_connections.md §4, `isa.yaml` (`DMA_STAGE`, `DMA_PACE`, `NI_EPOCH`, `SCALE_INLINE`, `DMA_GATHER`, `noc_header`) and PLAN
Phases 3, 4 and 7. For each Y-n I asked whether the new text closes the verifier's failure scenario and whether it opens a new
one. I walked the seven check-first items in order, then re-checked W-1, V-1, V-3 / X-6 rule 2, Z-1, Z-5 / Z-6 and D1.

**Own model.** Before I read `isa.staging_ring` I wrote a cycle model from microarch.md §2.5 alone (scratch, not committed; no
code in common).
- What it has:
  - Per-DQ entries with HBM byte ranges. Reads are granule pieces grouped into requests inside one 2 KiB block, and scale reads
    that cross a 2 KiB block of the scale array are two reads of one entry.
  - A ring of 16 B units, contiguous with wrap-skip, one allocation per entry at its first scale read (at its first data read
    for inline rows), and in-order reclaim. Raw slots for inline rows and for fold tiles from byte 64.
  - 512 tags (one per request) and 512 granule credits (one per granule touched). The pace is weighted by format and capped at
    4.5 x the ring, and a converting read may issue when none is in flight.
  - Credit-fair turns (the winner keeps its turn for 8 credits and holds it for credits and pace) and ring turns (a queue
    entered by a DQ whose entry does not fit or that finds others waiting; the head allocates 256 B before the turn passes).
    Greedy read-ahead within a DQ, and one request per cycle.
  - One response per granule. One `ni_rsp` FIFO per core, sinking a piece in max(elements / 256, bytes / 512) cycles; credits,
    tags and pace are returned at the sink.
  - HBM: `paced` (an issue budget at 87 B/cycle, responses L after issue), `server` (a FIFO at 87 B/cycle, L/2 out and back,
    32 B burst rounding) and `pc` (32 per-PC FIFOs of 21.8 B/cycle shared by N cores, PC from the memmap.md §3 hash).
  - Cross-DQ semaphore waits and signals between transfers, which the reference does not have.
- What it shares with the reference: the rule text, the three HBM abstractions as Verification 4 describes them, the 87 B/cycle
  share, and the stream shapes of the §2.5 table (8-granule weight entries, one entry per KV row).
- What it does not share: the code, address-based pieces everywhere, burst rounding, a separate tag count, credits returned at
  the sink rather than on arrival, a ring queue entered only under contention (the reference makes every allocation take the
  turn), semaphores, per-PC HBM with 8 cores, and no `boundary="shared"` mode (Z-1 is checked with the reference only).
- Per-DQ rates are data bytes per cycle over the window where every DQ still receives data: from the latest DQ's 5% mark to
  the earliest DQ's 95% mark. Single-stream rates are elements/cycle between 25% and 75%.

**Executable checks** (all under `scripts/memrun`):
- `make pytest`: 224 passed (rc 0, 27.5 s, 76 MB peak).
- `make isa`: the generators rewrote identical files, `git status` was clean afterwards, and `verilator --lint-only -Wall` on
  `isa_pkg.sv` passed.
- `make numerics`, `make hbm`, `make links`: not rerun. `git diff --stat 9aea46f b27474b` changes no model input: under
  `sw`, `sim` and `isa` only `isa/isa.py` changed, and it is not an input of those three.

## Probes (own model and `isa.staging_ring` side by side)

**1. Single stream, 16 KiB ring, `hbm="server"` unless paced** (elements/cycle, L = 500 / 700 / 845 / 1,000 / 1,200):

| stream | own model | `isa.staging_ring` | §2.5 table |
|---|---|---|---|
| NVFP4 weights, paced | 154.7 / 154.7 / 152.7 / 129.1 / 107.6 | 154.7 / 154.7 / 153.3 / 128.6 / 106.7 | 155 / 155 / 153 / 129 / 107 |
| NVFP4 weights, tags only | 130.4 / 118.6 / 111.3 / 104.4 / 96.7 | 129.6 / 117.7 / 110.3 / 103.4 / 95.7 | 130 / 118 / 110 / 103 / 96 |
| NVFP4 weights, the rule | 154.7 / 154.7 / 150.7 / 127.9 / 107.0 | 154.7 / 154.7 / 150.7 / 127.4 / 106.3 | 155 / 155 / 151 / 127 / 106 |
| KV gather, paced | 154.7 / 154.7 / 154.5 / 130.8 / 109.1 | 154.7 / 154.7 / 154.7 / 130.5 / 108.2 | 155 / 155 / 155 / 130 / 108 |
| KV gather, tags only | 133.1 / 118.7 / 111.4 / 104.5 / 96.8 | 136.0 / 117.9 / 110.6 / 103.6 / 95.9 | 136 / 118 / 111 / 104 / 96 |
| KV gather, the rule | 154.7 / 154.7 / 154.1 / 130.3 / 108.7 | 154.7 / 154.7 / 154.2 / 130.0 / 108.0 | 155 / 155 / 154 / 130 / 108 |
| MXFP4 weights, flat 72 KiB | 164 / 164 / 159 / 134 / 111 | 163.8 / 163.8 / 159.5 / 135.5 / 113.5 | 164 / 164 / 160 / 136 / 114 |
| MXFP8 weights, flat 72 KiB | 85 / 85 / 83 / 69 / 57 | 84.4 / 84.4 / 82.1 / 68.9 / 57.1 | 84 / 84 / 82 / 69 / 57 |
| MXFP4 / MXFP8 weights, the rule or tags only | full rate (164 / 85) at every L | full rate (163.8 / 84.4) | all |

Every row agrees within 2.2%. The largest gap is the KV gather with tags only at L = 500 (133 against 136).

**2. Ring sizes with the cap** (the rule, elements/cycle at the same five L; own / reference):

| ring (cap) | NVFP4 weights | KV gather | MXFP4 | MXFP8 |
|---|---|---|---|---|
| 4 KiB (18 KiB) | 63 / 45 / 38 / 32 / 27 · 62 / 45 / 38 / 32 / 27 | 65 / 47 / 39 / 33 / 27 · same | 125 / 91 / 75 / 64 / 54 · 125 / 90 / 75 / 64 / 53 | 84 / 83 / 75 / 64 / 54 · 84 / 83 / 75 / 64 / 53 |
| 8 KiB (36 KiB) | 125 / 90 / 75 / 64 / 54 · 125 / 90 / 75 / 64 / 53 | 130 / 93 / 77 / 65 / 54 · 130 / 93 / 77 / 65 / 55 | 164 / 164 / 151 / 128 / 107 · 164 / 164 / 151 / 127 / 106 | all · all |
| 24 KiB (72 KiB) | 155 / 155 / 151 / 128 / 107 · 155 / 155 / 151 / 128 / 106 | 155 / 155 / 154 / 130 / 109 · 155 / 155 / 154 / 130 / 108 | all · all | all · all |
| 32 KiB (72 KiB) | as 24 KiB · as 24 KiB | as 24 KiB · as 24 KiB | all · all | all · all |

The cap matches the §2.5 rows (8 KiB: 125 / 90 / 75 / 64 / 53 and 130 / 93 / 77 / 65 / 55). It helps MXFP4 at a 4 KiB ring (125
against 104 flat at L = 500) and the KV gather at 8 KiB (130 against 104). At 24-32 KiB the CSR's 72 KiB binds, as intended. The
derived limits that §2.5 rejects also reproduce: 24 KiB with 108 KiB and 32 KiB with 144 KiB give the KV gather 123.1 / 110.4
at 845 (§2.5: 123 / 110).

**3. Arbitration mixes at L = 845, `DMA_PACE` 72 KiB** (data B/cycle per DQ while all run; own / reference / §2.5):

| arbitration | KV + plain | two KV + plain | NVFP4 weights + KV | NVFP4 weights + plain |
|---|---|---|---|---|
| `greedy` | 72/6 · 72/6 · 72/6 | 37/37/3 · 38/38/1 · 37/37/3 | 37/37 · 37/37 · 37/37 | 38/44 · 38/44 · – |
| `hold` | 5/81 · 5/81 · 5/81 | 5/5/76 · 5/5/76 · 5/5/76 | 37/37 · 38/38 · 38/38 | 28/56 · 28/56 · – |
| **`fair` + ring turns (the rule)** | 28/55 · 28/56 · 28/56 | 20/20/39 · 18/18/39 · 18/18/39 | 37/37 · 37/37 · 37/37 | 41/41 · 41/41 · – |
| `fair`, no ring turns | 28/55 · 28/56 · 28/56 | 24/9/49 · 20/20/41 · 20/20/41 | **24/52** · 0/77 · 0/77 | 41/41 · 41/41 · – |

The rule's rows agree within 2 B/cycle. The last row's starvation is timing-dependent: my model finds the same direction (a
weight stream at 24 against 52) but not the reference's 0, because which DQ takes freed ring space first depends on the order
in which DQs are offered it. At L = 500 the rule gives the same shares (28/56, 21/21/41, 39/39, 41/41).

**4. Fairness knobs (own model, L = 845; the reference where it has the knob):**
- Issue quantum (credits per turn) 1 / 4 / 8 / 16 / 32: KV + plain 5/81, 17/68, 28/55, 28/55, 28/55; two KV + plain 5/5/76,
  14/14/56, 20/20/39, 20/20/41, 22/22/27; weights + KV 37/37 up to 8, 36/36 at 16, 31/31 at 32. So 8 sits at the start of the flat
  region: smaller starves one-credit DQs, and 32 starts to cost the plain DQ.
- Ring quantum 32 / 64 / 256 / 1,024 B: weights + KV 66/8, 60/15, 37/37, 37/37 (the reference: 66/8, 60/15, 37/37, 37/37).
  256 B is the smallest quantum that lets a 256 B NVFP4 entry compete with 32 B KV entries.
- **Two converting DQs of different formats** (X-10). NVFP4 + MXFP4: 27/54 (reference 27/54). NVFP4 + MXFP8: 17/66 (17/66).
  NVFP4 + MXFP8 + plain: 10/37/37 (10/37/38). MXFP4 + MXFP8 + plain: 19/33/33 (19/33/33). The totals stay at 80-85 B/cycle, but
  the ring turn hands out 256 B of ring, which is one NVFP4 entry, two MXFP4 entries or four MXFP8 entries. A ring quantum of
  8 data granules (one data request) gives 40/40, 40/40 and 28/28/28, and leaves weights + KV (37/37), KV + plain (28/55) and
  two KV + plain (20/20/39) unchanged.
- **Do the turns cost a single stream anything?** No. The rule and `greedy` without ring turns give identical rates for one
  stream (NVFP4 154.7 / 150.7 / 127.9, KV 154.7 / 154.2 / 130.4, MXFP8 84.4 at 500 / 845 / 1,000). Y-1's entry sizes next to a
  plain DQ at 845 (8 / 16 / 64 / 128 granules): converting 82 / 82 / 76 / 67 elements/cycle and plain 41 / 41 / 44 / 49 B/cycle,
  against 151 / 149 / 138 / 124 alone. The split is even in credits, and large entries lose ring coverage as they do alone.

**5. The pace and the turn** (X-8). With `DMA_PACE` 16 KiB at L = 845, NVFP4 weights + plain: own 17/17, reference 14/38 under
the rule, against 10/76 and 9/77 under `greedy`. KV + plain: own 17/34, reference 13/39, against 11/75 under `greedy`. The
converting DQ holds the turn while it waits for pace, so the plain DQ, which the text says is limited by tags only, gets at most
one turn per paced request. My model's coupling is stronger than the reference's, and I did not find why. The plain DQ loses
48-78% of its rate, and the total 40-60%. At the default 72 KiB with a 16 KiB ring there is no loss at 845. With a 2 KiB ring
(cap 9 KiB) the reference's plain DQ gets 64-67 against 80-81, and mine 81. A variant in my model, where a request that waits
only for pace lets a DQ whose next read is plain issue, gives 10/76 and 11/75 at 16 KiB. It leaves every default case unchanged
(41/41, 28/55, 20/41/20 for KV + plain + weights) and completes every run.

**6. Liveness of the turns** (check-first item 1; own model; the reference has no semaphores):
- **Targeted: the ring-turn head goes idle** (X-7). 2 KiB ring; DQ0 streams NVFP4 weights and keeps the ring full; DQ1 gathers
  37 KV rows, waits on flag F, then gathers 37 more; DQ2 gathers 200 KV rows and sets F when they complete. DQ1 reaches the head
  of the ring queue, allocates five 32 B entries (160 B, below the 256 B quantum) and waits on F. DQ2 and DQ0 wait behind it in
  the queue. At L = 500 nothing moves after cycle 3,628, with the ring empty and every credit free. The reference's end-of-stream
  pass hangs the same way, because DQ1 still has work. This holds at L = 300, 500 and 845 with 2 and 4 KiB rings; at 16 KiB the
  ring never queues and the run completes. With no semaphore at all, a DQ whose stream ends at 160 B into its turn blocks the others the same way
  (`isa.staging_ring` passes the turn there, in `_ring_release`, but the text does not say so). With the turn passed whenever the
  holder has nothing to allocate, every case completes.
- **Random programs:** 400 programs of 4 DQs, each 1-5 transfers drawn from NVFP4 / MXFP4 / MXFP8 weights (data and scale
  offsets random, so scale reads cross 2 KiB blocks), KV gathers, inline rows (raw slots, allocated at the first data read), fold
  tiles from byte 64 (raw slots plus scales), Engram rows and plain reads. Cross-DQ semaphore waits go only to earlier transfers,
  so every program is deadlock-free by itself. Rings 2-16 KiB, L = 50-845, `DMA_PACE` 0 / 1 / 8 / 72 KiB, 8-512 credits, paced or
  server HBM. Deadlocks: **332** with the turn kept until 256 B whatever the holder has left (the text), **119** with the turn
  passed only at the end of the DQ's stream (the reference's rule), **0** with the turn passed whenever the holder has no entry
  to start.
- **Targeted: the item 1 deadlock attempt.** Four DQs: inline rows (one row per entry), fold tiles from byte 64, NVFP4 weights
  with scale reads across 2 KiB blocks, and plain 2 KiB reads. Rings 2 / 4 / 8 KiB, 8 / 9 / 16 / 24 / 64 / 512 credits (8: a plain
  read needs every credit), L = 50 / 300 / 845, `DMA_PACE` 0 / 1 / 72 KiB, paced and server: 324 runs, 0 deadlocks (with the idle
  pass). In 4.49 M cycles one DQ held the issue turn while a different DQ was at the head of the ring queue.

**7. Z-1 under the new arbitration** (`isa.staging_ring`, `boundary="shared"`, `sizing="quarter"`, four L of 100-845, paced and
server with 72 KiB; deadlocked / runs):

| stream | DQs | 4 KiB | 8 KiB | 16 KiB | 32 KiB |
|---|---|---|---|---|---|
| index key, the rule | 3 / 4 | 8/8, 8/8 | 0/8, 8/8 | 0/8, 0/8 | 0/8, 0/8 |
| index key, `Z1_ARB` | 3 / 4 | 0/8, 8/8 | 1/8, 8/8 | 2/8, 3/8 | 0/8, 1/8 |
| fold tiles from byte 64, the rule | 3 / 4 | 0/8, 8/8 | 0/8, 8/8 | 0/8, 8/8 | 8/8, 8/8 |
| fold tiles, `Z1_ARB` | 3 / 4 | 0/8, 8/8 | 0/8, 8/8 | 0/8, 8/8 | 0/8, 8/8 |

`boundary="cut"` completed in every one of these runs under both arbitrations. So "shared" deadlocks under the new arbitration
far beyond the 4 KiB case the test asserts: fold tiles with four DQs at every ring size, the index key at 4-8 KiB. Some cases
deadlock only under the new arbitration (index key, 4 KiB, 3 DQs; fold tiles, 32 KiB, 3 DQs) and some only under the old one
(index key at 16 KiB). The pinning to `Z1_ARB` reproduces the earlier numbers and hides no regression.

**8. Partial responses** (check-first item 3). `isa.rd_rsp_layout` agrees with noc.md §2 (one `RD_RSP` per granule touched,
`gidx` from the first byte's granule, payload aligned, valid bytes from the tag table), hbm.md H-2 (descriptors with first burst
and burst count; a burst cut inside is read twice) and `isa.yaml` `gidx` (3 bits: at most 8 granules inside one 2 KiB block;
`len` 12 bits holds 2,048). For SRAM targets the text says "only the bytes". The element offset is tested only for the NVFP4
granule cut at byte 128 (element 256). Two cases are not covered (X-12):
- a request whose bytes have more than one SPM destination run;
- a fold-tile request from byte 64, whose every granule carries the 64 B tail of a split tile (raw slot), a whole tile
  (converted) and the 64 B head of the next split tile (another raw slot).

`ERR` and timeout: an `ERR` other than `LINK` frees the tag and the credits left in the 8-bit mask, and the core is stopped.
Since a request stays inside one 2 KiB block, it has one target and gets either responses or one `ERR`, so a partial request
needs no new rule.

**9. Response flits on the mesh** (X-9; noc.md §5 routes, `sim/analytic/noc.py`, with my own flit counts). Each `RD_RSP` is 2 flits,
also for a 32 B scale read or an 8 B Engram scale. Eight cores of a stack on the paged KV gather at the 0.85 share (a 32 B scale
read plus a 256 B row: 4 RSP flits and 2 REQ flits per 288 B): the busiest HBM leaf → mesh link is at **1.21** (1.42 at the full
share), against 0.68 for 2 KiB plain reads at the same rate. With a 1-flit response for a range inside one 128 B half, it is
0.91.

**10. Per-PC queues, a third model** (check-first item 5; own `pc` mode, 8 cores of one stack, `DMA_PACE` 72 KiB, mean / slowest
core):

| case | own, L = 500 (mean / slowest) | own, L = 845 | Verification 4 (500 / 845) | fix session (500 / 845) |
|---|---|---|---|---|
| 8 x KV gather, 16 KiB | 147.4 / 145.4 | **133.5** / 132.5 | 144 / 132 | 146 / 140 |
| 8 x KV gather, 24 KiB | 152.6 | 142.1 (+8.6) | 152 / 141 (+9) | 146 / 142 (+2) |
| 8 x KV gather, 32 KiB | 153.5 | 142.0 | 152 / 141 | |
| 8 x KV gather, tags only | 124.7 | 105.4 | 126 / 107 | 128 / 109 |
| 8 x NVFP4 weights | 153.5 / 152.8 | 144.4 / 143.7 | 155 / 146 | 149 / 143 |
| 1 converting + 7 plain, weights (B/cycle; plain cores) | 51 (92) | 49 (92) | 51 (92) | 50 (90-93) |
| 1 + 7, KV gather | 52 (93) | 48 (93) | 44 (93) | 49 |
| 4 + 4, weights | 61 (112) | 58 (112) | 62 (112) | |

With the ring as a byte count freed out of order (no in-order reclaim), the same model gives 150.3 / 137.6 at 16 KiB and gains
only +5 at 845 from 24 KiB.

**11. The pace key** (check-first item 2; own model, server, 72 KiB):
- A per-row gather of MX rows (256 B row plus its scale read, one entry per row), elements/cycle at L = 500 / 845 / 1,000:
  - MXFP4: by format 134 / 110 / 103, flat 155 / 151 / 130;
  - MXFP8: by format 67 / 55 / 52, flat 77 / 75 / 65.
- Engram (separate array): 0.260 / 0.218 rows/cycle by format at 500 / 845, against 0.302 / 0.291 flat (§2.5: 0.27 / 0.22 and
  0.30). Inline rows: 0.117 / 0.070 (§2.5: 0.12 / 0.07).
- Counting entries whose data is one granule in full and longer entries by format ("by entry shape"): these gathers get the
  flat rates, and the MX weight streams keep the full rate to 1,200 cycles (164 / 84). NVFP4 is unchanged.

**12. The `bw_dq` window.** `isa.staging_ring([kv, plain[:60]], hbm="server", inflight=72 KiB, rt=845)` returns `bw_dq` 77/0. The
plain DQ ran, but it finished before the 25% mark, so the window falls back to 25-75%, where it had no data. The streams of
`test_staging_ring_tag_arbitration` are long enough that none ends before the 25% mark (by my estimate from the 18/18/39 shares,
the short KV DQ of two KV + plain ends after the 75% mark), so its asserts are not affected.

## Check-first list, in order

1. **Ring turns and credit-fair turns (Y-3).**
   - **Liveness argument.** It is not complete as written. The issue turn has its early pass ("passes early when the DQ has
     nothing ready"); the ring turn has none, so a holder that has nothing to allocate keeps it (probe 6, X-7). With that pass
     added, the argument closes:
     - a held issue turn waits only for credits or pace, which responses free;
     - a ring-queue head waits only for space, which allocated entries free, since their reads need only credits, pace and
       issue turns, and those rotate;
     - an empty ring takes any entry ≤ ring (the allocator restarts at 0 when it is empty, as `_Ring` does).
     No other cycle turned up in 724 four-DQ runs with raw slots, inline rows, scale reads across 2 KiB blocks and as few as 8
     credits.
   - **As a formal property.** It is one Phase 3 can prove, but not as written in §2.5 and PLAN. Their properties ("every
     allocated entry needs only tags", "no data read waits for another entry's scales") are about entries, not the turns. It
     needs two safety properties and one bounded-liveness property under an environment that returns every granule within R
     cycles:
     - (P1) a ring-turn holder that cannot start an entry for a reason other than space releases the turn the next cycle;
     - (P2) the issue turn is held only while the holder's next request lacks credits or pace;
     - (P3) any DQ with a ready read issues within a bound in R, the quantum and the DQ count.
     All three fit SymbiYosys k-induction on a reduced configuration (2-4 DQs, 8 credits, a ring of 16 units).
   - **Fairness.** 1-credit / 8-credit mixes split credits evenly (28 + 3 against 56). Quantum 8 is right (probe 4). The ring
     quantum is right in bytes for NVFP4 against KV, but it splits converting DQs of different formats by scale bytes (17/66,
     X-10). The turns cost a single stream nothing. Holding the issue turn for pace throttles a plain neighbour when the pace
     binds (X-8). Write packets are not placed in the turns at all (X-11).
2. **The pace across formats (Y-2).** The weighted count and the cap reproduce at 4, 8, 16, 24 and 32 KiB (probe 2). NVFP4 / MXFP4 /
   MXFP8 in different DQs share by ring bytes, not by the pace (probe 4). The MXFP8 per-row gather loses 0.29 → 0.22 rows/cycle at
   845, as stated. A weight by format is the right key for multi-granule entries, whose pace bounds scale bytes in the ring.
   For one-granule entries the pace bounds the queue, which the flat count measures, so the format key is wrong there (X-14).
   No surveyed model has an MX row gather on a critical path.
3. **Partial responses (Y-5).** Consistent across noc.md §2, hbm.md H-2, microarch.md §2.5 and `isa.yaml` (probe 8). Two gaps
   (X-12), and the 2-flit cost of a sub-granule response (X-9).
4. **`isa.staging_ring` changes.** The new defaults reproduce the §2.5 tables within 2% (probes 1-3). The `bw_dq` window is
   right while every DQ outlives the 25% mark and wrong otherwise (probe 12, X-15). The Z-1 pinning hides no regression, and
   "shared" deadlocks under the new arbitration at every ring size for fold tiles (probe 7).
5. **Y-4.** The third model sides with the fourth verifier: 133.5 against 132 and 140 at 845, and +8.6 against +9 and +2 from a
   24 KiB ring. In-order reclaim explains about half of the fix session's gap: freeing out of order gives 137.6 and +5 (probe
   10). Its ring was "count-based", and the spec's ring reclaims in order. The other half (137.6 against 140) is probably address
   placement, not shown. This is no Phase 7 result: none of the three models has bank timing, FR-FCFS or the mesh, and probe 9
   adds a mesh limit that none of them models.
6. **The epoch bit (Y-10).** Closes the single-restart case. Gaps in X-13: two back-to-back restarts alias, `RD_RSP` and `WR_ACK`
   carry no epoch, and steps sharing a die are not covered by "the step". `NI_EPOCH` timing against in-flight requests is
   sound: it is written only while the step is stopped and its DMA idle, so no request of the step is in flight. A write while
   requests were in flight would only turn their `LINK` `ERR`s into `TIMEOUT`s, since the timeout stays as the backstop.
7. **Engram as a separate array (Y-7).** The bytes hold: 48 x 288 B = 13,824 B of HBM reads, 12.7 KB of payload, 288 B for an
   inline row at 8 B alignment too (`test_engram_separate_array_gather`). The sibling path is two plain gathers (256 B rows, 8 B
   scale rows) and two pushes with `rsem`. The requester converts SPM to SPM with `scale_reg` on the staged scales, and one
   semaphore can count both pushes (256 n + 8 n bytes). On C2C an 8 B scale row costs a 2-flit response, the same 4 flits per row
   as an inline 264 B row, and the traffic is about 3 MB per step, so it does not matter. `SCALE_INLINE` now serves no surveyed
   model but still costs the arbiter a second allocation trigger and the ring its 272 B unit (X-16).

## Verdicts on Y-1 to Y-10

| # | Verdict | Notes |
|---|---|---|
| Y-1 | resolved | The entry rule and the 2 L + T_entry formula hold. Own model at 845 with the rule: entries of 8 / 16 / 64 / 128 granules give 151 / 149 / 138 / 124 alone. The rule costs a single stream nothing next to a plain DQ beyond the even split (probe 4). No new scenario. |
| Y-2 | resolved, gap | MXFP4 / MXFP8 at full rate to 1,200, NVFP4 unchanged, and the cap right at 4-32 KiB, own model and reference. New: holding the issue turn for pace throttles plain DQs when the pace binds (X-8). The format key is wrong for one-granule entries, at no cost to surveyed models (X-14). |
| Y-3 | resolved, gap | Multi-credit and one-credit DQs no longer starve each other (28/56, 18-20/18-20/39, 37/37, own and reference). New: the ring turn has no early pass, which deadlocks with a semaphore wait or a stream end inside a turn (X-7, M). Ring bytes split converting DQs by format (X-10). Write packets have no place in the turns (X-11). Not a formal property yet. |
| Y-4 | resolved, gap | The table note, PLAN Phase 7 and hbm.md H-9 are as described. The third model sides with the fourth verifier (probe 10). New: the RSP flits of sub-granule responses load the HBM leaf links to 1.21 when a stack's 8 cores gather (X-9). |
| Y-5 | resolved, gap | The failure scenario (payload packed from the first byte; whole granules read) is closed in noc.md §2, hbm.md H-2 and the reference. Gaps: a request with more than one SPM destination run, and a fold-tile granule that feeds two raw slots and the converter (X-12). |
| Y-6 | resolved | `test_staging_ring_pace_below_one_request` and my model agree: a 1 KiB limit completes one read at a time, and the counter reaches 2^17. No new case. |
| Y-7 | resolved, gap | The separate-array gather is legal, non-split and about 2.2-3x the inline rate. Bytes and the sibling path check out. Whether `SCALE_INLINE` stays is an open choice (X-16). |
| Y-8 | resolved | With the entry as credits expected, the wait before writing use u reads the entry before adding n_u, which covers uses up to u - 2. The drain target after the last write covers all of them, and the final credit's +1 enters the adder's entry and its first wait. A per-use n_u is known to the producer, which is the one that chose the destinations. Argued, not run. |
| Y-9 | resolved | The exception now needs `SIGNAL`s from one DQ, and other single-adder flags fall back to the general rule. Matches d2d_c2c.md L-6 and the PLAN test programs. |
| Y-10 | resolved, gap | A stale `ERR` of the previous epoch is dropped whatever its age. "However long it stays on the mesh" holds for one restart, not two in a row, and responses carry no epoch (X-13). |

**Earlier verdicts, re-checked after these changes:**
- W-1 (every allocated entry needs only tags): holds. An allocated entry's reads need credits, pace and the issue turn, all freed
  by responses or rotating. A second scale read across a 2 KiB block needs no ring space. The ring turn is needed only to start
  an entry.
- V-1 (no response waits for another): holds. Per-granule responses of a cut granule each convert from their own entry. In my
  model no non-split data response arrived before its entry's scales, in any mode.
- V-3 / X-6 rule 2 (a DQ waiting on a semaphore holds nothing): **does not hold under the text as written.** A DQ that waits on a
  semaphore inside its ring turn holds the ring turn (X-7). With the pass on idle it holds again, and microarch.md §2.5's "so a
  DQ whose head waits on a semaphore holds nothing another DQ needs" becomes true.
- Z-1 (cut reads): holds. Cut never deadlocked, under either arbitration, in the reference or my model. Shared still does
  (probe 7).
- Z-5 / Z-6 with Y-8's base table: hold (Y-8 row).
- D1: not reopened. The 1.60x is converter and SPM-port capacity, which neither the pace weighting nor the turns change. X-9 is
  mesh capacity, a separate limit.

## Verification 5 findings

| # | Sev | From | Finding | Failure scenario | Suggested resolution |
|---|---|---|---|---|---|
| X-7 | M | Y-3, V-3, X-6 | **The ring turn has no early pass, so a holder with nothing to allocate deadlocks the ring.** §2.5: "only the DQ at its head allocates, keeping that turn for 256 B of ring". The issue turn "passes early when the DQ has nothing ready"; the ring turn does not. `isa.staging_ring` passes it at the end of a DQ's stream (`_ring_release`), but it has no semaphores, so it never sees a DQ whose next instruction waits. | 2 KiB ring, L = 500 (also 300 / 845, and a 4 KiB ring): DQ0 streams weights; DQ1 gathers 37 KV rows and then waits on flag F; DQ2 gathers 200 KV rows and sets F when they complete. DQ1 heads the ring queue, allocates 160 B and waits on F; DQ2 is queued behind it and never allocates. The core hangs from cycle 3,628 with the ring empty and every credit free. In 400 random 4-DQ programs: 332 deadlocks under the text, 119 under the reference's end-of-stream pass, 0 with the pass on idle. PLAN's test "a DQ blocked on a semaphore while another DQ streams" is this case. | Write: "The ring turn passes when its holder cannot start an entry for any reason other than ring space: no started transfer has an entry left, its next instruction is not at the head or waits on a semaphore, or its next read is plain." Add semaphore waits (or idle gaps) between transfers to `isa.staging_ring`, with the probe-6 case and a random sweep as tests. Add the P1-P3 formal properties (check-first item 1) to PLAN Phase 3. |
| X-8 | M | Y-2, Y-3 | **Holding the issue turn for pace throttles DQs the pace does not limit.** "While its next request waits for credits or pace it holds the turn: no other DQ's read overtakes it", while "plain reads are limited by tags only". When the pace binds, a plain DQ gets one turn per paced request. The pace binds at a `DMA_PACE` the runtime lowers, at the cap of a ring below 16 KiB, and above the round trip the ring covers. | `DMA_PACE` 16 KiB at L = 845. NVFP4 weights + 2 KiB plain reads: reference 14/38 B/cycle (52 of 87), own 17/17, against 9/77 and 10/76 under `greedy`. KV gather + plain: reference 13/39, own 17/34. At L = 1,200 the reference gives 11/30. The plain DQ, for example the next layer's activations, loses half or more of its rate to a stream that was throttled on purpose. | Hold the turn for credits; for pace, hold it only against other converting DQs: a request that waits only for pace lets a DQ whose next read is plain issue meanwhile. Own model: 10/76 and 11/75 at 16 KiB, every default case unchanged, every run complete. Add a 16 KiB-pace case to `test_staging_ring_tag_arbitration`. |
| X-9 | M | Y-5, Y-4 | **Sub-granule responses cost full 2-flit packets, and a stack's gathering cores overload its leaf links.** `RD_RSP` is always 2 flits (noc.md §2, hbm.md H-2), now one per granule touched. A 32 B KV scale read and an 8 B Engram scale each cost as much link as 256 B, and every one-granule entry adds a header-only REQ flit. noc.md §5 sizes the links for 2 KiB plain reads only. | DeepSeek decode attention gathers the top-512 KV rows per query (model_features.md row 2), a 32 B scale read plus a 256 B row each. With the 8 cores of a stack at the 0.85 share, the busiest HBM leaf → mesh link is at 1.21 of capacity (`noc.py` routes, my flit counts). So the gather gets at most about 0.83 of its share (about 128 elements/cycle) before per-PC effects (Y-4: 132-134). | Decide before the `core/ni` and `hbm` response paths: a 1-flit `RD_RSP` when the valid range lies in one 128 B half. It needs no new header field, since both ends know the range from the request and the tag table, and the KV gather drops to 0.91. Or keep 2 flits and add the gather case to noc.md §5 and PLAN Phase 7, marking the §2.5 KV column as mesh-optimistic too. |
| X-10 | L | Y-3 | **The ring quantum in bytes splits converting DQs by format.** 256 B of ring is one NVFP4 entry, two MXFP4 entries or four MXFP8 entries, each one data request. §2.5 says "under the rule, credits are shared evenly", which holds for KV + plain, not for two converting DQs. | NVFP4 + MXFP8 weights in two DQs at 845: 17/66 data B/cycle (own and reference); NVFP4 + MXFP4: 27/54; with a plain DQ: 10/37/37. The total is unchanged (80-85), so this is about sharing. A kernel that consumes an NVFP4 and an MXFP8 stream in lockstep runs at the NVFP4 stream's 17. | Count the ring turn in data granules of the allocated entries (8 granules, one data request) rather than in ring bytes: own model 40/40, 40/40, 28/28/28, with KV and plain cases unchanged. Or state the split in §2.5. |
| X-11 | L | Y-3, V-3 | **Write packets have no place in the credit-fair turns.** Shared resources "are taken per read or write packet, round-robin", but the turn lasts "until it has taken 8 granule credits", and writes take none. Read literally, a DQ that wins with a write keeps the turn until its writes run out. Not modelled. | A MoE dispatch `DMA_COPY` of 1 MiB from SPM to remote SPMs in DQ2 while DQ1 prefetches the next expert's weights: DQ1 issues nothing for at least 4,096 cycles (2.7 µs at the NI's 256 B/cycle). | Count a write packet's granule (one per packet, at most 256 B) toward the 8-credit turn. Do not hold the turn for write burst ids, which acks free. Add a read DQ next to a write DQ to the Phase 3 arbitration test. |
| X-12 | L | Y-5 | **The tag-table entry and the placement rule assume one destination run and one unit kind per request.** §2.5: granule g "lands at the destination + (g x 256 - the first byte's offset)", with one ring entry offset and one element offset per tag. Nothing says that a read request's SPM destination is contiguous, and with fold tiles from byte 64 every granule mixes raw and converted bytes. | (a) Two adjacent 512 B HBM rows (s1 = 512) read into SPM rows padded to s1 = 576 (bank spreading, §2.3): if the DMA merges them into one 1 KiB request, granule 2 lands at dst + 512 instead of dst + 576, silently. (b) A fold-tile granule from byte 64 feeds two raw slots and the converter; an NI built from the entry list has one ring offset to put it at. `test_rd_rsp_layout` covers raw placement and the NVFP4 element 256 only. | State that a read request is cut where its SPM destination run ends (as write packets are). State that raw-slot offsets and unit boundaries come from the ring entry (first unit address, unit size, raw-slot base) and not the tag table. Extend `isa.rd_rsp_layout` or a test with the fold-tile case and a padded destination. |
| X-13 | L | Y-10, W-2 | **The epoch covers one restart and only `ERR`s.** (a) "However long it stays on the mesh" holds for an `ERR` one restart old. After two restarts in a row the epoch is back, so the guarantee is again a time argument (two drains and two retrains). (b) `RD_RSP` and `WR_ACK` carry no epoch, so L-4 (d)'s "no late response of the old step matches a tag of the new one" still rests on the 100 µs timeout. (c) L-4 (c) / (e) restart "the step", but a stale replay-buffer `ERR` can fault a core of another step on the die (W-2). | (a) Link fails in step N, the step restarts (epoch 1) and the link fails again at once; at the second restart (epoch 0) an `ERR` from step N still held behind a stalled SPM port faults the new step. (b) A response delayed past its tag's timeout lands in the SPM of the reused tag's new request. (c) Step B's core faults `LINK` from a stale `ERR` while the CP restarts only step A. | Reword (a). Optionally echo `epoch` in `RD_RSP` and `WR_ACK` (bit 122 is a common header bit, and the target copies it from the request), so the core drops late responses of the old epoch too. Decide before `core/ni` RTL, as for Y-10. State that a link-down event restarts every step with a `LINK` or `TIMEOUT` fault, each with its own conditions 1-3 and `NI_EPOCH` toggle. |
| X-14 | L | Y-2 | **The format weight is the wrong key for one-granule entries.** For multi-granule entries the pace bounds scale bytes in the ring, which the format weight tracks. For one-granule entries (row gathers) it bounds the queue, which only the flat byte count tracks. | A per-row MXFP4 gather of 256 B rows at L = 845: 110 elements/cycle by format, 151 flat (own model); MXFP8 55 against 75; Engram 0.22 against 0.29 rows/cycle. No surveyed model has an MX row gather on a critical path: KV gathers are NVFP4 and Engram is hidden. | Optional: weight by entry shape. An entry whose data is one granule counts in full, longer ones by format (one more term in the shift select). Own model: the gathers get the flat rates, MX weights keep 164 / 84 to 1,200, NVFP4 is unchanged. Otherwise note the case in §2.5. |
| X-15 | L | Y-3, Y-10 | **Text and reference slips.** (a) microarch.md §2.5 "Reference" bullet: "Its defaults are the rule: ... turn-holding (`arb="hold"`)". The default is `"fair"`, and `"hold"` is the rejected rule that starves the KV gather (5/81). (b) `bw_dq` falls back to the 25-75% window when a DQ ends before the 25% mark and then reports it as 0 (probe 12). (c) The `isa.yaml` `NI_EPOCH` and `epoch` comments cite L-4 (e) for the epoch, which is (f). (d) isa.md §5 repeats "ring space in turns of 256 B" without the pass (X-7). | (a) An RTL engineer who takes the bullet at its word builds the rejected arbiter. (b) A future arbitration test with a short stream asserts on a 0 that is a window artifact, the same class of error as the fix session's first Y-3 measurement. | Fix (a), (c) and (d) with the X-7 text. For `bw_dq`, measure each DQ from the latest DQ's first data to the earliest DQ's last data, and return None when there is no common window. |
| X-16 | L | Y-7 | **`SCALE_INLINE` serves no surveyed model now.** It costs the arbiter a second allocation trigger (an entry allocated at its first data read, not at a scale read), the ring its 272 B largest unit, a TD flag, `isa.inline_addr` and five `ILLEGAL_FIELD` conditions, all to be verified in Phase 3. | No failure: it is cost without a user. Every Phase 3 property and test on the arbiter must cover the inline trigger, which no kernel will exercise. | The user's choice. Keep it, or mark the flag reserved (`ILLEGAL_FIELD`) in v1, keep the reference code and tests, and drop the inline path from the v1 `core/dma` RTL scope. |

## Before Phase 3, after verification 5

| Block | Still open |
|---|---|
| `core/dma` | Read-issue arbiter: X-7 (the ring turn's pass on idle), X-8 (no pace hold against plain DQs), X-11 (writes in the turns); optional X-10 (ring quantum in granules) and X-14 (pace key by entry shape); the P1-P3 formal properties. Text: X-12, X-15 (a), (d). X-16 if the inline path is dropped. Everything else as after the verification 4 fixes |
| `core/ni` | X-9 (1-flit `RD_RSP`, or accept 2 flits) before the response path; X-13 (b) if the epoch is echoed in responses (header); X-12 text |
| `core/seq` | none |
| `hbm` | X-9 (the `RD_RSP` flit count in H-2); X-2 (a), (c)-(e) on the H-9 checklist; per-initiator fairness (Y-4) |
| `common`, `arith` | none |
| Phase 4 `link`, `noc` | X-13 (two restarts in a row; the bridge's epoch copy as before); the W-2, Z-7 and Y-10 tests as listed in PLAN |
| compiler / runtime | as after the verification 4 fixes, plus X-13 (c) (restart every step with a `LINK` or `TIMEOUT` fault) and the X-16 choice |
| Phase 7 | Y-4 and X-9: 8 cores on per-PC queues with the KV gather's RSP flits on the leaf links; the pace coupling at reduced rings (X-8) |

**Can the `core/dma` read-issue arbiter start Phase 3 RTL?** **Not yet.**
- X-7 is a deadlock in the text as written, and the reference's partial fix (a pass at the end of the stream) still deadlocks
  119 of 400 random programs with semaphores.
- X-8 and X-11 change what the turn is held for.
- Each is one or two sentences in §2.5 and a small change to `isa.staging_ring` (semaphore gaps and the pass on idle, the pace
  rule, writes counted as credits), with the probe-6 cases as tests.
- Once those are in and the tests pass, the arbiter can start without another full verification pass. The P1-P3 properties go
  into PLAN Phase 3 at the same time.
- The rest of `core/dma` is not affected and can proceed.
- `core/ni` should settle X-9 (and X-13 (b), if taken) before its response path is written, because both change the response
  packet.

**Where an executable check is needed beyond these probes:**
- X-7, X-8, X-11, now: `isa.staging_ring` with semaphore waits between transfers, a 16 KiB-pace mixed case and a write DQ. The
  probe-6 targeted case and a random sweep become tests.
- X-7 in Phase 3: the P1-P3 formal properties on the arbiter.
- X-9: noc.md §5 with gather traffic, then Phase 7 with a mesh model, 8 cores per stack and per-PC queues.
- X-12: the Phase 3 `core/ni` test with a fold-tile request from byte 64 and a padded SPM destination.
- X-13: the Phase 4 late-`ERR` test with two restarts in a row, and a late `RD_RSP` past its timeout.
- Y-8 and Y-9: the golden-model semaphore check (still argued, not run).

**Not checked:**
- the JESD238 items (still unavailable);
- the generated SV and C++ beyond the lint in `make isa`;
- my per-PC model's fidelity: FIFO per PC, no bank or row timing, no FR-FCFS, no refresh, no mesh, 8 symmetric cores, my own
  address placement;
- why my pace coupling (probe 5) is stronger than the reference's;
- write packets, which neither model has (X-11 is argued);
- the mesh beyond the leaf-link estimate of probe 9: no router or VC model, and REQ flits of plain reads at the 1/16 rate;
- SPM port arbitration against the DMA, and the SPM load of raw slots;
- the published Engram checkpoint (no network; repo notes only);
- the golden-model checks, which do not exist yet;
- `make numerics`, `make hbm`, `make links`: not rerun, since their inputs did not change.

# Resolutions (verification 5 fixes, 2026-11-01)

**Who:** a sixth fix session (Opus 5.5). It is not the author, the reviewer, any earlier fix session or any verifier. Branch
`spec-verification5-fixes` from ed2ea70, one commit per group, `make pytest` green before each. The user chose X-9, X-13 (b) and
X-16, and took the optional X-10 and X-14 after asking for a recommendation. The tables above are unchanged.

**Method.** I re-derived every failure scenario before fixing it, in `isa.staging_ring` extended for the purpose (below), in
`noc.py` and in `isa.rd_rsp_layout`. Per-DQ rates are data bytes per cycle from the latest DQ's 5% mark to the earliest DQ's
95% mark of its own data, the window in which every DQ still receives data. Each suggested resolution was checked against
the specs and then taken, changed or rejected; each row says which. X-13 is argued, not run: the Phase 4 link tests do not
exist yet. Probe scripts and logs (`logs/x7_sweep400*.log`) are not committed.

| Group | Commit | Findings |
|---|---|---|
| 1 | b8e6596 | X-7, X-8, X-11, X-10 and X-14 (taken by the user), X-15 (a), (b), (d): the `core/dma` read-issue arbiter; P1-P3 in PLAN Phase 3 |
| 2 | a2eea9a | X-9 (user's choice), X-12, X-13 (a), (b) (user's choice): `core/ni`, `hbm`, NoC |
| 3 | 7b0f910 | X-13 (c), X-16 (user's choice), X-15 (c): runtime and ISA text |

| # | Status | Group | What was done |
|---|---|---|---|
| X-7 | fixed | 1 | **Re-derived** with semaphore waits in `isa.staging_ring`: 2 KiB ring, L = 500, DQ0 NVFP4 weights, DQ1 37 KV rows, a wait on F, 37 more, DQ2 200 KV rows, then a `SIGNAL` of F. Under the text (`ring_pass="quantum"`) and the reference's end-of-stream pass (`"end"`) the core hangs from cycle **3,649** (`hbm="server"`, 72 KiB; 3,114 paced), against the verifier's 3,628. It also hangs at L = 300 / 845 and with a 4 KiB ring. At 16 KiB the reference hung too, because it made every allocation take the turn. With the turn taken only when a DQ waits, which is what the text says and what the verifier's model did, 16 KiB completes under the end-of-stream pass and still hangs under the quantum rule. **Random sweep** (my own generator: 400 four-DQ programs, 1-5 transfers per DQ of NVFP4 / MXFP4 / MXFP8 weights, KV gathers, Engram rows, inline rows, fold tiles from byte 64 and plain reads, cross-DQ waits only on earlier transfers' `SIGNAL`s, front-end gaps, 2-16 KiB rings, L = 50-845, 8-512 credits, `DMA_PACE` off / 1 KiB / 8 KiB / 72 KiB, paced or server): **182 / 59 / 0** deadlocks under the quantum rule / end-of-stream pass / pass on idle, against the verifier's 332 / 119 / 0; with write transfers added (second seed) 169 / 46 / 0. Same direction; the counts differ with the programs and with turns taken only under contention. **Changed the wording:** the turn passes when its holder cannot start an entry for a reason other than ring space, **credits, pace or the issue turn**: no entry left, a wait, `SIGNAL` or gap at its head, or a plain read or write next. A queued DQ that becomes idle leaves the queue. The holder keeps the turn through credit and pace waits, which responses end; "any reason other than ring space" would also pass it on every credit wait, which liveness does not need and fairness would lose. A DQ that allocates while none waits takes no turn (`ring_take="contended"`; every share in the §2.5 table is identical either way). P1-P3 are written into microarch.md §2.5 and PLAN Phase 3, P2 including X-8's plain issue. Tests: `test_staging_ring_ring_turn_passes_when_idle` (the targeted case, its 3,628 ± 2%, a stream that ends 160 B into its turn, and 24 seeded random programs). microarch.md §2.5, isa.md §3 / §5, `isa.yaml` `DMA_STAGE`. |
| X-8 | fixed | 1 | **Re-derived.** `DMA_PACE` 16 KiB at L = 845, data B/cycle: NVFP4 weights + plain 2 KiB reads **16.5 / 16.5** with the turn held for the pace against every DQ (the text), KV gather + plain **17.0 / 34.0**; L = 1,200: 11.7 / 11.7. **Which coupling is right: the verifier's own model's 17 / 17.** The reference's 14 / 38 came from a defect in it. A plain DQ that started a plain read called `_allocated(d, 0)` and took the ring turn with 0 bytes. While the converting DQ held the issue turn, the plain DQ's release was never evaluated, so the converting DQ could not read scales ahead, had nothing ready more often, and passed the issue turn. With that one line patched in the committed reference, it gives 16.6 / 16.6 too. Plain reads take no ring turn now. **Took** the proposal: while the holder's request has its credits and waits only for the pace, a DQ whose next request is plain (a plain read or a write packet) issues it, outside the turn. Result: **9.6 / 76.2** and **10.7 / 74.9**, as "greedy" (9.5 / 76.3, 10.6 / 75.1; the verifier's own variant: 10 / 76, 11 / 75). Every default share is identical (41 / 41, 28 / 56), and every random program completes. A 2 KiB ring gives 81 for the plain DQ either way. **Cost found:** the paced stream itself drops from about 17 to about 10 B/cycle, because the plain DQ's backlog lengthens its round trip; the two together go from 33 to 86. The pace is a deliberate throttle, and the plain DQ is limited by tags only, as the text says. `test_staging_ring_pace_hold_and_formats`. |
| X-9 | fixed (user's choice) | 2 | **Re-derived** from `noc.py` routes (new `noc.gather_read`, `noc.rsp_flits`): the 8 cores of a stack on the paged KV gather at the 0.85 share load the busiest HBM leaf → mesh link to **1.21** (1.42 at the full share), against 0.68 for 2 KiB reads at the same rate, as the verifier had. With 1-flit responses for a range inside one 128 B half it is **0.91** (1.07 at the full share); Engram rows 1.32 → 0.99. The REQ direction, at two header flits per row, is at 0.60. **Options costed:** (a) 1 flit: no header field, since `flit_t` has head and tail and `WR` is already 1-2 flits, and both ends know the half from the request's range; a 128 B half-select in the target response formatters and in `core/ni`; (b) keep 2 flits and accept about 0.83 of the share (about 128 elements/cycle) on the mesh. **The user took (a).** noc.md §2 (table, partial granules) and §5 (three rows, a gather bullet), hbm.md H-2, microarch.md §2.5 (read requests, table note) and §2.6, `isa.yaml` `gidx`, `isa.rd_rsp_layout` (`flits`, `part`), PLAN Phase 3 `core/ni` and Phase 7. `test_gather_leaf_load`, `test_rd_rsp_layout` (1- and 2-flit reassembly). |
| X-10 | fixed (taken by the user) | 1 | **Re-derived** at L = 845, data B/cycle, ring quantum of 256 B / of 8 data granules: NVFP4 + MXFP4 27 / 54 → 40 / 40; NVFP4 + MXFP8 **17 / 66 → 40 / 40**; NVFP4 + MXFP8 + plain 9 / 38 / 38 → 28 / 28 / 28; MXFP4 + MXFP8 + plain 18 / 33 / 33 → 28 / 28 / 28. The verifier had 27 / 54, 17 / 66, 10 / 37 / 37, 19 / 33 / 33 and 40 / 40, 40 / 40, 28 / 28 / 28. KV + plain 28 / 56, two KV + plain 19 / 19 / 40, weights + KV 37 / 37 and weights + plain 41 / 41 are identical under both. **Took** the granule quantum: the turn counter adds an entry's data granules (at most 8, known when the entry is formed) instead of its ring bytes. The §2.5 table has two new columns and a row for the byte quantum. |
| X-11 | fixed | 1 | **Re-derived** with write transfers in `isa.staging_ring` (256 B packets, no tag, credit, pace or ring, acked a round trip later): a 1 MiB write next to NVFP4 weights or plain reads at L = 845. With a write taking no credit toward the turn, the reader receives nothing while the writes run (no common window) and finishes 3,896 / 4,074 cycles late, about the 4,096 packets, as the verifier argued. **Took** the proposal: a write packet counts one credit toward the 8 of a turn, and the turn is never held for burst ids. The reader then keeps about its rate alone: weights 73 B/cycle of data against 77, plain 87 / 86, writes 238 next to the weights. A KV gather gets 63 next to the writes, bound by the one request per cycle the model issues (4 rows per 16 cycles). **The arbitration test has writes** (`test_staging_ring_writes_in_turns`), and so does the second random sweep. PLAN Phase 3 adds a write DQ next to a read DQ. |
| X-12 | fixed | 2 | **Re-derived** by construction in `isa.rd_rsp_layout`. Contiguous MXFP4 fold tiles from byte 64: granule 1 carries the 64 B tail of tile 1 (raw slot 0), whole tile 2 and the 64 B head of tile 3 (raw slot 1), so one ring offset per tag places only one of them. 512 B rows into SPM rows padded to 576 B: one merged 1 KiB request lands granule 2 at dst + 512. **Took (a):** a read request is cut where its SPM destination run ends (for a converting read, its output run) and at the 2 KiB block; new reference `isa.read_requests`. **Changed (b):** the verifier proposed that raw-slot offsets come from the ring entry, not the tag table. In the spec they come from the entry's geometry, but the tag table carries what a response needs: the unit size, the first byte's offset in its unit and that unit's raw-slot index, next to the ring offsets of the entry's scales and raw slots. Responses arrive in any order, and the entries in flight can outnumber the tags (up to 1,024 of 16 B at 16 KiB), so a per-entry descriptor table would be larger than about 15 more bits per tag. The tag-table estimate becomes about 95 bits, 6 KiB; it is not a frozen number. The NI cuts the valid range at unit boundaries: a unit inside the granule converts, and a piece of a split unit goes to raw base + slot x pitch + offset, slot = the entry's split units before it. For power-of-two units of at most 256 B, either every granule boundary splits a unit or none does. `rd_rsp_layout(..., unit=)` gives the pieces; `test_rd_rsp_pieces_and_destination_runs` covers the fold-tile request, 150 random entries of 128 / 256 / 264 B units in shuffled response order, and the padded rows. noc.md §2, microarch.md §2.5, PLAN Phase 3 `core/ni`. |
| X-13 | fixed ((b) the user's choice) | 2, 3 | **Re-derived** by argument. (a) Two restarts in a row bring the bit back, so a packet from two restarts ago is kept out only by both restarts' conditions 1-3 and retrains. **Took:** reworded in d2d_c2c.md L-4 (f) and isa.md §8, with a Phase 4 test of a second failure right after a restart. Not taken: a 2-bit epoch, which only moves the limit. (b) **The user took the echo.** `epoch` (bit 122) is now also on `RD_RSP` and `WR_ACK`; every target copies it from the request, and a core drops a response or ack of the other epoch without freeing anything. **Added**, not in the finding: write-ack coalescing is per (initiator, burst id, epoch). Otherwise packets of the old step could count toward a reused burst id of the new one, and the ack's epoch would be ill-defined. `make isa` regenerated `isa_defs.py` and `isa_ref.md`; the header test now packs `epoch` on all six response and request types, and `MC_WR` still rejects it. noc.md §2 (table, rule 1), hbm.md H-2, d2d_c2c.md L-4 (b), (d), (f), isa.md §8, microarch.md §2.6, PLAN Phase 3 `core/ni` and Phase 4. (c) **Took:** every step with a core faulted `LINK` or `TIMEOUT` is restarted, each on its own cores with its own conditions 1-3 and `NI_EPOCH` toggle, and a step with no faulted core keeps running. d2d_c2c.md L-4 (c), L-11, isa.md §8, noc.md §7, PLAN Phase 4. |
| X-14 | fixed (taken by the user) | 1 | **Re-derived** at L = 500 / 845 / 1,000 / 1,200, by format / by entry shape: per-row MXFP4 gather 137 / 110 / 103 / 96 → 164 / **154** / 130 / 108 elements/cycle; Engram MXFP8 rows 0.269 / **0.216** / 0.202 / 0.187 → 0.33 / **0.301** / 0.254 / 0.211 rows/cycle. The verifier had 110 / 151 and 0.218 / 0.291 at 845, and 0.260 / 0.302 at 500. MX weights stay at 164 / 84 to 1,200 cycles under both, and NVFP4 weights and the NVFP4 KV gather are unchanged. **Took** it: the reads of an entry whose data is one granule count in full (`pace="shape"`, now the default). The Engram rates in microarch.md §2.5 and hyper_connections.md §4 are now 0.33 / 0.30. `isa.yaml` `DMA_PACE`, isa.md §5. |
| X-15 | fixed | 1, 3 | (a) The §2.5 "Reference" bullet lists the real defaults. (b) **Re-derived:** `[kv, plain[:60]]` gave `bw_dq` 77 / 0. **Changed:** the window runs from the latest DQ's 5% mark to the earliest DQ's 95% mark of its own data (the verifier's own measurement), rather than from first to last data, to keep ramp and tail out; it is `None` when empty, as here. A DQ that moves no data gets `None`. `test_staging_ring_bw_dq_window`. Every arbitration assert still holds; the rule's shares moved by at most 1 B/cycle (18 / 18 / 39 → 19 / 19 / 40), and the rejected "hold" row from 5 / 81 to 1 / 85. (c) The `NI_EPOCH` comment cites L-4 (f), as does the `epoch` field comment (group 2). (d) isa.md §5 states the ring turn's quantum, contention and pass (group 1). |
| X-16 | fixed (user's choice) | 3 | **Costed:** keeping it leaves a second allocation trigger, the inline address path and five `ILLEGAL_FIELD` checks in the Phase 3 scope with no kernel to exercise them; reserving it costs a later repacked table nothing in the encoding. **The user reserved it in v1.** A TD with `SCALE_INLINE` faults `ILLEGAL_FIELD`. The flag bit, `isa.inline_addr` and the reference's inline entries stay for a later version (`isa.block_addr(..., v1=False)`). In v1 every ring entry has a scale read, so allocation has one trigger. The split MXFP8 fold tile (264 B in 272 B of ring) keeps the largest-unit bound unchanged. **Also fixed:** isa.md §6 and hyper_connections.md §3 still said the n-gram gather used `SCALE_INLINE`, stale since Y-7. `isa.yaml` (`SCALE_INLINE`, MXFP8, `DMA_COPY` and `DMA_GATHER` text), memmap.md §6, microarch.md §2.5, isa.md §4 / §5 / §6, hyper_connections.md, PLAN Phase 3; `test_engram_inline_gather` checks the v1 fault. |

**Proposals not taken as written** (the row says why):
- X-7: the pass excludes credits, pace and the issue turn from "any reason other than ring space", and the turn is taken only under contention.
- X-12 (b): the tag table carries the unit size, offset and raw-slot index (copied from the entry at issue), not a lookup in a per-entry descriptor.
- X-13: write-ack coalescing per epoch added; no 2-bit epoch.
- X-15 (b): the window is the 5%-95% marks, not first-to-last data.
- X-16: reserved rather than removed. The encoding keeps the bit, and the definition and reference stay.

**`isa.staging_ring` changes** (the reference the `core/dma` RTL is compared against):
- Stream items: `("write", bytes)`, `("wait", s, n)`, `("signal", s, n)`, `("gap", c)` and `("fmt", name)`. Write packets complete a round trip after issue; waits block later entries but not started ones; a `SIGNAL` fires once every earlier entry of its DQ is complete.
- New parameters, with the rule as default: `ring_pass="idle"` (also `"end"`, `"quantum"`), `ring_take="contended"` (also `"always"`), `ring_q="granules"` (also `"bytes"`), `pace_hold="converting"` (also `"all"`), `write_turn=True`, `pace="shape"` (also `"format"`, `"flat"`).
- Plain entries take no ring turn. This is the defect that weakened the X-8 coupling in the reference before (above).
- `bw_dq`: the 5%-95% window, `None` when empty. New outputs: `nreq` (scale, data and write requests per DQ) and `sems`.
- `_ring_reads` takes entries with and without addresses in one stream, and writes.
- Every test before this session passes unchanged under the new defaults. The §2.5 shares move by at most 1 B/cycle.
- Also new: `isa.rd_rsp_layout` gains `flits`, `part` and `unit=` (pieces), `isa.read_requests` is new, and `isa.block_addr` gains `v1`.

**Regenerated:**
- `make isa`, groups 2 and 3: `isa_defs.py` and `isa_ref.md` (the `epoch` types, the `SCALE_INLINE` text). In group 1 only comments that the generators do not emit changed. `isa_pkg.sv` and `isa.h` are unchanged. The lint of `isa_pkg.sv` is clean, and `git status` was clean after a rerun.
- `make numerics`, `make hbm`, `make links`, `make sweep`, `make models`: not rerun. Under `sw` and `sim` only `sim/analytic/noc.py` changed (new functions and scenarios), and none of those targets imports it or `isa` (`git diff --stat ed2ea70 HEAD -- sw sim isa`).

**Frozen numbers:** none changed. The ring stays 16 KiB of SPM, `DMA_PACE` resets to 72 KiB, and the tag table keeps 512 entries. The header uses no new bit (`epoch`'s type list grows). The tag-table estimate grows from about 80 to about 95 bits per entry (5 to 6 KiB), a microarch.md estimate that arch.md does not freeze. D1 is unchanged. arch.md has a change-log row.

**Tests:** `make pytest` 230 passed (224 before).
- `test_isa.py` 49 → 54. New: pace hold and formats (X-8, X-10, X-14), writes in turns (X-11), the `bw_dq` window (X-15), the ring turn's pass on idle with the targeted case and a seeded random sweep (X-7), and response pieces with destination runs (X-12). Extended: `test_rd_rsp_layout` (1-flit), the header test (`epoch` on responses), the inline gather (v1 fault).
- `test_noc.py` 20 → 21: the gather leaf load (X-9).

## Before Phase 3, after the verification 5 fixes

| Block | Still open |
|---|---|
| `core/dma` | none at the spec level. The read-issue arbiter has X-7 (pass on idle, turns under contention), X-8 (pace hold against converting DQs only), X-10 (granule quantum), X-11 (writes count a credit) and X-14 (pace by entry shape) in the text and the reference, with tests, and P1-P3 in PLAN Phase 3. X-12: requests cut at destination runs. X-16: no inline path in v1 |
| `core/ni` | none at the spec level: 1-flit `RD_RSP` (X-9), the epoch on responses and acks (X-13 (b)), response pieces from the tag table (X-12), with references and tests |
| `core/seq` | none |
| `hbm` | none at the spec level: 1-flit `RD_RSP` and the epoch copy (H-2). X-2 (a), (c)-(e) stay on the H-9 checklist; per-initiator fairness (Y-4) |
| `common`, `arith` | none |
| Phase 4 `link`, `noc` | the W-2, Z-7, Y-10 tests, plus X-13: late responses and acks dropped by epoch, a second failure right after a restart, several steps on one die (PLAN) |
| compiler / runtime | as after the verification 4 fixes, plus X-13 (c) (restart every step with a faulted core, each with its own conditions and epoch toggle) and X-16 (no `SCALE_INLINE` in v1) |
| Phase 7 | Y-4 (8 cores on per-PC queues), and the gather on the mesh with 1-flit responses (0.91 in `noc.gather_read`; X-9) |

**Can the `core/dma` read-issue arbiter start Phase 3 RTL?** **Yes, with P1-P3 as its first deliverable.**
- Verification 5 set the condition: X-7, X-8 and X-11 in the text and the reference, the probe-6 case and a random sweep as tests, and P1-P3 in PLAN. All of that is done, and 800 random programs complete under the rule.
- No one but this session has checked four new details: the pass list's exclusions, turns only under contention, the plain issue outside a pace hold, and the paced stream's own loss. The check-first list below starts with them. None of them is a liveness question that P1-P3 would not catch.

**Can the `core/ni` response path start Phase 3 RTL?** **Yes.** Both choices that change the response packet are settled (X-9: 1-flit; X-13 (b): epoch echoed), and the placement rule of a converting response is defined with a reference (X-12).

**For the next verification pass, check first:**
1. **The X-7 pass as written.** The holder keeps the ring turn through credit, pace and issue-turn waits. Is there a cycle with that (0 in 800 random programs and the targeted cases)? Do P1-P3 as worded in microarch.md §2.5 cover it, and is `ring_take="contended"` (a free allocation takes no turn) fair as contention starts?
2. **The X-8 plain issue.** At a 16 KiB pace the paced stream drops from about 17 to about 10 B/cycle, because the plain DQ's backlog lengthens its round trip. Is that the right trade (total 33 → 86)? Is the claim right that the old reference's 14 / 38 came only from plain entries taking the ring turn?
3. **X-12's tag-table fields.** The unit size, offset and raw-slot index per tag; the slot-index derivation for power-of-two units of at most 256 B; and the 95-bit estimate.
4. **X-9.** The 1-flit half-select against `ni_rsp`'s 4-flit buffer and noc.md §4 (variable-length RSP packets), and the gather's REQ direction (0.60).
5. **X-13.** Coalescing per epoch at the targets, and whether a second failure right after a restart is as rare as the 1-bit epoch assumes.
6. **X-16.** That no v1 path needs an entry without a scale read (the Engram sibling path, E4M3 / BF16 conversions without scales).
7. **The `bw_dq` 5%-95% window** and the §2.5 table rebuilt on it.


# Verification 6 (scoped, 2026-11-02)

**Who:** a sixth verification session (Opus 5.5). It is not the author, any reviewer, any fix session or any earlier
verifier. This is a narrow pass over the seven items of "For the next verification pass, check first" in the resolutions of
the verification 5 fixes, nothing else in depth. No spec, model or test was changed; this section is the only edit (branch
`spec-verification6` from 5de3572). Findings are numbered from U-1.

**Method.** I read CLAUDE.md, Verification 5 and its resolutions, and the text the rows cite: microarch.md §2.5 (credit-fair
turns, ring turns, P1-P3, the pace, read requests, the tag-table entry, pieces of a converting response) and §2.6, noc.md §2,
§4, §5 and §7, hbm.md H-2, d2d_c2c.md L-4, isa.md §3, §5 and §8, memmap.md §6, `isa.yaml` (`noc_header` `epoch` and `gidx`,
`DMA_PACE`, `DMA_STAGE`, `SCALE_INLINE`), PLAN Phase 3 (`core/dma`, `core/ni`) and Phase 4. Then the references and their
tests: `isa.staging_ring`, `isa.rd_rsp_layout`, `isa.read_requests`, `noc.gather_read`, `noc.rsp_flits`, `test_isa.py`,
`test_noc.py`, and `isa/isa.py` at ed2ea70 for item 2.

**Own model.** Before reading `isa.staging_ring` I wrote a cycle model of the arbiter from microarch.md §2.5 alone (scratch,
not committed; no code in common).
- What it has:
  - Per-DQ programs of converting transfers (NVFP4 / MXFP4 / MXFP8 weights, paged NVFP4 KV rows, Engram MXFP8 rows, MXFP4
    fold tiles from byte 64 with raw slots), plain 2 KiB reads, 256 B write packets, semaphore waits, `SIGNAL`s released
    when every earlier entry, read and write of the DQ is complete, and front-end gaps.
  - Entries with HBM byte ranges. Reads are granule pieces cut at 2 KiB blocks, scale reads that cross a 2 KiB block of
    the scale array are two reads of one entry, and data offsets are random multiples of 32 B, so reads touch partial
    granules.
  - A circular ring of 16 B units (contiguous with wrap-skip, in-order reclaim, allocated at the entry's first scale
    read), 512 tags and 8-512 granule credits, and the pace weighted by format and entry shape, capped at 4.5 x the ring,
    with a converting read always allowed when none is in flight.
  - Credit-fair turns: quantum 8, held for credits, held for pace against converting DQs only, a write packet counting
    one. Ring turns: a queue entered when the entry does not fit or another DQ waits, a quantum of 8 data granules, the
    pass on idle, `"contended"` taking. Variants: the pass at the quantum or at the end of the stream; `"always"` taking;
    a 256 B quantum; no ring turn; the plain issue under a pace hold with the holder's credits reserved, or with a cap on
    plain bytes in flight.
  - One request per cycle and greedy read-ahead. One response per granule. A sink of max(elements / 256, bytes / 512)
    cycles per piece. Credits, tags and pace are freed at the sink.
  - HBM `server` (a FIFO at 87 B/cycle, L/2 out and back, 32 B bursts) and `paced` (an issue budget at 87 B/cycle, each
    response L after issue).
- What it shares with the reference: the rule text, the two HBM abstractions, the 87 B/cycle share and the stream shapes
  of the §2.5 table.
- What it does not share: the code; my own random-program generator; byte ranges and partial granules on every stream;
  credits freed at the sink, not on arrival; fold tiles as entries with address-derived raw slots; no `SCALE_INLINE`
  entries (reserved in v1); no `greedy` / `hold` / `shrink` arbitration.
- **Window:** per-DQ rates are data bytes per cycle from the latest DQ's 5% mark to the earliest DQ's 95% mark of its own
  data, the window in which every DQ still receives data (written before I read the reference's `bw_dq`). Single-stream
  rates are elements/cycle over the same window.
- Calibration against the §2.5 tables at L = 845 (own model):
  - NVFP4 weights alone 154.6 / 150.0 / 127.3 elements/cycle at 500 / 845 / 1,000, and the KV gather 154.6 / 153.1 /
    129.7. The §2.5 table has 155 / 151 / 127 and 155 / 154 / 130.
  - The rule's row of the arbitration table: 28/55, 19/19/40, 37/37, 40/40, 28/28/28.

**Executable checks** (under `scripts/memrun`):
- `make pytest`: 230 passed (rc 0, 40.7 s, 79 MB peak).
- Probe scripts with the project venv (largest: three 400-program sweeps, 50 s each, 19 MB peak).
- `make numerics`, `make hbm`, `make links`: not rerun; their inputs did not change (as the resolutions state).
- `make isa`: not rerun; nothing under `isa/` changed here, and `make pytest` includes the staleness test.

## Probes (own model, `isa.staging_ring`, the fix session)

**1. The X-7 targeted case** (2 KiB ring, `hbm="server"`, `DMA_PACE` 72 KiB: DQ0 NVFP4 weights; DQ1 37 KV rows, a wait on F,
37 more; DQ2 200 KV rows, then a `SIGNAL` of F). Last cycle of progress under the pass at the quantum / at the end of the stream;
the pass on idle completes everywhere in both models:

| ring, L | own | `isa.staging_ring` | fix session / Verification 5 |
|---|---|---|---|
| 2 KiB, 500 | **3,641** / 3,641 | 3,649 / 3,649 | 3,649 / 3,628 |
| 2 KiB, 300 / 845 | 2,241 / 6,056 (both rules) | 2,249 / 6,064 | hangs (no cycle given) |
| 4 KiB, 300 / 500 / 845 | 1,648 / 2,648 / 4,373 | 1,685 / 2,685 / 4,410 | hangs |
| 16 KiB, 300 / 500 / 845 | the quantum hangs at 5,060 / 5,460 / 6,500; the end-of-stream pass completes | 5,404 / 5,804 / 6,849; completes | the same pattern |
| stream ends 160 B into its turn (no semaphore), 2 / 4 KiB | the quantum hangs; the end-of-stream pass and the pass on idle complete | the same (test) | the same |

`hbm="paced"` gives the same cycles to within one.

**2. Random four-DQ programs with cross-DQ semaphores** (own generator).
- Setup: 1-5 transfers per DQ in one global order; a wait names a `SIGNAL` of an earlier transfer of another DQ, so each
  program is deadlock-free by itself. Gaps of 1-1,000 cycles. Rings of 2-16 KiB, L = 50-845, 8 / 9 / 16 / 24 / 64 / 512
  credits, `DMA_PACE` off / 1 / 8 / 16 / 72 KiB, paced or server.
- Deadlocks under the pass at the quantum / at the end of the stream / on idle:

| sweep | own | fix session | Verification 5 |
|---|---|---|---|
| 400 programs, no writes | 261 / 126 / **0** | 182 / 59 / 0 | 332 / 119 / 0 |
| 400 programs with write transfers | 237 / 89 / **0** | 169 / 46 / 0 | – |
| 400 with writes, `ring_take="always"` | – / – / **0** | – | – |

In the rule's runs the issue turn was held (for credits or pace) while a different DQ headed the ring queue for 271,700 cycles
(no writes) and 164,120 cycles (writes), so the case the item asks about occurs often.

**3. A targeted grid against the rule** (four DQs: MXFP4 fold tiles from byte 64 then NVFP4 weights; NVFP4 weights with scale
reads across a 2 KiB block, a wait, KV rows; plain reads, writes and a wait; KV rows, MXFP8 weights and writes; gaps and two
cross-DQ semaphores). Rings 2 / 4 KiB, 8 / 9 / 16 / 24 credits, `DMA_PACE` off / 1 / 4 / 72 KiB, L = 50 / 300 / 845, paced and
server: 192 runs, **0 deadlocks**. In 185,574 of the 6.8 M cycles the issue turn was held while another DQ headed the ring queue.

**4. `ring_take` when contention starts** (own model, L = 845). One DQ streams for 5,000 cycles, then a second starts. Pairs:
KV → NVFP4, NVFP4 → KV, Engram → NVFP4, NVFP4 → MXFP8 and MXFP8 → NVFP4, at 2, 4 and 16 KiB rings.
- `"contended"` and `"always"` give identical shares (for example 37.2 / 37.2 at 16 KiB and 4.7 / 4.7 at 2 KiB for KV and
  NVFP4).
- The late DQ's first allocation comes 30-280 cycles after its start under both.

**5. The plain issue under a pace hold** (X-8; `hbm="server"`, `DMA_PACE` 16 KiB, data B/cycle):

| case | own | `isa.staging_ring` | fix session |
|---|---|---|---|
| NVFP4 weights alone, L = 845 / 1,200 | 16.5 / 11.7 | 16.5 / 11.7 | about 17 |
| weights + plain, the rule, 845 | **9.5 / 76.3** | 9.5 / 76.4 | 9.6 / 76.2 |
| weights + plain, held against all, 845 | 16.5 / 16.5 | 16.6 / 16.6 | 16.5 / 16.5 |
| KV alone, 845 / 1,200 | 17.1 / 12.0 | 17.0 / 12.0 | – |
| KV + plain, the rule, 845 | **10.7 / 74.9** | 10.7 / 74.9 | 10.7 / 74.9 |
| KV + plain, held against all, 845 | 17.0 / 34.0 | 17.0 / 34.0 | 17.0 / 34.0 |
| weights + plain / KV + plain, the rule, 1,200 | 9.5 / 76.3, 10.7 / 74.9 | 9.5 / 76.4, 10.7 / 74.9 | – |
| default `DMA_PACE`, either hold | 40.9 / 41.0, 27.6 / 55.1 | 41 / 41, 28 / 56 | identical |
| 2 KiB ring (cap 9 KiB), either hold | 5.4 / 80.9, 5.7 / 80.5 | – | plain 81 either way |

- **The old reference's 14 / 38** (`isa/isa.py` at ed2ea70, run unchanged): 14.2 / 38.2 and 13.8 / 36.9 at 845, 11.2 / 30.1 at
  1,200.
  - The cause is as the fix session says. A plain entry (`need` = 0) calls `_allocated(d, 0)`, which makes its DQ the ring
    owner. `_ring_release` is evaluated only for the DQs in `order`, which is `[hold]` while another DQ holds the issue
    turn, so the converting holder cannot read scales ahead.
  - With one line added (`_allocated` returns at once when n = 0), the same code gives 16.6 / 16.6 and 17.0 / 34.1, which
    matches my model and the current reference. That line accounts for the whole difference.
- **Can the plain DQ take credits the paced holder then waits for?** Yes. "Has its credits" reserves nothing, so a plain
  read issued under the hold can leave the holder short, and the holder then holds for credits.
  - It costs anything only when credits are scarce. At 9 credits with `DMA_PACE` 1-2 KiB the weights get 0.8 B/cycle
    against 1.2 with the holder's credits reserved, and the plain DQ 1.6 against 1.2. At 24 credits and a 1-2 KiB pace the
    plain DQ gets 4.8 against 2.4. At 512 credits: 9.5 / 76.3 against 9.7 / 76.1.
  - Liveness is unaffected. The holder waits at most about 2R: in-flight converting reads return, and then the pace allows
    one read anyway.
- **A bound on the trade?** I capped the bytes of plain reads issued outside the turn (own model, weights + plain / KV + plain):

| cap | L = 500 | L = 845 | L = 1,200 |
|---|---|---|---|
| none (the rule) | 9.5 / 76.3 | 9.5 / 76.3 | 9.5 / 76.3 |
| 96 KiB | 10.9 / 74.7 | 10.9 / 74.7 | 10.9 / 74.7 |
| 72 KiB | 13.9 / 71.4 | 13.9 / 71.4 | 11.7 / **60.2** |
| 48 KiB | 19.1 / 65.5 | 16.5 / 56.5 | 11.7 / **40.2** |

  The total stays at 85-86 B/cycle as long as the cap is at least the share times L. A cap only moves bytes from the plain DQ
  to the paced one, which can never exceed its rate alone (16.5 / 11.7). At long round trips a cap costs the plain DQ more
  than it gives. No bound is needed (item 2 verdict).

**6. The §2.5 arbitration table rebuilt** (`isa.staging_ring`, `hbm="server"`, 72 KiB, the 5%-95% window; data B/cycle for
KV + plain | two KV + plain | NVFP4 + KV | NVFP4 + MXFP8 | NVFP4 + MXFP8 + plain):

| row, L = 845 | `isa.staging_ring` | own model | §2.5 |
|---|---|---|---|
| `greedy` | 73/5, 37/37/2, 37/37, 40/40, 28/28/28 | – | identical |
| `hold` | 1/85, 5/5/76, 38/37, 40/40, 21/21/42 | – | identical |
| `shrink` | 28/56, 20/20/41, 37/37, 40/40, 26/27/30 | – | identical |
| **the rule** | 28/56, 19/19/40, 37/37, 40/40, 28/28/28 | 28/55, 19/19/40, 37/37, 40/40, 28/28/28 | identical |
| no ring turn | 28/56, 19/19/41, 2/74, 2/82, 6/39/40 | 28/55, 22/13/46, **24/52**, 3/82, 5/40/40 | identical |
| byte quantum | 28/56, 19/19/40, 37/37, 17/66, 9/38/38 | 28/55, 19/19/40, 37/37, 17/66, 9/38/38 | identical |

At L = 500 the reference gives the rule's 28/56, 20/20/42, 39/38, 40/40, 28/28/28, which matches the §2.5 sentence, and my model
gives 28/56, 20/20/42, 39/39, 40/40, 28/28/28. The "no ring turn" starvation of NVFP4 + KV depends on timing (my 24/52 against
the reference's 2/74), as Verification 5 found. Nothing else differs by more than 1 B/cycle.

**7. Converting responses rebuilt from the text** (item 3).
- I wrote the NI as §2.5 describes it. Per tag it has the unit size, the first byte's offset in its unit and that unit's
  raw-slot index. It cuts at unit boundaries, takes "every boundary splits a unit or none does", and sets slot = the index
  plus the boundaries crossed.
- I compared it with `rd_rsp_layout(unit=)` on 3,000 random entries of 16-256 B power-of-two units, cut into requests at 2 KiB
  blocks and at random unit boundaries (5,401 requests):
  - Counting the granule boundaries from the first unit's start up to the piece's unit start: **0 mismatches**.
  - Counting the boundaries the piece itself has crossed (the literal "number of boundaries crossed"): **3,156
    mismatches**, on every tail of a split unit and on requests that start past a unit's split point.
- Unpadded MXFP4 fold rows (U-1), K = 384, `s1` = 192: each row is a 128 B tile and a 64 B short tile.
  - Over one 2 KiB request, the granule boundaries at 256, 1,024 and 1,792 split a tile, and those at 512, 768, 1,280 and
    1,536 fall between units.
  - The text's NI (one unit size of 128 B) gives 16 pieces against the true 24, and it is wrong from byte 128 on.
  - `rd_rsp_layout(unit=)` cannot express this layout either: it takes one size per entry.

**8. Gather flits** (item 4, by hand from the noc.md §5 inputs). Each core gathers 87 / 288 = 0.302 rows/cycle; 8 cores spread over 4
leaves and 2 planes of 128 B.
- KV gather: 3 RSP flits per row → 0.906 (2-flit: 1.21; at the full share 1.06).
- Engram: 87 / 264 rows → 0.99 (at the full share 1.16).
- REQ: 2 flits per row → 0.60.
These agree with `noc.scenarios()` and `test_gather_leaf_load`.

## Verdicts on the seven items

| # | Item | Verdict | Notes |
|---|---|---|---|
| 1 | Ring turn's pass (X-7) | **holds, with a note** | No cycle in 800 random programs, 192 targeted runs (8-24 credits, 1-4 KiB pace, writes, gaps, raw slots, scale reads across 2 KiB blocks) and 400 more under `"always"` taking, with the asked-for overlap (issue turn held while another DQ heads the ring queue) in 436 k sweep cycles and 186 k grid cycles. The targeted hang reproduces at 3,641 (reference 3,649, Verification 5 3,628). Keeping the turn through credit, pace and issue-turn waits is safe: each of these ends without the ring (credits and pace by responses within R, the issue turn by rotation), and no other DQ can allocate meanwhile, so the holder's space stays free once it appears. `"contended"` is fair when contention starts (probe 4): the DQ that does not fit takes the turn first, and every share equals `"always"`. P1-P3 are provable, with two wording points (note N-1). |
| 2 | Plain issue under a pace hold (X-8) | **holds, with a note** | 9.5 / 76.3 and 10.7 / 74.9 reproduce, as does the drop from 16.5 / 17.1 to 9.5 / 10.7. The old 14 / 38 came only from `_allocated(d, 0)` (probe 5). The plain DQ can take credits the holder then waits for, which matters only at ≤ 24 credits. The trade is right: a cap on plain bytes moves the split, not the total (85-86), and costs the plain DQ at long L. No bound (N-2). |
| 3 | Tag-table fields (X-12) | **finding (U-1), and a note** | For uniform power-of-two units of ≤ 256 B, the text is correct when the count is read precisely, and it agrees with the reference on 5,401 requests. "Every v1 unit that can be split is such a unit" is wrong for a row that is not a whole number of tiles. The fold's short last tile (numerics.md: "the last tile may be shorter"; §2.5: "the last tile of a row is short") gives a request units of two sizes, and a short tile of K mod 256 = 32j elements is 16j B (48 B at K = 352), not always a power of two (U-1). The precise count and the 95-bit estimate are N-3 and N-4. |
| 4 | 1-flit `RD_RSP` (X-9) | **holds, with a note** | Both ends compute the half from the same (`addr`, `len`), the target from the request and the NI from its tag entry. `ni_rsp`'s 4-flit buffer holds 2-4 responses. noc.md §4 is unaffected: virtual cut-through allocates whole packets (≤ 2 flits), so packet length is not in the dependency graph, and `build_cdg` has no lengths. The loads reproduce (probe 8). No new limit is needed (N-5). |
| 5 | Epoch on responses (X-13) | **holds, with a finding (U-3)** | Coalescing per (initiator, burst id, epoch): an old-epoch entry that a late packet reopens after the restart is flushed after `ACK_FLUSH` or evicted, and its ack is dropped at the core. It never counts toward a reused burst id. One bit is enough unless a link fails again right after a restart. A packet would then have to outlive both restarts' conditions 1-3 (two `T_DRAIN`s) and the 100 µs timeout, and L-4 (f) now says so. Restarting every step with a faulted core is sound: each toggles its own cores, and the epoch is per initiator, so cross-step traffic keeps its own. Not covered: a late *request* (U-3). |
| 6 | `SCALE_INLINE` reserved (X-16) | **holds for block formats; finding (U-2)** | Every block-format path has a scale read: the Engram sibling path (an SPM-to-SPM conversion with `scale_reg` on the staged 8 B scale rows), fold tiles, KV and index-key gathers. E4M3 / E5M2 → BF16 has 1 B elements and never splits. BF16 → FP32 and FP32 → BF16 have no element-alignment rule on the global side, so an element can span two granules, a split unit without a scale read (U-2). |
| 7 | `bw_dq` window | **holds** | The code is the 5% / 95% marks of each DQ's own data with snapshots of all DQs, `None` when empty or when a DQ has no data. The §2.5 table rebuilt on it matches every cell at 845 and the L = 500 sentence (probe 6). My model, written with the same window before I read the code, agrees on the rule's row within 1 B/cycle. |

**Notes** (no failure scenario of their own):
- **N-1 (P1-P3).**
  - P1 is a one-cycle safety property on predicates the RTL has (the enumerated pass list). Proving it on the list, not on
    "any reason other than", is what Phase 3 should do.
  - P2 as worded is false when the plain request lacks credits ("a ready plain request ... still issues", where readiness
    excludes credits). Write "a ready plain request of another DQ whose credits and tag are free issues (one per cycle)".
  - P1 + P3 + the W-1 property imply that a ring-queue head allocates, but the X-7 failure was exactly that it never did.
    Prove it directly as (P4): a DQ at the head of the allocation queue allocates, or leaves the queue, within a bound in R,
    the ring size and the quanta.
  - All four fit the reduced configuration in PLAN.
- **N-2 (X-8).** §2.5 gives 10 / 76 but not the paced stream's 17 alone. A sentence would make the trade explicit: the paced
  stream drops to about 10 because the plain DQ's queue lengthens its round trip, and a cap moves bytes without adding any.
- **N-3 (slot count).** "The count is the number of boundaries crossed" should read "the number of granule boundaries from
  the first unit's start (the request's first byte minus its element offset) up to the piece's unit start". For a non-split
  first unit, "that unit's raw-slot index" should be defined as the entry's split units before it. The PLAN `core/ni` test
  should add a request that starts past a tile's split point (a 2 KiB cut inside a split tile).
- **N-4 (95 bits).** About 95-101 bits with 22-bit SPM addresses and **10-bit ring offsets**, which means a ring of at most 16
  KiB. `DMA_STAGE`'s size field ([53:32]) admits up to 4 MiB (18-bit offsets, about 117 bits), and Phase 7 costs 24-32 KiB
  rings. State the largest ring the RTL supports and fault `DMA_STAGE` above it.
- **N-5 (gather loads).** At the full share the KV gather would need 1.06 and Engram 1.16 of the leaf links. Every DMA model
  already limits both gathers to about 0.85 of the share (tag-bound, with the core's 87 B/cycle), so the DMA binds before
  the mesh. No new limit is needed beyond the §5 rows.

## Verification 6 findings

| # | Sev | From | Finding | Failure scenario | Suggested resolution |
|---|---|---|---|---|---|
| U-1 | L | X-12, item 3 | **A request of fold tiles can hold units of two sizes, which one unit size per tag cannot describe.** The fold's last tile of a row is short (numerics.md N-2; §2.5 "the last tile of a row is short"). §2.5 calls a layout that is not padded "legal", converting at the ring-limited rate. When rows are contiguous in HBM and in the output, one request spans rows (`isa.read_requests` merges them), and its units alternate 128 B and 64 B. Granule boundaries then split some units and not others, the opposite of "either every boundary inside the request splits a unit or none does". A short tile need not be a power of two (48 B at K = 352), and it can itself be split. `rd_rsp_layout(unit=)` has one size per entry, so the reference does not cover the case. Even a per-row request (padded pitch) has two unit sizes; it works only if the NI ends the last unit at the request's end, which the text does not say. | MXFP4 → E4M3 fold, K = 384, `s1` = 192, output rows contiguous. One request [0, 2 KiB) spans rows 0-10. The tiles at rows 1, 5 and 9 are split (boundaries 256, 1,024 and 1,792), and the boundaries at 512, 768, 1,280 and 1,536 fall between units. An NI built from the text (unit size 128, first offset 0) cuts at the wrong places from byte 128 on and treats every unit as whole. Row 0's short tile and row 1's tile head go to the converter as one 128 B tile, and the split tiles' halves never reach a raw slot. The result is silently wrong E4M3 weights, or a raw slot that never completes and stalls in-order reclaim. No surveyed model has such a layout (every K is a multiple of 256, and the compiler pads). | **The user's choice.** (a) Keep unpadded layouts legal. The DMA cuts a converting request where the unit size changes (at a row's short last tile, or at the row end), the text states that a request's last unit may be short (it ends at the request's end), and raw slots use the full tile pitch. Add the K = 384 / `s1` = 192 case to `rd_rsp_layout` and the PLAN `core/ni` test. (b) Make split fold tiles illegal in v1, as X-16 did for `SCALE_INLINE`: a fold-converted TD whose tiles can cross a 256 B granule faults `ILLEGAL_FIELD` (the compiler already pads, and memmap.md §6 already says a fold-converted slice starts on a tile). v1 then has no split units at all, so raw slots, response pieces and the unit size and slot index in the tag table leave the v1 `core/dma` and `core/ni` scope, and the reference keeps them for later. I lean to (b): it costs no surveyed model anything and removes the least-exercised path from both blocks. |
| U-2 | L | X-16, item 6 | **A non-block conversion of 2 or 4 B elements has no alignment rule, so an element can span two granules with no scale read to stage it.** W-5 aligns block formats. BF16 → FP32 and FP32 → BF16 (cvt 9, 10) have no rule that the global base and strides are multiples of the element size, and HBM reads have no alignment requirement. In v1 every ring entry has a scale read (X-16), so the DMA has nowhere to stage half an element. "A response never waits for another" (V-1) forbids holding it in the converter. | `DMA_COPY` GLOBAL → SPM, cvt `BF16_FP32`, source base 0x…0FF, n0 = 1,024. Element 0 is bytes 0xFF-0x100: one byte in the `gidx` 0 response and one in `gidx` 1, or in two requests if the boundary is a 2 KiB block. The converter either waits for the second response (V-1's cycle) or converts a half element, silently. The same applies to FP32 → BF16 at an address ≡ 2 mod 4. | Add to isa.md §5 / `isa.yaml` `DMA_COPY` / `DMA_GATHER`: a converting TD whose source elements are 2 or 4 B has its global base, `s1`-`s3` and a gather's row bytes as multiples of the element size, else `ILLEGAL_FIELD`. Add it to the PLAN `ILLEGAL_FIELD` cases. One sentence; no reference change. |
| U-3 | L | X-13, item 5 | **The epoch filters responses, acks and `ERR`s, not late requests of the old step.** Targets copy `epoch` but execute every `WR` and `SEM` they accept. A write of the step before a restart that arrives after it lands in SPM and adds its bytes to its `rsem` semaphore, which the CP zeroed at the restart. L-4 (f)'s "whatever its age" is correctly limited to `ERR`s, responses and acks, but (d) and §8 do not say that requests still rest on the time argument. | A `WR` with `rsem` of step N is held past the initiator's 100 µs timeout and `T_DRAIN`, for example behind a stalled SPM port, the premise Y-10 and X-13 (b) were fixed for. It lands after the restart: its bytes add to the semaphore a consumer of the restarted step waits on, and the consumer reads a buffer the new step has not finished writing. | State in L-4 (f) and isa.md §8 that a late request of the old step is kept out only by conditions 1-3 and the timeout, as two restarts in a row are, and add a `WR` with `rsem` held past `T_DRAIN` to the Phase 4 late-packet test. Optional (**a real choice**, not recommended now): SPM and CORECTL targets drop a `WR` or `SEM` whose `epoch` differs from their own core's `NI_EPOCH`. That holds only if no core writes into another step's cores, and the CP's own accesses would need an exemption. |

**Outside the seven items** (one line each, not investigated):
- memmap.md §6 says "a fold-converted slice on a 256-element tile" as if it were a rule, while microarch.md §2.5 lists "a K
  slice that does not start on one" as a legal source of split tiles. An RTL engineer who reads memmap faults a TD the
  reference converts. This is settled by U-1 (b), or by deleting the memmap clause under (a).

## Phase 3 impact

- **`core/dma` read-issue arbiter: RTL may continue as planned.**
  - Items 1, 2 and 7 hold. The pass on idle, turns under contention, the granule quantum, the hold for pace against
    converting DQs only and the write packet's credit reproduce in an independent model.
  - N-1 (P2's wording, and P4 as a direct allocation-liveness property) and N-2 go into Phase 3 as formal-property and text
    issues, without another fix session.
- **`core/dma` request former and raw-slot path, and the `core/ni` response pieces: wait for U-1.** It is the user's choice,
  and it decides whether split units exist in v1:
  - (b) removes raw slots, pieces and two tag-table fields from both blocks;
  - (a) adds a request cut and a "last unit may be short" sentence.
  Either is a short text change with a reference test, not a full fix session. Until then, build the parts U-1 does not
  touch.
- **`core/ni` otherwise: may continue.** The 1-flit half-select, the epoch compare and drop, the tag table's destination,
  range, mask and epoch, and ack coalescing per (initiator, burst id, epoch) hold (items 4 and 5). N-3 and N-4 are RTL
  issues: the precise slot count if (a) is taken, and the ring-offset width with a `DMA_STAGE` maximum.
- **U-2:** one `ILLEGAL_FIELD` sentence for the `core/dma` TD checks. It can go in with U-1's text, or into Phase 3 as an RTL
  check with the sentence added to isa.md then.
- **U-3:** no Phase 3 impact. It is Phase 4 text and a test.

**Not checked:**
- anything outside the seven items beyond the one line above, including X-10, X-11 and X-14 except as they appear in probes
  5 and 6;
- per-PC HBM (Y-4), the mesh beyond probe 8's hand count, and SPM-port arbitration;
- the reference's `greedy`, `hold` and `shrink` modes in my model (I took them from the reference only);
- the generated SV and C++, `make isa`, and `make numerics` / `hbm` / `links`, whose inputs are unchanged;
- the Phase 4 epoch and late-packet tests, which do not exist yet (item 5 and U-3 are argued, not run);
- U-1 (a)'s request cut, which I did not build.

# Resolutions (verification 6 fixes, 2026-11-02)

**Who:** a seventh fix session (Opus 5.5). It is not the author, any reviewer, any earlier fix session or any verifier.
Branch `spec-verification6-fixes` from bc8f6af, one commit per group, `make pytest` green before each. Scope: U-1 to U-3 and
the notes N-1 to N-4, nothing else. The user chose U-1 (b) and, for U-3, to state the residual. The Verification 6 tables
above are unchanged.

**Method.** I re-derived U-1 executably with `isa.read_requests` and `isa.rd_rsp_layout` (probe script, not committed), and
U-2 and U-3 by argument from the spec text. N-2's numbers were rerun in `isa.staging_ring`. Before asking about U-1 I listed
every v1 path that creates a split unit (U-1 row) and wrote the exact `ILLEGAL_FIELD` condition. Each suggested resolution
was checked against the specs and then taken, changed or rejected; each row says which.

| Group | Commit | Findings |
|---|---|---|
| 1 | 95a2f4b | U-1 (b) (the user's choice), U-2, N-3, N-4: `core/dma` TD checks, `core/ni` response path, tag table |
| 2 | e42e8a0 | N-1, N-2: the `core/dma` read-issue arbiter text, P1-P4 in PLAN Phase 3 |
| 3 | a0cd582 | U-3 (the user's choice): runtime and link text, Phase 4 test |

| # | Status | Group | What was done |
|---|---|---|---|
| U-1 | fixed ((b), the user's choice) | 1 | **Agree with the verifier.** **Re-derived:** MXFP4 → E4M3, K = 384, `s1` = 192, output rows contiguous (384 B per row). `read_requests` forms (0, 2,048) and (2,048, 1,024) from 16 rows; the first request spans rows 0-10 with 128 B tiles and 64 B short tiles. The tiles at 192, 960 and 1,728 (rows 1, 5, 9) are split by the granule boundaries at **256, 1,024 and 1,792**; those at **512, 768, 1,280 and 1,536** fall between units (the verifier: the same). True pieces **24**, `rd_rsp_layout(unit=(0, 128, raw))` **16**; the first wrong piece is at byte **128** (the short tile [128, 192) against a 128 B unit [128, 256)). `rd_rsp_layout` takes one unit size per entry, so it cannot place the layout. Also: K = 352 (`s1` = 176) splits 7 units in the first 2 KiB, two of them 48 B short tiles (at 480 and 1,008), 29 pieces against 16; MXFP8, K = 384, `s1` = 384 splits the tiles at 384, 1,152 and 1,920 (the last across the 2 KiB block), 13 pieces against 8. With rows padded in SPM, or the HBM pitch padded to 256 B, each row is its own request and nothing splits. **Every v1 path that created a split unit:** fold (`*_E4M3`) sources whose row pitch is not whole 256-element tiles (the short last tile), a K slice or base off a tile (fold tiles from byte 64), `s2` / `s3` off a tile or n0 not a multiple of 256 with n2 / n3 > 1 (a tile across two dim-0 runs), on any fold source (non-paged, paged `byte` / `s1`, a gather's base + index x `s1`); and 2 / 4 B elements off their size (U-2). `*_BF16` and E4M3 / E5M2 → BF16 have elements of at most 1 B and never split; `SCALE_INLINE` was already reserved. **Took (b), changed:** the condition is an alignment rule, slightly stricter than "a tile can cross a granule", as W-5's block rule is: under a fold `cvt` the source base (PAGED: the pointer's `byte`), `s1`, `s2` and `s3` are multiples of the tile's bytes T (128 B at 4 bits, 256 B for MXFP8), and n0 is a multiple of 256 when n2 or n3 > 1 (the verifier did not name this clause), else `ILLEGAL_FIELD`. Every tile, the short last tile included, then lies in one granule, and a K slice starts on a tile. **The memmap.md §6 / microarch.md §2.5 contradiction** is settled for memmap: its clause is now a rule with the fault, and microarch.md no longer lists "a K slice that does not start on one" as legal. **Kept from (a):** a per-row request at the padded pitch still has two unit sizes, so the text now says a fold read's tiles are T bytes from its first byte and only the last may be short, ending at the request's end (a request that spans rows has whole tiles only). Raw slots, response pieces and the unit-size, unit-offset and slot-index tag fields leave the v1 `core/dma` and `core/ni`; the text and `isa.staging_ring` / `rd_rsp_layout(unit=)` keep them, marked as a later version's. An unpadded fold weight is repacked, or converted with the exact `*_BF16` path, which accepts it. New reference `isa.cvt_source_check`; `test_cvt_source_check_no_split_units` asserts the U-1 numbers above, the padded layout (one request per row, every tile in one granule, the short tile at the request's end), the K-slice, MXFP8, n2 and paged cases, and 300 random legal fold TDs with no split tile. microarch.md §2.5 (tag table, converting responses, no split units, ring allocation and size, the compiler bullet, the interface table), memmap.md §6, isa.md §5, `isa.yaml` `DMA_COPY` / `DMA_GATHER`, noc.md §2, PLAN Phase 3 `core/dma` and `core/ni`. |
| U-2 | fixed | 1 | **Agree**, by argument. `isa.block_addr` and the TD text have no rule for non-block formats (the test even uses a BF16 base of 0x102), and a read's `len` is in bytes, so `BF16_FP32` from 0x…0FF puts element 0 in the `gidx` 0 and 1 responses; FP32 → BF16 at 2 mod 4 the same. In v1 such a transfer has no scale read, hence no ring entry, and V-1 forbids holding the byte in the converter. **Took**, extended: the rule covers the source base (or paged `byte`), `s1`-`s3` and a gather's `row_bytes` whatever the source space, and it is in the reference (`isa.cvt_source_check`) instead of text only, so the Phase 3 `ILLEGAL_FIELD` cases have something to compare against. `cvt = NONE` is unaffected. isa.md §5, `isa.yaml` (`DMA_COPY`, `DMA_GATHER`, `row_bytes`), microarch.md §2.5, PLAN Phase 3. |
| U-3 | fixed (the user's choice: the residual stated) | 3 | **Agree**, by argument. Every target copies `epoch` and executes what it accepts; only the initiator compares. A late read is harmless (its response is dropped); a late `WR` or `SEM` (also a write-add or `MC_WR`) of the old step writes and adds to an `rsem` the CP zeroed. **Took** the stated residual: L-4 (d) and (f), the L-4 data-outcome line, L-11 and isa.md §8 say late requests rest on conditions 1-3 and the timeout, as packets two restarts old do; PLAN Phase 4 adds a `WR` with `rsem` held at its target past its initiator's timeout and `T_DRAIN`. **Not taken**, costed for the user: a target-side epoch check in the SPM and CORECTL targets. It needs every writer in the target's step (epochs diverge once one step restarts), a CP exemption, an epoch on `MC_WR` (none today: a header change), and it leaves HBM and GSRAM writes, which have no owning core, on the time argument anyway. |
| N-1 | fixed | 2 | (P1) is now stated on the enumerated pass list (no entry left and the next instruction or `DMA_LIST` entry not at the head, a semaphore wait, a `SIGNAL` waiting for release, a plain read or write next), which Phase 3 proves, not on "any reason other than". (P2) now reads: while the holder lacks only pace, a ready plain request of another DQ whose own resources are free issues, one per cycle; **changed:** "resources" names a burst id for a write packet next to the verifier's tag and credits. (P4) added as proposed, with its argument (while a head waits no other DQ allocates, so P3 drains a fixed set of entries, then the head fits; a head that goes idle leaves by P1). microarch.md §2.5 and PLAN Phase 3. |
| N-2 | fixed | 2 | **Re-derived** (`isa.staging_ring`, `hbm="server"`, `DMA_PACE` 16 KiB, L = 845): NVFP4 weights alone **16.6**, KV gather alone **17.0** (L = 1,200: 11.8 / 12.0); with a plain DQ **9.6 / 76.2** and **10.7 / 75.0**, total 85.8 / 85.7. The verifier: 16.5 / 17.1, 9.5 / 76.3, 10.7 / 74.9, 11.7 / 12.0. **Took** the sentence: the paced stream drops to about 10 because the plain DQ's queue lengthens its round trip, the two go from 17 + 17 to 86, and a cap only moves bytes (with the verifier's 48 KiB cap figure at 1,200). microarch.md §2.5. |
| N-3 | fixed (moot in v1) | 1 | Under U-1 (b) there are no pieces in v1, but the paragraph stays as a later version's definition, so the wording was fixed there: slot = the request's first unit's raw-slot index (the entry's split units before it, split or not) plus the granule boundaries from the first unit's start up to the piece's unit start, with the literal "boundaries crossed" named as the wrong count. The `rd_rsp_layout` docstring says the count runs from u0. The PLAN `core/ni` test for a request that starts past a split point moves with the pieces to the later version. |
| N-4 | fixed | 1 | **Re-derived** under (b): the v1 tag entry is about **70 bits** (22-bit SPM destination, 8-bit granule offset, 11-bit `len`, 8-bit mask, 2-bit DQ, epoch, 5 bits of format and shape, an 11-bit ring offset of the first unit's scales), 4.5 KiB for 512 tags; the verifier's 95-101 bits included the raw-slot fields. **Changed:** the largest ring is **32 KiB** (11-bit offsets in 16 B units), not the 16 KiB a 10-bit offset gives, so the 24 and 32 KiB rings that Phase 7 costs stay possible for one bit. A block-format conversion with `DMA_STAGE` above 32 KiB faults `ILLEGAL_FIELD`, as one below 2 KiB does (`isa.cvt_source_check(stage=)`, tested at 0, 1 KiB, 2, 16, 32 and 64 KiB). The pace counter is unaffected (4.5 x 32 KiB is within 18 bits). microarch.md §2.5, isa.md §5, `isa.yaml` `DMA_STAGE`, PLAN Phase 3. |

**Proposals not taken as written** (the row says why):
- U-1 (b): an alignment rule instead of "tiles can cross a granule", with an n0 clause for n2 / n3 > 1; (a)'s "last unit may be short" sentence kept, because padded per-row requests need it.
- U-2: in the reference too, and for any source space.
- U-3: the optional target-side check not taken (MC_WR has no epoch; HBM and GSRAM stay on the time argument).
- N-1: P2's free resources include a write packet's burst id.
- N-3: fixed although moot, in the later-version text.
- N-4: 32 KiB, not 16 KiB.

**Reference and tests:**
- New: `isa.cvt_source_check` (U-1 fold tiles, U-2 element alignment, N-4 `DMA_STAGE` bounds), constants `FOLD_CVTS` and `STAGE_MAX`.
- `isa.rd_rsp_layout`: docstring only (`unit=` is a later version's; N-3's count; one unit size per entry). `isa.read_requests`, `isa.block_addr` and `isa.staging_ring` are unchanged.
- `make pytest` 231 passed (230 before); `test_isa.py` 54 → 55 (`test_cvt_source_check_no_split_units`).

**Regenerated:**
- `make isa` in group 1: `docs/spec/isa_ref.md` only (the `DMA_COPY` and `DMA_GATHER` text). `isa_pkg.sv`, `isa.h` and `isa_defs.py` are unchanged; `verilator --lint-only -Wall isa/gen/isa_pkg.sv` is clean, and `git status` was clean after a rerun.
- `make numerics`, `make hbm`, `make links`, `make sweep`, `make models`: not rerun. Nothing under `sw`, `sim` or `models` changed (`git diff --stat main..HEAD -- sw sim models` is empty).

**Frozen numbers:** none changed. The ring default stays 16 KiB of SPM, `DMA_PACE` 72 KiB and the tag table 512 entries. The new 32 KiB ring maximum and the tag-table estimate (about 95 → 70 bits, 6 → 4.5 KiB) are microarch.md numbers that arch.md does not freeze. D1 is unchanged. arch.md has a change-log row.

**Outside the scope** (one line each, not fixed):
- `isa.read_requests` compares destination contiguity in the run's own bytes; for a converting read the destination run is in output bytes (2x or 4x the input), so a caller who passes output addresses gets no merges at all. Fewer merges is safe (more requests), but the docstring should say the destination is given in input-byte equivalents, or the function should take the ratio.

## Before Phase 3, after the verification 6 fixes

| Block | Still open |
|---|---|
| `core/dma` | none at the spec level. Arbiter: P1-P4 (N-1) as formal properties, with X-7, X-8, X-10, X-11, X-14 in text and reference. Request former: cuts at 2 KiB blocks, destination runs and entry boundaries; the v1 `ILLEGAL_FIELD` checks of U-1 (b), U-2 and N-4 against `isa.cvt_source_check`. No raw-slot path in v1 |
| `core/ni` | none at the spec level: 1-flit `RD_RSP`, the epoch compare, and converting responses that each convert from their own bytes (no pieces in v1, U-1 (b)); tag entry about 70 bits |
| `core/seq` | none |
| `hbm` | as after the verification 5 fixes (X-2 (a), (c)-(e) on the H-9 checklist; Y-4) |
| `common`, `arith` | none |
| Phase 4 `link`, `noc` | as after the verification 5 fixes, plus U-3: a late `WR` with `rsem` held past its initiator's timeout and `T_DRAIN` |
| compiler / runtime | as after the verification 5 fixes, plus U-1 (b): fold-weight pitches padded to whole tiles and K slices on tiles are now required, not only the compiler's practice; unpadded fold layouts are repacked or use `*_BF16` |
| Phase 7 | as after the verification 5 fixes; a ring above 16 KiB is possible up to 32 KiB (N-4) |
| later version | raw slots and response pieces (split fold tiles, `SCALE_INLINE`), with X-12's tag fields; it must cut a converting request where the unit size changes (U-1 (a)) |

**May `core/dma` start Phase 3 RTL?** **Yes, all of it.**
- Read-issue arbiter: yes, with P1-P4 as its first deliverable. N-1 and N-2 were text, and Verification 6 found the rule itself sound.
- Request former: yes. U-1 (b) removes the case it could not describe, and U-2 and N-4 are `ILLEGAL_FIELD` checks with a reference.
- Raw-slot path: there is none in v1. Nothing to build.

**May the `core/ni` response path start Phase 3 RTL?** **Yes.** In v1 every converting response converts from its own valid range, so the NI needs no unit cut, and the tag entry has no unit size, unit offset or slot index.

**For the next verification pass, check first:**
1. **The U-1 (b) rule.** Is base / `byte`, `s1`, `s2`, `s3` a multiple of T, plus n0 a multiple of 256 when n2 or n3 > 1, enough for every fold source (paged, gather)? Does "a fold read's tiles are T bytes from its first byte, the last ends at the request's end" hold under every request cut (2 KiB block, destination run, Z-1 entry boundary)?
2. **The 70-bit tag entry.** Is the ring offset of the first unit's scales enough for a v1 converting response without an element offset, including an entry that spans a destination-run cut (two requests in one entry)?
3. **P4 and P2 as worded** (microarch.md §2.5), against the reduced configuration in PLAN.
