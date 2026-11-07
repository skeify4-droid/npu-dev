// mxu_pe_dot: the dot-product stage of an MXU PE (numerics.md N-3). One 32-bit operand word per side: four E4M3
// codes (FP8 mode) or two BF16 codes (BF16 mode, `mode_bf16` = 1). The result d is the dot product rounded once to
// FP32; the PE adds the partial sum from the PE above with `fp32_add` (second rounding).
// The BF16 path has its own two multipliers next to the four FP8 ones (N-3: sharing would give quarter rate).
// Combinational. flags = {nan_in, sat, ovf, invalid}.
module mxu_pe_dot (
  input  logic        mode_bf16,
  input  logic [31:0] a,
  input  logic [31:0] w,
  output logic [31:0] d,
  output logic [3:0]  flags
);
  logic [31:0] d8, d16;
  logic [3:0]  f8, f16;

  fp8_dot4  u_fp8  (.a(a), .w(w), .y(d8),  .flags(f8));
  bf16_dot2 u_bf16 (.a(a), .w(w), .y(d16), .flags(f16));

  assign d     = mode_bf16 ? d16 : d8;
  assign flags = mode_bf16 ? f16 : f8;
endmodule
