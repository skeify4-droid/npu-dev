// cvt_fp32_int: FP32 -> INT8 / UINT8 / INT16 / INT32 / UINT32 (numerics.md N-2). Combinational.
//   RNE to the nearest integer, then saturate to the range; NaN -> 0; +-Inf saturate. Negative inputs to an unsigned
//   type give 0 (flagged as saturation when the rounded value is not zero).
//   y[BITS-1:0] is the two's complement result, upper bits 0.
//   flags = {nan_in, sat, ovf, invalid}: sat = a finite input was clamped.
module cvt_fp32_int #(
  parameter int BITS   = 8,
  parameter bit SIGNED = 1'b1
) (
  input  logic [31:0] x,
  output logic [31:0] y,
  output logic [3:0]  flags
);
  logic        neg, is_nan, is_inf, clamp;
  logic [63:0] m, lim;
  logic [23:0] frac_m;
  int          sh, rs;

  always_comb begin
    neg    = x[31];
    is_nan = (x[30:23] == 8'hFF) && (x[22:0] != 23'd0);
    is_inf = (x[30:23] == 8'hFF) && (x[22:0] == 23'd0);
    frac_m = (x[30:23] == 8'h00) ? {1'b0, x[22:0]} : {1'b1, x[22:0]};
    sh     = (x[30:23] == 8'h00) ? -149 : (int'(x[30:23]) - 150);       // value = frac_m * 2^sh
    m      = '0;
    rs     = 0;
    if (is_inf) m = 64'hFFFF_FFFF_FFFF_FFFF;
    else if (sh >= 0) m = (sh > 36) ? 64'hFFFF_FFFF_FFFF_FFFF : (64'(frac_m) << sh);
    else begin
      rs = -sh;
      if (rs > 24) m = 64'd0;                                   // below one half of the LSB (frac_m < 2^24, so rs - 1 <= 23 below)
      else begin
        m = 64'(frac_m >> rs);
        if (frac_m[rs-1] && ((|(frac_m & ((24'd1 << (rs-1)) - 24'd1))) || m[0])) m = m + 64'd1;
      end
    end
    lim    = '0;
    if (SIGNED) lim = neg ? (64'd1 << (BITS - 1)) : ((64'd1 << (BITS - 1)) - 64'd1);
    else        lim = neg ? 64'd0 : ((64'd1 << BITS) - 64'd1);
    clamp = (m > lim);
    if (clamp) m = lim;
    y = is_nan ? 32'd0 : (neg ? 32'(-m) : 32'(m));
    if (BITS < 32) y = y & ((32'd1 << BITS) - 32'd1);
    flags = {is_nan, clamp & !is_inf & !is_nan, 1'b0, 1'b0};
  end
endmodule
