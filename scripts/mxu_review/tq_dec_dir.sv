// Review part T (test quality): directed edge cases of the legality checks, on mxu_dec alone (DIM 8, SPM 128 KiB, 256 ACC rows,
// the sizes of tb_mxu). Each case is one MXU_MM after a reset; the result is the fault cause, "ok" (a context was pushed) or
// "not taken" (the instruction never left the queue head within 40 cycles). Expected values are written from mxu.md 4.1 by hand
// (no arith_ref, no gen_mxu_vectors.py). Run by scripts/mxu_review/tq_dec_dir.sh on the RTL and on mutated copies.
`timescale 1ns/1ps
import isa_pkg::*;
module tq_dec_dir;
  localparam int SPM = 131072, ACCR = 256;
  logic clk = 0, rst_n = 0;
  always #5 clk = ~clk;
  logic q_valid = 0, q_ready;
  logic [127:0] q_instr = '0;
  logic [6*64-1:0] q_regs = '0;
  logic [31:0] q_wval = '0, sem_rd_val = '0;
  logic [5:0] sem_rd_idx;
  logic dtab_req, dtab_gnt = 0, dtab_rvalid = 0;
  logic [7:0] dtab_idx;
  logic [255:0] dtab_data = '0, td_a, td_w;
  logic ctx_push, z_inc, fault_valid;
  logic [9:0] ctx_sync;
  logic [3:0] fault_cause;
  logic [63:0] fault_addr;
  logic c_trans, c_bf16, c_cs_en;
  logic [1:0] c_accm;
  logic [15:0] c_K, c_M, c_N, c_KT, c_NT;
  logic [21:0] c_a_base, c_a_pitch, c_w_base, c_w_pitch, c_cs_base, c_rs_base, c_csp;
  logic [10:0] c_acc_base;
  mxu_dec #(.DIM(8), .ACC_ROWS(ACCR), .SPM_BYTES(SPM)) u (.*, .inf_room(1'b1), .inf_empty(1'b1), .ctx_ready(1'b1));

  always @(posedge clk) begin
    dtab_gnt <= dtab_req && !dtab_gnt && !dtab_rvalid;
    dtab_rvalid <= dtab_gnt;
    if (dtab_gnt) dtab_data <= (dtab_idx == 8'd1) ? td_a : td_w;
  end

  function automatic logic [255:0] td(input logic [4:0] dt, input int n0, input int n1, input int s1);
    td = '0;
    td[TD_DTYPE_MSB:TD_DTYPE_LSB] = dt;
    td[7:5] = SPACE_SPM;
    td[TD_N0_MSB:TD_N0_LSB] = 16'(n0);
    td[TD_N1_MSB:TD_N1_LSB] = 16'(n1);
    td[63:48] = 16'd1;                  // n2
    td[79:64] = 16'd1;                  // n3
    td[111:80] = 32'(s1);
  endfunction

  int errs = 0;
  // one instruction: FP8 / BF16, trans_w = 1, A TD 1, W TD 2; registers {a, w, acc, m, rs, cs}
  task automatic run(input string name, input string exp, input bit bf16, input int K, input int N, input int M,
                     input longint a, input longint w, input longint acc, input bit m_reg, input bit resc, input longint rs,
                     input int wsem, input logic [31:0] wval, input logic [31:0] semv);
    logic [4:0] dt;
    string got;
    dt = bf16 ? 5'(DTYPE_BF16) : 5'(DTYPE_E4M3);
    rst_n = 0; q_valid = 0;
    repeat (2) @(posedge clk);
    td_a = td(dt, K, m_reg ? 7 : M, ((K * (bf16 ? 2 : 1) + 63) / 64) * 64);
    td_w = td(dt, K, N, ((K * (bf16 ? 2 : 1) + 63) / 64) * 64);
    q_instr = '0;
    q_instr[127:120] = OP_MXU_MM;
    q_instr[SYNC_WSEM_MSB:SYNC_WSEM_LSB] = 6'(wsem);
    q_instr[T_MXU_A_TD_MSB:T_MXU_A_TD_LSB] = 8'd1;
    q_instr[T_MXU_W_TD_MSB:T_MXU_W_TD_LSB] = 8'd2;
    q_instr[T_MXU_A_REG_MSB:T_MXU_A_REG_LSB] = 5'd1;
    q_instr[T_MXU_W_REG_MSB:T_MXU_W_REG_LSB] = 5'd2;
    q_instr[T_MXU_ACC_REG_MSB:T_MXU_ACC_REG_LSB] = 5'd3;
    q_instr[T_MXU_M_REG_MSB:T_MXU_M_REG_LSB] = m_reg ? 5'd4 : 5'd0;
    q_instr[T_MXU_RS_REG_MSB:T_MXU_RS_REG_LSB] = resc ? 5'd5 : 5'd0;
    q_instr[T_MXU_ACC_MODE_MSB:T_MXU_ACC_MODE_LSB] = resc ? 2'd2 : 2'd0;
    q_instr[T_MXU_TRANS_W_MSB] = 1'b1;
    q_regs = {64'd0, 64'(rs), 64'(M), 64'(acc), 64'(w), 64'(a)};
    q_wval = wval; sem_rd_val = semv;
    #1 rst_n = 1; q_valid = 1;
    got = "not taken";
    for (int c = 0; c < 40; c++) begin
      @(posedge clk); #1;
      if (u.dst != 3'd0 || q_valid == 0) q_valid = 0;
      if (fault_valid) begin got = $sformatf("fault %0d addr %0h", fault_cause, fault_addr); break; end
      if (ctx_push) begin got = "ok"; break; end
    end
    if (got != exp) errs++;
    $display("CASE %-12s got %-28s exp %-28s %s", name, got, exp, (got == exp) ? "" : "<-- differs");
  endtask

  initial begin
    // name, expected, bf16, K, N, M, R[a], R[w], R[acc], m_reg, rescale, R[rs], wsem, wval, sem value
    run("a_at_end",   "ok", 0, 64, 8, 1, SPM - 64, 4096, 0, 0, 0, 0, 0, 0, 0);                       // A extent ends exactly at the SPM end
    run("w_at_end",   "ok", 0, 64, 8, 1, 0, SPM - 512, 0, 0, 0, 0, 0, 0, 0);                         // 8 W rows of 64 B end at the SPM end
    run("rs_at_end",  "ok", 0, 64, 8, 16, 0, 4096, 0, 1, 1, SPM - 64, 0, 0, 0);                      // 16 rowscales end at the SPM end
    run("acc_at_end", "ok", 0, 64, 8, 8, 0, 4096, ACCR - 8, 0, 0, 0, 0, 0, 0);                       // rows 248 .. 255
    run("bf16_a_over", $sformatf("fault 7 addr %0h", SPM - 64), 1, 64, 8, 1, SPM - 64, 4096, 0, 0, 0, 0, 0, 0, 0);   // 128 B from 64 B before the end
    run("nt_ragged",  $sformatf("fault 7 addr %0h", 64'h8000_0000_0000_00f7), 0, 64, 36, 2, 0, 4096, 247, 0, 0, 0, 0, 0, 0);  // NT = 5: 247 + 10 > 256
    run("wait_wrap",  "ok", 0, 64, 8, 1, 0, 4096, 0, 0, 0, 0, 1, 32'hFFFF_FFFE, 32'd2);              // sem 2 is 4 past wval 2^32 - 2
    $display("TQ_DEC_DIR %0d cases differ", errs);
    $finish;
  end
endmodule
