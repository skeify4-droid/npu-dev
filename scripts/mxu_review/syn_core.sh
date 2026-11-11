#!/bin/bash
# Review (implementability): hierarchical Yosys synthesis of the MXU blocks, generic gates, no -flatten.
# `make syn` reads rtl/common/<BLOCK>.sv or the rtl/arith set only, so it cannot build an rtl/core block (finding I-1);
# this script reads the same files `make lint` gives Verilator for a core top.
# Usage (one at a time, under scripts/memrun):
#   scripts/memrun rev_syn_<tag> scripts/mxu_review/syn_core.sh <top> <tag> [-set P V ...]
#   e.g. syn_core.sh mxu_array a16 -set ROWS 16 -set COLS 16
# (`chparam -set`, then `hierarchy -auto-top` (chparam renames the top): `hierarchy -chparam` on mxu_array trips an internal assertion of Yosys 0.70+post,
#  kernel/rtlil_bufnorm.cc:679, a tool bug.)
# Outputs in build/mxu_review/syn/<tag>.{coarse,stat,check}.txt.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
TOP=$1; TAG=$2; shift 2
OUT=$ROOT/build/mxu_review/syn; mkdir -p "$OUT"
cd "$ROOT"
ARITH=$(ls rtl/arith/*.sv | sort | tr '\n' ' ')
CORE=$(ls rtl/core/*.sv | sort | tr '\n' ' ')
case $TOP in                        # the files each top needs (chparam renames the module; with the whole set read,
  mxu_wpack) FILES=rtl/core/mxu_wpack.sv ;;                   # hierarchy -top no longer finds it)
  mxu_array) FILES="rtl/core/mxu_array.sv $ARITH" ;;
  mxu_dec)   FILES="isa/gen/isa_pkg.sv rtl/core/mxu_dec.sv" ;;
  mxu_dec_fix) FILES="isa/gen/isa_pkg.sv build/mxu_review/fix/mxu_dec.sv"; TOP=mxu_dec ;;   # fix_dec_narrow.py copy
  mxu_bb)    FILES="isa/gen/isa_pkg.sv rtl/core/mxu.sv rtl/core/mxu_dec.sv rtl/core/mxu_wpack.sv"; TOP=mxu
             BB="read_verilog -sv -lib rtl/core/mxu_array.sv;" ;;           # mxu with the array as a blackbox
  mxu_array_bbpe) TOP=mxu_array                      # the array's own logic, PE as a blackbox. On the packed copy of
             FILES=build/mxu_review/fix/mxu_array.sv          # fix_array_packed.sh (proven equivalent): the original loses
             BB="read_verilog -sv -lib rtl/arith/mxu_pe.sv;" ;;   # every PE instance with a blackbox PE (finding I-2)
  *)         FILES="isa/gen/isa_pkg.sv $CORE $ARITH" ;;
esac
yosys -q -l "$OUT/$TAG.yosys.log" -p "
${BB:-}
read_verilog -sv $FILES;
${*:+chparam $* $TOP;}
hierarchy -check -auto-top;
synth -run begin:fine;
tee -q -o $OUT/$TAG.coarse.txt stat -width;
tee -q -o $OUT/$TAG.check.txt check;
synth -run fine:;
tee -q -o $OUT/$TAG.stat.txt stat;
tee -q -a $OUT/$TAG.check.txt check;
"
echo "done $TOP $TAG"
