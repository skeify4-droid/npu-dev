// fold_key: the amax key of one element for the fold (numerics.md N-2, "Fold"). Combinational.
// key[16:0] = {e_msb + 256, normalized significand[7:0]} of |element x block scale| (exact, tensor scale excluded),
// so an integer compare of keys is a compare of magnitudes. Zero and NaN elements give key 0 (NaNs do not take part in
// amax; fold_quant writes them as E4M3 NaN). e_msb is the exponent of the leading bit, in [-136, 135].
module fold_key #(
  parameter int ELEM  = 0,
  parameter int SCALE = 0
) (
  input  logic [7:0]  el,
  input  logic [7:0]  sc,
  output logic [16:0] key
);
  logic              sign, nan, zero;
  logic [7:0]        sig, norm;
  logic signed [9:0] elsb;
  logic [3:0]        lz;
  logic signed [9:0] e_msb;
  logic              unused;

  mx_exact #(.ELEM(ELEM), .SCALE(SCALE)) u_ex (.el(el), .sc(sc), .sign(sign), .nan(nan), .zero(zero), .sig(sig), .elsb(elsb));

  always_comb begin
    lz = 4'd8;
    for (int i = 0; i < 8; i++) if (sig[i]) lz = 4'(7 - i);
    norm  = sig << lz;
    e_msb = elsb + 10'sd7 - 10'(signed'({6'b0, lz}));
    key   = (sig == 8'd0) ? 17'd0 : {9'(e_msb + 10'sd256), norm};
  end
  assign unused = sign ^ nan ^ zero;
endmodule
