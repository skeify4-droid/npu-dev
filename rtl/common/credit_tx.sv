// Credit-based flow control, sender side: one credit counter per virtual channel (noc.md section 1-4, credit per VC,
// virtual cut-through).
//
// A VC starts with DEPTH credits (the receiver's buffer depth for that VC). Each flit sent on VC v takes one credit,
// each credit returned by the receiver (credit_rx at the far end) gives one back. The counter is saturating:
//   - never below 0: a send without a credit is ignored by the counter and flagged on err_underflow;
//   - never above DEPTH: a credit that arrives with the counter full is ignored and flagged on err_overflow.
// Neither flag may fire in a correct system; both are combinational and meant for assertions / sticky error bits.
//
// Ports use one bit per VC (packed vectors). More than one VC may send in the same cycle if the caller's link
// allows it; the counters are independent.
//
// Parameters
//   NVC    number of virtual channels (the router uses 5)
//   DEPTH  per-VC credits at reset = per-VC receiver buffer depth in flits (>= 1)
//   PKT    credits needed to start a packet (virtual cut-through: the largest packet in flits, 2 for noc.md; <= DEPTH)
//
// can_send_pkt is a virtual cut-through test only if no new head enters a VC while a packet is in progress on it: it
// does not count the credits still owed to the body of a packet already started. The caller guarantees that (output
// VC allocated per packet, or a port-level lock such as arb_rr `hold`). Counter-example without it, DEPTH 4 with 3
// credits: A head (2 left), B head passes >= 2 (1 left), A tail (0 left), B tail has no credit; B's head sits in the
// receive buffer without its tail and the VC's queue interleaves two packets.
//
// Timing: a send in cycle t shows in `credits` in t+1. A credit_in in cycle t shows in `can_send` in t+1.
// Width-generic: it never sees flit data, so it does not depend on flit_t.
module credit_tx #(
  parameter int  NVC   = 5,
  parameter int  DEPTH = 4,
  parameter int  PKT   = 2,
  localparam int CW    = $clog2(DEPTH + 1)
) (
  input  logic               clk,
  input  logic               rst_n,
  input  logic [NVC-1:0]     send,            // a flit leaves on VC v this cycle (caller checked can_send)
  input  logic [NVC-1:0]     credit_in,       // a credit returns for VC v this cycle
  output logic [NVC-1:0]     can_send,        // VC v has at least one credit (body / single flit)
  output logic [NVC-1:0]     can_send_pkt,    // VC v has at least PKT credits (a packet head may start)
  output logic [NVC*CW-1:0]  credits,         // current credits, CW bits per VC (VC v at [v*CW +: CW])
  output logic               err_underflow,   // send on a VC without a credit
  output logic               err_overflow     // credit returned to a VC that already has all its credits
);
  logic [CW-1:0] cnt [NVC];
  logic [NVC-1:0] under_v, over_v;

  if (DEPTH < 1 || PKT < 1 || PKT > DEPTH) begin : g_bad_param
    $error("credit_tx: need DEPTH >= 1 and 1 <= PKT <= DEPTH");
  end

  always_comb begin
    for (int v = 0; v < NVC; v++) begin
      can_send[v]     = (cnt[v] != '0);
      can_send_pkt[v] = (cnt[v] >= CW'(PKT));
      credits[v*CW +: CW] = cnt[v];
      under_v[v]      = send[v] && (cnt[v] == '0);
      over_v[v]       = credit_in[v] && (cnt[v] == CW'(DEPTH));
    end
  end
  assign err_underflow = |under_v;
  assign err_overflow  = |over_v;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int v = 0; v < NVC; v++) cnt[v] <= CW'(DEPTH);
    end else begin
      for (int v = 0; v < NVC; v++) begin
        case ({send[v] && !under_v[v], credit_in[v] && !over_v[v]})
          2'b10:   cnt[v] <= cnt[v] - 1'b1;
          2'b01:   cnt[v] <= cnt[v] + 1'b1;
          default: cnt[v] <= cnt[v];
        endcase
      end
    end
  end
endmodule
