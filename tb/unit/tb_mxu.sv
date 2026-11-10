// Self-checking testbench for mxu (iverilog -g2012): whole MXU_MM instructions on DIM = 8 and 16 against the images of
// scripts/gen_mxu_vectors.py (build/mxu/mxu_<dim>_*.hex). The expected ACC image is computed there from the *logical* A x W
// (arith_ref PE chain per K tile, then arith_ref.acc_update), so the packer, the addressing, the loop order and the
// ACC controls are checked end to end.
// Models: SPM = byte array, fixed latency SPM_LAT, garbage in lanes that were not requested, optional random grant stalls;
// DTAB = file; semaphores = counters; ACC = FP32 array updated by the RTL's own acc_update lanes at one row per cycle,
// with a check that two updates of the same row are at least ACC_LAT cycles apart, acc_done 5 cycles after the last row.
// Optional bank model (+BANK=1 / +BANK=2 on the vvp command line; default 0 = the ideal ports above): the SPM of memmap.md section 4,
// 32 banks of 64 B, bank = addr[10:6] ^ addr[15:11], one 64 B granule read per bank and cycle. A gang request (4 A, 8 W or 1 scale
// lane) is split over as many cycles as its busiest bank has distinct granules (lanes of one 64 B row are one read, which is how the
// shim of mxu.md M-10 serves DIM-byte lanes); `ready` stays low meanwhile and rises in the cycle the last lane is read (the grant is
// the commit), the data follow exactly SPM_LAT later. +BANK=1: the three ports have banks of their own (the old S-4 model);
// +BANK=2: the ports share the banks, rotating priority per cycle. PERF = 1 adds the tile-period report (mxu.md section 7).
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
  parameter int RDYV      = 0,       // 1: a port's ready is low while its request has no valid lane (M-23); +RDYV=n overrides
  parameter int PERF      = 0,       // 1: report the distribution of the gaps between consecutive output rows (M = 1: the tile period)
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
  logic [31:0]           expv [0:6];            // flags, sem[1..5], ordering pair {A, B} (6 bits each)
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
  logic a_req_ready, a_rdy_r, a_bk_rdy;
  logic [4*LB*8-1:0] a_rsp_data;
  logic [7:0] w_req_v;
  logic [8*22-1:0] w_req_addr;
  logic w_req_ready, w_rdy_r, w_bk_rdy;
  logic [8*LB*8-1:0] w_rsp_data;
  logic s_req_v;
  logic [21:0] s_req_addr;
  logic s_req_ready, s_rdy_r, s_bk_rdy;
  logic [LB*8-1:0] s_rsp_data;
  logic acc_valid, acc_rescale, acc_overwrite, acc_use_cs, acc_last, acc_done;
  logic [10:0] acc_row;
  logic [DIM*32-1:0] acc_part, acc_cs;
  logic [6:0] acc_ncols;
  logic [31:0] acc_rowscale;
  logic sem_inc_valid, fault_valid;
  logic [5:0] sem_inc_idx;
  logic [3:0] sem_inc_amt, fp_flags, fault_cause;
  logic [63:0] fault_addr;

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


  longint cyc = 0;
  int sem_incs = 0;
  longint first_inc [0:63];
  initial for (int i = 0; i < 64; i++) first_inc[i] = -1;
  logic [5:0] last_done_cnt;
  int pending_done = 0;       // acc_done pulses issued and not yet matched by a sem_inc
  always @(posedge clk) begin
    if (!rst_n) begin
      for (int i = 0; i < 64; i++) sem[i] <= 32'd0;
    end else if (sem_inc_valid) begin
      sem[sem_inc_idx] <= sem[sem_inc_idx] + 32'(sem_inc_amt);
      if (first_inc[sem_inc_idx] < 0) first_inc[sem_inc_idx] = cyc;
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
  // ready = the random / blocking pattern (registered) and the bank model (combinational, always 1 when it is off)
  // +RDYV=1 (M-23, review R-2): a port's ready is low whenever its request has no valid lane, so an MXU that waits for the
  // grant of an empty request hangs (a stricter SPM than the default, which raises ready whatever valid)
  int rdyv_mode = RDYV;
  initial void'($value$plusargs("RDYV=%d", rdyv_mode));
  assign a_req_ready = a_rdy_r && a_bk_rdy && (rdyv_mode == 0 || a_req_v != 4'd0);
  assign w_req_ready = w_rdy_r && w_bk_rdy && (rdyv_mode == 0 || w_req_v != 8'd0);
  assign s_req_ready = s_rdy_r && s_bk_rdy && (rdyv_mode == 0 || s_req_v);
  always @(posedge clk) begin
    if (a_req_v != 4'd0 && a_req_ready && A_BLOCK > 0) begin blk <= A_BLOCK; a_rdy_r <= 1'b0; end
    else if (blk > 1) begin blk <= blk - 1; a_rdy_r <= 1'b0; end
    else begin blk <= 0; a_rdy_r <= ($urandom_range(0, 99) >= STALL_PCT); end
    w_rdy_r <= ($urandom_range(0, 99) >= STALL_PCT);
    s_rdy_r <= ($urandom_range(0, 99) >= STALL_PCT);
  end

  // ---------------- bank model (see the header; lanes 0..3 = A, 4..11 = W, 12 = scales)
  int bank_mode = 0;
  initial void'($value$plusargs("BANK=%d", bank_mode));
  logic [12:0]    bk_v;
  logic [13*22-1:0] bk_addr;
  assign bk_v    = {s_req_v, w_req_v, a_req_v};
  assign bk_addr = {s_req_addr, w_req_addr, a_req_addr};
  logic [12:0]    bk_coll = '0;            // lanes read so far for the gang being held
  logic [13*22-1:0] bk_key = '0;           // their addresses (a changed request starts again)
  logic [12:0]    bk_pend, bk_serve, bk_cont;
  int             bk_rr = 0;
  int             bk_win, bk_p, bk_pl, bk_pw, bk_dom;
  logic [21:0]    bk_a1, bk_a2;
  logic [4:0]     bk_b1, bk_b2;
  function automatic int lane_port(input int l);
    return (l < 4) ? 0 : (l < 12) ? 1 : 2;
  endfunction
  always @* begin
    bk_serve = '0; bk_cont = '0; bk_pend = '0;
    for (int l = 0; l < 13; l++) begin
      bk_cont[l] = bk_coll[l] && bk_v[l] && (bk_key[22*l +: 22] == bk_addr[22*l +: 22]);
      bk_pend[l] = bk_v[l] && !bk_cont[l];
    end
    if (bank_mode != 0)
      for (int dom = 0; dom < 3; dom++)           // mode 1: one arbitration domain per port; mode 2: one for all
        for (int b = 0; b < 32; b++) begin
          bk_win = -1;
          for (int pi = 0; pi < 3; pi++) begin
            bk_p = (bk_rr + pi) % 3;
            for (int l = 0; l < 13; l++) begin
              bk_a1 = bk_addr[22*l +: 22];
              bk_b1 = bk_a1[10:6] ^ bk_a1[15:11];
              if (bk_win < 0 && lane_port(l) == bk_p && bk_pend[l] && (bank_mode == 2 ? dom == 0 : lane_port(l) == dom) && int'(bk_b1) == b)
                bk_win = l;
            end
          end
          if (bk_win >= 0)
            for (int l = 0; l < 13; l++)           // one read serves every lane of the same port in the same 64 B granule
              if (bk_pend[l] && lane_port(l) == lane_port(bk_win) && bk_addr[22*l+6 +: 16] == bk_addr[22*bk_win+6 +: 16])
                bk_serve[l] = 1'b1;
        end
    else bk_serve = bk_pend;
  end
  assign a_bk_rdy = (bk_pend[3:0]  & ~bk_serve[3:0])  == 4'd0;
  assign w_bk_rdy = (bk_pend[11:4] & ~bk_serve[11:4]) == 8'd0;
  assign s_bk_rdy = (bk_pend[12]   & ~bk_serve[12])   == 1'b0;
  // statistics (all ports): beats granted, beats that took more than one cycle, cycles a request waited for the banks
  longint bk_beats [3], bk_slow [3], bk_wait [3];
  logic [2:0] bk_first = 3'b111;                   // the port's request is in its first cycle
  initial for (int i = 0; i < 3; i++) begin bk_beats[i] = 0; bk_slow[i] = 0; bk_wait[i] = 0; end
  always @(posedge clk) if (rst_n) begin
    bk_rr <= (bk_rr + 1) % 3;
    for (int l = 0; l < 13; l++) begin
      bk_key[22*l +: 22] <= bk_addr[22*l +: 22];
      // the gang of a port is granted (valid and ready of the bank model; random stalls are not the bank model's business)
      if ((lane_port(l) == 0 ? a_bk_rdy : lane_port(l) == 1 ? w_bk_rdy : s_bk_rdy)) bk_coll[l] <= 1'b0;
      else bk_coll[l] <= bk_cont[l] || bk_serve[l];
    end
    if (a_req_v != 0) begin if (a_bk_rdy) begin bk_beats[0]++; if (!bk_first[0]) bk_slow[0]++; bk_first[0] <= 1'b1; end else begin bk_wait[0]++; bk_first[0] <= 1'b0; end end
    if (w_req_v != 0) begin if (w_bk_rdy) begin bk_beats[1]++; if (!bk_first[1]) bk_slow[1]++; bk_first[1] <= 1'b1; end else begin bk_wait[1]++; bk_first[1] <= 1'b0; end end
    if (s_req_v)      begin if (s_bk_rdy) begin bk_beats[2]++; if (!bk_first[2]) bk_slow[2]++; bk_first[2] <= 1'b1; end else begin bk_wait[2]++; bk_first[2] <= 1'b0; end end
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
  longint last_row_cyc = 0;
  logic [4:0] dsr = 5'd0;
  assign acc_done = dsr[4];       // five cycles after the last row
  longint row_last [0:ACCR-1];
  int done_cnt = 0;
  logic [3:0] flags_or = 4'd0;
  int rows_seen = 0;
  longint rowcyc [0:4095];
  always @(posedge clk) begin
    cyc <= cyc + 1;
    dsr <= {dsr[3:0], rst_n && acc_valid && acc_last};
    if (rst_n) begin
      flags_or <= flags_or | fp_flags;
      if (acc_valid) begin
        if (MAX_PERIOD > 0 && rows_seen > 0 && cyc - last_row_cyc > MAX_PERIOD) begin
          errors++; $display("%s: pass period %0d cycles > %0d", PRE, cyc - last_row_cyc, MAX_PERIOD);
        end
        if (PERF && rows_seen < 4096) rowcyc[rows_seen] = cyc;
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

  // ---------------- tile period (PERF): the gaps between consecutive output rows; with M = 1 every pass is one row
  task automatic perf_report();
    int n, lo, hi, w0, w1;
    longint sum, g, mn, mx;
    int hist [0:127];
    n = rows_seen - 1;                                  // gaps
    w0 = 4; w1 = n - 3;                                 // steady state: leave out the first 4 and the last 3 gaps (fill and drain)
    sum = 0; mn = 1 << 30; mx = 0;
    for (int i = 0; i < 128; i++) hist[i] = 0;
    for (int i = w0; i < w1; i++) begin
      g = rowcyc[i + 1] - rowcyc[i];
      sum += g; if (g < mn) mn = g; if (g > mx) mx = g;
      hist[(g > 127) ? 127 : int'(g)]++;
    end
    $write("%sPERF DIM %0d bank %0d: passes %0d, first row at cycle %0d, last at %0d; steady-state gaps %0d..%0d: mean %0d.%02d min %0d max %0d, histogram",
           PRE, DIM, bank_mode, rows_seen, rowcyc[0], rowcyc[rows_seen-1], w0, w1 - 1, sum / (w1 - w0), (sum * 100 / (w1 - w0)) % 100, mn, mx);
    for (int i = 0; i < 128; i++) if (hist[i] != 0) $write(" %0d:%0d", i, hist[i]);
    $write("\nPERFALL DIM %0d bank %0d: whole instruction (last row - first row) / (passes - 1) = %0d.%02d\n", DIM, bank_mode,
           (rowcyc[rows_seen-1] - rowcyc[0]) / (rows_seen - 1), ((rowcyc[rows_seen-1] - rowcyc[0]) * 100 / (rows_seen - 1)) % 100);
  endtask

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
        wait (qi == NINS && !q_valid && sem[1] == expv[1] && sem[2] == expv[2] && sem[3] == expv[3] && sem[4] == expv[4] && sem[5] == expv[5] && dut.inf_cnt == 0);
      end
      begin : w_time
        repeat (`ifdef QUICK 6000 `else 40000 `endif) @(posedge clk);
        errors++; $display("%s: TIMEOUT (qi=%0d sem1..5=%0d %0d %0d %0d %0d inf=%0d)", PRE, qi, sem[1], sem[2], sem[3], sem[4], sem[5], dut.inf_cnt);
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
    for (int i = 1; i <= 5; i++)
      if (sem[i] != expv[i]) begin errors++; $display("%s: sem[%0d] = %0d exp %0d", PRE, i, sem[i], expv[i]); end
    // retirement in order: the first increment of sem[A] (an instruction with rows) comes before the first one of sem[B] (an M = 0
    // instruction queued behind it)
    if (expv[6] != 0 && (first_inc[expv[6][11:6]] < 0 || first_inc[expv[6][5:0]] < 0 || first_inc[expv[6][11:6]] >= first_inc[expv[6][5:0]])) begin
      errors++; $display("%s: sem[%0d] incremented (cycle %0d) before sem[%0d] (cycle %0d): out-of-order completion", PRE, expv[6][5:0], first_inc[expv[6][5:0]], expv[6][11:6], first_inc[expv[6][11:6]]);
    end
    if (rows_seen == 0) begin errors++; $display("%s: no rows reached the ACC", PRE); end
    $display("%s: %0d ACC rows in %0d cycles, flags %h", PRE, rows_seen, cyc, flags_or);
    if (bank_mode != 0)
      $display("%s: bank model %0d: beats A %0d W %0d S %0d; beats that took > 1 cycle A %0d W %0d S %0d; cycles waited A %0d W %0d S %0d", PRE, bank_mode,
               bk_beats[0], bk_beats[1], bk_beats[2], bk_slow[0], bk_slow[1], bk_slow[2], bk_wait[0], bk_wait[1], bk_wait[2]);
    if (PERF) perf_report();
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
  logic [4+64+256+256+128+6*64+32-1:0] mem [0:N-1];
  logic q_valid = 0, q_ready;
  logic [127:0] q_instr = '0;
  logic [6*64-1:0] q_regs = '0;
  logic [31:0] q_wval = '0;
  logic [5:0] sem_rd_idx;
  logic dtab_req, dtab_gnt = 0, dtab_rvalid = 0;
  logic [7:0] dtab_idx;
  logic [255:0] dtab_data, td_a, td_w;
  logic [3:0] a_req_v, sem_inc_amt, fp_flags, fault_cause, cause_exp;
  logic [63:0] addr_exp;
  logic [4*22-1:0] a_req_addr;
  logic [7:0] w_req_v;
  logic [8*22-1:0] w_req_addr;
  logic s_req_v, acc_valid, acc_rescale, acc_overwrite, acc_use_cs, acc_last, sem_inc_valid, fault_valid;
  logic [21:0] s_req_addr;
  logic [10:0] acc_row;
  logic [DIM*32-1:0] acc_part, acc_cs;
  logic [6:0] acc_ncols;
  logic [31:0] acc_rowscale;
  logic [63:0] fault_addr;
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
      {cause_exp, addr_exp, td_w, td_a, q_instr, q_regs, q_wval} = mem[k];
      #1 rst_n = 1; q_valid = 1;
      repeat (60) @(posedge clk);
      if (!fault_valid || fault_cause !== cause_exp) begin
        errors++; $display("fault case %0d: valid %b cause %0d expected %0d", k, fault_valid, fault_cause, cause_exp);
      end else if (fault_addr !== addr_exp) begin
        errors++; $display("fault case %0d: cause %0d FAULT_ADDR %h expected %h", k, fault_cause, fault_addr, addr_exp);
      end
      if (spm_touch != 0) begin errors++; $display("fault case %0d: %0d SPM / ACC / semaphore actions", k, spm_touch); end
      if (q_ready) begin errors++; $display("fault case %0d: queue still popping", k); end
    end
    done = 1;
  end
endmodule

`ifdef PERF
// Tile-period measurement (mxu.md section 7; scripts/mxu_perf.sh): one M = 1 instruction on a `PDIM x `PDIM MXU, vectors from
// `gen_mxu_vectors.py --perf` (hex files and counts.svh in the working directory), banks selected by +BANK=n.
module tb_mxu;
  logic d;
  int e;
  mxu_tc #(.DIM(`PDIM), .NINS(`CNT_PERF_PROG), .NTD(`CNT_PERF_DTAB), .SPMB(`CNT_PERF_SPM), .ACCR(`CNT_PERF_ACC0), .STALL_PCT(0), .PERF(1), .PRE("")) t (.done(d), .errors(e));
  initial begin
    wait (d);
    if (e == 0) $display("PASS mxu"); else $display("FAIL mxu: %0d errors", e);
    $finish;
  end
endmodule
`else
module tb_mxu;
  logic d8, d16, d8s, d16s, d32, d8b, dr8, dr16;
  int e8, e16, e8s, e16s, e32, e8b, er8, er16;
`ifdef ONLY32
  assign d8 = 1, e8 = 0, d16 = 1, e16 = 0, d8s = 1, e8s = 0, d16s = 1, e16s = 0, d8b = 1, e8b = 0, dr8 = 1, er8 = 0, dr16 = 1, er16 = 0;
  mxu_tc #(.DIM(32), .NINS(`CNT_MXU_32_PROG), .NTD(`CNT_MXU_32_DTAB), .SPMB(`CNT_MXU_32_SPM), .ACCR(`CNT_MXU_32_ACC0), .STALL_PCT(0), .MAX_PERIOD(26), .PRE("mxu_32_")) t32 (.done(d32), .errors(e32));
`elsif ONLY16
  assign d8 = 1, e8 = 0, d8s = 1, e8s = 0, d16s = 1, e16s = 0, d32 = 1, e32 = 0, d8b = 1, e8b = 0, dr8 = 1, er8 = 0, dr16 = 1, er16 = 0;
  mxu_tc #(.DIM(16), .NINS(`CNT_MXU_16_PROG), .NTD(`CNT_MXU_16_DTAB), .SPMB(`CNT_MXU_16_SPM), .ACCR(`CNT_MXU_16_ACC0), .STALL_PCT(0),  .PRE("mxu_16_")) t16 (.done(d16), .errors(e16));
`elsif QUICK
  assign d16 = 1, d8s = 1, d16s = 1, d32 = 1, e16 = 0, e8s = 0, e16s = 0, e32 = 0, d8b = 1, e8b = 0, dr8 = 1, er8 = 0, dr16 = 1, er16 = 0;
  mxu_tc #(.DIM(8),  .NINS(`CNT_MXU_8_PROG),  .NTD(`CNT_MXU_8_DTAB),  .SPMB(`CNT_MXU_8_SPM),  .ACCR(`CNT_MXU_8_ACC0),  .STALL_PCT(0),  .PRE("mxu_8_"))  t8  (.done(d8),  .errors(e8));
`else
  mxu_tc #(.DIM(8),  .NINS(`CNT_MXU_8_PROG),  .NTD(`CNT_MXU_8_DTAB),  .SPMB(`CNT_MXU_8_SPM),  .ACCR(`CNT_MXU_8_ACC0),  .STALL_PCT(0),  .PRE("mxu_8_"))  t8  (.done(d8),  .errors(e8));
  mxu_tc #(.DIM(8),  .NINS(`CNT_MXU_8_PROG),  .NTD(`CNT_MXU_8_DTAB),  .SPMB(`CNT_MXU_8_SPM),  .ACCR(`CNT_MXU_8_ACC0),  .STALL_PCT(30), .ACC_LAT(24), .PRE("mxu_8_"))  t8s (.done(d8s), .errors(e8s));
  mxu_tc #(.DIM(16), .NINS(`CNT_MXU_16_PROG), .NTD(`CNT_MXU_16_DTAB), .SPMB(`CNT_MXU_16_SPM), .ACCR(`CNT_MXU_16_ACC0), .STALL_PCT(0),  .PRE("mxu_16_")) t16 (.done(d16), .errors(e16));
  mxu_tc #(.DIM(8),  .NINS(`CNT_MXU_8_PROG),  .NTD(`CNT_MXU_8_DTAB),  .SPMB(`CNT_MXU_8_SPM),  .ACCR(`CNT_MXU_8_ACC0),  .STALL_PCT(0), .A_BLOCK(40), .PRE("mxu_8_"))  t8b (.done(d8b), .errors(e8b));
  mxu_tc #(.DIM(16), .NINS(`CNT_MXU_16_PROG), .NTD(`CNT_MXU_16_DTAB), .SPMB(`CNT_MXU_16_SPM), .ACCR(`CNT_MXU_16_ACC0), .STALL_PCT(30), .PRE("mxu_16_")) t16s (.done(d16s), .errors(e16s));
  mxu_tc #(.DIM(32), .NINS(`CNT_MXU_32_PROG), .NTD(`CNT_MXU_32_DTAB), .SPMB(`CNT_MXU_32_SPM), .ACCR(`CNT_MXU_32_ACC0), .STALL_PCT(0), .MAX_PERIOD(26), .PRE("mxu_32_")) t32 (.done(d32), .errors(e32));
  // M-23: SPM whose ready is low for a request with no valid lane (the MXU must never wait for the grant of an empty beat)
  mxu_tc #(.DIM(8),  .NINS(`CNT_MXU_8_PROG),  .NTD(`CNT_MXU_8_DTAB),  .SPMB(`CNT_MXU_8_SPM),  .ACCR(`CNT_MXU_8_ACC0),  .STALL_PCT(30), .RDYV(1), .PRE("mxu_8_"))  t8r  (.done(dr8),  .errors(er8));
  mxu_tc #(.DIM(16), .NINS(`CNT_MXU_16_PROG), .NTD(`CNT_MXU_16_DTAB), .SPMB(`CNT_MXU_16_SPM), .ACCR(`CNT_MXU_16_ACC0), .STALL_PCT(0), .RDYV(1), .PRE("mxu_16_")) t16r (.done(dr16), .errors(er16));
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
    wait (d8 && d16 && d8s && d16s && d32 && d8b && dr8 && dr16 && df);
    if (e8 + e16 + e8s + e16s + e32 + e8b + er8 + er16 + ef == 0) $display("PASS mxu");
    else $display("FAIL mxu: %0d errors (8: %0d, 8 stalls: %0d, 8 long A stalls: %0d, 16: %0d, 16 stalls: %0d, 32: %0d, 8 / 16 ready-needs-valid: %0d / %0d, faults: %0d)", e8 + e16 + e8s + e16s + e32 + e8b + er8 + er16 + ef, e8, e8s, e8b, e16, e16s, e32, er8, er16, ef);
    $finish;
  end
endmodule
`endif
