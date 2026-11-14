#!/bin/bash
# Review part T (test quality): can the MXU vector files be stale, missing, short, long or edited without `make unit` failing?
# Works on a scratch copy of build/arith and build/mxu (build/mxu_review_tq/lock/<case>), never on build/ itself, and runs
# `make unit BUILD=<copy> UNIT_TB=<the two MXU testbenches>` (the other testbenches do not read build/mxu).
# Second part: the testbench's own guard (the `Unable to open / Not enough words / Too many words` grep of `make unit`), i.e.
# what happens if the lock is bypassed (a script that runs vvp directly): each case runs the vvp of tb_mxu -DQUICK on a copy.
# Usage: scripts/memrun tq_lock scripts/mxu_review/tq_lock.sh      (about 4 min, < 0.3 GB)
set -u
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
W=$ROOT/build/mxu_review_tq/lock
TBS="tb/unit/tb_mxu_array.sv tb/unit/tb_mxu.sv"
rm -rf "$W"; mkdir -p "$W"

fresh() {     # $1 = case dir: a copy of the current, verified vectors with their time stamps
  rm -rf "$W/$1"; mkdir -p "$W/$1"
  cp -Rp build/arith build/mxu "$W/$1/"
  rm -f "$W/$1/mxu/"*.vvp
}
run_unit() {  # $1 = case; prints the verdict of make unit and its first failure line
  local out rc
  out=$(make unit BUILD="$W/$1" UNIT_TB="$TBS" 2>&1); rc=$?
  echo "$out" > "$W/$1.log"
  if [ $rc -eq 0 ] && echo "$out" | grep -q "unit: ALL PASS"; then echo "  make unit: PASS (rc 0)"
  else echo "  make unit: FAIL (rc $rc): $(echo "$out" | grep -m1 -E 'mxu_lock:|FAILED|Error' )"; fi
}

echo "== L0 unchanged copy"; fresh l0; run_unit l0
echo "== L1 missing file (mxu_8_exp.hex deleted)"; fresh l1; rm "$W/l1/mxu/mxu_8_exp.hex"; run_unit l1
echo "   ... and make mxu-vec again (does it regenerate a missing file like arith-vec does?)"
out=$(make mxu-vec BUILD="$W/l1" 2>&1); echo "  make mxu-vec rc $?: $(echo "$out" | tail -1)"
echo "   ... after removing the stamp"; rm -f "$W/l1/mxu/.stamp"
out=$(make mxu-vec BUILD="$W/l1" 2>&1); echo "  make mxu-vec rc $?: $(echo "$out" | tail -1)"
echo "== L2 short file (last row of mxu_8_spm.hex removed)"; fresh l2; sed -i '' '$d' "$W/l2/mxu/mxu_8_spm.hex"; run_unit l2
echo "== L3 long file (one row appended to mxu_array_8.hex)"; fresh l3; tail -1 "$W/l3/mxu/mxu_array_8.hex" >> "$W/l3/mxu/mxu_array_8.hex"; run_unit l3
echo "== L4 edited value, same row count (row 21 of mxu_8_acc1.hex: last digit flipped)"; fresh l4
awk 'NR==21 { c = substr($0, length($0)); r = (c == "0") ? "1" : "0"; $0 = substr($0, 1, length($0)-1) r } { print }' "$W/l4/mxu/mxu_8_acc1.hex" > "$W/l4/t" && mv "$W/l4/t" "$W/l4/mxu/mxu_8_acc1.hex"; run_unit l4
echo "== L5 counts.svh and a hex file shortened together (consistent, like a generator that dropped a case)"; fresh l5
sed -i '' '$d' "$W/l5/mxu/mxu_8_prog.hex"; sed -i '' 's/CNT_MXU_8_PROG 31/CNT_MXU_8_PROG 30/' "$W/l5/mxu/counts.svh"; run_unit l5
echo "== L6 stale generator input (gen_mxu_vectors.py newer than the stamp; output unchanged)"; fresh l6; touch -t 202001010000 "$W/l6/mxu/.stamp"; run_unit l6

# ---- the testbench's own guard, lock bypassed: compile tb_mxu -DQUICK once in a copy, then run vvp on edited copies
echo "== TB guard (lock bypassed: vvp of tb_mxu -DQUICK run directly, as make unit's loop does, then its grep)"
fresh g; G=$W/g/mxu
(cd "$G" && iverilog -g2012 -DQUICK -o "$W/g/tbq" "$ROOT/tb/unit/tb_mxu.sv" "$ROOT/isa/gen/isa_pkg.sv" "$ROOT"/rtl/core/*.sv "$ROOT"/rtl/arith/*.sv 2>/dev/null)
guard() {    # $1 label; the edit has been made in $G; restore afterwards from build/mxu
  local out v
  out=$(cd "$G" && vvp -n "$W/g/tbq" 2>&1)
  if echo "$out" | grep -qE "Unable to open|Not enough words|Too many words"; then v="caught by make unit's grep"; else v="no simulator warning"; fi
  echo "  $1: $(echo "$out" | grep -m1 -E '^(PASS|FAIL)') | $v"
  cp -p build/mxu/*.hex build/mxu/counts.svh "$G/"
}
guard "unchanged"
rm "$G/mxu_8_dtab.hex"; guard "missing mxu_8_dtab.hex"
sed -i '' '$d' "$G/mxu_8_acc1.hex"; guard "acc1 one row short"
sed -i '' '$d' "$G/mxu_8_exp.hex"; guard "exp one row short (the ordering pair)"
echo 0 >> "$G/mxu_8_exp.hex"; guard "exp one row long"
sed -i '' '1s/^.*$/0/' "$G/mxu_8_exp.hex"; guard "exp: expected flags edited to 0"
awk 'NR==2 { $0 = substr($0, 1, length($0)-8) } { print }' "$G/mxu_8_prog.hex" > "$G/t" && mv "$G/t" "$G/mxu_8_prog.hex"; guard "prog row 2 with 8 hex digits fewer (same row count)"
