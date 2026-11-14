#!/bin/bash
# Review part T: tq_dec_dir.sv (directed legality edge cases on mxu_dec) on the RTL and on the decoder survivors of tq_mutants.py.
# Usage: scripts/mxu_review/tq_dec_dir.sh      (seconds; needs build/mxu_review_tq/mut/<name> from `tq_mutants.py run`)
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
B=build/mxu_review_tq/dec_dir; mkdir -p $B
for t in rtl dec_a_range_ge dec_w_range_ge dec_rs_range_ge dec_acc_ge dec_ka_bf16 dec_nt_floor dec_wait_unsigned; do
  src=$([ $t = rtl ] && echo rtl/core/mxu_dec.sv || echo build/mxu_review_tq/mut/$t/rtl/core/mxu_dec.sv)
  iverilog -g2012 -s tq_dec_dir -o $B/$t.vvp isa/gen/isa_pkg.sv $src scripts/mxu_review/tq_dec_dir.sv || exit 1
  echo "== $t"; vvp -n $B/$t.vvp | grep -E "differs|TQ_DEC_DIR"
done
