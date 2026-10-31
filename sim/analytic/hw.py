"""Hardware parameters of the baseline (docs/PLAN.md 2.2, frozen in docs/spec/arch.md).

All rates are per die unless named per package. Efficiencies turn peaks into what the roofline uses:
`mxu_eff` for dense MXU work, `hbm_eff` for sustained HBM bandwidth (refresh, bank conflicts, page misses).
"""
import copy

BASE = {
    "clock_hz": 1.5e9,
    "cores": 32,
    "mxu_fp8_macs_per_cycle": 64 * 64 * 4,   # 64x64 PEs, 4-way FP8 dot product each
    "bf16_rate": 0.5,                        # BF16 MACs at half the FP8 rate
    "vpu_flops_per_cycle": 512,              # per core: 16 lanes x 16 fp32 x FMA (assumption, frozen in arch.md)
    "sfu_ops_per_cycle": 128,                # per core: exp / reciprocal / rsqrt; 128 keeps head_dim-64 attention MXU-bound (arch.md)
    "stacks": 4,
    "stack_bw": 1024 * 9.6e9 / 8,            # HBM3e 1024-bit at 9.6 Gb/s = 1.2288 TB/s
    "stack_bytes": 36e9,                     # 12-Hi, 24 Gb dies
    "hbm_eff": 0.85,
    "mxu_eff": 0.80,
    "hbm_usable": 0.92,                      # capacity left after page tables, code, firmware, fragmentation
    "workspace_bytes": 4e9,                  # activations, MoE dispatch buffers, collective staging per die
    "sram_bytes": 32 * 4e6 + 32e6,           # scratchpads + global SRAM
    "dies_per_pkg": 2,
    "d2d_bw": 5e12, "d2d_lat": 0.5e-6,       # per direction, die to die in a package
    "c2c_bw_pkg": 16 * 100e9, "c2c_lat": 2.0e-6,   # 16 ports x 100 GB/s per direction per package; per collective step
    "overlap": 0.0,                          # fraction of communication hidden under compute (0 = none, conservative)
}


def make(**over):
    hw = copy.deepcopy(BASE)
    for k, v in over.items():
        if k not in hw:
            raise KeyError(k)
        hw[k] = v
    return hw


def peaks(hw):
    """Per-die sustained rates used by the roofline."""
    fp8 = hw["cores"] * hw["mxu_fp8_macs_per_cycle"] * 2 * hw["clock_hz"]
    return {
        "fp8": fp8 * hw["mxu_eff"], "bf16": fp8 * hw["bf16_rate"] * hw["mxu_eff"],
        "vpu": hw["cores"] * hw["vpu_flops_per_cycle"] * hw["clock_hz"],
        "sfu": hw["cores"] * hw["sfu_ops_per_cycle"] * hw["clock_hz"],
        "hbm_bw": hw["stacks"] * hw["stack_bw"] * hw["hbm_eff"],
        "hbm_bytes": hw["stacks"] * hw["stack_bytes"],
        "peak_fp8": fp8, "peak_hbm_bw": hw["stacks"] * hw["stack_bw"],
    }


def ops_per_byte(hw):
    p = peaks(hw)
    return p["fp8"] / p["hbm_bw"]
