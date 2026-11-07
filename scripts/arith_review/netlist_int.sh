#!/bin/bash
# Second review (F9): synthesize cvt_fp32_int with Yosys for the five integer types (flattened gate netlists) and run every
# row of build/arith/cvt_fp32_<type>.hex through each netlist. Shows that the fixed RTL (rs > 24, no out-of-range bit
# select) synthesizes to the behaviour the simulator checked. Outputs in build/arith_review/netlist_int/.
# Usage: scripts/memrun arith_netlist_int scripts/arith_review/netlist_int.sh
set -u
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
W=$ROOT/build/arith_review/netlist_int
rm -rf "$W"; mkdir -p "$W"
SRC=$(echo "$ROOT"/rtl/arith/*.sv)
TB=$W/tb.sv
echo '`timescale 1ns/1ps' > "$TB"
echo '`include "arith/counts.svh"' >> "$TB"
echo 'module tb; int e = 0, n = 0;' >> "$TB"
for cfg in "int8 8 1" "uint8 8 0" "int16 16 1" "int32 32 1" "uint32 32 0"; do
  set -- $cfg; tag=$1; bits=$2; sg=$3
  yosys -q -p "read_verilog -sv $SRC; hierarchy -top cvt_fp32_int -chparam BITS $bits -chparam SIGNED $sg; synth -top cvt_fp32_int -flatten; rename cvt_fp32_int net_$tag; write_verilog -noattr $W/net_$tag.v" || { echo "yosys failed: $tag"; exit 1; }
  M=CNT_CVT_FP32_$(echo $tag | tr a-z A-Z)
  cat >> "$TB" <<EOF
  logic [67:0] m_$tag [0:\`$M-1]; logic [31:0] x_$tag, y_$tag; logic [3:0] f_$tag;
  net_$tag u_$tag (.x(x_$tag), .y(y_$tag), .flags(f_$tag));
  initial begin
    \$readmemh("arith/cvt_fp32_$tag.hex", m_$tag);
    for (int i = 0; i < \`$M; i++) begin
      x_$tag = m_$tag[i][67:36]; #1; n++;
      if (y_$tag !== m_$tag[i][35:4] || f_$tag !== m_$tag[i][3:0]) begin e++; if (e < 5) \$display("MISMATCH $tag x=%h y=%h f=%h exp %h %h", x_$tag, y_$tag, f_$tag, m_$tag[i][35:4], m_$tag[i][3:0]); end
    end
    \$display("$tag: %0d rows", \`$M);
  end
EOF
done
echo '  initial begin #100000000; if (e == 0) $display("PASS netlist cvt_fp32_int (%0d rows)", n); else $display("FAIL netlist cvt_fp32_int: %0d errors", e); $finish; end' >> "$TB"
echo 'endmodule' >> "$TB"
iverilog -g2012 -I "$ROOT/build" -o "$W/sim" "$TB" "$W"/net_*.v || exit 1
(cd "$ROOT/build" && vvp -n "$W/sim")
