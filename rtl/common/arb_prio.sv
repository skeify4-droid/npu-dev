// Fixed-priority arbiter: N requesters, one-hot grant, the lowest index wins. Combinational grant, registered `last`.
//
// Same ports and `hold` / `accept` meaning as arb_rr, so the two are interchangeable:
//   - grant = lowest-numbered requester, one-hot or zero, never a non-requester;
//   - `accept` with a grant records the winner in `last`; nothing else is stateful (there is no fairness pointer);
//   - `hold` (a multi-flit packet is in progress) grants only `last`, and only while it requests. Tie it to 0 for
//     single-flit traffic.
// Low indices can starve high ones by design. Use it where the order is a rule (for example response before request).
//
// Parameters
//   N  number of requesters, any value >= 1
module arb_prio #(
  parameter int N = 4
) (
  input  logic         clk,
  input  logic         rst_n,
  input  logic [N-1:0] req,
  input  logic         hold,
  input  logic         accept,
  output logic [N-1:0] grant
);
  logic [N-1:0] last;
  logic [N-1:0] pr_grant;

  assign pr_grant = req & (~req + 1'b1);        // lowest set bit

  assign grant = hold ? (last & req) : pr_grant;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)                        last <= '0;
    else if (accept && (grant != '0))  last <= grant;
  end
endmodule
