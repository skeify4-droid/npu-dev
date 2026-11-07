// Self-checking testbench for mxu_pe_dot (iverilog -g2012): the mode mux over fp8_dot4 / bf16_dot2, bit-exact against
// sw/npumodel/arith_ref.py (vectors: scripts/gen_arith_vectors.py, FP8 rows then BF16 rows).
`timescale 1ns/1ps
`include "arith/counts.svh"   // CNT_<file>: rows of each vector file (scripts/gen_arith_vectors.py)
module tb_mxu_pe_dot;
  localparam int MAXN = `CNT_MXU_PE_DOT;      // exact row count of the file
  logic [100:0] mem [0:MAXN-1];
  logic mode;
  logic [31:0] a, w, d;
  logic [3:0] flags;
  int errors = 0, n = 0, n8 = 0, n16 = 0;

  mxu_pe_dot dut (.mode_bf16(mode), .a(a), .w(w), .d(d), .flags(flags));

  initial begin
    $readmemh("arith/mxu_pe_dot.hex", mem);
    for (int i = 0; i < MAXN; i++) begin
      if (^mem[i] === 1'bx) break;
      mode = mem[i][100]; a = mem[i][99:68]; w = mem[i][67:36];
      #1;
      n++;
      if (mode) n16++; else n8++;
      if (d !== mem[i][35:4] || flags !== mem[i][3:0]) begin
        errors++;
        if (errors <= 10) $display("MISMATCH mode=%b a=%h w=%h d=%h flags=%h  exp d=%h flags=%h", mode, a, w, d, flags, mem[i][35:4], mem[i][3:0]);
      end
    end
    if (n != MAXN || n8 < 1000 || n16 < 1000) begin errors++; $display("expected %0d vectors, read %0d (fp8 %0d, bf16 %0d)", MAXN, n, n8, n16); end
    if (errors == 0) $display("PASS mxu_pe_dot (%0d fp8 + %0d bf16 vectors)", n8, n16);
    else $display("FAIL mxu_pe_dot: %0d errors of %0d", errors, n);
    $finish;
  end
endmodule
