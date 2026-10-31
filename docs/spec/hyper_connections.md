# Hyper-connections and hashed-gather tables: hardware and compiler requirements

Status: **decided 2026-10-31: supported.** Three of the 11 target models (DeepSeek V4.1 Flash, GLM-5.3-Flash, Qwen3.8-Flash-Next)
carry the residual stream as 4 parallel copies ("hyper-connections", HC), and two (DeepSeek, Qwen) add hashed n-gram embedding tables that are
injected into that stream. This document turns what the model code does (see `docs/reports/model_features.md`) into requirements.
Numbers marked *(P1)* are proposals for Phase 2 to confirm. Per-model costs come from `sw/npumodel/schema.py` (`hc_ops`) and are tabulated in
`docs/reports/model_survey.md`.

## 1. Two variants

The residual stream `x` has shape `[tokens, n=4, d]`. Each layer has two mixers (before attention, before the FFN).

**mHC with Sinkhorn** (DeepSeek V4.1 Flash, GLM-5.3-Flash), per sublayer, per token:
```
v      = flatten(x)                               # [4d], fp32
m      = (v . W) * rsqrt(mean(v^2) + eps)         # W: fp32 [24 x 4d]  -> 24 values
pre, post, comb = split(m, scale, base)           # 4 + 4 + (4x4)
comb   = Sinkhorn(comb, 20 iterations)            # alternate row/column normalization, doubly stochastic
y_in   = sum_i pre[i] * x[i]                      # collapse 4 -> 1, feeds norm + attention (or FFN)
x_new  = post[:, None] * y_out[None, :] + comb^T . x      # expand 1 -> 4 and mix the residual
```
Single-Pass mHC: the `pre` of sublayer k+1 comes out of the same projection that produced `post`/`comb` of sublayer k, so the stream is read once per sublayer.

**Gated low-rank HC** (Qwen3.8-Flash-Next), per sublayer, per token:
```
u      = grouped_rmsnorm(flatten(x))              # [4d]
w      = sigmoid(Up(silu(Down(u) / 4)))           # Down: [4d -> 320], Up: [320 -> 4d]
y_in   = mean_i( w[i] * u[i] )                    # collapse 4 -> 1
g      = 2 * sigmoid(Inject(u) / 4)               # Inject: [4d -> 4]
x_new  = x + g[:, None] * y_out[None, :]          # per-stream injection
```

## 2. Cost per token (from the descriptors)

| Model | Variant | Mixer weights / sublayer | MACs / sublayer | Share of model's active MACs | Stream traffic / sublayer (unfused, bf16) |
|---|---|---|---|---|---|
| DeepSeek V4.1 Flash (d = 5120) | mHC | 0.49 M (fp32) | 0.49 M | 0.3% | 120 KB |
| GLM-5.3-Flash (d = 4096) | mHC | 0.39 M (fp32) | 0.39 M | 0.3% | 96 KB |
| Qwen3.8-Flash-Next (d = 2560) | gated | 6.6 M (bf16) | 6.6 M | **9.5%** | 60 KB |

mHC is cheap in arithmetic; its cost is **memory traffic and latency** (a sequential 20-iteration Sinkhorn per token, twice per layer, 80 times per step).
Qwen's mixer is the opposite: ordinary skinny GEMMs, but 10% of decode compute and 6.6 M weights x 96 sublayers = 0.63 B parameters to stream each step.

## 3. Requirements

**HC-1: residual stream lives on-chip.** Per token the stream is `4d` bf16 = 40 KB (DeepSeek), 32 KB (GLM-Flash), 20 KB (Qwen).
If it spilled to HBM, a 2048-token prefill chunk would move 2 sublayers x 40 layers x 120 KB x 2048 = 20.1 GB (4.1 ms at 4.9 TB/s), about 20% of that
chunk's compute time (31 TFLOP at 1.57 PFLOPS FP8 is 20 ms). Rules *(P1)*: shard the stream along `d` across the cores of a die (the whole 128 MB of scratchpad would hold about 3,300 DeepSeek tokens, so use prefill chunks of 1,024 tokens or less: 41 MB, a third of it,
leaving the rest for weight and KV tiles), and fuse collapse, norm and expand into the neighbouring kernels
(the Mega-mHC idea: the reference halves activation traffic this way).

**HC-2: mixing projection precision.** DeepSeek and GLM keep the mixer weights in fp32 and do the projection in fp32. The baseline MXU is FP8 with BF16
at half rate and FP32 accumulate. Proposal *(P1)*: run the projection as BF16 hi/lo split (3 passes) on the MXU; at 0.5 M MACs per sublayer the cost is
under 1% of the layer either way. Alternative: fp32 FMA on the VPU. **Decided in Phase 2 (numerics.md D2):** two BF16 MXU passes over pre-split `W_lo`, `W_hi` (the stream is exact in BF16, so this equals BF16X3);
KL to the FP32 mixer about 1e-11 in a toy 80-sublayer model, while plain BF16 gives 2e-6 to 2e-5 (fallback) and E4M3 fails the KL < 1e-3 bound.
The skinny shape (`N = 24` against a 64-wide array, 37% utilization) is irrelevant at this size.

**HC-3: Sinkhorn on the VPU.** Per token a 4x4 matrix, 20 iterations of row sum, reciprocal, scale, column sum, reciprocal, scale: about 2.8 kFLOP per sublayer.
Vectorize across tokens (one lane per token, 16 values each); needs reciprocal and exp/softplus/sqrt from the SFU (already in the baseline) and no cross-lane ops.
Add one fused microcoded instruction `HC_SINKHORN` *(P1)* so the sequence does not occupy the command queue. Latency matters in decode (batch-1 steps):
20 dependent iterations x 80 sublayers; the compiler overlaps one sublayer's mix with the other sublayer's compute (the `pre` coefficients are available one sublayer ahead by design).

**HC-4: new tile ops** *(P1, names for isa.yaml)*: `HC_MIX` (project + rsqrt scale + split; or a GEMM plus VPU ops), `HC_SINKHORN`, `HC_PRE` (weighted 4->1 collapse),
`HC_POST` (1->4 expand + comb mix), and for the gated variant `HC_GATED_MIX` (grouped RMSNorm, SiLU, sigmoid on the low-rank path). All are elementwise or small-GEMM
compositions of existing MXU/VPU/SFU capability; no new datapath is required, only fused instructions and scratchpad layouts for `[tokens, 4, d]`.
*Phase 2:* defined in `isa/isa.yaml` as `HC_MIX`, `HC_SINKHORN`, `HC_PRE` (with `mean=1` for the gated variant) and `HC_POST` (`MHC` or `GATED`);
the stream layout is in `docs/spec/memmap.md` §7, and `DMA_GATHER_ROWS` is `DMA_GATHER` with `SCALE_INLINE`.

**HC-5: tensor parallelism.** With the stream sharded along `d`, each mixer needs one all-reduce of `[tokens, 25]` fp32 (24 outputs plus the RMS statistic):
80 extra latency-critical collectives per step for DeepSeek. This is small in bytes and large in count. The distributed schedule (reduce-scatter by write-add,
then multicast; d2d_c2c.md L-7) does it in about 0.1 µs intra-die, and about 0.2 µs across D2D. The perf simulator must model these. Qwen's gated mixer needs the `[tokens, 320]` low-rank activations reduced instead.

**HC-6: speculative layers.** DeepSeek's DSpark draft layers and the MTP layers also carry the 4 streams (the descriptor counts them in `mtp`).

## 4. Hashed n-gram gather (DeepSeek Engram, Qwen n-gram embedding)

| | DeepSeek V4.1 Flash | Qwen3.8-Flash-Next |
|---|---|---|
| Layers | 1 and 14 | 1 |
| Table | 2 x ~384 M rows x 256 FP8 (+ scale per 32) = 197 G elements | ~320 M rows x 160 BF16 = 51 G elements |
| Rows gathered per token per layer | 24 (3 n-gram sizes x 8 heads), 264 B each | 16 (2 x 8), 320 B each |
| Projection after the gather | `[6144 -> 25600]` = 157 M MACs per layer | `[2560 -> 10240]` + `[2560 -> 2560]` = 33 M MACs |
| Bytes per token (all layers) | ~13 KB (about 18 KB counting burst rounding and the separate scale array) | ~5 KB |

**G-1: indices come from token ids only**, never from activations. They can be computed ahead of time and the gather issued at the start of a step (or for a whole prefill chunk),
completely hidden behind the earlier layers. Remote latency (D2D/C2C) does not matter for throughput.

**G-2: the hash runs on the host or command processor**, not in the NPU datapath. It needs 64-bit multiply, XOR and modulo by 24 distinct primes, plus a tokenizer-derived
vocabulary map (DeepSeek). The result is an int32/int64 index list in HBM, `[tokens, layers, rows]`, about 400 B per DeepSeek token. This avoids 64-bit modulo hardware.

**G-3: `DMA_GATHER_ROWS`** *(P1)*: base address, row size (256/320 B), index list pointer and count, optional scale-array base (MXFP8 rows dequantize to BF16 on the way in,
the same path as the planned MXFP4 decompression), destination scratchpad address. It must keep 64 or more row requests in flight and tolerate rows that are not burst-aligned
(264 B is 4.1 bursts of 64 B).

**G-4: tables are sharded by row range** across the HBM of all dies and packages that hold the model (DeepSeek's 197 GB table alone exceeds one die's 144 GB).
A remote row costs one C2C/D2D round trip of 264 B. Traffic is ~3 MB per decode step at batch 256: negligible bandwidth, so only the memory map and the address
translation need to handle it. Capacity is the real impact; it goes into the Phase 1 step 3 package-count analysis.

**G-5: the gate after the gather** (DeepSeek: per-stream normalized dot product, signed sqrt, sigmoid; Qwen: key/value projections into the 4 streams) is VPU/SFU work plus one GEMM;
it reuses the HC ops above.

## 5. Impact on the plan

- Phase 1: the op-graph generator and roofline model must include HC ops, the CED prefill layer range (DeepSeek runs 20 of 40 layers in prefill), and the gather traffic.
- Phase 2: add the ops of HC-4 and G-3 to `isa.yaml`; add the numerics experiment of HC-2 to `numerics.md`; define stream layouts in `memmap.md`.
- Phase 3: the VPU/SFU microcode for Sinkhorn is part of the VPU RTL scope; the DMA RTL gets the gather mode with unaligned rows.
- Phase 7 (simulator): model the 80 per-step tiny all-reduces and the gather engine's outstanding-request limit.
- Verification: golden-model the mixers against the reference code semantics in NumPy (the formulas above), bit-exact for the elementwise parts.

## 6. Open items

- HC-2 numerics: decided (numerics.md D2); confirm on the real `hc_fn` weights (numerics.md N-9).
- Whether decode keeps the stream replicated rather than sharded for small batches (trade the HC-5 collectives for 4x more residual storage per core).
- DeepSeek's `hc_head` / final collapse and GLM's "hyper head" are one more `HC_PRE`-like op before the output norm; included in HC-4.
