#!/bin/bash
# Second review (51f8cbe): try to make the arith regression pass with broken inputs. Every scenario works on a scratch copy
# of the vectors under build/arith_review/attack/ (build/arith itself is only read) and runs the testbenches through
# run_tb.sh, which applies the same checks as `make unit` (Unable to open / Not enough words / Too many words, then ^PASS).
# Usage: scripts/memrun arith_attack scripts/arith_review/attack_infra.sh [--make]
#   --make  also runs the whole `make unit BUILD=<scratch>` on scenario S1 (about 90 s) to show the Makefile path passes.
# Prints one line per scenario: "S<n> <what> -> <tb> PASS|FAIL ..." and "ATTACK WORKS" when broken inputs pass.
# run_tb.sh now requires the vector directory to equal scripts/arith_vectors.lock, so S1, S3 and S4 print "attack fails
# (caught)" (the printed FAIL names the check: the lock, or the testbench itself). No line may say "ATTACK WORKS" or "UNEXPECTED PASS".
set -u
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
S=$ROOT/scripts/arith_review
W=$ROOT/build/arith_review/attack
PY=${PY:-/Volumes/EVO970PLUS/eda/venv/bin/python}
[ -x "$PY" ] || PY=python3
RTL=$ROOT/rtl/arith
rm -rf "$W"; mkdir -p "$W"
export TMPDIR=$W
[ -f "$ROOT/build/arith/counts.svh" ] || { echo "run make arith-vec first"; exit 2; }

fresh() {   # fresh <name>: a full copy of build/arith as <W>/<name>/arith
  mkdir -p "$W/$1"; cp -p -R "$ROOT/build/arith" "$W/$1/arith"
}
setcnt() {  # setcnt <dir> <MACRO> <n>
  sed -i '' -E "s/^\`define $2 .*/\`define $2 $3/" "$1/arith/counts.svh"
}
tb() {      # tb <label> <rtl dir> <vec dir> <tb> <expect PASS|FAIL>
  local r; r=$("$S/run_tb.sh" "$2" "$3" "$4" | head -1)
  local verdict="as expected"
  if [ "$5" = PASS ] && [[ "$r" == *PASS ]]; then verdict="ATTACK WORKS (broken input passes)"; fi
  if [ "$5" = FAIL ] && [[ "$r" == *PASS ]]; then verdict="UNEXPECTED PASS"; fi
  if [ "$5" = PASS ] && [[ "$r" != *PASS ]]; then verdict="attack fails (caught)"; fi
  echo "$1 -> $r   [$verdict]"
}
mutrtl() {  # mutrtl <name> <file> <old> <new> : copy of rtl/arith with one replacement, prints the dir
  local d=$W/rtl_$1; rm -rf "$d"; cp -R "$RTL" "$d"
  OLD="$3" NEW="$4" F="$d/$2" "$PY" - <<'EOF'
import os
p, o, n = os.environ["F"], os.environ["OLD"], os.environ["NEW"]
s = open(p).read(); assert o in s, ("no match", p, o); open(p, "w").write(s.replace(o, n))
EOF
  echo "$d"
}

# ---- S1: truncated file and counts.svh edited to match it
fresh s1; head -n 1000 "$W/s1/arith/fp32_add.hex" > "$W/t" && mv "$W/t" "$W/s1/arith/fp32_add.hex"; setcnt "$W/s1" CNT_FP32_ADD 1000
tb "S1 fp32_add.hex cut to 1,000 rows + CNT_FP32_ADD 1000" "$RTL" "$W/s1" fp32_add PASS
for f in fp32_mul fp32_max bf16_dot2 fp8_dot4 acc_update mxu_pe_dot; do
  m=CNT_$(echo $f | tr a-z A-Z); head -n 1000 "$W/s1/arith/$f.hex" > "$W/t" && mv "$W/t" "$W/s1/arith/$f.hex"; setcnt "$W/s1" $m 1000
  tb "S1 $f.hex cut to 1,000 rows + $m 1000" "$RTL" "$W/s1" $f PASS
done
head -n 100 "$W/s1/arith/mxu_pe.hex" > "$W/t" && mv "$W/t" "$W/s1/arith/mxu_pe.hex"; setcnt "$W/s1" CNT_MXU_PE 100
tb "S1 mxu_pe.hex cut to 100 rows + CNT_MXU_PE 100" "$RTL" "$W/s1" mxu_pe PASS
head -n 1 "$W/s1/arith/fold256_mxfp8.hex" > "$W/t" && mv "$W/t" "$W/s1/arith/fold256_mxfp8.hex"; setcnt "$W/s1" CNT_FOLD256_MXFP8 1
tb "S1 fold256_mxfp8.hex cut to 1 tile + CNT 1" "$RTL" "$W/s1" fold_quant PASS
head -n 1 "$W/s1/arith/cvt_fp32_e4m3.hex" > "$W/t" && mv "$W/t" "$W/s1/arith/cvt_fp32_e4m3.hex"; setcnt "$W/s1" CNT_CVT_FP32_E4M3 1
tb "S1 cvt_fp32_e4m3.hex cut to 1 row + CNT 1" "$RTL" "$W/s1" cvt_fp32_fp PASS
head -n 10 "$W/s1/arith/cvt_dec_e4m3_fp32.hex" > "$W/t" && mv "$W/t" "$W/s1/arith/cvt_dec_e4m3_fp32.hex"; setcnt "$W/s1" CNT_CVT_DEC_E4M3_FP32 10
tb "S1 cvt_dec_e4m3_fp32.hex cut to 10 rows + CNT 10 (tb has a hard-coded 256)" "$RTL" "$W/s1" cvt_dec PASS
head -n 10 "$W/s1/arith/fp8_mul.hex" > "$W/t" && mv "$W/t" "$W/s1/arith/fp8_mul.hex"; setcnt "$W/s1" CNT_FP8_MUL 10
tb "S1 fp8_mul.hex cut to 10 rows + CNT 10 (tb has a hard-coded 65536)" "$RTL" "$W/s1" fp8_mul PASS

# ---- S2: truncated file, counts.svh untouched (the F1 fix: the count mechanism alone, so the lock check is off here)
fresh s2; head -n 1000 "$W/s2/arith/fp32_add.hex" > "$W/t" && mv "$W/t" "$W/s2/arith/fp32_add.hex"
ARITH_LOCK=0 tb "S2 fp32_add.hex cut to 1,000 rows, counts untouched" "$RTL" "$W/s2" fp32_add FAIL
fresh s2b; cat "$W/s2b/arith/fp32_add.hex" <(head -n 5 "$W/s2b/arith/fp32_add.hex") > "$W/t" && mv "$W/t" "$W/s2b/arith/fp32_add.hex"
ARITH_LOCK=0 tb "S2 fp32_add.hex with 5 extra rows, counts untouched" "$RTL" "$W/s2b" fp32_add FAIL
fresh s2c; rm "$W/s2c/arith/cvt_fp32_fp16.hex"
ARITH_LOCK=0 tb "S2 cvt_fp32_fp16.hex removed" "$RTL" "$W/s2c" cvt_fp32_fp FAIL

# ---- S3: a zero count. [0:MAXN-1] = [0:-1] is a two-word array, so a 2-row file fills it without a warning; the loop runs 0 times.
fresh s3; head -n 2 "$W/s3/arith/fp32_add.hex" > "$W/t" && mv "$W/t" "$W/s3/arith/fp32_add.hex"; setcnt "$W/s3" CNT_FP32_ADD 0
tb "S3 CNT_FP32_ADD 0 + a 2-row file (zero rows checked)" "$RTL" "$W/s3" fp32_add PASS
fresh s3b; : > "$W/s3b/arith/fp32_add.hex"; setcnt "$W/s3b" CNT_FP32_ADD 0
tb "S3 CNT_FP32_ADD 0 + an empty file" "$RTL" "$W/s3b" fp32_add PASS

# ---- S4: an old-but-consistent vector set: the generator and reference of 815acb1 (before the fixes), with a counts.svh
# made from the files' own line counts, as a stale build/ copied from another checkout would look.
mkdir -p "$W/oldsrc"; (cd "$ROOT" && git archive 815acb1 scripts/gen_arith_vectors.py sw/npumodel | tar -x -C "$W/oldsrc")
"$PY" "$W/oldsrc/scripts/gen_arith_vectors.py" "$W/old/arith" > "$W/oldgen.log" 2>&1 || { echo "S4 old generator failed"; tail -5 "$W/oldgen.log"; }
{ for f in "$W"/old/arith/*.hex; do n=$(basename "$f" .hex); echo "\`define CNT_$(echo "$n" | tr a-z A-Z) $(wc -l < "$f" | tr -d ' ')"; done; } > "$W/old/arith/counts.svh"
tb "S4 old fp8_dot4.hex (no corner sums), current RTL" "$RTL" "$W/old" fp8_dot4 PASS
D=$(mutrtl w38 fp8_dot4.sv "logic signed [38:0] sum;" "logic signed [37:0] sum;"); "$PY" - "$D/fp8_dot4.sv" <<'EOF'
import sys; p = sys.argv[1]; s = open(p).read(); open(p, "w").write(s.replace("sum[38]", "sum[37]"))
EOF
tb "S4 old fp8_dot4.hex, mutant dot_width38 (38-bit sum)" "$D" "$W/old" fp8_dot4 PASS
tb "S4 current fp8_dot4.hex, mutant dot_width38" "$D" "$ROOT/build" fp8_dot4 FAIL
D=$W/rtl_pre_f3; rm -rf "$D"; cp -R "$RTL" "$D"; (cd "$ROOT" && git show 815acb1:rtl/arith/acc_update.sv) > "$D/acc_update.sv"
tb "S4 old acc_update.hex, pre-F3 acc_update.sv" "$D" "$W/old" acc_update PASS
tb "S4 current acc_update.hex, pre-F3 acc_update.sv" "$D" "$ROOT/build" acc_update FAIL
D=$W/rtl_pre_f6; rm -rf "$D"; cp -R "$RTL" "$D"; (cd "$ROOT" && git show 815acb1:rtl/arith/cvt_fp32_fp.sv) > "$D/cvt_fp32_fp.sv"
tb "S4 old cvt_fp32_*.hex, pre-F6 cvt_fp32_fp.sv (BF16 flush)" "$D" "$W/old" cvt_fp32_fp PASS
tb "S4 old cvt_fp32_*.hex, current cvt_fp32_fp.sv" "$RTL" "$W/old" cvt_fp32_fp FAIL

# ---- S5: which counts.svh does `include "counts.svh"` pick? Makefile: cwd = repo root, -I build/arith. The pre-lock testbenches
# included "arith/counts.svh", for which a stray arith/counts.svh in the cwd won; now that file is not looked at.
mkdir -p "$W/inc/root/arith" "$W/inc/ipath"
echo '`define CNT_FP32_ADD 11' > "$W/inc/root/arith/counts.svh"     # a stray arith/counts.svh in the cwd of the compile
echo '`define CNT_FP32_ADD 33' > "$W/inc/ipath/counts.svh"          # the -I directory (build/arith in the Makefile)
mxn() { (cd "$W/inc/root" && iverilog -g2012 -I "$W/inc/ipath" -E -o "$W/inc/pp.sv" "$ROOT/tb/unit/tb_fp32_add.sv" 2>/dev/null); grep -m1 -E 'localparam int MAXN' "$W/inc/pp.sv" | grep -oE '[0-9]+' | head -1; }
echo "S5 stray ./arith/counts.svh (11) next to the real one in -I (33): MAXN = $(mxn)   [33 = the -I copy: no longer shadowed]"
echo '`define CNT_FP32_ADD 44' > "$W/inc/root/counts.svh"           # a stray counts.svh in the cwd itself
echo "S5 stray ./counts.svh (44) in the compile directory: MAXN = $(mxn)   [44 = shadowed: the residual, only the repo root counts]"
rm -f "$W/inc/root/counts.svh"

# ---- S6: the testbench's own zero-count guard (the lock is bypassed with ARITH_LOCK=0): CNT 0 and a 2-row file
fresh s6; head -n 2 "$W/s6/arith/fp32_add.hex" > "$W/t" && mv "$W/t" "$W/s6/arith/fp32_add.hex"; setcnt "$W/s6" CNT_FP32_ADD 0
ARITH_LOCK=0 tb "S6 CNT_FP32_ADD 0 + a 2-row file, lock check off (testbench guard)" "$RTL" "$W/s6" fp32_add FAIL

# ---- S1 through the Makefile itself (optional, seconds now): a stale but complete build dir with a fresh stamp is not regenerated, but arith-vec verifies it against the lock
if [ "${1:-}" = "--make" ]; then
  mkdir -p "$W/mk"; cp -p -R "$W/s1/arith" "$W/mk/arith"
  # undo the three truncations that hard-coded counts or floors catch, so the run shows what the count file alone guards
  for f in cvt_dec_e4m3_fp32 fp8_mul mxu_pe_dot; do cp -p "$ROOT/build/arith/$f.hex" "$W/mk/arith/"; setcnt "$W/mk" "CNT_$(echo $f | tr a-z A-Z)" "$(wc -l < "$W/mk/arith/$f.hex" | tr -d ' ')"; done
  touch "$W/mk/arith/.stamp"
  (cd "$ROOT" && make unit BUILD="$W/mk" > "$W/mk.log" 2>&1); rc=$?
  echo "S1 make unit BUILD=<S1 copy, 9 files truncated + counts edited>: rc=$rc, $(grep -c '^PASS' "$W/mk.log") PASS lines, last: $(tail -1 "$W/mk.log")   [rc must be non-zero, 0 PASS lines]"
  grep -E "^arith_lock" "$W/mk.log" | head -4 | sed 's/^/    /'
fi
