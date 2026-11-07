// fp_round: round an exact unsigned magnitude `mag * 2^elsb` to a binary floating-point format.
// The one rounding block of `arith`: FP32 adder / multiplier / dot-product rounding, every FP32 -> narrow-format
// conversion, the exact format -> FP32 / BF16 decodes, MX dequantization and the fold all instantiate it
// (numerics.md N-1, N-2). Purely combinational.
//
// Rules
//   - RNE (ties to even), done once on the exact input.
//   - Results below the format's smallest normal land on the subnormal grid first (round, then look at the result).
//     With FTZ = 1 a result that is still subnormal after rounding becomes a signed zero; one that rounds up to the
//     smallest normal is kept (the golden model's `round_to(..., ftz=True)`).
//   - Overflow (rounded magnitude above the largest finite value; also an input too large for the code space):
//       SAT = 1  -> largest finite code, `sat` = 1 (PTX .satfinite);
//       SAT = 0, KIND 0 -> Inf, `ovf` = 1;   SAT = 0, KIND 1/2 -> the all-ones code (NaN for KIND 1), `ovf` = 1.
//   - mag = 0 gives a signed zero (`sign` is passed through). NaN / Inf inputs are the wrapper's job.
//
// Parameters
//   W     width of mag
//   EB    exponent bits, MB mantissa bits of the target; the code is {sign, exp, mantissa}
//   KIND  0 IEEE-like (all-ones exponent is Inf / NaN: BF16, FP16, FP32, E5M2)
//         1 FN (E4M3: no Inf, only S.1111.111 is NaN)
//         2 NONE (E2M1: no Inf, no NaN)
//   EW    width of the signed exponent elsb (the exponent of mag's LSB)
//   SUBN_MAX  largest subnormal shift implemented (the number of binades below the smallest normal that are rounded
//         on the subnormal grid). The default is the full range. With FTZ = 1 a smaller value is exact: a result more
//         than SUBN_MAX binades below the smallest normal is below it after rounding too and becomes a signed zero, so
//         SUBN_MAX = 1 (only [2^-127, 2^-126) can round up to 2^-126) replaces the barrel shifter by one mux, and
//         0 is right where results below 2^-126 are exact (sums of normal FP32 values). Requires FTZ = 1 below the
//         default.
module fp_round #(
  parameter int W    = 24,
  parameter int EB   = 8,
  parameter int MB   = 23,
  parameter int KIND = 0,
  parameter bit SAT  = 1'b0,
  parameter bit FTZ  = 1'b0,
  parameter int EW   = 12,
  parameter int SUBN_MAX = 1000
) (
  input  logic                 sign,
  input  logic [W-1:0]         mag,
  input  logic signed [EW-1:0] elsb,
  output logic [EB+MB:0]       code,
  output logic                 ovf,
  output logic                 sat
);
  localparam int K    = MB + 1;                             // kept bits (hidden + mantissa)
  localparam int WP   = ((W > K) ? W : K) + 2;              // padded width: room for guard and sticky
  localparam int LZW  = $clog2(WP + 1);
  localparam int BIAS = (1 << (EB - 1)) - 1;
  localparam int CW   = EB + MB + 2;
  localparam int EMIN = 1 - BIAS;
  localparam int MAXC = (KIND == 0) ? ((((1 << EB) - 2) << MB) | ((1 << MB) - 1)) :
                        (KIND == 1) ? ((1 << (EB + MB)) - 2) : ((1 << (EB + MB)) - 1);
  localparam int INFC = ((1 << EB) - 1) << MB;
  localparam int ONES = (1 << (EB + MB)) - 1;

  logic [WP-1:0]  magp, norm;
  logic [LZW-1:0] lz;
  logic [K+1:0]   r0, r1;
  logic [K-1:0]   kept;
  logic           g, st, inc;
  logic [CW-1:0]  code_w;
  logic [EB+MB-1:0] mag_out;
  logic           over;
  int             e_msb, sh, base;
  logic           uflow;
  localparam int  SMAX = (SUBN_MAX < K + 2) ? SUBN_MAX : (K + 2);

  if (SMAX < K + 2 && !FTZ) begin : g_chk
    $error("fp_round: SUBN_MAX below the full range needs FTZ = 1");
  end

  always_comb begin
    magp = '0;
    magp[WP-1 -: W] = mag;
    lz = LZW'(WP);
    for (int i = 0; i < WP; i++) if (magp[i]) lz = LZW'(WP - 1 - i);
    norm = magp << lz;

    e_msb = int'(elsb) + (W - 1) - int'(lz);
    sh    = (e_msb < EMIN) ? (EMIN - e_msb) : 0;
    base  = (sh > 0) ? 0 : (e_msb + BIAS - 1);
    if (base > (1 << EB)) base = 1 << EB;

    r0 = {norm[WP-1 -: K], norm[WP-1-K], |norm[WP-2-K:0]};
    uflow = (sh > SMAX);
    if (SMAX == 0) r1 = r0;
    else if (SMAX == 1) r1 = (sh == 1) ? {1'b0, r0[K+1:2], r0[1] | r0[0]} : r0;
    else if (sh >= K + 2) r1 = {{(K+1){1'b0}}, |r0};
    else begin
      r1    = r0 >> sh;
      r1[0] = r1[0] | (|(r0 & ((((K+2)'(1)) << sh) - (K+2)'(1))));
    end
    kept = r1[K+1:2];
    g    = r1[1];
    st   = r1[0];
    inc  = g & (st | kept[0]);

    code_w = (CW'(base) << MB) + CW'(kept) + CW'(inc);
  end

  always_comb begin
    over    = (code_w > CW'(MAXC));
    mag_out = code_w[EB+MB-1:0];
    ovf     = 1'b0;
    sat     = 1'b0;
    if (mag == '0) mag_out = '0;
    else if (uflow) mag_out = '0;
    else if (over) begin
      if (SAT)               begin mag_out = (EB+MB)'(MAXC); sat = 1'b1; end
      else if (KIND == 0)    begin mag_out = (EB+MB)'(INFC); ovf = 1'b1; end
      else                   begin mag_out = (EB+MB)'(ONES); ovf = 1'b1; end
    end else if (FTZ && (code_w < CW'(1 << MB))) mag_out = '0;
  end

  assign code = {sign, mag_out};
endmodule
