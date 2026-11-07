#!/bin/bash
# run_tb.sh <rtl_dir> <vec_cwd> <tb...> : compile tb/unit/tb_<tb>.sv with every .sv in rtl_dir and run vvp with cwd vec_cwd
# (which holds arith/*.hex and arith/counts.svh; it is only read, and is the include path of the count macros). Prints "<tb> PASS|FAIL|COMPILE" and the first mismatch lines. Scratch files go
# to a temporary directory under $TMPDIR (default build/arith_review) and are removed.
RTL=$1; CWD=$2; shift 2
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
BASE=${TMPDIR:-$ROOT/build/arith_review}
mkdir -p "$BASE"
TMP=$(mktemp -d "$BASE/rtb.XXXXXX")
for tb in "$@"; do
  if ! iverilog -g2012 -I "$CWD" -o "$TMP/$tb" "$ROOT/tb/unit/tb_$tb.sv" "$RTL"/*.sv 2>"$TMP/$tb.cmp"; then
    echo "$tb COMPILE"; grep -v "sorry: constant selects" "$TMP/$tb.cmp" | head -3; continue
  fi
  (cd "$CWD" && vvp -n "$TMP/$tb") >"$TMP/$tb.out" 2>&1
  if grep -qE "Unable to open|Not enough words|Too many words" "$TMP/$tb.out"; then echo "$tb FAIL (vector file missing, short or long)"
  elif grep -q "^PASS" "$TMP/$tb.out"; then echo "$tb PASS"; else echo "$tb FAIL"; grep -E "MISMATCH|FAIL|too few|expected" "$TMP/$tb.out" | head -4; fi
done
rm -rf "$TMP"
