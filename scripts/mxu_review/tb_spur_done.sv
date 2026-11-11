// tb_spur_done (implementability review, finding I-10): one `acc_done` pulse while no instruction is in flight. mxu.sv:128 says such a
// pulse "is ignored"; this shows what inf_cnt and q_ready do. DIM 8, every other input idle.
// iverilog -g2012 -o x.vvp isa/gen/isa_pkg.sv rtl/core/*.sv rtl/arith/*.sv scripts/mxu_review/tb_spur_done.sv && vvp -n x.vvp
`timescale 1ns/1ps
module tb_spur_done;
  logic clk = 1'b0, rst_n = 1'b0;
  always #5 clk = ~clk;
  logic q_valid = 1'b0, acc_done = 1'b0;
  logic q_ready, dtab_req, s_req_v, acc_valid, acc_rescale, acc_overwrite, acc_use_cs, acc_last, sem_inc_valid, fault_valid;
  logic [5:0] sem_rd_idx, sem_inc_idx;
  logic [7:0] dtab_idx, w_req_v;
  logic [3:0] a_req_v, sem_inc_amt, fp_flags, fault_cause;
  logic [4*22-1:0] a_req_addr;
  logic [8*22-1:0] w_req_addr;
  logic [21:0] s_req_addr;
  logic [10:0] acc_row;
  logic [8*32-1:0] acc_part, acc_cs;
  logic [6:0] acc_ncols;
  logic [31:0] acc_rowscale;
  logic [63:0] fault_addr;
  mxu #(.DIM(8)) dut (.clk, .rst_n, .q_valid, .q_ready, .q_instr(128'd0), .q_regs('0), .q_wval(32'd0), .sem_rd_idx, .sem_rd_val(32'd0),
    .dtab_req, .dtab_idx, .dtab_gnt(1'b0), .dtab_rvalid(1'b0), .dtab_data(256'd0),
    .a_req_v, .a_req_addr, .a_req_ready(1'b0), .a_rsp_data('0), .w_req_v, .w_req_addr, .w_req_ready(1'b0), .w_rsp_data('0),
    .s_req_v, .s_req_addr, .s_req_ready(1'b0), .s_rsp_data('0),
    .acc_valid, .acc_row, .acc_part, .acc_ncols, .acc_rescale, .acc_overwrite, .acc_use_cs, .acc_rowscale, .acc_cs, .acc_last,
    .acc_done, .sem_inc_valid, .sem_inc_idx, .sem_inc_amt, .fp_flags, .fault_valid, .fault_cause, .fault_addr);
  initial begin
    repeat (2) @(posedge clk); #1 rst_n = 1'b1;
    @(posedge clk); #1;
    q_valid = 1'b1; #1;
    $display("before: inf_cnt %0d, q_ready with q_valid %b", dut.inf_cnt, q_ready);
    q_valid = 1'b0;
    acc_done = 1'b1; @(posedge clk); #1 acc_done = 1'b0;
    repeat (20) @(posedge clk); #1;
    q_valid = 1'b1; #1;
    $display("after one acc_done with nothing in flight, 20 cycles later: inf_cnt %0d, sem_inc_valid seen 0, q_ready with q_valid %b",
             dut.inf_cnt, q_ready);
    $finish;
  end
endmodule
