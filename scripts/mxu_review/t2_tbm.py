#!/usr/bin/env python3
"""Review part TR2, question 2: break one check that TR added to tb_mxu (t2_tb.py --tbm) and compare, for the unmutated RTL and for the
RTL mutants the check is there for, the intact testbench with the broken one on the same instances. A check is load-bearing for a
mutant when the intact testbench kills it and the broken one does not.

Groups:
  drain   the fault runs only (mxu_fault_tc at DIM 8 and 16, about 5 s per run), for every RTL mutant of tq_mutants.py,
          tr_mutants.py, t2_mutants.py and the author's trf_* / dec_* mutants: intact vs drain_off (TR-3 reverted), drain_window40 (lanes
          judged in the first 40 cycles only), drain_nolast, drain_norow, drain_nodone, legal_off (TR's mutant, re-anchored)
  dense   CHKC, the 64 semaphores, the second preload, the coverage counters, fcheck of sem / exp, on the instances that carry them
Results: build/mxu_review_t2/tbm_<group>.tsv (rtl, tbm, PASS|FAIL, per-instance error counts). Usage:
  source env.sh; scripts/memrun t2_tbm python3 scripts/mxu_review/t2_tbm.py drain [intact TBM ...] [--jobs N]   (ONLY=rtl,t2:x,... limits the RTLs)
  source env.sh; scripts/memrun t2_tbm python3 scripts/mxu_review/t2_tbm.py dense [--jobs N]
"""
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(ROOT, "build", "mxu_review_t2")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts", "mxu_mutants"))


def all_mutants():
    import tq_mutants
    import tr_mutants
    import t2_mutants  # noqa: F401  (sets tr_mutants.M to the t2 list: read the tr list first)
    names = []
    names += ["tq:" + m["name"] for m in tq_mutants.M if "@" not in m["name"]]
    import importlib
    trm = importlib.reload(tr_mutants)
    names += ["tr:" + m["name"] for m in trm.M if "@" not in m["name"]]
    names += ["t2:" + m["name"] for m in t2_mutants.M]
    import mxu_mutants
    names += ["au:" + m[0] for m in mxu_mutants.M if m[1] in ("mxu.sv", "mxu_dec.sv") or m[0].startswith("trf_")]
    return names


def run(run, rtl, tbm, insts, faults=True, vec=None, plus=None):
    cmd = [sys.executable, os.path.join(HERE, "t2_tb.py"), "--run", run, "--rtl", rtl]
    if tbm:
        cmd += ["--tbm", tbm]
    if faults:
        cmd += ["--faults"]
    if vec:
        cmd += ["--vec", vec]
    if plus:
        cmd += ["--plus", plus]
    r = subprocess.run(cmd + insts, capture_output=True, text=True, cwd=ROOT)
    o = r.stdout + r.stderr
    res = re.search(r"^RESULT \S+ (\S+)", o, re.M)
    errs = " ".join("%s=%s" % m for m in re.findall(r"^TR (\S+) errors (\d+)", o, re.M))
    first = ""
    for ln in o.splitlines():
        if re.match(r"^(legal|fault) case|^mxu_\w+: .*(cycles, more|sem\[|never full|TIMEOUT|rows|hex digits|before its wait)", ln):
            first = ln[:150]
            break
    return (res.group(1) if res else "NORESULT"), errs, first


def main():
    grp = sys.argv[1]
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 2
    path = os.path.join(OUT, "tbm_%s.tsv" % grp)
    done = set()
    if os.path.exists(path):
        done = set(tuple(l.split("\t")[:2]) for l in open(path))
    tasks = []
    if grp == "drain":
        tbms = [a for a in sys.argv[2:] if not a.startswith("--") and not a.isdigit()] or ["intact", "drain_off"]
        tbms = ["" if t == "intact" else t for t in tbms]
        only = os.environ.get("ONLY")
        for rtl in (only.split(",") if only else ["rtl"] + all_mutants()):
            for t in tbms:
                tasks.append((rtl, t, [], True, None, None))
    elif grp == "dense":
        P = lambda s: "t8=`P8" + s  # noqa: E731
        ideal = ["t8=`P8,.CHKP(1),.CHKC(1)", "t16=`P16,.CHKP(1),.CHKC(1)", "t32=`P32,.CHKP(1),.CHKC(1)",
                 "t8x=`P8X,.CHKP(1),.CHKC(1)", "t16x=`P16X,.SPM_LAT(3),.CHKP(1),.CHKC(1)"]
        pairs = [
            ("chkc_off", ideal, ["rtl", "au:trf_gap_plus1_m2", "au:gap_x_every_pass", "t2:t2_push_no_bypass", "t2:t2_ctx_after_tok",
                                 "t2:t2_gapx_plus1", "t2:t2_gap_bigm_plus1", "t2:t2_rs_depth_small"]),
            ("chkc_10pct", ideal, ["rtl", "au:trf_gap_plus1_m2", "au:gap_x_every_pass", "t2:t2_push_no_bypass", "t2:t2_ctx_after_tok"]),
            ("sem64_off", ["t8=`P8", "t16=`P16"], ["rtl", "tr:tr_sem_idx_b4", "tr:tr_sem_amt_b2", "tr:tr_inf_head_newest", "au:trf_wait_signed",
                                                   "t2:t2_wait_wval0"]),
            ("semhex_off", ["t8=`P8", "t16=`P16"], ["rtl", "tr:tr_sem_idx_b4", "tr:tr_sem_amt_b2", "au:trf_wait_signed"]),
            ("preload2_off", ["t8=`P8", "t16=`P16"], ["rtl", "au:trf_wait_signed"]),
            ("preload_off", ["t8=`P8", "t16=`P16"], ["rtl", "tq:dec_wait_unsigned"]),
            ("cov_inf_cnt4", ["t8d=`P8,.DONE_EXTRA(60),.EXP_INF_FULL(1)"], ["rtl", "tr:tr_inf_room3", "au:inf_mem_always"]),
            ("cov_inf_off", ["t8d=`P8,.DONE_EXTRA(60),.EXP_INF_FULL(1)"], ["rtl", "tr:tr_inf_room3", "au:inf_mem_always"]),
            ("cov_cs_off", ["t8l=`P8,.SPM_LAT(1),.CS_DEPTH(2),.EXP_CS_FULL(1)", "t16l=`P16,.SPM_LAT(3),.CS_DEPTH(2),.EXP_CS_FULL(1)"],
             ["rtl", "tr:tr_cs_full_minus1"]),
            ("fcheck_sem_off", [P("")], ["rtl"]),
            ("fcheck_exp12", [P("")], ["rtl"]),
        ]
        for tbm, insts, rtls in pairs:
            for rtl in rtls:
                for t in ("", tbm):
                    tasks.append((rtl, t, insts, False, None, None))
        # de-duplicate (the intact runs repeat across pairs with the same instances)
        seen, tt = set(), []
        for x in tasks:
            k = (x[0], x[1], tuple(x[2]))
            if k not in seen:
                seen.add(k)
                tt.append(x)
        tasks = tt
    tasks = [t for t in tasks if (t[0], t[1] or "intact") not in done or grp == "dense"]
    print("%d runs, %d jobs" % (len(tasks), jobs), flush=True)

    def one(t):
        rtl, tbm, insts, faults, vec, plus = t
        tag = "%s_%s_%s" % (grp, rtl.replace(":", "_"), tbm or "intact")
        if grp == "dense":
            tag += "_" + re.sub(r"\W", "", insts[0].split("=")[0]) + str(len(insts))
        res, errs, first = run(tag, rtl, tbm, insts, faults, vec, plus)
        line = "\t".join([rtl, tbm or "intact", res, ",".join(i.split("=")[0] for i in insts) or "faults", errs, first])
        with open(path, "a") as fh:
            fh.write(line + "\n")
        print(line, flush=True)
    with ThreadPoolExecutor(jobs) as ex:
        list(ex.map(one, tasks))


if __name__ == "__main__":
    main()
