#!/bin/bash
# Runs the cdc-sta harness (scripts/cdc_sta/run.sh) on header / netlist mutants of afifo.sv, one at a time, and prints the
# first failing checks of each with the expectation from cfgs.tsv.
# Usage: scripts/memrun mut_hdr scripts/afifo_mutants/run_header_mutants.sh [mutant ...]   (default: all)
# Mutants: scripts/afifo_mutants/mk_header_mutants.py (default build/afifo_mutants/hdr; MUT_DIR overrides); work directories
# <MUT_DIR>/out_<name>, logs <MUT_DIR>/<name>.log. The VM is the heavy job (about 18 minutes for all 25 mutants): nothing else
# heavy at the same time. Expected: every mutant fails except n_allow_paths (passes) and r_keep_msb (survives).
set -uo pipefail
R=$(cd "$(dirname "$0")/../.." && pwd)
H=${MUT_DIR:-$R/build/afifo_mutants/hdr}
[ -f "$H/cfgs.tsv" ] || python3 "$R/scripts/afifo_mutants/mk_header_mutants.py" "$H" || exit 1
cd "$R"
while IFS=$'\t' read -r m cfgs exp; do
  if [ $# -gt 0 ] && [[ ! " $* " == *" $m "* ]]; then continue; fi
  # stdin from /dev/null: eda-run (orb) would otherwise read the rest of cfgs.tsv
  CDC_SRC=$H/$m/afifo.sv CDC_B=$H/out_$m scripts/cdc_sta/run.sh $cfgs > "$H/$m.log" 2>&1 < /dev/null; rc=$?
  echo "### $m rc=$rc expect: $exp"
  grep -E "^cdc-sta:|extract:" "$H/$m.log" | head -3
  grep -E " FAIL " "$H/$m.log" | sed 's/^RESULT //' | cut -c1-230 | head -14
done < "$H/cfgs.tsv"
echo "#### done"
