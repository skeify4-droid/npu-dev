// Round-robin arbiter: N requesters, one-hot grant, combinational grant from registered state.
//
// Every cycle grant is one-hot or zero and never to a non-requester.
//   - grant = the first requester at or after the pointer (wrapping); the pointer is the requester after the last accepted winner.
//   - `accept` says the grant was taken this cycle (downstream ready / flit sent). Only then does the state change:
//     the pointer moves to the requester after the winner. A grant that is not accepted changes nothing, so the same
//     requester keeps winning until it is taken (no starvation of the winner by a stalled consumer, no skipped turn).
//     `accept` with no grant is ignored.
//   - `hold` is the lock for multi-flit packets: while it is high the arbiter grants only the requester that won the
//     last accepted grant (`last`), and only while that requester requests. If it drops its request the grant is
//     zero (nobody else may interleave). The caller raises `hold` from the cycle after a head flit is accepted until
//     the cycle after the tail flit is accepted (i.e. "a packet is in progress"). The pointer is moved by the head
//     flit's accept (body / tail flits leave it where it is), so the next packet starts after the packet's owner.
//     Tie `hold` to 0 for single-flit traffic.
//
// Fairness: with hold = 0 and every grant accepted, a requester that keeps requesting is granted within N cycles
// (formal: tb/formal/arb.sby). With hold used, a requester waits at most one whole packet per other requester.
//
// Parameters
//   N  number of requesters, any value >= 1
module arb_rr #(
  parameter int N = 4
) (
  input  logic         clk,
  input  logic         rst_n,
  input  logic [N-1:0] req,
  input  logic         hold,
  input  logic         accept,
  output logic [N-1:0] grant
);
  // The pointer is kept as a mask of the requesters at or after it (thermometer code): all ones after reset (pointer 0);
  // after winner w the bits above w. The winner is the lowest masked request, or the lowest request when none is masked in.
  logic [N-1:0] mask;
  logic [N-1:0] last;           // one-hot (or zero after reset): winner of the last accepted grant
  logic [N-1:0] mreq, g_masked, g_all, rr_grant;

  assign mreq     = req & mask;
  assign g_masked = mreq & (~mreq + 1'b1);       // lowest set bit
  assign g_all    = req  & (~req  + 1'b1);
  assign rr_grant = (mreq != '0) ? g_masked : g_all;

  assign grant = hold ? (last & req) : rr_grant;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      mask <= '1;
      last <= '0;
    end else if (accept && (grant != '0)) begin
      last <= grant;
      mask <= ~(grant | (grant - 1'b1));          // under hold the grant is `last`, so a body flit rewrites the same value
    end
  end
endmodule
