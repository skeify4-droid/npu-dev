// mxu_pe: one processing element of the weight-stationary array (microarch.md 2.2, numerics.md N-3), arithmetic part.
//   psum_out = fp32_add(psum_in, dot(a, w))      (two roundings: the dot, then this add)
// Stationary weight registers, shadow buffers and the A forwarding registers belong to core/mxu; this block takes
// the operand words and the partial sum.
//
// Parameters
//   REG_DOT  1: the dot product is registered before the add. `a`/`w` must then be presented one cycle before the
//               `psum_in` they belong to (the A skew of the array absorbs this).
//   REG_OUT  1: psum_out is registered (one PE row per cycle down a column).
// Latency a/w -> psum_out is REG_DOT + REG_OUT cycles, psum_in -> psum_out is REG_OUT cycles. `flags` are aligned
// with psum_out; they are not valid-qualified (the MXU masks them with the data valid it carries).
// Datapath registers have no reset (the array's valid bits qualify them); `rst_n` only clears `flags`' registers.
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
  logic [31:0] d, d_q, sum;
  logic [3:0]  f_dot, f_dot_q, f_add;

  mxu_pe_dot u_dot (.mode_bf16(mode_bf16), .a(a), .w(w), .d(d), .flags(f_dot));

  if (REG_DOT) begin : g_rd
    always_ff @(posedge clk) d_q <= d;
    always_ff @(posedge clk or negedge rst_n) begin
      if (!rst_n) f_dot_q <= '0;
      else        f_dot_q <= f_dot;
    end
  end else begin : g_nrd
    assign d_q     = d;
    assign f_dot_q = f_dot;
  end

  fp32_add u_add (.a(psum_in), .b(d_q), .y(sum), .flags(f_add));

  // nan_in of the add counts a NaN that was already in psum_in, which a previous PE has reported; report only what
  // is new: the dot's nan_in, the add's ovf / invalid. (A NaN made by the dot itself is reported through the add's
  // nan_in as well, so nan_in is the dot's flag only.)
  if (!REG_DOT && !REG_OUT) begin : g_comb
    logic unused_ck;
    assign unused_ck = &{1'b0, clk, rst_n};
  end

  logic [3:0] f_new;
  logic       unused;
  assign unused = &{1'b0, f_dot_q[2], f_add[3:2]};
  assign f_new = {f_dot_q[3], 1'b0, f_add[1] | f_dot_q[1], f_add[0] | f_dot_q[0]};

  if (REG_OUT) begin : g_ro
    always_ff @(posedge clk) psum_out <= sum;
    always_ff @(posedge clk or negedge rst_n) begin
      if (!rst_n) flags <= '0;
      else        flags <= f_new;
    end
  end else begin : g_nro
    assign psum_out = sum;
    assign flags    = f_new;
  end
endmodule
