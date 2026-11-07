// Self-checking testbench for mxu_pe (iverilog -g2012): the four pipeline variants driven back to back with the rows of
// arith/mxu_pe.hex ({mode, a, w, psum_in, psum_out, flags}); one row per clock, no bubbles.
//   REG_DOT=1, REG_OUT=1: a/w of row i at cycle i, psum_in of row i at cycle i+1, result at cycle i+2 (latency 2).
//   REG_DOT=1, REG_OUT=0: a/w of row i at cycle i, psum_in of row i at cycle i+1, result in cycle i+1 (combinational add).
//   REG_DOT=0, REG_OUT=1: a/w and psum_in of row i at cycle i, result at cycle i+1.
//   REG_DOT=0, REG_OUT=0: combinational.
// Reset: the flag registers (the dot's flags of REG_DOT=1, the output flags of REG_OUT=1) clear asynchronously. The test
// holds inputs whose dot and add raise flags (INVALID, NAN_IN, OVERFLOW), checks that the flags read 0 while rst_n is low
// (reset applied at the start with flagging inputs, and in the middle of a run after the pipelines are full and flags
// are non-zero), and that they stay 0 in the first cycles after release with benign inputs (stale dot flags would
// otherwise reach the output flags).
`timescale 1ns/1ps
`include "arith/counts.svh"   // CNT_<file>: rows of each vector file (scripts/gen_arith_vectors.py)
module tb_mxu_pe;
  localparam int MAXN = `CNT_MXU_PE;      // exact row count of the file
  logic [132:0] mem [0:MAXN-1];
  logic clk = 0, rst_n = 0;
  logic mode;
  logic [31:0] a, w, ps_d1, ps_d0, y11, y10, y01, y00;   // ps_d1: psum_in for REG_DOT=1 (one cycle later), ps_d0: aligned
  logic [3:0]  f11, f10, f01, f00;
  int errors = 0, n = 0, nrows = 0;

  mxu_pe #(.REG_DOT(1'b1), .REG_OUT(1'b1)) p11 (.clk(clk), .rst_n(rst_n), .mode_bf16(mode), .a(a), .w(w), .psum_in(ps_d1), .psum_out(y11), .flags(f11));
  mxu_pe #(.REG_DOT(1'b1), .REG_OUT(1'b0)) p10 (.clk(clk), .rst_n(rst_n), .mode_bf16(mode), .a(a), .w(w), .psum_in(ps_d1), .psum_out(y10), .flags(f10));
  mxu_pe #(.REG_DOT(1'b0), .REG_OUT(1'b1)) p01 (.clk(clk), .rst_n(rst_n), .mode_bf16(mode), .a(a), .w(w), .psum_in(ps_d0), .psum_out(y01), .flags(f01));
  mxu_pe #(.REG_DOT(1'b0), .REG_OUT(1'b0)) p00 (.clk(clk), .rst_n(rst_n), .mode_bf16(mode), .a(a), .w(w), .psum_in(ps_d0), .psum_out(y00), .flags(f00));

  always #5 clk = ~clk;

  task automatic chk(string nm, int i, logic [31:0] y, logic [3:0] f);
    if (y !== mem[i][35:4] || f !== mem[i][3:0]) begin
      errors++;
      if (errors <= 10) $display("MISMATCH %s row %0d y=%h f=%h exp y=%h f=%h", nm, i, y, f, mem[i][35:4], mem[i][3:0]);
    end
  endtask

  // flags of the three variants that have flag registers (p11, p10, p01) must read 0; p00 is combinational and has no reset.
  // skip10: p10's flags are the combinational add flags of the unreset data register d_q, so they can be non-zero in reset.
  task automatic flags_zero(string when_, bit skip10);
    if (f11 !== 4'd0 || f01 !== 4'd0 || (!skip10 && f10 !== 4'd0)) begin
      errors++;
      $display("RESET: flags not 0 %s: f11=%h f10=%h f01=%h", when_, f11, f10, f01);
    end
  endtask

  // Drive constant flagging inputs until the pipelines are full, check the flags are non-zero (the case is effective),
  // assert rst_n asynchronously in the middle of a cycle, check the flags read 0 at once and while rst_n stays low, release
  // with benign inputs and check the flags stay 0.
  task automatic reset_case(string nm, logic m, logic [31:0] aa, logic [31:0] ww, logic [31:0] pp, bit skip10);
    @(negedge clk);
    rst_n = 1; mode = m; a = aa; w = ww; ps_d0 = pp; ps_d1 = pp;
    repeat (4) @(negedge clk);
    if (f11 === 4'd0 || f01 === 4'd0) begin
      errors++; $display("RESET %s: case raises no flags (f11=%h f01=%h): the test is vacuous", nm, f11, f01);
    end
    #2 rst_n = 0;                                          // asynchronous: not on a clock edge
    #1 flags_zero({nm, ", right after rst_n falls"}, skip10);
    repeat (2) @(negedge clk);
    flags_zero({nm, ", rst_n held low"}, skip10);
    @(negedge clk);
    rst_n = 1; mode = 0; a = 0; w = 0; ps_d0 = 0; ps_d1 = 0;     // release together with benign inputs
    @(negedge clk);
    flags_zero({nm, ", first cycle after release"}, 0);
    @(negedge clk);
    flags_zero({nm, ", second cycle after release"}, 0);
  endtask

  initial begin
    $readmemh("arith/mxu_pe.hex", mem);
    for (int i = 0; i < MAXN; i++) begin if (^mem[i] === 1'bx) break; nrows++; end
    // reset at the start with inputs that raise flags: BF16 Inf * 0 (INVALID)
    mode = 1; a = 32'h3F80_7F80; w = 32'h3F80_0000; ps_d0 = 0; ps_d1 = 0;
    repeat (3) @(posedge clk);
    #1 flags_zero("at the start of reset", 0);
    @(negedge clk);
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
    // REG_DOT=1: psum one cycle after a/w; mode is held with a/w for the dot stage only. p11 registers the result (visible
    // one cycle after psum), p10 does not (visible in the cycle of psum).
    for (int i = 0; i <= nrows + 1; i++) begin
      @(negedge clk);
      if (i > 1) chk("p11", i - 2, y11, f11);
      if (i < nrows) begin mode = mem[i][132]; a = mem[i][131:100]; w = mem[i][99:68]; end
      if (i >= 1 && i - 1 < nrows) ps_d1 = mem[i-1][67:36];
      if (i >= 1 && i - 1 < nrows) begin #1 chk("p10", i - 1, y10, f10); end
      n++;
    end
    // reset in the middle of a run: INVALID (Inf * 0), NAN_IN (a NaN operand), OVERFLOW (the add overflows: max + max)
    reset_case("INVALID",  1'b1, 32'h3F80_7F80, 32'h3F80_0000, 32'h0000_0000, 1'b0);
    reset_case("NAN_IN",   1'b1, 32'h3F80_7FC0, 32'h3F80_3F80, 32'h0000_0000, 1'b0);
    reset_case("NAN_IN8",  1'b0, 32'h0000_007F, 32'h0000_0038, 32'h0000_0000, 1'b0);
    reset_case("OVERFLOW", 1'b1, 32'h0000_7F7F, 32'h0000_3F80, 32'h7F7F_FFFF, 1'b1);
    if (nrows != MAXN) begin errors++; $display("expected %0d rows, read %0d", MAXN, nrows); end
    if (errors == 0) $display("PASS mxu_pe (%0d rows, four pipeline variants, reset tests)", nrows);
    else $display("FAIL mxu_pe: %0d errors of %0d rows", errors, nrows);
    $finish;
  end
endmodule
