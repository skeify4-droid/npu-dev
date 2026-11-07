// fp32_max: FP32 maximum for the write-add ALU `MAX_F32` (numerics.md N-5, N-6). Combinational.
//   - A NaN operand gives the canonical NaN 0x7FC00000 (NaN wins) and flags.nan_in.
//   - Subnormal operands read as zero (DAZ, sign kept); the result is that signed zero when it wins.
//   - Inf is ordered. max(-0, +0) = +0 in either operand order; max(-0, -0) = -0.
//   - flags = {nan_in, sat, ovf, invalid}: only nan_in.
module fp32_max (
  input  logic [31:0] a,
  input  logic [31:0] b,
  output logic [31:0] y,
  output logic [3:0]  flags
);
  logic        a_nan, b_nan, a_ge;
  logic [30:0] am, bm;

  always_comb begin
    a_nan = (a[30:23] == 8'hFF) && (a[22:0] != 23'd0);
    b_nan = (b[30:23] == 8'hFF) && (b[22:0] != 23'd0);
    am    = (a[30:23] == 8'h00) ? 31'd0 : a[30:0];
    bm    = (b[30:23] == 8'h00) ? 31'd0 : b[30:0];
    if ((am == 31'd0) && (bm == 31'd0)) a_ge = ~a[31] | b[31];       // +0 beats -0; equal zeros keep a
    else if (a[31] != b[31])            a_ge = ~a[31];
    else if (!a[31])                    a_ge = (am >= bm);
    else                                a_ge = (am <= bm);
    if (a_nan | b_nan) y = 32'h7FC00000;
    else if (a_ge)     y = {a[31], am};
    else               y = {b[31], bm};
    flags = {a_nan | b_nan, 3'b000};
  end
endmodule
