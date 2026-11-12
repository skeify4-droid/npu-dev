// mxu_dec: queue head, wait, DTAB reads and the legality checks of MXU_MM (docs/spec/mxu.md 4.1, 5; decisions M-10 .. M-18).
// Hands a checked context (c_*) to the load FSM of `mxu` when `ctx_ready`; raises `fault_*` instead and stops for an illegal
// instruction. An instruction with M = 0 passes every check, reads and writes nothing, and completes (`z_inc`, with `ctx_sync`)
// once nothing is in flight, i.e. in order after the earlier instructions' acc_done.
// A separate module so that the control of `mxu` can be proved without these 64-bit address computations (tb/formal/mxu.sby).
// the import is outside the module because the Yosys front end of tb/formal/mxu_dec.sby does not take it inside
// verilator lint_off IMPORTSTAR
import isa_pkg::*;
// verilator lint_on IMPORTSTAR
module mxu_dec #(
  parameter int DIM       = 8,
  parameter int ACC_ROWS  = 2048,
  parameter int SPM_BYTES = 4194304
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
  input  logic                inf_empty,     // the in-flight instruction FIFO is empty
  input  logic                ctx_ready,     // the load FSM is idle and holds no context
  output logic                ctx_push,      // one cycle: c_* are written, the instruction is in flight
  output logic                z_inc,         // one cycle: an M = 0 instruction completes; sem_inc {ssem, sinc} = ctx_sync
  output logic [9:0]          ctx_sync,      // {ssem, sinc} of the instruction (valid with ctx_push and z_inc)
  output logic                c_trans,
  output logic                c_bf16,
  output logic                c_cs_en,
  output logic [1:0]          c_accm,
  output logic [15:0]         c_K, c_M, c_N, c_KT, c_NT,
  output logic [21:0]         c_a_base, c_a_pitch, c_w_base, c_w_pitch, c_cs_base, c_rs_base,
  output logic [21:0]         c_csp,         // row pitch of the column-scale array, roundup(4 N, 64) bytes (M-11)
  output logic [10:0]         c_acc_base,
  output logic                fault_valid,
  output logic [3:0]          fault_cause,
  output logic [63:0]         fault_addr
);
  if (DIM != 8 && DIM != 16 && DIM != 32 && DIM != 64) begin : g_bad_dim
    $error("mxu_dec: DIM must be 8, 16, 32 or 64");
  end
  if (ACC_ROWS < 2 || ACC_ROWS > 2048) begin : g_bad_acc_rows
    $error("mxu_dec: ACC_ROWS must be 2 .. 2048");                 // c_acc_base is 11 bits; the narrowed extents use clog2(ACC_ROWS) bits
  end
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

  // ---- derived values (M-24, review I-3). No divider and no wide multiplier: every divisor is a power of two (shift), and an
  // extent is only looked at under the M-18 priority, i.e. after the word, space, descriptor and alignment checks and the ACC
  // RANGE check have passed. At that point K, N are in 1 .. 65535 (descriptor check; they are 16-bit TD fields), s1 >= 0
  // (alignment check: bit 31 clear), and M <= ACC_ROWS (ACC check). The products below are exact under those conditions; where a
  // condition does not hold an earlier flag in the priority chain wins, so the value of the later flag is never used. The sums
  // stay 96 bits wide (a 64-bit register + a product below 2^47 + 2^17 cannot wrap).
  localparam int LD  = $clog2(DIM);
  localparam int RW  = $clog2(ACC_ROWS + 1);          // bits of M and R[acc_reg] once they are <= ACC_ROWS (12 at 2048)
  localparam int MW  = $clog2(ACC_ROWS);              // bits of M - 1, 1 <= M <= ACC_ROWS (11)
  localparam int NTW = 17 - LD;                       // bits of ceil(65535 / DIM)
  logic        fp8, bf16;
  logic [63:0] vM;
  logic [15:0] vK, vN, vKw, vKT;                      // K <= 65535 and a K tile has at least 16 elements
  logic [NTW-1:0] vNT;
  logic [9:0]  vKTF;                                  // ceil(K / 128 or 256) <= 513
  logic [18:0] vCSP;                                  // roundup(4 N, 64) <= 2^18
  logic [12:0] csp_q;
  logic [95:0] a_ext, w_ext, cs_ext, rs_ext;          // end (exclusive) of the bytes of each operand
  logic [16:0] ka_b, wk_b;                            // K * bytes per element, the second W extent term
  logic [15:0] w_rows, w_elem, w_rm1;                 // W: rows along s1 and elements per row (trans_w swaps K and N)
  logic [MW-1:0] a_m1;
  logic [MW+30:0] a_prod;
  logic [46:0] w_prod;
  logic [9:0]  cs_rm1;
  logic [28:0] cs_prod;
  logic [NTW+RW:0] acc_sum;                           // R[acc_reg] + NT M on its low bits
  logic        m_zero;
  assign m_zero = (vM == 64'd0);
  // M-18 order: word fields, TD_SPACE (A, W), descriptor rows (no address), alignment (A, W, s1 A, s1 W, cs, rs), RANGE (ACC, A, W, cs, rs)
  logic        bad_op, f_word, f_sp_a, f_sp_w, f_desc, f_al_a, f_al_w, f_al_sa, f_al_sw, f_al_cs, f_al_rs;
  logic        f_r_acc, f_r_a, f_r_w, f_r_cs, f_r_rs;
  logic        bad_any;
  logic [3:0]  bad_cause;
  logic [63:0] bad_addr;
  always_comb begin
    fp8  = (a_dt == 5'(DTYPE_E4M3)) && (w_dt == 5'(DTYPE_E4M3));
    bf16 = (a_dt == 5'(DTYPE_BF16)) && (w_dt == 5'(DTYPE_BF16));
    vK   = a_n0;
    vM   = m_en ? r_m : 64'(a_n1);               // R[m_reg] as a 64-bit unsigned number, compared in full (M-14)
    vKw  = i_trans ? w_n0 : w_n1;
    vN   = i_trans ? w_n1 : w_n0;
    vNT  = NTW'((17'(vN) + 17'(DIM - 1)) >> LD);
    vKT  = 16'((17'(vK) + (bf16 ? 17'(2 * DIM - 1) : 17'(4 * DIM - 1))) >> (bf16 ? LD + 1 : LD + 2));   // K tiles (passes) of the MXU
    // fold tile of the column scales (128 / 256), the same in every config (M-13): rows of the column-scale array
    vKTF = 10'((17'(vK) + (bf16 ? 17'd127 : 17'd255)) >> (bf16 ? 7 : 8));
    csp_q = 13'((19'(vN) * 19'd4 + 19'd63) >> 6);
    vCSP = {csp_q, 6'd0};                        // roundup(4 N, 64)
    ka_b = bf16 ? {vK, 1'b0} : {1'b0, vK};
    // A: (M - 1) x s1 on MW x 31 bits (M <= ACC_ROWS, s1 >= 0)
    a_m1   = MW'(vM - 64'd1);
    a_prod = a_m1 * a_s1[30:0];
    a_ext  = 96'(r_a) + 96'(a_prod) + 96'(ka_b);
    // W: one product, its operands muxed by trans_w (rows >= 1 after the descriptor check)
    w_rows = i_trans ? vN : vK;
    w_elem = i_trans ? vK : vN;
    wk_b   = bf16 ? {w_elem, 1'b0} : {1'b0, w_elem};
    w_rm1  = w_rows - 16'd1;
    w_prod = w_rm1 * w_s1[30:0];
    w_ext  = 96'(r_w) + 96'(w_prod) + 96'(wk_b);
    // cs: (rows of the scale array - 1) x CSP on 10 x 19 bits
    cs_rm1  = vKTF - 10'd1;
    cs_prod = cs_rm1 * vCSP;
    cs_ext = 96'(r_cs) + 96'(cs_prod) + 96'(vN) * 96'd4;
    rs_ext = 96'(r_rs) + 96'({vM[RW-1:0], 2'd0});      // 4 M, M <= ACC_ROWS

    bad_op  = ins[127:120] != OP_MXU_MM;
    f_word  = (ins[38:0] != 39'd0) || (i_mode != 3'd0) || (i_accm == 2'd3) || (i_accm == 2'd2 ? !rs_en : rs_en);
    f_sp_a  = (a_sp != SPACE_SPM);
    f_sp_w  = (w_sp != SPACE_SPM);
    f_desc  = !(fp8 || bf16) || (a_fl != 8'd0) || (w_fl != 8'd0) || (a_n2 != 16'd1) || (a_n3 != 16'd1) || (w_n2 != 16'd1) || (w_n3 != 16'd1) ||
              (vK == 16'd0) || (vK != vKw) || (vN == 16'd0);
    // 64 B in every config (the SPM bank width, M-10); N has no rule (M-11); a negative s1 is an alignment fault of that s1
    f_al_a  = (r_a[5:0] != 6'd0);
    f_al_w  = (r_w[5:0] != 6'd0);
    f_al_sa = a_s1[31] || (a_s1[5:0] != 6'd0);
    f_al_sw = w_s1[31] || (w_s1[5:0] != 6'd0);
    f_al_cs = cs_en && (r_cs[5:0] != 6'd0);
    f_al_rs = rs_en && (r_rs[5:0] != 6'd0);
    // RANGE: the ACC rows first (R[acc_reg] + NT M in 64 bits without a wrap: each term is compared before the sum is trusted),
    // then the operand extents (review S-10): the bytes an operand has, not the lanes the MXU reads. An operand with no bytes
    // (A and the rowscale of an M = 0 instruction) cannot be out of range. Lanes the MXU reads past an extent are discarded: they
    // stay inside the 64 B granule of the extent, except the cs window (4 DIM B, DIM >= 32) and the BF16 non-trans window
    // (2 DIM B, DIM = 64), which wrap in the 22-bit SPM address (mxu.md 4.1).
    acc_sum = (NTW+RW+1)'(r_acc[RW-1:0]) + (NTW+RW+1)'(vNT * vM[RW-1:0]);   // meaningful when the first two terms are false
    f_r_acc = (r_acc > 64'(ACC_ROWS)) || (vM > 64'(ACC_ROWS)) || (acc_sum > (NTW+RW+1)'(ACC_ROWS));
    f_r_a   = !m_zero && (a_ext > 96'(SPM_BYTES));
    f_r_w   = (w_ext > 96'(SPM_BYTES));
    f_r_cs  = cs_en && (cs_ext > 96'(SPM_BYTES));
    f_r_rs  = rs_en && !m_zero && (rs_ext > 96'(SPM_BYTES));

    bad_any   = 1'b1;
    bad_cause = FAULT_CAUSE_ILLEGAL_FIELD;
    bad_addr  = 64'd0;
    if (bad_op)        bad_cause = FAULT_CAUSE_ILLEGAL_OP;
    else if (f_word)   begin end
    else if (f_sp_a)   begin bad_cause = FAULT_CAUSE_TD_SPACE; bad_addr = r_a; end
    else if (f_sp_w)   begin bad_cause = FAULT_CAUSE_TD_SPACE; bad_addr = r_w; end
    else if (f_desc)   begin end
    else if (f_al_a)   bad_addr = r_a;
    else if (f_al_w)   bad_addr = r_w;
    else if (f_al_sa)  bad_addr = r_a;
    else if (f_al_sw)  bad_addr = r_w;
    else if (f_al_cs)  bad_addr = r_cs;
    else if (f_al_rs)  bad_addr = r_rs;
    else if (f_r_acc)  begin bad_cause = FAULT_CAUSE_RANGE; bad_addr = r_acc | 64'h8000_0000_0000_0000; end
    else if (f_r_a)    begin bad_cause = FAULT_CAUSE_RANGE; bad_addr = r_a; end
    else if (f_r_w)    begin bad_cause = FAULT_CAUSE_RANGE; bad_addr = r_w; end
    else if (f_r_cs)   begin bad_cause = FAULT_CAUSE_RANGE; bad_addr = r_cs; end
    else if (f_r_rs)   begin bad_cause = FAULT_CAUSE_RANGE; bad_addr = r_rs; end
    else               bad_any = 1'b0;
  end


  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      dst <= D_IDLE;
      fault_valid <= 1'b0;
      fault_cause <= 4'd0;
      fault_addr  <= 64'd0;
    end else begin
      case (dst)
        D_IDLE: if (q_ready) dst <= D_TDA_REQ;
        D_TDA_REQ: if (dtab_gnt) dst <= D_TDA_WAIT;
        D_TDA_WAIT: if (dtab_rvalid) dst <= D_TDW_REQ;
        D_TDW_REQ: if (dtab_gnt) dst <= D_TDW_WAIT;
        D_TDW_WAIT: if (dtab_rvalid) dst <= D_CHECK;
        D_CHECK: begin
          if (bad_any) begin
            fault_valid <= 1'b1;
            fault_cause <= bad_cause;
            fault_addr  <= bad_addr;
            dst <= D_FAULT;
          end else dst <= D_PUSH;
        end
        D_PUSH: if (m_zero ? inf_empty : ctx_ready) dst <= D_IDLE;
        default: ;
      endcase
    end
  end
  // the context is written in D_PUSH when the load FSM can take it
  assign ctx_push = (dst == D_PUSH) && !m_zero && ctx_ready;
  // M = 0 (M-14): no context, no FIFO entry; it retires when every earlier instruction has (the FIFO is empty), which is in order
  assign z_inc    = (dst == D_PUSH) && m_zero && inf_empty;
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
      c_K        <= vK;
      c_M        <= 16'(vM);
      c_N        <= vN;
      c_KT       <= vKT;
      c_NT       <= 16'(vNT);
      c_a_base   <= 22'(r_a);
      c_a_pitch  <= 22'(a_s1);
      c_w_base   <= 22'(r_w);
      c_w_pitch  <= 22'(w_s1);
      c_cs_base  <= 22'(r_cs);
      c_csp      <= 22'(vCSP);
      c_rs_base  <= 22'(r_rs);
      c_acc_base <= 11'(r_acc);
    end
  end
  assign dtab_req = (dst == D_TDA_REQ) || (dst == D_TDW_REQ);
  assign dtab_idx = (dst == D_TDA_REQ) ? ins[T_MXU_A_TD_MSB:T_MXU_A_TD_LSB] : ins[T_MXU_W_TD_MSB:T_MXU_W_TD_LSB];

  logic unused_td;
  assign unused_td = &{1'b0, td_a[255:112], td_w[255:112]};

`ifdef FORMAL
  // ================================================================== formal (tb/formal/mxu_dec.sby; spec mxu.md 6)
  // The real decoder alone, every input free except: reset is low for the first two cycles and high afterwards. The shadow
  // registers below take the instruction, R[m_reg] and the TD n1 field straight from the inputs, so the properties do not
  // read the decoder's own latches. They say what the legality checks and the DIM-independent handshake of the M = 0 path
  // (M-14: no context, no FIFO entry, completes only with an empty FIFO) must do; the arithmetic of the checks is tb_mxu's.
  reg [1:0] f_rc = 2'd0;
  always @(posedge clk) if (f_rc != 2'd3) f_rc <= f_rc + 2'd1;
  always @(*) begin
    if (f_rc < 2'd2) assume (!rst_n);
    else             assume (rst_n);
  end

  wire        f_acc  = (dst == D_IDLE) && q_ready;                  // an instruction is taken
  wire        f_chk  = (dst == D_CHECK);
  wire        f_done = ctx_push || z_inc || (f_chk && bad_any);     // it leaves the decoder: context, M = 0 completion or fault
  reg [127:0] f_ins;
  reg [63:0]  f_rm;
  reg [15:0]  f_n1;
  reg [1:0]   f_out = 2'd0;                                         // taken minus left
  reg         f_pdone = 1'b0, f_pstall = 1'b0, f_pbad = 1'b0, f_pok = 1'b0, f_ppush = 1'b0;   // previous cycle: left D_CHECK with / without a fault
  always @(posedge clk) begin
    if (f_acc) begin
      f_ins <= q_instr;
      f_rm  <= q_regs[64*3 +: 64];
    end
    if (dst == D_TDA_WAIT && dtab_rvalid) f_n1 <= dtab_data[TD_N1_MSB:TD_N1_LSB];
    if (!rst_n) begin
      f_out <= 2'd0; f_pdone <= 1'b0; f_pstall <= 1'b0; f_pbad <= 1'b0; f_pok <= 1'b0; f_ppush <= 1'b0;
    end else begin
      f_out    <= f_out + {1'b0, f_acc} - {1'b0, f_done};
      f_pdone  <= ctx_push || z_inc;
      f_pstall <= (dst == D_PUSH) && m_zero && !inf_empty;
      f_pbad   <= f_chk && bad_any;
      f_pok    <= f_chk && !bad_any;
      f_ppush  <= (dst == D_PUSH);
    end
  end
  wire [63:0] f_mval = (f_ins[T_MXU_M_REG_MSB:T_MXU_M_REG_LSB] != 5'd0) ? f_rm : 64'(f_n1);   // M of the instruction, 64 bits

  always @(*) begin
    if (rst_n && f_rc == 2'd3) begin
      // one outcome per instruction taken: a context, an M = 0 completion or a fault, never two
      assert (!(z_inc && ctx_push));
      assert (f_out <= 2'd1);
      assert (!f_done || f_out == 2'd1);
      assert (!f_pdone || dst == D_IDLE);
      // M = 0: completes only in order (FIFO empty), never pushes a context, M is R[m_reg] in 64 bits or n1 of the TD
      assert (!z_inc || (dst == D_PUSH && inf_empty));
      assert (!z_inc || f_mval == 64'd0);
      assert (!ctx_push || (ctx_ready && f_mval != 64'd0));
      assert (!f_pstall || dst == D_PUSH);                              // an M = 0 that found the FIFO not empty is still there next cycle (it leaves only through z_inc)
      // sem_inc fields
      assert (!(z_inc || ctx_push) || ctx_sync == {f_ins[SYNC_SSEM_MSB:SYNC_SSEM_LSB], f_ins[SYNC_SINC_MSB:SYNC_SINC_LSB]});
      // every instruction, M = 0 included, is checked: a fault stops in D_FAULT, D_PUSH is entered only from a check without one
      // (stated on the previous cycle's result, so that the solver does not compare two copies of the 96-bit address arithmetic)
      assert (!f_pbad || (dst == D_FAULT && fault_valid));
      assert (dst != D_PUSH || f_pok || f_ppush);
      // with no bytes, A and the rowscale of an M = 0 instruction cannot fault
      assert (!m_zero || (!f_r_a && !f_r_rs));
    end
  end

  // reachability: an M = 0 completion that waited and one that did not, from the register and from the TD, a context, a fault of an M = 0 instruction
  always @(*) begin
    if (rst_n && f_rc == 2'd3) begin
      cover (z_inc && f_pstall);
      cover (z_inc && !f_pstall && f_ins[T_MXU_M_REG_MSB:T_MXU_M_REG_LSB] != 5'd0);
      cover (z_inc && !f_pstall && f_ins[T_MXU_M_REG_MSB:T_MXU_M_REG_LSB] == 5'd0);
      cover (ctx_push);
      cover (f_chk && bad_any && m_zero);
    end
  end
`endif
endmodule
