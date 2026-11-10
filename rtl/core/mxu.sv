// mxu: the matrix unit of a core (docs/spec/mxu.md). Executes MXU_MM from the MQ head: decode and legality checks, the
// tile loop (N tile j outer, K tile t inner), stationary-weight fetch (w_rd, group by group), column scales (scale_rd),
// the A stream (a_rd) into mxu_array, rowscale prefetch, and the ACC input stream with completion.
//
// Everything on the SPM side has a fixed response latency SPM_LAT after the grant and is never back-pressured.
// Lane width LB = DIM bytes (DIM = 8, 16, 32 or 64). Legality, FAULT_ADDR and M = 0 follow mxu.md 4.1 (M-10 .. M-18);
// `mode = BF16X3` is reserved in v1 (ILLEGAL_FIELD, M-15).
// The ACC (core/acc) accepts one row per cycle (`acc_valid`, no ready) and pulses `acc_done` when the write of the row that
// carried `acc_last` is visible; spacing of two updates of the same ACC row is at least ACC_LAT cycles (mxu.md 4.4).
module mxu #(
  parameter int DIM       = 8,
  parameter int SPM_LAT   = 2,
  parameter int ACC_LAT   = 8,
  parameter int CS_DEPTH  = 4,
  parameter int ACC_ROWS  = 2048,
  parameter int SPM_BYTES = 4194304,
  localparam int LB       = DIM,
  localparam int GRP      = DIM / 4,
  localparam int NG       = 4,
  localparam int BPG      = DIM / 8,
  localparam int KB       = 4 * DIM,
  localparam int NCW      = $clog2(DIM + 1),
  localparam int META_W   = 24,
  localparam int CS_SHIFT = 6 - $clog2(DIM),          // log2(64 / DIM): MXU passes per scale row
  localparam int RS_DEPTH = (2 * SPM_LAT + DIM + 6) / GRP + 3,
  localparam int NIDX     = (DIM / 8 > GRP / 2) ? DIM / 8 : GRP / 2,
  localparam int IDXW     = (NIDX > 1) ? $clog2(NIDX) : 1
) (
  input  logic                clk,
  input  logic                rst_n,
  // MQ head
  input  logic                q_valid,
  output logic                q_ready,
  input  logic [127:0]        q_instr,
  input  logic [6*64-1:0]     q_regs,
  input  logic [31:0]         q_wval,
  output logic [5:0]          sem_rd_idx,
  input  logic [31:0]         sem_rd_val,
  // DTAB
  output logic                dtab_req,
  output logic [7:0]          dtab_idx,
  input  logic                dtab_gnt,
  input  logic                dtab_rvalid,
  input  logic [255:0]        dtab_data,
  // SPM: A stream (4 lanes), stationary load (8 lanes), scales (1 lane)
  output logic [3:0]          a_req_v,
  output logic [4*22-1:0]     a_req_addr,
  input  logic                a_req_ready,
  input  logic [4*LB*8-1:0]   a_rsp_data,
  output logic [7:0]          w_req_v,
  output logic [8*22-1:0]     w_req_addr,
  input  logic                w_req_ready,
  input  logic [8*LB*8-1:0]   w_rsp_data,
  output logic                s_req_v,
  output logic [21:0]         s_req_addr,
  input  logic                s_req_ready,
  input  logic [LB*8-1:0]     s_rsp_data,
  // ACC input stream
  output logic                acc_valid,
  output logic [10:0]         acc_row,
  output logic [DIM*32-1:0]   acc_part,
  output logic [6:0]          acc_ncols,
  output logic                acc_rescale,
  output logic                acc_overwrite,
  output logic                acc_use_cs,
  output logic [31:0]         acc_rowscale,
  output logic [DIM*32-1:0]   acc_cs,
  output logic                acc_last,
  input  logic                acc_done,
  // completion, flags, faults
  output logic                sem_inc_valid,
  output logic [5:0]          sem_inc_idx,
  output logic [3:0]          sem_inc_amt,
  output logic [3:0]          fp_flags,
  output logic                fault_valid,
  output logic [3:0]          fault_cause,
  output logic [63:0]         fault_addr
);
  // ================================================================== D: queue head, TDs, checks (mxu_dec)
  typedef enum logic [2:0] {L_IDLE, L_CALC, L_RUN, L_PUSH} lstate_t;
  lstate_t lst;

  // in-flight instruction FIFO {ssem, sinc}; instructions complete in order
  logic [9:0] inf_mem [4];
  logic [2:0] inf_cnt;
  logic [1:0] inf_wr, inf_rd;
  logic       inf_push, inf_pop;

  // instruction context handed to the load FSM
  logic        ctx_v, ctx_take, ctx_ready;
  logic        c_trans, c_bf16, c_cs_en;
  logic [1:0]  c_accm;
  logic [15:0] c_K, c_M, c_N, c_KT, c_NT;
  logic [21:0] c_a_base, c_a_pitch, c_w_base, c_w_pitch, c_cs_base, c_rs_base, c_csp;
  logic [10:0] c_acc_base;
  logic [9:0]  ctx_sync;
  logic        z_inc;                   // an M = 0 instruction completes (mxu_dec: only with nothing in flight)

  assign ctx_ready = !ctx_v && (lst == L_IDLE);
  mxu_dec #(.DIM(DIM), .ACC_ROWS(ACC_ROWS), .SPM_BYTES(SPM_BYTES)) u_dec (
    .clk, .rst_n, .q_valid, .q_ready, .q_instr, .q_regs, .q_wval, .sem_rd_idx, .sem_rd_val,
    .dtab_req, .dtab_idx, .dtab_gnt, .dtab_rvalid, .dtab_data,
    .inf_room(inf_cnt < 3'd4), .inf_empty(inf_cnt == 3'd0), .ctx_ready, .ctx_push(inf_push), .z_inc, .ctx_sync,
    .c_trans, .c_bf16, .c_cs_en, .c_accm, .c_K, .c_M, .c_N, .c_KT, .c_NT,
    .c_a_base, .c_a_pitch, .c_w_base, .c_w_pitch, .c_cs_base, .c_rs_base, .c_csp, .c_acc_base,
    .fault_valid, .fault_cause, .fault_addr);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) ctx_v <= 1'b0;
    else if (inf_push) ctx_v <= 1'b1;
    else if (ctx_take) ctx_v <= 1'b0;
  end

  // in-flight FIFO
  logic [9:0] inf_in;
  assign inf_in = ctx_sync;
  assign inf_pop = acc_done;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      inf_cnt <= 3'd0; inf_wr <= 2'd0; inf_rd <= 2'd0;
    end else begin
      if (inf_push) begin inf_mem[inf_wr] <= inf_in; inf_wr <= inf_wr + 2'd1; end
      if (inf_pop) inf_rd <= inf_rd + 2'd1;
      inf_cnt <= inf_cnt + 3'(inf_push) - 3'(inf_pop);
    end
  end
  logic [9:0] inf_head;
  assign inf_head = inf_mem[inf_rd];
  // completion: acc_done retires the head of the FIFO; an M = 0 instruction retires itself (z_inc) while the FIFO is empty, so
  // the two never coincide (an acc_done with an empty FIFO is ignored)
  logic [9:0] sem_inc_w;
  assign sem_inc_w     = z_inc ? ctx_sync : inf_head;
  assign sem_inc_valid = ((acc_done && (inf_cnt != 3'd0)) || z_inc) && (sem_inc_w[9:4] != 6'd0) && (sem_inc_w[3:0] != 4'd0);
  assign sem_inc_idx   = sem_inc_w[9:4];
  assign sem_inc_amt   = sem_inc_w[3:0];

  // ================================================================== scale port arbiter and tags
  logic        rs_want, cs_want;
  logic [21:0] rs_addr, cs_addr;
  logic        rs_gnt, cs_gnt;
  assign s_req_v    = rs_want | cs_want;
  assign s_req_addr = rs_want ? rs_addr : cs_addr;
  assign rs_gnt     = s_req_ready && rs_want;
  assign cs_gnt     = s_req_ready && !rs_want && cs_want;

  // tag: {v, src (1 = rowscale), last cs lane, cs lane index (2 bits)}
  logic [4:0] s_tag [SPM_LAT];
  logic [1:0] cs_lane_issue;       // next column-scale lane to request (0..3)
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int k = 0; k < SPM_LAT; k++) s_tag[k] <= 5'd0;
    end else begin
      s_tag[0] <= {s_req_v && s_req_ready, rs_want, cs_lane_issue == 2'd3, cs_lane_issue};
      for (int k = 1; k < SPM_LAT; k++) s_tag[k] <= s_tag[k-1];
    end
  end
  logic [4:0] s_rtag;
  assign s_rtag = s_tag[SPM_LAT-1];

  // ================================================================== L: tile loop, stationary load, column scales
  logic [15:0] l_j, l_t;
  logic [1:0]  l_g;
  logic [IDXW-1:0] l_idx;
  logic        l_w_all;                 // all beats of the tile requested
  logic        l_w_done;                // last beat landed in the shadow
  logic        l_cs_all, l_cs_done;     // column scales: requested / stored
  logic [2*NG-1:0] free_g;              // [b*NG + g]: group g of shadow buffer b may be written
  logic [2*NG-1:0] swap_tap;
  logic            l_buf;               // shadow buffer of the tile being loaded; toggles per tile

  // per-tile values
  logic [21:0] t_w_base;
  logic [8:0]  t_kb;                    // valid K bytes of this tile
  logic [7:0]  t_ncols;
  logic [21:0] t_cs_addr;
  logic        t_cs_en;
  logic [63:0] ke0;

  logic [63:0] ctx_Kbytes;
  assign ctx_Kbytes = 64'(c_K) * (c_bf16 ? 64'd2 : 64'd1);

  // shift for K tile bytes / elements
  always_comb begin
    ke0 = 64'(l_t) * (c_bf16 ? 64'(2 * DIM) : 64'(4 * DIM));
  end

  // ---- W beat lanes
  logic [7:0]  wl_lane_v;
  logic [21:0] wl_lane_addr [8];
  logic [7:0]  wl_nbytes, wl_ncols_b;
  logic [63:0] kbeat, nb_calc, col_idx;
  always_comb begin
    wl_lane_v = 8'd0;
    for (int l = 0; l < 8; l++) wl_lane_addr[l] = 22'd0;
    wl_nbytes = 8'd0;
    wl_ncols_b = t_ncols;
    kbeat = 64'd0;
    nb_calc = 64'd0;
    col_idx = 64'd0;
    if (c_trans) begin
      nb_calc = (64'(t_kb) > 64'(l_g) * 64'(LB)) ? ((64'(t_kb) - 64'(l_g) * 64'(LB) > 64'(LB)) ? 64'(LB) : 64'(t_kb) - 64'(l_g) * 64'(LB)) : 64'd0;
      wl_nbytes = 8'(nb_calc);
      for (int l = 0; l < 8; l++) begin
        col_idx = 64'(l_j) * 64'(DIM) + 64'(l_idx) * 64'd8 + 64'(l);
        wl_lane_v[l] = (col_idx < 64'(c_N)) && (nb_calc != 64'd0);
        wl_lane_addr[l] = 22'(64'(t_w_base) + (64'(l_idx) * 64'd8 + 64'(l)) * 64'(c_w_pitch) + 64'(l_g) * 64'(LB));
      end
    end else if (!c_bf16) begin
      for (int l = 0; l < 8; l++) begin
        kbeat = ke0 + 64'd8 * (64'(l_g) * 64'(GRP / 2) + 64'(l_idx)) + 64'(l);
        wl_lane_v[l] = kbeat < 64'(c_K);
        wl_lane_addr[l] = 22'(64'(c_w_base) + kbeat * 64'(c_w_pitch) + 64'(l_j) * 64'(DIM));
      end
    end else begin
      for (int l = 0; l < 8; l++) begin
        kbeat = ke0 + 64'd4 * (64'(l_g) * 64'(GRP / 2) + 64'(l_idx)) + 64'(l / 2);
        wl_lane_v[l] = kbeat < 64'(c_K);
        wl_lane_addr[l] = 22'(64'(c_w_base) + kbeat * 64'(c_w_pitch) + 64'd2 * 64'(l_j) * 64'(DIM) + 64'(l % 2) * 64'(LB));
      end
    end
  end

  logic w_want, w_gnt, w_last_beat;
  assign w_want = (lst == L_RUN) && !l_w_all && (free_g[int'(l_buf) * NG + int'(l_g)] || l_idx != '0);   // only the first beat of a group waits for the swap
  assign w_req_v = w_want ? wl_lane_v : 8'd0;
  // M-23 (review R-2): a beat with no valid lane is granted by the MXU itself and never presented as a request (w_req_v = 0 needs
  // no ready); its response slot is still taken, so the zero window is written into the shadow like any beat.
  assign w_gnt = w_want && (w_req_ready || wl_lane_v == 8'd0);
  assign w_last_beat = (l_g == 2'(NG - 1)) && (l_idx == IDXW'(BPG - 1));
  for (genvar l = 0; l < 8; l++) begin : g_wa
    assign w_req_addr[22*l +: 22] = wl_lane_addr[l];
  end

  // W tags: {v, last, g(2), idx, trans, bf16, lane_v(8), nbytes(8), ncols(8)}
  localparam int WTW = 1 + 1 + 1 + 2 + IDXW + 1 + 1 + 8 + 8 + 8;
  logic [WTW-1:0] w_tag [SPM_LAT];
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (int k = 0; k < SPM_LAT; k++) w_tag[k] <= '0;
    end else begin
      w_tag[0] <= {w_gnt, w_last_beat, l_buf, l_g, l_idx, c_trans, c_bf16, wl_lane_v, wl_nbytes, wl_ncols_b};
      for (int k = 1; k < SPM_LAT; k++) w_tag[k] <= w_tag[k-1];
    end
  end
  logic [WTW-1:0] w_rtag;
  assign w_rtag = w_tag[SPM_LAT-1];
  logic            wr_v, wr_last, wr_trans, wr_bf16, wr_buf;
  logic [1:0]      wr_g;
  logic [IDXW-1:0] wr_idx;
  logic [7:0]      wr_lane_v, wr_nbytes, wr_ncols;
  assign {wr_v, wr_last, wr_buf, wr_g, wr_idx, wr_trans, wr_bf16, wr_lane_v, wr_nbytes, wr_ncols} = w_rtag;

  logic [128*32-1:0] win;
  mxu_wpack #(.DIM(DIM)) u_pack (.trans(wr_trans), .bf16(wr_bf16), .lane_v(wr_lane_v), .nbytes(wr_nbytes),
                                 .ncols(wr_ncols), .lanes(w_rsp_data), .win(win));

  // ---- column scale fetch and storage
  logic [DIM*32-1:0] cs_mem [CS_DEPTH];
  logic [$clog2(CS_DEPTH):0] cs_cnt;          // entries reserved or filled, freed by the exit stage
  logic [$clog2(CS_DEPTH)-1:0] cs_wr, cs_rd;
  logic        cs_free_ev;                    // exit stage: a pass with column scales ended
  assign cs_want = (lst == L_RUN) && t_cs_en && !l_cs_all;
  assign cs_addr = t_cs_addr + 22'(cs_lane_issue) * 22'(LB);
  logic cs_reserved;                          // this tile holds its slot

  // ---- token to the stream FSM
  logic        tok_v, tok_take;
  logic [15:0] tk_j, tk_t, tk_M;
  logic [8:0]  tk_kb;
  logic [7:0]  tk_ncols;
  logic        tk_last, tk_bf16, tk_cs_en, tk_buf;
  logic [1:0]  tk_accm;
  logic [21:0] tk_a_base, tk_a_pitch, tk_rs_base;
  logic [10:0] tk_acc_base;

  logic        last_tile;
  assign last_tile = (l_j == c_NT - 16'd1) && (l_t == c_KT - 16'd1);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      lst <= L_IDLE;
      l_j <= '0; l_t <= '0; l_g <= '0; l_idx <= '0;
      l_w_all <= 1'b0; l_w_done <= 1'b0; l_cs_all <= 1'b0; l_cs_done <= 1'b0;
      free_g <= '1;
      tok_v <= 1'b0;
      cs_lane_issue <= 2'd0;
      cs_cnt <= '0; cs_wr <= '0; cs_rd <= '0;
      cs_reserved <= 1'b0;
      l_buf <= 1'b0;
    end else begin
      // free flags: cleared at each beat issue of the group, set by the swap
      for (int g = 0; g < 2*NG; g++) begin
        if (swap_tap[g]) free_g[g] <= 1'b1;
        else if (w_gnt && (int'(l_buf) * NG + int'(l_g)) == g) free_g[g] <= 1'b0;
      end
      if (tok_take) tok_v <= 1'b0;
      // cs FIFO count: +1 at reservation, -1 at the end of a pass that used it
      cs_cnt <= cs_cnt + (cs_gnt && cs_lane_issue == 2'd0 && !cs_reserved ? 1 : 0) - (cs_free_ev ? 1 : 0);
      if (cs_free_ev) cs_rd <= (cs_rd == $bits(cs_rd)'(CS_DEPTH - 1)) ? '0 : cs_rd + 1'b1;
      case (lst)
        L_IDLE: if (ctx_v) begin
          l_j <= '0; l_t <= '0;
          lst <= L_CALC;
        end
        L_CALC: begin
          l_g <= '0; l_idx <= '0;
          l_w_all <= 1'b0; l_w_done <= 1'b0;
          l_cs_all <= !t_cs_en; l_cs_done <= !t_cs_en;
          cs_lane_issue <= 2'd0;
          cs_reserved <= 1'b0;
          if (!t_cs_en || cs_cnt < CS_DEPTH[$clog2(CS_DEPTH):0]) lst <= L_RUN;
        end
        L_RUN: begin
          if (w_gnt) begin
            if (w_last_beat) l_w_all <= 1'b1;
            if (l_idx == IDXW'(BPG - 1)) begin l_idx <= '0; l_g <= l_g + 2'd1; end
            else l_idx <= l_idx + 1'b1;
          end
          if (cs_gnt) begin
            cs_reserved <= 1'b1;
            cs_lane_issue <= cs_lane_issue + 2'd1;
            if (cs_lane_issue == 2'd3) l_cs_all <= 1'b1;
          end
          // responses
          if (wr_v && wr_last) l_w_done <= 1'b1;
          if (s_rtag[4] && !s_rtag[3]) begin
            if (s_rtag[2]) begin
              l_cs_done <= 1'b1;
              cs_wr <= (cs_wr == $bits(cs_wr)'(CS_DEPTH - 1)) ? '0 : cs_wr + 1'b1;
            end
          end
          if (l_w_done && l_cs_done) lst <= L_PUSH;
        end
        L_PUSH: if (!tok_v || tok_take) begin
          tok_v <= 1'b1;
          l_buf <= !l_buf;
          // next tile
          if (last_tile) lst <= L_IDLE;
          else begin
            if (l_t == c_KT - 16'd1) begin l_t <= '0; l_j <= l_j + 16'd1; end
            else l_t <= l_t + 16'd1;
            lst <= L_CALC;
          end
        end
        default: lst <= L_IDLE;
      endcase
    end
  end
  assign ctx_take = (lst == L_IDLE) && ctx_v;

  // tile values (valid in L_RUN and L_PUSH; combinational from the loop counters)
  always_comb begin
    t_kb = 9'(((ctx_Kbytes - 64'(l_t) * 64'(KB)) > 64'(KB)) ? 64'(KB) : (ctx_Kbytes - 64'(l_t) * 64'(KB)));
    t_ncols = 8'((64'(c_N) - 64'(l_j) * 64'(DIM)) > 64'(DIM) ? 64'(DIM) : (64'(c_N) - 64'(l_j) * 64'(DIM)));
    t_w_base = c_trans ? 22'(64'(c_w_base) + 64'(l_j) * 64'(DIM) * 64'(c_w_pitch) + 64'(l_t) * 64'(KB)) : c_w_base;
    // scale row of pass t: t >> log2(64 / DIM) (the scale rows are per fold tile, M-13), pitch CSP (M-11); column tile j: 4 DIM B
    t_cs_addr = 22'(64'(c_cs_base) + 64'(c_csp) * (64'(l_t) >> CS_SHIFT) + 64'd4 * 64'(DIM) * 64'(l_j));
    t_cs_en = c_cs_en;
  end

  // token fill: in L_PUSH
  always_ff @(posedge clk) begin
    if (lst == L_PUSH && (!tok_v || tok_take)) begin
      tk_j <= l_j; tk_t <= l_t; tk_M <= c_M; tk_kb <= t_kb; tk_ncols <= t_ncols;
      tk_buf <= l_buf; tk_last <= last_tile; tk_bf16 <= c_bf16; tk_cs_en <= c_cs_en; tk_accm <= c_accm;
      tk_a_base <= c_a_base; tk_a_pitch <= c_a_pitch; tk_rs_base <= c_rs_base; tk_acc_base <= c_acc_base;
    end
  end

  // column scale storage: lane responses
  always_ff @(posedge clk) begin
    if (s_rtag[4] && !s_rtag[3]) cs_mem[cs_wr][LB*8*s_rtag[1:0] +: LB*8] <= s_rsp_data;
  end

  // ================================================================== S: A stream
  typedef enum logic [1:0] {S_IDLE, S_RUN} sstate_t;
  sstate_t sst;
  logic [15:0] s_m, s_t, s_Mrows;
  logic [8:0]  s_kb;
  logic [7:0]  s_ncols;
  logic        s_last, s_bf16, s_cs_en, s_buf;
  logic [1:0]  s_accm;
  logic [21:0] s_a_pitch, s_rs_base;
  logic [21:0] s_addr;
  logic [10:0] s_accrow;
  logic [15:0] s_lane_cnt;            // rowscale lanes requested in this pass
  logic [7:0]  gap_cnt;
  // M-22 (review R-1): after the last pass of an instruction the next pass (another instruction, any acc_base / M) may update
  // any row of this pass's range. Its first row issues 2 + gap cycles after this pass's last row, so the gap is ACC_LAT - 2
  // whatever M (the K passes of one instruction keep the shorter ACC_LAT - M gap of mxu.md 4.4).
  localparam int GAP_X = (ACC_LAT > 2) ? ACC_LAT - 2 : 0;
  logic        rs_need;
  logic [3:0]  a_lane_v;
  logic [4*22-1:0] a_lane_addr;
  logic [$clog2(RS_DEPTH):0] rs_res;
  logic        rs_free_ev;
  logic [$clog2(RS_DEPTH)-1:0] rs_wr, rs_rd;

  assign rs_need = (s_accm == 2'd2) && (s_t == 16'd0);
  logic need_rs_lane;
  assign need_rs_lane = rs_need && (s_m % 16'(GRP) == 16'd0) && (s_lane_cnt == s_m / 16'(GRP));
  assign rs_want = (sst == S_RUN) && need_rs_lane && (rs_res < RS_DEPTH[$clog2(RS_DEPTH):0]);
  assign rs_addr = s_rs_base + 22'(s_lane_cnt) * 22'(LB);

  for (genvar l = 0; l < 4; l++) begin : g_av
    assign a_lane_v[l] = (l * LB) < int'(s_kb);
    assign a_lane_addr[22*l +: 22] = s_addr + 22'(l * LB);
  end
  logic a_want, a_gnt;
  assign a_want = (sst == S_RUN) && !need_rs_lane;
  assign a_req_v = a_want ? a_lane_v : 4'd0;
  assign a_req_addr = a_lane_addr;
  assign a_gnt = a_want && a_req_ready;
  assign tok_take = (sst == S_IDLE) && tok_v && (gap_cnt == 8'd0);

  // A tags: {v, first, lastp, lasti, resc, ovw, ucs, ncols(8), kb(9), bf16, acc_row(11)}
  localparam int ATW = 1 + 1 + 1 + 1 + 1 + 1 + 1 + 8 + 9 + 1 + 1 + 11;
  logic [ATW-1:0] a_tag [SPM_LAT];
  logic           a_first, a_lastp, a_lasti, a_resc, a_ovw, a_ucs;
  assign a_first = (s_m == 16'd0);
  assign a_lastp = (s_m == s_Mrows - 16'd1);
  assign a_lasti = a_lastp && s_last;
  assign a_resc  = rs_need;
  assign a_ovw   = (s_accm == 2'd0) && (s_t == 16'd0);
  assign a_ucs   = s_cs_en;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      sst <= S_IDLE;
      for (int k = 0; k < SPM_LAT; k++) a_tag[k] <= '0;
      gap_cnt <= 8'd0;
      s_m <= '0; s_lane_cnt <= '0; s_addr <= '0; s_accrow <= '0;
    end else begin
      a_tag[0] <= {a_gnt, a_first, a_lastp, a_lasti, a_resc, a_ovw, a_ucs, s_ncols, s_kb, s_bf16, s_buf, s_accrow};
      for (int k = 1; k < SPM_LAT; k++) a_tag[k] <= a_tag[k-1];
      if (gap_cnt != 8'd0 && sst == S_IDLE) gap_cnt <= gap_cnt - 8'd1;
      case (sst)
        S_IDLE: if (tok_take) begin
          sst <= S_RUN;
          s_m <= '0;
          s_lane_cnt <= '0;
          s_t <= tk_t; s_Mrows <= tk_M; s_kb <= tk_kb; s_ncols <= tk_ncols; s_last <= tk_last; s_bf16 <= tk_bf16;
          s_cs_en <= tk_cs_en; s_buf <= tk_buf; s_accm <= tk_accm; s_a_pitch <= tk_a_pitch; s_rs_base <= tk_rs_base;
          s_addr <= tk_a_base + 22'(64'(tk_t) * 64'(KB));
          s_accrow <= tk_acc_base + 11'(64'(tk_j) * 64'(tk_M));
        end
        S_RUN: begin
          if (rs_gnt) s_lane_cnt <= s_lane_cnt + 16'd1;
          if (a_gnt) begin
            s_m <= s_m + 16'd1;
            s_addr <= s_addr + s_a_pitch;
            s_accrow <= s_accrow + 11'd1;
            if (a_lastp) begin
              sst <= S_IDLE;
              gap_cnt <= (s_Mrows >= 16'(ACC_LAT)) ? 8'd0 : 8'(ACC_LAT) - 8'(s_Mrows);
              // M-22: M >= 2 leaves a gap of at most ACC_LAT - 2 above; M = 1 already has ACC_LAT - 1
              if (s_last && s_Mrows >= 16'd2) gap_cnt <= 8'(GAP_X);
            end
          end
        end
        default: sst <= S_IDLE;
      endcase
    end
  end

  // rowscale reservation count and lane storage
  logic [GRP*32-1:0] rs_mem [RS_DEPTH];
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rs_res <= '0; rs_wr <= '0; rs_rd <= '0;
    end else begin
      rs_res <= rs_res + (rs_gnt ? 1 : 0) - (rs_free_ev ? 1 : 0);
      if (s_rtag[4] && s_rtag[3]) rs_wr <= (rs_wr == $bits(rs_wr)'(RS_DEPTH - 1)) ? '0 : rs_wr + 1'b1;
      if (rs_free_ev) rs_rd <= (rs_rd == $bits(rs_rd)'(RS_DEPTH - 1)) ? '0 : rs_rd + 1'b1;
    end
  end
  always_ff @(posedge clk) begin
    if (s_rtag[4] && s_rtag[3]) rs_mem[rs_wr] <= s_rsp_data;      // a rowscale lane is one SPM lane: GRP * 32 = LB * 8 bits
  end

  // A response path: mask bytes past the tile's K, feed the array
  logic            ar_v, ar_first, ar_lastp, ar_lasti, ar_resc, ar_ovw, ar_ucs, ar_bf16, ar_buf;
  logic [7:0]      ar_ncols;
  logic [8:0]      ar_kb;
  logic [10:0]     ar_row;
  assign {ar_v, ar_first, ar_lastp, ar_lasti, ar_resc, ar_ovw, ar_ucs, ar_ncols, ar_kb, ar_bf16, ar_buf, ar_row} = a_tag[SPM_LAT-1];
  logic [KB*8-1:0] a_masked;
  always_comb begin
    for (int b = 0; b < KB; b++) a_masked[8*b +: 8] = (b < int'(ar_kb)) ? a_rsp_data[8*b +: 8] : 8'd0;
  end

  // ================================================================== array and exit stage
  logic            y_valid;
  logic [DIM*32-1:0] y;
  logic [META_W-1:0] y_meta;
  logic [3:0]      arr_flags;
  logic [NCW-1:0]  ar_nc;
  assign ar_nc = ar_ncols[NCW-1:0];

  mxu_array #(.ROWS(DIM), .COLS(DIM), .META_W(META_W)) u_arr (
    .clk(clk), .rst_n(rst_n),
    .wl_en(wr_v), .wl_buf(wr_buf), .wl_trans(wr_trans), .wl_grp(wr_g), .wl_idx(wr_idx), .wl_data(win),
    .swap(ar_v && ar_first), .swap_buf(ar_buf), .swap_tap(swap_tap),
    .a_valid(ar_v), .a_mode_bf16(ar_bf16), .a_ncols(ar_nc),
    .a_meta({ar_ncols[6:0], ar_ucs, ar_ovw, ar_resc, ar_lasti, ar_lastp, ar_first, ar_row}),
    .a_words(a_masked),
    .y_valid(y_valid), .y(y), .y_meta(y_meta), .flags(arr_flags));

  logic [10:0] x_row;
  logic        x_first, x_lastp, x_lasti, x_resc, x_ovw, x_ucs;
  logic [6:0]  x_ncols;
  assign {x_ncols, x_ucs, x_ovw, x_resc, x_lasti, x_lastp, x_first, x_row} = y_meta;
  logic unused_first;
  assign unused_first = &{1'b0, x_first, ar_ncols[7]};

  logic [$clog2(GRP+1)-1:0] rs_idx;
  logic [GRP*32-1:0] rs_head;
  assign rs_head = rs_mem[rs_rd];

  assign acc_valid     = y_valid;
  assign acc_row       = x_row;
  assign acc_part      = y;
  assign acc_ncols     = x_ncols;
  assign acc_rescale   = x_resc;
  assign acc_overwrite = x_ovw;
  assign acc_use_cs    = x_ucs;
  assign acc_rowscale  = rs_head[32*rs_idx +: 32];
  assign acc_cs        = cs_mem[cs_rd];
  assign acc_last      = y_valid && x_lasti;
  assign fp_flags      = arr_flags;
  assign cs_free_ev    = y_valid && x_lastp && x_ucs;
  assign rs_free_ev    = y_valid && x_resc && ((rs_idx == ($clog2(GRP+1))'(GRP - 1)) || x_lastp);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) rs_idx <= '0;
    else if (y_valid && x_resc) rs_idx <= rs_free_ev ? '0 : rs_idx + 1'b1;
  end

`ifdef FORMAL
  // ================================================================== formal (tb/formal/mxu.sby; spec mxu.md 6)
  // Run with mxu_dec and mxu_pe replaced by tb/formal/mxu_dec_stub.sv and mxu_stubs.sv: the instruction context is free but
  // legal, the arithmetic is gone, and every property below is about control. The environment is free except for the
  // assumptions in this block: a request waits at most 2 cycles for its grant, acc_done follows the last row within 3
  // cycles and only after it, reset is low for the first two cycles and high afterwards.
  localparam int F_NI = 1 << IDXW;

  reg [1:0] f_rc = 2'd0;
  always @(posedge clk) if (f_rc != 2'd3) f_rc <= f_rc + 2'd1;
  always @(*) begin
    if (f_rc < 2'd2) assume (!rst_n);
    else             assume (rst_n);
  end

  // ---- environment: bounded waits for the three SPM ports
  reg [1:0] f_ha = 2'd0, f_hw = 2'd0, f_hs = 2'd0;
  always @(posedge clk) begin
    f_ha <= (a_req_v != 4'd0 && !a_req_ready) ? ((f_ha == 2'd3) ? f_ha : f_ha + 2'd1) : 2'd0;
    f_hw <= (w_req_v != 8'd0 && !w_req_ready) ? ((f_hw == 2'd3) ? f_hw : f_hw + 2'd1) : 2'd0;
    f_hs <= (s_req_v && !s_req_ready)         ? ((f_hs == 2'd3) ? f_hs : f_hs + 2'd1) : 2'd0;
  end
  always @(*) begin
    assume (!(f_ha >= 2'd2 && a_req_v != 4'd0 && !a_req_ready));
    assume (!(f_hw >= 2'd2 && w_req_v != 8'd0 && !w_req_ready));
    assume (!(f_hs >= 2'd2 && s_req_v && !s_req_ready));
  end
  // a beat whose lanes are all invalid never requests (w_req_v is 0) and is granted by the MXU itself (M-23), so the W wait bound
  // above is enough; the A port has lane 0 valid in every request, but its grant is a_want && ready, bound it the same way
  reg [1:0] f_haw = 2'd0;
  always @(posedge clk) f_haw <= (a_want && !a_req_ready) ? ((f_haw == 2'd3) ? f_haw : f_haw + 2'd1) : 2'd0;
  always @(*) begin
    assume (!(f_haw >= 2'd2 && a_want && !a_req_ready));
  end

  // ---- environment: acc_done only for a last row that has been written, within 3 cycles
  reg [2:0] f_pend = 3'd0, f_dage = 3'd0;
  wire f_lastrow = acc_valid && acc_last;
  always @(posedge clk) begin
    f_pend <= f_pend + {2'b00, f_lastrow} - {2'b00, acc_done};
    if (acc_done || f_pend == 3'd0) f_dage <= 3'd0;
    else if (f_dage != 3'd7)       f_dage <= f_dage + 3'd1;
  end
  always @(*) begin
    assume (!acc_done || f_pend != 3'd0);
    assume (!(f_pend != 3'd0 && f_dage >= 3'd3 && !acc_done));
  end

  // ---- P1 / P2: shadow buffers. f_got[b][g][idx] = a beat written and not yet copied by the swap of its group.
  reg  [2*NG*F_NI-1:0] f_got = '0;
  reg  [2*NG*F_NI-1:0] f_clr, f_set;
  integer fi, fg;
  always @(*) begin
    f_clr = '0; f_set = '0;
    for (fi = 0; fi < 2*NG; fi = fi + 1) if (swap_tap[fi]) f_clr[fi*F_NI +: F_NI] = {F_NI{1'b1}};
    if (wr_v) f_set[(int'(wr_buf) * NG + int'(wr_g)) * F_NI + int'(wr_idx)] = 1'b1;
  end
  always @(posedge clk) f_got <= (f_got & ~f_clr) | f_set;
  always @(*) if (rst_n) begin
    // P1: never write a beat over data its swap has not copied yet (also catches a duplicated beat)
    if (wr_v) assert (!(f_got[(int'(wr_buf) * NG + int'(wr_g)) * F_NI + int'(wr_idx)] && !swap_tap[int'(wr_buf) * NG + int'(wr_g)]));
    // P2: a swap needs every beat of its tile (no lost beat)
    if (ar_v && ar_first)
      for (fg = 0; fg < NG; fg = fg + 1)
        for (fi = 0; fi < BPG; fi = fi + 1)
          assert (f_got[(int'(ar_buf) * NG + fg) * F_NI + fi]);
  end

  // ---- P3: a row of the ACC is updated at most every ACC_LAT cycles, whatever the stalls (one arbitrary row)
  (* anyconst *) wire [10:0] f_row;
  reg f_seen = 1'b0;
  reg [7:0] f_since = 8'd0;
  always @(posedge clk) begin
    if (y_valid && x_row == f_row) begin f_seen <= 1'b1; f_since <= 8'd0; end
    else if (f_since != 8'hFF)     f_since <= f_since + 8'd1;
  end
  always @(*) if (rst_n && y_valid && x_row == f_row && f_seen) assert (f_since + 8'd1 >= 8'(ACC_LAT));
  // instruction id of an output row (toggles after the last row of an instruction): P3 across instructions (M-22)
  reg f_ins = 1'b0, f_row_ins = 1'b0;
  always @(posedge clk) begin
    if (y_valid && x_lasti) f_ins <= !f_ins;
    if (y_valid && x_row == f_row) f_row_ins <= f_ins;
  end

  // ---- P4: rows leave in pass order: a pass starts with `first`, then consecutive rows, ends with `lastp`
  reg f_inpass = 1'b0;
  reg [10:0] f_prev = 11'd0;
  always @(posedge clk) if (y_valid) begin f_inpass <= !x_lastp; f_prev <= x_row; end
  always @(*) if (rst_n && y_valid) begin
    if (f_inpass) assert (!x_first && x_row == f_prev + 11'd1);
    else          assert (x_first);
  end

  // ---- P5: the column-scale and rowscale FIFOs: counts within their depth, entries filled before they are read
  reg [4:0] f_csf = 5'd0, f_rsf = 5'd0;
  wire f_cs_fill = s_rtag[4] && !s_rtag[3] && s_rtag[2];
  wire f_rs_fill = s_rtag[4] && s_rtag[3];
  always @(posedge clk) begin
    f_csf <= f_csf + {4'd0, f_cs_fill} - {4'd0, cs_free_ev};
    f_rsf <= f_rsf + {4'd0, f_rs_fill} - {4'd0, rs_free_ev};
  end
  always @(*) if (rst_n) begin
    assert (cs_cnt <= CS_DEPTH);
    assert (rs_res <= RS_DEPTH);
    assert (f_csf <= cs_cnt);
    assert (f_rsf <= rs_res);
    if (cs_free_ev) assert (f_csf != 5'd0);
    if (rs_free_ev) assert (f_rsf != 5'd0);
    if (y_valid && x_ucs)  assert (f_csf != 5'd0);        // the pass's scales are in the FIFO when its rows arrive
    if (y_valid && x_resc) assert (f_rsf != 5'd0);
    // a pass that rescales starts at element 0 of a lane (a partial last lane was dropped at the end of the previous pass)
    if (y_valid && x_resc && x_first) assert (rs_idx == '0);
  end

  // independent counts of the reservations (first lane grant of a tile's scales, rowscale lane grants) minus the frees: the
  // DUT counters must agree, so a slot that is never returned (a deadlock after CS_DEPTH passes) shows up at the first free
  reg [4:0] f_csr = 5'd0, f_rsr = 5'd0;
  reg       f_cs_res = 1'b0;                    // the tile in L holds its reservation
  wire f_cs_take = cs_gnt && cs_lane_issue == 2'd0 && !f_cs_res;
  always @(posedge clk) begin
    f_csr <= f_csr + {4'd0, f_cs_take} - {4'd0, cs_free_ev};
    f_rsr <= f_rsr + {4'd0, rs_gnt} - {4'd0, rs_free_ev};
    if (f_cs_take) f_cs_res <= 1'b1; else if (lst == L_CALC) f_cs_res <= 1'b0;
  end
  always @(*) if (rst_n) begin
    assert (cs_cnt == f_csr[$clog2(CS_DEPTH):0]);
    assert (rs_res == f_rsr[$clog2(RS_DEPTH):0]);
  end

`ifdef F_COVER
  reg [2:0] f_swp0 = 3'd0, f_swp1 = 3'd0;
  always @(posedge clk) if (ar_v && ar_first) begin
    if (ar_buf) f_swp1 <= (f_swp1 == 3'd7) ? f_swp1 : f_swp1 + 3'd1;
    else        f_swp0 <= (f_swp0 == 3'd7) ? f_swp0 : f_swp0 + 3'd1;
  end
  always @(*) if (rst_n) begin
    cover (acc_valid && acc_last);
    cover (f_swp0 >= 3'd2 && f_swp1 >= 3'd1);              // a buffer reused after the other was used
    cover (f_swp0 >= 3'd2 && swap_tap[0] && w_gnt);        // a load right after a tap
    cover (sem_inc_valid);
    cover (y_valid && x_resc && x_ucs);
    // the row of P3 updated by two different instructions, the second one 0 .. 3 cycles after the first allowed instant: the
    // ACC_LAT - M gap alone cannot cover it, the instruction-boundary gap of M-22 does
    cover (y_valid && x_row == f_row && f_seen && f_row_ins != f_ins && f_since + 8'd1 >= 8'(ACC_LAT) && f_since + 8'd1 <= 8'(ACC_LAT) + 8'd3);
  end
`endif
`endif
endmodule
