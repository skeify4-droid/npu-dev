#!/usr/bin/env python3
"""Elaboration guards of mxu / mxu_dec (review I-10, mxu.md section 5): a parameter outside its range must stop Icarus, Verilator and
Yosys (hierarchy), a parameter at the edge of its range must elaborate on all three.

Usage: python3 scripts/mxu_mutants/guard_check.py [RTL_CORE_DIR]      (default rtl/core; the mutant runner passes a mutated copy)
Exit status 0 when every case behaves as expected. About 10 s.
"""
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CORE = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(ROOT, "rtl", "core")
ARITH = sorted(os.path.join(ROOT, "rtl/arith", f) for f in os.listdir(os.path.join(ROOT, "rtl/arith")) if f.endswith(".sv"))
FILES = [os.path.join(ROOT, "isa/gen/isa_pkg.sv")] + sorted(os.path.join(CORE, f) for f in os.listdir(CORE) if f.endswith(".sv")) + ARITH

# (top, parameter, value, must_elaborate)
CASES = []
# DIM 32 / 64 are not elaborated here (4096 PEs per tool); `make lint` does them (CORE_LINT_VAR)
for v, ok in ((4, 0), (12, 0), (8, 1), (16, 1)):
    CASES.append(("mxu", "DIM", v, ok))
for v, ok in ((0, 0), (1, 1), (2, 1)):
    CASES.append(("mxu", "SPM_LAT", v, ok))
for v, ok in ((0, 0), (1, 0), (2, 1), (4, 1), (5, 1)):
    CASES.append(("mxu", "CS_DEPTH", v, ok))
for v, ok in ((0, 0), (1, 1), (8, 1), (255, 1), (256, 0), (300, 0)):
    CASES.append(("mxu", "ACC_LAT", v, ok))
for v, ok in ((0, 0), (1, 0), (2, 1), (2048, 1), (2049, 0)):
    CASES.append(("mxu_dec", "ACC_ROWS", v, ok))
for v, ok in ((12, 0), (8, 1)):
    CASES.append(("mxu_dec", "DIM", v, ok))


def run(cmd):
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0


def tools(top, par, val):
    yield "iverilog", run(["iverilog", "-g2012", "-s", top, "-P", "%s.%s=%d" % (top, par, val), "-o", os.devnull] + FILES)
    yield "verilator", run(["verilator", "--lint-only", "-Wall", "--top-module", top, "-G%s=%d" % (par, val)] + FILES)
    ys = "read_verilog -sv %s; hierarchy -check -top %s -chparam %s %d" % (" ".join(FILES), top, par, val)
    yield "yosys", run(["yosys", "-q", "-p", ys])


bad = 0
for top, par, val, ok in CASES:
    res = dict(tools(top, par, val))
    wrong = [t for t, passed in res.items() if passed != bool(ok)]
    if wrong:
        bad += 1
    print("%-8s %-9s = %-5d %-14s %s" % (top, par, val, "elaborates" if ok else "must fail", ("UNEXPECTED in " + ", ".join(wrong)) if wrong else "ok"), flush=True)
print("guard cases: %d, unexpected: %d" % (len(CASES), bad))
sys.exit(1 if bad else 0)
