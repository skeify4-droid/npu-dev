#!/bin/bash
# Review finding I-3 (adapted by the author, 2026-11-12, after the fix): the decoder BEFORE the fix against the decoder NOW, tb_dec_miter.sv
# unchanged, DIM 8 and 64. Gold ("mxu_dec" in the testbench) = rtl/core/mxu_dec.sv of the reviewed commit (OLD_REF, default 0255c28),
# fix ("mxu_dec_fix") = the current rtl/core/mxu_dec.sv with the module renamed. The reviewer's version compared the file in the tree
# with its own narrowed copy (fix_dec_narrow.py); that script is no longer used, the tree is the fix.
# Usage: scripts/memrun rev_dec_miter scripts/mxu_review/run_dec_miter.sh [N]
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd); cd "$ROOT"
N=${1:-200000}
OLD_REF=${OLD_REF:-0255c28}
F=build/mxu_review/fix; mkdir -p "$F"
git show "$OLD_REF:rtl/core/mxu_dec.sv" > $F/mxu_dec_old.sv
sed 's/^module mxu_dec #(/module mxu_dec_fix #(/' rtl/core/mxu_dec.sv > $F/mxu_dec_fix.sv
grep -q '^module mxu_dec_fix' $F/mxu_dec_fix.sv
for D in 8 64; do
  iverilog -g2012 -P tb_dec_miter.DIM=$D -o $F/miter_$D.vvp isa/gen/isa_pkg.sv $F/mxu_dec_old.sv $F/mxu_dec_fix.sv \
    ${TB:-scripts/mxu_review/tb_dec_miter.sv} 2>&1 | grep -v "constant selects" || true
  for S in 1 2; do echo "== DIM $D seed $S"; vvp -n $F/miter_$D.vvp +N=$N +SEED=$S | grep -v finish; done
done
