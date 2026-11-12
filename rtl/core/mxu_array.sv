// mxu_array: the weight-stationary PE array of the MXU (microarch.md 2.2, numerics.md N-3, docs/spec/mxu.md).
//
// ROWS x COLS `mxu_pe` (REG_DOT = REG_OUT = 1). PE row i holds K elements 4i..4i+3 (FP8) or 2i, 2i+1 (BF16) of the
// stationary tile as one 32-bit word per PE; PE column j is output column j. One A row (ROWS words) enters per cycle;
// its word i is delayed i cycles (the A skew), is broadcast over the COLS PEs of row i, and meets the partial sum of
// the PE above one cycle later. The partial sum leaves the bottom row as one FP32 per column.
//
// Timing (T = cycle of `a_valid`):
//   PE row i sees the A row's word i in cycle T + 1 + i; y / y_valid / y_meta appear in cycle T + ARR_LAT, ARR_LAT = ROWS + 2.
//   Rows may be issued back to back or with bubbles; each is an independent wave.
//   Two shadow buffers (0, 1): a tile is written into one while the other may still be waiting for, or being copied by,
//   its swap, so the next tile's load does not wait for the previous swap to travel down the rows (spec mxu.md 3).
//   `swap` in cycle T (from buffer `swap_buf`) makes that shadow tile the active one for the A row presented in that same cycle T and every
//   later row; the swap reaches PE row i in cycle T + i (the copy happens at the end of that cycle), so the rows of
//   the previous tile, which are ahead of it in the skew, still see the old weights. `swap` may be asserted with the first row of the new tile only.
//   `swap_tap[b*NG + g]` pulses in the cycle in which a swap from buffer b copies the last PE row of group g: from that cycle on
//   (a write in the same cycle is fine, it lands after the copy read) group g of buffer b may be rewritten.
//
// Shadow load (window write): one 128-word window per cycle (`wl_data`, 32 bits per word); GRP PE rows form a load group
// (GRP = ROWS / 4: four groups, because the SPM lane of the load port is ROWS bytes = 16 words = GRP rows of one column,
// spec mxu.md).
//   trans = 1: words [(c % 8) * 16 + r] -> PE (row g*GRP + r, column 8 * wl_idx + c)      (8 columns x GRP rows)
//   trans = 0: words [(r % 2) * COLS + c] -> PE (row g*GRP + 2 * wl_idx + r, column c)    (2 rows x COLS columns)
//   with g = wl_grp. The packer (`mxu_wpack`) builds the window, zeroes lanes past K and columns past N.
//
// Flags: the `mxu_pe` flags of every PE row, qualified with the valid of the wave they belong to (arith.md contract
// item 1, 2) and with the column mask of that wave (columns >= a_ncols contribute nothing: arith.md contract item 3),
// ORed over rows and columns. A wave's flags appear in cycles T + 3 .. T + ARR_LAT (row i at T + i + 3), so every flag
// of a wave is out no later than its row.
//
// Data registers have no reset; only the valid chain, the swap chain and the PE flag registers reset (asynchronous, active low).
module mxu_array #(
  parameter int ROWS   = 8,
  parameter int COLS   = 8,
  parameter int GRP    = ROWS / 4,
  parameter int META_W = 1,
  localparam int NG      = ROWS / GRP,
  localparam int GW      = (NG > 1) ? $clog2(NG) : 1,
  localparam int NIDX    = (COLS / 8 > GRP / 2) ? COLS / 8 : GRP / 2,
  localparam int IDXW    = (NIDX > 1) ? $clog2(NIDX) : 1,
  localparam int NCW     = $clog2(COLS + 1),
  localparam int WL_WORDS = 128,
  localparam int ARR_LAT = ROWS + 2
) (
  input  logic                      clk,
  input  logic                      rst_n,
  // shadow window write
  input  logic                      wl_en,
  input  logic                      wl_buf,        // shadow buffer written
  input  logic                      wl_trans,
  input  logic [GW-1:0]             wl_grp,
  input  logic [IDXW-1:0]           wl_idx,
  input  logic [WL_WORDS*32-1:0]    wl_data,
  // swap shadow -> active, with the first A row of the new tile
  input  logic                      swap,
  input  logic                      swap_buf,      // shadow buffer that becomes active
  output logic [2*NG-1:0]           swap_tap,      // [b*NG + g]: a swap from buffer b has copied the last row of group g
  // A row
  input  logic                      a_valid,
  input  logic                      a_mode_bf16,
  input  logic [NCW-1:0]            a_ncols,
  input  logic [META_W-1:0]         a_meta,
  input  logic [ROWS*32-1:0]        a_words,
  // partial sums out
  output logic                      y_valid,
  output logic [COLS*32-1:0]        y,
  output logic [META_W-1:0]         y_meta,
  output logic [3:0]                flags
);
  if (ROWS % GRP != 0 || GRP % 2 != 0 || GRP > 16 || GRP < 2 || COLS % 8 != 0 || COLS > 64 || ROWS > 64 || COLS * 2 > WL_WORDS) begin : g_bad
    $error("mxu_array: unsupported geometry");
  end

  // window words past the geometry (trans: 8 * 16 words, non-trans: 2 * COLS words) are not used by a small array
  logic unused_wl;
  assign unused_wl = &{1'b0, wl_data};

  // ---------------- sideband chain: valid | mode | ncols | meta, one stage per cycle, index k = cycle T + 1 + k
  logic [ARR_LAT-1:0]       sv;
  logic                     sm [ARR_LAT];
  logic [NCW-1:0]           sn [ARR_LAT];
  logic [META_W-1:0]        sx [ARR_LAT];

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) sv <= '0;
    else        sv <= {sv[ARR_LAT-2:0], a_valid};
  end
  always_ff @(posedge clk) begin
    sm[0] <= a_mode_bf16;
    sn[0] <= a_ncols;
    sx[0] <= a_meta;
    for (int k = 1; k < ARR_LAT; k++) begin
      sm[k] <= sm[k-1];
      sn[k] <= sn[k-1];
      sx[k] <= sx[k-1];
    end
  end
  assign y_valid = sv[ARR_LAT-1];
  assign y_meta  = sx[ARR_LAT-1];

  // ---------------- swap chain: swp[i] is high in cycle T + i
  // I-8 (review part I): bit 0 is a wire, bits ROWS-1..1 are flops; each vector has one kind of driver
  logic [ROWS-1:1] swp0_q, swp1_q;
  logic [ROWS-1:0] swp0, swp1;      // swap from buffer 0 / 1
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin swp0_q <= '0; swp1_q <= '0; end
    else begin swp0_q <= swp0[ROWS-2:0]; swp1_q <= swp1[ROWS-2:0]; end
  end
  assign swp0 = {swp0_q, swap && !swap_buf};
  assign swp1 = {swp1_q, swap && swap_buf};
  for (genvar g = 0; g < NG; g++) begin : g_tap
    assign swap_tap[g]      = swp0[g*GRP + GRP - 1];
    assign swap_tap[NG + g] = swp1[g*GRP + GRP - 1];
  end

  // ---------------- A skew: row i delayed i + 1 cycles. A is zero in an invalid cycle (keeps invalid waves clean).
  logic [31:0] a_row [ROWS];
  for (genvar i = 0; i < ROWS; i++) begin : g_skew
    logic [31:0] a_in;
    assign a_in = a_valid ? a_words[32*i +: 32] : 32'd0;
    logic [32*(i+1)-1:0] sr;
    if (i == 0) begin : g_f
      always_ff @(posedge clk) sr <= a_in;
    end else begin : g_n
      always_ff @(posedge clk) sr <= {sr[32*i-1:0], a_in};
    end
    assign a_row[i] = sr[32*(i+1)-1 -: 32];
  end

  // ---------------- PE grid
  // I-2 (review part I): packed, not unpacked. The PE output ports drive these; with an unpacked 2-D array and a blackbox
  // mxu_pe Yosys keeps them as memories and `opt_clean` deletes every PE instance (a separately synthesized or hard-macro PE).
  logic [ROWS-1:0][COLS-1:0][31:0] ps;
  logic [ROWS-1:0][COLS-1:0][3:0]  fl;
  logic [3:0]  rq [ROWS];          // flags of PE row i, qualified

  for (genvar i = 0; i < ROWS; i++) begin : g_row
    localparam int GI = i / GRP;
    localparam int RI = i % GRP;
    logic [3:0] masked [COLS];
    for (genvar j = 0; j < COLS; j++) begin : g_col
      logic [31:0] w_sh0, w_sh1, w_ac, wd;
      logic        we;
      logic [31:0] w_t, w_n;
      assign w_t = wl_data[32*((j % 8) * 16 + RI) +: 32];
      assign w_n = wl_data[32*((RI % 2) * COLS + j) +: 32];
      assign wd  = wl_trans ? w_t : w_n;
      assign we  = wl_en && (wl_grp == GW'(GI)) &&
                   (wl_trans ? (wl_idx == IDXW'(j / 8)) : (wl_idx == IDXW'(RI / 2)));
      always_ff @(posedge clk) begin
        if (we && !wl_buf) w_sh0 <= wd;
        if (we &&  wl_buf) w_sh1 <= wd;
        if (swp0[i])       w_ac <= w_sh0;
        else if (swp1[i])  w_ac <= w_sh1;
      end

      logic [31:0] pin;
      if (i == 0) begin : g_top
        assign pin = 32'd0;
      end else begin : g_in
        assign pin = ps[i-1][j];
      end
      mxu_pe #(.REG_DOT(1'b1), .REG_OUT(1'b1)) u_pe (
        .clk(clk), .rst_n(rst_n), .mode_bf16(sm[i]), .a(a_row[i]), .w(w_ac),
        .psum_in(pin), .psum_out(ps[i][j]), .flags(fl[i][j]));
      // column mask of the wave: columns >= ncols are dropped
      assign masked[j] = (sv[i+2] && (NCW'(j) < sn[i+2])) ? fl[i][j] : 4'd0;
    end
    // flags of row i belong to the wave at sideband stage i + 2
    always_comb begin
      rq[i] = 4'd0;
      for (int j = 0; j < COLS; j++) rq[i] = rq[i] | masked[j];
    end
  end

  for (genvar j = 0; j < COLS; j++) begin : g_y
    assign y[32*j +: 32] = ps[ROWS-1][j];
  end

  // OR the rows. Row i's flags are valid-qualified in `row_or` already.
  always_comb begin
    flags = 4'd0;
    for (int i = 0; i < ROWS; i++) flags = flags | rq[i];
  end
endmodule
