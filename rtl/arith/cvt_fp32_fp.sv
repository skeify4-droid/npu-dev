// cvt_fp32_fp: FP32 -> BF16 / FP16 / E4M3 / E5M2 / E2M1 (numerics.md N-2). Combinational.
//   FMT 0 BF16, 1 FP16, 2 E4M3, 3 E5M2, 4 E2M1. The result is y[WO-1:0] = {sign, exp, mantissa}; the upper bits are 0.
//   - RNE. E4M3, E5M2, E2M1 saturate (|x| above the largest finite value after rounding and +-Inf give +-max);
//     BF16 and FP16 overflow to +-Inf.
//   - Subnormal results are kept for every target (N-2, N-5): the value is rounded onto the destination's subnormal grid
//     (BF16 2^-133, FP16 2^-24, ...), and an FP32 subnormal input is converted, not read as zero (DAZ applies to
//     arithmetic units only). A BF16 result that rounds up to 2^-126 is the minimum normal.
//   - NaN in -> the canonical NaN of the target (BF16 0x7FC0, FP16 0x7E00, E4M3 0x7F, E5M2 0x7E). E2M1 has no NaN:
//     y = 0 and `nan_in` tells the block-quantizer (N-2: FP4 -> block NaN scale).
//   - flags = {nan_in, sat, ovf, invalid}: sat = a finite input saturated, ovf = a finite input overflowed to Inf.
module cvt_fp32_fp #(
  parameter int FMT = 0
) (
  input  logic [31:0] x,
  output logic [15:0] y,
  output logic [3:0]  flags
);
  localparam int EB   = (FMT == 0) ? 8 : (FMT == 1) ? 5 : (FMT == 2) ? 4 : (FMT == 3) ? 5 : 2;
  localparam int MB   = (FMT == 0) ? 7 : (FMT == 1) ? 10 : (FMT == 2) ? 3 : (FMT == 3) ? 2 : 1;
  localparam int KIND = (FMT == 2) ? 1 : (FMT == 4) ? 2 : 0;
  localparam bit SAT  = (FMT >= 2);
  localparam bit FTZ  = 1'b0;                   // conversions keep subnormals
  localparam int WO   = 1 + EB + MB;
  localparam logic [15:0] NANC = (FMT == 0) ? 16'h7FC0 : (FMT == 1) ? 16'h7E00 : (FMT == 2) ? 16'h007F :
                                 (FMT == 3) ? 16'h007E : 16'h0000;

  logic        is_nan, is_inf, ovf, sat;
  logic [23:0] mag;
  logic signed [11:0] elsb;
  logic [WO-1:0] r;

  always_comb begin
    is_nan = (x[30:23] == 8'hFF) && (x[22:0] != 23'd0);
    is_inf = (x[30:23] == 8'hFF) && (x[22:0] == 23'd0);
    if (is_inf) begin
      mag  = 24'd1;
      elsb = 12'sd400;                         // far above any format: overflow / saturate by the format's rule
    end else if (x[30:23] == 8'h00) begin
      mag  = {1'b0, x[22:0]};
      elsb = -12'sd149;
    end else begin
      mag  = {1'b1, x[22:0]};
      elsb = 12'(signed'({1'b0, x[30:23]})) - 12'sd150;
    end
  end

  fp_round #(.W(24), .EB(EB), .MB(MB), .KIND(KIND), .SAT(SAT), .FTZ(FTZ), .EW(12)) u_rnd (
    .sign(x[31]), .mag(mag), .elsb(elsb), .code(r), .ovf(ovf), .sat(sat));

  always_comb begin
    y = 16'(r);
    if (is_nan) y = NANC;
    flags = {is_nan, sat & !is_inf & !is_nan, ovf & !is_inf & !is_nan, 1'b0};
  end
endmodule
