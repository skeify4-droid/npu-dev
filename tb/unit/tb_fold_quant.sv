// Self-checking testbench for the fold blocks (iverilog -g2012): fold_key -> fold_amax -> fold_c -> fold_quant on
// tiles of 64 elements, for MXFP4, MXFP8 and NVFP4 inputs. Rows hold the tile (elements, scale codes) and the expected
// amax key, c, column scale (2^c, FP32 bits), the 64 E4M3 codes and their flags
// (sw/npumodel/arith_ref.py; test_arith.py checks the fold against numerics.fold_e4m3).
// Tiles: random, narrow / 14-binade / 15-binade block-exponent spreads, extreme scale codes, NaN elements and scales,
// all-zero tiles.
`timescale 1ns/1ps
module fold_chk #(
  parameter int    ELEM  = 0,
  parameter int    SCALE = 0,
  parameter string FILE  = "",
  parameter int    N     = 64,
  parameter int    MAXN  = 2000,
  localparam int   RW    = 2*8*N + 17 + 10 + 32 + 8*N + 4*N
) (
  output int errors, output int n
);
  logic [RW-1:0] mem [0:MAXN-1];
  logic [N*8-1:0]  els, scs, vs, v_exp;
  logic [N*4-1:0]  fls, fl_exp;
  logic [N*17-1:0] keys;
  logic [16:0] amax;
  logic signed [9:0] c;
  logic [31:0] cs;

  for (genvar i = 0; i < N; i++) begin : g
    fold_key #(.ELEM(ELEM), .SCALE(SCALE)) u_k (.el(els[8*i +: 8]), .sc(scs[8*i +: 8]), .key(keys[17*i +: 17]));
    fold_quant #(.ELEM(ELEM), .SCALE(SCALE)) u_q (.el(els[8*i +: 8]), .sc(scs[8*i +: 8]), .c(c), .v(vs[8*i +: 8]), .flags(fls[4*i +: 4]));
  end
  fold_amax #(.N(N), .KW(17)) u_max (.keys(keys), .amax(amax));
  fold_c u_c (.key(amax), .c(c), .cs(cs));

  initial begin
    errors = 0; n = 0;
    $readmemh(FILE, mem);
    for (int i = 0; i < MAXN; i++) begin
      if (^mem[i] === 1'bx) break;
      els = mem[i][RW-1 -: 8*N];
      scs = mem[i][RW-1-8*N -: 8*N];
      v_exp  = mem[i][4*N + 8*N - 1 : 4*N];
      fl_exp = mem[i][4*N-1:0];
      #1;
      n++;
      if (amax !== mem[i][4*N + 8*N + 32 + 10 + 17 - 1 -: 17] || c !== mem[i][4*N + 8*N + 32 + 10 - 1 -: 10] ||
          cs !== mem[i][4*N + 8*N + 32 - 1 -: 32] || vs !== v_exp || fls !== fl_exp) begin
        errors++;
        if (errors <= 5) $display("MISMATCH ELEM=%0d SCALE=%0d tile %0d: amax=%h c=%0d cs=%h  exp amax=%h c=%0d cs=%h  vs_ok=%b fl_ok=%b", ELEM, SCALE, i,
                                  amax, c, cs, mem[i][4*N + 8*N + 32 + 10 + 17 - 1 -: 17], $signed(mem[i][4*N + 8*N + 32 + 10 - 1 -: 10]),
                                  mem[i][4*N + 8*N + 32 - 1 -: 32], vs === v_exp, fls === fl_exp);
      end
    end
  end
endmodule

module tb_fold_quant;
  int e [3], n [3];
  fold_chk #(.ELEM(0), .SCALE(0), .FILE("arith/fold_mxfp4.hex")) c0 (.errors(e[0]), .n(n[0]));
  fold_chk #(.ELEM(1), .SCALE(0), .FILE("arith/fold_mxfp8.hex")) c1 (.errors(e[1]), .n(n[1]));
  fold_chk #(.ELEM(0), .SCALE(1), .FILE("arith/fold_nvfp4.hex")) c2 (.errors(e[2]), .n(n[2]));
  initial begin
    #100000000;
    if (e[0] + e[1] + e[2] == 0 && n[0] == 1500 && n[1] == 1500 && n[2] == 1500)
      $display("PASS fold_quant (fold_key, fold_amax, fold_c, fold_quant: 1500 tiles each of MXFP4, MXFP8, NVFP4)");
    else
      $display("FAIL fold_quant: errors %0d %0d %0d, tiles %0d %0d %0d", e[0], e[1], e[2], n[0], n[1], n[2]);
    $finish;
  end
endmodule
