#!/usr/bin/env python3
"""Generate an N x N output-stationary MAC array for synthesis calibration (not the final MXU).
Each PE: signed 8x8 multiply, 32-bit accumulate, operands forwarded right (a) and down (b).
Ports are packed vectors so Yosys reads them. Usage: gen_pe_array.py N OUT.sv
"""
import sys

def gen(n: int, w: int = 8, acc: int = 32) -> str:
    L = [
        f"// Auto-generated {n}x{n} MAC array (calibration only). Do not edit by hand.",
        f"module pe_array_{n}x{n} (",
        "  input  logic clk,",
        "  input  logic rst_n,",
        f"  input  logic [{n*w-1}:0] a_in,   // row i operand at [i*{w} +: {w}]",
        f"  input  logic [{n*w-1}:0] b_in,   // col j operand at [j*{w} +: {w}]",
        f"  output logic [{n*n*acc-1}:0] c_out // PE(i,j) at [(i*{n}+j)*{acc} +: {acc}]",
        ");",
        f"  logic signed [{w-1}:0] a_r [0:{n-1}][0:{n-1}];",
        f"  logic signed [{w-1}:0] b_r [0:{n-1}][0:{n-1}];",
        f"  logic signed [{acc-1}:0] c_r [0:{n-1}][0:{n-1}];",
        "  always_ff @(posedge clk or negedge rst_n) begin",
        "    if (!rst_n) begin",
        f"      for (int i = 0; i < {n}; i++) for (int j = 0; j < {n}; j++) begin",
        "        a_r[i][j] <= '0; b_r[i][j] <= '0; c_r[i][j] <= '0;",
        "      end",
        "    end else begin",
        f"      for (int i = 0; i < {n}; i++) for (int j = 0; j < {n}; j++) begin",
        f"        a_r[i][j] <= (j == 0) ? $signed(a_in[i*{w} +: {w}]) : a_r[i][j-1];",
        f"        b_r[i][j] <= (i == 0) ? $signed(b_in[j*{w} +: {w}]) : b_r[i-1][j];",
        f"        c_r[i][j] <= c_r[i][j] + {acc}'(a_r[i][j] * b_r[i][j]);",
        "      end",
        "    end",
        "  end",
    ]
    for i in range(n):
        for j in range(n):
            L.append(f"  assign c_out[({i}*{n}+{j})*{acc} +: {acc}] = c_r[{i}][{j}];")
    L.append("endmodule")
    return "\n".join(L)

if __name__ == "__main__":
    open(sys.argv[2], "w").write(gen(int(sys.argv[1])) + "\n")
