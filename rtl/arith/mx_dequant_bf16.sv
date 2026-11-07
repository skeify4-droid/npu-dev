// mx_dequant_bf16: MXFP4 / MXFP8 / NVFP4 element x block scale -> BF16 (DMA `*_BF16`, numerics.md N-2).
//   ELEM / SCALE as mx_exact: MXFP4 = (0, 0), MXFP8 = (1, 0), NVFP4 = (0, 1).
//   Exact for MXFP4 and NVFP4 (at most 6 significant bits) and for MXFP8 down to 2^-130; below that the product is
//   rounded (RNE) onto the BF16 subnormal grid (2^-133), never flushed. Overflow (E8M0 scale 2^127 x 6 > BF16 max) gives
//   +-Inf. An element or scale NaN gives 0x7FC0. Zero keeps its sign (element sign XOR scale sign).
//   The NVFP4 tensor scale is not applied (it becomes a column scale). flags = {nan_in, sat, ovf, invalid}.
module mx_dequant_bf16 #(
  parameter int ELEM  = 0,
  parameter int SCALE = 0
) (
  input  logic [7:0]  el,
  input  logic [7:0]  sc,
  output logic [15:0] y,
  output logic [3:0]  flags
);
  logic              sign, nan, zero, ovf, unused_sat;
  logic [7:0]        sig;
  logic signed [9:0] elsb;
  logic [15:0]       r;

  mx_exact #(.ELEM(ELEM), .SCALE(SCALE)) u_ex (.el(el), .sc(sc), .sign(sign), .nan(nan), .zero(zero), .sig(sig), .elsb(elsb));

  fp_round #(.W(8), .EB(8), .MB(7), .KIND(0), .SAT(1'b0), .FTZ(1'b0), .EW(10)) u_rnd (
    .sign(sign), .mag(sig), .elsb(elsb), .code(r), .ovf(ovf), .sat(unused_sat));

  logic unused;
  assign unused = zero;
  assign y     = nan ? 16'h7FC0 : r;
  assign flags = {nan, 1'b0, ovf & !nan, 1'b0};
endmodule
