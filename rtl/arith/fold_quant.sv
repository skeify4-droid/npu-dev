// fold_quant: write one element of a fold tile (numerics.md N-2, "Fold" step 3): v = RNE_E4M3(element x scale x 2^-c)
// with saturation (which does not trigger when c comes from the tile's own amax). ELEM / SCALE as mx_exact.
// NaN element or scale -> E4M3 NaN 0x7F (and `nan_in`). Combinational.
// flags = {nan_in, sat, ovf, invalid}.
module fold_quant #(
  parameter int ELEM  = 0,
  parameter int SCALE = 0
) (
  input  logic [7:0]        el,
  input  logic [7:0]        sc,
  input  logic signed [9:0] c,
  output logic [7:0]        v,
  output logic [3:0]        flags
);
  logic              sign, nan, zero, sat, unused_ovf;
  logic [7:0]        sig, r;
  logic signed [9:0] elsb;
  logic signed [11:0] e2;

  mx_exact #(.ELEM(ELEM), .SCALE(SCALE)) u_ex (.el(el), .sc(sc), .sign(sign), .nan(nan), .zero(zero), .sig(sig), .elsb(elsb));

  assign e2 = 12'(elsb) - 12'(c);

  fp_round #(.W(8), .EB(4), .MB(3), .KIND(1), .SAT(1'b1), .FTZ(1'b0), .EW(12)) u_rnd (
    .sign(sign), .mag(sig), .elsb(e2), .code(r), .ovf(unused_ovf), .sat(sat));

  logic unused;
  assign unused = zero;
  assign v     = nan ? 8'h7F : r;
  assign flags = {nan, sat & !nan, 2'b00};
endmodule
