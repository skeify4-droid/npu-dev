#!/bin/bash
# Review part TR, question 3: the unmutated RTL against tb_mxu's checkers in other legal environments (scripts/mxu_review/tr_tb.py).
# One configuration at a time; results appended to build/mxu_review_tr/env_results.txt ("RESULT <run> PASS|FAIL <s> ...") and
# the per-instance lines to build/mxu_review_tr/env.log. Usage: scripts/memrun tr_env scripts/mxu_review/tr_env.sh [GROUP ...]
# Groups: acc spl csd stall dx bank sparse rdyv d32 (default: all but bank and d32).
cd "$(dirname "$0")/../.." || exit 1
OUT=build/mxu_review_tr
mkdir -p $OUT
R() { python3 scripts/mxu_review/tr_tb.py "$@" 2>&1 | tee -a $OUT/env.log | grep -E '^RESULT' >> $OUT/env_results.txt; }
L=40000
G=${*:-acc spl csd stall dx sparse rdyv}
for g in $G; do case $g in
acc)
  for a in 5 24; do
    R --run acc$a "t8=\`P8,.ACC_LAT($a)" "t8s=\`P8,.STALL_PCT(30),.ACC_LAT($a)" "t16=\`P16,.ACC_LAT($a)" "t16s=\`P16,.STALL_PCT(30),.ACC_LAT($a)"
  done
  R --run acc255 "t8=\`P8,.ACC_LAT(255)"                                         # the original cycle limit
  R --run acc255L "t8=\`P8,.ACC_LAT(255),.TOUT(2000000)" "t16=\`P16,.ACC_LAT(255),.TOUT(2000000)" "t8s=\`P8,.STALL_PCT(30),.ACC_LAT(255),.TOUT(2000000)"
  # ACC_LAT above the ACC's own acc_done delay (mxu.md 5: legal, idle cycles only)
  R --run dlat "t8a=\`P8,.ACC_LAT(9),.DLAT(8)" "t8b=\`P8,.ACC_LAT(24),.DLAT(8)" "t16b=\`P16,.ACC_LAT(24),.DLAT(8)" "t8c=\`P8,.STALL_PCT(30),.ACC_LAT(24),.DLAT(1)" "t8d=\`P8,.ACC_LAT(24),.DLAT(8),.DONE_EXTRA(60)"
  # the period bound of the ideal instances at other ACC_LAT
  R --run chkp_acc5 "t8=\`P8,.ACC_LAT(5),.CHKP(1)" "t16=\`P16,.ACC_LAT(5),.CHKP(1)"
  R --run chkp_acc24 "t8=\`P8,.ACC_LAT(24),.CHKP(1)" "t16=\`P16,.ACC_LAT(24),.CHKP(1)"
  ;;
spl)
  for s in 1 2 3 4 5; do
    R --run spl$s "t8=\`P8,.SPM_LAT($s)" "t8s=\`P8,.STALL_PCT(30),.SPM_LAT($s)" "t16=\`P16,.SPM_LAT($s)" "t16s=\`P16,.STALL_PCT(30),.SPM_LAT($s),.ACC_LAT(24)"
    R --run chkp_spl$s "t8=\`P8,.SPM_LAT($s),.CHKP(1)" "t16=\`P16,.SPM_LAT($s),.CHKP(1)"
  done
  ;;
csd)
  for s in 1 2 3; do
    R --run csd3_spl$s "t8=\`P8,.CS_DEPTH(3),.SPM_LAT($s)" "t8s=\`P8,.STALL_PCT(30),.CS_DEPTH(3),.SPM_LAT($s)" "t16=\`P16,.CS_DEPTH(3),.SPM_LAT($s)" "t16s=\`P16,.STALL_PCT(30),.CS_DEPTH(3),.SPM_LAT($s)"
  done
  R --run csd3_full "t8=\`P8,.CS_DEPTH(3),.SPM_LAT(1),.EXP_CS_FULL(1)" "t16=\`P16,.CS_DEPTH(3),.SPM_LAT(3),.EXP_CS_FULL(1)"
  R --run csd2_s "t8s=\`P8,.STALL_PCT(30),.CS_DEPTH(2),.SPM_LAT(1)" "t16s=\`P16,.STALL_PCT(30),.CS_DEPTH(2),.SPM_LAT(3)"
  R --run csd4_full "t8=\`P8,.CS_DEPTH(4),.SPM_LAT(1),.EXP_CS_FULL(1)" "t16=\`P16,.CS_DEPTH(4),.SPM_LAT(3),.EXP_CS_FULL(1)"
  ;;
stall)
  R --run stall_chkp "t8s=\`P8,.STALL_PCT(30),.CHKP(1)" "t16s=\`P16,.STALL_PCT(30),.CHKP(1)" "t8b=\`P8,.A_BLOCK(40),.CHKP(1)"
  ;;
dx)
  R --run dx400 "t8=\`P8,.DONE_EXTRA(400)" "t16=\`P16,.DONE_EXTRA(400)"
  R --run dx2000 "t8=\`P8,.DONE_EXTRA(2000),.TOUT(2000000)" "t8s=\`P8,.STALL_PCT(30),.DONE_EXTRA(2000),.TOUT(2000000)"
  R --run dx400_inf "t8=\`P8,.DONE_EXTRA(400),.EXP_INF_FULL(1)" "t16=\`P16,.DONE_EXTRA(60),.EXP_INF_FULL(1)"
  ;;
sparse)
  R --run sparse_env "t8x1=\`P8X,.SPM_LAT(1),.ACC_LAT(24),.STALL_PCT(30)" "t16x5=\`P16X,.SPM_LAT(5),.STALL_PCT(30),.CS_DEPTH(2)" "t8x3=\`P8X,.CS_DEPTH(3),.DONE_EXTRA(60)" "t8xc=\`P8X,.CHKP(1)" "t16xc=\`P16X,.CHKP(1),.SPM_LAT(3)"
  ;;
rdyv)
  R --run rdyv_env "t8r1=\`P8,.STALL_PCT(30),.RDYV(1),.SPM_LAT(1),.CS_DEPTH(2)" "t8r5=\`P8,.STALL_PCT(30),.RDYV(1),.SPM_LAT(5),.ACC_LAT(24)" "t16r3=\`P16,.STALL_PCT(30),.RDYV(1),.SPM_LAT(3),.CS_DEPTH(3)"
  ;;
bank)
  # the whole tb_mxu (its 13 instances and both fault runs, as make unit runs them) with the bank models
  I13=("t8=\`P8,.STALL_PCT(0),.CHKP(1)" "t16=\`P16,.STALL_PCT(0),.CHKP(1)" "t8s=\`P8,.STALL_PCT(30),.ACC_LAT(24)" "t16s=\`P16,.STALL_PCT(30)"
       "t8b=\`P8,.STALL_PCT(0),.A_BLOCK(40)" "t32=\`P32,.STALL_PCT(0),.CHKP(1)" "t8r=\`P8,.STALL_PCT(30),.RDYV(1)" "t16r=\`P16,.STALL_PCT(0),.RDYV(1)"
       "t8l=\`P8,.STALL_PCT(0),.SPM_LAT(1),.CS_DEPTH(2),.EXP_CS_FULL(1)" "t16l=\`P16,.STALL_PCT(0),.SPM_LAT(3),.CS_DEPTH(2),.EXP_CS_FULL(1)"
       "t8d=\`P8,.STALL_PCT(0),.DONE_EXTRA(60),.EXP_INF_FULL(1)" "t8x=\`P8X,.STALL_PCT(0)" "t16x=\`P16X,.STALL_PCT(0),.SPM_LAT(3)")
  R --run bank0 --faults "${I13[@]}"
  R --run bank1 --faults --plus +BANK=1 "${I13[@]}"
  R --run bank2 --faults --plus +BANK=2 "${I13[@]}"
  ;;
d32)
  R --run d32_env "t32a=\`P32,.SPM_LAT(3),.ACC_LAT(24),.STALL_PCT(30)" "t32b=\`P32,.SPM_LAT(1),.CS_DEPTH(2),.DONE_EXTRA(60)"
  ;;
esac; done
