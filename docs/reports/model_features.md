# Model feature investigation (Phase 1, step 1b)

The first ingestion pass flagged features that `config.json` does not explain. This report resolves them from each
model's own code, README or technical report. The checkable results are encoded as tests in `tb/unit/test_descriptors.py`.

**Sources** (all read on 2026-10-31; downloaded to scratch, not committed): DeepSeek `inference/model.py`, `engram.py`, `kernel.py`,
the tech report PDF and README; Ling `modeling_bailing_moe_v3.py` and README; Mistral `params.json`, README and `transformers`
`modeling_mistral_large4.py`; Muse Spark `transformers` `modeling_muse_spark.py` and README; Qwen `transformers` `modeling_qwen4_exp.py` and README;
GLM-5.3-Flash `transformers` `modeling_glm5_next.py` and README; Step 5 `inference/model.py`; MiMo `modeling_mimo_v2.py`, README and `dflash/`;
MiniMax README and `dspark/`; Hugging Face tensor-dtype counts (`/api/models/<repo>`).

## Two independent checks that make the results trustworthy

1. **Weight bytes.** Estimated from the layer plan and dtypes, divided by the published safetensors size: all 11 within 6%
   (DeepSeek 0.998, GLM-5.3 0.999, Ling 0.999, Qwen 0.997, Muse 0.997, Mistral 0.992, Step 0.962, MiMo 0.978/0.979, GLM-Flash 0.981, MiniMax 0.941).
2. **Parameter counts quoted by the authors** (total within 5%, active within 13%).
   Close hits: MiMo-Flash 309.8 vs 309 B; GLM-Flash 320.8 vs 320 B; Qwen 179.5 vs 180 B (125 + 51 n-gram + 4 MTP); Muse 820 vs 816 B (see row 13).

Two stronger confirmations on DeepSeek V4.1 Flash: the descriptor gives **exactly 890.0 bytes of global KV per token**
(the README figure: 720 B main KV + 170 B index keys), and 557.2 G expert elements (the HF `I8` count is 557.17 G).

## Resolved questions

| # | Model | Question | Answer | Evidence |
|---|---|---|---|---|
| 1 | DeepSeek V4.1 | What do `compress_ratios` mean? | 0 = sliding window only. r > 0 = the global KV cache holds one entry per r tokens, but **only the four `kv_source_layer_ids` build it**; every other layer reads the nearest earlier source. Encoder layers use r = 2 (except layers 0-1, window only), decoder layers use r = 1. Every layer also keeps a 128-token raw window. | `Attention` docstring in `model.py`; 890 B/token reproduced |
| 2 | DeepSeek V4.1 | What are `kv_source` / `index_source` layers? | CSA2 modes. **Full** (ids 2, 8, 14, 20): build the cache and the indexer. **Reindex** (24, 28, 32, 36): rescore the shared index keys with their own query. **Reuse** (all others): use the previous top-k. Each query attends to the 128-token window plus the top-512 cache entries. | tech report §2.3.1, `Indexer` |
| 3 | DeepSeek V4.1 | What is the candidate pool? | Layer 20 also picks the 2048 best blocks of 8 positions (16,384 candidates). Reindex layers search only inside that pool, so their cost stops growing with context. | tech report §2.3.2, `select_candidate_blocks` |
| 4 | DeepSeek V4.1 | Why 8B active in prefill vs 16B in decode? | **Causal Encoder-Decoder.** Layers 0-19 are the encoder, 20-39 the decoder. Decoder global KV is projected from the encoder's last hidden state, so **prefill runs only 20 layers** (descriptor: 7.6 B active). Decoder SWA KV is rebuilt by replaying only the last 128 prompt tokens (SWA Bounded Replay). | tech report §2.2; README |
| 5 | DeepSeek V4.1 | What are the `dspark` heads? | 3 draft layers **inside the main weights**: 128 routed experts, top-3, FP4, plus a Markov head and a confidence head. Block size 5. Modeled as MTP layers (they were wrongly given 384 experts before, which inflated the model by 27 B). | `DSparkBlock`; safetensors keys `mtp.*`; HF `I8` count |
| 6 | DeepSeek V4.1 | KV format? | MQA with one 512-wide head where **K = V** (one vector, not two). Global KV is FP4 E2M1 with an E4M3 scale per 16 (0.5625 B/element). Index keys are FP4 with an E8M0 scale per 32. The raw window is FP8. | `Compressor`, `Indexer`, README |
| 7 | DeepSeek V4.1 | What exactly is Engram? | Per engram layer (ids 1, 14): hash the last 2/3/4 tokens (3 sizes x 8 heads = **24 rows**), gather 24 FP8 rows of 256 from a ~384 M-row table (scale per 32), project 6144 -> 5 x 5120, and add a sigmoid-gated value into each of the 4 residual streams. **The hash depends only on token ids**, so the gather can be issued before the layer runs. | `engram.py`, `Engram` |
| 8 | DeepSeek V4.1, GLM-5.3-Flash | What is mHC exactly? | Per sublayer: RMS-normalize the 4 streams as one 4d vector, project with an fp32 `[24 x 4d]` matrix, split into pre (4), post (4), comb (4x4), run 20 Sinkhorn iterations on comb, then `pre`-collapse before the sublayer and `post`-expand + `comb`-mix after it. **The coefficients one sublayer computes are used by the next** (Single-Pass mHC). GLM-5.3-Flash uses the same `[24 x 4d]` form. | `Block.hc_mixes`; `Glm5NextTextHyperConnection` |
| 9 | Ling 3.1 | Layer pattern? | Layer i is MLA iff (i+1) % 8 == 0: 9 MLA + 63 KDA. Confirmed (my assumption was right). KDA has six full-size projections (q, k, v, forget gate, output gate, o) and a short conv; this fixed the active-parameter estimate (22.4 -> 24.5 B vs 25 B published). | `modeling_bailing_moe_v3.py`; README "63 KDA + 9 Gated MLA (7:1)" |
| 10 | Mistral Large 4 | Router scoring? | **Softmax** over fp32 logits, normalized top-k, no selection bias (my assumption was right). | `MistralLarge4TextTopkRouter` |
| 11 | Mistral Large 4 | Anything not in config.json? | Layers 39, 43, ..., 71 (every 4th, the NoPE layers) **read layer 35's KV**; FP8 KV and FP8 indexer; DSpark draft head = 3 dense BF16 MLA layers, block 3; query scaling by position and tanh logit cap 30. | `params.json` |
| 12 | Muse Spark Open | Router scoring? | **Sigmoid with a selection-only correction bias** (my assumption of softmax was wrong), logits scaled by `output_multiplier`. | `MuseSparkTopkRouter` |
| 13 | Muse Spark Open | Other hidden structure? | Elementwise sigmoid **output gate** on attention (adds 4.2 B parameters; the weight-size check improves 0.992 -> 0.997, and the file also holds ~1.8 B of vision parameters, so the README's 816 B is a different rounding of the text model than our 820 B), unit-scale QK norm, learned per-channel residual gates, experts behind `pre_expert_proj` / `post_expert_proj` at width 4096. | `MuseSparkTextAttention`, `MuseSparkMoE` |
| 14 | Qwen3.8-Flash-Next | Is its hyper-connection Sinkhorn? | **No.** It is a gated low-rank mixer: grouped RMSNorm, `[4d -> 320 -> 4d]` with SiLU then sigmoid, mean-collapse, plus a `[4d -> 4]` sigmoid injection gate. It costs 6.6 M MACs per sublayer (**9.5% of active compute**), unlike mHC (0.3%). My earlier "Sinkhorn-style, unverified" flag was wrong. | `Qwen4ExpTextGatedResidual` |
| 15 | Qwen3.8-Flash-Next | N-gram table shape? | 16 heads (2 n-gram sizes x 8) of ~20 M rows x 160 BF16 = 51.2 B elements, on one layer (layer index 1; `ple_layer_ids` is 1-based). Gathers 16 rows of 320 B per token. Matches the README's "51B n-gram embedding". | `Qwen4ExpTextNGramEmbedding`; README |
| 16 | Qwen3.8-Flash-Next | Router and indexer? | Softmax router with normalized top-k, no bias. QSA indexer (MQA, 4 query heads, 1 key head, dim 128) on the 12 full-attention layers over keys compressed by 4; 2048-token budget = 512 blocks. | `Qwen4ExpTextTopKRouter`, `QSAIndexer`, README |
| 17 | GLM-5.3-Flash | Linear attention? | KDA with q/k/v/o full size but **low-rank** forget and output gates; the earlier formula over-counted. Total now 320.8 B vs 320 B published. | `Glm5NextTextLinearAttention` |
| 18 | MiMo-V2.6 | What is the 3-layer MTP (Flash) and where is the drafter? | The 3 MTP layers in the main weights are **dense** SWA layers (not MoE as first modeled; that added 19 B). Speculative decoding uses a separate 5-layer **DFlash** drafter (`dflash/`, SWA window 1024, block 8). Pro keeps its MTP in a separate file. | safetensors keys `model.mtp.*`; README; `dflash/config.json` |
| 19 | MiniMax-M3.1 | Why are the experts 4-bit? | README: the routed experts and attention paths are quantization-aware trained; HF dtype counts show `U8`. DSpark draft head (5 layers, block 7) is separate (`dspark/`). | README; `dspark/config.json` |
| 20 | Step 5 | Router, MTP? | Sigmoid with a selection-only bias. The 3 MTP layers are in the main weights (`layer_types` has 95 = 92 + 3 entries). | `Step5MoE`, `Step5Attention` |

## Corrections to the first-pass descriptors

DeepSeek DSpark layers 384 -> 128 experts; DeepSeek global KV ~10 KB/token (K and V counted separately, no cross-layer sharing, FP8) -> 890 B/token;
Muse router softmax -> sigmoid with bias; Qwen hyper-connection Sinkhorn -> gated low-rank; MiMo MTP MoE -> dense; Ling and GLM-Flash linear-attention
parameter counts; Qwen n-gram table shape (320 M rows x 160, not 20 M x 2560; same size); engram table dtype FP8 with scales.

## Still open

- **MiniMax Sparse Attention** (arXiv 2606.13392): the block top-k scoring (`score=max`, init/local blocks) is modeled from config only; the paper is not read.
  The MiniMax weight check is 6% low, so some tensors are larger than modeled.
- **Step 5 CSA block compression:** the reference code uses weighted-ReLU scores and the compression is approximated as a plain indexer.
- **GLM-5.3 indexer sharing:** `indexer_types` (21 full, 57 shared) is used as given; no code read for GLM-5.3 itself (GLM-5.3-Flash code was read).
- **GLM-5.3 and MiMo-Pro published counts:** GLM-5.3 README has none; MiMo-Pro's 1.02 T / 42 B matches.
- **Draft/verify schedules** (DSpark, DFlash, MTP) and acceptance rates are not modeled. They are sweep parameters in Phase 1 step 4.
- **Mistral drafter weights:** unknown whether they are in the 1.06 TB or separate (tiny either way: ~3 GB).
- **mHC numerics:** the reference computes the mixing projection in fp32. Whether BF16 operands with FP32 accumulate are accurate enough is a Phase 2 numerics experiment.
