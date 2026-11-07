# Reviewer's checks of `rtl/arith`

These scripts produced the evidence in [docs/reports/arith_review.md](../../docs/reports/arith_review.md). They are written
independently of `sw/npumodel/arith_ref.py`. Keep it that way: do not import `arith_ref` into `indep.py` or derive one from the
other. Outputs go to `build/arith_review/` (generated). Run one at a time; heavy runs under `scripts/memrun`.

| File | What it does | Typical run |
|---|---|---|
| `indep.py` | Independent oracle written from numerics.md: exact `Fraction` values, its own decoders, RNE with subnormal grid / saturation / FTZ, flags. Rules numerics.md leaves open are marked `INTERP`. | imported by the others |
| `check_files.py` | Recomputes every row of `build/arith/*.hex` with `indep.py` and counts disagreements. Agreement on all 2,209,152 rows at `815acb1`. | `scripts/memrun arith_indep python3 scripts/arith_review/check_files.py [DIR] [name ...]` (about 30 s) |
| `handpicked.py` | 265 cases whose expected bits were worked out by hand (the literals are the oracle). Checks them against `arith_ref` and `indep.py`, then writes hex files padded with rows from `build/arith` so the existing testbenches accept them. | `python3 scripts/arith_review/handpicked.py` then `scripts/arith_review/run_tb.sh rtl/arith build/arith_review/hp fp32_add fp32_mul fp32_max fp8_dot4 bf16_dot2 cvt_fp32_fp cvt_fp32_int fold_quant` |
| `gbound.py` | Targeted vectors for the `fp_addu` guard-bit bound (one-bit cancellation, d = 1..60, near-tie mantissas) for `fp32_add` and `bf16_dot2`. | `python3 scripts/arith_review/gbound.py`, then `run_tb.sh` with an RTL copy at another `.G(...)` |
| `my_mutants.py` | The reviewer's 44 mutants (`predicted` column: kill, equiv, unreachable). Default vectors are `build/arith`. | `scripts/memrun arith_review_mutants python3 scripts/arith_review/my_mutants.py [VECDIR] [name ...]` (about 6 min) |
| `run_tb.sh` | Builds `tb/unit/tb_<block>.sv` against any RTL directory and runs it from a vector directory (read only). | `scripts/arith_review/run_tb.sh <rtl_dir> <dir with arith/*.hex> <block...>` |

Expected status after the fixes from the review:
- `dot_width38` is killed once F5's corner sums are in the vectors.
- `pe_flags_norst` and `pe_fdot_norst` are killed once F4's reset test exists.
- `foldq_sat_off` is killed or removed, depending on the F10 decision.
- The eight `equiv` mutants survive.

Notes:
- `handpicked.py` pads each file to 1,200 rows (1,500 tiles for the fold) to meet the old count checks. Once F1 adds exact
  counts, either fold the hand cases into `gen_arith_vectors.py` (preferred) or adjust the padding.
- `indep.py` composes `acc_update` naively and so shares finding F3. Update it together with the fix.
