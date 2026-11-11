#!/bin/bash
# Review finding I-4: the author's mxu_dec against the narrowed copy (fix_dec_narrow.py), tb_dec_miter.sv, DIM 8 and 64.
# Usage: scripts/memrun rev_dec_miter scripts/mxu_review/run_dec_miter.sh [N]
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd); cd "$ROOT"
N=${1:-200000}
F=build/mxu_review/fix
python3 scripts/mxu_review/fix_dec_narrow.py >/dev/null
for D in 8 64; do
  iverilog -g2012 -P tb_dec_miter.DIM=$D -o $F/miter_$D.vvp isa/gen/isa_pkg.sv rtl/core/mxu_dec.sv $F/mxu_dec_fix.sv \
    scripts/mxu_review/tb_dec_miter.sv 2>&1 | grep -v "constant selects" || true
  for S in 1 2; do echo "== DIM $D seed $S"; vvp -n $F/miter_$D.vvp +N=$N +SEED=$S | grep -v finish; done
done
