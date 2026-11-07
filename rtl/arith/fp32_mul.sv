// fp32_mul: IEEE-754 binary32 multiplication, RNE, DAZ on inputs and FTZ on the result (the rowscale / colscale
// multipliers at the ACC input, N-3). Combinational.
//   - Subnormal inputs read as zero. A result below 2^-126 is rounded on the subnormal grid, then flushed to a signed
//     zero. The sign of a zero result is the XOR of the input signs.
//   - NaN in -> canonical NaN; Inf * 0 -> canonical NaN (invalid); overflow -> Inf.
//   - flags = {nan_in, sat, ovf, invalid} as fp32_add.
module fp32_mul (
  input  logic [31:0] a,
  input  logic [31:0] b,
  output logic [31:0] y,
  output logic [3:0]  flags
);
  logic a_nan, b_nan, a_inf, b_inf, a_z, b_z, nan_r, inv, ovf, unused_sat, s;
  logic [23:0] ma, mb;
  logic [47:0] p;
  logic signed [11:0] elsb;
  logic [31:0] r;

  always_comb begin
    a_nan = (a[30:23] == 8'hFF) && (a[22:0] != 23'd0);
    b_nan = (b[30:23] == 8'hFF) && (b[22:0] != 23'd0);
    a_inf = (a[30:23] == 8'hFF) && (a[22:0] == 23'd0);
    b_inf = (b[30:23] == 8'hFF) && (b[22:0] == 23'd0);
    a_z   = (a[30:23] == 8'h00);
    b_z   = (b[30:23] == 8'h00);
    inv   = !a_nan && !b_nan && ((a_inf && b_z) || (b_inf && a_z));
    nan_r = a_nan | b_nan | inv;
    s     = a[31] ^ b[31];
    ma    = a_z ? 24'd0 : {1'b1, a[22:0]};
    mb    = b_z ? 24'd0 : {1'b1, b[22:0]};
    p     = ma * mb;
    // value = ma * mb * 2^(ea - 127 - 23 + eb - 127 - 23)
    elsb  = 12'(signed'({1'b0, a[30:23]})) + 12'(signed'({1'b0, b[30:23]})) - 12'sd300;
  end

  fp_round #(.W(48), .EB(8), .MB(23), .KIND(0), .SAT(1'b0), .FTZ(1'b1), .EW(12), .SUBN_MAX(1)) u_rnd (
    .sign(s), .mag(p), .elsb(elsb), .code(r), .ovf(ovf), .sat(unused_sat));

  always_comb begin
    if (nan_r)                y = 32'h7FC00000;
    else if (a_inf || b_inf)  y = {s, 8'hFF, 23'd0};
    else                      y = r;
    flags = {a_nan | b_nan, 1'b0, ovf & !a_inf & !b_inf & !nan_r, inv};
  end
endmodule
