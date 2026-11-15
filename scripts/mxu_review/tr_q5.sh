#!/bin/bash
# Review part TR, question 5: author's kill mutants (scripts/mxu_mutants/mxu_mutants.py) in --quick and in full mode; sample of 10 of the
# 55 `kill` mutants drawn with random.Random(20261115).sample. Usage: scripts/memrun tr_q5_<k> scripts/mxu_review/tr_q5.sh NAME ...
cd "$(dirname "$0")/../.." || exit 1
python3 scripts/mxu_mutants/mxu_mutants.py tb --quick "$@" | sed 's/^/QUICK /'
python3 scripts/mxu_mutants/mxu_mutants.py tb "$@" | sed 's/^/FULL  /'
