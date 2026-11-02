// Self-checking testbench for credit_rx (iverilog -g2012): one credit per freed flit, one cycle later, none lost,
// none invented, also across a reset. Runs the registered (REG=1) and pass-through (REG=0) variants.
`timescale 1ns/1ps

module credit_rx_chk #(
  parameter int NVC = 5,
  parameter bit REG = 1'b1,
  parameter int ID  = 0
) (
  input  logic clk,
  input  logic rst_req,
  output int   errors, n_free
);
  logic rst_n = 0;
  logic [NVC-1:0] free = 0, credit_out;
  credit_rx #(.NVC(NVC), .REG(REG)) dut (.*);

  int sent [NVC];          // total frees so far, per VC
  int got  [NVC];          // total credits seen so far, per VC
  logic [NVC-1:0] prev_free = 0;

  initial begin errors = 0; n_free = 0; for (int v = 0; v < NVC; v++) begin sent[v] = 0; got[v] = 0; end end

  always @(posedge rst_req) begin
    #(1.7);
    rst_n = 0;
    for (int v = 0; v < NVC; v++) begin sent[v] = 0; got[v] = 0; end
    prev_free = 0;
    repeat (2) @(posedge clk);
    #1 rst_n = 1;
  end

  always @(posedge clk) begin
    if (rst_n === 1'b1) begin
      for (int v = 0; v < NVC; v++) begin
        // expected output this cycle: the free of the previous cycle (REG) or of this cycle (pass-through)
        if (credit_out[v] !== (REG ? prev_free[v] : free[v])) begin
          errors++; $display("[%0d] vc%0d credit_out %b != expected %b", ID, v, credit_out[v], REG ? prev_free[v] : free[v]);
        end
        if (free[v]) begin sent[v]++; n_free++; end
        if (credit_out[v]) got[v]++;
        if (got[v] > sent[v]) begin errors++; $display("[%0d] vc%0d credit without a free", ID, v); end
        if (sent[v] - got[v] > 1) begin errors++; $display("[%0d] vc%0d credit lost", ID, v); end
      end
      prev_free = free;
      free <= NVC'($urandom) & NVC'($urandom);             // about 1/4 per VC, with bursts of consecutive frees
      if ($urandom_range(9) == 0) free <= '1;              // all VCs at once
    end else begin
      free <= 0;
    end
  end
endmodule

module tb_credit_rx;
  localparam int N = 4;
  logic clk = 0;
  always #5 clk = ~clk;
  logic rst_req = 0;
  int errs [N], nf [N];
  credit_rx_chk #(5, 1, 0) c0 (clk, rst_req, errs[0], nf[0]);
  credit_rx_chk #(5, 0, 1) c1 (clk, rst_req, errs[1], nf[1]);
  credit_rx_chk #(1, 1, 2) c2 (clk, rst_req, errs[2], nf[2]);
  credit_rx_chk #(3, 0, 3) c3 (clk, rst_req, errs[3], nf[3]);
  int total;
  initial begin
    repeat (2) @(posedge clk);
    rst_req = 1; #5 rst_req = 0;
    repeat (6) @(posedge clk);
    for (int r = 0; r < 4; r++) begin
      repeat (400) @(posedge clk);
      rst_req = 1; #5 rst_req = 0;
      repeat (8) @(posedge clk);
    end
    total = 0;
    for (int i = 0; i < N; i++) begin total += errs[i]; if (nf[i] < 300) begin total++; $display("too few frees in config %0d", i); end end
    if (total == 0) $display("PASS tb_credit_rx");
    else            $fatal(1, "FAIL tb_credit_rx: %0d errors", total);
    $finish;
  end
endmodule
