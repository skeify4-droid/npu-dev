#!/bin/bash
# Runs the eight afifo sby tasks (tb/formal/afifo.sby) and tb_afifo on RTL mutants and prints one summary line each:
#   MUT <name> | <task>=PASS|FAIL ... | unreached-cover-lines | failed asserts | tb_afifo result
# Usage: scripts/memrun mut_rtl scripts/afifo_mutants/run_rtl_mutants.sh [mutant ...]   (default: all in the mutant directory)
# Mutants: scripts/afifo_mutants/mk_rtl_mutants.py (default build/afifo_mutants/rtl; MUT_DIR overrides). Work directories
# <MUT_DIR>/run/<name>. About 20 s per mutant and a few minutes in all; formal and Icarus only, no VM.
# Expected: every mutant fails at least one task or the tb (a mutant that passes all of them is a gap). Lines of afifo.sv
# in "unreached" / "asserts" are lines of the mutant, which differ from the repo's by the mutation.
set -uo pipefail
R=$(cd "$(dirname "$0")/../.." && pwd)
source /Volumes/EVO970PLUS/claude/env_setup/env.sh
export PATH=$EDA_HOME/tools/sby/bin:$EDA_HOME/venv/bin:$PATH
H=${MUT_DIR:-$R/build/afifo_mutants/rtl}
[ -f "$H/orig/afifo.sv" ] || python3 "$R/scripts/afifo_mutants/mk_rtl_mutants.py" "$H" || exit 1
MUTS=("$@")
[ ${#MUTS[@]} -gt 0 ] || MUTS=($(cd "$H" && ls -d */ | tr -d / | grep -v -x -e orig -e run))
for m in "${MUTS[@]}"; do
  m=${m%/}; W=$H/run/$m; rm -rf "$W"; mkdir -p "$W"
  cp "$R/tb/formal/afifo_formal.sv" "$W/"
  sed "s|../../rtl/common/afifo.sv|$H/$m/afifo.sv|" "$R/tb/formal/afifo.sby" > "$W/afifo.sby"
  (cd "$W" && sby -f afifo.sby > sby.log 2>&1)
  res=""
  for t in d2 d4 d8 ph2 ph4 cov4 l2 l4; do
    st=$(grep -h "DONE (" "$W/afifo_$t/logfile.txt" 2>/dev/null | sed -E 's/.*DONE \(([A-Z]+).*/\1/' | tail -1)
    res="$res $t=${st:-?}"
  done
  unreached=$(grep -h "Unreached cover" "$W/afifo_cov4/logfile.txt" 2>/dev/null | sed -E 's/.*(afifo(_formal)?\.sv:[0-9]+).*/\1/' | sort -u | tr '\n' ',')
  af=$(grep -h -E "Assert failed|failed assertion" "$W"/afifo_*/logfile.txt 2>/dev/null | sed -E 's/.*(afifo(_formal)?\.sv:[0-9]+).*/\1/' | sort | uniq -c | tr -s ' ' | tr '\n' ';')
  iverilog -g2012 -o "$W/tb" "$R/tb/unit/tb_afifo.sv" "$H/$m/afifo.sv" > "$W/iv.log" 2>&1 && (cd "$W" && vvp -n tb > tb.out 2>&1)
  tb=$(grep -E "^PASS|FAIL tb_afifo" "$W/tb.out" 2>/dev/null | head -1); [ -n "$tb" ] || tb="tb:none($(head -c 80 "$W/iv.log"))"
  first=$(grep -m2 -E "overflow|phantom|data 0x|lost|never|thr " "$W/tb.out" 2>/dev/null | tr '\n' '|')
  echo "MUT $m |$res | unreached-cover-lines: ${unreached:-none} | asserts: ${af:-none} | $tb | $first"
done
