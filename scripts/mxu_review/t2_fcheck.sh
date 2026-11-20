#!/bin/bash
# Review part TR2, question 2 (fcheck of the files TR added): corrupted copies of build/mxu (sem.hex, exp.hex rows 12 / 13) judged by
# (1) the vector lock, (2) the intact tb_mxu checkers run directly (t2_tb.py, no lock), (3) the same with fcheck off. Results:
# build/mxu_review_t2/fcheck.txt. Usage: source env.sh; scripts/memrun t2_fcheck scripts/mxu_review/t2_fcheck.sh
cd "$(dirname "$0")/../.." || exit 1
OUT=build/mxu_review_t2
V=$OUT/vec_bad
: > $OUT/fcheck.txt
corrupt() {   # name, python snippet that edits files in $V (cwd)
  rm -rf $V; cp -Rp build/mxu $V
  (cd $V && python3 -c "$2")
  lock=$(python3 scripts/mxu_lock.py verify $V >/dev/null 2>&1 && echo pass || echo FAIL)
  for t in intact fcheck_off; do
    tb=; [ $t = fcheck_off ] && tb="--tbm fcheck_off"
    o=$(python3 scripts/mxu_review/t2_tb.py --run fck_$1_$t --vec $V $tb "t8=\`P8,.CHKC(1)" 2>&1)
    res=$(echo "$o" | grep -E '^RESULT' | awk '{print $3}')
    warn=$(echo "$o" | grep -cE 'Not enough words|Too many words|Unable to open')
    why=$(echo "$o" | grep -E 'rows|hex digits|sem\[|TIMEOUT|cycles, more|errors [1-9]' | grep -v "ACC rows in" | head -2 | tr '\n' ' ' | cut -c1-150)
    echo -e "$1\t$t\tlock=$lock\ttb=$res\treadmemh_warnings=$warn\t$why" >> $OUT/fcheck.txt
  done
}
corrupt sem_short   "l=open('mxu_8_sem.hex').read().split('\n'); open('mxu_8_sem.hex','w').write('\n'.join(l[:63])+'\n')"
corrupt sem_long    "open('mxu_8_sem.hex','a').write('00000000\n')"
corrupt sem_digit   "l=open('mxu_8_sem.hex').read().split('\n'); l[41]=l[41][1:]; open('mxu_8_sem.hex','w').write('\n'.join(l))"
corrupt sem_wrong   "l=open('mxu_8_sem.hex').read().split('\n'); l[42]='80000002'; open('mxu_8_sem.hex','w').write('\n'.join(l))"
corrupt exp_13rows  "l=open('mxu_8_exp.hex').read().split('\n'); open('mxu_8_exp.hex','w').write('\n'.join(l[:13])+'\n')"
corrupt exp_12rows  "l=open('mxu_8_exp.hex').read().split('\n'); open('mxu_8_exp.hex','w').write('\n'.join(l[:12])+'\n')"
corrupt exp_cyc0    "l=open('mxu_8_exp.hex').read().split('\n'); l[12]='0'; open('mxu_8_exp.hex','w').write('\n'.join(l))"
corrupt exp_cycbig  "l=open('mxu_8_exp.hex').read().split('\n'); l[12]='%x' % (2281*2); open('mxu_8_exp.hex','w').write('\n'.join(l))"
rm -rf $V
cat $OUT/fcheck.txt
