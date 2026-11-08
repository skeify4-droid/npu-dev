#!/usr/bin/env python3
"""Pitch scan behind mxu.md M-17 / memmap.md section 4: which W row pitches load a FULL stationary tile in the port-limited
32 cycles for all three beat shapes of mxu.md section 4.3 (trans, non-trans FP8, non-trans BF16), at 2 KiB-aligned tile bases.

    python3 scripts/mxu_review/pitch_scan.py            # report on stdout (needs numpy, imported by spec_checks)

The beat model is spec_checks.py's (transcribed from mxu.md section 4.3, bank hash from memmap.md section 4); the test
tb/unit/test_isa.py::test_mxu_load_beat_pitches ties this module to isa.spm_bank and asserts the results below.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spec_checks as S  # noqa: E402

SHAPES = {"trans": S.trans_tile, "non-trans FP8": S.nontrans_fp8_tile, "non-trans BF16": S.nontrans_bf16_tile}
BASES_2K = [i * 2048 for i in range(32)]          # the hash repeats every 64 KiB
PADDED = [3072, 4608, 8704, 17408] + [(1 << k) + 512 for k in range(15, 18)]    # the rule of memmap.md section 4


def tile_cycles(pitch, bases=BASES_2K, ts=range(2), js=range(2)):
    """Worst cycles per tile over bases and tile positions, per beat shape (32 = conflict-free)."""
    return {n: S.load_cycles(sh, pitch, bases, ts, js)[0] for n, sh in SHAPES.items()}


def conflict_free_pitches(lo=64, hi=16384, step=64):
    return [p for p in range(lo, hi + 1, step) if all(v == 32 for v in tile_cycles(p).values())]


def main():
    ok = conflict_free_pitches()
    print(f"conflict-free pitches 64 B..16 KiB (64 B steps, 2 KiB-aligned bases): {len(ok)} of 256")
    print("  " + ", ".join(map(str, ok)))
    print(f"{'pitch':>8s}  " + "  ".join(f"{n:>14s}" for n in SHAPES))
    for p in (2048, 2560, 3072, 4096, 4608, 8192, 8704, 16384, 17408, 32768, 33280, 65536, 66048, 131072, 131584):
        c = tile_cycles(p)
        print(f"{p:8d}  " + "  ".join(f"{c[n]:14d}" for n in SHAPES) + ("   padded rule" if p in PADDED else ""))


if __name__ == "__main__":
    main()
