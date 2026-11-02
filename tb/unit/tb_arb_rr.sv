// Self-checking testbench for arb_rr (iverilog -g2012): reference model with an explicit pointer, grant properties
// (one-hot or zero, only to a requester, a grant whenever anything requests), fairness bound, hold/lock with packets
// of 1-3 flits and bubbles, stalled accepts, reset in the middle.
`timescale 1ns/1ps

module arb_rr_chk #(
  parameter int N  = 4,
  parameter int ID = 0
) (
  input  logic clk,
  input  logic rst_req,
  input  int   preq,          // percent chance per cycle that an idle requester starts requesting
  input  int   pacc,          // percent chance of accept
  input  logic use_hold,      // packets of 1-3 flits with hold, otherwise single flits
  output int   errors, n_acc, n_hold_flits, n_bubbles, min_grants
);
  logic rst_n = 0;
  logic [N-1:0] req = 0, grant;
  logic hold = 0, accept = 0;
  arb_rr #(.N(N)) dut (.*);

  int           mptr = 0;            // model: pointer (requester with the highest priority)
  logic [N-1:0] mlast = 0;
  logic [N-1:0] expg;
  int           wait_cnt [N];        // accepted head grants to others while i has been requesting continuously
  int           grants [N];
  logic [N-1:0] want = 0;            // the sender's intent: persists until its packet is done
  int           owner = 0, left = 0; // packet in progress (after its head was accepted): owner and flits still to send
  logic         pkt = 0;
  logic         acc_now;
  int           widx;

  initial begin errors = 0; n_acc = 0; n_hold_flits = 0; n_bubbles = 0; min_grants = 0;
    for (int i = 0; i < N; i++) begin wait_cnt[i] = 0; grants[i] = 0; end end

  always @(posedge rst_req) begin
    #(1.3);
    rst_n = 0;
    mptr = 0; mlast = 0; pkt = 0; want = 0; left = 0;
    for (int i = 0; i < N; i++) wait_cnt[i] = 0;
    repeat (2) @(posedge clk);
    #1 rst_n = 1;
  end

  always @(posedge clk) begin
    if (rst_n === 1'b1) begin
      // reference grant
      expg = '0;
      if (hold) expg = mlast & req;
      else for (int i = 0; i < N; i++) begin
        widx = (mptr + i) % N;
        if (req[widx] && expg == '0) expg[widx] = 1'b1;
      end
      if (grant !== expg) begin errors++; $display("[%0d] grant %b != expected %b (req %b hold %b ptr %0d last %b)", ID, grant, expg, req, hold, mptr, mlast); end
      if ((grant & (grant - 1'b1)) != 0) begin errors++; $display("[%0d] grant not one-hot: %b", ID, grant); end
      if ((grant & ~req) != 0) begin errors++; $display("[%0d] grant to a non-requester: %b req %b", ID, grant, req); end
      if (!hold && req != 0 && grant == 0) begin errors++; $display("[%0d] no grant with requests %b", ID, req); end
      if (hold && grant == 0) n_bubbles++;

      acc_now = accept && (grant != 0);
      for (int i = 0; i < N; i++) if (!req[i]) wait_cnt[i] = 0;
      if (acc_now) begin
        n_acc++;
        for (int i = 0; i < N; i++) if (grant[i]) grants[i]++;
        if (!hold) begin                                      // a head (or single) flit: the pointer moves, others wait
          for (int i = 0; i < N; i++) begin
            if (grant[i]) begin
              mptr = (i + 1) % N; wait_cnt[i] = 0; owner = i;
            end else if (req[i]) begin
              wait_cnt[i]++;
              if (wait_cnt[i] > N - 1) begin errors++; $display("[%0d] requester %0d waited for %0d grants", ID, i, wait_cnt[i]); end
            end
          end
          left = use_hold ? $urandom_range(2) : 0;            // flits after the head
          pkt  = (left > 0);
          if (left == 0) want[owner] = 0;
        end else begin                                        // a body / tail flit under hold: pointer untouched
          n_hold_flits++;
          left--;
          if (left == 0) begin pkt = 0; want[owner] = 0; end
        end
        mlast = grant;
      end

      // next cycle's inputs
      for (int i = 0; i < N; i++) if (!want[i] && ($urandom_range(99) < preq)) want[i] = 1;
      for (int i = 0; i < N; i++) req[i] <= want[i] && !(pkt && i == owner && $urandom_range(4) == 0);   // the owner may bubble
      hold   <= pkt;
      accept <= ($urandom_range(99) < pacc);
    end else begin
      hold <= 0; accept <= 0; req <= 0;
    end
    min_grants = grants[0];
    for (int i = 1; i < N; i++) if (grants[i] < min_grants) min_grants = grants[i];
  end
endmodule

module tb_arb_rr;
  localparam int NC = 8;
  logic clk = 0;
  always #5 clk = ~clk;
  logic rst_req = 0;
  int preq = 50, pacc = 100;
  logic use_hold = 0;
  int errs [NC], nacc [NC], nhf [NC], nbub [NC], ming [NC];

  arb_rr_chk #(1, 0) c0 (clk, rst_req, preq, pacc, use_hold, errs[0], nacc[0], nhf[0], nbub[0], ming[0]);
  arb_rr_chk #(2, 1) c1 (clk, rst_req, preq, pacc, use_hold, errs[1], nacc[1], nhf[1], nbub[1], ming[1]);
  arb_rr_chk #(3, 2) c2 (clk, rst_req, preq, pacc, use_hold, errs[2], nacc[2], nhf[2], nbub[2], ming[2]);
  arb_rr_chk #(4, 3) c3 (clk, rst_req, preq, pacc, use_hold, errs[3], nacc[3], nhf[3], nbub[3], ming[3]);
  arb_rr_chk #(5, 4) c4 (clk, rst_req, preq, pacc, use_hold, errs[4], nacc[4], nhf[4], nbub[4], ming[4]);   // the router's port count
  arb_rr_chk #(6, 5) c5 (clk, rst_req, preq, pacc, use_hold, errs[5], nacc[5], nhf[5], nbub[5], ming[5]);
  arb_rr_chk #(8, 6) c6 (clk, rst_req, preq, pacc, use_hold, errs[6], nacc[6], nhf[6], nbub[6], ming[6]);
  arb_rr_chk #(16, 7) c7 (clk, rst_req, preq, pacc, use_hold, errs[7], nacc[7], nhf[7], nbub[7], ming[7]);

  int total;
  initial begin
    repeat (2) @(posedge clk);
    rst_req = 1; #5 rst_req = 0;
    repeat (6) @(posedge clk);
    for (int round = 0; round < 3; round++) begin
      for (int h = 0; h < 2; h++) begin
        use_hold = (h == 1);
        for (int ph = 0; ph < 5; ph++) begin
          case (ph)
            0: begin preq = 100; pacc = 100; end    // everyone always requesting, always accepted
            1: begin preq = 100; pacc = 40;  end    // everyone requesting, accepts stall
            2: begin preq = 10;  pacc = 100; end    // sparse requests
            3: begin preq = 40;  pacc = 70;  end
            4: begin preq = 100; pacc = 100; end
          endcase
          repeat (600) @(posedge clk);
        end
      end
      rst_req = 1; #5 rst_req = 0;                  // reset in the middle of traffic
      repeat (10) @(posedge clk);
    end
    total = 0;
    for (int i = 0; i < NC; i++) begin
      total += errs[i];
      if (ming[i] == 0) begin total++; $display("config %0d: a requester was never granted", i); end
      if (nacc[i] < 1000 || (i > 0 && nhf[i] == 0) || (i > 0 && nbub[i] == 0)) begin
        total++; $display("coverage hole in config %0d: accepts %0d hold flits %0d bubbles %0d", i, nacc[i], nhf[i], nbub[i]);
      end
    end
    if (total == 0) $display("PASS tb_arb_rr");
    else            $fatal(1, "FAIL tb_arb_rr: %0d errors", total);
    $finish;
  end
endmodule
