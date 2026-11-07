// cvt_dec: exact decode of a narrow format to FP32 or BF16 (numerics.md N-2, "exact" rows). Combinational.
//   FMT 0 BF16, 1 FP16, 2 E4M3, 3 E5M2, 4 E2M1 (input x[WI-1:0], upper bits ignored).
//   TO_BF16 = 0: y = FP32. TO_BF16 = 1: y[15:0] = BF16 (y[31:16] = 0), RNE; exact for E4M3, E5M2, E2M1 and BF16;
//   FP16 -> BF16 rounds (an FP16 significand has 11 bits; the table row in N-2 says "exact" for FP16 -> BF16, which only
//   holds for FP16 -> FP32).
//   Subnormals of the source are normalized (and kept: BF16 subnormals decode to FP32 subnormals). E5M2 / FP16 / BF16 Inf
//   stays Inf; NaN becomes the canonical NaN of the destination (0x7FC00000 / 0x7FC0).
//   flags = {nan_in, sat, ovf, invalid}: only nan_in (a NaN was decoded).
module cvt_dec #(
  parameter int FMT     = 2,
  parameter bit TO_BF16 = 1'b0
) (
  input  logic [15:0] x,
  output logic [31:0] y,
  output logic [3:0]  flags
);
  localparam int EB   = (FMT == 0) ? 8 : (FMT == 1) ? 5 : (FMT == 2) ? 4 : (FMT == 3) ? 5 : 2;
  localparam int MB   = (FMT == 0) ? 7 : (FMT == 1) ? 10 : (FMT == 2) ? 3 : (FMT == 3) ? 2 : 1;
  localparam int KIND = (FMT == 2) ? 1 : (FMT == 4) ? 2 : 0;
  localparam int BIAS = (1 << (EB - 1)) - 1;
  localparam int WI   = 1 + EB + MB;
  localparam int OB   = TO_BF16 ? 7 : 23;                      // destination mantissa bits

  logic              s, is_nan, is_inf;
  logic [EB-1:0]     ef;
  logic [MB-1:0]     mf;
  logic [MB:0]       mag;
  logic signed [11:0] elsb;
  logic [8+OB:0]     r;
  logic              unused_ovf, unused_sat;

  always_comb begin
    s  = x[WI-1];
    ef = x[WI-2 -: EB];
    mf = x[MB-1:0];
    is_nan = 1'b0;
    is_inf = 1'b0;
    if (KIND == 0) begin
      is_nan = (ef == '1) && (mf != '0);
      is_inf = (ef == '1) && (mf == '0);
    end else if (KIND == 1) begin
      is_nan = (ef == '1) && (mf == '1);
    end
    mag  = {(ef != '0), mf};
    elsb = 12'((ef == '0) ? 1 : int'(ef)) - 12'(BIAS + MB);
  end

  fp_round #(.W(MB + 1), .EB(8), .MB(OB), .KIND(0), .SAT(1'b0), .FTZ(1'b0), .EW(12)) u_rnd (
    .sign(s), .mag(mag), .elsb(elsb), .code(r), .ovf(unused_ovf), .sat(unused_sat));

  always_comb begin
    if (is_nan)      y = TO_BF16 ? 32'h00007FC0 : 32'h7FC00000;
    else if (is_inf) y = TO_BF16 ? {16'd0, s, 8'hFF, 7'd0} : {s, 8'hFF, 23'd0};
    else             y = 32'(r);
    flags = {is_nan, 3'b000};
  end
endmodule
