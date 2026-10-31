#!/usr/bin/env python3
"""P&R calibration wrapper: pe_pr_<N> = pe_array_<N>x<N> + registered 32-bit XOR fold of all
accumulators, so the block has few IO pins (the bare array has N*N*32 outputs).
Usage: gen_pe_pr.py N OUT.sv"""
import sys
n = int(sys.argv[1]); acc = 32
L = [f"// Auto-generated P&R wrapper for pe_array_{n}x{n}. Do not edit by hand.",
     f"module pe_pr_{n} (input logic clk, input logic rst_n,",
     f"  input logic [{n*8-1}:0] a_in, input logic [{n*8-1}:0] b_in, output logic [{acc-1}:0] y);",
     f"  logic [{n*n*acc-1}:0] c;",
     f"  pe_array_{n}x{n} u_arr (.clk, .rst_n, .a_in, .b_in, .c_out(c));",
     f"  logic [{acc-1}:0] fold;",
     "  always_comb begin", "    fold = '0;",
     f"    for (int k = 0; k < {n*n}; k++) fold = fold ^ c[k*{acc} +: {acc}];",
     "  end",
     "  always_ff @(posedge clk or negedge rst_n) if (!rst_n) y <= '0; else y <= fold;",
     "endmodule"]
open(sys.argv[2], "w").write("\n".join(L) + "\n")
