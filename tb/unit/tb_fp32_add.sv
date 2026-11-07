// Self-checking testbench for fp32_add (iverilog -g2012), bit-exact against the exact reference of
// sw/npumodel/arith_ref.py (vectors from scripts/gen_arith_vectors.py: specials cross product, random operand
// flavours, alignment shifts 0..40, cancellation, results around 2^-126, near the maximum).
`timescale 1ns/1ps
`include "counts.svh"   // CNT_<file>: rows of each vector file (scripts/gen_arith_vectors.py)
module tb_fp32_add;
  localparam int MAXN = `CNT_FP32_ADD;      // exact row count of the file
  logic [99:0] mem [0:MAXN-1];
  logic [31:0] a, b, y;
  logic [3:0] flags;
  int errors = 0, n = 0;

  fp32_add dut (.a(a), .b(b), .y(y), .flags(flags));

  initial begin
    $readmemh("arith/fp32_add.hex", mem);
    for (int i = 0; i < MAXN; i++) begin
      if (^mem[i] === 1'bx) break;
      a = mem[i][99:68]; b = mem[i][67:36];
      #1;
      n++;
      if (y !== mem[i][35:4] || flags !== mem[i][3:0]) begin
        errors++;
        if (errors <= 10) $display("MISMATCH a=%h b=%h y=%h flags=%h  exp y=%h flags=%h", a, b, y, flags, mem[i][35:4], mem[i][3:0]);
      end
    end
    if (n != MAXN || MAXN < 1) begin errors++; $display("expected %0d vectors, read %0d", MAXN, n); end
    if (errors == 0) $display("PASS fp32_add (%0d vectors)", n);
    else $display("FAIL fp32_add: %0d errors of %0d", errors, n);
    $finish;
  end
endmodule
