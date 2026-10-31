"""Roofline model of one decode or prefill step on a multi-die, multi-package system (Phase 1 step 3).

Mapping (per pipeline stage of `dies_stage = packages * dies_per_pkg / pp` dies):
  attention / dense FFN / LM head: tensor parallel over `tp` dies, data parallel (by sequence) over dies_stage / tp
  routed experts: expert parallel over all dies of the stage (balanced routing)
  pipeline: layers split evenly over `pp` stages; the batch is split into pp micro-batches
Each op runs at max(compute time, HBM time) on one die (its share of the work). Communication per layer:
  TP all-reduce after attention and after a dense FFN (ring), MoE dispatch (fp8) + combine (bf16) all-to-all,
  activation hand-off between pipeline stages. Links: D2D inside a package, C2C between packages.
Capacity per die: weights of its share + KV of its sequences + a fixed workspace must fit hbm_usable.
"""
import math

from npumodel import opgraph as G, schema as S, specdec
from . import hw as HW

PREFILL_CHUNK = 8192   # tokens per prefill chunk for pipeline parallelism


def _layer_of(op, n_layers):
    li = op["layer"]
    if li >= 1000:      # drafter / MTP: last stage
        return n_layers - 1
    if li < 0:
        return 0 if op["name"] == "embed" else n_layers - 1
    return li


def _link(hw, group):
    """(per-die bandwidth, latency, off-package fraction) for a collective over `group` dies."""
    dpp = hw["dies_per_pkg"]
    if group <= dpp:
        return hw["d2d_bw"], hw["d2d_lat"], 0.0
    return hw["c2c_bw_pkg"] / dpp, hw["c2c_lat"], 1 - dpp / group


def _comm(hw, kind, group, bytes_per_die):
    if group <= 1 or bytes_per_die <= 0:
        return 0.0
    bw, lat, off = _link(hw, group)
    if kind == "allreduce":
        steps = 2 * (group - 1)
        return 2 * (group - 1) / group * bytes_per_die / bw + steps * lat
    # all-to-all: on-package share over D2D, off-package share over C2C, two latency rounds
    on = bytes_per_die * (1 - 1 / group) * (1 - off)
    offb = bytes_per_die * (1 - 1 / group) * off
    return max(on / hw["d2d_bw"], offb / bw if off else 0.0) + 2 * lat


def layer_kv_dup(a, tp):
    """How many TP ranks hold (and read) the same KV of one sequence: MLA latents and MQA / few-KV-head caches cannot
    be split by heads, so they are replicated across the TP group. Linear-attention state splits by head."""
    if tp <= 1 or a["kind"] == "linear":
        return 1.0
    if a["kind"] == "mla":
        return float(tp)
    return tp / min(tp, a["n_kv_heads"])


def kv_dup(desc, tp):
    """KV-weighted replication factor of the whole model under tensor parallelism."""
    if tp <= 1:
        return 1.0
    num = den = 0.0
    for a, _ in S.expand_layers(desc):
        w = sum(G.entry_bytes(a)) or 1.0
        num += w * layer_kv_dup(a, tp)
        den += w
    return num / den


def capacity(desc, hw, packages, pp, tp, batch, ctx, kv_dtype="fp8", weight_dtype=None, with_drafter=True):
    """Bytes per die of the most loaded stage -> dict(weights, kv, total, limit, fits)."""
    p = HW.peaks(hw)
    dies = packages * hw["dies_per_pkg"]
    ds = dies / pp
    c = S.count_params(desc)
    dt = dict(desc["weights"]["dtypes"], **(weight_dtype or {}))
    B = lambda k: S.DTYPE_BYTES[dt[k]]
    ov = {} if weight_dtype else c["expert_override"]       # a deployment dtype replaces the published per-part dtypes
    dv = {} if weight_dtype else c["dense_override"]
    expert_b = (c["expert"] - sum(ov.values())) * B("expert") + sum(n * S.DTYPE_BYTES[k] for k, n in ov.items())
    dense_b = ((c["dense"] - sum(dv.values())) * B("dense") + sum(n * S.DTYPE_BYTES[k] for k, n in dv.items())
               + c["hc"] * S.DTYPE_BYTES[dt.get("hc", "fp32")])
    embed_b = c["embed"] * B("embed")
    table_b = c["table"] * B("table")
    dr = desc.get("drafter")
    draft_b = S.drafter_bytes(dr) if (with_drafter and dr and dr.get("attn")) else 0.0
    w = (dense_b / pp) / tp + expert_b / pp / ds + embed_b / tp + table_b / dies + draft_b / tp
    kv = batch * S.kv_bytes(desc, ctx, kv_dtype) / pp / ds * kv_dup(desc, tp)
    limit = p["hbm_bytes"] * hw["hbm_usable"]
    tot = w + kv + hw["workspace_bytes"]
    return {"weights": w, "kv": kv, "total": tot, "limit": limit, "fits": tot <= limit}


def stage_time(ops, desc, hw, packages, pp, tp, n_tokens):
    """Seconds for one stage to process its ops (ops built for ONE micro-batch). -> (time, breakdown)."""
    p = HW.peaks(hw)
    dies = packages * hw["dies_per_pkg"]
    ds = dies / pp
    dp = ds / tp
    h = desc["dims"]["hidden"]
    L = desc["dims"]["n_layers"]
    per_stage = math.ceil(L / pp)
    br = {"compute": 0.0, "memory": 0.0, "comm": 0.0, "by_kind": {}}
    stage_t = [0.0] * pp
    layers = S.expand_layers(desc)
    for o in ops:
        st = min(pp - 1, _layer_of(o, L) // per_stage)
        sh = o["shard"]
        fl = o["flops"] / ds if sh != "rep" else o["flops"] / dp
        wb = {"tp": o["w_bytes"] / tp, "ep": o["w_bytes"] / ds, "seq": o["w_bytes"] / ds, "rep": o["w_bytes"]}[sh]
        dup = 1.0
        if o["kind"] in ("attn", "kv_write") and 0 <= o["layer"] < L:
            dup = layer_kv_dup(layers[o["layer"]][0], tp)   # index scans are split by context (vLLM indexer_decode_cp)
        mem = wb + (o["kv_bytes"] * dup + o["io_bytes"]) / ds
        rate = p["vpu"] if o["unit"] == "vpu" else p["sfu"] if o["unit"] == "sfu" else p[o["dtype"]] if o["dtype"] in ("fp8", "bf16") else p["vpu"]
        tc = fl / rate if o["unit"] != "dma" else 0.0
        tm = mem / p["hbm_bw"]
        t = max(tc, tm)
        stage_t[st] += t
        br["compute" if tc >= tm else "memory"] += t
        br["by_kind"][o["kind"]] = br["by_kind"].get(o["kind"], 0.0) + t
    # communication per main layer (counted on every stage evenly)
    act = n_tokens * h * 2.0
    hc = desc.get("extras", {}).get("hyper_connections")
    t_ar_attn = _comm(hw, "allreduce", tp, act / dp)
    comm_layer = []
    for a, f in S.expand_layers(desc):
        t = t_ar_attn
        if f["kind"] == "dense":
            t += t_ar_attn
        else:
            tok_die = n_tokens / ds
            t += _comm(hw, "a2a", ds, tok_die * f["top_k"] * f.get("d_in", h) * (1.0 + 2.0))
        comm_layer.append(t)
    for i, t in enumerate(comm_layer):
        st = min(pp - 1, i // per_stage)
        stage_t[st] += t * (1 - hw["overlap"])
        br["comm"] += t * (1 - hw["overlap"])
    if pp > 1:  # hand-off of the residual stream (n streams with hyper-connections) to the next stage
        bw, lat, _ = _link(hw, hw["dies_per_pkg"] + 1 if packages > 1 else 2)
        hand = n_tokens * h * 2.0 * (hc["mult"] if hc else 1) / ds / bw + lat
        stage_t = [t + hand for t in stage_t]
        br["comm"] += hand * pp
    return max(stage_t), br


def decode(desc, hw, packages, batch, ctx, pp=1, tp=1, gamma=0, domain="code", kv_dtype="fp8", weight_dtype=None):
    """Steady-state decode with optional speculative decoding (gamma draft tokens per round).

    Returns dict: step_s (one round for one sequence), tok_s_user, tok_s_total (system), fits, mem, breakdown."""
    sd = desc.get("spec_decode")
    method = sd["method"] if (sd and gamma > 0) else None
    micro = max(1, batch / pp)
    q = gamma + 1 if method else 1
    ops = G.build(desc, "decode", micro, ctx, q_tokens=q, kv_dtype=kv_dtype, weight_dtype=weight_dtype)
    if method:
        ops += G.draft_ops(desc, micro, ctx, gamma, kv_dtype=kv_dtype, weight_dtype=weight_dtype)
    t_stage, br = stage_time(ops, desc, hw, packages, pp, tp, micro * q)
    step = t_stage * pp
    tau = specdec.tau(method, domain, gamma) if method else 1.0
    cap = capacity(desc, hw, packages, pp, tp, batch, ctx, kv_dtype, weight_dtype)
    return {"step_s": step, "tau": tau, "tok_s_user": tau / step, "tok_s_total": batch * tau / step,
            "fits": cap["fits"], "mem": cap, "breakdown": br, "pp": pp, "tp": tp, "gamma": gamma}


def prefill(desc, hw, packages, prompt, batch=1, pp=1, tp=1, kv_dtype="fp8", weight_dtype=None):
    """Time to prefill `batch` prompts of `prompt` tokens (pipelined over pp stages by chunks)."""
    ops = G.build(desc, "prefill", batch, prompt, kv_dtype=kv_dtype, weight_dtype=weight_dtype)
    t_stage, br = stage_time(ops, desc, hw, packages, pp, tp, batch * prompt)
    chunks = max(1, batch * prompt / PREFILL_CHUNK)   # chunked prefill keeps all pp stages busy after the fill
    t = t_stage * (1 + (pp - 1) / chunks)
    return {"time_s": t, "tok_s": batch * prompt / t, "breakdown": br}


def mappings(packages, hw, max_tp=8):
    dies = packages * hw["dies_per_pkg"]
    for pp in [x for x in (1, 2, 4, 8, 16, 32, 64) if dies % x == 0]:
        ds = dies // pp
        for tp in [x for x in (1, 2, 4, 8, 16) if x <= min(ds, max_tp) and ds % x == 0]:
            yield pp, tp


def best_decode(desc, hw, packages, batch, ctx, objective="tok_s_total", **kw):
    """Best (pp, tp) mapping that fits in memory, by objective; None if nothing fits."""
    best = None
    for pp, tp in mappings(packages, hw):
        if batch < pp:
            continue
        r = decode(desc, hw, packages, batch, ctx, pp=pp, tp=tp, **kw)
        if r["fits"] and (best is None or r[objective] > best[objective]):
            best = r
    return best


def min_packages(desc, hw, batch, ctx, kv_dtype="fp8", weight_dtype=None, limit=64):
    """Fewest packages (power of two) on which some mapping holds weights + KV for batch x ctx."""
    n = 1
    while n <= limit:
        for pp, tp in mappings(n, hw):
            if capacity(desc, hw, n, pp, tp, batch, ctx, kv_dtype, weight_dtype)["fits"]:
                return n, pp, tp
        n *= 2
    return None
