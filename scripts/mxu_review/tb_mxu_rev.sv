// tb_mxu_rev: the review's own testbench of `mxu` (review part R, docs/reports/mxu_review.md). Not the author's tb_mxu.
// Inputs (directory +DIR=...): spm.hex (bytes), dtab.hex (256-bit entries), prog.hex (one instruction per line:
// {wval[31:0], regs[6*64-1:0], instr[127:0]}), acc.hex (ACC_ROWS*DIM FP32 words, row-major). +NI=<instructions>.
// +STALL=<percent of cycles a port / the DTAB refuses>, +SEED=<n>, +RMW=1: ACC writes visible ACC_LAT after the row (acc.md 5), +RDYV=1: a port's ready is low while it has no valid lane
// (a valid/ready SPM that only grants requests), +MAXCYC=<n>.
// +XFILL=1 (implementability review): a lane that is not granted, and the DTAB data outside the cycle after a grant, are all X; an X
// monitor logs XPROP for an X on a control output, or on a payload in a cycle in which its valid says it is meaningful.
// Outputs in the same directory: rtl_stream.txt (every acc_valid row, raw), rtl_log.txt (POP / SEM / FLAGS / ACCFL / HAZARD /
// OOB / DONEDLY / FAULT / DONE / TIMEOUT events), rtl_acc.hex (ACC image after the acc_update lanes of the rows).
`timescale 1ns/1ps
module tb_mxu_rev;
  parameter int DIM       = 8;
  parameter int SPM_BYTES = 65536;
  parameter int ACC_ROWS  = 256;
  parameter int ACC_LAT   = 8;
  parameter int SPM_LAT   = 2;
  localparam int LB = DIM;

  logic clk = 1'b0, rst_n = 1'b0;
  always #5 clk = ~clk;
  integer cyc = 0;
  always @(posedge clk) cyc <= cyc + 1;

  // ---------------------------------------------------------------- DUT
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
  logic s_req_v, s_req_ready;
  logic [21:0] s_req_addr;
  logic [LB*8-1:0] s_rsp_data;
  logic acc_valid, acc_rescale, acc_overwrite, acc_use_cs, acc_last, acc_done;
  logic [10:0] acc_row;
  logic [DIM*32-1:0] acc_part, acc_cs;
  logic [6:0] acc_ncols;
  logic [31:0] acc_rowscale;
  logic sem_inc_valid;
  logic [5:0] sem_inc_idx;
  logic [3:0] sem_inc_amt, fp_flags, fault_cause;
  logic fault_valid;
  logic [63:0] fault_addr;

  mxu #(.DIM(DIM), .SPM_LAT(SPM_LAT), .ACC_LAT(ACC_LAT), .ACC_ROWS(ACC_ROWS), .SPM_BYTES(SPM_BYTES)) dut (.*);

  // ---------------------------------------------------------------- memories and options
  reg [7:0]   spm  [0:SPM_BYTES-1];
  reg [255:0] dtab [0:255];
  reg [543:0] prog [0:63];
  reg [31:0]  accm [0:ACC_ROWS*DIM-1];
  reg [31:0]  sem  [0:63];
  integer     last_upd [0:ACC_ROWS-1];
  integer NI, STALL, SEED, RDYV, MAXCYC, RMW, XFILL;
  reg [8*512-1:0] dir;
  integer flog, fstr;

  // ---------------------------------------------------------------- MQ head
  integer pc = 0;
  assign q_valid = (pc < NI);
  assign q_instr = prog[pc][127:0];
  assign q_regs  = prog[pc][511:128];
  assign q_wval  = prog[pc][543:512];
  assign sem_rd_val = sem[sem_rd_idx];
  always @(posedge clk) if (rst_n && q_valid && q_ready) begin
    $fdisplay(flog, "POP %0d %0d", cyc, pc);
    pc <= pc + 1;
  end

  // ---------------------------------------------------------------- DTAB: grant (random), data the cycle after
  reg d_rdy;
  always @(posedge clk) d_rdy <= ($urandom % 100) >= STALL;
  assign dtab_gnt = dtab_req && d_rdy;
  always @(posedge clk) begin
    dtab_rvalid <= rst_n && dtab_gnt;
    if (dtab_gnt) dtab_data <= dtab[dtab_idx];
    else if (XFILL != 0) dtab_data <= 'x;
  end

  // ---------------------------------------------------------------- SPM: three ports, fixed latency, random stalls
  reg a_rdy_r, w_rdy_r, s_rdy_r;
  always @(posedge clk) begin
    a_rdy_r <= ($urandom % 100) >= STALL;
    w_rdy_r <= ($urandom % 100) >= STALL;
    s_rdy_r <= ($urandom % 100) >= STALL;
  end
  assign a_req_ready = a_rdy_r && (RDYV == 0 || a_req_v != 0);
  assign w_req_ready = w_rdy_r && (RDYV == 0 || w_req_v != 0);
  assign s_req_ready = s_rdy_r && (RDYV == 0 || s_req_v);

  function automatic [LB*8-1:0] lane_rd(input [21:0] addr, input v, input string port);
    lane_rd = '0;
    for (int b = 0; b < LB; b++) begin
      if (!v) lane_rd[8*b +: 8] = (XFILL != 0) ? 8'bx : 8'($urandom);
      else if (int'(addr) + b >= SPM_BYTES) lane_rd[8*b +: 8] = 8'hFF;
      else lane_rd[8*b +: 8] = spm[int'(addr) + b];
    end
    if (v && (int'(addr) + LB > SPM_BYTES)) $fdisplay(flog, "OOB %0d %s %0h", cyc, port, addr);
  endfunction

  reg [4*LB*8-1:0] a_pipe [SPM_LAT];
  reg [8*LB*8-1:0] w_pipe [SPM_LAT];
  reg [LB*8-1:0]   s_pipe [SPM_LAT];
  always @(posedge clk) begin
    for (int l = 0; l < 4; l++) a_pipe[0][l*LB*8 +: LB*8] <= lane_rd(a_req_addr[22*l +: 22], a_req_v[l] && a_req_ready, "A");
    for (int l = 0; l < 8; l++) w_pipe[0][l*LB*8 +: LB*8] <= lane_rd(w_req_addr[22*l +: 22], w_req_v[l] && w_req_ready, "W");
    s_pipe[0] <= lane_rd(s_req_addr, s_req_v && s_req_ready, "S");
    for (int k = 1; k < SPM_LAT; k++) begin a_pipe[k] <= a_pipe[k-1]; w_pipe[k] <= w_pipe[k-1]; s_pipe[k] <= s_pipe[k-1]; end
  end
  assign a_rsp_data = a_pipe[SPM_LAT-1];
  assign w_rsp_data = w_pipe[SPM_LAT-1];
  assign s_rsp_data = s_pipe[SPM_LAT-1];

  // ---------------------------------------------------------------- ACC: acc_update lanes, word-masked write, acc_done
  logic [31:0] lane_y [DIM];
  logic [3:0]  lane_f [DIM];
  for (genvar c = 0; c < DIM; c++) begin : g_lane
    acc_update u_lane (.acc(accm[int'(acc_row) * DIM + c]), .part(acc_part[32*c +: 32]), .rowscale(acc_rowscale),
                       .cs(acc_cs[32*c +: 32]), .rescale(acc_rescale), .use_cs(acc_use_cs), .overwrite(acc_overwrite),
                       .y(lane_y[c]), .flags(lane_f[c]));
  end
  reg [ACC_LAT-1:0] done_sr;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) done_sr <= '0;
    else done_sr <= {done_sr[ACC_LAT-2:0], acc_valid && acc_last};
  end
  assign acc_done = done_sr[ACC_LAT-1];

  // +RMW=1: acc.md 5 timing. The row is read in its acc_valid cycle T and its write is visible from T + ACC_LAT (no forwarding).
  reg               pw_v   [ACC_LAT-1];
  reg [10:0]        pw_row [ACC_LAT-1];
  reg [6:0]         pw_nc  [ACC_LAT-1];
  reg [DIM*32-1:0]  pw_d   [ACC_LAT-1];
  initial for (int k = 0; k < ACC_LAT-1; k++) pw_v[k] = 1'b0;
  always @(posedge clk) begin
    if (pw_v[ACC_LAT-2])
      for (int c = 0; c < DIM; c++) if (c < int'(pw_nc[ACC_LAT-2])) accm[int'(pw_row[ACC_LAT-2]) * DIM + c] <= pw_d[ACC_LAT-2][32*c +: 32];
    for (int k = ACC_LAT-2; k > 0; k--) begin
      pw_v[k] <= pw_v[k-1]; pw_row[k] <= pw_row[k-1]; pw_nc[k] <= pw_nc[k-1]; pw_d[k] <= pw_d[k-1];
    end
    pw_v[0] <= (RMW != 0) && rst_n && acc_valid && (int'(acc_row) < ACC_ROWS);
    pw_row[0] <= acc_row;
    pw_nc[0] <= acc_ncols;
    for (int c = 0; c < DIM; c++) pw_d[0][32*c +: 32] <= lane_y[c];
  end

  reg [3:0] accfl_or = 4'd0, fpfl_or = 4'd0;
  always @(posedge clk) if (rst_n && acc_valid) begin
    logic [3:0] f;
    f = 4'd0;
    if (int'(acc_row) >= ACC_ROWS) $fdisplay(flog, "OOB %0d ACC %0d", cyc, acc_row);
    else begin
      if (last_upd[acc_row] >= 0 && cyc - last_upd[acc_row] < ACC_LAT)
        $fdisplay(flog, "HAZARD %0d row %0d prev %0d", cyc, acc_row, last_upd[acc_row]);
      last_upd[acc_row] <= cyc;
      for (int c = 0; c < DIM; c++) if (c < int'(acc_ncols)) begin
        if (RMW == 0) accm[int'(acc_row) * DIM + c] <= lane_y[c];
        f = f | lane_f[c];
      end
    end
    accfl_or <= accfl_or | f;
    if (f != 0) $fdisplay(flog, "ACCFL %0d %0h", cyc, f);
    $fwrite(fstr, "R %0d %0d %0d %0d %0d %0d %0d %08h", cyc, acc_row, acc_ncols, acc_rescale, acc_overwrite, acc_use_cs,
            acc_last, (XFILL != 0 && !acc_rescale) ? 32'd0 : acc_rowscale);
    for (int c = 0; c < DIM; c++) $fwrite(fstr, " %08h", (XFILL != 0 && (c >= int'(acc_ncols) || !acc_use_cs)) ? 32'd0 : acc_cs[32*c +: 32]);
    for (int c = 0; c < DIM; c++) $fwrite(fstr, " %08h", (XFILL != 0 && c >= int'(acc_ncols)) ? 32'd0 : acc_part[32*c +: 32]);
    $fwrite(fstr, "\n");
  end

  // ---------------------------------------------------------------- completion, flags, faults
  integer nsem = 0;
  always @(posedge clk) if (rst_n) begin
    if (sem_inc_valid) begin
      sem[sem_inc_idx] <= sem[sem_inc_idx] + 32'(sem_inc_amt);
      nsem <= nsem + 1;
      $fdisplay(flog, "SEM %0d %0d %0d", cyc, sem_inc_idx, sem_inc_amt);
    end
    if (fp_flags != 4'd0) begin
      fpfl_or <= fpfl_or | fp_flags;
      $fdisplay(flog, "FLAGS %0d %0h", cyc, fp_flags);
    end
    if (fault_valid) begin
      $fdisplay(flog, "FAULT %0d %0d %016h", cyc, fault_cause, fault_addr);
    end
  end


  // ---------------------------------------------------------------- X monitor (+XFILL, but always on): control outputs are never X
  // after reset; a payload is never X while its valid says it is used (lanes: per lane valid; ACC: columns < acc_ncols)
  integer nx = 0;
  // (^v) === 1'bx rather than $isunknown: in this block Icarus 13 took `if ($isunknown({...}))` with every bit 0 / 1 (review I-13)
  task automatic xp(input string what);
    if (nx < 200) $fdisplay(flog, "XPROP %0d %s", cyc, what);
    nx = nx + 1;
  endtask
  always @(negedge clk) if (rst_n && cyc > 4) begin
    if (((^({q_ready, dtab_req, a_req_v, w_req_v, s_req_v, acc_valid, sem_inc_valid, fault_valid, fp_flags})) === 1'bx))
      xp($sformatf("control q_ready %b dtab_req %b a_req_v %b w_req_v %b s_req_v %b acc_valid %b sem_inc_valid %b fault_valid %b fp_flags %b",
                   q_ready, dtab_req, a_req_v, w_req_v, s_req_v, acc_valid, sem_inc_valid, fault_valid, fp_flags));
`ifndef GATE          // the gate-level run (gl_sim.py) has no internal names
    if (((^({dut.lst, dut.sst, dut.u_dec.dst, dut.ctx_v, dut.tok_v, dut.inf_cnt, dut.cs_cnt, dut.rs_res, dut.rs_idx, dut.free_g, dut.gap_cnt})) === 1'bx))
      xp($sformatf("state lst %b sst %b dst %b ctx_v %b tok_v %b inf_cnt %b cs_cnt %b rs_res %b rs_idx %b free_g %b gap_cnt %b",
                   dut.lst, dut.sst, dut.u_dec.dst, dut.ctx_v, dut.tok_v, dut.inf_cnt, dut.cs_cnt, dut.rs_res, dut.rs_idx, dut.free_g, dut.gap_cnt));
`endif
    if (dtab_req && ((^(dtab_idx)) === 1'bx)) xp("dtab_idx");
    for (int l = 0; l < 4; l++) if (a_req_v[l] && ((^(a_req_addr[22*l +: 22])) === 1'bx)) xp("a_req_addr");
    for (int l = 0; l < 8; l++) if (w_req_v[l] && ((^(w_req_addr[22*l +: 22])) === 1'bx)) xp("w_req_addr");
    if (s_req_v && ((^(s_req_addr)) === 1'bx)) xp("s_req_addr");
    if (acc_valid) begin
      if (((^({acc_row, acc_ncols, acc_rescale, acc_overwrite, acc_use_cs, acc_last})) === 1'bx)) xp("acc_ctl");
      for (int c = 0; c < DIM; c++) if (c < int'(acc_ncols)) begin
        if (((^(acc_part[32*c +: 32])) === 1'bx)) xp($sformatf("acc_part[%0d]", c));
        if (acc_use_cs && ((^(acc_cs[32*c +: 32])) === 1'bx)) xp($sformatf("acc_cs[%0d]", c));
      end
      if (acc_rescale && ((^(acc_rowscale)) === 1'bx)) xp("acc_rowscale");
    end
    if (sem_inc_valid && ((^({sem_inc_idx, sem_inc_amt})) === 1'bx)) xp("sem_inc");
    if (fault_valid && ((^({fault_cause, fault_addr})) === 1'bx)) xp("fault");
  end

  // ---------------------------------------------------------------- run
  initial begin
    string s;
    if (!$value$plusargs("DIR=%s", dir)) begin $display("need +DIR"); $finish; end
    if (!$value$plusargs("NI=%d", NI)) NI = 1;
    if (!$value$plusargs("STALL=%d", STALL)) STALL = 0;
    if (!$value$plusargs("SEED=%d", SEED)) SEED = 1;
    if (!$value$plusargs("RDYV=%d", RDYV)) RDYV = 0;
    if (!$value$plusargs("MAXCYC=%d", MAXCYC)) MAXCYC = 200000;
    if (!$value$plusargs("RMW=%d", RMW)) RMW = 0;
    if (!$value$plusargs("XFILL=%d", XFILL)) XFILL = 0;
    void'($urandom(SEED));
    s = string'(dir);
    $readmemh({s, "/spm.hex"}, spm);
    $readmemh({s, "/dtab.hex"}, dtab);
    $readmemh({s, "/prog.hex"}, prog, 0, NI - 1);
    $readmemh({s, "/acc.hex"}, accm);
    for (int i = 0; i < 64; i++) sem[i] = 32'd0;
    for (int i = 0; i < ACC_ROWS; i++) last_upd[i] = -1000;
    flog = $fopen({s, "/rtl_log.txt"}, "w");
    fstr = $fopen({s, "/rtl_stream.txt"}, "w");
    repeat (3) @(posedge clk);
    #1 rst_n = 1'b1;
    while (cyc < MAXCYC && !(nsem >= NI && pc >= NI) && !fault_valid) @(posedge clk);
    if (fault_valid) $fdisplay(flog, "DONE %0d fault", cyc);
    else if (cyc >= MAXCYC) $fdisplay(flog, "TIMEOUT %0d pc %0d nsem %0d", cyc, pc, nsem);
    else begin
      repeat (ACC_LAT + DIM + 40) @(posedge clk);   // spurious rows or increments after completion show up in the logs
      $fdisplay(flog, "DONE %0d", cyc);
    end
    $fdisplay(flog, "FPFLAGS %0h", fpfl_or);
    $fdisplay(flog, "ACCFLAGS %0h", accfl_or);
    for (int i = 0; i < 64; i++) if (sem[i] != 0) $fdisplay(flog, "SEMVAL %0d %0d", i, sem[i]);
    $fclose(fstr);
    $fclose(flog);
    $writememh({s, "/rtl_acc.hex"}, accm);
    $finish;
  end
endmodule
