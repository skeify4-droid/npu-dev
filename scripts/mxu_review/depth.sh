#!/bin/bash
# Review (implementability): longest combinational path, in generic gates after `synth` (ABC), of one module, flip-flops
# excluded (Yosys `ltp -noff`). The PE (`mxu_pe`, flattened) is the reference: its stages are what the 1.5 GHz clock was sized for.
# Usage: scripts/memrun rev_depth_<tag> scripts/mxu_review/depth.sh <top> <tag> [-set P V ...]
#   top: mxu_pe | mxu_dec | mxu_wpack | mxu_local (mxu with mxu_dec, mxu_wpack and mxu_array as blackboxes)
# Output: build/mxu_review/syn/<tag>.depth.txt (the ltp report: length and the cells of the path, with their source lines).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
TOP=$1; TAG=$2; shift 2
OUT=$ROOT/build/mxu_review/syn; mkdir -p "$OUT"
cd "$ROOT"
ARITH=$(ls rtl/arith/*.sv | sort | tr '\n' ' ')
BB=""
case $TOP in
  mxu_pe)    FILES="$ARITH" ;;
  mxu_dec)   FILES="isa/gen/isa_pkg.sv rtl/core/mxu_dec.sv" ;;
  mxu_dec_fix) FILES="isa/gen/isa_pkg.sv build/mxu_review/fix/mxu_dec.sv"; TOP=mxu_dec ;;   # fix_dec_narrow.py copy
  mxu_wpack) FILES=rtl/core/mxu_wpack.sv ;;
  mxu_local) FILES="rtl/core/mxu.sv"; TOP=mxu
             BB="read_verilog -sv isa/gen/isa_pkg.sv; read_verilog -sv -lib rtl/core/mxu_dec.sv rtl/core/mxu_wpack.sv rtl/core/mxu_array.sv;" ;;
esac
HT="-top $TOP"; [ $# -gt 0 ] && HT=-auto-top      # chparam renames the top
yosys -q -l "$OUT/$TAG.depth.log" -p "
$BB
read_verilog -sv $FILES;
${*:+chparam $* $TOP;}
hierarchy -check $HT;
synth -flatten;
tee -q -o $OUT/$TAG.depth.txt ltp -noff;
"
grep -m1 "Longest topological path" "$OUT/$TAG.depth.txt"
