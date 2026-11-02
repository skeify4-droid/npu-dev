// Self-checking testbench for credit_tx (iverilog -g2012): per-VC counter against a plain integer reference.
// Legal traffic (send only with a credit, credits return only for flits in flight, random return delay) plus
// an abuse phase (sends without a credit, spurious credits) that must saturate at 0 and DEPTH and raise the flags.
`timescale 1ns/1ps

module credit_tx_chk #(
  parameter int NVC   = 5,
  parameter int DEPTH = 4,
  parameter int PKT   = 2,
  parameter int ID    = 0
) (
  input  logic clk,
  input  logic rst_req,
  input  int   psend,         // percent chance per VC per cycle of a send
  input  int   pret,          // percent chance per cycle per VC of returning a credit for an outstanding flit
  input  logic abuse,         // ignore legality
  output int   errors, n_send, n_stall, n_under, n_over
);
  localparam int CW = $clog2(DEPTH + 1);
  logic rst_n = 0;
  logic [NVC-1:0] send = 0, credit_in = 0, can_send, can_send_pkt;
  logic [NVC*CW-1:0] credits;
  logic err_underflow, err_overflow;

  credit_tx #(.NVC(NVC), .DEPTH(DEPTH), .PKT(PKT)) dut (.*);

  int cnt [NVC];
  int dec, inc;
  logic eu, eo;

  initial begin errors = 0; n_send = 0; n_stall = 0; n_under = 0; n_over = 0; for (int v = 0; v < NVC; v++) cnt[v] = DEPTH; end

  always @(posedge rst_req) begin
    #(1.7);
    rst_n = 0;
    for (int v = 0; v < NVC; v++) cnt[v] = DEPTH;
    repeat (2) @(posedge clk);
    #1 rst_n = 1;
  end

  always @(posedge clk) begin
    if (rst_n === 1'b1) begin
      eu = 0; eo = 0;
      for (int v = 0; v < NVC; v++) begin
        if (credits[v*CW +: CW] !== CW'(cnt[v])) begin errors++; $display("[%0d] vc%0d credits %0d != model %0d", ID, v, credits[v*CW +: CW], cnt[v]); end
        if (can_send[v] !== (cnt[v] > 0)) begin errors++; $display("[%0d] vc%0d can_send wrong", ID, v); end
        if (can_send_pkt[v] !== (cnt[v] >= PKT)) begin errors++; $display("[%0d] vc%0d can_send_pkt wrong", ID, v); end
        if (cnt[v] == 0) n_stall++;
        dec = (send[v] && cnt[v] > 0) ? 1 : 0;
        inc = (credit_in[v] && cnt[v] < DEPTH) ? 1 : 0;
        if (send[v] && cnt[v] == 0) eu = 1;
        if (credit_in[v] && cnt[v] == DEPTH) eo = 1;
        if (send[v] && cnt[v] > 0) n_send++;
        cnt[v] = cnt[v] - dec + inc;
      end
      if (err_underflow !== eu) begin errors++; $display("[%0d] err_underflow %b != %b", ID, err_underflow, eu); end
      if (err_overflow  !== eo) begin errors++; $display("[%0d] err_overflow %b != %b", ID, err_overflow, eo); end
      if (eu) n_under++;
      if (eo) n_over++;
      // next cycle's inputs
      for (int v = 0; v < NVC; v++) begin
        if (abuse) begin
          send[v] <= ($urandom_range(99) < psend);
          credit_in[v] <= ($urandom_range(99) < pret);
        end else begin
          send[v] <= (cnt[v] > 0) && ($urandom_range(99) < psend);            // legal: only with a credit
          credit_in[v] <= (cnt[v] < DEPTH) && ($urandom_range(99) < pret);    // legal: only for a flit in flight
        end
      end
    end else begin
      send <= 0; credit_in <= 0;
    end
  end
endmodule

module tb_credit_tx;
  localparam int N = 5;
  logic clk = 0;
  always #5 clk = ~clk;
  logic rst_req = 0, abuse = 0;
  int psend = 50, pret = 50;
  int errs [N], ns [N], nst [N], nu [N], no [N], nsp [N];

  credit_tx_chk #(5, 4, 2, 0) c0 (clk, rst_req, psend, pret, abuse, errs[0], ns[0], nst[0], nu[0], no[0]);   // the router's shape
  credit_tx_chk #(5, 3, 2, 1) c1 (clk, rst_req, psend, pret, abuse, errs[1], ns[1], nst[1], nu[1], no[1]);
  credit_tx_chk #(1, 1, 1, 2) c2 (clk, rst_req, psend, pret, abuse, errs[2], ns[2], nst[2], nu[2], no[2]);
  credit_tx_chk #(3, 8, 2, 3) c3 (clk, rst_req, psend, pret, abuse, errs[3], ns[3], nst[3], nu[3], no[3]);
  credit_tx_chk #(2, 2, 2, 4) c4 (clk, rst_req, psend, pret, abuse, errs[4], ns[4], nst[4], nu[4], no[4]);

  int total;
  initial begin
    repeat (2) @(posedge clk);
    rst_req = 1; #5 rst_req = 0;
    repeat (6) @(posedge clk);
    for (int round = 0; round < 4; round++) begin
      abuse = 0;
      for (int ph = 0; ph < 5; ph++) begin
        case (ph)
          0: begin psend = 50;  pret = 50;  end
          1: begin psend = 100; pret = 0;   end      // run out of credits
          2: begin psend = 0;   pret = 100; end      // all credits back
          3: begin psend = 100; pret = 100; end
          4: begin psend = 30;  pret = 10;  end
        endcase
        repeat (120) @(posedge clk);
      end
      abuse = 1; psend = 70; pret = 70;               // protocol violations: saturate, flag
      repeat (150) @(posedge clk);
      abuse = 1; psend = 100; pret = 0;  repeat (30) @(posedge clk);
      abuse = 1; psend = 0; pret = 100;  repeat (30) @(posedge clk);
      abuse = 0;
      rst_req = 1; #5 rst_req = 0;                    // reset in the middle of traffic
      repeat (10) @(posedge clk);
    end
    total = 0;
    for (int i = 0; i < N; i++) begin
      total += errs[i];
      if (ns[i] < 500 || nst[i] == 0 || nu[i] == 0 || no[i] == 0) begin
        total++; $display("coverage hole in config %0d: sends %0d stalls %0d under %0d over %0d", i, ns[i], nst[i], nu[i], no[i]);
      end
    end
    if (total == 0) $display("PASS tb_credit_tx");
    else            $fatal(1, "FAIL tb_credit_tx: %0d errors", total);
    $finish;
  end
endmodule
