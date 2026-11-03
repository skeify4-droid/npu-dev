#!/bin/bash
# Runs inside the VM (scripts/cdc_sta/run.sh calls it): every file is generated and read on the VM side.
# Usage: inner.sh <repo root> <EDA_HOME> <cfg>...   cfg = name:kind:WIDTH:DEPTH
set -uo pipefail
R=$1; EDA=$2; shift 2
D=$R/scripts/cdc_sta; B=$R/build/cdc_sta
rm -rf "$B"; mkdir -p "$B/tmp"
python3 "$D/extract.py" "$R/rtl/common/afifo.sv" "$B" || exit 1
cp "$D/sel.tcl" "$B/sel.tcl"
: > "$B/summary.txt"
for cfg in "$@"; do
  IFS=: read -r name kind w d <<< "$cfg"
  python3 "$D/gen.py" "$name" "$kind" "$w" "$d" "$R/rtl/common/afifo.sv" "$B" || exit 1
  ORFS=(make -C "$FLOW_HOME" YOSYS_EXE="$EDA/yosys-linux/bin/yosys" OPENROAD_EXE="$EDA/openroad/bin/openroad"
        DESIGN_CONFIG="$B/$name.mk" WORK_HOME="$B/work_$name")
  if [[ $kind == cts* ]]; then
    echo "== $name ($kind, WIDTH $w, DEPTH $d): ORFS synthesis, floorplan, CTS"
    "${ORFS[@]}" cts > "$B/$name.flow.log" 2>&1; frc=$?
    res=$B/work_$name/results/asap7/cdc_$name/base; lg=$B/work_$name/logs/asap7/cdc_$name/base
    rep() { echo "RESULT $name/$1 $2 $3" | tee -a "$B/summary.txt"; }
    # flow-level checks, per variant
    nviol=$(cat "$lg"/2_1_floorplan.log "$lg"/4_1_cts.log 2>/dev/null | grep -c 'RSZ-0094.*endpoints with setup violations')
    case $kind in
      cts|ctsn)
        [ $frc = 0 ] && ! grep -qE 'CTS-0137|RSZ-3006|\[ERROR' "$B/$name.flow.log" \
          && rep flow PASS "floorplan and CTS ran, no error" || rep flow FAIL "rc $frc, see $B/$name.flow.log"
        [ "$nviol" = 0 ] && rep repair_setup PASS "no setup-violating endpoint in the floorplan / CTS repair_timing" \
          || rep repair_setup FAIL "$nviol repair_timing runs with setup violations (expect 0)" ;;
      ctsc)
        [ $frc = 0 ] && rep flow PASS "floorplan and CTS ran" || rep flow FAIL "rc $frc, see $B/$name.flow.log"
        n12=$(cat "$lg"/2_1_floorplan.log "$lg"/4_1_cts.log 2>/dev/null | grep -c 'RSZ-0094.*Found 12 endpoints with setup violations')
        [ "$n12" = 2 ] && rep repair_setup PASS "clocks-only SDC: floorplan and CTS repair_timing both repair the 12 crossing endpoints" \
          || rep repair_setup FAIL "expected 12 violating endpoints in both runs, got $n12 of 2 (see $lg)" ;;
      ctsi)
        [ $frc != 0 ] && grep -q 'CTS-0137' "$B/$name.flow.log" \
          && rep instance_dt_stops_cts PASS "instance dont_touch before CTS: flow stops with CTS-0137" \
          || rep instance_dt_stops_cts FAIL "expected CTS-0137, rc $frc, see $B/$name.flow.log" ;;
    esac
    if [ $frc = 0 ] && [ $kind != ctsi ]; then
      case $kind in cts) eh=0; ed=nets+inst; ev=0 ;; ctsn) eh=1; ed=none; ev=0 ;; ctsc) eh=1; ed=none; ev=some ;; esac
      for clr in 0 1; do
        log=$B/$name.clr$clr.post.log
        if [ $clr = 1 ]; then export CLR=1; else unset CLR; fi
        [ $kind = cts ] && unset POST_BASIC || export POST_BASIC=1
        ODB=$res/4_cts.odb SDC=$res/4_cts.sdc KIND=top DEPTH=$d MODE=post TMPD=$B/tmp EXPECT_HB=$eh EXPECT_DT=$ed EXPECT_VIOL=$ev \
          "$EDA/openroad/bin/openroad" -no_init -no_splash -exit "$D/check.tcl" > "$log" 2>&1
        grep -q '^RESULT ' "$log" || echo "RESULT post FAIL no result (see $log)" >> "$log"
        grep '^RESULT ' "$log" | sed "s|^RESULT |RESULT $name/clr$clr/|" | tee -a "$B/summary.txt"
      done
      unset CLR POST_BASIC
    fi
    continue
  fi
  echo "== $name ($kind, WIDTH $w, DEPTH $d): ORFS synthesis"
  "${ORFS[@]}" synth > "$B/$name.synth.log" 2>&1 \
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
