// fp8_dot4: the FP8 half of the MXU PE (numerics.md N-3). Four E4M3 x E4M3 products formed exactly, added exactly in a
// 39-bit fixed-point adder (LSB 2^-18), and rounded once to FP32 (RNE). The add of the incoming partial sum is a
// separate FP32 add (`fp32_add`), so a PE has two roundings.
//
// a, w: four E4M3 codes each, lane i at [8*i +: 8]. Combinational.
// y: FP32 bits. A NaN operand gives the canonical NaN 0x7FC00000. An exact zero sum is +0, except when all four
// products are zero and negative (-0), as an IEEE add of the four products would give.
// flags = {nan_in, sat, ovf, invalid} (FP_STATUS bit order): only nan_in can be set.
module fp8_dot4 (
  input  logic [31:0] a,
  input  logic [31:0] w,
  output logic [31:0] y,
  output logic [3:0]  flags
);
  logic [3:0]        neg, nan;
  logic [3:0][35:0]  mag;
  logic signed [38:0] sum;
  logic [37:0]       amag;
  logic              any_nan, all_zero_neg, any_nz, s_out;
  logic [31:0]       r;
  logic              unused_ovf, unused_sat;

  for (genvar i = 0; i < 4; i++) begin : g_mul
    fp8_mul u_mul (.a(a[8*i +: 8]), .b(w[8*i +: 8]), .neg(neg[i]), .nan(nan[i]), .mag(mag[i]));
  end

  always_comb begin
    sum = '0;
    any_nz = 1'b0;
    all_zero_neg = 1'b1;
    for (int i = 0; i < 4; i++) begin
      sum = sum + (neg[i] ? -$signed({3'b000, mag[i]}) : $signed({3'b000, mag[i]}));
      if (mag[i] != 36'd0) any_nz = 1'b1;
      if (!neg[i]) all_zero_neg = 1'b0;
    end
    any_nan = |nan;
    s_out   = sum[38] | (!any_nz & all_zero_neg);
    amag    = sum[38] ? 38'(-sum) : sum[37:0];
  end

  fp_round #(.W(38), .EB(8), .MB(23), .KIND(0), .SAT(1'b0), .FTZ(1'b1), .EW(12), .SUBN_MAX(0)) u_rnd (
    .sign(s_out), .mag(amag), .elsb(12'sd0 - 12'sd18), .code(r), .ovf(unused_ovf), .sat(unused_sat));

  assign y     = any_nan ? 32'h7FC00000 : r;
  assign flags = {any_nan, 3'b000};
endmodule
