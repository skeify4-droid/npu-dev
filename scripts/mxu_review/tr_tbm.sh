#!/bin/bash
# Review part TR, question 2: break one check of tb_mxu at a time (tr_tb.py --tbm) and see (a) whether the unmutated RTL still passes
# and (b) whether the RTL mutants that this check is there for are still killed, comparing the intact and the broken testbench on the
# same instances. Results: build/mxu_review_tr/tbm_results.txt. Usage: scripts/memrun tr_tbm scripts/mxu_review/tr_tbm.sh [GROUP ...]
cd "$(dirname "$0")/../.." || exit 1
OUT=build/mxu_review_tr
R() { python3 scripts/mxu_review/tr_tb.py "$@" 2>&1 | tee -a $OUT/tbm.log | grep -E '^RESULT' >> $OUT/tbm_results.txt; }
# run the instance set $I with the intact testbench and with --tbm $1, for the unmutated RTL and every RTL mutant given
pair() {
  local tbm=$1; shift
  for rtl in rtl "$@"; do
    local tag=${rtl//:/_}
    [ "$rtl" = rtl ] || R --run "tbm_${tbm}_${tag}_intact" --rtl "$rtl" $F "${I[@]}"
    R --run "tbm_${tbm}_${tag}" --rtl "$rtl" --tbm "$tbm" $F "${I[@]}"
  done
}
G=${*:-retire flags done chkp preload acclast legal}
for g in $G; do case $g in
retire)
  F=; I=("t8=\`P8" "t16=\`P16" "t8d=\`P8,.DONE_EXTRA(60),.EXP_INF_FULL(1)")
  pair retire_seq_off tr:tr_inf_head_newest tr:tr_sem_amt_b2 tr:tr_sem_idx_b4 tr:tr_zinc_with_pop au:inf_mem_always
  pair retire_early_off tq:ex_pop_at_last_row tr:tr_pop_row_or_done tr:tr_last_first_row
  ;;
flags)
  F=; I=("t8=\`P8" "t16=\`P16")
  pair flag_ring_off tr:tr_flags_reg1 tr:tr_flags_validonly tq:ex_flags_late tq:ex_flags_sticky tq:ex_flags_nomask
  pair flag_or_off tr:tr_flags_reg1 tq:ex_flags_late tq:ex_flags_nomask
  ;;
done)
  F=; I=("t8=\`P8" "t16=\`P16" "t8d=\`P8,.DONE_EXTRA(60),.EXP_INF_FULL(1)")
  pair done_early1 tq:ex_pop_at_last_row
  pair done_noextra au:inf_mem_always
  pair done_noorder
  ;;
chkp)
  F=; I=("t8=\`P8,.CHKP(1)" "t16=\`P16,.CHKP(1)" "t32=\`P32,.CHKP(1)")
  pair chkp_off tq:ex_gap_plus1 tq:bf_one_buffer
  ;;
preload)
  F=; I=("t8=\`P8" "t16=\`P16")
  pair preload_off tq:dec_wait_unsigned
  ;;
acclast)
  F=; I=("t8=\`P8" "t16=\`P16")
  pair acclast_off tr:tr_last_first_row tr:tr_last_every_pass_jlast tq:ex_last_every_pass
  ;;
legal)
  F=--faults; I=("t8=\`P8" "t16=\`P16" "t8x=\`P8X" "t16x=\`P16X,.SPM_LAT(3)")
  pair legal_off tq:dec_a_range_ge tq:dec_w_range_ge tq:dec_rs_range_ge tq:dec_acc_ge tr:tr_acc_ge16
  ;;
esac; done
