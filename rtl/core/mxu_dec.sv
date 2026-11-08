// mxu_dec: queue head, wait, DTAB reads and the legality checks of MXU_MM (docs/spec/mxu.md 4.1, 5). Hands a checked
// context (c_*) to the load FSM of `mxu` when `ctx_ready`; raises `fault_*` instead and stops for an illegal instruction.
// A separate module so that the control of `mxu` can be proved without these 64-bit address computations (tb/formal/mxu.sby).
module mxu_dec #(
  parameter int DIM       = 8,
  parameter int ACC_ROWS  = 2048,
  parameter int SPM_BYTES = 4194304,
  localparam int LB       = DIM
) (
  input  logic                clk,
  input  logic                rst_n,
  input  logic                q_valid,
  output logic                q_ready,
  input  logic [127:0]        q_instr,
  input  logic [6*64-1:0]     q_regs,
  input  logic [31:0]         q_wval,
  output logic [5:0]          sem_rd_idx,
  input  logic [31:0]         sem_rd_val,
  output logic                dtab_req,
  output logic [7:0]          dtab_idx,
  input  logic                dtab_gnt,
  input  logic                dtab_rvalid,
  input  logic [255:0]        dtab_data,
  input  logic                inf_room,      // the in-flight instruction FIFO has room
  input  logic                ctx_ready,     // the load FSM is idle and holds no context
  output logic                ctx_push,      // one cycle: c_* are written, the instruction is in flight
  output logic [9:0]          ctx_sync,      // {ssem, sinc} of the instruction (valid with ctx_push)
  output logic                c_trans,
  output logic                c_bf16,
  output logic                c_cs_en,
  output logic [1:0]          c_accm,
  output logic [15:0]         c_K, c_M, c_N, c_KT, c_NT,
  output logic [21:0]         c_a_base, c_a_pitch, c_w_base, c_w_pitch, c_cs_base, c_rs_base,
  output logic [10:0]         c_acc_base,
  output logic                fault_valid,
  output logic [3:0]          fault_cause,
  output logic [31:0]         fault_addr
);
  import isa_pkg::*;

  typedef enum logic [3:0] {D_IDLE, D_TDA_REQ, D_TDA_WAIT, D_TDW_REQ, D_TDW_WAIT, D_CHECK, D_PUSH, D_FAULT} dstate_t;
  dstate_t dst;

  logic [127:0] ins;
  logic [63:0]  r_a, r_w, r_acc, r_m, r_rs, r_cs;
  logic [255:0] td_a, td_w;

  logic [5:0] f_wsem;
  assign f_wsem     = q_instr[SYNC_WSEM_MSB -: 6];
  assign sem_rd_idx = f_wsem;
  logic sem_ok;
  assign sem_ok = (f_wsem == 6'd0) || (32'($signed(sem_rd_val - q_wval)) < 32'h8000_0000);

  assign q_ready = (dst == D_IDLE) && q_valid && sem_ok && inf_room && !fault_valid;

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


  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      dst <= D_IDLE;
      fault_valid <= 1'b0;
      fault_cause <= 4'd0;
      fault_addr  <= 32'd0;
    end else begin
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
        D_PUSH: if (ctx_ready) dst <= D_IDLE;
        default: ;
      endcase
    end
  end
  // the context is written in D_PUSH when the load FSM can take it
  assign ctx_push = (dst == D_PUSH) && ctx_ready;
  assign ctx_sync = {ins[SYNC_SSEM_MSB:SYNC_SSEM_LSB], ins[SYNC_SINC_MSB:SYNC_SINC_LSB]};
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
    if (ctx_push) begin
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

  logic unused_td;
  assign unused_td = &{1'b0, td_a[255:112], td_w[255:112]};
endmodule
