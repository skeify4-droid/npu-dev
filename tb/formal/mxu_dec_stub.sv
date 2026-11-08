// Formal-only replacement of mxu_dec (tb/formal/mxu.sby): instead of decoding an instruction it pushes a free, legal
// context whenever the load FSM can take one. Legal means what mxu_dec's checks guarantee: M, K, N >= 1, K and N
// consistent with the K / N tile counts, ACC rows in range, acc_mode <= 2. The sizes are bounded by MMAX, KTMAX, NTMAX
// (-define in the .sby) to keep the state space small. No faults, no queue.
`ifndef MMAX
`define MMAX 2
`endif
`ifndef KTMAX
`define KTMAX 2
`endif
`ifndef NTMAX
`define NTMAX 2
`endif
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
  input  logic                inf_room,
  input  logic                inf_empty,
  input  logic                ctx_ready,
  output logic                ctx_push,
  output logic                z_inc,
  output logic [9:0]          ctx_sync,
  output logic                c_trans,
  output logic                c_bf16,
  output logic                c_cs_en,
  output logic [1:0]          c_accm,
  output logic [15:0]         c_K, c_M, c_N, c_KT, c_NT,
  output logic [21:0]         c_a_base, c_a_pitch, c_w_base, c_w_pitch, c_cs_base, c_rs_base, c_csp,
  output logic [10:0]         c_acc_base,
  output logic                fault_valid,
  output logic [3:0]          fault_cause,
  output logic [63:0]         fault_addr
);
  (* anyseq *) wire        f_push;
  (* anyseq *) wire        f_trans, f_bf16, f_cs;
  (* anyseq *) wire [1:0]  f_accm;
  (* anyseq *) wire [1:0]  f_M, f_KT, f_NT;      // narrow: the bounds below fit 2 bits, and the upper context bits are constant 0
  (* anyseq *) wire [4:0]  f_acc;
  (* anyseq *) wire [9:0]  f_sync;
  (* anyseq *) wire [15:0] f_Kx, f_Nx;
  (* anyseq *) wire [21:0] f_addr;
  // K and N do not influence any property (they only shape lane valid bits and byte masks), so they are free here.
  always @(*) begin
    assume (f_M >= 2'd1 && f_M <= 2'(`MMAX));
    assume (f_KT >= 2'd1 && f_KT <= 2'(`KTMAX));
    assume (f_NT >= 2'd1 && f_NT <= 2'(`NTMAX));
    assume (f_accm <= 2'd2);
    assume (f_acc <= 5'd16);                         // ACC rows far from the 11-bit limit
    assume ({7'd0, f_acc} + {10'd0, f_NT} * {10'd0, f_M} <= 12'(ACC_ROWS));
  end

  assign q_ready = 1'b0;
  assign sem_rd_idx = 6'd0;
  assign dtab_req = 1'b0;
  assign dtab_idx = 8'd0;
  assign fault_valid = 1'b0;
  assign fault_cause = 4'd0;
  assign fault_addr = 64'd0;
  assign z_inc = 1'b0;                              // the stub decoder never makes an M = 0 instruction (M >= 1 above)
  assign ctx_push = ctx_ready && f_push;

  always @(posedge clk) if (ctx_push) begin
    c_trans <= f_trans; c_bf16 <= f_bf16; c_cs_en <= f_cs; c_accm <= f_accm;
    c_K <= f_Kx; c_M <= {14'd0, f_M}; c_N <= f_Nx; c_KT <= {14'd0, f_KT}; c_NT <= {14'd0, f_NT};
    c_a_base <= f_addr; c_a_pitch <= f_addr; c_w_base <= f_addr; c_w_pitch <= f_addr; c_cs_base <= f_addr; c_rs_base <= f_addr; c_csp <= f_addr;
    c_acc_base <= {6'd0, f_acc};
  end
  assign ctx_sync = f_sync;
  initial begin c_M = 16'd0; c_KT = 16'd0; c_NT = 16'd0; c_acc_base = 11'd0; end   // constant upper bits for the solver

  logic unused;
  assign unused = &{1'b0, clk, rst_n, q_valid, q_instr, q_regs, q_wval, sem_rd_val, dtab_gnt, dtab_rvalid, dtab_data, inf_room, inf_empty, SPM_BYTES[0], DIM[0], ACC_ROWS[0]};
endmodule
