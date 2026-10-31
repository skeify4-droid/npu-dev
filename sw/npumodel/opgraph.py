"""Op-graph generator (Phase 1 step 2): the operators one prefill or decode step executes, per layer.

    ops = build(desc, "decode", batch=64, ctx=131072, q_tokens=1)
    ops = build(desc, "prefill", batch=1, ctx=32768)
    ops = draft_ops(desc, batch=64, ctx=131072, gamma=7)       # speculative-decoding drafter, one round

Each op is a dict:
  name, layer, kind     gemm | attn | index_scan | topk | linear_scan | moe_experts | gather | kv_write | vpu | lm_head
  unit                  mxu | vpu | sfu | dma    (where the work runs)
  dtype                 operand dtype of the MACs (fp8 or bf16; 4-bit weights are dequantized to fp8 in the DMA path)
  flops                 2 x MACs (or VPU/SFU element ops) for the whole step, all sequences
  w_bytes               weight bytes read from HBM (once per step)
  kv_bytes              KV / index-key / recurrent-state bytes read and written in HBM
  io_bytes              other HBM traffic (table rows, embedding rows)
  shard                 how parallelism splits it: tp (heads / columns), ep (experts), seq (per sequence), rep (replicated)
  m                     GEMM rows (tokens) per weight read, for the MXU efficiency model (gemm / moe_experts)
Activations are assumed to stay on chip between ops (scratchpad / global SRAM); spill is a Phase 7 question.

Prefill attention sums the causal triangle exactly (sum over positions of min(position, cap)); prefill K/V and
index keys are re-read once per query tile of PREFILL_Q_TILE tokens (FlashAttention-style tiling).
"""
from . import schema as S

ACT = 2.0                 # bf16 activations
PREFILL_Q_TILE = 1024     # query rows kept on chip per K/V pass in prefill
LINEAR_CHUNK = 64         # chunk length of the chunked delta-rule scan (KDA / Gated DeltaNet)


def _sum_min(n, cap):
    """sum_{p=1..n} min(p, cap)."""
    cap = min(cap, n)
    return cap * (cap + 1) / 2 + (n - cap) * cap


def _op(name, layer, kind, unit, flops=0.0, w_bytes=0.0, kv_bytes=0.0, io_bytes=0.0, shard="tp", dtype="fp8", m=0, **kw):
    return dict(name=name, layer=layer, kind=kind, unit=unit, dtype=dtype, flops=float(flops), w_bytes=float(w_bytes),
                kv_bytes=float(kv_bytes), io_bytes=float(io_bytes), shard=shard, m=m, **kw)


def entry_bytes(a, kv_dtype="fp8", window_dtype="fp8"):
    """(bytes of one global-cache entry, bytes of one window entry) for attention spec `a`."""
    kb = S.DTYPE_BYTES[a.get("kv_dtype", kv_dtype)]
    wb = S.DTYPE_BYTES[a.get("window_dtype", window_dtype)]
    if a["kind"] == "mla":
        e = a["kv_lora_rank"] + a["qk_rope_dim"]
        return e * kb, e * wb
    if a["kind"] == "gqa":
        e = a["n_kv_heads"] * (a["head_dim"] if a.get("kv_k_eq_v") else a["head_dim"] + a.get("v_head_dim", a["head_dim"]))
        return e * kb, e * wb
    return 0.0, 0.0


def _attn_core_dims(a):
    """(heads, qk dims, pv dims) per query for the attention math (MLA uses the absorbed latent form)."""
    if a["kind"] == "mla":
        return a["n_heads"], a["kv_lora_rank"] + a["qk_rope_dim"], a["kv_lora_rank"]
    return a["n_heads"], a["head_dim"], a.get("v_head_dim", a["head_dim"])


def _split_entries(a, ctx):
    """(global entries, window entries) one query reads at context ctx."""
    w = min(ctx, a.get("window") or 0)
    sp = a.get("sparse")
    r = a.get("kv_compress_ratio")
    if sp:
        return min(ctx / (r or 1), sp["topk"]), w
    if a.get("window"):
        return (ctx / r if r else 0.0), w
    return ctx, 0


def attention_ops(a, li, h, phase, batch, ctx, q, wdt, kv_dtype, window_dtype, tag="attn"):
    ops = []
    full = S._attn_params(a, h)
    base = S._attn_params({k: v for k, v in a.items() if k != "sparse"}, h)
    T = batch * (q if phase == "decode" else ctx)
    dense_b = S.DTYPE_BYTES[wdt["dense"]]
    split = {} if wdt.get("_deploy") else S.attn_dtype_split(a, h)   # published per-part dtypes (a deployment overrides them)
    wb = (base - sum(split.values())) * dense_b + sum(n * S.DTYPE_BYTES[k] for k, n in split.items())
    ops.append(_op(f"{tag}_proj", li, "gemm", "mxu", 2 * T * base, wb, m=T))
    if full > base:
        ops.append(_op(f"{tag}_index_proj", li, "gemm", "mxu", 2 * T * (full - base), (full - base) * dense_b, m=T))
    if a["kind"] == "linear":
        nv, dk, dv = a["n_v_heads"], a["k_dim"], a["v_dim"]
        state = nv * dk * dv * 4.0
        if phase == "decode":
            fl = T * nv * dk * dv * 2 * 4      # decay, delta-rule update (k^T S, outer product), readout
            kv = batch * 2 * state            # state read + write (fp32)
        else:
            fl = T * nv * 2 * (3 * dk * dv + LINEAR_CHUNK * (dk + dv))
            kv = batch * 2 * state
        ops.append(_op(f"{tag}_linear_scan", li, "linear_scan", "mxu", fl, kv_bytes=kv, shard="tp", dtype="bf16"))
        return ops
    gb, wb = entry_bytes(a, kv_dtype, window_dtype)
    H, dqk, dv = _attn_core_dims(a)
    sp = a.get("sparse")
    role = S.index_role(sp)
    # ---- index scan (keys scored per query) and top-k selection
    if role in ("own", "reindex"):
        hd, nh = sp["head_dim"], sp["n_heads"]
        ib = S.DTYPE_BYTES[sp.get("key_dtype", a.get("kv_dtype", kv_dtype))] * sp.get("k_heads", 1)
        if phase == "decode":
            N = S.index_keys_scanned(a, ctx)
            fl = 2 * T * nh * hd * N
            kv = batch * N * hd * ib
            sel = T * N * max(1, sp.get("groups", sp.get("n_heads", 1) if sp.get("kind") == "block_topk" else 1)) * 2
        else:
            pool = (a.get("kv_compress_ratio") or 1) * max(1, sp.get("key_pool", 1))
            cap = sp.get("candidate_pool", ctx / pool) * pool if role == "reindex" and sp.get("candidate_pool") else ctx
            keys_sum = _sum_min(ctx, cap) / pool
            fl = 2 * batch * nh * hd * keys_sum
            kv = batch * (ctx / pool) * hd * ib * max(1, ctx / PREFILL_Q_TILE)
            sel = 2 * batch * keys_sum
        ops.append(_op(f"{tag}_index_scan", li, "index_scan", "mxu", fl, kv_bytes=kv, shard="seq"))
        ops.append(_op(f"{tag}_topk", li, "topk", "vpu", sel, shard="seq", dtype="fp32"))
    # ---- attention math over the selected / windowed / full entries
    if phase == "decode":
        g, w = _split_entries(a, ctx)
        fl = 2 * T * H * (g + w) * (dqk + dv)
        kv = batch * (g * gb + w * wb)   # read once per sequence per step (shared by the q tokens and the heads of a group)
        exps = T * H * (g + w)
    else:
        r = a.get("kv_compress_ratio") or 1
        w_cap = a.get("window") or 0
        if sp:
            ent = _sum_min(ctx, sp["topk"] * r) / r + (_sum_min(ctx, w_cap) if w_cap else 0)
        elif w_cap:
            ent = _sum_min(ctx, w_cap) + (ctx * ctx / 2 / r if a.get("kv_compress_ratio") else 0)
        else:
            ent = ctx * (ctx + 1) / 2
        fl = 2 * batch * H * ent * (dqk + dv)
        g, w = _split_entries(a, ctx)
        kv = batch * (g * gb + w * wb) * max(1, ctx / PREFILL_Q_TILE)
        exps = batch * H * ent
    ops.append(_op(f"{tag}_core", li, "attn", "mxu", fl, kv_bytes=kv, shard="tp", dtype="bf16" if a["kind"] == "mla" else "fp8"))
    ops.append(_op(f"{tag}_softmax", li, "vpu", "sfu", exps, shard="tp", dtype="fp32"))
    # ---- cache writes (layers that own a cache, plus the raw window)
    new = batch * (q if phase == "decode" else ctx)
    wr = 0.0
    if a.get("kv_role", "own") == "own":
        if not a.get("window") or a.get("kv_compress_ratio") is not None:
            wr += new / (a.get("kv_compress_ratio") or 1) * gb
        if role == "own":
            wr += new / ((a.get("kv_compress_ratio") or 1) * max(1, sp.get("key_pool", 1))) * sp["head_dim"] * \
                S.DTYPE_BYTES[sp.get("key_dtype", a.get("kv_dtype", kv_dtype))] * sp.get("k_heads", 1)
    if a.get("window"):
        wr += new * wb
    ops.append(_op(f"{tag}_kv_write", li, "kv_write", "dma", kv_bytes=wr, shard="seq"))
    return ops


def ffn_ops(f, li, h, T, wdt, tag="ffn"):
    dense_b = S.DTYPE_BYTES[wdt["dense"]]
    if f["kind"] == "dense":
        p = 3 * h * f["d_ff"]
        return [_op(f"{tag}_dense", li, "gemm", "mxu", 2 * T * p, p * dense_b, m=T)]
    E, k, de = f["n_experts"], f["top_k"], f["d_expert"]
    d_in = f.get("d_in", h)
    ops = [_op(f"{tag}_router", li, "gemm", "mxu", 2 * T * h * E, h * E * 2, shard="rep", dtype="bf16", m=T),
           _op(f"{tag}_router_topk", li, "topk", "vpu", 2 * T * E, shard="rep", dtype="fp32")]
    if d_in != h:
        ops.append(_op(f"{tag}_expert_io_proj", li, "gemm", "mxu", 2 * T * 2 * h * d_in, 2 * h * d_in * dense_b, m=T))
    if f.get("n_shared"):
        p = 3 * d_in * f["d_shared"] * f["n_shared"]
        ops.append(_op(f"{tag}_shared", li, "gemm", "mxu", 2 * T * p, p * dense_b, m=T))
    e_act = E * (1 - (1 - k / E) ** T)          # distinct experts touched by T tokens under uniform routing
    pe = 3 * d_in * de
    eb = S.DTYPE_BYTES[f.get("expert_dtype", wdt["expert"])]
    ops.append(_op(f"{tag}_experts", li, "moe_experts", "mxu", 2 * T * k * pe, e_act * pe * eb, shard="ep",
                   m=T * k / max(e_act, 1e-9), experts_touched=e_act, dtype="bf16" if eb >= 2 else "fp8"))
    return ops


def _hc_ops(hc, li, h, T):
    o = S.hc_ops(hc, h)
    return [_op("hc_mix", li, "gemm", "mxu", 2 * 2 * T * o["macs_proj"], 2 * S.hc_params_per_sublayer(hc, h) * 4.0, shard="rep", dtype="bf16", m=T),
            _op("hc_elem", li, "vpu", "vpu", 2 * (2 * T * o["macs_elem"] + T * o["small_flops"]), shard="rep", dtype="fp32")]


def _wdt(desc, weight_dtype):
    w = dict(desc["weights"]["dtypes"])
    if weight_dtype:
        w.update(weight_dtype)
        w["_deploy"] = True
    return w


def build(desc, phase, batch, ctx, q_tokens=1, kv_dtype="fp8", window_dtype="fp8", weight_dtype=None, n_layers=None):
    """Ops of one step. decode: batch sequences x q_tokens new tokens each (q_tokens = gamma + 1 when verifying a draft)
    attending `ctx` cached tokens. prefill: batch prompts of `ctx` tokens. weight_dtype overrides desc dtypes, e.g.
    {"expert": "mxfp4"}. n_layers: run only the first n layers (DeepSeek's encoder in prefill)."""
    assert phase in ("prefill", "decode")
    h, V = desc["dims"]["hidden"], desc["dims"]["vocab"]
    wdt = _wdt(desc, weight_dtype)
    T = batch * (q_tokens if phase == "decode" else ctx)
    if n_layers is None and phase == "prefill":
        n_layers = desc["dims"].get("encoder_layers") or None
    emb_b = S.DTYPE_BYTES[wdt["embed"]]
    ops = [_op("embed", -1, "gather", "dma", io_bytes=T * h * emb_b, shard="rep")]
    hc = desc.get("extras", {}).get("hyper_connections")
    tables = {t["layer"]: t for t in desc.get("extras", {}).get("tables", [])}
    for li, (a, f) in enumerate(S.expand_layers(desc)[:n_layers]):
        ops += attention_ops(a, li, h, phase, batch, ctx, q_tokens, wdt, kv_dtype, window_dtype)
        ops += ffn_ops(f, li, h, T, wdt)
        ops.append(_op("norm_residual", li, "vpu", "vpu", T * h * 12, shard="rep", dtype="fp32"))
        if hc:
            ops += _hc_ops(hc, li, h, T)
        if li in tables:
            t = tables[li]
            ops.append(_op("table_gather", li, "gather", "dma", io_bytes=T * t["lookups_per_token"] * t["dim"] * S.DTYPE_BYTES[wdt["table"]],
                           shard="rep", rows=T * t["lookups_per_token"], row_bytes=t["dim"] * S.DTYPE_BYTES[wdt["table"]]))
            ops.append(_op("table_proj", li, "gemm", "mxu", 2 * T * t["proj_params"], t["proj_params"] * S.DTYPE_BYTES[wdt["dense"]], m=T))
    rows = T if phase == "decode" else batch   # prefill needs logits for the last position only
    ops.append(_op("lm_head", -1, "lm_head", "mxu", 2 * rows * V * h, V * h * emb_b, m=rows, dtype="bf16"))
    return ops


def draft_ops(desc, batch, ctx, gamma, kv_dtype="fp8", window_dtype="fp8", weight_dtype=None, accepted=None):
    """Ops of one drafting round (decode). `accepted`: tokens accepted in the previous round (sets how many new target
    hidden rows the drafter ingests; default gamma / 2 + 1). Returns [] when the model has no drafter / MTP or gamma = 0."""
    sd = desc.get("spec_decode")
    if not sd or gamma <= 0:
        return []
    h, V = desc["dims"]["hidden"], desc["dims"]["vocab"]
    wdt = _wdt(desc, weight_dtype)
    emb_b = S.DTYPE_BYTES[wdt["embed"]]
    acc = accepted if accepted is not None else gamma / 2 + 1
    ops = []
    if sd["method"] == "mtp" or sd.get("in_weights") == "mtp":
        mt = desc["mtp"]
        a, f = desc["attn_types"][mt["attn"]], desc["ffn_types"][mt["ffn"]]
        if sd["method"] == "mtp":       # chained: one token per step, gamma sequential steps through the MTP layers
            steps, q, lm_rows = gamma, 1, batch
        else:                           # DSpark stored as MTP layers: one parallel pass over the block
            steps, q, lm_rows = 1, gamma + 1, batch * gamma
        for s in range(steps):
            li = 1000 + s
            for j in range(mt["n_layers"] if sd["method"] != "mtp" else 1):
                lj = li + 10 * j
                T = batch * q
                ops.append(_op("mtp_eh_proj", lj, "gemm", "mxu", 2 * T * 2 * h * h, 2 * h * h * S.DTYPE_BYTES[wdt["dense"]], m=T))
                ops += attention_ops(a, lj, h, "decode", batch, ctx, q, wdt, kv_dtype, window_dtype, tag="mtp_attn")
                ops += ffn_ops(f, lj, h, T, wdt, tag="mtp_ffn")
            head_b = emb_b
            ops.append(_op("mtp_lm_head", li, "lm_head", "mxu", 2 * lm_rows * V * h, V * h * head_b, m=lm_rows, dtype="bf16"))
    else:
        dr = desc["drafter"]
        hd = dr["hidden"]
        db = S.DTYPE_BYTES[dr["dtype"]]
        ab = S.DTYPE_BYTES[dr.get("aux_dtype", dr["dtype"])]
        Tq = batch * (gamma + 1)
        Tc = batch * acc
        ops.append(_op("draft_fc", 2000, "gemm", "mxu", 2 * Tc * dr["fc_in"] * hd, dr["fc_in"] * hd * ab, m=Tc, dtype="bf16"))
        a = dict(dr["attn"], window=dr.get("window"))
        lp = S._drafter_attn_params(dr["attn"], hd)
        H, dqk, dv = _attn_core_dims(a)
        gb, _ = entry_bytes(a, kv_dtype)
        win = min(ctx, dr.get("window") or ctx)
        for j in range(dr["n_layers"]):
            lj = 2001 + j
            ops.append(_op("draft_attn_proj", lj, "gemm", "mxu", 2 * (Tq + Tc) * lp, lp * db, m=Tq + Tc))
            ops.append(_op("draft_attn_core", lj, "attn", "mxu", 2 * Tq * H * (win + gamma + 1) * (dqk + dv),
                           kv_bytes=batch * win * gb, shard="tp"))
            ops.append(_op("draft_ffn", lj, "gemm", "mxu", 2 * Tq * 3 * hd * dr["d_ff"], 3 * hd * dr["d_ff"] * db, m=Tq))
            ops.append(_op("draft_kv_write", lj, "kv_write", "dma", kv_bytes=Tc * gb, shard="seq"))
        ops.append(_op("draft_lm_head", 2100, "lm_head", "mxu", 2 * batch * gamma * V * h, V * h * emb_b, m=batch * gamma, dtype="bf16"))
    if sd["method"] == "dspark":
        r = sd.get("markov_rank") or (desc.get("drafter") or {}).get("markov_rank", 256)
        for k in range(gamma):     # Markov bias W1[x_{k-1}] . W2^T over the vocabulary, sequential in k
            ops.append(_op("markov_head", 2200 + k, "gemm", "mxu", 2 * batch * r * V, V * r * 2.0, m=batch, dtype="bf16"))
    return ops


def totals(ops):
    t = {"flops": 0.0, "w_bytes": 0.0, "kv_bytes": 0.0, "io_bytes": 0.0}
    for o in ops:
        for k in t:
            t[k] += o[k]
    return t
