// Synchronous FIFO, valid/ready on both sides.
//
// Parameters
//   WIDTH        data width in bits
//   DEPTH        entries, any value >= 1 (not only powers of two)
//   FALLTHROUGH  0 (default): registered read, out_valid rises the cycle after a push into an empty FIFO.
//                1: fall-through, an empty FIFO shows in_valid/in_data on out_valid/out_data in the same cycle
//                   (combinational in_valid -> out_valid and in_data -> out_data path). A word taken in that cycle
//                   is not stored.
//
// Choices (listed as open questions in docs/reports/status.md, "Phase 3: common blocks")
//   - in_ready = !full, independent of out_ready: a full FIFO accepts nothing even if it is popped in the same cycle.
//     No combinational path from out_ready to in_ready.
//   - FALLTHROUGH defaults to 0 (no combinational valid path through the block).
//   - count / full / empty describe the stored entries. With FALLTHROUGH a bypassed word is never counted.
//   - Memory is not reset (only the pointers and the count), out_data is undefined while out_valid is 0.
//   - Reset is asynchronous, active low (as trivial_add).
module fifo #(
  parameter int  WIDTH       = 8,
  parameter int  DEPTH       = 4,
  parameter bit  FALLTHROUGH = 1'b0,
  localparam int CW          = $clog2(DEPTH + 1)
) (
  input  logic             clk,
  input  logic             rst_n,
  // write side
  input  logic             in_valid,
  output logic             in_ready,
  input  logic [WIDTH-1:0] in_data,
  // read side
  output logic             out_valid,
  input  logic             out_ready,
  output logic [WIDTH-1:0] out_data,
  // status
  output logic [CW-1:0]    count,
  output logic             full,
  output logic             empty
);
  localparam int PW = (DEPTH > 1) ? $clog2(DEPTH) : 1;
  localparam logic [PW-1:0] LAST = PW'(DEPTH - 1);

  logic [WIDTH-1:0] mem [DEPTH];
  logic [PW-1:0]    wptr, rptr;
  logic [CW-1:0]    cnt;
  logic             push, pop, store, deq;

  assign count    = cnt;
  assign full     = (cnt == CW'(DEPTH));
  assign empty    = (cnt == '0);
  assign in_ready = !full;

  if (FALLTHROUGH) begin : g_ft
    assign out_valid = !empty || in_valid;
    assign out_data  = empty ? in_data : mem[rptr];
  end else begin : g_reg
    assign out_valid = !empty;
    assign out_data  = mem[rptr];
  end

  assign push  = in_valid && in_ready;
  assign pop   = out_valid && out_ready;
  assign deq   = pop && !empty;                        // a pop of a stored word
  assign store = push && !(pop && empty);              // a push that is not bypassed (bypass only exists with FALLTHROUGH)

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      wptr <= '0;
      rptr <= '0;
      cnt  <= '0;
    end else begin
      if (store) begin
        wptr <= (wptr == LAST) ? '0 : wptr + 1'b1;
      end
      if (deq) begin
        rptr <= (rptr == LAST) ? '0 : rptr + 1'b1;
      end
      cnt <= cnt + CW'(store) - CW'(deq);
    end
  end

  always_ff @(posedge clk) begin
    if (store) mem[wptr] <= in_data;
  end
endmodule
