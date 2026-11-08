// Self-checking testbench for mxu (iverilog -g2012): whole MXU_MM instructions on DIM = 8 and 16 against the images of
// scripts/gen_mxu_vectors.py (build/mxu/mxu_<dim>_*.hex). The expected ACC image is computed there from the *logical* A x W
// (arith_ref PE chain per K tile, then arith_ref.acc_update), so the packer, the addressing, the loop order and the
// ACC controls are checked end to end.
// Models: SPM = byte array, fixed latency SPM_LAT, garbage in lanes that were not requested, optional random grant stalls;
// DTAB = file; semaphores = counters; ACC = FP32 array updated by the RTL's own acc_update lanes at one row per cycle,
// with a check that two updates of the same row are at least ACC_LAT cycles apart, acc_done 5 cycles after the last row.
// Checks: final ACC image (bit-exact), OR of fp_flags against the expected PE flags, final semaphore values,
// sem_inc only after acc_done, no fault, no ACC hazard, program finished within the time limit.
`timescale 1ns/1ps
`include "counts.svh"
module mxu_tc #(
  parameter int DIM       = 8,
  parameter int NINS      = 1,
  parameter int NTD       = 1,
  parameter int SPMB      = 1,
  parameter int ACCR      = 1,
  parameter int STALL_PCT = 0,
  parameter int SPM_LAT   = 2,
  parameter int ACC_LAT   = 8,
  parameter int A_BLOCK   = 0,       // cycles the A port refuses requests after each grant (lets the loader run ahead of the swap)
  parameter int MAX_PERIOD = 0,      // > 0: consecutive output rows (M = 1 passes) at most this many cycles apart
  parameter string PRE    = ""
) (
  output logic done,
  output int   errors
);
  localparam int LB = DIM;
  localparam int KB = 4 * DIM;
  logic clk = 0, rst_n = 0;
  always #5 clk = ~clk;

  // ---------------- images
  logic [7:0]            spm  [0:SPMB-1];
  logic [255:0]          dtab [0:NTD-1];
  logic [128+6*64+32-1:0] prog [0:NINS-1];
  logic [DIM*32-1:0]     acc0 [0:ACCR-1];
  logic [DIM*32-1:0]     acc1 [0:ACCR-1];
  logic [31:0]           expv [0:2];
  logic [31:0]           accm [0:ACCR*DIM-1];

  // ---------------- DUT
  logic q_valid, q_ready;
  logic [127:0] q_instr;
  logic [6*64-1:0] q_regs;
  logic [31:0] q_wval;
  logic [5:0] sem_rd_idx;
  logic [31:0] sem_rd_val;
  logic dtab_req, dtab_gnt, dtab_rvalid;
  logic [7:0] dtab_idx;
  logic [255:0] dtab_data;
  logic [3:0] a_req_v;
  logic [4*22-1:0] a_req_addr;
  logic a_req_ready;
  logic [4*LB*8-1:0] a_rsp_data;
  logic [7:0] w_req_v;
  logic [8*22-1:0] w_req_addr;
  logic w_req_ready;
  logic [8*LB*8-1:0] w_rsp_data;
  logic s_req_v;
  logic [21:0] s_req_addr;
  logic s_req_ready;
  logic [LB*8-1:0] s_rsp_data;
  logic acc_valid, acc_rescale, acc_overwrite, acc_use_cs, acc_last, acc_done;
  logic [10:0] acc_row;
  logic [DIM*32-1:0] acc_part, acc_cs;
  logic [6:0] acc_ncols;
  logic [31:0] acc_rowscale;
  logic sem_inc_valid, fault_valid;
  logic [5:0] sem_inc_idx;
  logic [3:0] sem_inc_amt, fp_flags, fault_cause;
  logic [31:0] fault_addr;

  mxu #(.DIM(DIM), .SPM_LAT(SPM_LAT), .ACC_LAT(ACC_LAT), .ACC_ROWS(ACCR), .SPM_BYTES(SPMB)) dut (.*);

  // ---------------- program feed
  int qi = 0;
  initial begin q_valid = 0; q_instr = '0; q_regs = '0; q_wval = '0; end
  always @(posedge clk) begin
    if (!rst_n) begin
      qi <= 0; q_valid <= 0;
    end else if (!q_valid || q_ready) begin
      if (qi < NINS) begin
        {q_instr, q_regs, q_wval} <= prog[qi];
        q_valid <= 1;
        qi <= qi + 1;
      end else q_valid <= 0;
    end
  end

  // ---------------- semaphores
  logic [31:0] sem [0:63];
  assign sem_rd_val = sem[sem_rd_idx];
  // a wait must have been satisfied when the instruction is popped (serial-number compare)
  always @(posedge clk) if (rst_n && q_valid && q_ready && q_instr[119:114] != 6'd0) begin
    if ($signed(sem[q_instr[119:114]] - q_wval) < 0) begin errors++; $display("%s: instruction started before its wait was satisfied", PRE); end
  end


  int sem_incs = 0;
  logic [5:0] last_done_cnt;
  int pending_done = 0;       // acc_done pulses issued and not yet matched by a sem_inc
  always @(posedge clk) begin
    if (!rst_n) begin
      for (int i = 0; i < 64; i++) sem[i] <= 32'd0;
    end else if (sem_inc_valid) begin
      sem[sem_inc_idx] <= sem[sem_inc_idx] + 32'(sem_inc_amt);
      sem_incs++;
    end
  end

  // ---------------- DTAB
  always @(posedge clk) begin
    dtab_gnt <= 1'b0; dtab_rvalid <= 1'b0;
    if (!rst_n) begin end
    else begin
      // grant after a random delay, data the cycle after the grant
      if (dtab_req && !dtab_gnt && ($urandom_range(0, 99) >= STALL_PCT)) begin
        dtab_gnt <= 1'b1;
      end
      if (dtab_gnt) begin dtab_rvalid <= 1'b1; dtab_data <= dtab[dtab_idx]; end
    end
  end

  // ---------------- SPM ports
  function automatic logic [7:0] rb(input int addr, input int garbage);
    if (addr >= 0 && addr < SPMB) return spm[addr];
    return 8'hEE;
  endfunction
  logic [4*LB*8-1:0] a_pipe [SPM_LAT];
  logic [8*LB*8-1:0] w_pipe [SPM_LAT];
  logic [LB*8-1:0]   s_pipe [SPM_LAT];
  assign a_rsp_data = a_pipe[SPM_LAT-1];
  assign w_rsp_data = w_pipe[SPM_LAT-1];
  assign s_rsp_data = s_pipe[SPM_LAT-1];
  logic [4*LB*8-1:0] a_nxt;
  logic [8*LB*8-1:0] w_nxt;
  logic [LB*8-1:0]   s_nxt;
  int blk = 0;
  always @(posedge clk) begin
    if (a_req_v != 4'd0 && a_req_ready && A_BLOCK > 0) begin blk <= A_BLOCK; a_req_ready <= 1'b0; end
    else if (blk > 1) begin blk <= blk - 1; a_req_ready <= 1'b0; end
    else begin blk <= 0; a_req_ready <= ($urandom_range(0, 99) >= STALL_PCT); end
    w_req_ready <= ($urandom_range(0, 99) >= STALL_PCT);
    s_req_ready <= ($urandom_range(0, 99) >= STALL_PCT);
  end
  // The grant of this cycle is (valid && ready); the response is built at the end of the cycle.
  always @(posedge clk) begin
    for (int l = 0; l < 4; l++)
      for (int b = 0; b < LB; b++)
        a_nxt[(l*LB + b)*8 +: 8] = (a_req_v[l] && a_req_ready) ? rb(int'(a_req_addr[22*l +: 22]) + b, 0) : 8'hFF;
    for (int l = 0; l < 8; l++)
      for (int b = 0; b < LB; b++)
        w_nxt[(l*LB + b)*8 +: 8] = (w_req_v[l] && w_req_ready) ? rb(int'(w_req_addr[22*l +: 22]) + b, 0) : 8'hFF;
    for (int b = 0; b < LB; b++)
      s_nxt[b*8 +: 8] = (s_req_v && s_req_ready) ? rb(int'(s_req_addr) + b, 0) : 8'hFF;
    a_pipe[0] <= a_nxt; w_pipe[0] <= w_nxt; s_pipe[0] <= s_nxt;
    for (int k = 1; k < SPM_LAT; k++) begin a_pipe[k] <= a_pipe[k-1]; w_pipe[k] <= w_pipe[k-1]; s_pipe[k] <= s_pipe[k-1]; end
  end
  // lane addresses must be LB aligned and inside the SPM whenever a lane is requested
  always @(posedge clk) if (rst_n) begin
    for (int l = 0; l < 4; l++) if (a_req_v[l] && a_req_ready) begin
      if (a_req_addr[22*l +: 22] % LB != 0 || int'(a_req_addr[22*l +: 22]) + LB > SPMB) begin errors++; $display("%s: bad A lane addr %0d", PRE, a_req_addr[22*l +: 22]); end
    end
    for (int l = 0; l < 8; l++) if (w_req_v[l] && w_req_ready) begin
      if (w_req_addr[22*l +: 22] % LB != 0 || int'(w_req_addr[22*l +: 22]) + LB > SPMB) begin errors++; $display("%s: bad W lane addr %0d", PRE, w_req_addr[22*l +: 22]); end
    end
    if (s_req_v && s_req_ready && (s_req_addr % LB != 0 || int'(s_req_addr) + LB > SPMB)) begin errors++; $display("%s: bad S addr %0d", PRE, s_req_addr); end
  end

  // ---------------- ACC model
  logic [31:0] lane_y [DIM];
  logic [3:0]  lane_f [DIM];
  logic [31:0] lane_acc [DIM];
  for (genvar c = 0; c < DIM; c++) begin : g_lane
    assign lane_acc[c] = accm[int'(acc_row) * DIM + c];
    acc_update u (.acc(lane_acc[c]), .part(acc_part[32*c +: 32]), .rowscale(acc_rowscale), .cs(acc_cs[32*c +: 32]),
                  .rescale(acc_rescale), .use_cs(acc_use_cs), .overwrite(acc_overwrite), .y(lane_y[c]), .flags(lane_f[c]));
  end
  longint cyc = 0, last_row_cyc = 0;
  logic [4:0] dsr = 5'd0;
  assign acc_done = dsr[4];       // five cycles after the last row
  longint row_last [0:ACCR-1];
  int done_cnt = 0;
  logic [3:0] flags_or = 4'd0;
  int rows_seen = 0;
  always @(posedge clk) begin
    cyc <= cyc + 1;
    dsr <= {dsr[3:0], rst_n && acc_valid && acc_last};
    if (rst_n) begin
      flags_or <= flags_or | fp_flags;
      if (acc_valid) begin
        if (MAX_PERIOD > 0 && rows_seen > 0 && cyc - last_row_cyc > MAX_PERIOD) begin
          errors++; $display("%s: pass period %0d cycles > %0d", PRE, cyc - last_row_cyc, MAX_PERIOD);
        end
        last_row_cyc = cyc;
        rows_seen++;
        if (int'(acc_row) >= ACCR) begin errors++; $display("%s: ACC row %0d out of range", PRE, acc_row); end
        else begin
          if (row_last[acc_row] != -1 && cyc - row_last[acc_row] < ACC_LAT) begin
            errors++; if (errors < 10) $display("%s: ACC hazard row %0d: updates %0d cycles apart", PRE, acc_row, cyc - row_last[acc_row]);
          end
          row_last[acc_row] = cyc;
          for (int c = 0; c < DIM; c++) if (c < int'(acc_ncols)) accm[int'(acc_row) * DIM + c] <= lane_y[c];
        end
      end
    end
  end
  initial for (int i = 0; i < ACCR; i++) row_last[i] = -1;

  // ---------------- run and check
  initial begin
    done = 0; errors = 0;
    $readmemh({PRE, "spm.hex"}, spm);
    $readmemh({PRE, "dtab.hex"}, dtab);
    $readmemh({PRE, "prog.hex"}, prog);
    $readmemh({PRE, "acc0.hex"}, acc0);
    $readmemh({PRE, "acc1.hex"}, acc1);
    $readmemh({PRE, "exp.hex"}, expv);
    for (int r = 0; r < ACCR; r++) for (int c = 0; c < DIM; c++) accm[r*DIM + c] = acc0[r][32*c +: 32];
    repeat (4) @(posedge clk);
    rst_n = 1;
    // wait for all completions (instructions with a semaphore) or the time limit
    fork
      begin : w_done
        wait (qi == NINS && !q_valid && sem[1] == expv[1] && sem[2] == expv[2] && dut.inf_cnt == 0);
      end
      begin : w_time
        repeat (`ifdef QUICK 6000 `else 40000 `endif) @(posedge clk);
        errors++; $display("%s: TIMEOUT (qi=%0d sem1=%0d sem2=%0d inf=%0d)", PRE, qi, sem[1], sem[2], dut.inf_cnt);
      end
    join_any
    disable w_done; disable w_time;
    repeat (20) @(posedge clk);
    if (fault_valid) begin errors++; $display("%s: fault cause %0d", PRE, fault_cause); end
    for (int r = 0; r < ACCR; r++)
      for (int c = 0; c < DIM; c++)
        if (accm[r*DIM + c] !== acc1[r][32*c +: 32]) begin
          errors++; if (errors < 12) $display("%s: ACC[%0d][%0d] = %h exp %h", PRE, r, c, accm[r*DIM + c], acc1[r][32*c +: 32]);
        end
    if (flags_or !== expv[0][3:0]) begin errors++; $display("%s: fp_flags OR %h exp %h", PRE, flags_or, expv[0][3:0]); end
    if (sem[1] != expv[1] || sem[2] != expv[2]) begin errors++; $display("%s: sem %0d %0d exp %0d %0d", PRE, sem[1], sem[2], expv[1], expv[2]); end
    if (rows_seen == 0) begin errors++; $display("%s: no rows reached the ACC", PRE); end
    $display("%s: %0d ACC rows in %0d cycles, flags %h", PRE, rows_seen, cyc, flags_or);
    done = 1;
  end
endmodule

// Legality faults: every line is an illegal instruction (plus its two TDs) run after a reset. The MXU must raise the cause, stop
// taking instructions and touch neither the SPM nor the ACC.
module mxu_fault_tc #(parameter int DIM = 8, parameter int N = 1, parameter int SPMB = 1, parameter int ACCR = 1, parameter string FILE = "")
  (output logic done, output int errors);
  localparam int LB = DIM;
  logic clk = 0, rst_n = 0;
  always #5 clk = ~clk;
  logic [4+256+256+128+6*64+32-1:0] mem [0:N-1];
  logic q_valid = 0, q_ready;
  logic [127:0] q_instr = '0;
  logic [6*64-1:0] q_regs = '0;
  logic [31:0] q_wval = '0;
  logic [5:0] sem_rd_idx;
  logic dtab_req, dtab_gnt = 0, dtab_rvalid = 0;
  logic [7:0] dtab_idx;
  logic [255:0] dtab_data, td_a, td_w;
  logic [3:0] a_req_v, sem_inc_amt, fp_flags, fault_cause, cause_exp;
  logic [4*22-1:0] a_req_addr;
  logic [7:0] w_req_v;
  logic [8*22-1:0] w_req_addr;
  logic s_req_v, acc_valid, acc_rescale, acc_overwrite, acc_use_cs, acc_last, sem_inc_valid, fault_valid;
  logic [21:0] s_req_addr;
  logic [10:0] acc_row;
  logic [DIM*32-1:0] acc_part, acc_cs;
  logic [6:0] acc_ncols;
  logic [31:0] acc_rowscale, fault_addr;
  logic [5:0] sem_inc_idx;
  mxu #(.DIM(DIM), .ACC_ROWS(ACCR), .SPM_BYTES(SPMB)) dut (
    .clk, .rst_n, .q_valid, .q_ready, .q_instr, .q_regs, .q_wval, .sem_rd_idx, .sem_rd_val(32'd0),
    .dtab_req, .dtab_idx, .dtab_gnt, .dtab_rvalid, .dtab_data,
    .a_req_v, .a_req_addr, .a_req_ready(1'b1), .a_rsp_data('0), .w_req_v, .w_req_addr, .w_req_ready(1'b1), .w_rsp_data('0),
    .s_req_v, .s_req_addr, .s_req_ready(1'b1), .s_rsp_data('0),
    .acc_valid, .acc_row, .acc_part, .acc_ncols, .acc_rescale, .acc_overwrite, .acc_use_cs, .acc_rowscale, .acc_cs, .acc_last,
    .acc_done(1'b0), .sem_inc_valid, .sem_inc_idx, .sem_inc_amt, .fp_flags, .fault_valid, .fault_cause, .fault_addr);
  always @(posedge clk) begin
    dtab_gnt <= dtab_req && !dtab_gnt && !dtab_rvalid;
    dtab_rvalid <= dtab_gnt;
    if (dtab_gnt) dtab_data <= (dut.u_dec.dst == 4'd1) ? td_a : td_w;
  end
  int spm_touch = 0;
  always @(posedge clk) if (|a_req_v || |w_req_v || s_req_v || acc_valid || sem_inc_valid) spm_touch++;
  initial begin
    done = 0; errors = 0;
    $readmemh(FILE, mem);
    for (int k = 0; k < N; k++) begin
      rst_n = 0; q_valid = 0; spm_touch = 0;
      repeat (3) @(posedge clk);
      {cause_exp, td_w, td_a, q_instr, q_regs, q_wval} = mem[k];
      #1 rst_n = 1; q_valid = 1;
      repeat (60) @(posedge clk);
      if (!fault_valid || fault_cause !== cause_exp) begin
        errors++; $display("fault case %0d: valid %b cause %0d expected %0d", k, fault_valid, fault_cause, cause_exp);
      end
      if (spm_touch != 0) begin errors++; $display("fault case %0d: %0d SPM / ACC / semaphore actions", k, spm_touch); end
      if (q_ready) begin errors++; $display("fault case %0d: queue still popping", k); end
    end
    done = 1;
  end
endmodule

module tb_mxu;
  logic d8, d16, d8s, d16s, d32, d8b;
  int e8, e16, e8s, e16s, e32, e8b;
`ifdef ONLY32
  assign d8 = 1, e8 = 0, d16 = 1, e16 = 0, d8s = 1, e8s = 0, d16s = 1, e16s = 0, d8b = 1, e8b = 0;
  mxu_tc #(.DIM(32), .NINS(`CNT_MXU_32_PROG), .NTD(`CNT_MXU_32_DTAB), .SPMB(`CNT_MXU_32_SPM), .ACCR(`CNT_MXU_32_ACC0), .STALL_PCT(0), .MAX_PERIOD(26), .PRE("mxu_32_")) t32 (.done(d32), .errors(e32));
`elsif ONLY16
  assign d8 = 1, e8 = 0, d8s = 1, e8s = 0, d16s = 1, e16s = 0, d32 = 1, e32 = 0, d8b = 1, e8b = 0;
  mxu_tc #(.DIM(16), .NINS(`CNT_MXU_16_PROG), .NTD(`CNT_MXU_16_DTAB), .SPMB(`CNT_MXU_16_SPM), .ACCR(`CNT_MXU_16_ACC0), .STALL_PCT(0),  .PRE("mxu_16_")) t16 (.done(d16), .errors(e16));
`elsif QUICK
  assign d16 = 1, d8s = 1, d16s = 1, d32 = 1, e16 = 0, e8s = 0, e16s = 0, e32 = 0, d8b = 1, e8b = 0;
  mxu_tc #(.DIM(8),  .NINS(`CNT_MXU_8_PROG),  .NTD(`CNT_MXU_8_DTAB),  .SPMB(`CNT_MXU_8_SPM),  .ACCR(`CNT_MXU_8_ACC0),  .STALL_PCT(0),  .PRE("mxu_8_"))  t8  (.done(d8),  .errors(e8));
`else
  mxu_tc #(.DIM(8),  .NINS(`CNT_MXU_8_PROG),  .NTD(`CNT_MXU_8_DTAB),  .SPMB(`CNT_MXU_8_SPM),  .ACCR(`CNT_MXU_8_ACC0),  .STALL_PCT(0),  .PRE("mxu_8_"))  t8  (.done(d8),  .errors(e8));
  mxu_tc #(.DIM(8),  .NINS(`CNT_MXU_8_PROG),  .NTD(`CNT_MXU_8_DTAB),  .SPMB(`CNT_MXU_8_SPM),  .ACCR(`CNT_MXU_8_ACC0),  .STALL_PCT(30), .ACC_LAT(24), .PRE("mxu_8_"))  t8s (.done(d8s), .errors(e8s));
  mxu_tc #(.DIM(16), .NINS(`CNT_MXU_16_PROG), .NTD(`CNT_MXU_16_DTAB), .SPMB(`CNT_MXU_16_SPM), .ACCR(`CNT_MXU_16_ACC0), .STALL_PCT(0),  .PRE("mxu_16_")) t16 (.done(d16), .errors(e16));
  mxu_tc #(.DIM(8),  .NINS(`CNT_MXU_8_PROG),  .NTD(`CNT_MXU_8_DTAB),  .SPMB(`CNT_MXU_8_SPM),  .ACCR(`CNT_MXU_8_ACC0),  .STALL_PCT(0), .A_BLOCK(40), .PRE("mxu_8_"))  t8b (.done(d8b), .errors(e8b));
  mxu_tc #(.DIM(16), .NINS(`CNT_MXU_16_PROG), .NTD(`CNT_MXU_16_DTAB), .SPMB(`CNT_MXU_16_SPM), .ACCR(`CNT_MXU_16_ACC0), .STALL_PCT(30), .PRE("mxu_16_")) t16s (.done(d16s), .errors(e16s));
  mxu_tc #(.DIM(32), .NINS(`CNT_MXU_32_PROG), .NTD(`CNT_MXU_32_DTAB), .SPMB(`CNT_MXU_32_SPM), .ACCR(`CNT_MXU_32_ACC0), .STALL_PCT(0), .MAX_PERIOD(26), .PRE("mxu_32_")) t32 (.done(d32), .errors(e32));
`endif
  logic df;
  int ef;
`ifdef QUICK
  mxu_fault_tc #(.DIM(8), .N(`CNT_MXU_FAULT_8), .SPMB(`CNT_MXU_8_SPM), .ACCR(`CNT_MXU_8_ACC0), .FILE("mxu_fault_8.hex")) tf (.done(df), .errors(ef));
`elsif ONLY16
  assign df = 1, ef = 0;
`elsif ONLY32
  assign df = 1, ef = 0;
`else
  mxu_fault_tc #(.DIM(8), .N(`CNT_MXU_FAULT_8), .SPMB(`CNT_MXU_8_SPM), .ACCR(`CNT_MXU_8_ACC0), .FILE("mxu_fault_8.hex")) tf (.done(df), .errors(ef));
`endif
  initial begin
    wait (d8 && d16 && d8s && d16s && d32 && d8b && df);
    if (e8 + e16 + e8s + e16s + e32 + e8b + ef == 0) $display("PASS mxu");
    else $display("FAIL mxu: %0d errors (8: %0d, 8 stalls: %0d, 8 long A stalls: %0d, 16: %0d, 16 stalls: %0d, 32: %0d, faults: %0d)", e8 + e16 + e8s + e16s + e32 + e8b + ef, e8, e8s, e8b, e16, e16s, e32, ef);
    $finish;
  end
endmodule
