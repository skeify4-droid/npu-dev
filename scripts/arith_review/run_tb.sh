#!/bin/bash
# run_tb.sh <rtl_dir> <vec_cwd> <tb...> : compile tb/unit/tb_<tb>.sv with every .sv in rtl_dir and run vvp with cwd vec_cwd
# (which holds arith/*.hex and arith/counts.svh; it is only read, and arith/ is the include path of the count macros).
# Like `make unit` it first requires vec_cwd/arith to equal scripts/arith_vectors.lock (scripts/arith_lock.py verify):
# a set that differs prints "<tb> FAIL (vectors differ from scripts/arith_vectors.lock)" and nothing is simulated.
# ARITH_LOCK=0 skips that check (for deliberately different vectors, e.g. gbound.py's).
# Prints "<tb> PASS|FAIL|COMPILE" and the first mismatch lines. Scratch files go
# to a temporary directory under $TMPDIR (default build/arith_review) and are removed.
RTL=$(cd "$1" && pwd); CWD=$(cd "$2" && pwd); shift 2   # absolute: the compile runs in $CWD/arith
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
if [ "${ARITH_LOCK:-1}" != 0 ] && ! python3 "$ROOT/scripts/arith_lock.py" verify "$CWD/arith" >/dev/null 2>&1; then
  for tb in "$@"; do echo "$tb FAIL (vectors differ from scripts/arith_vectors.lock)"; done
  exit 0
fi
BASE=${TMPDIR:-$ROOT/build/arith_review}
mkdir -p "$BASE"
TMP=$(mktemp -d "$BASE/rtb.XXXXXX")
for tb in "$@"; do
  # compiled inside vec_cwd/arith: Icarus finds `include "counts.svh"` in the compile directory before any -I
  if ! (cd "$CWD/arith" && iverilog -g2012 -o "$TMP/$tb" "$ROOT/tb/unit/tb_$tb.sv" "$RTL"/*.sv) 2>"$TMP/$tb.cmp"; then
    echo "$tb COMPILE"; grep -v "sorry: constant selects" "$TMP/$tb.cmp" | head -3; continue
  fi
  (cd "$CWD" && vvp -n "$TMP/$tb") >"$TMP/$tb.out" 2>&1
  if grep -qE "Unable to open|Not enough words|Too many words" "$TMP/$tb.out"; then echo "$tb FAIL (vector file missing, short or long)"
  elif grep -q "^PASS" "$TMP/$tb.out"; then echo "$tb PASS"; else echo "$tb FAIL"; grep -E "MISMATCH|FAIL|too few|expected" "$TMP/$tb.out" | head -4; fi
done
rm -rf "$TMP"
