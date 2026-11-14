// Review part H: a control-only stand-in for mxu_pe (hz_run.py --stub), so that the control of mxu can be run at DIM 32 / 64
// in Icarus. Same ports; the partial sum is a cheap function of the inputs, so hz_run.py checks no partial sum in this mode.
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
