// Formal wrappers for credit_tx / credit_rx (SymbiYosys, single clock, k-induction).
//
//   credit_tx_formal    credit_tx alone, completely free inputs (including illegal sends and spurious credits):
//                       the counter stays in [0, DEPTH], never moves the wrong way, and the flags are exact.
//                       Then the same module with the legal-usage assumption (send only with a credit, credits only
//                       for flits in flight): conservation credits + in_flight == DEPTH, and no error flag.
//   credit_loop_formal  credit_tx -> receive buffer model -> credit_rx -> credit_tx, the receiver frees flits at will:
//                       credits + buffered + (credit in the return flop) == DEPTH on every VC, the buffer never
//                       holds more than DEPTH flits (no overflow), and a send never happens without a credit.
`ifndef FNVC
`define FNVC 2
`endif
`ifndef FDEPTH
`define FDEPTH 3
`endif

module credit_tx_formal #(
  parameter NVC   = `FNVC,
  parameter DEPTH = `FDEPTH,
  parameter PKT   = (DEPTH > 1) ? 2 : 1
) (
  input              clk,
  input              rst_n,
  input  [NVC-1:0]   send,
  input  [NVC-1:0]   credit_in
);
  localparam CW = $clog2(DEPTH + 1);
  wire [NVC-1:0]    can_send, can_send_pkt;
  wire [NVC*CW-1:0] credits;
  wire              err_underflow, err_overflow;
  (* anyconst *) reg legal;                  // a constant of the run: 0 = free inputs, 1 = legal usage only

  credit_tx #(.NVC(NVC), .DEPTH(DEPTH), .PKT(PKT)) dut (
    .clk(clk), .rst_n(rst_n), .send(send), .credit_in(credit_in), .can_send(can_send),
    .can_send_pkt(can_send_pkt), .credits(credits), .err_underflow(err_underflow), .err_overflow(err_overflow));

  reg init = 1;
  always @(posedge clk) init <= 0;
  always @(*) if (init) assume (!rst_n);

  integer v;
  reg [NVC-1:0] full;
  always @(*) for (v = 0; v < NVC; v = v + 1) full[v] = (credits[v*CW +: CW] == DEPTH);

  // model of the far end under legal usage: flits sent whose credit has not come back
  reg [CW-1:0] inflight [0:NVC-1];
  initial for (v = 0; v < NVC; v = v + 1) inflight[v] = 0;
  always @(posedge clk) begin
    for (v = 0; v < NVC; v = v + 1) begin
      if (!rst_n) inflight[v] <= 0;
      else if (send[v] && !credit_in[v])  inflight[v] <= inflight[v] + 1'b1;
      else if (!send[v] && credit_in[v])  inflight[v] <= inflight[v] - 1'b1;
    end
  end
  always @(*) begin
    if (legal)
      for (v = 0; v < NVC; v = v + 1) begin
        if (send[v])      assume (can_send[v]);            // a send needs a credit
        if (credit_in[v]) assume (inflight[v] != 0);       // a credit comes back only for a flit that is out
      end
  end

  // with any inputs: range, derived flags, exact error flags
  always @(*) begin
    if (rst_n) begin
      for (v = 0; v < NVC; v = v + 1) begin
        assert (credits[v*CW +: CW] <= DEPTH);
        assert (can_send[v]     == (credits[v*CW +: CW] != 0));
        assert (can_send_pkt[v] == (credits[v*CW +: CW] >= PKT));
      end
      assert (err_underflow == |(send & ~can_send));
      assert (err_overflow  == |(credit_in & full));
    end
  end

  // with any inputs: a counter moves by one credit, only on a matching event, never past 0 or DEPTH
  reg [NVC*CW-1:0] p_credits;
  reg [NVC-1:0]    p_send, p_cin;
  reg              p_ok = 0;
  always @(posedge clk) begin p_credits <= credits; p_send <= send; p_cin <= credit_in; p_ok <= rst_n; end
  always @(*) begin
    if (p_ok && rst_n)
      for (v = 0; v < NVC; v = v + 1) begin
        if (credits[v*CW +: CW] < p_credits[v*CW +: CW]) begin
          assert (p_send[v] && p_credits[v*CW +: CW] != 0);       // a decrement: a send, with a credit to take
          assert (credits[v*CW +: CW] + 1 == p_credits[v*CW +: CW]);
        end
        if (credits[v*CW +: CW] > p_credits[v*CW +: CW]) begin
          assert (p_cin[v] && p_credits[v*CW +: CW] != DEPTH);    // an increment: a returned credit, with room
          assert (credits[v*CW +: CW] == p_credits[v*CW +: CW] + 1);
        end
      end
  end

  // legal usage: credits + flits in flight == DEPTH, no error flag
  always @(*) begin
    if (legal && rst_n)
      for (v = 0; v < NVC; v = v + 1) begin
        assert (credits[v*CW +: CW] + inflight[v] == DEPTH);
        assert (!err_underflow && !err_overflow);
      end
  end

  always @(posedge clk) if (rst_n) begin
    cover (!legal && credits[0 +: CW] == 0);
    cover (legal && credits[0 +: CW] == 0);
    cover (legal && p_ok && credits[0 +: CW] == DEPTH && p_credits[0 +: CW] != DEPTH);
    cover (!legal && err_underflow);
    cover (!legal && err_overflow);
  end
endmodule

module credit_loop_formal #(
  parameter NVC   = `FNVC,
  parameter DEPTH = `FDEPTH
) (
  input              clk,
  input              rst_n,
  input  [NVC-1:0]   want_send,            // the sender would like to send on VC v
  input  [NVC-1:0]   want_free             // the receiver would like to free a flit from VC v
);
  localparam CW = $clog2(DEPTH + 1);
  wire [NVC-1:0]    can_send, can_send_pkt, credit_out, credit_in;
  wire [NVC*CW-1:0] credits;
  wire              err_underflow, err_overflow;
  reg  [CW-1:0]     buffered [0:NVC-1];     // flits in the receive buffer (the link is zero-latency here)
  integer v;

  reg init = 1;
  always @(posedge clk) init <= 0;
  always @(*) if (init) assume (!rst_n);

  wire [NVC-1:0] send = want_send & can_send;                       // sender obeys its credits
  reg  [NVC-1:0] free;
  always @(*) for (v = 0; v < NVC; v = v + 1) free[v] = want_free[v] && buffered[v] != 0;   // receiver frees what it holds

  credit_tx #(.NVC(NVC), .DEPTH(DEPTH), .PKT((DEPTH > 1) ? 2 : 1)) tx (
    .clk(clk), .rst_n(rst_n), .send(send), .credit_in(credit_in), .can_send(can_send),
    .can_send_pkt(can_send_pkt), .credits(credits), .err_underflow(err_underflow), .err_overflow(err_overflow));
  credit_rx #(.NVC(NVC), .REG(1'b1)) rx (.clk(clk), .rst_n(rst_n), .free(free), .credit_out(credit_out));
  assign credit_in = credit_out;

  initial for (v = 0; v < NVC; v = v + 1) buffered[v] = 0;
  always @(posedge clk) begin
    for (v = 0; v < NVC; v = v + 1) begin
      if (!rst_n) buffered[v] <= 0;
      else buffered[v] <= buffered[v] + send[v] - free[v];
    end
  end

  always @(*) begin
    if (rst_n) begin
      assert (!err_underflow && !err_overflow);
      for (v = 0; v < NVC; v = v + 1) begin
        assert (buffered[v] <= DEPTH);                                              // receive buffer never overflows
        assert (credits[v*CW +: CW] + buffered[v] + credit_out[v] == DEPTH);        // every credit is somewhere
      end
    end
  end

  always @(posedge clk) if (rst_n) begin
    cover (buffered[0] == DEPTH);
    cover (credit_out[0]);
    cover (credits[0 +: CW] == 0 && credit_out[0]);
  end
endmodule
