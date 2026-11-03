#!/bin/bash
# Runs inside the VM (scripts/cdc_sta/run.sh calls it): every file is generated and read on the VM side.
# Usage: inner.sh <repo root> <EDA_HOME> <cfg>...   cfg = name:kind:WIDTH:DEPTH
set -uo pipefail
R=$1; EDA=$2; shift 2
D=$R/scripts/cdc_sta; B=$R/build/cdc_sta
rm -rf "$B"; mkdir -p "$B/tmp"
python3 "$D/extract.py" "$R/rtl/common/afifo.sv" "$B" || exit 1
: > "$B/summary.txt"
for cfg in "$@"; do
  IFS=: read -r name kind w d <<< "$cfg"
  python3 "$D/gen.py" "$name" "$kind" "$w" "$d" "$R/rtl/common/afifo.sv" "$B" || exit 1
  echo "== $name ($kind, WIDTH $w, DEPTH $d): ORFS synthesis"
  make -C "$FLOW_HOME" YOSYS_EXE="$EDA/yosys-linux/bin/yosys" OPENROAD_EXE="$EDA/openroad/bin/openroad" \
    DESIGN_CONFIG="$B/$name.mk" WORK_HOME="$B/work_$name" synth > "$B/$name.synth.log" 2>&1 \
    || { echo "RESULT $name/synth FAIL see $B/$name.synth.log" | tee -a "$B/summary.txt"; continue; }
  net=$B/work_$name/results/asap7/cdc_$name/base/1_2_yosys.v
  modes="text"; [ "$kind" = top ] && modes="excl_none excl_gray excl_mem excl_arst text"
  for clr in 0 1; do
    for mode in $modes; do
      log=$B/$name.clr$clr.$mode.log
      if [ $clr = 1 ]; then export CLR=1; else unset CLR; fi
      NETLIST=$net KIND=$kind DEPTH=$d CONS=$B/cons_$kind.tcl MODE=$mode TMPD=$B/tmp \
        "$EDA/openroad/bin/openroad" -no_init -no_splash -exit "$D/check.tcl" > "$log" 2>&1
      n=$(grep -c '^RESULT ' "$log")
      [ "$n" -gt 0 ] || echo "RESULT $mode FAIL no result (see $log)" >> "$log"
      grep '^RESULT ' "$log" | sed "s|^RESULT |RESULT $name/clr$clr/|" | tee -a "$B/summary.txt"
    done
  done
done
unset CLR
fails=$(grep -c ' FAIL ' "$B/summary.txt"); total=$(grep -c '^RESULT ' "$B/summary.txt")
if [ "$fails" = 0 ]; then echo "cdc-sta: ALL PASS ($total checks)"; else echo "cdc-sta: $fails of $total FAILED"; exit 1; fi
