// Self-checking testbench for bf16_dot2 (iverilog -g2012), bit-exact against the exact reference of
// sw/npumodel/arith_ref.py (vectors from scripts/gen_arith_vectors.py: random, corner and cancellation cases).
`timescale 1ns/1ps
`include "counts.svh"   // CNT_<file>: rows of each vector file (scripts/gen_arith_vectors.py)
module tb_bf16_dot2;
  localparam int MAXN = `CNT_BF16_DOT2;      // exact row count of the file
  logic [99:0] mem [0:MAXN-1];
  logic [31:0] a, w, y;
  logic [3:0] flags;
  int errors = 0, n = 0;

  bf16_dot2 dut (.a(a), .w(w), .y(y), .flags(flags));

  initial begin
    $readmemh("arith/bf16_dot2.hex", mem);
    for (int i = 0; i < MAXN; i++) begin
      if (^mem[i] === 1'bx) break;
      a = mem[i][99:68]; w = mem[i][67:36];
      #1;
      n++;
      if (y !== mem[i][35:4] || flags !== mem[i][3:0]) begin
        errors++;
        if (errors <= 10) $display("MISMATCH a=%h w=%h y=%h flags=%h  exp y=%h flags=%h", a, w, y, flags, mem[i][35:4], mem[i][3:0]);
      end
    end
    if (n != MAXN || MAXN < 1) begin errors++; $display("expected %0d vectors, read %0d", MAXN, n); end
    if (errors == 0) $display("PASS bf16_dot2 (%0d vectors)", n);
    else $display("FAIL bf16_dot2: %0d errors of %0d", errors, n);
    $finish;
  end
endmodule
