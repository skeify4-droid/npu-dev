#!/bin/bash
# Review part TR2, question 3 / 4: the unmutated RTL (and two legal variants) against tb_mxu's checkers (929d820) in legal environments
# the tests do not run, through scripts/mxu_review/t2_tb.py. One configuration at a time; results appended to
# build/mxu_review_t2/env_results.txt ("RESULT <run> PASS|FAIL <s> ...") and the per-instance lines to build/mxu_review_t2/env.log.
# Usage: source env.sh; scripts/memrun t2_env scripts/mxu_review/t2_env.sh [GROUP ...]
# Groups: spl (SPM_LAT 1 .. 5 on the five ideal programs: cycles and the M = 1 period, the bounds unchecked), chk (the same with CHKP / CHKC
# at the SPM_LAT / CS_DEPTH / ACC_LAT / DONE_EXTRA values the instances do not use), csd (CS_DEPTH 2 .. 4 with CHKP / CHKC at the nominal
# SPM_LAT), acc (ACC_LAT 5 / 24, DONE_EXTRA 60 / 400 on the ideal programs), bank (the whole tb_mxu, 13 instances + faults, +BANK=0/1/2,
# three seeds), layout (the period instruction moved: t2_vec.py variants, +BANK=1 / 2, CHKP), var (legal RTL variants), verilator (lint of
# the patched testbench is not run: see the report).
cd "$(dirname "$0")/../.." || exit 1
OUT=build/mxu_review_t2
mkdir -p $OUT
R() { python3 scripts/mxu_review/t2_tb.py "$@" 2>&1 | tee -a $OUT/env.log | grep -E '^RESULT' >> $OUT/env_results.txt; }
I5() {   # the five ideal programs with extra parameters $1 (each instance's nominal SPM_LAT unless $1 sets one) and checks $2
  echo "t8=\`P8$1$2" "t16=\`P16$1$2" "t32=\`P32$1$2" "t8x=\`P8X$1$2" "t16x=\`P16X$1$2"
}
G=${*:-spl chk csd acc var}
for g in $G; do case $g in
spl)
  for s in 1 2 3 4 5; do R --run spl$s $(I5 ",.SPM_LAT($s)" ""); done
  ;;
chk)
  # the bounds where the header says they do not hold (fail by design) and where it says nothing (CS_DEPTH, seeds)
  R --run chk_spl1 $(I5 ",.SPM_LAT(1)" ",.CHKP(1),.CHKC(1)")
  R --run chk_spl3 $(I5 ",.SPM_LAT(3)" ",.CHKP(1),.CHKC(1)")
  R --run chk_acc5 $(I5 ",.ACC_LAT(5)" ",.CHKP(1),.CHKC(1)")
  R --run chk_dlat "t8=\`P8,.ACC_LAT(8),.DLAT(1),.CHKP(1),.CHKC(1)" "t16=\`P16,.DLAT(1),.CHKP(1),.CHKC(1)" "t8x=\`P8X,.DLAT(1),.CHKP(1),.CHKC(1)"
  R --run chk_dx2 "t8=\`P8,.DONE_EXTRA(2),.CHKP(1),.CHKC(1)" "t16=\`P16,.DONE_EXTRA(2),.CHKP(1),.CHKC(1)" "t8x=\`P8X,.DONE_EXTRA(2),.CHKP(1),.CHKC(1)" "t16x=\`P16X,.SPM_LAT(3),.DONE_EXTRA(2),.CHKP(1),.CHKC(1)"
  ;;
csd)
  for c in 2 3 4; do
    R --run csd$c "t8=\`P8,.CS_DEPTH($c),.CHKP(1),.CHKC(1)" "t16=\`P16,.CS_DEPTH($c),.CHKP(1),.CHKC(1)" "t32=\`P32,.CS_DEPTH($c),.CHKP(1),.CHKC(1)" \
      "t8x=\`P8X,.CS_DEPTH($c),.CHKP(1),.CHKC(1)" "t16x=\`P16X,.SPM_LAT(3),.CS_DEPTH($c),.CHKP(1),.CHKC(1)"
  done
  ;;
acc)
  for a in 5 24; do R --run acc$a $(I5 ",.ACC_LAT($a)" ""); done
  R --run accs24 "t8x=\`P8X,.STALL_PCT(30),.ACC_LAT(24)" "t16x=\`P16X,.STALL_PCT(30),.ACC_LAT(24),.SPM_LAT(5),.CS_DEPTH(3)" "t32=\`P32,.STALL_PCT(30),.ACC_LAT(24),.SPM_LAT(4)"
  R --run dx60 $(I5 ",.DONE_EXTRA(60)" "")
  R --run dx400 $(I5 ",.DONE_EXTRA(400)" "")
  ;;
bank)
  I13=("t8=\`P8,.STALL_PCT(0),.CHKP(1),.CHKC(1)" "t16=\`P16,.STALL_PCT(0),.CHKP(1),.CHKC(1)" "t8s=\`P8,.STALL_PCT(30),.ACC_LAT(24)" "t16s=\`P16,.STALL_PCT(30)"
       "t8b=\`P8,.STALL_PCT(0),.A_BLOCK(40)" "t32=\`P32,.STALL_PCT(0),.CHKP(1),.CHKC(1)" "t8r=\`P8,.STALL_PCT(30),.RDYV(1),.SPM_LAT(5),.CS_DEPTH(3)"
       "t16r=\`P16,.STALL_PCT(0),.RDYV(1)" "t8l=\`P8,.STALL_PCT(0),.SPM_LAT(1),.CS_DEPTH(2),.EXP_CS_FULL(1)"
       "t16l=\`P16,.STALL_PCT(0),.SPM_LAT(3),.CS_DEPTH(2),.EXP_CS_FULL(1)" "t8d=\`P8,.STALL_PCT(0),.DONE_EXTRA(60),.EXP_INF_FULL(1)"
       "t8x=\`P8X,.STALL_PCT(0),.CHKP(1),.CHKC(1)" "t16x=\`P16X,.STALL_PCT(0),.SPM_LAT(3),.CHKP(1),.CHKC(1)")
  for b in ${BANKS:-0 1 2}; do
    for sd in ${SEEDS:-0 11 23}; do
      I=("${I13[@]/%/,.SEED($sd)}")
      R --run bank${b}_seed$sd --faults --plus +BANK=$b "${I[@]}"
    done
  done
  ;;
layout)
  for v in ${LAYOUTS:-shift256 shift512 shift768 shift1024 shift1280 shift1536 shift1792 shift2048 shift4096 wpad960 wpad1984 shift768_wpad960}; do
    [ -d $OUT/vec/$v ] || python3 scripts/mxu_review/t2_vec.py $v >> $OUT/env.log
    for b in 0 1 2; do
      R --run lay_${v}_b$b --vec $OUT/vec/$v --plus +BANK=$b "t8=\`P8,.CHKP(1)" "t16=\`P16,.CHKP(1)"
    done
  done
  ;;
var)
  for v in sem_reg acc_reg; do
    R --run var_$v --rtl var:$v --faults "t8=\`P8,.CHKP(1),.CHKC(1)" "t16=\`P16,.CHKP(1),.CHKC(1)" "t8d=\`P8,.DONE_EXTRA(60),.EXP_INF_FULL(1)" "t8x=\`P8X,.CHKP(1),.CHKC(1)"
  done
  ;;
esac; done
