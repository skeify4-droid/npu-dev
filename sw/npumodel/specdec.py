"""Speculative-decoding acceptance model (Phase 1).

A round drafts `gamma` tokens, the target verifies gamma + 1 positions in one pass and keeps the accepted prefix
plus one bonus token. With conditional acceptance p_k at draft position k (the probability that token k is
accepted given tokens 1..k-1 were), the expected tokens emitted per round is

    tau(gamma) = 1 + sum_{k=1..gamma} prod_{j<=k} p_j

Profiles (p_k = min(1, p1 * decay**(k-1))) are fitted to the DSpark paper (arXiv 2607.05147), Table 1, Qwen3-8B
target, gamma = 7, averaged over the three benchmarks of each domain:
  dspark  semi-autoregressive (parallel backbone + Markov head): flat conditional acceptance (paper Fig. 2)
  dflash  parallel block drafter: position-1 acceptance from Fig. 2 (0.88 math, 0.87 code, 0.72 chat), then decay
  mtp     chained MTP modules drafting one token per step; modeled with the autoregressive Eagle3 numbers
          (flat; Fig. 2 shows it stable or rising). Sanity check: DeepSeek-V3 reports 85-90 % acceptance of the
          second token with MTP-1; the fitted values (0.84 math, 0.80 code, 0.62 chat) are below it, so conservative.
The DFlash fit decays faster than Fig. 2 shows for code (0.87 -> 0.71 at position 7 vs 0.78) because Fig. 2 is
measured on Qwen3-4B and Table 1 is averaged on Qwen3-8B; it matches tau, which is what the model uses.
These are a target model of 8 B parameters; acceptance of the 11 target models is not published, so the
domain is a sweep parameter.
"""
TABLE1_QWEN3_8B = {  # accepted length per round incl. the bonus token, gamma = 7
    "eagle3": {"math": (5.30, 4.77, 3.91), "code": (3.96, 4.33, 4.17), "chat": (2.66, 2.54, 2.54)},
    "dflash": {"math": (5.33, 4.91, 4.07), "code": (4.36, 4.64, 4.39), "chat": (3.11, 2.98, 2.81)},
    "dspark": {"math": (6.17, 5.78, 5.01), "code": (5.16, 5.52, 5.17), "chat": (3.72, 3.58, 3.21)},
}
DFLASH_P1 = {"math": 0.88, "code": 0.87, "chat": 0.72}
GAMMA_FIT = 7
METHOD_TABLE = {"dspark": "dspark", "dflash": "dflash", "mtp": "eagle3"}


def accepted_length(p1, decay, gamma):
    tau, surv = 1.0, 1.0
    for k in range(gamma):
        surv *= min(1.0, p1 * decay ** k)
        tau += surv
    return tau


def _bisect(f, lo, hi, target, it=60):
    for _ in range(it):
        mid = (lo + hi) / 2
        if f(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def fit_profiles():
    """{method: {domain: (p1, decay)}} fitted to Table 1."""
    out = {}
    for method, tab in METHOD_TABLE.items():
        out[method] = {}
        for dom, vals in TABLE1_QWEN3_8B[tab].items():
            tau = sum(vals) / len(vals)
            if method == "dflash":
                p1 = DFLASH_P1[dom]
                d = _bisect(lambda x: accepted_length(p1, x, GAMMA_FIT), 0.5, 1.0, tau)
                out[method][dom] = (p1, d)
            else:
                p = _bisect(lambda x: accepted_length(x, 1.0, GAMMA_FIT), 0.0, 1.0, tau)
                out[method][dom] = (p, 1.0)
    return out


PROFILES = fit_profiles()


def tau(method, domain, gamma):
    """Expected tokens per round for `gamma` draft tokens (gamma = 0: plain decode, 1 token)."""
    if not method or gamma <= 0:
        return 1.0
    p1, d = PROFILES[method][domain]
    return accepted_length(p1, d, gamma)
