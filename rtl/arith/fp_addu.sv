// fp_addu: exact-then-round-ready addition of two unpacked values (the aligner of fp32_add and the BF16 PE's product
// sum). value = (-1)^s * m * 2^(e - (SW-1)), m normalized (MSB set) or the operand is flagged zero (m = 0).
//
// Output: sign `s`, magnitude `mag` and `elsb` (the exponent of mag's LSB) of the sum, ready for fp_round. The sum is
// exact except for a sticky bit jammed into mag[0] when the smaller operand is shifted past G guard bits. Rounding at
// 24 bits stays correct for the sticky bit because, for an exponent difference of 2 or more, at most one bit
// cancels, so the result's MSB is at bit SW + G - 2 or above, its 24-bit rounding LSB at SW + G - 25 or above and the
// guard bit at SW + G - 26 or above; the jammed bit (bit 0) must lie below the guard bit: SW + G >= 27. A difference
// of 0 or 1 is exact. Callers use one bit more (fp32_add: SW 24, G 4; bf16_dot2: SW 16, G 12). The default G = 26 is
// the generous setting; the tests fail at SW + G = 26 (G = 2 / G = 10) and with the jam removed at SW + G = 27.
// Signed zero: both operands zero -> negative only if both are; exact cancellation -> +0 (RNE).
module fp_addu #(
  parameter int SW = 24,
  parameter int G  = 26,
  parameter int EW = 12,
  localparam int WS = SW + G + 1
) (
  input  logic                 a_s,
  input  logic                 a_z,
  input  logic signed [EW-1:0] a_e,
  input  logic [SW-1:0]        a_m,
  input  logic                 b_s,
  input  logic                 b_z,
  input  logic signed [EW-1:0] b_e,
  input  logic [SW-1:0]        b_m,
  output logic                 s,
  output logic [WS-1:0]        mag,
  output logic signed [EW-1:0] elsb
);
  logic                 swap, big_s, small_s, big_z, small_z, lost;
  logic signed [EW-1:0] big_e, small_e;
  logic [SW-1:0]        big_m, small_m;
  logic [WS-1:0]        bx, sx0, sx, sum;
  int                   d;

  always_comb begin
    if (a_z)      swap = 1'b1;
    else if (b_z) swap = 1'b0;
    else          swap = (a_e < b_e) || ((a_e == b_e) && (a_m < b_m));
    big_s   = swap ? b_s : a_s;
    big_z   = swap ? b_z : a_z;
    big_e   = swap ? b_e : a_e;
    big_m   = swap ? b_m : a_m;
    small_s = swap ? a_s : b_s;
    small_z = swap ? a_z : b_z;
    small_e = swap ? a_e : b_e;
    small_m = swap ? a_m : b_m;

    d   = int'(big_e) - int'(small_e);
    bx  = big_z ? '0 : {1'b0, big_m, {G{1'b0}}};
    sx0 = {1'b0, small_m, {G{1'b0}}};
    lost = 1'b0;
    if (small_z) sx = '0;
    else if (d >= WS) sx = WS'(1);
    else begin
      sx   = sx0 >> d;
      lost = |(sx0 & ((WS'(1) << d) - WS'(1)));
      sx[0] = sx[0] | lost;
    end

    sum  = (big_s ^ small_s) ? (bx - sx) : (bx + sx);
    mag  = sum;
    s    = (sum == '0) ? (a_s & b_s & a_z & b_z) : big_s;
    elsb = big_e - EW'(SW - 1) - EW'(G);
  end
endmodule
