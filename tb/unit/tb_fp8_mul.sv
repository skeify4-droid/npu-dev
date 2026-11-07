// Self-checking testbench for fp8_mul (iverilog -g2012): all 65,536 E4M3 x E4M3 pairs, expected from ml_dtypes'
// decode of the codes (scripts/gen_arith_vectors.py). Run from build/ (vectors in build/arith/).
`timescale 1ns/1ps
`include "counts.svh"   // CNT_<file>: rows of each vector file (scripts/gen_arith_vectors.py)
module tb_fp8_mul;
  localparam int MAXN = `CNT_FP8_MUL;      // exact row count of the file
  logic [53:0] mem [0:MAXN-1];
  logic [7:0] a, b;
  logic neg, nan;
  logic [35:0] mag;
  int errors = 0, n = 0;

  fp8_mul dut (.a(a), .b(b), .neg(neg), .nan(nan), .mag(mag));

  initial begin
    $readmemh("arith/fp8_mul.hex", mem);
    for (int i = 0; i < MAXN; i++) begin
      if (^mem[i] === 1'bx) break;
      a = mem[i][53:46]; b = mem[i][45:38];
      #1;
      n++;
      if (neg !== mem[i][37] || nan !== mem[i][36] || (!mem[i][36] && mag !== mem[i][35:0])) begin
        errors++;
        if (errors <= 10) $display("MISMATCH a=%h b=%h neg=%b nan=%b mag=%h  exp neg=%b nan=%b mag=%h", a, b, neg, nan, mag, mem[i][37], mem[i][36], mem[i][35:0]);
      end
    end
    if (n != 65536 || n != MAXN || MAXN < 1) begin errors++; $display("expected 65536 vectors (count file: %0d), read %0d", MAXN, n); end
    if (errors == 0) $display("PASS fp8_mul (%0d pairs)", n);
    else $display("FAIL fp8_mul: %0d errors", errors);
    $finish;
  end
endmodule
