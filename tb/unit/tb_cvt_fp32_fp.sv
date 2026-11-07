// Self-checking testbench for cvt_fp32_fp (iverilog -g2012): FP32 -> BF16 / FP16 / E4M3 / E5M2 / E2M1, bit-exact
// against sw/npumodel/arith_ref.py (vectors: random FP32, every rounding midpoint of the target +- 1 FP32 ulp,
// max + 1/2 ulp, subnormal / normal boundaries, NaN and Inf; test_arith.py checks the reference against ml_dtypes).
`timescale 1ns/1ps
`include "counts.svh"   // CNT_<file>: rows of each vector file (scripts/gen_arith_vectors.py)
module cvt_fp_chk #(
  parameter int    FMT  = 0,
  parameter string FILE = "",
  parameter int    MAXN = 1               // exact row count of FILE
) (
  output int errors, output int n
);
  logic [51:0] mem [0:MAXN-1];
  logic [31:0] x;
  logic [15:0] y;
  logic [3:0]  flags;
  cvt_fp32_fp #(.FMT(FMT)) dut (.x(x), .y(y), .flags(flags));
  initial begin
    errors = 0; n = 0;
    $readmemh(FILE, mem);
    for (int i = 0; i < MAXN; i++) begin
      if (^mem[i] === 1'bx) break;
      x = mem[i][51:20];
      #1;
      n++;
      if (y !== mem[i][19:4] || flags !== mem[i][3:0]) begin
        errors++;
        if (errors <= 5) $display("MISMATCH FMT=%0d x=%h y=%h flags=%h  exp y=%h flags=%h", FMT, x, y, flags, mem[i][19:4], mem[i][3:0]);
      end
    end
    if (n != MAXN || MAXN < 1) begin errors++; $display("FMT=%0d: expected %0d vectors, read %0d", FMT, MAXN, n); end
  end
endmodule

module tb_cvt_fp32_fp;
  int e [5], n [5];
  cvt_fp_chk #(.FMT(0), .FILE("arith/cvt_fp32_bf16.hex"), .MAXN(`CNT_CVT_FP32_BF16)) c0 (.errors(e[0]), .n(n[0]));
  cvt_fp_chk #(.FMT(1), .FILE("arith/cvt_fp32_fp16.hex"), .MAXN(`CNT_CVT_FP32_FP16)) c1 (.errors(e[1]), .n(n[1]));
  cvt_fp_chk #(.FMT(2), .FILE("arith/cvt_fp32_e4m3.hex"), .MAXN(`CNT_CVT_FP32_E4M3)) c2 (.errors(e[2]), .n(n[2]));
  cvt_fp_chk #(.FMT(3), .FILE("arith/cvt_fp32_e5m2.hex"), .MAXN(`CNT_CVT_FP32_E5M2)) c3 (.errors(e[3]), .n(n[3]));
  cvt_fp_chk #(.FMT(4), .FILE("arith/cvt_fp32_e2m1.hex"), .MAXN(`CNT_CVT_FP32_E2M1)) c4 (.errors(e[4]), .n(n[4]));
  initial begin
    #3000000000;                                            // all checkers finish at time = their row count
    if (e[0] + e[1] + e[2] + e[3] + e[4] == 0)
      $display("PASS cvt_fp32_fp (BF16 %0d, FP16 %0d, E4M3 %0d, E5M2 %0d, E2M1 %0d vectors)", n[0], n[1], n[2], n[3], n[4]);
    else
      $display("FAIL cvt_fp32_fp: errors %0d %0d %0d %0d %0d", e[0], e[1], e[2], e[3], e[4]);
    $finish;
  end
endmodule
