#!/bin/bash
# Review (implementability), finding I-2: with `mxu_pe` read as a blackbox (a separately synthesized or hard-macro PE), Yosys keeps the
# unpacked `ps[ROWS][COLS]` / `fl[ROWS][COLS]` of mxu_array as memories (no mem2reg), and `opt_clean` deletes every PE instance.
# This script (1) shows it on the author's mxu_array, (2) makes a copy with `ps` / `fl` packed (build/mxu_review/fix/mxu_array.sv,
# the author's file is not touched), shows that the PEs survive there, and (3) proves the copy equivalent to the original with a small
# non-blackbox PE stand-in (Yosys equiv_make / equiv_simple / equiv_induct), ROWS = COLS = 8 and 16.
# Usage: scripts/memrun rev_fix_array scripts/mxu_review/fix_array_packed.sh
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
F=build/mxu_review/fix; mkdir -p "$F"
python3 - <<'EOF'
s = open('rtl/core/mxu_array.sv').read()
for old, new in (("  logic [31:0] ps [ROWS][COLS];", "  logic [ROWS-1:0][COLS-1:0][31:0] ps;"),
                 ("  logic [3:0]  fl [ROWS][COLS];", "  logic [ROWS-1:0][COLS-1:0][3:0]  fl;")):
    assert s.count(old) == 1, old
    s = s.replace(old, new)
open('build/mxu_review/fix/mxu_array.sv', 'w').write(s)
EOF
# a PE stand-in with the same ports and latency shape (not a blackbox, so the original elaborates the usual way)
cat >"$F/pe_standin.sv" <<'EOF'
module mxu_pe #(parameter bit REG_DOT = 1'b1, parameter bit REG_OUT = 1'b1) (
  input logic clk, rst_n, mode_bf16, input logic [31:0] a, w, psum_in, output logic [31:0] psum_out, output logic [3:0] flags);
  logic [31:0] d;
  always_ff @(posedge clk) d <= (a ^ w) + {31'd0, mode_bf16};
  always_ff @(posedge clk) psum_out <= psum_in + d;
  always_ff @(posedge clk or negedge rst_n) if (!rst_n) flags <= '0; else flags <= d[3:0] ^ psum_in[7:4];
endmodule
EOF
printf 'module mxu_pe #(parameter bit REG_DOT=1, parameter bit REG_OUT=1) (input logic clk, rst_n, mode_bf16, input logic [31:0] a, w, psum_in, output logic [31:0] psum_out, output logic [3:0] flags);\nendmodule\n' >"$F/pe_bb.sv"

for src in rtl/core/mxu_array.sv "$F/mxu_array.sv"; do
  yosys -q -p "read_verilog -sv -lib $F/pe_bb.sv; read_verilog -sv $src; hierarchy -top mxu_array; proc; opt_clean;
               tee -q -o $F/count.txt select -count t:mxu_pe" >/dev/null
  echo "PE blackbox, $src: $(cat $F/count.txt) mxu_pe instances left after proc; opt_clean (want 64)"
done

for d in 8 16; do
  yosys -q -l "$F/equiv_$d.log" -p "
    read_verilog -sv $F/pe_standin.sv rtl/core/mxu_array.sv; chparam -set ROWS $d -set COLS $d mxu_array; hierarchy -auto-top;
    proc; flatten; opt_clean; rename -top gold; design -stash gold;
    read_verilog -sv $F/pe_standin.sv $F/mxu_array.sv; chparam -set ROWS $d -set COLS $d mxu_array; hierarchy -auto-top;
    proc; flatten; opt_clean; rename -top gate; design -stash gate;
    design -copy-from gold -as gold gold; design -copy-from gate -as gate gate;
    equiv_make gold gate equiv; hierarchy -top equiv; async2sync; equiv_simple -seq 2; equiv_induct; equiv_status -assert" >/dev/null
  echo "ROWS = COLS = $d: packed copy equivalent to the original ($(grep -E 'Of those cells' "$F/equiv_$d.log" | tail -1 | xargs))"
done
