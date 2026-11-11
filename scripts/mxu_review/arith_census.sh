#!/bin/bash
# Review (implementability): the multipliers, dividers and wide adders/comparators of an MXU block after Yosys' width
# reduction (wreduce, peepopt) but before alumacc folds them into $macc / $alu, so each `*`, `/`, `%` of the source shows as
# one cell with its real operand widths. Prints one line per $mul / $div / $mod cell: type, A/B/Y widths, source line.
# Usage: scripts/memrun rev_census_<tag> scripts/mxu_review/arith_census.sh <top> <tag> [-set P V ...]
# Output: build/mxu_review/syn/<tag>.census.txt
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
TOP=$1; TAG=$2; shift 2
OUT=$ROOT/build/mxu_review/syn; mkdir -p "$OUT"
cd "$ROOT"
ARITH=$(ls rtl/arith/*.sv | sort | tr '\n' ' ')
case $TOP in
  mxu_wpack) FILES=rtl/core/mxu_wpack.sv ;;
  mxu_array) FILES="rtl/core/mxu_array.sv $ARITH" ;;
  mxu_dec)   FILES="isa/gen/isa_pkg.sv rtl/core/mxu_dec.sv" ;;
  mxu_dec_fix) FILES="isa/gen/isa_pkg.sv build/mxu_review/fix/mxu_dec.sv"; TOP=mxu_dec ;;   # fix_dec_narrow.py copy
  mxu)       FILES="isa/gen/isa_pkg.sv rtl/core/mxu.sv rtl/core/mxu_dec.sv rtl/core/mxu_wpack.sv" ;;  # array kept as a blackbox below
esac
BB=""; [ "$TOP" = mxu ] && BB="read_verilog -sv -lib rtl/core/mxu_array.sv;"
J=$OUT/$TAG.census.json
yosys -q -p "
$BB
read_verilog -sv $FILES;
${*:+chparam $* $TOP;}
hierarchy -check -auto-top;
proc; opt_expr; opt_clean; opt -nodffe -nosdff; wreduce; peepopt; opt_clean; opt -nodffe -nosdff; wreduce; opt_clean;
write_json $J;
"
python3 - "$J" "$OUT/$TAG.census.txt" <<'EOF'
import json, sys, collections
d = json.load(open(sys.argv[1]))
rows = []
for mname, m in d["modules"].items():
    if m.get("attributes", {}).get("blackbox"): continue
    for cname, c in m["cells"].items():
        t = c["type"]
        if t in ("$mul", "$div", "$mod", "$divfloor", "$modfloor", "$pow"):
            p = c["parameters"]
            w = lambda k: int(p[k], 2) if isinstance(p[k], str) else p[k]
            rows.append((mname.split("\\")[-1], t, w("A_WIDTH"), w("B_WIDTH"), w("Y_WIDTH"),
                         c["attributes"].get("src", "?")))
rows.sort(key=lambda r: (r[0], r[5]))
with open(sys.argv[2], "w") as f:
    for r in rows:
        f.write("%-10s %-5s A%-3d x B%-3d -> Y%-3d  %s\n" % r)
    cnt = collections.Counter((r[0], r[1]) for r in rows)
    f.write("total: " + ", ".join("%s %s %d" % (k[0], k[1], v) for k, v in sorted(cnt.items())) + "\n")
print(open(sys.argv[2]).read())
EOF
