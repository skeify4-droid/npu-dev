"""Descriptor schema (version 1) for the NPU model workloads.

A descriptor is a plain dict (stored as YAML in models/descriptors/<slug>.yaml):

  schema_version, name, slug, hf_repo, config_sha256
  arch      {model_type, architectures}
  dims      {hidden, vocab, n_layers, max_ctx, tie_embeddings, final_logit_softcap}
  weights   {total_bytes, dtypes{expert,dense,embed,table}, notes}
  attn_types{key: attention spec}     see ATTN_KINDS
  ffn_types {key: ffn spec}           see FFN_KINDS
  layers    [{n, attn, ffn}]          run-length encoded, in execution order
  mtp       {n_layers, attn, ffn} | null    multi-token-prediction layers stored in the main weights
  drafter   {kind, n_layers, block, ...} | null  separate speculative-decoding draft model (not in main weights)
  extras    {hyper_connections, tables}
  published {total_b, active_b, source}  parameter counts quoted by the model authors (cross-check)
  sources   [str]                   what each uncertain fact was verified against
  flags     [{id, severity, detail}]  features outside the base schema or unverified assumptions

Attention kinds: gqa (also MQA / sliding window), mla, linear (gdn | kda).
An attention spec may carry `sparse` (indexer or block top-k) and the KV-cache fields:
  window / window_dtype   raw sliding-window KV kept by this layer
  kv_role: own | reuse    `own` stores a global (full-context) cache, `reuse` reads an earlier layer's
  kv_compress_ratio       tokens per cached entry for an `own` cache (default 1)
  kv_dtype                storage dtype of the global cache (default: caller's kv_dtype)
  kv_k_eq_v               K and V are one vector (MQA latent), so one head_dim per entry
Hyper-connections (extras.hyper_connections): variant mhc_sinkhorn | gated_lowrank, see hc_ops().
FFN kinds: dense, moe.
"""
import copy
import yaml

SCHEMA_VERSION = 1
DTYPE_BYTES = {"bf16": 2.0, "fp16": 2.0, "fp32": 4.0, "fp8": 1.0, "mxfp8": 1.0 + 1 / 32,
               "fp4": 0.5, "mxfp4": 0.5 + 1 / 32, "nvfp4": 0.5 + 1 / 16}
ATTN_KINDS = {
    "gqa": ["n_heads", "n_kv_heads", "head_dim"],
    "mla": ["n_heads", "kv_lora_rank", "qk_nope_dim", "qk_rope_dim", "v_head_dim"],
    "linear": ["variant", "n_k_heads", "n_v_heads", "k_dim", "v_dim", "conv_kernel"],
}
FFN_KINDS = {
    "dense": ["d_ff"],
    "moe": ["n_experts", "top_k", "d_expert"],
}
# required = outside the base schema but the hardware/compiler WILL support it (docs/spec/hyper_connections.md);
# unsupported = no plan to support it.
SEVERITIES = ("info", "warn", "required", "unsupported")


class SchemaError(ValueError):
    pass


def _req(d, keys, where):
    for k in keys:
        if k not in d or d[k] is None:
            raise SchemaError(f"{where}: missing '{k}'")


def validate(desc):
    """Raise SchemaError on structural problems. Returns desc."""
    _req(desc, ["schema_version", "name", "slug", "hf_repo", "dims", "weights", "attn_types",
                "ffn_types", "layers", "flags"], "descriptor")
    if desc["schema_version"] != SCHEMA_VERSION:
        raise SchemaError(f"schema_version {desc['schema_version']} != {SCHEMA_VERSION}")
    _req(desc["dims"], ["hidden", "vocab", "n_layers", "max_ctx"], "dims")
    _req(desc["weights"], ["total_bytes", "dtypes"], "weights")
    for k, v in desc["weights"]["dtypes"].items():
        if v not in DTYPE_BYTES:
            raise SchemaError(f"weights.dtypes.{k}: unknown dtype {v}")
    for k, a in desc["attn_types"].items():
        if a.get("kind") not in ATTN_KINDS:
            raise SchemaError(f"attn_types.{k}: bad kind {a.get('kind')}")
        _req(a, ATTN_KINDS[a["kind"]], f"attn_types.{k}")
    for k, f in desc["ffn_types"].items():
        if f.get("kind") not in FFN_KINDS:
            raise SchemaError(f"ffn_types.{k}: bad kind {f.get('kind')}")
        _req(f, FFN_KINDS[f["kind"]], f"ffn_types.{k}")
    n = 0
    for i, r in enumerate(desc["layers"]):
        _req(r, ["n", "attn", "ffn"], f"layers[{i}]")
        if r["attn"] not in desc["attn_types"]:
            raise SchemaError(f"layers[{i}]: unknown attn '{r['attn']}'")
        if r["ffn"] not in desc["ffn_types"]:
            raise SchemaError(f"layers[{i}]: unknown ffn '{r['ffn']}'")
        n += r["n"]
    if n != desc["dims"]["n_layers"]:
        raise SchemaError(f"layers sum to {n}, dims.n_layers = {desc['dims']['n_layers']}")
    mtp = desc.get("mtp")
    if mtp:
        _req(mtp, ["n_layers", "attn", "ffn"], "mtp")
        if mtp["attn"] not in desc["attn_types"] or mtp["ffn"] not in desc["ffn_types"]:
            raise SchemaError("mtp: unknown attn/ffn type")
    for f in desc["flags"]:
        _req(f, ["id", "severity", "detail"], "flag")
        if f["severity"] not in SEVERITIES:
            raise SchemaError(f"flag {f['id']}: bad severity {f['severity']}")
    return desc


def expand_layers(desc):
    """List of (attn_spec, ffn_spec) per main layer, in order."""
    out = []
    for r in desc["layers"]:
        out += [(desc["attn_types"][r["attn"]], desc["ffn_types"][r["ffn"]])] * r["n"]
    return out


def index_role(sp):
    """own = indexer q + keys + key cache; reindex = indexer q, reads another layer's keys; reuse = nothing."""
    if not sp:
        return None
    return sp.get("index_role") or ("own" if sp.get("owns_indexer", True) else "reuse")


def _attn_params(a, h):
    k = a["kind"]
    if k == "gqa":
        H, G, dq = a["n_heads"], a["n_kv_heads"], a["head_dim"]
        dv = a.get("v_head_dim", dq)
        q = H * dq
        p = h * (G * dq if a.get("kv_k_eq_v") else G * (dq + dv))
        if a.get("q_lora_rank"):
            p += h * a["q_lora_rank"] + a["q_lora_rank"] * q
        else:
            p += h * q
        if a.get("o_lora_rank"):
            p += H * dv * a["o_lora_rank"] + a["o_groups"] * a["o_lora_rank"] * h
        else:
            p += H * dv * h
        if a.get("out_gate") == "head":
            p += h * H
        elif a.get("out_gate") == "elem":
            p += h * H * dv
    elif k == "mla":
        H = a["n_heads"]
        qk = a["qk_nope_dim"] + a["qk_rope_dim"]
        p = (h * a["q_lora_rank"] + a["q_lora_rank"] * H * qk) if a.get("q_lora_rank") else h * H * qk
        p += h * (a["kv_lora_rank"] + a["qk_rope_dim"])
        p += a["kv_lora_rank"] * H * (a["qk_nope_dim"] + a["v_head_dim"])
        p += H * a["v_head_dim"] * h
    else:  # linear: q, k, v, output projection, plus optional forget / output gates
        nk, nv, dk, dv = a["n_k_heads"], a["n_v_heads"], a["k_dim"], a["v_dim"]
        p = h * (2 * nk * dk + nv * dv) + nv * dv * h
        for g in ("f_gate", "out_gate"):
            kind = a.get(g)
            if kind == "full":
                p += h * nv * dv
            elif kind == "lowrank":
                r = a.get("gate_rank", dk)
                p += h * r + r * nv * dv
        p += h * 2 * nv  # per-head decay / beta (approximate)
    sp = a.get("sparse")
    role = index_role(sp)
    if role in ("own", "reindex"):
        p += sp.get("q_in", h) * sp["n_heads"] * sp["head_dim"] + h * sp["n_heads"]
    if role == "own":
        p += sp.get("k_in", h) * sp["head_dim"]
    return p


def _ffn_params(f, h):
    """-> (dense_class_params, expert_class_params, active_total)"""
    if f["kind"] == "dense":
        p = 3 * h * f["d_ff"]
        return p, 0, p
    d_in = f.get("d_in", h)
    proj = 2 * h * d_in if d_in != h else 0
    sh = 3 * d_in * f.get("d_shared", 0) * f.get("n_shared", 0)
    router = h * f["n_experts"]
    exp = f["n_experts"] * 3 * d_in * f["d_expert"]
    active = proj + sh + router + f["top_k"] * 3 * d_in * f["d_expert"]
    return proj + sh + router, exp, active


def hc_params_per_sublayer(hc, h):
    """Weights of one hyper-connection mixer (there are two per layer: attention and FFN)."""
    n = hc["mult"]
    if hc["variant"] == "mhc_sinkhorn":  # one [(2+n)n, n*h] projection (+ tiny base/scale)
        return (2 + n) * n * n * h + (2 + n) * n + 3
    r = hc["lowrank"]  # gated_lowrank: down [n*h -> r], up [r -> n*h], inject [n*h -> n]
    return 2 * n * h * r + n * h * n


def hc_ops(hc, h):
    """Per-token cost of ONE hyper-connection mixer (a sublayer's pre + post), in the units the hardware needs.

    macs_proj      MACs of the mixing projection (runs on the MXU in fp32/bf16 or on the VPU)
    macs_elem      elementwise MACs of the pre (collapse) and post (expand + residual mix) steps
    small_flops    scalar-ish work on [n x n] matrices: Sinkhorn iterations / sigmoids (VPU)
    stream_bytes   residual stream bytes read + written per token (bf16), before any fusion
    """
    n = hc["mult"]
    if hc["variant"] == "mhc_sinkhorn":
        # Sinkhorn: each iteration normalizes rows then columns of an n x n matrix (~4 flops per element per pass)
        small = hc["sinkhorn_iters"] * 2 * n * n * 4 + (2 + n) * n * 8
        return {"macs_proj": (2 + n) * n * n * h, "macs_elem": n * h + (n + n * n) * h, "small_flops": small,
                "stream_bytes": 2 * (n * h * 2) + n * h * 2}  # read stream for mix + for pre/post, write new stream
    r = hc["lowrank"]
    return {"macs_proj": 2 * n * h * r + n * h * n, "macs_elem": 2 * n * h, "small_flops": n * h * 4,
            "stream_bytes": 2 * (n * h * 2) + n * h * 2}


def active_params(desc, n_layers=None):
    """Per-token active parameters of the first `n_layers` main layers (all if None). Excludes lm_head when
    n_layers is given (prefill needs no logits for most tokens)."""
    h = desc["dims"]["hidden"]
    tot = 0
    for a, f in expand_layers(desc)[:n_layers]:
        tot += _attn_params(a, h) + _ffn_params(f, h)[2]
    hc = desc.get("extras", {}).get("hyper_connections")
    if hc:
        tot += 2 * hc_params_per_sublayer(hc, h) * (n_layers or len(expand_layers(desc)))
    return tot


def count_params(desc):
    """Parameter counts by weight class (from the layer plan, not from the file size)."""
    h = desc["dims"]["hidden"]
    c = {"dense": 0, "expert": 0, "embed": 0, "table": 0, "hc": 0, "active": 0}
    plan = expand_layers(desc)
    mt = desc.get("mtp")
    if mt:
        plan = plan + [(desc["attn_types"][mt["attn"]], desc["ffn_types"][mt["ffn"]])] * mt["n_layers"]
        c["dense"] += mt["n_layers"] * 2 * h * h  # eh_proj
    for a, f in plan:
        c["dense"] += _attn_params(a, h)
        d, e, act = _ffn_params(f, h)
        c["dense"] += d
        c["expert"] += e
        c["active"] += _attn_params(a, h) + act
    emb = desc["dims"]["vocab"] * h
    c["embed"] = emb if desc["dims"].get("tie_embeddings") else 2 * emb
    c["active"] += emb  # lm_head matmul reads the output embedding once per token
    ex = desc.get("extras", {})
    for t in ex.get("tables", []):
        c["table"] += t["entries"] * t["dim"]
        c["dense"] += t.get("proj_params", 0)
        c["active"] += t.get("proj_params", 0)
    hc = ex.get("hyper_connections")
    if hc:
        per = 2 * hc_params_per_sublayer(hc, h) * len(plan)
        c["hc"] = per
        c["active"] += per
    c["total"] = c["dense"] + c["expert"] + c["embed"] + c["table"] + c["hc"]
    return c


def expected_weight_bytes(desc):
    c = count_params(desc)
    dt = desc["weights"]["dtypes"]
    b = lambda k: DTYPE_BYTES[dt[k]]
    return (c["dense"] * b("dense") + c["expert"] * b("expert") + c["embed"] * b("embed")
            + c["table"] * b("table") + c["hc"] * DTYPE_BYTES[dt.get("hc", "fp32")])


def kv_bytes(desc, ctx, kv_dtype="fp8", state_dtype="fp32", window_dtype="fp8"):
    """KV-cache / recurrent-state bytes for ONE sequence of `ctx` tokens (main layers only).

    Returns total; use kv_breakdown() for the global / window / state split.
    """
    return sum(kv_breakdown(desc, ctx, kv_dtype, state_dtype, window_dtype).values())


def kv_breakdown(desc, ctx, kv_dtype="fp8", state_dtype="fp32", window_dtype="fp8"):
    """{global, index, window, state} bytes for one sequence. `global` and `index` grow with ctx."""
    out = {"global": 0.0, "index": 0.0, "window": 0.0, "state": 0.0}
    sb = DTYPE_BYTES[state_dtype]
    for a, _ in expand_layers(desc):
        kb = DTYPE_BYTES[a.get("kv_dtype", kv_dtype)]
        wb = DTYPE_BYTES[a.get("window_dtype", window_dtype)]
        owns = a.get("kv_role", "own") == "own"
        if a["kind"] == "gqa":
            per = a["head_dim"] * (1 if a.get("kv_k_eq_v") else 1) + (0 if a.get("kv_k_eq_v") else a.get("v_head_dim", a["head_dim"]))
            per *= a["n_kv_heads"]
            if a.get("window"):
                out["window"] += min(ctx, a["window"]) * per * wb
                if a.get("kv_compress_ratio") is not None and owns:
                    out["global"] += (ctx / a["kv_compress_ratio"]) * per * kb
            elif owns:
                out["global"] += ctx * per * kb
        elif a["kind"] == "mla":  # latent cache (weight-absorbed decode)
            if owns:
                out["global"] += ctx * (a["kv_lora_rank"] + a["qk_rope_dim"]) * kb
        else:
            out["state"] += a["n_v_heads"] * a["k_dim"] * a["v_dim"] * sb
            out["state"] += a["conv_kernel"] * (2 * a["n_k_heads"] * a["k_dim"] + a["n_v_heads"] * a["v_dim"]) * DTYPE_BYTES[kv_dtype]
        sp = a.get("sparse")
        if sp and owns and index_role(sp) == "own":
            ratio = a.get("kv_compress_ratio") or 1
            ib = DTYPE_BYTES[sp.get("key_dtype", a.get("kv_dtype", kv_dtype))]
            out["index"] += (ctx / (ratio * max(1, sp.get("key_pool", 1)))) * sp["head_dim"] * ib
    return out


def load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def dump(desc, path):
    with open(path, "w") as f:
        yaml.safe_dump(copy.deepcopy(desc), f, sort_keys=False, width=100, default_flow_style=None)
