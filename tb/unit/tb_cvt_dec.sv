// Self-checking testbench for cvt_dec (iverilog -g2012): every code of BF16, FP16, E4M3, E5M2 and E2M1 decoded to FP32 and
// to BF16, bit-exact against sw/npumodel/arith_ref.py (itself checked against ml_dtypes by test_arith.py).
`timescale 1ns/1ps
`include "arith/counts.svh"   // CNT_<file>: rows of each vector file (scripts/gen_arith_vectors.py)
module cvt_dec_chk #(
  parameter int    FMT     = 2,
  parameter bit    TO_BF16 = 1'b0,
  parameter string FILE    = "",
  parameter int    MAXN    = 1            // exact row count of FILE
) (
  output int errors, output int n
);
  logic [51:0] mem [0:MAXN-1];
  logic [15:0] x;
  logic [31:0] y;
  logic [3:0]  flags;
  cvt_dec #(.FMT(FMT), .TO_BF16(TO_BF16)) dut (.x(x), .y(y), .flags(flags));
  initial begin
    errors = 0; n = 0;
    $readmemh(FILE, mem);
    for (int i = 0; i < MAXN; i++) begin
      if (^mem[i] === 1'bx) break;
      x = mem[i][51:36];
      #1;
      n++;
      if (y !== mem[i][35:4] || flags !== mem[i][3:0]) begin
        errors++;
        if (errors <= 5) $display("MISMATCH FMT=%0d BF16=%0d x=%h y=%h flags=%h  exp y=%h flags=%h", FMT, TO_BF16, x, y, flags, mem[i][35:4], mem[i][3:0]);
      end
    end
    if (n != MAXN) begin errors++; $display("FMT=%0d BF16=%0d: expected %0d vectors, read %0d", FMT, TO_BF16, MAXN, n); end
  end
endmodule

module tb_cvt_dec;
  int e [10], n [10];
  cvt_dec_chk #(.FMT(0), .TO_BF16(0), .FILE("arith/cvt_dec_bf16_fp32.hex"), .MAXN(`CNT_CVT_DEC_BF16_FP32)) c0 (.errors(e[0]), .n(n[0]));
  cvt_dec_chk #(.FMT(0), .TO_BF16(1), .FILE("arith/cvt_dec_bf16_bf16.hex"), .MAXN(`CNT_CVT_DEC_BF16_BF16)) c1 (.errors(e[1]), .n(n[1]));
  cvt_dec_chk #(.FMT(1), .TO_BF16(0), .FILE("arith/cvt_dec_fp16_fp32.hex"), .MAXN(`CNT_CVT_DEC_FP16_FP32)) c2 (.errors(e[2]), .n(n[2]));
  cvt_dec_chk #(.FMT(1), .TO_BF16(1), .FILE("arith/cvt_dec_fp16_bf16.hex"), .MAXN(`CNT_CVT_DEC_FP16_BF16)) c3 (.errors(e[3]), .n(n[3]));
  cvt_dec_chk #(.FMT(2), .TO_BF16(0), .FILE("arith/cvt_dec_e4m3_fp32.hex"), .MAXN(`CNT_CVT_DEC_E4M3_FP32)) c4 (.errors(e[4]), .n(n[4]));
  cvt_dec_chk #(.FMT(2), .TO_BF16(1), .FILE("arith/cvt_dec_e4m3_bf16.hex"), .MAXN(`CNT_CVT_DEC_E4M3_BF16)) c5 (.errors(e[5]), .n(n[5]));
  cvt_dec_chk #(.FMT(3), .TO_BF16(0), .FILE("arith/cvt_dec_e5m2_fp32.hex"), .MAXN(`CNT_CVT_DEC_E5M2_FP32)) c6 (.errors(e[6]), .n(n[6]));
  cvt_dec_chk #(.FMT(3), .TO_BF16(1), .FILE("arith/cvt_dec_e5m2_bf16.hex"), .MAXN(`CNT_CVT_DEC_E5M2_BF16)) c7 (.errors(e[7]), .n(n[7]));
  cvt_dec_chk #(.FMT(4), .TO_BF16(0), .FILE("arith/cvt_dec_e2m1_fp32.hex"), .MAXN(`CNT_CVT_DEC_E2M1_FP32)) c8 (.errors(e[8]), .n(n[8]));
  cvt_dec_chk #(.FMT(4), .TO_BF16(1), .FILE("arith/cvt_dec_e2m1_bf16.hex"), .MAXN(`CNT_CVT_DEC_E2M1_BF16)) c9 (.errors(e[9]), .n(n[9]));
  int tot;
  initial begin
    #100000000;
    tot = 0;
    for (int i = 0; i < 10; i++) tot += e[i];
    if (tot == 0 && n[0] == 65536 && n[1] == 65536 && n[2] == 65536 && n[3] == 65536 && n[4] == 256 && n[5] == 256 &&
        n[6] == 256 && n[7] == 256 && n[8] == 16 && n[9] == 16)
      $display("PASS cvt_dec (every code of 5 formats to FP32 and BF16)");
    else
      $display("FAIL cvt_dec: %0d errors, counts %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d", tot, n[0], n[1], n[2], n[3], n[4], n[5], n[6], n[7], n[8], n[9]);
    $finish;
  end
endmodule
