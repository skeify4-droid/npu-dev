// Review part H (control hazards and stalls): randomized environment around `mxu` (docs/reports/mxu_review.md part H).
// Driven and checked by scripts/mxu_review/hz_run.py; nothing here knows what the result should be, the testbench only
// drives the ports and logs what the MXU does. Independent of tb_mxu / gen_mxu_vectors.py / arith_ref.py.
//
// Environment:
//   * MQ: the program of the current segment, random gaps between entries (the head stays valid once raised).
//   * semaphores: 64 x 32 bit, read combinationally; sem_inc and external increments (other engines) at given cycles.
//   * DTAB: grant after a random delay, data the cycle after the grant.
//   * SPM: three ports with independent random stalls (per-cycle probability plus bursts of random length); optionally `ready`
//     only for a request with a valid lane (RDYV). Data exactly SPM_LAT cycles after the grant, random garbage in every other
//     cycle and in invalid lanes. Valid lanes outside the SPM or not LB-aligned are logged as errors.
//   * ACC: every acc_valid row is logged; acc_done for an acc_last row ACC_LAT + a random extra delay later, in order, one per cycle.
//   * fault: logged; optionally a reset `rstd` cycles later, after which segment 1 runs (fresh semaphores, same SPM / DTAB).
//     `rst_at` forces a reset at a given cycle of segment 0 (mid-flight), fault or not.
// Log lines (cycle first): Q take, A acc row, D acc_done, S sem_inc, F fault, R reset, E error, X unknown, FL flags, END.
`timescale 1ns/1ps
module tb_mxu_hz;
  parameter int DIM = 8;
  parameter int SPM_LAT = 2;
  parameter int ACC_LAT = 8;
  parameter int CS_DEPTH = 4;
  parameter int ACC_ROWS = 256;
  parameter int SPM_BYTES = 262144;
  localparam int LB = DIM;
  localparam int NPMAX = 512;

  reg clk = 1'b0, rst_n = 1'b0;
  always #5 clk = ~clk;

  // ------------------------------------------------------------------ plusargs
  reg [1023:0] dir;
  integer seed, pa, pw, ps, ba, bw, bs, bla, blw, bls, rdyv, accx, accp, qgap, qgp, dtd, maxcyc, rstd, rst_at, n0, n1, ne0, ne1;
  initial begin
    if (!$value$plusargs("dir=%s", dir)) dir = ".";
    if (!$value$plusargs("seed=%d", seed)) seed = 1;
    if (!$value$plusargs("pa=%d", pa)) pa = 0;
    if (!$value$plusargs("pw=%d", pw)) pw = 0;
    if (!$value$plusargs("ps=%d", ps)) ps = 0;
    if (!$value$plusargs("ba=%d", ba)) ba = 0;
    if (!$value$plusargs("bw=%d", bw)) bw = 0;
    if (!$value$plusargs("bs=%d", bs)) bs = 0;
    if (!$value$plusargs("bla=%d", bla)) bla = 1;
    if (!$value$plusargs("blw=%d", blw)) blw = 1;
    if (!$value$plusargs("bls=%d", bls)) bls = 1;
    if (!$value$plusargs("rdyv=%d", rdyv)) rdyv = 0;
    if (!$value$plusargs("accx=%d", accx)) accx = 0;
    if (!$value$plusargs("accp=%d", accp)) accp = 0;       // per-mille of acc_last rows that get an extra delay
    if (!$value$plusargs("qgap=%d", qgap)) qgap = 0;       // max MQ gap
    if (!$value$plusargs("qgp=%d", qgp)) qgp = 0;          // per-mille of entries with a gap
    if (!$value$plusargs("dtd=%d", dtd)) dtd = 0;
    if (!$value$plusargs("maxcyc=%d", maxcyc)) maxcyc = 400000;
    if (!$value$plusargs("rstd=%d", rstd)) rstd = -1;
    if (!$value$plusargs("rst_at=%d", rst_at)) rst_at = -1;
    if (!$value$plusargs("n0=%d", n0)) n0 = 0;
    if (!$value$plusargs("n1=%d", n1)) n1 = 0;
    if (!$value$plusargs("ne0=%d", ne0)) ne0 = 0;
    if (!$value$plusargs("ne1=%d", ne1)) ne1 = 0;
  end

  // ------------------------------------------------------------------ RNG (xorshift32)
  reg [31:0] rng;
  function automatic [31:0] rnd(input integer dummy);
    begin
      rng = rng ^ (rng << 13); rng = rng ^ (rng >> 17); rng = rng ^ (rng << 5);
      rnd = rng;
    end
  endfunction
  function automatic integer pm(input integer p);      // 1 with probability p / 1000
    begin pm = ((rnd(0) % 1000) < p) ? 1 : 0; end
  endfunction

  // ------------------------------------------------------------------ memories
  reg [7:0]   spm  [SPM_BYTES];
  reg [255:0] dtab [256];
  reg [543:0] prog [2][NPMAX];
  reg [31:0]  sem_init [2][64];
  reg [31:0]  ext_t [2][256];
  reg [7:0]   ext_i [2][256];
  reg [31:0]  ext_a [2][256];
  integer fd, fl, k, r;
  reg [1023:0] fn;
  reg [543:0] t_p;
  reg [31:0] t_a, t_b, t_c;
  initial begin
    rng = 32'h9E3779B9 ^ seed;
    if (rng == 0) rng = 1;
    $sformat(fn, "%0s/spm.hex", dir);  $readmemh(fn, spm);
    $sformat(fn, "%0s/dtab.hex", dir); $readmemh(fn, dtab);
    for (k = 0; k < 2; k++) begin
      $sformat(fn, "%0s/prog%0d.hex", dir, k);
      fd = $fopen(fn, "r");
      if (fd != 0) begin
        r = 0;
        while (!$feof(fd) && r < NPMAX) begin
          if ($fscanf(fd, "%h\n", t_p) == 1) begin prog[k][r] = t_p; r = r + 1; end
          else r = NPMAX;
        end
        $fclose(fd);
      end
      $sformat(fn, "%0s/sem%0d.hex", dir, k);
      fd = $fopen(fn, "r");
      for (r = 0; r < 64; r++) sem_init[k][r] = 0;
      if (fd != 0) begin
        for (r = 0; r < 64; r++) if ($fscanf(fd, "%h\n", t_a) == 1) sem_init[k][r] = t_a;
        $fclose(fd);
      end
      $sformat(fn, "%0s/ext%0d.txt", dir, k);
      fd = $fopen(fn, "r");
      if (fd != 0) begin
        for (r = 0; r < (k ? ne1 : ne0); r++)
          if ($fscanf(fd, "%d %d %d\n", t_a, t_b, t_c) == 3) begin ext_t[k][r] = t_a; ext_i[k][r] = t_b[7:0]; ext_a[k][r] = t_c; end
          else ext_t[k][r] = 32'hFFFFFFFF;
        $fclose(fd);
      end
    end
    $sformat(fn, "%0s/log.txt", dir);
    fl = $fopen(fn, "w");
  end

  // ------------------------------------------------------------------ DUT
  logic         q_valid, q_ready;
  logic [127:0] q_instr;
  logic [383:0] q_regs;
  logic [31:0]  q_wval;
  logic [5:0]   sem_rd_idx;
  logic [31:0]  sem_rd_val;
  logic         dtab_req, dtab_gnt, dtab_rvalid;
  logic [7:0]   dtab_idx;
  logic [255:0] dtab_data;
  logic [3:0]   a_req_v;
  logic [4*22-1:0] a_req_addr;
  logic         a_req_ready;
  logic [4*LB*8-1:0] a_rsp_data;
  logic [7:0]   w_req_v;
  logic [8*22-1:0] w_req_addr;
  logic         w_req_ready;
  logic [8*LB*8-1:0] w_rsp_data;
  logic         s_req_v, s_req_ready;
  logic [21:0]  s_req_addr;
  logic [LB*8-1:0] s_rsp_data;
  logic         acc_valid, acc_rescale, acc_overwrite, acc_use_cs, acc_last, acc_done;
  logic [10:0]  acc_row;
  logic [DIM*32-1:0] acc_part, acc_cs;
  logic [6:0]   acc_ncols;
  logic [31:0]  acc_rowscale;
  logic         sem_inc_valid;
  logic [5:0]   sem_inc_idx;
  logic [3:0]   sem_inc_amt;
  logic [3:0]   fp_flags;
  logic         fault_valid;
  logic [3:0]   fault_cause;
  logic [63:0]  fault_addr;

  mxu #(.DIM(DIM), .SPM_LAT(SPM_LAT), .ACC_LAT(ACC_LAT), .CS_DEPTH(CS_DEPTH), .ACC_ROWS(ACC_ROWS), .SPM_BYTES(SPM_BYTES)) u_dut (.*);

  // ------------------------------------------------------------------ state
  integer cyc = 0, seg = 0, qk = 0, qwait = 0, ek = 0, seg_start = 0;
  integer idle = 0, rst_pend = -1, rst_hold = 0, faulted = 0, done = 0;
  reg [31:0] sem [64];
  integer dq_t [1024];
  integer dq_h = 0, dq_n = 0, dq_last = 0;
  reg [31:0] garb [64];

  // MQ head
  wire [543:0] pe = prog[seg][qk];
  assign q_valid  = rst_n && (qk < (seg ? n1 : n0)) && (qwait == 0);
  assign q_instr  = pe[127:0];
  assign q_regs   = pe[511:128];
  assign q_wval   = pe[543:512];
  assign sem_rd_val = sem[sem_rd_idx];

  // DTAB
  integer dt_cnt = 0;
  reg dt_rv = 1'b0;
  reg [255:0] dt_data;
  assign dtab_gnt    = dtab_req && (dt_cnt == 0);
  assign dtab_rvalid = dt_rv;
  assign dtab_data   = dt_rv ? dt_data : {8{garb[5]}};

  // SPM ready
  reg a_rr = 1'b1, w_rr = 1'b1, s_rr = 1'b1;
  integer a_bl = 0, w_bl = 0, s_bl = 0;
  assign a_req_ready = a_rr && (!rdyv || a_req_v != 4'd0);
  assign w_req_ready = w_rr && (!rdyv || w_req_v != 8'd0);
  assign s_req_ready = s_rr && (!rdyv || s_req_v);

  always @(negedge clk) begin
    for (int i = 0; i < 64; i++) garb[i] <= rnd(0);
    if (a_bl > 0) begin a_rr <= 1'b0; a_bl <= a_bl - 1; end
    else if (pm(ba)) begin a_rr <= 1'b0; a_bl <= rnd(0) % bla; end
    else a_rr <= !pm(pa);
    if (w_bl > 0) begin w_rr <= 1'b0; w_bl <= w_bl - 1; end
    else if (pm(bw)) begin w_rr <= 1'b0; w_bl <= rnd(0) % blw; end
    else w_rr <= !pm(pw);
    if (s_bl > 0) begin s_rr <= 1'b0; s_bl <= s_bl - 1; end
    else if (pm(bs)) begin s_rr <= 1'b0; s_bl <= rnd(0) % bls; end
    else s_rr <= !pm(ps);
  end

  // SPM response pipelines: [0] written at the grant edge, [SPM_LAT-1] drives the response
  reg [4*LB*8-1:0] a_pd [SPM_LAT];
  reg [8*LB*8-1:0] w_pd [SPM_LAT];
  reg [LB*8-1:0]   s_pd [SPM_LAT];
  reg [SPM_LAT-1:0] a_pv = '0, w_pv = '0, s_pv = '0;
  wire [2047:0] garbw = {garb[63], garb[62], garb[61], garb[60], garb[59], garb[58], garb[57], garb[56], garb[55], garb[54], garb[53],
                         garb[52], garb[51], garb[50], garb[49], garb[48], garb[47], garb[46], garb[45], garb[44], garb[43], garb[42],
                         garb[41], garb[40], garb[39], garb[38], garb[37], garb[36], garb[35], garb[34], garb[33], garb[32], garb[31],
                         garb[30], garb[29], garb[28], garb[27], garb[26], garb[25], garb[24], garb[23], garb[22], garb[21], garb[20],
                         garb[19], garb[18], garb[17], garb[16], garb[15], garb[14], garb[13], garb[12], garb[11], garb[10], garb[9],
                         garb[8], garb[7], garb[6], garb[5], garb[4], garb[3], garb[2], garb[1], garb[0]};
  assign a_rsp_data = a_pv[SPM_LAT-1] ? a_pd[SPM_LAT-1] : garbw[4*LB*8-1:0];
  assign w_rsp_data = w_pv[SPM_LAT-1] ? w_pd[SPM_LAT-1] : garbw[2047 -: 8*LB*8];
  assign s_rsp_data = s_pv[SPM_LAT-1] ? s_pd[SPM_LAT-1] : garbw[1023 -: LB*8];

  function automatic [LB*8-1:0] lane_rd(input [21:0] addr, input integer port, input integer lane);
    reg [LB*8-1:0] v;
    begin
      v = garbw[LB*8*(lane+1)-1 -: LB*8];
      if (int'(addr) + LB > SPM_BYTES) $fwrite(fl, "%0d E range %0d %0d %0d\n", cyc, port, lane, addr);
      else if (addr % LB != 0) $fwrite(fl, "%0d E align %0d %0d %0d\n", cyc, port, lane, addr);
      else for (int b = 0; b < LB; b++) v[8*b +: 8] = spm[int'(addr) + b];
      lane_rd = v;
    end
  endfunction

  // ------------------------------------------------------------------ main clocked process
  integer i, nt;
  reg [31:0] sem_n [64];
  reg [4*LB*8-1:0] a_new;
  reg [8*LB*8-1:0] w_new;
  reg [LB*8-1:0]   s_new;
  reg fault_q = 1'b0;
  always @(posedge clk) begin
    if (rst_n) begin
      // X check on the control outputs
      if ((^{q_ready, acc_valid, sem_inc_valid, fault_valid, a_req_v, w_req_v, s_req_v, dtab_req}) === 1'bx)
        $fwrite(fl, "%0d X ctl %b %b %b %b %b %b %b %b\n", cyc, q_ready, acc_valid, sem_inc_valid, fault_valid, a_req_v, w_req_v, s_req_v, dtab_req);
      // MQ
      if (q_valid && q_ready) begin
        $fwrite(fl, "%0d Q %0d %0d %0d\n", cyc, seg, qk, sem_rd_val);
        qk <= qk + 1;
        qwait <= pm(qgp) ? (rnd(0) % (qgap + 1)) : 0;
      end else if (qwait > 0) qwait <= qwait - 1;
      // DTAB
      if (dtab_gnt) begin
        dt_rv <= 1'b1; dt_data <= dtab[dtab_idx];
        dt_cnt <= (dtd > 0) ? (rnd(0) % (dtd + 1)) : 0;
      end else begin
        dt_rv <= 1'b0;
        if (dtab_req && dt_cnt > 0) dt_cnt <= dt_cnt - 1;
      end
      // SPM grants
      a_new = '0; w_new = '0; s_new = '0;
      if (a_req_v != 4'd0 && a_req_ready)
        for (int l = 0; l < 4; l++) a_new[LB*8*l +: LB*8] = a_req_v[l] ? lane_rd(a_req_addr[22*l +: 22], 0, l) : garbw[LB*8*(l+1)-1 -: LB*8];
      if (w_req_v != 8'd0 && w_req_ready)
        for (int l = 0; l < 8; l++) w_new[LB*8*l +: LB*8] = w_req_v[l] ? lane_rd(w_req_addr[22*l +: 22], 1, l) : garbw[LB*8*(l+1)-1 -: LB*8];
      if (s_req_v && s_req_ready) s_new = lane_rd(s_req_addr, 2, 0);
      a_pd[0] <= a_new; w_pd[0] <= w_new; s_pd[0] <= s_new;
      a_pv <= {a_pv, a_req_v != 4'd0 && a_req_ready};
      w_pv <= {w_pv, w_req_v != 8'd0 && w_req_ready};
      s_pv <= {s_pv, s_req_v && s_req_ready};
      for (int j = 1; j < SPM_LAT; j++) begin a_pd[j] <= a_pd[j-1]; w_pd[j] <= w_pd[j-1]; s_pd[j] <= s_pd[j-1]; end
      // ACC stream
      if (acc_valid) begin
        $fwrite(fl, "%0d A %0d %0d %b%b%b%b %h %h %h\n", cyc, acc_row, acc_ncols, acc_rescale, acc_overwrite, acc_use_cs, acc_last,
                acc_rowscale, acc_cs, acc_part);
        if ((^{acc_row, acc_ncols, acc_rescale, acc_overwrite, acc_use_cs, acc_last}) === 1'bx) $fwrite(fl, "%0d X acc\n", cyc);
        if (acc_last) begin
          nt = cyc + ACC_LAT + (pm(accp) ? (rnd(0) % (accx + 1)) : 0);
          if (dq_n > 0 && nt <= dq_last) nt = dq_last + 1;
          dq_t[(dq_h + dq_n) % 1024] = nt;
          dq_n = dq_n + 1;
          dq_last = nt;
        end
      end
      if (acc_done) begin
        $fwrite(fl, "%0d D\n", cyc);
        dq_h = (dq_h + 1) % 1024; dq_n = dq_n - 1;
      end
      if (fp_flags != 4'd0) $fwrite(fl, "%0d FL %b\n", cyc, fp_flags);
      // semaphores
      for (i = 0; i < 64; i++) sem_n[i] = sem[i];
      if (sem_inc_valid) begin
        $fwrite(fl, "%0d S %0d %0d\n", cyc, sem_inc_idx, sem_inc_amt);
        sem_n[sem_inc_idx] = sem_n[sem_inc_idx] + 32'(sem_inc_amt);
      end
      while (ek < (seg ? ne1 : ne0) && ext_t[seg][ek] <= cyc - seg_start) begin
        sem_n[ext_i[seg][ek]] = sem_n[ext_i[seg][ek]] + ext_a[seg][ek];
        $fwrite(fl, "%0d EXT %0d %0d\n", cyc, ext_i[seg][ek], ext_a[seg][ek]);
        ek = ek + 1;
      end
      for (i = 0; i < 64; i++) sem[i] <= sem_n[i];
      // fault
      if (fault_valid && !fault_q) begin
        $fwrite(fl, "%0d F %0d %h\n", cyc, fault_cause, fault_addr);
        faulted = 1;
        if (seg == 0 && rstd >= 0 && rst_pend < 0) rst_pend = cyc + rstd;
      end
      fault_q <= fault_valid;
      if (seg == 0 && rst_at >= 0 && cyc == rst_at && rst_pend < 0) rst_pend = cyc;
      // end of the run: everything taken (or a fault without a reset to come), nothing pending, quiet for a while
      if ((qk >= (seg ? n1 : n0) || (faulted && rst_pend < 0)) && dq_n == 0 && ek >= (seg ? ne1 : ne0) &&
          !acc_valid && a_req_v == 4'd0 && w_req_v == 8'd0 && !s_req_v && !dtab_req && !sem_inc_valid && rst_pend < 0 &&
          !(seg == 0 && rst_at >= 0 && cyc <= rst_at))
        idle = idle + 1;
      else idle = 0;
    end
    cyc <= cyc + 1;
  end

  // acc_done: a reg updated off the clock edge from the queue
  always @(negedge clk) acc_done <= rst_n && dq_n > 0 && dq_t[dq_h] == cyc;

  // reset sequencing (initial reset, then the segment change)
  initial begin
    #1;
    for (int s = 0; s < 64; s++) sem[s] = sem_init[0][s];
    acc_done = 1'b0;
    rst_n = 1'b0;
    repeat (3) @(negedge clk);
    rst_n = 1'b1;
    forever begin
      @(negedge clk);
      if (rst_pend >= 0 && cyc > rst_pend) begin
        rst_n = 1'b0;
        $fwrite(fl, "%0d R\n", cyc);
        seg = 1; qk = 0; qwait = 0; ek = 0; dq_n = 0; dq_h = 0; faulted = 0; rst_pend = -1;
        a_pv = '0; w_pv = '0; s_pv = '0; dt_rv = 1'b0; dt_cnt = 0;
        for (int s = 0; s < 64; s++) sem[s] = sem_init[1][s];
        repeat (2) @(negedge clk);
        seg_start = cyc;
        rst_n = 1'b1;
      end
    end
  end

  // coverage of the control situations this review is about (cycles)
  integer cv_inf4 = 0, cv_inf4d = 0, cv_freew = 0, cv_tokw = 0, cv_gapw = 0, cv_csfull = 0, cv_rsfull = 0, cv_zwait = 0, cv_astall = 0;
  always @(posedge clk) if (rst_n) begin
    if (u_dut.inf_cnt == 3'd4) cv_inf4 = cv_inf4 + 1;
    if (u_dut.inf_cnt == 3'd4 && u_dut.inf_head != u_dut.ctx_sync) cv_inf4d = cv_inf4d + 1;
    if (u_dut.lst == 2 && !u_dut.l_w_all && u_dut.l_idx == '0 && !u_dut.free_g[int'(u_dut.l_buf) * 4 + int'(u_dut.l_g)]) cv_freew = cv_freew + 1;
    if (u_dut.lst == 3 && u_dut.tok_v && !u_dut.tok_take) cv_tokw = cv_tokw + 1;
    if (u_dut.sst == 0 && u_dut.tok_v && u_dut.gap_cnt != 0) cv_gapw = cv_gapw + 1;
    if (u_dut.lst == 1 && u_dut.t_cs_en && u_dut.cs_cnt == CS_DEPTH) cv_csfull = cv_csfull + 1;
    if (u_dut.sst == 1 && u_dut.need_rs_lane && !u_dut.rs_want) cv_rsfull = cv_rsfull + 1;
    if (u_dut.u_dec.dst == 6 && u_dut.u_dec.m_zero && !u_dut.u_dec.inf_empty) cv_zwait = cv_zwait + 1;
    if (u_dut.a_want && !a_req_ready) cv_astall = cv_astall + 1;
  end

  // no-progress watchdog: nothing taken, no sem_inc, no fault, no reset for 20000 cycles
  integer last_prog = 0, nrows = 0, maxrows;
  initial if (!$value$plusargs("maxrows=%d", maxrows)) maxrows = 1000000;
  always @(posedge clk) if (acc_valid) nrows <= nrows + 1;
  always @(posedge clk) if (!rst_n || (q_valid && q_ready) || sem_inc_valid || (fault_valid && !fault_q)) last_prog <= cyc;

  // end
  always @(posedge clk) begin
    if (idle > 1000 || cyc > maxcyc || cyc - last_prog > 20000 || nrows > maxrows) begin
      $fwrite(fl, "COV inf4=%0d inf4d=%0d freew=%0d tokw=%0d gapw=%0d csfull=%0d rsfull=%0d zwait=%0d astall=%0d\n",
              cv_inf4, cv_inf4d, cv_freew, cv_tokw, cv_gapw, cv_csfull, cv_rsfull, cv_zwait, cv_astall);
      $fwrite(fl, "%0d END %s seg=%0d qk=%0d lst=%0d sst=%0d dst=%0d tok_v=%0d ctx_v=%0d inf_cnt=%0d gap=%0d free=%b cs_cnt=%0d rs_res=%0d l_w_all=%0d l_w_done=%0d l_cs_done=%0d l_g=%0d s_m=%0d\n",
              cyc, (cyc > maxcyc || cyc - last_prog > 20000 || nrows > maxrows) ? "TIMEOUT" : "IDLE", seg, qk, u_dut.lst, u_dut.sst, u_dut.u_dec.dst, u_dut.tok_v, u_dut.ctx_v,
              u_dut.inf_cnt, u_dut.gap_cnt, u_dut.free_g, u_dut.cs_cnt, u_dut.rs_res, u_dut.l_w_all, u_dut.l_w_done, u_dut.l_cs_done,
              u_dut.l_g, u_dut.s_m);
      for (i = 0; i < 64; i++) $fwrite(fl, "SEM %0d %h\n", i, sem[i]);
      $fclose(fl);
      $finish;
    end
  end
endmodule
