// Self-checking testbench for mx_dequant_bf16 (iverilog -g2012): MXFP4 (E2M1 x E8M0), MXFP8 (E4M3 x E8M0) and NVFP4
// (E2M1 x E4M3 scale), exhaustive over every (element, scale) pair, bit-exact against sw/npumodel/arith_ref.py
// (checked against numerics.mx_dequant and ml_dtypes by test_arith.py).
`timescale 1ns/1ps
`include "counts.svh"   // CNT_<file>: rows of each vector file (scripts/gen_arith_vectors.py)
module mx_chk #(
  parameter int    ELEM  = 0,
  parameter int    SCALE = 0,
  parameter string FILE  = "",
  parameter int    MAXN  = 1              // exact row count of FILE
) (
  output int errors, output int n
);
  logic [35:0] mem [0:MAXN-1];
  logic [7:0]  el, sc;
  logic [15:0] y;
  logic [3:0]  flags;
  mx_dequant_bf16 #(.ELEM(ELEM), .SCALE(SCALE)) dut (.el(el), .sc(sc), .y(y), .flags(flags));
  initial begin
    errors = 0; n = 0;
    $readmemh(FILE, mem);
    for (int i = 0; i < MAXN; i++) begin
      if (^mem[i] === 1'bx) break;
      el = mem[i][35:28]; sc = mem[i][27:20];
      #1;
      n++;
      if (y !== mem[i][19:4] || flags !== mem[i][3:0]) begin
        errors++;
        if (errors <= 5) $display("MISMATCH ELEM=%0d SCALE=%0d el=%h sc=%h y=%h flags=%h  exp y=%h flags=%h", ELEM, SCALE, el, sc, y, flags, mem[i][19:4], mem[i][3:0]);
      end
    end
    if (n != MAXN || MAXN < 1) begin errors++; $display("ELEM=%0d SCALE=%0d: expected %0d vectors, read %0d", ELEM, SCALE, MAXN, n); end
  end
endmodule

module tb_mx_dequant_bf16;
  int e [3], n [3];
  mx_chk #(.ELEM(0), .SCALE(0), .FILE("arith/mx_dequant_mxfp4.hex"), .MAXN(`CNT_MX_DEQUANT_MXFP4)) c0 (.errors(e[0]), .n(n[0]));
  mx_chk #(.ELEM(1), .SCALE(0), .FILE("arith/mx_dequant_mxfp8.hex"), .MAXN(`CNT_MX_DEQUANT_MXFP8)) c1 (.errors(e[1]), .n(n[1]));
  mx_chk #(.ELEM(0), .SCALE(1), .FILE("arith/mx_dequant_nvfp4.hex"), .MAXN(`CNT_MX_DEQUANT_NVFP4)) c2 (.errors(e[2]), .n(n[2]));
  initial begin
    #100000000;
    if (e[0] + e[1] + e[2] == 0 && n[0] == 4096 && n[1] == 65536 && n[2] == 4096)
      $display("PASS mx_dequant_bf16 (MXFP4 4096, MXFP8 65536, NVFP4 4096 pairs)");
    else
      $display("FAIL mx_dequant_bf16: errors %0d %0d %0d, counts %0d %0d %0d", e[0], e[1], e[2], n[0], n[1], n[2]);
    $finish;
  end
endmodule
