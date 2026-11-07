// mx_exact: exact value of a block-scaled element, element x block scale (numerics.md N-1, N-2).
//   ELEM  0: E2M1 (el[3:0])            1: E4M3 (el[7:0])
//   SCALE 0: E8M0 (2^(sc-127), 0xFF = NaN)   1: E4M3 (NVFP4 block scale; its sign bit is honoured, 0x7F / 0xFF = NaN)
// value = (-1)^sign * sig * 2^elsb, sig up to 8 bits (4 x 4), not normalized. zero: the value is zero (sig = 0).
// nan: the element or the scale is NaN (then sig = 0). The NVFP4 tensor scale is not applied here (N-2).
// Combinational.
module mx_exact #(
  parameter int ELEM  = 0,
  parameter int SCALE = 0
) (
  input  logic [7:0]         el,
  input  logic [7:0]         sc,
  output logic               sign,
  output logic               nan,
  output logic               zero,
  output logic [7:0]         sig,
  output logic signed [9:0]  elsb
);
  logic [3:0]        se, ss;
  logic signed [9:0] ee, es;
  logic              e_nan, s_nan, e_z, s_z, e_s, s_s;

  always_comb begin
    if (ELEM == 0) begin
      e_s   = el[3];
      se    = (el[2:1] == 2'd0) ? {3'b000, el[0]} : {2'b00, 1'b1, el[0]};
      ee    = 10'((el[2:1] == 2'd0) ? 1 : int'(el[2:1])) - 10'sd2;
      e_nan = 1'b0;
      e_z   = (el[2:0] == 3'd0);
    end else begin
      e_s   = el[7];
      se    = {(el[6:3] != 4'd0), el[2:0]};
      ee    = 10'((el[6:3] == 4'd0) ? 1 : int'(el[6:3])) - 10'sd10;
      e_nan = (el[6:0] == 7'h7F);
      e_z   = (el[6:0] == 7'd0);
    end
    if (SCALE == 0) begin
      s_s   = 1'b0;
      ss    = 4'd1;
      es    = 10'(int'(sc) - 127);
      s_nan = (sc == 8'hFF);
      s_z   = 1'b0;
    end else begin
      s_s   = sc[7];
      ss    = {(sc[6:3] != 4'd0), sc[2:0]};
      es    = 10'((sc[6:3] == 4'd0) ? 1 : int'(sc[6:3])) - 10'sd10;
      s_nan = (sc[6:0] == 7'h7F);
      s_z   = (sc[6:0] == 7'd0);
    end
    sign = e_s ^ s_s;
    nan  = e_nan | s_nan;
    zero = e_z | s_z;
    sig  = (nan | zero) ? 8'd0 : (se * ss);
    elsb = ee + es;
  end
endmodule
