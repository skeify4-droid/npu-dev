#!/bin/bash
# Review part T: the survivors of tq_mutants.py (make unit) against the part H random environment (hz_run.py, independent of
# arith_ref.py and gen_mxu_vectors.py). A kill here shows that the mutant is a real defect that make unit misses (a test gap,
# not an equivalent change). Needs the mutated copies of `tq_mutants.py run` (build/mxu_review_tq/mut/<name>/rtl).
# Usage: scripts/memrun tq_hz scripts/mxu_review/tq_hz.sh [NAME ...]     (about 40 min for the 31 survivors, 8 jobs, < 3 GB)
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
RES=build/mxu_review_tq/results_uniq.tsv
NAMES=${*:-$(awk -F'\t' '$4 == "survived" {print $1}' $RES)}
CFG="d8_l2_a8 d8_l1_a1 d8_l3_a3 d8_l5_a13 d16_l2_a8 d16_l1_a3"
for n in $NAMES; do
  out=$(python3 scripts/mxu_review/hz_run.py --rtl build/mxu_review_tq/mut/$n --cfg $CFG --seeds 6 --seed0 700 --jobs 8 --quiet --stop 2>&1)
  if echo "$out" | grep -q "^FAIL"; then
    echo "$n	KILLED_BY_HZ	$(echo "$out" | grep -m1 -A1 '^FAIL' | tail -1 | sed 's/^ *//' | cut -c1-200)"
  else
    echo "$n	survives_hz	$(echo "$out" | tail -1 | cut -c1-120)"
  fi
done
