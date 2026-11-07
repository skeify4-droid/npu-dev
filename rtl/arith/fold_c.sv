// fold_c: the fold exponent c from a tile's amax key (numerics.md N-2): the smallest integer with amax <= 448 * 2^c,
// c = 0 for an all-zero tile. amax = 1.f * 2^e: c = e - 8 when 1.f <= 1.75 (448 = 1.75 * 2^8), else e - 7.
// Also gives the column scale 2^c as FP32 bits (`cs`, exact; c below -149 gives 0, c below -126 is subnormal; the
// ACC multiplier flushes such a scale, which needs amax < 2^-118 and does not occur for real weights).
module fold_c (
  input  logic [16:0]        key,
  output logic signed [9:0]  c,
  output logic [31:0]        cs
);
  logic signed [9:0] e;
  logic              unused_ovf, unused_sat;
  logic [31:0]       r;

  always_comb begin
    e = 10'(signed'({1'b0, key[16:8]})) - 10'sd256;
    if (key == 17'd0)        c = 10'sd0;
    else if (key[7:0] > 8'hE0) c = e - 10'sd7;
    else                     c = e - 10'sd8;
  end

  fp_round #(.W(1), .EB(8), .MB(23), .KIND(0), .SAT(1'b0), .FTZ(1'b0), .EW(10)) u_cs (
    .sign(1'b0), .mag(1'b1), .elsb(c), .code(r), .ovf(unused_ovf), .sat(unused_sat));
  assign cs = r;
endmodule
