// Self-checking testbench for fp8_dot4 (iverilog -g2012), bit-exact against the exact reference of
// sw/npumodel/arith_ref.py (vectors from scripts/gen_arith_vectors.py: random, corner and cancellation cases).
`timescale 1ns/1ps
module tb_fp8_dot4;
  localparam int MAXN = 250000;
  logic [99:0] mem [0:MAXN-1];
  logic [31:0] a, w, y;
  logic [3:0] flags;
  int errors = 0, n = 0;

  fp8_dot4 dut (.a(a), .w(w), .y(y), .flags(flags));

  initial begin
    $readmemh("arith/fp8_dot4.hex", mem);
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
    if (n < 1000) begin errors++; $display("too few vectors: %0d", n); end
    if (errors == 0) $display("PASS fp8_dot4 (%0d vectors)", n);
    else $display("FAIL fp8_dot4: %0d errors of %0d", errors, n);
    $finish;
  end
endmodule
