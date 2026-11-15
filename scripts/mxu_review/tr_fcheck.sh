#!/bin/bash
# Review part TR, question 2 (fcheck): corrupted copies of build/mxu against (1) the vector lock (scripts/mxu_lock.py verify, which make
# mxu-vec / make unit run), (2) the intact tb_mxu checkers run directly (vvp, no lock, as review scripts run them), (3) the same with
# fcheck disabled (tr_tb.py --tbm fcheck_off), and the $readmemh warnings make unit greps for. Results: build/mxu_review_tr/fcheck.txt
cd "$(dirname "$0")/../.." || exit 1
OUT=build/mxu_review_tr
V=$OUT/vec_bad
: > $OUT/fcheck.txt
corrupt() {   # name, python snippet that edits files in $V (cwd)
  rm -rf $V; cp -Rp build/mxu $V
  (cd $V && python3 -c "$2")
  lock=$(python3 scripts/mxu_lock.py verify $V >/dev/null 2>&1 && echo pass || echo FAIL)
  for t in intact fcheck_off; do
    tb=; [ $t = fcheck_off ] && tb="--tbm fcheck_off"
    o=$(python3 scripts/mxu_review/tr_tb.py --run fck_$1_$t --vec $V $tb $3 "t8=\`P8" 2>&1)
    res=$(echo "$o" | grep -E '^RESULT' | awk '{print $3}')
    warn=$(echo "$o" | grep -cE 'Not enough words|Too many words|Unable to open')
    why=$(echo "$o" | grep -E 'rows|hex digits|errors [1-9]|FAIL' | head -2 | tr '\n' ' ' | cut -c1-150)
    echo -e "$1\t$t\tlock=$lock\ttb=$res\treadmemh_warnings=$warn\t$why" >> $OUT/fcheck.txt
  done
}
corrupt flg_short   "l=open('mxu_8_flg.hex').read().split('\n'); open('mxu_8_flg.hex','w').write('\n'.join(l[:-2])+'\n')"
corrupt ins_lastdig "l=open('mxu_8_ins.hex').read().split('\n'); l[36]=l[36][:-1]; open('mxu_8_ins.hex','w').write('\n'.join(l))"
corrupt flg_lead0   "l=open('mxu_8_flg.hex').read().split('\n'); i=[k for k,x in enumerate(l) if x.startswith('0')][0]; l[i]=l[i][1:]; open('mxu_8_flg.hex','w').write('\n'.join(l))"
corrupt exp_long    "open('mxu_8_exp.hex','a').write('0\n')"
corrupt exp_short   "l=open('mxu_8_exp.hex').read().split('\n'); open('mxu_8_exp.hex','w').write('\n'.join(l[:7])+'\n')"
corrupt fault_short "l=open('mxu_fault_8.hex').read().split('\n'); open('mxu_fault_8.hex','w').write('\n'.join(l[:-2])+'\n')" --faults
corrupt ins_swap    "l=open('mxu_8_ins.hex').read().split('\n'); l[37],l[38]=l[38],l[37]; open('mxu_8_ins.hex','w').write('\n'.join(l))"
rm -rf $V
cat $OUT/fcheck.txt
