// Self-checking testbench for cvt_fp32_int (iverilog -g2012): INT8 / UINT8 / INT16 / INT32 / UINT32, bit-exact against
// sw/npumodel/arith_ref.py (vectors: integers +- 0.25 / 0.5 / 0.75 around zero and the range limits, one FP32 ulp
// either side, random magnitudes, NaN, Inf).
`timescale 1ns/1ps
module cvt_int_chk #(
  parameter int    BITS   = 8,
  parameter bit    SIGNED = 1'b1,
  parameter string FILE   = "",
  parameter int    MAXN   = 60000
) (
  output int errors, output int n
);
  logic [67:0] mem [0:MAXN-1];
  logic [31:0] x, y;
  logic [3:0]  flags;
  cvt_fp32_int #(.BITS(BITS), .SIGNED(SIGNED)) dut (.x(x), .y(y), .flags(flags));
  initial begin
    errors = 0; n = 0;
    $readmemh(FILE, mem);
    for (int i = 0; i < MAXN; i++) begin
      if (^mem[i] === 1'bx) break;
      x = mem[i][67:36];
      #1;
      n++;
      if (y !== mem[i][35:4] || flags !== mem[i][3:0]) begin
        errors++;
        if (errors <= 5) $display("MISMATCH BITS=%0d SIGNED=%0d x=%h y=%h flags=%h  exp y=%h flags=%h", BITS, SIGNED, x, y, flags, mem[i][35:4], mem[i][3:0]);
      end
    end
  end
endmodule

module tb_cvt_fp32_int;
  int e [5], n [5];
  cvt_int_chk #(.BITS(8),  .SIGNED(1), .FILE("arith/cvt_fp32_int8.hex"))   c0 (.errors(e[0]), .n(n[0]));
  cvt_int_chk #(.BITS(8),  .SIGNED(0), .FILE("arith/cvt_fp32_uint8.hex"))  c1 (.errors(e[1]), .n(n[1]));
  cvt_int_chk #(.BITS(16), .SIGNED(1), .FILE("arith/cvt_fp32_int16.hex"))  c2 (.errors(e[2]), .n(n[2]));
  cvt_int_chk #(.BITS(32), .SIGNED(1), .FILE("arith/cvt_fp32_int32.hex"))  c3 (.errors(e[3]), .n(n[3]));
  cvt_int_chk #(.BITS(32), .SIGNED(0), .FILE("arith/cvt_fp32_uint32.hex")) c4 (.errors(e[4]), .n(n[4]));
  initial begin
    #100000000;
    if (e[0] + e[1] + e[2] + e[3] + e[4] == 0 && n[0] > 1000 && n[4] > 1000)
      $display("PASS cvt_fp32_int (%0d vectors per type)", n[0]);
    else
      $display("FAIL cvt_fp32_int: errors %0d %0d %0d %0d %0d", e[0], e[1], e[2], e[3], e[4]);
    $finish;
  end
endmodule
