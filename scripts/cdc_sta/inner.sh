#!/bin/bash
# Runs inside the VM (scripts/cdc_sta/run.sh calls it): every file is generated and read on the VM side.
# Usage: inner.sh <repo root> <EDA_HOME> <cfg>...   cfg = name:kind:WIDTH:DEPTH
# CDC_SRC / CDC_B (default rtl/common/afifo.sv, build/cdc_sta) point the run at another afifo.sv and another work
# directory: the mutation runs of the checks themselves (the work directory is removed first).
# The scripts are copied into the work directory first and only the copies run (check.tcl, sel.tcl, extract.py, gen.py;
# the ORFS hooks source the copy of sel.tcl), so an edit of scripts/cdc_sta/ during a run changes nothing in it.
set -uo pipefail
R=$1; EDA=$2; shift 2
D=$R/scripts/cdc_sta; B=${CDC_B:-$R/build/cdc_sta}; SRC=${CDC_SRC:-$R/rtl/common/afifo.sv}
case "$B" in ""|/|"$R"|"$R"/) echo "inner.sh: refusing work directory '$B'"; exit 1 ;; esac
rm -rf "$B"; mkdir -p "$B/tmp"
cp "$D/check.tcl" "$D/sel.tcl" "$D/extract.py" "$D/gen.py" "$B/"
D=$B
: > "$B/summary.txt"
if ! xo=$(python3 "$D/extract.py" "$SRC" "$B" 2>&1); then
  echo "$xo"; echo "RESULT extract FAIL $(echo "$xo" | tail -1)" | tee -a "$B/summary.txt"
  echo "cdc-sta: 1 of 1 FAILED"; exit 1
fi
echo "$xo"
for cfg in "$@"; do
  IFS=: read -r name kind w d <<< "$cfg"
  python3 "$D/gen.py" "$name" "$kind" "$w" "$d" "$SRC" "$B" || exit 1
  ORFS=(make -C "$FLOW_HOME" YOSYS_EXE="$EDA/yosys-linux/bin/yosys" OPENROAD_EXE="$EDA/openroad/bin/openroad"
        DESIGN_CONFIG="$B/$name.mk" WORK_HOME="$B/work_$name")
  if [[ $kind == cts* ]]; then
    echo "== $name ($kind, WIDTH $w, DEPTH $d): ORFS synthesis, floorplan, CTS$([ $kind = cts ] && echo ", global and detail route")"
    # cts: through global and detail route (5_1_grt, 5_route), the checks run on each stage; the controls stop at CTS
    tgt=cts; stages="4_cts"; [ $kind = cts ] && { tgt=route; stages="4_cts 5_1_grt 5_route"; }
    "${ORFS[@]}" $tgt > "$B/$name.flow.log" 2>&1; frc=$?
    # the corner and parasitics the flow used, from the platform config (make print-VAR)
    pv() { make -C "$FLOW_HOME" DESIGN_CONFIG="$B/$name.mk" "print-$1" 2>/dev/null | sed -n "s/^$1: //p"; }
    LIBS=$(pv LIB_FILES); SETRC=$(pv PLATFORM_DIR)/setRC.tcl; export LIBS SETRC
    res=$B/work_$name/results/asap7/cdc_$name/base; lg=$B/work_$name/logs/asap7/cdc_$name/base
    rep() { echo "RESULT $name/$1 $2 $3" | tee -a "$B/summary.txt"; }
    # flow-level checks, per variant
    nviol=$(cat "$lg"/2_1_floorplan.log "$lg"/4_1_cts.log 2>/dev/null | grep -c 'RSZ-0094.*endpoints with setup violations')
    nok=$(cat "$lg"/2_1_floorplan.log "$lg"/4_1_cts.log 2>/dev/null | grep -c 'RSZ-0098.*No setup violations')
    case $kind in
      cts|ctsn)
        [ $frc = 0 ] && ! grep -qE 'CTS-0137|RSZ-3006|\[ERROR' "$B/$name.flow.log" \
          && rep flow PASS "floorplan and CTS$([ $kind = cts ] && echo ', global and detail route') ran, no error" || rep flow FAIL "rc $frc, see $B/$name.flow.log"
        # a missing log or a skipped repair_timing gives no RSZ-0094 line either, so ask for the two "No setup violations"
        [ "$nok" = 2 ] && [ "$nviol" = 0 ] && rep repair_setup PASS "floorplan and CTS repair_timing both report no setup violations (2 RSZ-0098)" \
          || rep repair_setup FAIL "$nok of 2 RSZ-0098 No setup violations, $nviol RSZ-0094 runs with setup violations (expect 2, 0; see $lg)" ;;
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
      [ $kind = cts ] && unset POST_BASIC || export POST_BASIC=1
      for stage in $stages; do
        for clr in 0 1; do
          log=$B/$name.$stage.clr$clr.post.log
          if [ $clr = 1 ]; then export CLR=1; else unset CLR; fi
          if [ ! -f "$res/$stage.odb" ] || [ ! -f "$res/$stage.sdc" ]; then
            echo "RESULT post FAIL no $stage.odb / $stage.sdc in $res" > "$log"
          else
            ODB=$res/$stage.odb SDC=$res/$stage.sdc KIND=top DEPTH=$d WIDTH=$w MODE=post TMPD=$B/tmp EXPECT_HB=$eh EXPECT_DT=$ed EXPECT_VIOL=$ev \
              "$EDA/openroad/bin/openroad" -no_init -no_splash -exit "$D/check.tcl" > "$log" 2>&1
          fi
          grep -q '^RESULT ' "$log" || echo "RESULT post FAIL no result (see $log)" >> "$log"
          grep '^RESULT ' "$log" | sed "s|^RESULT |RESULT $name/$stage/clr$clr/|" | tee -a "$B/summary.txt"
        done
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
      NETLIST=$net KIND=$kind DEPTH=$d WIDTH=$w CONS=$B/cons_$kind.tcl MODE=$mode TMPD=$B/tmp \
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
