// Self-checking testbench for fifo (iverilog -g2012).
// Several configurations run side by side against a queue reference model, with random valid/ready stalls,
// phases that fill and drain the FIFO back to back, and resets in the middle.
`timescale 1ns/1ps

module fifo_chk #(
  parameter int WIDTH = 8,
  parameter int DEPTH = 4,
  parameter bit FT    = 1'b0,
  parameter int ID    = 0
) (
  input  logic clk,
  input  logic phase_rst,              // asynchronous reset request from the top
  input  int   pin,                    // probability (percent) of in_valid per cycle
  input  int   pout,                   // probability (percent) of out_ready per cycle
  output int   errors,
  output int   n_push, n_pop, n_full, n_empty, n_bypass
);
  localparam int CW = $clog2(DEPTH + 1);
  logic             rst_n;
  logic             in_valid, in_ready, out_valid, out_ready;
  logic [WIDTH-1:0] in_data, out_data;
  logic [CW-1:0]    count;
  logic             full, empty;

  fifo #(.WIDTH(WIDTH), .DEPTH(DEPTH), .FALLTHROUGH(FT)) dut (
    .clk(clk), .rst_n(rst_n), .in_valid(in_valid), .in_ready(in_ready), .in_data(in_data),
    .out_valid(out_valid), .out_ready(out_ready), .out_data(out_data),
    .count(count), .full(full), .empty(empty));

  logic [WIDTH-1:0] q [$];
  logic [WIDTH-1:0] exp;
  logic             push, pop;
  logic [WIDTH-1:0] ref_next = 0;
  bit               rst_seen = 0;

  initial begin errors = 0; n_push = 0; n_pop = 0; n_full = 0; n_empty = 0; n_bypass = 0; rst_n = 0; in_valid = 0; out_ready = 0; in_data = 0; end

  // reset: asserted asynchronously (mid-cycle) when phase_rst rises, released at a clock edge three cycles later
  always @(posedge phase_rst) begin
    #(1.3);
    rst_n = 0;
    q.delete();
    repeat (3) @(posedge clk);
    #1 rst_n = 1;
  end

  // checks and model update at the clock edge, before the DUT's nonblocking updates take effect
  always @(posedge clk) begin
    if (rst_n === 1'b1) begin
      rst_seen = 1;
      if (count !== CW'(q.size())) begin errors++; $display("[%0d] count %0d != model %0d", ID, count, q.size()); end
      if (full !== (q.size() == DEPTH)) begin errors++; $display("[%0d] full wrong", ID); end
      if (empty !== (q.size() == 0)) begin errors++; $display("[%0d] empty wrong", ID); end
      if (in_ready !== (q.size() < DEPTH)) begin errors++; $display("[%0d] in_ready wrong", ID); end
      if (out_valid !== ((q.size() > 0) || (FT && in_valid))) begin errors++; $display("[%0d] out_valid wrong", ID); end
      push = in_valid && in_ready;
      pop  = out_valid && out_ready;
      if (full)  n_full++;
      if (empty) n_empty++;
      if (pop) begin
        if (q.size() > 0) exp = q[0]; else exp = in_data;       // bypass: only legal with FT (out_valid check above)
        if (q.size() == 0) n_bypass++;
        if (out_data !== exp) begin errors++; $display("[%0d] data %h != expected %h", ID, out_data, exp); end
        n_pop++;
      end
      if (push) begin q.push_back(in_data); n_push++; end
      if (pop)  void'(q.pop_front());
    end
    // drive the next cycle's inputs (valid stays up with the same data until accepted)
    if (rst_n === 1'b1) begin
      if (!in_valid || in_ready) begin
        if ($urandom_range(99) < pin) begin in_valid <= 1; in_data <= ref_next; ref_next <= ref_next + 1'b1; end
        else in_valid <= 0;
      end
      out_ready <= ($urandom_range(99) < pout);
    end else begin
      in_valid <= 0; out_ready <= 0;
    end
  end
endmodule

module tb_fifo;
  localparam int NCFG = 12;
  logic clk = 0;
  always #5 clk = ~clk;
  logic rst_req = 0;
  int pin = 50, pout = 50;
  int errs [NCFG], npush [NCFG], npop [NCFG], nfull [NCFG], nempty [NCFG], nbyp [NCFG];

  // {WIDTH, DEPTH, FT}
  fifo_chk #(8, 1, 0, 0)   c0  (clk, rst_req, pin, pout, errs[0],  npush[0],  npop[0],  nfull[0],  nempty[0],  nbyp[0]);
  fifo_chk #(8, 1, 1, 1)   c1  (clk, rst_req, pin, pout, errs[1],  npush[1],  npop[1],  nfull[1],  nempty[1],  nbyp[1]);
  fifo_chk #(8, 2, 0, 2)   c2  (clk, rst_req, pin, pout, errs[2],  npush[2],  npop[2],  nfull[2],  nempty[2],  nbyp[2]);
  fifo_chk #(8, 2, 1, 3)   c3  (clk, rst_req, pin, pout, errs[3],  npush[3],  npop[3],  nfull[3],  nempty[3],  nbyp[3]);
  fifo_chk #(16, 3, 0, 4)  c4  (clk, rst_req, pin, pout, errs[4],  npush[4],  npop[4],  nfull[4],  nempty[4],  nbyp[4]);
  fifo_chk #(16, 3, 1, 5)  c5  (clk, rst_req, pin, pout, errs[5],  npush[5],  npop[5],  nfull[5],  nempty[5],  nbyp[5]);
  fifo_chk #(32, 4, 0, 6)  c6  (clk, rst_req, pin, pout, errs[6],  npush[6],  npop[6],  nfull[6],  nempty[6],  nbyp[6]);
  fifo_chk #(32, 4, 1, 7)  c7  (clk, rst_req, pin, pout, errs[7],  npush[7],  npop[7],  nfull[7],  nempty[7],  nbyp[7]);
  fifo_chk #(8, 5, 0, 8)   c8  (clk, rst_req, pin, pout, errs[8],  npush[8],  npop[8],  nfull[8],  nempty[8],  nbyp[8]);
  fifo_chk #(8, 5, 1, 9)   c9  (clk, rst_req, pin, pout, errs[9],  npush[9],  npop[9],  nfull[9],  nempty[9],  nbyp[9]);
  fifo_chk #(64, 8, 0, 10) c10 (clk, rst_req, pin, pout, errs[10], npush[10], npop[10], nfull[10], nempty[10], nbyp[10]);
  fifo_chk #(1, 16, 1, 11) c11 (clk, rst_req, pin, pout, errs[11], npush[11], npop[11], nfull[11], nempty[11], nbyp[11]);

  int total;
  initial begin
    repeat (2) @(posedge clk);
    rst_req = 1; #20 rst_req = 0;
    repeat (8) @(posedge clk);
    // phases: {pin, pout}: balanced, fill, drain, both always, mostly empty, mostly full, stall-heavy
    for (int round = 0; round < 6; round++) begin
      for (int ph = 0; ph < 8; ph++) begin
        case (ph)
          0: begin pin = 50;  pout = 50;  end
          1: begin pin = 100; pout = 0;   end   // fill back to back
          2: begin pin = 0;   pout = 100; end   // drain back to back
          3: begin pin = 100; pout = 100; end
          4: begin pin = 15;  pout = 90;  end
          5: begin pin = 90;  pout = 15;  end
          6: begin pin = 100; pout = 50;  end
          7: begin pin = 30;  pout = 30;  end
        endcase
        repeat (150) @(posedge clk);
        if (ph == 5 && (round % 2 == 1)) begin           // reset in the middle of a phase, mostly full
          rst_req = 1; #20 rst_req = 0;
          repeat (12) @(posedge clk);
        end
      end
      rst_req = 1; #20 rst_req = 0;                       // reset at a phase boundary
      repeat (10) @(posedge clk);
    end
    total = 0;
    for (int i = 0; i < NCFG; i++) begin
      total += errs[i];
      if (npush[i] < 500 || npop[i] < 500 || nfull[i] == 0 || nempty[i] == 0) begin
        total++; $display("coverage hole in config %0d: push %0d pop %0d full %0d empty %0d", i, npush[i], npop[i], nfull[i], nempty[i]);
      end
      if (i % 2 == 1 && nbyp[i] == 0) begin total++; $display("no bypass seen in FT config %0d", i); end
    end
    if (total == 0) $display("PASS tb_fifo");
    else            $fatal(1, "FAIL tb_fifo: %0d errors", total);
    $finish;
  end
endmodule
