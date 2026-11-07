// Self-checking testbench for the fold blocks (iverilog -g2012): fold_key -> fold_amax -> fold_c -> fold_quant on
// tiles of 64 elements (fold_*.hex) and of 256 elements (fold256_*.hex, the tile of the spec and the default of fold_amax),
// for MXFP4, MXFP8 and NVFP4 inputs. Rows hold the tile (elements, scale codes) and the expected
// amax key, c, column scale (2^c, FP32 bits), the 64 E4M3 codes and their flags
// (sw/npumodel/arith_ref.py; test_arith.py checks the fold against numerics.fold_e4m3).
// Tiles: random, narrow / 14-binade / 15-binade block-exponent spreads, extreme scale codes, NaN elements and scales,
// all-zero tiles, the hand-derived tiles (64), and in the 256-element files one tile per position with a single non-zero
// element (every leaf of the max tree reaches the root). foldq_*.hex tests fold_quant alone with a column exponent below
// the element's own, which is the only way to reach its SATURATE flag.
`timescale 1ns/1ps
`include "arith/counts.svh"   // CNT_<file>: rows of each vector file (scripts/gen_arith_vectors.py)
module fold_chk #(
  parameter int    ELEM  = 0,
  parameter int    SCALE = 0,
  parameter string FILE  = "",
  parameter int    N     = 64,
  parameter int    MAXN  = 1,               // exact tile count of FILE
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
    if (n != MAXN) begin errors++; $display("ELEM=%0d SCALE=%0d N=%0d: expected %0d tiles, read %0d", ELEM, SCALE, N, MAXN, n); end
  end
endmodule

// fold_quant alone (foldq_*.hex): {el, sc, c, v, flags} with c below, at and above the element's own c, so that the
// SATURATE output fires (inside a tile c comes from the tile's amax and the element never saturates).
module foldq_chk #(
  parameter int    ELEM  = 0,
  parameter int    SCALE = 0,
  parameter string FILE  = "",
  parameter int    MAXN  = 1                // exact row count of FILE
) (
  output int errors, output int n, output int nsat
);
  logic [37:0] mem [0:MAXN-1];
  logic [7:0] el, sc, v;
  logic signed [9:0] c;
  logic [3:0] flags;
  fold_quant #(.ELEM(ELEM), .SCALE(SCALE)) dut (.el(el), .sc(sc), .c(c), .v(v), .flags(flags));
  initial begin
    errors = 0; n = 0; nsat = 0;
    $readmemh(FILE, mem);
    for (int i = 0; i < MAXN; i++) begin
      if (^mem[i] === 1'bx) break;
      el = mem[i][37:30]; sc = mem[i][29:22]; c = mem[i][21:12];
      #1;
      n++;
      if (mem[i][2]) nsat++;
      if (v !== mem[i][11:4] || flags !== mem[i][3:0]) begin
        errors++;
        if (errors <= 5) $display("MISMATCH foldq ELEM=%0d SCALE=%0d el=%h sc=%h c=%0d v=%h flags=%h  exp v=%h flags=%h", ELEM, SCALE, el, sc, c, v, flags, mem[i][11:4], mem[i][3:0]);
      end
    end
    if (n != MAXN) begin errors++; $display("foldq ELEM=%0d SCALE=%0d: expected %0d rows, read %0d", ELEM, SCALE, MAXN, n); end
    if (nsat < 100) begin errors++; $display("foldq ELEM=%0d SCALE=%0d: only %0d rows exercise SATURATE", ELEM, SCALE, nsat); end
  end
endmodule

module tb_fold_quant;
  int e [9], n [9], ns [3];
  fold_chk #(.ELEM(0), .SCALE(0), .FILE("arith/fold_mxfp4.hex"), .MAXN(`CNT_FOLD_MXFP4)) c0 (.errors(e[0]), .n(n[0]));
  fold_chk #(.ELEM(1), .SCALE(0), .FILE("arith/fold_mxfp8.hex"), .MAXN(`CNT_FOLD_MXFP8)) c1 (.errors(e[1]), .n(n[1]));
  fold_chk #(.ELEM(0), .SCALE(1), .FILE("arith/fold_nvfp4.hex"), .MAXN(`CNT_FOLD_NVFP4)) c2 (.errors(e[2]), .n(n[2]));
  fold_chk #(.ELEM(0), .SCALE(0), .FILE("arith/fold256_mxfp4.hex"), .N(256), .MAXN(`CNT_FOLD256_MXFP4)) c3 (.errors(e[3]), .n(n[3]));
  fold_chk #(.ELEM(1), .SCALE(0), .FILE("arith/fold256_mxfp8.hex"), .N(256), .MAXN(`CNT_FOLD256_MXFP8)) c4 (.errors(e[4]), .n(n[4]));
  fold_chk #(.ELEM(0), .SCALE(1), .FILE("arith/fold256_nvfp4.hex"), .N(256), .MAXN(`CNT_FOLD256_NVFP4)) c5 (.errors(e[5]), .n(n[5]));
  foldq_chk #(.ELEM(0), .SCALE(0), .FILE("arith/foldq_mxfp4.hex"), .MAXN(`CNT_FOLDQ_MXFP4)) q0 (.errors(e[6]), .n(n[6]), .nsat(ns[0]));
  foldq_chk #(.ELEM(1), .SCALE(0), .FILE("arith/foldq_mxfp8.hex"), .MAXN(`CNT_FOLDQ_MXFP8)) q1 (.errors(e[7]), .n(n[7]), .nsat(ns[1]));
  foldq_chk #(.ELEM(0), .SCALE(1), .FILE("arith/foldq_nvfp4.hex"), .MAXN(`CNT_FOLDQ_NVFP4)) q2 (.errors(e[8]), .n(n[8]), .nsat(ns[2]));
  initial begin
    #100000000;
    if (e[0] + e[1] + e[2] + e[3] + e[4] + e[5] + e[6] + e[7] + e[8] == 0)
      $display("PASS fold_quant (fold_key, fold_amax, fold_c, fold_quant: tiles of 64: %0d MXFP4, %0d MXFP8, %0d NVFP4; tiles of 256: %0d, %0d, %0d; fold_quant alone: %0d, %0d, %0d rows, %0d, %0d, %0d saturating)",
               n[0], n[1], n[2], n[3], n[4], n[5], n[6], n[7], n[8], ns[0], ns[1], ns[2]);
    else
      $display("FAIL fold_quant: errors %0d %0d %0d %0d %0d %0d %0d %0d %0d, tiles %0d %0d %0d %0d %0d %0d, rows %0d %0d %0d", e[0], e[1], e[2], e[3], e[4], e[5], e[6], e[7], e[8],
               n[0], n[1], n[2], n[3], n[4], n[5], n[6], n[7], n[8]);
    $finish;
  end
endmodule
