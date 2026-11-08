// mxu: the matrix unit of a core (docs/spec/mxu.md). Executes MXU_MM from the MQ head: decode and legality checks, the
// tile loop (N tile j outer, K tile t inner), stationary-weight fetch (w_rd, group by group), column scales (scale_rd),
// the A stream (a_rd) into mxu_array, rowscale prefetch, and the ACC input stream with completion.
//
// Everything on the SPM side has a fixed response latency SPM_LAT after the grant and is never back-pressured.
// Lane width LB = DIM bytes. `mode = BF16X3` is not implemented (ILLEGAL_FIELD, mxu.md 9).
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
  output logic [31:0]         fault_addr
);
  import isa_pkg::*;

  // ================================================================== D: queue head, TDs, checks
  typedef enum logic [3:0] {D_IDLE, D_TDA_REQ, D_TDA_WAIT, D_TDW_REQ, D_TDW_WAIT, D_CHECK, D_PUSH, D_FAULT} dstate_t;
  dstate_t dst;
  typedef enum logic [2:0] {L_IDLE, L_CALC, L_RUN, L_PUSH} lstate_t;
  lstate_t lst;

  logic [127:0] ins;
  logic [63:0]  r_a, r_w, r_acc, r_m, r_rs, r_cs;
  logic [255:0] td_a, td_w;

  logic [5:0] f_wsem;
  assign f_wsem     = q_instr[SYNC_WSEM_MSB -: 6];
  assign sem_rd_idx = f_wsem;
  logic sem_ok;
  assign sem_ok = (f_wsem == 6'd0) || (32'($signed(sem_rd_val - q_wval)) < 32'h8000_0000);

  // in-flight instruction FIFO {ssem, sinc}; instructions complete in order
  logic [9:0] inf_mem [4];
  logic [2:0] inf_cnt;
  logic [1:0] inf_wr, inf_rd;
  logic       inf_push, inf_pop;

  assign q_ready = (dst == D_IDLE) && q_valid && sem_ok && (inf_cnt < 3'd4) && !fault_valid;

  // field decode of the latched instruction
  logic [4:0]  a_dt, w_dt;
  logic [2:0]  a_sp, w_sp;
  logic [7:0]  a_fl, w_fl;
  logic [15:0] a_n0, a_n1, a_n2, a_n3, w_n0, w_n1, w_n2, w_n3;
  logic [31:0] a_s1, w_s1;
  assign a_dt = td_a[TD_DTYPE_MSB:TD_DTYPE_LSB];
  assign w_dt = td_w[TD_DTYPE_MSB:TD_DTYPE_LSB];
  assign a_sp = td_a[TD_SPACE_MSB:TD_SPACE_LSB];
  assign w_sp = td_w[TD_SPACE_MSB:TD_SPACE_LSB];
  assign a_fl = td_a[TD_FLAGS_MSB:TD_FLAGS_LSB];
  assign w_fl = td_w[TD_FLAGS_MSB:TD_FLAGS_LSB];
  assign a_n0 = td_a[TD_N0_MSB:TD_N0_LSB];
  assign a_n1 = td_a[TD_N1_MSB:TD_N1_LSB];
  assign a_n2 = td_a[TD_N2_MSB:TD_N2_LSB];
  assign a_n3 = td_a[TD_N3_MSB:TD_N3_LSB];
  assign w_n0 = td_w[TD_N0_MSB:TD_N0_LSB];
  assign w_n1 = td_w[TD_N1_MSB:TD_N1_LSB];
  assign w_n2 = td_w[TD_N2_MSB:TD_N2_LSB];
  assign w_n3 = td_w[TD_N3_MSB:TD_N3_LSB];
  assign a_s1 = td_a[TD_S1_MSB:TD_S1_LSB];
  assign w_s1 = td_w[TD_S1_MSB:TD_S1_LSB];

  logic       i_trans;
  logic [1:0] i_accm;
  logic [2:0] i_mode;
  logic       cs_en, rs_en, m_en;
  assign i_trans = ins[T_MXU_TRANS_W_MSB];
  assign i_accm  = ins[T_MXU_ACC_MODE_MSB:T_MXU_ACC_MODE_LSB];
  assign i_mode  = ins[T_MXU_MODE_MSB:T_MXU_MODE_LSB];
  assign cs_en   = ins[T_MXU_CS_REG_MSB:T_MXU_CS_REG_LSB] != 5'd0;
  assign rs_en   = ins[T_MXU_RS_REG_MSB:T_MXU_RS_REG_LSB] != 5'd0;
  assign m_en    = ins[T_MXU_M_REG_MSB:T_MXU_M_REG_LSB] != 5'd0;

  // derived values (64-bit arithmetic: nothing wraps)
  logic        fp8, bf16;
  logic [63:0] vK, vM, vN, vKw, vNT, vKT, vKTE, veb;
  logic [63:0] a_ext, w_ext, cs_ext, rs_ext;
  logic        bad_op, bad_space, bad_field, bad_range;
  logic [31:0] bad_addr;
  always_comb begin
    fp8  = (a_dt == 5'(DTYPE_E4M3)) && (w_dt == 5'(DTYPE_E4M3));
    bf16 = (a_dt == 5'(DTYPE_BF16)) && (w_dt == 5'(DTYPE_BF16));
    veb  = bf16 ? 64'd2 : 64'd1;
    vK   = 64'(a_n0);
    vM   = m_en ? r_m : 64'(a_n1);
    vKw  = i_trans ? 64'(w_n0) : 64'(w_n1);
    vN   = i_trans ? 64'(w_n1) : 64'(w_n0);
    vNT  = (vN + 64'(DIM) - 64'd1) / 64'(DIM);
    vKTE = bf16 ? 64'(2 * DIM) : 64'(4 * DIM);
    vKT  = (vK + vKTE - 64'd1) / vKTE;
    a_ext  = r_a + (vM - 64'd1) * 64'(a_s1) + vK * veb;
    w_ext  = i_trans ? (r_w + (vN - 64'd1) * 64'(w_s1) + vK * veb) : (r_w + (vK - 64'd1) * 64'(w_s1) + vN * veb);
    cs_ext = r_cs + 64'd4 * vKT * vN;
    rs_ext = r_rs + 64'd4 * vM;
    bad_op    = ins[127:120] != OP_MXU_MM;
    bad_space = (a_sp != SPACE_SPM) || (w_sp != SPACE_SPM);
    bad_field = !(fp8 || bf16) || (i_mode != 3'd0) || (i_accm == 2'd3) || (i_accm == 2'd2 && !rs_en) ||
                (a_fl != 8'd0) || (w_fl != 8'd0) || (a_n2 != 16'd1) || (a_n3 != 16'd1) || (w_n2 != 16'd1) || (w_n3 != 16'd1) ||
                (vK != vKw) || (vK == 64'd0) || (vN == 64'd0) || (vM == 64'd0) ||
                a_s1[31] || w_s1[31] ||
                ((r_a % 64'(LB)) != 0) || ((r_w % 64'(LB)) != 0) || ((64'(a_s1) % 64'(LB)) != 0) || ((64'(w_s1) % 64'(LB)) != 0) ||
                (cs_en && (((r_cs % 64'(LB)) != 0) || ((64'd4 * vN) % 64'(LB) != 0))) || (rs_en && ((r_rs % 64'(LB)) != 0));
    bad_range = (vM > 64'(ACC_ROWS)) || (r_acc + vNT * vM > 64'(ACC_ROWS)) || (r_a >= 64'(SPM_BYTES)) || (r_w >= 64'(SPM_BYTES)) ||
                (a_ext > 64'(SPM_BYTES)) || (w_ext > 64'(SPM_BYTES)) || (cs_en && (cs_ext > 64'(SPM_BYTES))) ||
                (rs_en && (i_accm == 2'd2) && (rs_ext > 64'(SPM_BYTES)));
    bad_addr  = (r_acc + vNT * vM > 64'(ACC_ROWS) || vM > 64'(ACC_ROWS)) ? 32'(r_acc) :
                (a_ext > 64'(SPM_BYTES) || r_a >= 64'(SPM_BYTES)) ? 32'(r_a) :
                (w_ext > 64'(SPM_BYTES) || r_w >= 64'(SPM_BYTES)) ? 32'(r_w) :
                (cs_en && cs_ext > 64'(SPM_BYTES)) ? 32'(r_cs) : 32'(r_rs);
  end

  // instruction context handed to the load FSM
  logic        ctx_v, ctx_take;
  logic        c_trans, c_bf16, c_cs_en;
  logic [1:0]  c_accm;
  logic [15:0] c_K, c_M, c_N, c_KT, c_NT;
  logic [21:0] c_a_base, c_a_pitch, c_w_base, c_w_pitch, c_cs_base, c_rs_base;
  logic [10:0] c_acc_base;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      dst <= D_IDLE;
      fault_valid <= 1'b0;
      fault_cause <= 4'd0;
      fault_addr  <= 32'd0;
      ctx_v <= 1'b0;
      inf_push <= 1'b0;
    end else begin
      inf_push <= 1'b0;
      if (ctx_take) ctx_v <= 1'b0;
      case (dst)
        D_IDLE: if (q_ready) dst <= D_TDA_REQ;
        D_TDA_REQ: if (dtab_gnt) dst <= D_TDA_WAIT;
        D_TDA_WAIT: if (dtab_rvalid) dst <= D_TDW_REQ;
        D_TDW_REQ: if (dtab_gnt) dst <= D_TDW_WAIT;
        D_TDW_WAIT: if (dtab_rvalid) dst <= D_CHECK;
        D_CHECK: begin
          if (bad_op || bad_space || bad_field || bad_range) begin
            fault_valid <= 1'b1;
            fault_cause <= bad_op ? FAULT_CAUSE_ILLEGAL_OP : bad_space ? FAULT_CAUSE_TD_SPACE :
                           bad_field ? FAULT_CAUSE_ILLEGAL_FIELD : FAULT_CAUSE_RANGE;
            fault_addr  <= (bad_op || bad_space || bad_field) ? 32'd0 : bad_addr;
            dst <= D_FAULT;
          end else dst <= D_PUSH;
        end
        D_PUSH: if (!ctx_v && lst == L_IDLE) begin
          ctx_v <= 1'b1;
          inf_push <= 1'b1;
          dst <= D_IDLE;
        end
        default: ;
      endcase
    end
  end
  // the context is written in D_PUSH (same edge as ctx_v rises)
  always_ff @(posedge clk) begin
    if (dst == D_IDLE && q_ready) begin
      ins   <= q_instr;
      r_a   <= q_regs[64*0 +: 64];
      r_w   <= q_regs[64*1 +: 64];
      r_acc <= q_regs[64*2 +: 64];
      r_m   <= q_regs[64*3 +: 64];
      r_rs  <= q_regs[64*4 +: 64];
      r_cs  <= q_regs[64*5 +: 64];
    end
    if (dst == D_TDA_WAIT && dtab_rvalid) td_a <= dtab_data;
    if (dst == D_TDW_WAIT && dtab_rvalid) td_w <= dtab_data;
    if (dst == D_PUSH && !ctx_v && lst == L_IDLE) begin
      c_trans    <= i_trans;
      c_bf16     <= bf16;
      c_cs_en    <= cs_en;
      c_accm     <= i_accm;
      c_K        <= 16'(vK);
      c_M        <= 16'(vM);
      c_N        <= 16'(vN);
      c_KT       <= 16'(vKT);
      c_NT       <= 16'(vNT);
      c_a_base   <= 22'(r_a);
      c_a_pitch  <= 22'(a_s1);
      c_w_base   <= 22'(r_w);
      c_w_pitch  <= 22'(w_s1);
      c_cs_base  <= 22'(r_cs);
      c_rs_base  <= 22'(r_rs);
      c_acc_base <= 11'(r_acc);
    end
  end
  assign dtab_req = (dst == D_TDA_REQ) || (dst == D_TDW_REQ);
  assign dtab_idx = (dst == D_TDA_REQ) ? ins[T_MXU_A_TD_MSB:T_MXU_A_TD_LSB] : ins[T_MXU_W_TD_MSB:T_MXU_W_TD_LSB];

  // in-flight FIFO
  logic [9:0] inf_in;
  assign inf_in = {ins[SYNC_SSEM_MSB:SYNC_SSEM_LSB], ins[SYNC_SINC_MSB:SYNC_SINC_LSB]};
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
  assign sem_inc_valid = acc_done && (inf_cnt != 3'd0) && (inf_head[9:4] != 6'd0) && (inf_head[3:0] != 4'd0);
  assign sem_inc_idx   = inf_head[9:4];
  assign sem_inc_amt   = inf_head[3:0];

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
  assign w_gnt = w_want && w_req_ready;
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
    t_cs_addr = 22'(64'(c_cs_base) + 64'd4 * (64'(l_t) * 64'(c_N) + 64'(DIM) * 64'(l_j)));
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
  assign unused_first = &{1'b0, x_first, td_a[255:112], td_w[255:112], ar_ncols[7]};

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
endmodule
