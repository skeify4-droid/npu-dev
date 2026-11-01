# Architecture baseline (frozen at the end of Phase 1, 2026-11-01)

This freezes the numbers of `docs/PLAN.md` §2.2. Each one is justified by the Phase 1 analytical model:
the op-graph generator `sw/npumodel/opgraph.py`, the roofline `sim/analytic/roofline.py` and the sweeps
`sim/analytic/sweep.py` (`make sweep`, about 3 s and 20 MB). The numbers it cites come from
[`docs/reports/phase1_sweeps.md`](../reports/phase1_sweeps.md), which `make sweep` regenerates together with the plots in `plots/`.
Phase 2 refines the micro-architecture under these numbers. Changing one of them needs a new sweep and an entry in §5.

## 1. Frozen numbers

| Item | Value | Derived |
|---|---|---|
| Clock | 1.5 GHz (checked on ASAP7 in Phase 6) | |
| Cores per die | 32 | |
| MXU per core | 64 x 64 PEs, each a 4-way FP8 dot product: 16,384 FP8 MAC/cycle, FP32 accumulate; BF16 at half rate | 1.57 PFLOPS FP8, 0.79 PFLOPS BF16 per die |
| VPU per core | 512 FP32 FLOP/cycle (16 lanes x 16 x FMA) | 24.6 TFLOPS per die |
| SFU per core | 128 exp / reciprocal / rsqrt per cycle | 6.1 T ops/s per die |
| HBM per die | 4 x HBM3e 12-Hi (1024 bit, 9.6 Gb/s/pin, 36 GB) | 4.92 TB/s, 144 GB; sustained ops:byte about 301 at 0.8 MXU / 0.85 HBM efficiency (the controller model alone reaches 0.95-0.99 on decode traffic, [hbm.md](hbm.md) H-6) |
| SRAM per die | 32 x 4 MB scratchpad (32 banks, double-buffered DMA) + 32 MB global SRAM; + 32 x 512 KB MXU accumulators and 72 KB IMEM/DTAB per core *(P2)* | 160 MB + 18 MB |
| Dies per package | 2 | 3.1 PFLOPS FP8, 288 GB, 9.8 TB/s per package |
| D2D | **1.4 TB/s** per direction payload (4 sub-links, what the NoC can feed; was 5 TB/s, changed in Phase 2), about 0.5 µs per collective step (planning value; bottom-up 0.09 µs, [d2d_c2c.md](d2d_c2c.md) L-5) | |
| C2C | 16 ports x 100 GB/s per direction per package (6 SerDes lanes each), about 2 µs per collective step (planning value; bottom-up 0.24 µs) | 1.6 TB/s per package |
| NoC | 8 x 4 2D mesh, **2 planes** of 128 B/cycle/link/direction *(P2)*, HBM controllers on 16 edge leaf routers ([noc.md](noc.md)) | carries 3.3 KB/cycle of HBM ingress at <= 80% link load |
| Weight formats | FP8 E4M3/E5M2, BF16, MXFP8 (E8M0 scale / 32), MXFP4 (E8M0 / 32), **NVFP4 (E4M3 scale / 16 + FP32 tensor scale)**; 4-bit and MXFP8 are dequantized to FP8 in the DMA path | |
| KV / cache formats | FP8, BF16, **NVFP4** (DeepSeek global KV, MiniMax KV and index keys as served), MXFP4 index keys (DeepSeek) | |
| Recurrent state | FP32 (as published); BF16 evaluated and rejected in Phase 2 ([numerics.md](numerics.md) D3) | |

Changes from the Phase 0 plan: the SFU rate is set at 128 per core instead of being left open, NVFP4 is added to the weight and
KV formats, and the VPU rate is fixed at 512 FLOP/cycle/core. Everything else is unchanged.

## 2. Why these numbers

**Decode is HBM-bound for all 11 models.** At the serving point (64 sequences x 128K context), 97–100% of the op time is
HBM-bound (report §6). Routed-expert weights take 50–85% of the step. At batch 64 most experts are touched every step, so
the step streams nearly all expert weights. Bandwidth per die is therefore the lever, not MXU size.
- 6 stacks per die would give x1.43–1.66 tokens/s per package and halve the package count for 6 of the 11 models (report §4).
  It is the largest single sensitivity.
- The baseline stays at **4 stacks**, because a reticle-sized die also needs edge for D2D and C2C (PLAN §2.1).
- A 6-stack die without a D2D edge is the main Phase 6 floorplan question.

**The MXU is sized for prefill, and decode cannot use it.**
- Routed experts become compute-bound at about 151 tokens per expert with FP8 weights, and at about 80–85 with MXFP4/NVFP4
  (report §1, [plot](plots/moe_crossover.svg)).
- With uniform routing that needs 2.6k–9.6k tokens in flight per expert-parallel group (decode batch x draft tokens).
- Prefill of a 32K prompt is 99–100% compute-bound on every model. A smaller MXU would slow time-to-first-token in
  proportion, while a larger one would not help decode.
- The 64 x 64 x 4 MXU per core is kept.

**The VPU and SFU are not on the critical path at these rates.**
- At the decode serving point, VPU, SFU and top-k work is below 3% of step time for every model (computed with the same sweep).
- In prefill the SFU was the risk. Softmax needs one exp per score, and a score costs 2 x head_dim MACs, so at 64 exp/cycle
  head_dim-64 attention (Muse Spark) would be SFU-bound by 1.6x. MiMo and MiniMax would sit at 0.6–0.8x.
- **128 exp/cycle per core** keeps attention MXU-bound for head_dim >= 64.

**2 dies per package; the links are not the bottleneck.**
- Running the same models on 1 or 4 dies per package changes throughput per die by -2% to +7% (report §4).
- Communication is 1–12% of the decode step. The highest share is MiniMax at TP=4, from its per-layer MoE all-to-all.
- C2C stays as planned. D2D drops to 1.4 TB/s in Phase 2, because the mesh edge cannot feed more (noc.md §5). That changes decode
  throughput at the serving point by at most 0.2%, because the decode all-reduces are latency-bound.
- Overlap of communication with compute is assumed to be zero, so these numbers are conservative.

**4-bit weights pay off where the model was trained for them.**
- For FP8-published experts, MXFP4 experts give x1.39–1.75 tokens/s per package, and halve the package count for 3 models (GLM-5.3, Qwen, Step 5; report §4).
- Four models already ship 4-bit experts (DeepSeek MXFP4, MiMo x2, MiniMax NVFP4).
- MXFP4 and NVFP4 dequantization in the DMA path is therefore required.
- The package counts below do **not** assume post-training 4-bit quantization of the other models.

**Capacity, not bandwidth, sets the package count for large-KV models.**
- At 128K, KV per sequence ranges from 0.12 GB (DeepSeek: compressed, FP4, K = V) to 9.5 GB (Step 5: BF16 reference cache).
  MiniMax is 5.1 GB with NVFP4 KV and index keys.
- MLA and MQA caches are replicated under tensor parallelism. The mapping search therefore prefers TP = 2–4 with data-parallel
  attention.

## 3. Package count per model (fixed)

Serving point: 128K context, 64 concurrent sequences per replica, at least 50 tokens/s per user **without** speculative decoding.
Weights use the `fp8` deployment: every weight class at min(published dtype, FP8), and 4-bit QAT experts stay 4-bit.
KV dtype is FP8 unless the model's own cache format is smaller or specified (DeepSeek NVFP4/MXFP4, MiniMax NVFP4, Step 5 BF16).
Mappings are pipeline x tensor parallel (expert parallel spans the stage); the search picks the fastest mapping that fits.

| Model | Packages | Dies | Weights only | Decode tok/s/user | tok/s per package | 8 x 1M context fits on | Main limiter |
|---|---|---|---|---|---|---|---|
| Qwen3.8-Flash-Next | **2** | 4 | 1 | 129 | 4,118 | 2 | KV (1.8 GB/seq) on top of 180 GB of FP8 weights |
| MiMo-V2.6-Flash | **2** | 4 | 1 | 65 | 2,076 | 2 | full-attention KV (9 layers, 1.5 GB/seq) |
| GLM-5.3-Flash | **2** | 4 | 2 | 56 | 1,781 | 2 | expert streaming; KDA state 143 MB/seq |
| MiniMax-M3.1-Flash | **4** | 8 | 1 | 109 | 1,744 | 4 | KV + index keys (5.1 GB/seq) |
| DeepSeek V4.1 Flash | **4** | 8 | 2 | 146 | 2,330 | 4 | weights 510 GB incl. 197 G-element Engram tables leave no KV room on 2 |
| Ling 3.1 Flash | **4** | 8 | 4 | 60 | 968 | 4 | weights (572 B) |
| GLM-5.3 | **8** | 16 | 4 | 79 | 633 | 8 | MLA KV (6.2 GB/seq) |
| Step 5 Preview | **8** | 16 | 4 | 104 | 830 | 8 | BF16 KV (9.5 GB/seq) |
| MiMo-V2.6-Pro | **8** | 16 | 4 | 85 | 681 | 4 | speed: 4 packages hold it but give < 50 tok/s/user |
| Mistral Large 4 | **8** | 16 | 8 | 59 | 469 | 8 | weights (1.05 T) |
| Muse Spark Open | **8** | 16 | 4 | 58 | 464 | 8 | KV (4.5 GB/seq) |

The largest system needed is **8 packages (16 dies, 2.3 TB HBM)**, so the scale-up fabric must support at least 8 packages.
The perf simulator keeps 32 as its upper bound for sensitivity studies.

Speculative decoding with each model's own drafter gives a further x1.4–2.7 per user (report §5).
- These factors use acceptance measured on an 8 B target, so treat them as an estimate.
- Verification raises the tokens per expert, which moves decode toward the MoE crossover.
- None of the package counts above relies on speculative decoding.

## 4. Requirements this adds for Phase 2

Where Phase 2 covers each one: [isa.md](isa.md) §6 (instruction per feature), [microarch.md](microarch.md) §2.

- **DMA:** NVFP4 dequantization (FP8 scale per 16, FP32 tensor scale), alongside MXFP4 and MXFP8.
  Also an NVFP4 KV write path (quantize on store).
- **Index-scan path** (MiniMax, DeepSeek, GLM, Mistral, Qwen, Step):
  - Decode scores every index key: for MiniMax that is 64x more bytes than the selected KV.
  - At 128K, MiniMax spends 12% of the step on the index scan and top-k.
  - The scan is a streaming GEMV over the key cache plus a block-max and top-k on the VPU.
  - It is split across TP ranks by context, as in vLLM's `indexer_decode_cp`.
- **Step 5 CSA:** a region-summary write path. Every 8 tokens a softmax-gated mean of 8 keys is stored as FP8.
  The scoring is `sum_h w_h relu(q_h . s)`.
- **Linear-attention state** (GLM-5.3-Flash, Ling, Qwen):
  - FP32 state is 0.25–0.6 GB per sequence-step of HBM traffic at short context.
  - That is too large for SRAM, so it streams from HBM. A BF16 state would gain 3-6% decode but adds 0.7-2% recurrent output error, so it stays FP32 (`numerics.md` D3).
- **Spec decode:**
  - Verify batches of gamma + 1 tokens per sequence.
  - DSpark's Markov head runs gamma sequential `r x V` GEMVs (MiniMax: 7 x 102 MB of BF16 weights per round unless cached).
  - The target LM head runs over gamma rows per round.
- **SFU:** 128 exp/cycle/core (above).

## 5. Change log

| Date | Change | Evidence |
|---|---|---|
| 2026-11-01 | Frozen. SFU 128/cycle/core; NVFP4 weights and KV; VPU 512 FLOP/cycle/core; package counts fixed. | `docs/reports/phase1_sweeps.md` |
| 2026-11-01 | Phase 2: D2D 5 -> 1.4 TB/s per direction; NoC 2 mesh planes; + 16 MB ACC and 2.3 MB IMEM/DTAB per die; no central collective engine (distributed write-add, multicast, semaphores). Package counts unchanged; decode within 0.2%. | [microarch.md](microarch.md) §8, [noc.md](noc.md) §5, `make sweep` rerun |
| 2026-11-01 | Phase 2 HBM, links and spec review: no frozen number changes. The HBM controller model supports the 0.85 efficiency. C2C ports need 6 x 200 Gb/s lanes for 100 GB/s of payload. The roofline's collective formulas are conservative (ring latency) and under-count off-package all-to-all bytes (review R-1). With the d2d_c2c.md schedules decode changes by -1% to +16% and no package count changes; the roofline stays as frozen. | [hbm.md](hbm.md), [d2d_c2c.md](d2d_c2c.md), [spec_review.md](../reports/spec_review.md), `make hbm`, `make links` |
| 2026-11-01 | Phase 2 numerics: no frozen number changes. BF16 at half rate confirmed as a 2-way BF16 dot product per PE (microarch.md had quarter rate); recurrent state stays FP32; NVFP4 takes the exact BF16 path when memory-bound. | [numerics.md](numerics.md) N-3, N-7; `make numerics` |
| 2026-11-01 | Phase 2 fix session for the independent spec review: no frozen number changes. The DMA SPM write port is widened to 512 B/cycle, so the exact NVFP4 -> BF16 path keeps a 1.65x margin at every stage (D1 holds). Engram rows on the other rail of another package are staged through the sibling die instead of replicating the tables per rail, which would have raised DeepSeek's "weights only" count from 2 to 4. HBM controller results after the added timing rules stay at 0.95-0.99 on decode traffic. | [spec_review.md](../reports/spec_review.md) Resolutions; `make numerics`, `make hbm`, `make links` (L7) |
| 2026-11-01 | Phase 2 fixes for the verification findings: no frozen number changes. Block scales are staged in SPM before the data that uses them (review V-1), and those writes share the DMA's 512 B/cycle SPM write port, so the exact NVFP4 -> BF16 path keeps a 1.60x margin at 0.85 share (was 1.65x; D1 holds). The staging ring is a 16 KiB SPM region, not new SRAM, so the SRAM row is unchanged. | [spec_review.md](../reports/spec_review.md) Resolutions (verification fixes); `make numerics` |
| 2026-11-01 | Phase 2 fixes for the second verification: no frozen number changes. The DMA staging ring takes an entry's scales and raw slots together, so it cannot deadlock; it is sized for scales (16 KiB keeps NVFP4 -> BF16 at the HBM share up to an 845-cycle round trip), and the compiler pads fold-weight row pitches to whole 256-element tiles. D1 is unchanged. Semaphore reassignment, link-down tag handling and the tail-block write order are rules, not hardware. | [spec_review.md](../reports/spec_review.md) Resolutions (verification 2 fixes) |
| 2026-11-01 | Phase 2 fixes for the third verification: no frozen number changes. With the HBM share as a queue, a converting stream issued greedily falls 8-12% short of its share at a 500-cycle round trip and 23-28% at 845, so converting reads are paced (CSR `DMA_PACE`, 72 KiB in flight, chosen by the user). That gives the paced rates back: NVFP4 -> BF16 155 / 150 / 127 elements/cycle at 500 / 845 / 1,000-cycle round trips. The staging ring stays a 16 KiB SPM region, so the SRAM row is unchanged; D1 is unchanged. Ring reads never span two entries, Engram inline rows are defined, and the semaphore-move and link-down restart rules are tightened. | [spec_review.md](../reports/spec_review.md) Resolutions (verification 3 fixes) |
| 2026-11-01 | Phase 2 fixes for the fourth verification: no frozen number changes. Converting reads are now counted by format (NVFP4 x1, MXFP4 x1/2, MXFP8 x1/4) against min(`DMA_PACE`, 4.5 x the staging ring), chosen by the user, so MXFP4 and MXFP8 weight streams keep the HBM share to a 1,200-cycle round trip (they lost 16-18% at 1,000 under the flat 72 KiB). NVFP4 -> BF16 stays 155 / 151 / 127 elements/cycle at 500 / 845 / 1,000 cycles. The ring stays 16 KiB and `DMA_PACE` resets to 72 KiB, so the SRAM row is unchanged; the pace counter gains a bit (18), the NoC header uses one reserved bit (`epoch`), and the tag table keeps 512 entries. D1 is unchanged. The Engram table keeps its published separate scale array, so the capacity numbers are unchanged. | [spec_review.md](../reports/spec_review.md) Resolutions (verification 4 fixes) |

## 6. Model limits (what the roofline does not capture)

- No overlap of communication with compute, and no MXU efficiency loss at small GEMM M beyond the fixed 0.8.
  Phase 2 chose a weight-stationary MXU (microarch.md §2.2); its small-M efficiency is measured in Phase 3/7.
- MoE routing is uniform. Real routing is skewed, which touches fewer experts but loads the hot ones unevenly.
- Prefill of one prompt is spread over the data-parallel ranks of a stage, which assumes context-parallel prefill.
- Sparse attention reads `topk` entries per sequence per step, even when the gamma + 1 verify tokens select different blocks
  (a lower bound).
- Acceptance profiles come from Qwen3-8B (DSpark paper). The 11 targets' own acceptance is not published.
