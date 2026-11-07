// fp8_mul: exact E4M3 x E4M3 product as a fixed-point magnitude (numerics.md N-3, FP8 PE).
// |a * b| = mag * 2^-18. The largest product is 448 * 448 = 200704, below 2^18 (mag < 2^36); the smallest non-zero
// is 2^-9 * 2^-9 = 2^-18 (mag = 1). `neg` is the product's sign (also for a zero product). `nan`: either operand is
// the E4M3 NaN (S.1111.111). E4M3 has no Inf.
module fp8_mul (
  input  logic [7:0]  a,
  input  logic [7:0]  b,
  output logic        neg,
  output logic        nan,
  output logic [35:0] mag
);
  logic [3:0] sa, sb;
  logic [3:0] ea, eb;
  logic [7:0] p;
  logic [4:0] shift;

  always_comb begin
    sa = {(a[6:3] != 4'd0), a[2:0]};
    sb = {(b[6:3] != 4'd0), b[2:0]};
    ea = (a[6:3] == 4'd0) ? 4'd1 : a[6:3];       // exponent field, subnormals use 1
    eb = (b[6:3] == 4'd0) ? 4'd1 : b[6:3];
    p  = sa * sb;
    shift = 5'({1'b0, ea}) + 5'({1'b0, eb}) - 5'd2;     // value = p * 2^(ea + eb - 14 - 6), LSB 2^-18 -> shift ea + eb - 2
    mag = 36'(p) << shift;
    neg = a[7] ^ b[7];
    nan = (a[6:0] == 7'h7F) | (b[6:0] == 7'h7F);
  end
endmodule
