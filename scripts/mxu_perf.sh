#!/bin/bash
# Tile-period measurement of the MXU at M = 1 (mxu.md section 7, review S-4 / S-5): one instruction of 24..64 passes per case,
# W at the pitches of scripts/gen_mxu_vectors.py PERF_PITCHES, on tb_mxu's behavioural SPM with the bank model (+BANK=n).
#
#   scripts/mxu_perf.sh [DIM ...]            default: 16 32 (add 64 only after checking the RSS, see docs/reports/capacity.md)
#   BANKS="0 1 2" scripts/mxu_perf.sh 32     bank models to run (0 ideal ports, 1 per-port banks, 2 shared banks)
#   PITCHES="2560 4608 3072" ...             pitches to run
#
# One case at a time, each under scripts/memrun (logs/mxu_perf_d<DIM>_p<PITCH>_b<BANK>.{log,mem.log}); results are appended to
# build/mxu_perf/results.txt (the PERF / PERFALL lines of the testbench, with the peak RSS and wall time of the run).
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
PY=${PY:-/Volumes/EVO970PLUS/eda/venv/bin/python}
DIMS=${*:-16 32}
BANKS=${BANKS:-"0 1 2"}
PITCHES=${PITCHES:-"2560 4608 3072"}
P=$ROOT/build/mxu_perf
[ -f $P/d16_p2560/spm.hex ] || scripts/memrun mxu_perf_gen $PY scripts/gen_mxu_vectors.py --perf || exit 1
SRC="$ROOT/isa/gen/isa_pkg.sv $(ls $ROOT/rtl/core/*.sv | sort | tr '\n' ' ') $(ls $ROOT/rtl/arith/*.sv | sort | tr '\n' ' ')"
echo "$SRC" | tr ' ' '\n' | grep . > $P/src.f
for d in $DIMS; do
  for p in $PITCHES; do
    c=$P/d${d}_p${p}
    [ -f $c/spm.hex ] || { echo "no vectors for DIM $d pitch $p"; continue; }
    ( cd $c && echo "$ROOT/tb/unit/tb_mxu.sv" > cmd.f && cat $P/src.f >> cmd.f &&
      iverilog -g2012 -DPERF -DPDIM=$d -o tb.vvp -c cmd.f 2>&1 | grep -v "sorry: constant selects" )
    for b in $BANKS; do
      n=mxu_perf_d${d}_p${p}_b${b}
      ( cd $c && $ROOT/scripts/memrun $n vvp -n tb.vvp +BANK=$b )
      { echo "== DIM $d pitch $p bank $b: $(head -1 $ROOT/logs/$n.mem.log)"; grep -E "PERF|PASS|FAIL|bank model|TIMEOUT|ACC rows" $ROOT/logs/$n.log; } | tee -a $P/results.txt
    done
  done
done
