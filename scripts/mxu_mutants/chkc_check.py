#!/usr/bin/env python3
"""The cycle-count check CHKC of tb_mxu (review TR2-2) against edited vectors: it does not depend on the RTL, so the checks are not RTL mutants.

tb_mxu -DQUICK (the 8 x 8 instance `t8` with CHKP / CHKC, and the DIM 8 fault run) runs against copies of build/mxu whose exp.hex row 12
({ACC_LAT, CS_DEPTH, SPM_LAT, count}, tb_mxu header) is edited:

  as_is        the vectors as generated                                        -> PASS
  stale_high   the recorded count 5 % above the real one (a faster RTL)        -> FAIL "more than 2 % below"   (two-sided check)
  stale_low    the recorded count 5 % below the real one (a slower RTL)        -> FAIL "more than 2 % above"
  within       the record 1.5 % above / below the real count                   -> PASS (both)
  other_spm    the recorded SPM_LAT differs from the instance's                -> PASS, prints "CHKC skipped"
  other_cs     the recorded CS_DEPTH differs                                   -> PASS, prints "CHKC skipped"
  other_acc    the recorded ACC_LAT differs                                    -> PASS, prints "CHKC skipped"
  no_count     count 0                                                         -> FAIL "no cycle count recorded"

Usage (needs build/mxu from `make mxu-vec`; about 2 minutes, 0.3 GB):
  scripts/memrun chkc_check python3 scripts/mxu_mutants/chkc_check.py
Exit status 0 when every case behaved as listed.
"""
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VEC = os.path.join(ROOT, "build", "mxu")
WORK = os.path.join(ROOT, "build", "mxu_mutants", "chkc")


def compile_tb():
    os.makedirs(WORK, exist_ok=True)
    exe = os.path.join(WORK, "tb.vvp")
    srcs = [os.path.join(ROOT, "tb/unit/tb_mxu.sv"), os.path.join(ROOT, "isa/gen/isa_pkg.sv")] + \
        sorted(os.path.join(ROOT, "rtl/core", f) for f in os.listdir(os.path.join(ROOT, "rtl/core")) if f.endswith(".sv")) + \
        sorted(os.path.join(ROOT, "rtl/arith", f) for f in os.listdir(os.path.join(ROOT, "rtl/arith")) if f.endswith(".sv"))
    c = subprocess.run(["iverilog", "-g2012", "-DQUICK", "-o", exe] + srcs, cwd=VEC, capture_output=True, text=True)
    if c.returncode != 0:
        sys.exit("compile failed: " + c.stderr[-500:])
    return exe


def run_case(exe, name, edit):
    d = os.path.join(WORK, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    for f in os.listdir(VEC):
        if f.startswith("mxu_8_") or f.startswith("mxu_fault_8") or f == "counts.svh":
            os.symlink(os.path.join(VEC, f), os.path.join(d, f))
    path = os.path.join(d, "mxu_8_exp.hex")
    os.unlink(path)
    lines = open(os.path.join(VEC, "mxu_8_exp.hex")).read().split("\n")
    v = int(lines[12], 16)
    cnt, spm, cs, acc = v & 0xFFFFFFFF, (v >> 32) & 255, (v >> 40) & 255, (v >> 48) & 255
    assert cnt != 0, "the vectors record no count for the 8 x 8 program"
    cnt, spm, cs, acc = edit(cnt, spm, cs, acc)
    lines[12] = format(cnt | (spm << 32) | (cs << 40) | (acc << 48), "x")
    open(path, "w").write("\n".join(lines))
    r = subprocess.run(["vvp", "-n", exe], cwd=d, capture_output=True, text=True, timeout=1800)
    return r.stdout


CASES = [
    ("as_is", lambda c, s, k, a: (c, s, k, a), True, None),
    ("stale_high", lambda c, s, k, a: (c * 105 // 100, s, k, a), False, "more than 2 % below"),
    ("stale_low", lambda c, s, k, a: (c * 95 // 100, s, k, a), False, "more than 2 % above"),
    ("within_high", lambda c, s, k, a: (c * 1015 // 1000, s, k, a), True, None),
    ("within_low", lambda c, s, k, a: (c * 985 // 1000, s, k, a), True, None),
    ("other_spm", lambda c, s, k, a: (c, s + 1, k, a), True, "CHKC skipped"),
    ("other_cs", lambda c, s, k, a: (c, s, k - 1, a), True, "CHKC skipped"),
    ("other_acc", lambda c, s, k, a: (c, s, k, a + 1), True, "CHKC skipped"),
    ("no_count", lambda c, s, k, a: (0, s, k, a), False, "no cycle count recorded"),
]


def main():
    exe = compile_tb()
    bad = 0
    for name, edit, passes, text in CASES:
        out = run_case(exe, name, edit)
        ok_pass = bool(re.search(r"^PASS mxu", out, re.M))
        ok = (ok_pass == passes) and (text is None or text in out)
        if not ok:
            bad += 1
        print("%-12s %-4s %s%s" % (name, "PASS" if ok_pass else "FAIL", "as expected" if ok else "<-- UNEXPECTED",
                                    "" if text is None else "  (output has '%s': %s)" % (text, text in out)), flush=True)
    print("cases: %d, unexpected: %d" % (len(CASES), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
