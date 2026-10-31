#!/usr/bin/env bash
# Usage: scripts/verilator_calib.sh N   -> builds & runs pe_array_NxN under memrun (build and run logged separately)
set -euo pipefail
N=$1; R=$(cd "$(dirname "$0")/.." && pwd); M=pe_array_${N}x${N}
D=$R/build/vcalib_$N; rm -rf "$D"; mkdir -p "$D"
sed "s/VTOP/V$M/g" "$R/tb/calib/pe_main.cpp" > "$D/main.cpp"
"$R/scripts/memrun" vbuild_$N verilator --cc --exe --build -j 4 -O3 --Mdir "$D/obj" \
  -Wno-fatal --top-module $M "$R/syn/gen/$M.sv" "$D/main.cpp" -o sim
"$R/scripts/memrun" vrun_$N "$D/obj/sim"
cat "$R/logs/vbuild_$N.mem.log" "$R/logs/vrun_$N.mem.log"
