// Self-checking testbench for mxu_pe (iverilog -g2012): three pipeline variants driven back to back with the rows of
// arith/mxu_pe.hex ({mode, a, w, psum_in, psum_out, flags}); one row per clock, no bubbles.
//   REG_DOT=1, REG_OUT=1: a/w of row i at cycle i, psum_in of row i at cycle i+1, result at cycle i+2 (latency 2).
//   REG_DOT=0, REG_OUT=1: a/w and psum_in of row i at cycle i, result at cycle i+1.
//   REG_DOT=0, REG_OUT=0: combinational.
// Also checks the registered flags clear on reset.
`timescale 1ns/1ps
module tb_mxu_pe;
  localparam int MAXN = 20000;
  logic [132:0] mem [0:MAXN-1];
  logic clk = 0, rst_n = 0;
  logic mode;
  logic [31:0] a, w, ps_d1, ps_d0, y11, y01, y00;   // ps_d1: psum_in for REG_DOT=1 (one cycle later), ps_d0: aligned
  logic [3:0]  f11, f01, f00;
  int errors = 0, n = 0, nrows = 0;

  mxu_pe #(.REG_DOT(1'b1), .REG_OUT(1'b1)) p11 (.clk(clk), .rst_n(rst_n), .mode_bf16(mode), .a(a), .w(w), .psum_in(ps_d1), .psum_out(y11), .flags(f11));
  mxu_pe #(.REG_DOT(1'b0), .REG_OUT(1'b1)) p01 (.clk(clk), .rst_n(rst_n), .mode_bf16(mode), .a(a), .w(w), .psum_in(ps_d0), .psum_out(y01), .flags(f01));
  mxu_pe #(.REG_DOT(1'b0), .REG_OUT(1'b0)) p00 (.clk(clk), .rst_n(rst_n), .mode_bf16(mode), .a(a), .w(w), .psum_in(ps_d0), .psum_out(y00), .flags(f00));

  always #5 clk = ~clk;

  task automatic chk(string nm, int i, logic [31:0] y, logic [3:0] f);
    if (y !== mem[i][35:4] || f !== mem[i][3:0]) begin
      errors++;
      if (errors <= 10) $display("MISMATCH %s row %0d y=%h f=%h exp y=%h f=%h", nm, i, y, f, mem[i][35:4], mem[i][3:0]);
    end
  endtask

  initial begin
    $readmemh("arith/mxu_pe.hex", mem);
    for (int i = 0; i < MAXN; i++) begin if (^mem[i] === 1'bx) break; nrows++; end
    mode = 0; a = 0; w = 0; ps_d0 = 0; ps_d1 = 0;
    repeat (3) @(posedge clk);
    // flags registers are cleared by reset
    #1 if (f11 !== 4'd0 || f01 !== 4'd0) begin errors++; $display("flags not reset"); end
    rst_n = 1;
    // combinational variant: row by row
    for (int i = 0; i < nrows; i++) begin
      mode = mem[i][132]; a = mem[i][131:100]; w = mem[i][99:68]; ps_d0 = mem[i][67:36];
      #1 chk("p00", i, y00, f00);
    end
    // REG_DOT=0, REG_OUT=1: result visible after the next clock edge
    for (int i = 0; i <= nrows; i++) begin
      @(negedge clk);
      if (i > 0) chk("p01", i - 1, y01, f01);
      if (i < nrows) begin mode = mem[i][132]; a = mem[i][131:100]; w = mem[i][99:68]; ps_d0 = mem[i][67:36]; end
    end
    // REG_DOT=1, REG_OUT=1: psum one cycle after a/w; mode is held with a/w for the dot stage only
    for (int i = 0; i <= nrows + 1; i++) begin
      @(negedge clk);
      if (i > 1) chk("p11", i - 2, y11, f11);
      if (i < nrows) begin mode = mem[i][132]; a = mem[i][131:100]; w = mem[i][99:68]; end
      if (i >= 1 && i - 1 < nrows) ps_d1 = mem[i-1][67:36];
      n++;
    end
    if (nrows < 1000) begin errors++; $display("too few vectors: %0d", nrows); end
    if (errors == 0) $display("PASS mxu_pe (%0d rows, three pipeline variants)", nrows);
    else $display("FAIL mxu_pe: %0d errors of %0d rows", errors, nrows);
    $finish;
  end
endmodule
