// bf16_dot2: the BF16 half of the MXU PE (numerics.md N-3). Two BF16 x BF16 products formed exactly (8 x 8 bit
// significands, 16-bit product), their exact sum rounded once to FP32 (RNE, FTZ). The PE then adds the incoming
// partial with `fp32_add`.
//
// a, w: two BF16 codes each, lane i at [16*i +: 16]. Combinational.
//   - BF16 subnormal operands read as zero (DAZ). A result below 2^-126 is rounded on the FP32 subnormal grid, then
//     flushed (as fp32_add).
//   - NaN operand, or Inf * 0, or Inf - Inf across the two products -> canonical NaN. Inf * finite -> Inf.
//   - Zero sum: (-0) + (-0) = -0, otherwise +0.
//   - flags = {nan_in, sat, ovf, invalid}.
module bf16_dot2 (
  input  logic [31:0] a,
  input  logic [31:0] w,
  output logic [31:0] y,
  output logic [3:0]  flags
);
  logic [1:0]        p_nan, p_inf, p_z, p_s, in_nan;
  logic [1:0][15:0]  p_m;
  logic signed [1:0][11:0] p_e;
  logic              nan_r, inv, sum_nan, ovf, unused_sat, u_s;
  logic [28:0]       u_mag;
  logic signed [11:0] u_elsb;
  logic [31:0]       r;

  always_comb begin
    for (int i = 0; i < 2; i++) begin
      logic [15:0] x, v;
      logic        x_nan, x_inf, x_z, v_nan, v_inf, v_z;
      logic [15:0] prod;
      logic signed [11:0] e;
      x = a[16*i +: 16];
      v = w[16*i +: 16];
      x_nan = (x[14:7] == 8'hFF) && (x[6:0] != 7'd0);
      v_nan = (v[14:7] == 8'hFF) && (v[6:0] != 7'd0);
      x_inf = (x[14:7] == 8'hFF) && (x[6:0] == 7'd0);
      v_inf = (v[14:7] == 8'hFF) && (v[6:0] == 7'd0);
      x_z   = (x[14:7] == 8'h00);
      v_z   = (v[14:7] == 8'h00);
      prod  = {1'b1, x[6:0]} * {1'b1, v[6:0]};              // in [2^14, 2^16)
      e     = 12'(signed'({1'b0, x[14:7]})) + 12'(signed'({1'b0, v[14:7]})) - 12'sd254;
      in_nan[i] = x_nan | v_nan;
      p_nan[i]  = x_nan | v_nan | (x_inf & v_z) | (v_inf & x_z);
      p_inf[i]  = (x_inf | v_inf) & !p_nan[i];
      p_z[i]    = x_z | v_z;
      p_s[i]    = x[15] ^ v[15];
      p_m[i]    = p_z[i] ? 16'd0 : (prod[15] ? prod : {prod[14:0], 1'b0});
      p_e[i]    = prod[15] ? e + 12'sd1 : e;
    end
    inv     = !(|in_nan) && ((p_nan[0] | p_nan[1]) || (p_inf[0] && p_inf[1] && (p_s[0] != p_s[1])));
    sum_nan = (|p_nan) | (p_inf[0] & p_inf[1] & (p_s[0] != p_s[1]));
    nan_r   = sum_nan;
  end

  fp_addu #(.SW(16), .G(12), .EW(12)) u_add (
    .a_s(p_s[0]), .a_z(p_z[0]), .a_e(p_e[0]), .a_m(p_m[0]),
    .b_s(p_s[1]), .b_z(p_z[1]), .b_e(p_e[1]), .b_m(p_m[1]),
    .s(u_s), .mag(u_mag), .elsb(u_elsb));

  fp_round #(.W(29), .EB(8), .MB(23), .KIND(0), .SAT(1'b0), .FTZ(1'b1), .EW(12), .SUBN_MAX(1)) u_rnd (
    .sign(u_s), .mag(u_mag), .elsb(u_elsb), .code(r), .ovf(ovf), .sat(unused_sat));

  always_comb begin
    if (nan_r)         y = 32'h7FC00000;
    else if (p_inf[0]) y = {p_s[0], 8'hFF, 23'd0};
    else if (p_inf[1]) y = {p_s[1], 8'hFF, 23'd0};
    else               y = r;
    flags = {|in_nan, 1'b0, ovf & !(|p_inf) & !nan_r, inv};
  end
endmodule
