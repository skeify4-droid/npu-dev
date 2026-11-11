#!/bin/bash
# Review (implementability): native Yosys + ABC mapping of one MXU module, flattened, to the ASAP7 RVT TT libraries of the pinned ORFS
# checkout (no VM, no placement, no wires): cell area in um^2 (flip-flops separately) and the ABC delay estimate of the worst
# register-to-register / port path against the 1.5 GHz clock (667 ps, arch.md). An estimate for comparing blocks, not sign-off.
# Usage: scripts/memrun rev_asap7_<tag> scripts/mxu_review/asap7.sh <top> <tag> [-set P V ...]
#   top: mxu_pe | mxu_pe_dot | fp32_add | mxu_dec | mxu_wpack | mxu_local (mxu with mxu_dec, mxu_wpack, mxu_array as blackboxes) | mxu_array_bbpe
#   KEEP_OUT=<port> (env): before mapping, every other output loses its port flag and the logic that fed only those goes, so the
#   delay is the one of the cone of that output (e.g. KEEP_OUT=w_req_addr: the W lane address generation).
# Output: build/mxu_review/asap7/<tag>.{stat,abc}.txt
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
TOP=$1; TAG=$2; shift 2
EDA_HOME=${EDA_HOME:-/Volumes/EVO970PLUS/eda}
PLIB=$EDA_HOME/orfs/flow/platforms/asap7/lib/NLDM
OUT=$ROOT/build/mxu_review/asap7; mkdir -p "$OUT"
LIBS=""
for f in SIMPLE_RVT_TT_nldm_211120 INVBUF_RVT_TT_nldm_220122 AO_RVT_TT_nldm_211120 OA_RVT_TT_nldm_211120 SEQ_RVT_TT_nldm_220123; do
  src=$PLIB/asap7sc7p5t_$f.lib; [ -f "$src" ] || src=$src.gz
  dst=$OUT/$f.lib
  [ -f "$dst" ] || { case $src in *.gz) gunzip -c "$src" >"$dst" ;; *) cp "$src" "$dst" ;; esac; }
  LIBS="$LIBS $dst"
done
SEQ=$OUT/SEQ_RVT_TT_nldm_220123.lib
# Yosys' liberty script for -D, with `stime -p` at the end so the log has the mapped delay ({D} = the -D target)
printf '%s\n' 'strash' '&get -n' '&fraig -x' '&put' 'scorr' 'dc2' 'strash' '&get -n' '&dch -f' '&nf {D}' '&put' \
  'buffer' 'upsize {D}' 'dnsize {D}' 'stime -p' >"$OUT/abc.script"
cd "$ROOT"
ARITH=$(ls rtl/arith/*.sv | sort | tr '\n' ' ')
BB=""
case $TOP in
  mxu_pe|mxu_pe_dot|fp32_add) FILES="$ARITH" ;;          # the PE and its two registered stages
  mxu_dec)   FILES="isa/gen/isa_pkg.sv rtl/core/mxu_dec.sv" ;;
  mxu_dec_fix) FILES="isa/gen/isa_pkg.sv build/mxu_review/fix/mxu_dec.sv"; TOP=mxu_dec ;;   # fix_dec_narrow.py copy
  mxu_wpack) FILES=rtl/core/mxu_wpack.sv ;;
  mxu_local) FILES="rtl/core/mxu.sv"; TOP=mxu
             BB="read_verilog -sv isa/gen/isa_pkg.sv; read_verilog -sv -lib rtl/core/mxu_dec.sv rtl/core/mxu_wpack.sv rtl/core/mxu_array.sv;" ;;
  mxu_array_bbpe) TOP=mxu_array                      # the array's own logic, PE as a blackbox. On the packed copy of
             FILES=build/mxu_review/fix/mxu_array.sv          # fix_array_packed.sh (proven equivalent): the original loses
             BB="read_verilog -sv -lib rtl/arith/mxu_pe.sv;" ;;   # every PE instance with a blackbox PE (finding I-2)
esac
HT="-top $TOP"; [ $# -gt 0 ] && HT=-auto-top
RL=""; for l in $LIBS; do RL="$RL read_liberty -lib $l;"; done
AL=""; for l in $LIBS; do AL="$AL -liberty $l"; done
yosys -l "$OUT/$TAG.log" -q -p "
$RL
$BB
read_verilog -sv $FILES;
${*:+chparam $* $TOP;}
hierarchy -check $HT;
synth -flatten;
${KEEP_OUT:+delete -port o:* o:$KEEP_OUT %d; opt_clean -purge;}
dfflibmap -liberty $SEQ;
debug abc -D 667 $AL -script $OUT/abc.script;
opt_clean;
tee -q -o $OUT/$TAG.stat.txt stat $(for l in $LIBS; do printf -- '-liberty %s ' $l; done);
"
grep -h -E "Delay =" "$OUT/$TAG.log" | tail -1 | tee "$OUT/$TAG.abc.txt" || true
grep -E "Chip area|cells$" "$OUT/$TAG.stat.txt" | tail -2
