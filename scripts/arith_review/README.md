# Reviewer's checks of `rtl/arith`

These scripts produced the evidence in [docs/reports/arith_review.md](../../docs/reports/arith_review.md). They are written
independently of `sw/npumodel/arith_ref.py`. Keep it that way: do not import `arith_ref` into `indep.py` or derive one from the
other. Outputs go to `build/arith_review/` (generated). Run one at a time; heavy runs under `scripts/memrun`.

| File | What it does | Typical run |
|---|---|---|
| `indep.py` | Independent oracle written from numerics.md: exact `Fraction` values, its own decoders, RNE with subnormal grid / saturation / FTZ, flags. Rules numerics.md leaves open are marked `INTERP`. | imported by the others |
| `check_files.py` | Recomputes every row of `build/arith/*.hex` with `indep.py` and counts disagreements. Agreement on all 2,209,152 rows at `815acb1`. | `scripts/memrun arith_indep python3 scripts/arith_review/check_files.py [DIR] [name ...]` (about 30 s) |
| `handpicked.py` | The hand cases (265 of the review plus the ACC cases of F3 and the BF16 subnormal conversions of F6: 279). The literals are in `scripts/arith_hand_cases.py` (data only); `gen_arith_vectors.py` puts them into `build/arith/*.hex` with the expected values of `arith_ref`, and `test_arith.py` asserts them against `arith_ref`. This script checks them against `arith_ref` and `indep.py`. | `python3 scripts/arith_review/handpicked.py` |
| `gbound.py` | Targeted vectors for the `fp_addu` guard-bit bound (one-bit cancellation, d = 1..60, near-tie mantissas) for `fp32_add` and `bf16_dot2`. | `python3 scripts/arith_review/gbound.py`, then `run_tb.sh` with an RTL copy at another `.G(...)` |
| `my_mutants.py` | The reviewer's 44 mutants (`predicted` column: kill, equiv, unreachable). Default vectors are `build/arith`. | `scripts/memrun arith_review_mutants python3 scripts/arith_review/my_mutants.py [VECDIR] [name ...]` (about 6 min) |
| `run_tb.sh` | Builds `tb/unit/tb_<block>.sv` against any RTL directory (`-I` the vector directory, for `arith/counts.svh`) and runs it from a vector directory (read only). A missing, short or long vector file is a FAIL. | `scripts/arith_review/run_tb.sh <rtl_dir> <dir with arith/*.hex> <block...>` |

Expected status after the fixes from the review:
- `dot_width38` is killed once F5's corner sums are in the vectors.
- `pe_flags_norst` and `pe_fdot_norst` are killed once F4's reset test exists.
- `foldq_sat_off` is killed (F10: `fold_quant` is tested alone with `c` below the element's own, `foldq_*.hex`).
- The eight `equiv` mutants survive. `int_rs24` is now `int_rs25`: the F9 fix is in the RTL (`rs > 24`), so the mutant restores the
  old `rs > 25` and is equivalent.

Notes:
- The hand cases are folded into `gen_arith_vectors.py`; `handpicked.py` no longer writes hex files.
- `indep.py` carries two changes after the review: the F3 flag rule (NAN_IN only from external operands of `acc_update`) and the
  F6 decision (FP32 -> BF16 keeps subnormals, `indep.py:249`; it had followed the old N-2 text that flushed them).
- `gbound.py` writes `arith/counts.svh` next to its hex files so the count-checking testbenches accept them.
