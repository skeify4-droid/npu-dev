// Trivial pipeline-register adder: harness check for the Phase 0 flow.
module trivial_add #(
  parameter int W = 8
) (
  input  logic         clk,
  input  logic         rst_n,
  input  logic [W-1:0] a,
  input  logic [W-1:0] b,
  output logic [W:0]   sum
);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) sum <= '0;
    else        sum <= {1'b0, a} + {1'b0, b};
  end
endmodule
