// Formal-only stand-ins (tb/formal/mxu.sby). The properties are about control, so the arithmetic of a PE is irrelevant:
// the stub keeps the ports and a little state, and no property depends on its value.
module mxu_pe #(
  parameter bit REG_DOT = 1'b1,
  parameter bit REG_OUT = 1'b1
) (
  input  logic        clk,
  input  logic        rst_n,
  input  logic        mode_bf16,
  input  logic [31:0] a,
  input  logic [31:0] w,
  input  logic [31:0] psum_in,
  output logic [31:0] psum_out,
  output logic [3:0]  flags
);
  always_ff @(posedge clk) psum_out <= psum_in ^ a ^ w ^ {31'd0, mode_bf16};
  assign flags = 4'd0;
  logic unused;
  assign unused = &{1'b0, rst_n, REG_DOT, REG_OUT};
endmodule
