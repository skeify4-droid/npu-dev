// fp32_add: IEEE-754 binary32 addition, RNE, DAZ on inputs and FTZ on the result (numerics.md principles, N-3 ACC
// input, N-6 write-add). Combinational.
//   - Subnormal inputs read as zero (sign kept). A result below 2^-126 is rounded on the subnormal grid and then
//     flushed to a signed zero (an exact difference of two normals is flushed; none can round up to 2^-126).
//   - x + (-x) = +0; (-0) + (-0) = -0.
//   - NaN in -> canonical NaN 0x7FC00000. Inf + (-Inf) -> canonical NaN. Overflow -> Inf.
//   - flags = {nan_in, sat, ovf, invalid}: nan_in = an input is NaN; invalid = NaN made from non-NaN inputs;
//     ovf = a finite sum rounded to Inf.
module fp32_add (
  input  logic [31:0] a,
  input  logic [31:0] b,
  output logic [31:0] y,
  output logic [3:0]  flags
);
  logic a_nan, b_nan, a_inf, b_inf, a_z, b_z, nan_r, inv, ovf, unused_sat;
  logic u_s;
  logic [28:0] u_mag;
  logic signed [11:0] u_elsb;
  logic [31:0] r;

  always_comb begin
    a_nan = (a[30:23] == 8'hFF) && (a[22:0] != 23'd0);
    b_nan = (b[30:23] == 8'hFF) && (b[22:0] != 23'd0);
    a_inf = (a[30:23] == 8'hFF) && (a[22:0] == 23'd0);
    b_inf = (b[30:23] == 8'hFF) && (b[22:0] == 23'd0);
    a_z   = (a[30:23] == 8'h00);
    b_z   = (b[30:23] == 8'h00);
    inv   = !a_nan && !b_nan && a_inf && b_inf && (a[31] != b[31]);
    nan_r = a_nan | b_nan | inv;
  end

  fp_addu #(.SW(24), .G(4), .EW(12)) u_add (
    .a_s(a[31]), .a_z(a_z), .a_e(12'(signed'({1'b0, a[30:23]})) - 12'sd127), .a_m(a_z ? 24'd0 : {1'b1, a[22:0]}),
    .b_s(b[31]), .b_z(b_z), .b_e(12'(signed'({1'b0, b[30:23]})) - 12'sd127), .b_m(b_z ? 24'd0 : {1'b1, b[22:0]}),
    .s(u_s), .mag(u_mag), .elsb(u_elsb));

  fp_round #(.W(29), .EB(8), .MB(23), .KIND(0), .SAT(1'b0), .FTZ(1'b1), .EW(12), .SUBN_MAX(0)) u_rnd (
    .sign(u_s), .mag(u_mag), .elsb(u_elsb), .code(r), .ovf(ovf), .sat(unused_sat));

  always_comb begin
    if (nan_r)      y = 32'h7FC00000;
    else if (a_inf) y = {a[31], 8'hFF, 23'd0};
    else if (b_inf) y = {b[31], 8'hFF, 23'd0};
    else            y = r;
    flags = {a_nan | b_nan, 1'b0, ovf & !a_inf & !b_inf & !nan_r, inv};
  end
endmodule
