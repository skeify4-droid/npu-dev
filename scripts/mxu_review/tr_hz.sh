#!/bin/bash
# Review part TR: survivors of tr_mutants.py against part H's random environment (hz_run.py: independent of arith_ref.py and
# gen_mxu_vectors.py; 256 KiB SPM, 256 / 512 ACC rows at DIM 8 / 16). A kill shows a real defect that make unit misses.
# Usage: scripts/memrun tr_hz scripts/mxu_review/tr_hz.sh NAME ...   (needs build/mxu_review_tr/mut/<name>/rtl from tr_mutants.py run)
ROOT=$(cd "$(dirname "$0")/../.." && pwd); cd "$ROOT"
CFG="d8_l2_a8 d8_l1_a1 d8_l3_a3 d8_l5_a13 d16_l2_a8 d16_l1_a3"
for n in "$@"; do
  out=$(python3 scripts/mxu_review/hz_run.py --rtl build/mxu_review_tr/mut/$n --cfg $CFG --seeds 6 --seed0 900 --jobs 8 --quiet --stop 2>&1)
  if echo "$out" | grep -q "^FAIL"; then echo "$n	KILLED_BY_HZ	$(echo "$out" | grep -m1 -A1 '^FAIL' | tail -1 | sed 's/^ *//' | cut -c1-200)"
  else echo "$n	survives_hz	$(echo "$out" | tail -1 | cut -c1-120)"; fi
done
