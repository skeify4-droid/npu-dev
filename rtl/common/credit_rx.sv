// Credit-based flow control, receiver side: returns one credit for every flit freed from the VC buffer.
//
// `free[v]` is high for one cycle when a flit leaves the receive buffer of VC v (the buffer slot is reusable). The
// credit goes out on `credit_out[v]` in the NEXT cycle (registered, so the return link starts at a flop): the credit
// return time is free-cycle + 1, plus the link's own latency, plus one cycle in credit_tx. One flit can be freed per
// VC per cycle, and each credit wire carries one credit per cycle, so no credit is ever queued or lost.
//
// Choice (status note, open question): registered return (REG = 1, default). REG = 0 passes `free` through
// combinationally for a receiver that wants the credit one cycle earlier and registers it itself.
//
// Parameters
//   NVC  number of virtual channels
//   REG  1 (default): credit_out is a flop output; 0: credit_out = free
module credit_rx #(
  parameter int NVC = 5,
  parameter bit REG = 1'b1
) (
  input  logic           clk,
  input  logic           rst_n,
  input  logic [NVC-1:0] free,
  output logic [NVC-1:0] credit_out
);
  if (REG) begin : g_reg
    always_ff @(posedge clk or negedge rst_n) begin
      if (!rst_n) credit_out <= '0;
      else        credit_out <= free;
    end
  end else begin : g_comb
    assign credit_out = free;
    logic unused;
    assign unused = &{1'b0, clk, rst_n};
  end
endmodule
