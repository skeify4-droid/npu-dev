// Formal wrappers for arb_rr and arb_prio (SymbiYosys, single clock). All inputs are free, hold and accept included.
//
//   arb_rr_formal / arb_prio_formal : grant one-hot or zero, grant only to a requester, a request is granted when nothing
//       else requests (and any request is granted when hold is low), hold grants only the last accepted winner,
//       and equality with an independent reference model that keeps its own pointer (arb_rr) or the lowest-index rule
//       (arb_prio). A grant that is not accepted changes nothing. k-induction.
//   arb_rr_fair_formal : with hold low and every grant accepted, a request that stays up is granted within N cycles.
`ifndef FN
`define FN 4
`endif

module arb_rr_formal #(
  parameter N = `FN
) (
  input          clk,
  input          rst_n,
  input  [N-1:0] req,
  input          hold,
  input          accept
);
  wire [N-1:0] grant;
  arb_rr #(.N(N)) dut (.clk(clk), .rst_n(rst_n), .req(req), .hold(hold), .accept(accept), .grant(grant));

  reg init = 1;
  always @(posedge clk) init <= 0;
  always @(*) if (init) assume (!rst_n);

  // reference model: explicit pointer, index loop
  reg [3:0] m_ptr = 0;
  integer i, w;
  reg [N-1:0] m_last = 0, m_grant;
  always @(*) begin
    m_grant = 0;
    for (i = 0; i < N; i = i + 1) begin
      w = (m_ptr + i) % N;
      if (((req >> w) & 1'b1) && m_grant == 0) m_grant = {{(N-1){1'b0}}, 1'b1} << w;
    end
    if (hold) m_grant = m_last & req;
  end
  wire acc = accept && (grant != 0);
  always @(posedge clk) begin
    if (!rst_n) begin m_ptr <= 0; m_last <= 0; end
    else if (acc) begin
      m_last <= grant;
      for (i = 0; i < N; i = i + 1) if (grant[i]) m_ptr <= (i + 1) % N;
    end
  end

  always @(*) if (rst_n) begin
    assert ((grant & (grant - 1'b1)) == 0);                           // one-hot or zero
    assert ((grant & ~req) == 0);                                     // only to a requester
    if (!hold && req != 0) assert (grant != 0);                       // a request is always served when not locked
    if (!hold && (req & (req - 1'b1)) == 0) assert (grant == req);    // alone: granted
    if (hold) assert (grant == (m_last & req));                       // locked: only the last winner
    assert (grant == m_grant);                                        // the whole behaviour, against the reference
  end

  // a grant that is not accepted changes no state: same inputs, same grant next cycle
  reg [N-1:0] p_req = 0, p_grant = 0;
  reg         p_hold = 0, p_acc = 0, p_ok = 0;
  always @(posedge clk) begin p_req <= req; p_grant <= grant; p_hold <= hold; p_acc <= acc; p_ok <= rst_n; end
  always @(*) if (rst_n && p_ok) begin
    if (!p_acc && req == p_req && hold == p_hold) assert (grant == p_grant);
    // pointer moves past the winner: the same request set cannot give the same winner again unless it is alone
    if (p_acc && !p_hold && !hold && req == p_req && (req & (req - 1'b1)) != 0) assert (grant != p_grant);
  end

  always @(posedge clk) if (rst_n) begin
    cover (acc && !hold && req == {N{1'b1}});
    cover (hold && grant != 0);
    cover (hold && grant == 0 && req != 0);
  end
endmodule

module arb_prio_formal #(
  parameter N = `FN
) (
  input          clk,
  input          rst_n,
  input  [N-1:0] req,
  input          hold,
  input          accept
);
  wire [N-1:0] grant;
  arb_prio #(.N(N)) dut (.clk(clk), .rst_n(rst_n), .req(req), .hold(hold), .accept(accept), .grant(grant));

  reg init = 1;
  always @(posedge clk) init <= 0;
  always @(*) if (init) assume (!rst_n);

  integer i;
  reg [N-1:0] m_last = 0, m_grant;
  always @(*) begin
    m_grant = 0;
    for (i = N - 1; i >= 0; i = i - 1) if (req[i]) begin m_grant = 0; m_grant[i] = 1'b1; end   // lowest index wins
    if (hold) m_grant = m_last & req;
  end
  wire acc = accept && (grant != 0);
  always @(posedge clk) begin
    if (!rst_n) m_last <= 0;
    else if (acc) m_last <= grant;
  end

  always @(*) if (rst_n) begin
    assert ((grant & (grant - 1'b1)) == 0);
    assert ((grant & ~req) == 0);
    if (!hold && req != 0) assert (grant != 0);
    if (!hold && (req & (req - 1'b1)) == 0) assert (grant == req);
    if (!hold) assert (grant == (req & (~req + 1'b1)));                // lowest set bit
    if (!hold) for (i = 0; i < N; i = i + 1) if (grant[i]) assert ((req & ((1 << i) - 1)) == 0);   // nobody lower requests
    if (hold) assert (grant == (m_last & req));
    assert (grant == m_grant);
  end

  always @(posedge clk) if (rst_n) begin
    cover (!hold && req == {N{1'b1}});
    cover (hold && grant != 0);
  end
endmodule

module arb_rr_fair_formal #(
  parameter N = `FN
) (
  input          clk,
  input          rst_n,
  input  [N-1:0] req
);
  wire [N-1:0] grant;
  wire         hold = 1'b0;
  wire         accept = (grant != 0);                    // every grant is taken
  arb_rr #(.N(N)) dut (.clk(clk), .rst_n(rst_n), .req(req), .hold(hold), .accept(accept), .grant(grant));

  (* anyconst *) reg [2:0] t;                            // the requester under watch
  reg init = 1;
  always @(posedge clk) init <= 0;
  always @(*) begin
    if (init) assume (!rst_n);
    if (!init) assume (rst_n);                            // no reset after the first cycle (reset clears a waiting count)
    assume (t < N);
  end
  // the request stays up until it is granted
  wire rq_t = (req >> t) & 1'b1, gr_t = (grant >> t) & 1'b1;     // shifts: t < N is only assumed
  reg p_wait = 0;
  reg [7:0] wcnt = 0;
  always @(posedge clk) begin
    p_wait <= rst_n && rq_t && !gr_t;
    if (rst_n && rq_t && !gr_t) wcnt <= wcnt + 1'b1; else wcnt <= 0;
  end
  always @(*) if (!init && p_wait) assume (rq_t);
  always @(*) if (!init) assert (wcnt < N);              // granted within N cycles of requesting
  always @(posedge clk) if (!init) cover (wcnt == N - 1);
endmodule
