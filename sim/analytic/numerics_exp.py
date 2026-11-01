"""Phase 2 numerics experiments (docs/spec/numerics.md N-7) -> docs/reports/numerics_experiments.md.

E1  block-scale fold into E4M3 (cvt *_E4M3) vs the exact BF16 path, and what the exact path costs in prefill.
E2  hyper-connection (mHC) mixer precision: FP32 vs BF16 vs BF16 hi/lo vs E4M3, KL of the final logits (HC-2).
E3  BF16 recurrent state for gated delta-rule linear attention (KDA / Gated DeltaNet), and what it would save.
E4  MXU accumulation order over long K.

All inputs are synthetic (fixed seeds); no model weights are needed. Run: make numerics (about a minute, <300 MB).
"""
from __future__ import annotations

import os

import numpy as np

from npumodel import numerics as nm, opgraph as G
from . import hw as HW, roofline as R, sweep as SW

ROOT = SW.ROOT
REPORT = os.path.join(ROOT, "docs", "reports", "numerics_experiments.md")
f32 = np.float32


def rel(y, r):
    return float(np.linalg.norm(y - r) / np.linalg.norm(r))


# ------------------------------------------------------------------ E1
def e1_weights(rng, kind, N, K):
    if kind == "Gaussian":
        w = rng.standard_normal((N, K))
    elif kind == "Student-t (4)":
        w = rng.standard_t(4, (N, K))
    else:                                            # input-channel scale spread (log-normal, sigma = 1)
        w = rng.standard_normal((N, K)) * np.exp(rng.normal(0, 1.0, (1, K)))
    return (w * 0.02).astype(f32).astype(np.float64)


def e1(N=256, K=6144, M=64):
    """MiniMax expert shape (w1: K = 6144). Activations BF16 with 16 outlier channels (x30)."""
    rng = np.random.default_rng(1)
    X = rng.standard_normal((M, K))
    X[:, rng.choice(K, 16, replace=False)] *= 30
    X = nm.bf16(X)
    ea, sa = nm.e4m3_row_quant(X)
    Xa8 = ea * sa
    ae, as_, aT = nm.nvfp4_quant(X)
    Xa4 = nm.nvfp4_dequant(ae, as_, aT).reshape(M, K)
    rows = []
    for kind in ("Gaussian", "Student-t (4)", "channel-scaled"):
        W = e1_weights(rng, kind, N, K)
        el, s, T = nm.nvfp4_quant(W)
        Wq = nm.nvfp4_dequant(el, s, T).reshape(N, K)         # the published (served) weights, exactly
        v, cs = nm.fold_e4m3(Wq / T)
        Wf = nm.unfold(v, cs) * T
        yr = X @ Wq.T
        yb = nm.mxu_mm(X, Wq.T, "BF16")                        # cvt NVFP4_BF16: exact weights, BF16 MXU
        rows.append(dict(fmt="NVFP4", dist=kind, changed=float(np.mean(Wf != Wq)),
                         quant=rel(Wq, W), fold_w=rel(Wf, Wq), fold_y=rel(X @ Wf.T, yr), a8=rel(Xa8 @ Wq.T, yr),
                         fold_a8=rel(Xa8 @ Wf.T, yr), a4=rel(Xa4 @ Wq.T, yr), bf16=rel(yb, yr)))
        for name, f in (("MXFP4", nm.E2M1), ("MXFP8", nm.E4M3)):
            e, Xs = nm.mx_quant(W, f)
            Wm = nm.mx_dequant(e, Xs).reshape(N, K)
            v, cs = nm.fold_e4m3(Wm)
            Wmf = nm.unfold(v, cs)
            yr = X @ Wm.T
            rows.append(dict(fmt=name, dist=kind, changed=float(np.mean(Wmf != Wm)), quant=rel(Wm, W),
                             fold_w=rel(Wmf, Wm), fold_y=rel(X @ Wmf.T, yr), a8=rel(Xa8 @ Wm.T, yr),
                             fold_a8=rel(Xa8 @ Wmf.T, yr), a4=float("nan"), bf16=float("nan")))
    return rows


def _force_bf16(pred):
    """Context: G.build with the ops matching pred(op, desc) moved to the BF16 MXU rate."""
    orig = G.build

    def build(desc, *a, **kw):
        ops = orig(desc, *a, **kw)
        for o in ops:
            if pred(o, desc):
                o["dtype"] = "bf16"
        return ops
    return orig, build


def best_prefill(desc, hw, packages, prompt):
    best = None
    for pp, tp in R.mappings(packages, hw):
        r = R.prefill(desc, hw, packages, prompt, pp=pp, tp=tp)
        if best is None or r["time_s"] < best["time_s"]:
            best = dict(r, pp=pp, tp=tp)
    return best


def e1_cost(prompt=32768):
    """Prefill time if NVFP4 tensors always take the exact BF16 path. Decode is HBM-bound (arch.md §2)."""
    hw = HW.make()
    descs = SW.load_all()
    cases = [("minimax_m3_1_flash", "NVFP4 experts", lambda o, d: o["kind"] == "moe_experts"),
             ("deepseek_v4_1_flash", "NVFP4 global KV (attention core)", lambda o, d: o["kind"] == "attn")]
    out = []
    for key, what, pred in cases:
        d = descs[key]
        n, _ = SW.serving_packages(d, hw)
        base = best_prefill(d, hw, n, prompt)
        orig, patched = _force_bf16(pred)
        G.build = patched
        try:
            ex = best_prefill(d, hw, n, prompt)
        finally:
            G.build = orig
        out.append(dict(model=d["name"], what=what, packages=n, fold_s=base["time_s"], exact_s=ex["time_s"]))
    return out


# ------------------------------------------------------------------ E2
def mhc_model(variant, alpha, seed, stream_bf16, d=256, n=4, L=80, T=256, V=4096, ff=512):
    """Toy 80-sublayer mHC network (40 layers x 2 sublayers), per-token MLP sublayers (attention mixing does
    not change how mixer rounding propagates). Mixer per hyper_connections.md §1 with the mHC-paper split:
    pre = sigmoid(a m + b), post = 2 sigmoid(a m), comb = Sinkhorn(exp(a m + b_res), 20 iterations)."""
    rng = np.random.default_rng(seed)
    phi = [(rng.standard_normal((n * d, 24)) / np.sqrt(n * d)).astype(f32) for _ in range(L)]
    bpre = np.full(4, np.log(1 / 3), f32)                         # sigmoid -> 0.25
    bres = (np.eye(4) * 4.0 - 2.0).astype(f32).ravel()             # comb starts near identity
    W1 = [(rng.standard_normal((d, ff)) / np.sqrt(d)).astype(f32) for _ in range(L)]
    W2 = [(rng.standard_normal((ff, d)) / np.sqrt(ff) * 0.5).astype(f32) for _ in range(L)]
    E = rng.standard_normal((T, d)).astype(f32)
    head = (rng.standard_normal((d, V)) / np.sqrt(d) * 4).astype(f32)
    st = (lambda a: nm.bf16(a).astype(f32)) if stream_bf16 else (lambda a: a)
    x = st(np.repeat(E[:, None, :], n, axis=1))
    for li in range(L):
        v = x.reshape(T, n * d)
        P = phi[li]
        vd = v.astype(np.float64)
        if variant == "null":                     # FP32 with the K order reversed: the benign-noise floor
            m = (v[:, ::-1].astype(f32) @ P[::-1].astype(f32)).astype(f32)
        elif variant == "FP32":
            m = (vd @ P.astype(np.float64)).astype(f32)
        elif variant == "BF16":
            m = (vd @ nm.bf16(P)).astype(f32)
        elif variant == "BF16 hi/lo":
            hi, lo = nm.bf16_split(P)
            m = (vd @ lo + vd @ hi).astype(f32)
        elif variant == "E4M3":
            el, sc = nm.e4m3_row_quant(P.T)
            m = (vd @ (el * sc).T).astype(f32)
        m = m * (1.0 / np.sqrt(np.mean(v.astype(f32) ** 2, axis=1, keepdims=True) + 1e-6)).astype(f32)
        pre = 1 / (1 + np.exp(-(alpha * m[:, :4] + bpre)))
        post = 2 / (1 + np.exp(-(alpha * m[:, 4:8])))
        c = np.exp(alpha * m[:, 8:] + bres).reshape(T, 4, 4)
        for _ in range(20):
            c = c / c.sum(2, keepdims=True)
            c = c / c.sum(1, keepdims=True)
        y = np.einsum("ti,tid->td", pre, x)
        y = y / np.sqrt(np.mean(y ** 2, 1, keepdims=True) + 1e-6)
        h = y @ W1[li]
        yo = st((h / (1 + np.exp(-h))) @ W2[li])
        x = st(post[:, :, None] * yo[:, None, :] + np.einsum("tij,tid->tjd", c, x))
    z = x.mean(1)
    z = z / np.sqrt(np.mean(z ** 2, 1, keepdims=True) + 1e-6)
    return (z @ head).astype(np.float64)


def kl(a, b):
    la = a - a.max(1, keepdims=True)
    la = la - np.log(np.exp(la).sum(1, keepdims=True))
    lb = b - b.max(1, keepdims=True)
    lb = lb - np.log(np.exp(lb).sum(1, keepdims=True))
    return float(np.mean(np.sum(np.exp(la) * (la - lb), 1)))


def entropy(a):
    la = a - a.max(1, keepdims=True)
    la = la - np.log(np.exp(la).sum(1, keepdims=True))
    return float(np.mean(-np.sum(np.exp(la) * la, 1)))


E2_VARIANTS = ("null", "BF16", "BF16 hi/lo", "E4M3")


def e2(seeds=3):
    rows = []
    for stream_bf16 in (True, False):
        for alpha in (1.0, 4.0):
            res = {v: [] for v in E2_VARIANTS}
            top1 = []
            ent = []
            for seed in range(seeds):
                ref = mhc_model("FP32", alpha, seed, stream_bf16)
                ent.append(entropy(ref))
                for v in E2_VARIANTS:
                    out = mhc_model(v, alpha, seed, stream_bf16)
                    res[v].append(kl(ref, out))
                    if v == "BF16":
                        top1.append(float(np.mean(ref.argmax(1) == out.argmax(1))))
            rows.append(dict(stream="BF16" if stream_bf16 else "FP32", alpha=alpha, H=float(np.mean(ent)),
                             top1_bf16=float(np.mean(top1)), **{v: float(np.mean(res[v])) for v in E2_VARIANTS}))
    return rows


# ------------------------------------------------------------------ E3
def sr_bf16(x, rng):
    b = x.astype(f32).view(np.uint32).astype(np.uint64)
    r = rng.integers(0, 1 << 16, size=x.shape, dtype=np.uint64)
    return (((b + r) >> 16) << 16).astype(np.uint32).view(f32)


E3_MEM = {"short (alpha 0.90-0.99)": (-2, -1), "mixed (alpha 0.90-0.9999)": (-4, -1), "long (alpha 0.999-0.9999)": (-4, -3)}


def e3_run(T, mem, chunk=1, H=4, dk=128, dv=128, seed=0):
    """Gated delta rule with a per-key-channel decay (KDA; Gated DeltaNet is the scalar case):
    S_t = diag(a_t) S_{t-1};  S_t += beta_t k_t (v_t - S_t^T k_t)^T;  o_t = S_t^T q_t;  q, k L2-normalized.
    The state is rounded to BF16 every `chunk` tokens (1 = decode: read + write per token)."""
    rng = np.random.default_rng(seed)
    lo, hi = E3_MEM[mem]
    la = -np.power(10.0, rng.uniform(lo, hi, (H, dk)))
    S = {k: np.zeros((H, dk, dv), np.float64 if k == "ref" else f32) for k in ("ref", "FP32", "BF16", "BF16 SR")}
    srng = np.random.default_rng(99)
    err = {k: [] for k in ("FP32", "BF16", "BF16 SR", "BF16 output")}
    for t in range(T):
        k = rng.standard_normal((H, dk))
        k /= np.linalg.norm(k, axis=1, keepdims=True)
        q = rng.standard_normal((H, dk))
        q /= np.linalg.norm(q, axis=1, keepdims=True)
        v = rng.standard_normal((H, dv))
        beta = 1 / (1 + np.exp(-rng.standard_normal((H, 1))))
        a = np.exp(la * (1 + rng.normal(0, 0.1, (H, dk))))
        outs = {}
        for key in S:
            dt = np.float64 if key == "ref" else f32
            St = S[key] * a[:, :, None].astype(dt)
            kk = k.astype(dt)
            pred = np.einsum("hk,hkv->hv", kk, St)
            St = St + beta.astype(dt)[:, :, None] * kk[:, :, None] * (v.astype(dt) - pred)[:, None, :]
            outs[key] = np.einsum("hk,hkv->hv", q.astype(dt), St).astype(np.float64)
            if (t + 1) % chunk == 0:
                if key == "BF16":
                    St = nm.bf16(St).astype(f32)
                elif key == "BF16 SR":
                    St = sr_bf16(St, srng)
            S[key] = St
        r = outs["ref"]
        n = np.linalg.norm(r)
        for key in ("FP32", "BF16", "BF16 SR"):
            err[key].append(np.linalg.norm(outs[key] - r) / n)
        err["BF16 output"].append(np.linalg.norm(nm.bf16(r) - r) / n)
    w = T // 8
    return {k: (float(np.sqrt(np.mean(np.square(e[:w])))), float(np.sqrt(np.mean(np.square(e[-w:])))))
            for k, e in err.items()}


def e3(T=8192):
    rows = []
    for mem in E3_MEM:
        rows.append(dict(mem=mem, chunk=1, **e3_run(T, mem)))
    rows.append(dict(mem="long (alpha 0.999-0.9999)", chunk=64, **e3_run(T, "long (alpha 0.999-0.9999)", chunk=64)))
    return rows


def e3_gain():
    """Decode at the serving point with the recurrent state halved (BF16) vs FP32, for the linear-attention models."""
    hw = HW.make()
    out = []
    orig = G.build

    def half_state(desc, *a, **kw):
        ops = orig(desc, *a, **kw)
        for o in ops:
            if o["kind"] == "linear_scan":
                o["kv_bytes"] *= 0.5
        return ops
    for key, d in SW.load_all().items():
        if not any(o["kind"] == "linear_scan" for o in orig(d, "decode", 1, 1024)):
            continue
        n, base = SW.serving_packages(d, hw)
        wd = SW.deploy_dtype(d, "fp8")
        G.build = half_state
        try:
            r = R.best_decode(d, hw, n, SW.SERVE["batch"], SW.SERVE["ctx"], weight_dtype=wd)
        finally:
            G.build = orig
        st = sum(o["kv_bytes"] for o in orig(d, "decode", SW.SERVE["batch"], SW.SERVE["ctx"]) if o["kind"] == "linear_scan")
        out.append(dict(model=d["name"], packages=n, state_gb=st / 1e9, base=base["tok_s_user"], bf16=r["tok_s_user"]))
    return out


# ------------------------------------------------------------------ E4
def e4(M=16, N=64, K=16384):
    """Operands as the MXU sees them: FP8 elements spread over the E4M3 range by their scales (E4M3_ROW
    activations, folded weights); BF16 activations and weights of normal magnitude."""
    rng = np.random.default_rng(4)
    rows = []
    for mode in ("FP8", "BF16"):
        if mode == "FP8":
            A, _ = nm.e4m3_row_quant(rng.standard_normal((M, K)))
            v, _ = nm.fold_e4m3(rng.standard_normal((N, K)))
            W = v.T
        else:
            A = nm.bf16(rng.standard_normal((M, K)))
            W = nm.bf16(rng.standard_normal((K, N)) * 0.05)
        ref = A @ W                                       # float64; exact for the FP8 case
        y = nm.mxu_mm(A, W, mode)
        seq = np.zeros((M, N), f32)                       # naive FP32 loop, one product at a time
        for k in range(K):
            seq = (seq + (A[:, k:k + 1] * W[k:k + 1, :]).astype(f32)).astype(f32)
        scale = np.spacing(np.float32(np.max(np.abs(ref))))
        rows.append(dict(mode=mode, K=K, mxu=rel(y, ref), seq=rel(seq.astype(np.float64), ref),
                         maxulp=float(np.max(np.abs(y - ref)) / scale), maxulp_seq=float(np.max(np.abs(seq - ref)) / scale)))
    return rows


# ------------------------------------------------------------------ report
def fmt_e(x):
    return "-" if x != x else f"{x:.1e}"


# DMA weight path widths (microarch.md sections 2.2, 2.5; review finding I-3)
DMA_CVT_ELEMS = 256        # converter output, elements per cycle
DMA_SPM_WR = 512           # DMA SPM write port, B/cycle: converted data plus the block scales staged before it (review V-1)
MXU_WLOAD = 512            # MXU stationary-load port, B/cycle
# (path, source B/element, destination B/element, staged scale B/element)
FEED_PATHS = [("MXFP4 -> E4M3 (fold)", 0.53125, 1, 1 / 32), ("NVFP4 -> E4M3 (fold)", 0.5625, 1, 1 / 16),
              ("NVFP4 -> BF16 (exact, D1)", 0.5625, 2, 1 / 16), ("MXFP8 -> E4M3 (fold)", 1.03125, 1, 1 / 32),
              ("FP8, no conversion", 1.0, 1, 0)]


def main():
    r1 = e1()
    c1 = e1_cost()
    r2 = e2()
    r3 = e3()
    g3 = e3_gain()
    r4 = e4()
    hw = HW.make()
    L = ["# Phase 2 numerics experiments", "",
         "Generated by `make numerics` (`sim/analytic/numerics_exp.py`, golden model `sw/npumodel/numerics.py`). Do not edit by hand.",
         "Decisions drawn from these numbers are in [`docs/spec/numerics.md`](../spec/numerics.md) N-7. All inputs are synthetic with fixed seeds.", ""]
    L += ["## E1. Block-scale fold into E4M3 (`cvt *_E4M3`) vs the exact BF16 path", "",
          "Weights `[256 x 6144]` (MiniMax expert `w1` input width), quantized with the published recipes (NVFP4: FP32 tensor scale "
          "amax/(448*6), E4M3 scale per 16; MX: OCP v1.0 shared exponent per 32). The fold writes E4M3 elements and one power-of-two FP32 "
          "column scale per (row, 256-deep K tile). Activations: 64 BF16 rows with 16 outlier channels (x30).",
          "Errors are relative Frobenius norms. `fold W`: folded vs exact dequantized weights. GEMM columns are relative to "
          "`X_bf16 @ W_exact`: `fold`: folded weights, BF16 activations (isolates the fold); `A8`: E4M3 per-row activations (what every FP8 "
          "GEMM pays); `fold+A8`: the MXU FP8 path; `A4`: NVFP4 activations (W4A4, as MiniMax is served on Blackwell); `BF16 path`: "
          "`cvt NVFP4_BF16` through the bit-accurate MXU model.", "",
          "| format | weights | elements changed | quantization error (vs original) | fold W | GEMM: fold | A8 | fold+A8 | A4 | BF16 path |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in r1:
        L.append(f"| {r['fmt']} | {r['dist']} | {r['changed']:.2%} | {r['quant']:.1e} | {fmt_e(r['fold_w'])} | {fmt_e(r['fold_y'])} | "
                 f"{r['a8']:.1e} | {r['fold_a8']:.1e} | {fmt_e(r['a4'])} | {fmt_e(r['bf16'])} |")
    per_core = HW.peaks(hw)["hbm_bw"] / hw["cores"] / hw["clock_hz"]
    L += ["", f"Decode feed margin: a core's HBM share is {per_core:.0f} B/cycle at {hw['hbm_eff']:.0%} "
          f"({per_core / hw['hbm_eff']:.0f} at peak). Every stage of the weight path must carry "
          f"that many elements per cycle: the DMA converter ({DMA_CVT_ELEMS} elements/cycle), the DMA SPM write port "
          f"({DMA_SPM_WR} B/cycle, which also writes the block scales into the staging ring before the data that uses them, "
          f"review finding V-1) and the MXU stationary-load port ({MXU_WLOAD} B/cycle). Margin = narrowest stage / need "
          "(microarch.md section 2.5, review findings I-3, V-1).", "",
          "| path | source B/element | need (elements/cycle) | converter | SPM write | stationary load | margin |",
          "|---|---|---|---|---|---|---|"]
    for name, src_b, dst_b, sc_b in FEED_PATHS:
        need = per_core / src_b
        cap = {"wr": DMA_SPM_WR / (dst_b + sc_b), "ld": MXU_WLOAD / dst_b}
        if "no conversion" not in name:
            cap["cvt"] = DMA_CVT_ELEMS
        L.append(f"| {name} | {src_b:g} | {need:.0f} | {cap['cvt'] if 'cvt' in cap else '-'} | {cap['wr']:.0f} | {cap['ld']:.0f} | "
                 f"{min(cap.values()) / need:.2f}x |")
    need = per_core / 0.5625
    L += ["", f"Before the I-3 fix the DMA SPM write port was 256 B/cycle: NVFP4 -> BF16 then carried {256 / 2 / need:.2f}x of the need. "
          "Decode stays HBM-bound on every path above.", "",
          "Prefill of one 32K-token prompt at the serving package count (best PP/TP mapping) if the NVFP4 tensors always take the BF16 path:", "",
          "| model | NVFP4 tensors | packages | fold (FP8 rate) | exact (BF16 rate) | slowdown |", "|---|---|---|---|---|---|"]
    for c in c1:
        L.append(f"| {c['model']} | {c['what']} | {c['packages']} | {c['fold_s'] * 1e3:.0f} ms | {c['exact_s'] * 1e3:.0f} ms | "
                 f"{c['exact_s'] / c['fold_s'] - 1:+.0%} |")
    L += ["", "## E2. Hyper-connection mixer precision (HC-2)", "",
          "Toy 80-sublayer mHC network (d = 256, 4 streams, 20 Sinkhorn iterations, vocabulary 4096, 256 tokens, 3 seeds); only the "
          "`[4d x 24]` mixer projection changes between variants. `alpha` scales the mixer logits (1: typical, 4: stress). Mean KL(FP32 "
          "reference || variant) of the final next-token distribution. `null` = FP32 with the summation order reversed (benign-noise floor). "
          "`BF16 hi/lo` = two BF16 passes over `W_lo` then `W_hi` (the stream is exact in BF16, so this equals `BF16X3`). "
          "`stream FP32` removes the BF16 rounding of the residual stream to expose each variant's own contribution.", "",
          "| stream | alpha | entropy (nats) | null | BF16 | BF16 hi/lo | E4M3 | top-1 agreement, BF16 |", "|---|---|---|---|---|---|---|---|"]
    for r in r2:
        L.append(f"| {r['stream']} | {r['alpha']:.0f} | {r['H']:.2f} | {r['null']:.1e} | {r['BF16']:.1e} | {r['BF16 hi/lo']:.1e} | "
                 f"{r['E4M3']:.1e} | {r['top1_bf16']:.1%} |")
    L += ["", "## E3. BF16 recurrent state (gated delta rule)", "",
          "4 heads of 128 x 128, 8192 tokens, per-key-channel decay drawn per head channel (log-uniform in the range given), "
          "L2-normalized q and k, beta = sigmoid(N(0, 1)). Output error relative to a float64 reference, RMS over the first / last 1024 "
          "tokens. `chunk 1`: the state is rounded every token (decode writes it back each step); `chunk 64`: once per 64-token chunk "
          "(chunked prefill). `BF16 output`: the error of rounding the exact output to BF16, i.e. what the next layer sees anyway.", "",
          "| decay | chunk | FP32 state | BF16 state (RNE) | BF16 state (stochastic) | BF16 output rounding |", "|---|---|---|---|---|---|"]
    for r in r3:
        L.append(f"| {r['mem']} | {r['chunk']} | " + " | ".join(f"{r[k][0]:.1e} / {r[k][1]:.1e}" for k in ("FP32", "BF16", "BF16 SR", "BF16 output")) + " |")
    L += ["", "Decode at the serving point (64 x 128K, fp8 deployment, serving package count) with the state in BF16 instead of FP32:", "",
          "| model | packages | state traffic per step (GB) | tokens/s/user FP32 | BF16 | gain |", "|---|---|---|---|---|---|"]
    for g in g3:
        L.append(f"| {g['model']} | {g['packages']} | {g['state_gb']:.1f} | {g['base']:.1f} | {g['bf16']:.1f} | {g['bf16'] / g['base'] - 1:+.1%} |")
    L += ["", "## E4. MXU accumulation over long K", "",
          "`[16 x 16384] x [16384 x 64]`. FP8: E4M3 per-row activations times folded weights (both use the full E4M3 range); BF16: normal inputs. Relative error vs float64 for the specified PE order "
          "(numerics.md N-3) and for a naive one-product-at-a-time FP32 loop; max error in FP32 ulps of the largest |result|.", "",
          "| mode | K | MXU order | naive FP32 sequential | max error, MXU (ulp) | max error, naive (ulp) |", "|---|---|---|---|---|---|"]
    for r in r4:
        L.append(f"| {r['mode']} | {r['K']} | {r['mxu']:.1e} | {r['seq']:.1e} | {r['maxulp']:.1f} | {r['maxulp_seq']:.1f} |")
    with open(REPORT, "w") as f:
        f.write("\n".join(L) + "\n")
    print(f"wrote {REPORT}")


if __name__ == "__main__":
    main()
