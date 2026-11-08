// Self-checking testbench for mxu_array (iverilog -g2012): 8 x 8 and 16 x 16 arrays driven by the cycle programs of
// build/mxu/mxu_array_<dim>.hex (scripts/gen_mxu_vectors.py; the expected values come from sw/npumodel/arith_ref.py).
// One line per clock: expected {flags, y_valid, y_meta, y}, then the inputs {a_meta, a_ncols, a_mode, a_valid, swap,
// wl_idx, wl_grp, wl_trans, wl_en, wl_data, a_words}. Inputs change 1 ns after the rising edge, outputs are compared
// 1 ns before the next one: y_valid, flags every cycle, y and y_meta when y_valid is expected.
// Covers: back-to-back tiles with the swap skewed down the rows, shadow writes in the first allowed cycle (the tap cycle of the
// previous swap), trans / non-trans windows, bubbles in the A stream, FP8 <-> BF16 on consecutive waves, column mask on
// flags, NaN / Inf / overflow operands, reset (all valid bits and flag registers clear, nothing is reported after release).
`timescale 1ns/1ps
`include "counts.svh"
module mxu_array_tc #(
  parameter int DIM = 8,
  parameter int N   = 1,
  parameter string FILE = ""
) (
  output logic done,
  output int   errors
);
  localparam int META_W = 16;
  localparam int LW = 4 + 1 + META_W + DIM*32 + META_W + 8 + 1 + 1 + 1 + 1 + 8 + 8 + 1 + 1 + 1 + 128*32 + DIM*32;
  localparam int NG = 4;
  localparam int GRP = DIM / 4;
  localparam int GW = (NG > 1) ? $clog2(NG) : 1;
  localparam int NIDX = (DIM / 8 > GRP / 2) ? DIM / 8 : GRP / 2;
  localparam int IDXW = (NIDX > 1) ? $clog2(NIDX) : 1;
  localparam int NCW = $clog2(DIM + 1);

  logic [LW-1:0] mem [0:N-1];
  logic clk = 0, rst_n = 0;
  logic wl_en = 0, wl_trans = 0, wl_buf = 0, swap = 0, swap_buf = 0, a_valid = 0, a_mode = 0;
  logic [GW-1:0] wl_grp = '0;
  logic [IDXW-1:0] wl_idx = '0;
  logic [128*32-1:0] wl_data = '0;
  logic [NCW-1:0] a_ncols = '0;
  logic [META_W-1:0] a_meta = '0;
  logic [DIM*32-1:0] a_words = '0;
  logic y_valid;
  logic [DIM*32-1:0] y;
  logic [META_W-1:0] y_meta;
  logic [3:0] flags;
  logic [2*NG-1:0] swap_tap;

  mxu_array #(.ROWS(DIM), .COLS(DIM), .META_W(META_W)) dut (
    .clk(clk), .rst_n(rst_n), .wl_en(wl_en), .wl_buf(wl_buf), .wl_trans(wl_trans), .wl_grp(wl_grp), .wl_idx(wl_idx), .wl_data(wl_data),
    .swap(swap), .swap_buf(swap_buf), .swap_tap(swap_tap), .a_valid(a_valid), .a_mode_bf16(a_mode), .a_ncols(a_ncols), .a_meta(a_meta),
    .a_words(a_words), .y_valid(y_valid), .y(y), .y_meta(y_meta), .flags(flags));

  always #5 clk = ~clk;

  // swap_tap must pulse once per swap and group, group g at cycle swap + g*GRP + GRP - 1
  int taps = 0, swaps_seen = 0;
  logic [DIM-1:0] sw_d0, sw_d1;     // swap from buffer 0 / 1 delayed by k + 1 cycles (reset with the array)
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin sw_d0 <= '0; sw_d1 <= '0; end
    else begin sw_d0 <= {sw_d0[DIM-2:0], swap && !swap_buf}; sw_d1 <= {sw_d1[DIM-2:0], swap && swap_buf}; end
  end
  always @(posedge clk) begin
    if (swap && rst_n) swaps_seen++;
    for (int g = 0; g < 2*NG; g++) if (swap_tap[g]) taps++;
  end
  // tap timing: group g of buffer b pulses exactly g*GRP + GRP - 1 cycles after a swap from b, every cycle checked
  always @(negedge clk) begin
    for (int g = 0; g < NG; g++) begin
      if (swap_tap[g] !== sw_d0[g*GRP + GRP - 2]) begin
        errors++; if (errors < 10) $display("MISMATCH %s: swap_tap[0][%0d] = %b, expected %b", FILE, g, swap_tap[g], sw_d0[g*GRP + GRP - 2]);
      end
      if (swap_tap[NG + g] !== sw_d1[g*GRP + GRP - 2]) begin
        errors++; if (errors < 10) $display("MISMATCH %s: swap_tap[1][%0d] = %b, expected %b", FILE, g, swap_tap[NG + g], sw_d1[g*GRP + GRP - 2]);
      end
    end
  end

  logic [LW-1:0] ln;
  logic [3:0] e_flags;
  logic e_yv;
  logic [META_W-1:0] e_meta;
  logic [DIM*32-1:0] e_y;
  int rows_checked = 0;
  logic [7:0] l_nc, l_idx, l_grp;

  initial begin
    done = 0; errors = 0;
    $readmemh(FILE, mem);
    // reset: valid chain and PE flag registers clear at once; rows applied during reset must not show up afterwards
    rst_n = 0;
    a_valid = 1; a_mode = 1; a_ncols = NCW'(DIM); a_words = {DIM*32{1'b1}}; swap = 1;
    repeat (3) @(posedge clk);
    if (swap_tap !== '0) begin errors++; $display("MISMATCH %s: swap_tap in reset: %b", FILE, swap_tap); end
    #1;
    if (y_valid !== 1'b0 || flags !== 4'd0) begin errors++; $display("MISMATCH %s: y_valid/flags in reset: %b %h", FILE, y_valid, flags); end
    a_valid = 0; a_words = '0; swap = 0;
    @(posedge clk); #1;
    rst_n = 1;
    for (int k = 0; k < N; k++) begin
      ln = mem[k];
      // unpack, LSB first
      {e_flags, e_yv, e_meta, e_y, a_meta, l_nc, a_mode, a_valid, swap, swap_buf, l_idx, l_grp, wl_trans, wl_buf, wl_en, wl_data, a_words} = ln;
      a_ncols = l_nc[NCW-1:0]; wl_idx = l_idx[IDXW-1:0]; wl_grp = l_grp[GW-1:0];
      @(negedge clk);   // 5 ns after rising edge: compare the outputs of this cycle just before the next rising edge
      #4;
      if (y_valid !== e_yv) begin errors++; if (errors < 10) $display("MISMATCH %s cycle %0d: y_valid %b exp %b", FILE, k, y_valid, e_yv); end
      if (flags !== e_flags) begin errors++; if (errors < 10) $display("MISMATCH %s cycle %0d: flags %h exp %h", FILE, k, flags, e_flags); end
      if (e_yv) begin
        rows_checked++;
        if (y !== e_y || y_meta !== e_meta) begin
          errors++;
          if (errors < 10) $display("MISMATCH %s cycle %0d: meta %0d/%0d y %h exp %h", FILE, k, y_meta, e_meta, y, e_y);
        end
      end
      @(posedge clk); #1;
    end
    if (rows_checked == 0) begin errors++; $display("MISMATCH %s: no rows checked", FILE); end
    if (taps != swaps_seen * NG) begin errors++; $display("MISMATCH %s: swap_tap pulses %0d, expected %0d", FILE, taps, swaps_seen * NG); end
    done = 1;
  end
endmodule

module tb_mxu_array;
  logic d8, d16;
  int e8, e16;
  mxu_array_tc #(.DIM(8),  .N(`CNT_MXU_ARRAY_8),  .FILE("mxu_array_8.hex"))  t8  (.done(d8),  .errors(e8));
  mxu_array_tc #(.DIM(16), .N(`CNT_MXU_ARRAY_16), .FILE("mxu_array_16.hex")) t16 (.done(d16), .errors(e16));
  initial begin
    wait (d8 && d16);
    if (e8 + e16 == 0) $display("PASS mxu_array");
    else $display("FAIL mxu_array: %0d errors (8x8: %0d, 16x16: %0d)", e8 + e16, e8, e16);
    $finish;
  end
endmodule
