// Self-checking testbench for mxu (iverilog -g2012): whole MXU_MM instructions on DIM = 8 and 16 against the images of
// scripts/gen_mxu_vectors.py (build/mxu/mxu_<dim>_*.hex). The expected ACC image is computed there from the *logical* A x W
// (arith_ref PE chain per K tile, then arith_ref.acc_update), so the packer, the addressing, the loop order and the
// ACC controls are checked end to end.
// Models: SPM = byte array, fixed latency SPM_LAT, garbage in lanes that were not requested, optional random grant stalls;
// DTAB = file; semaphores = counters; ACC = FP32 array updated by the RTL's own acc_update lanes at one row per cycle,
// with a check that two updates of the same row are at least ACC_LAT cycles apart; acc_done exactly ACC_LAT cycles after the acc_last row
// (acc.md: DONE_EXTRA = 0), or ACC_LAT + a random extra delay, in order (DONE_EXTRA > 0: lets the in-flight FIFO fill, review H-2 / T-5).
// Optional bank model (+BANK=1 / +BANK=2 on the vvp command line; default 0 = the ideal ports above): the SPM of memmap.md section 4,
// 32 banks of 64 B, bank = addr[10:6] ^ addr[15:11], one 64 B granule read per bank and cycle. A gang request (4 A, 8 W or 1 scale
// lane) is split over as many cycles as its busiest bank has distinct granules (lanes of one 64 B row are one read, which is how the
// shim of mxu.md M-10 serves DIM-byte lanes); `ready` stays low meanwhile and rises in the cycle the last lane is read (the grant is
// the commit), the data follow exactly SPM_LAT later. +BANK=1: the three ports have banks of their own (the old S-4 model);
// +BANK=2: the ports share the banks, rotating priority per cycle. PERF = 1 adds the tile-period report (mxu.md section 7).
// Checks: final ACC image (bit-exact); fp_flags in every cycle against the per-wave PE flags of build/mxu/<pre>flg.hex (PE row i of a wave
// that leaves the array at cycle c is flagged at c - DIM + 1 + i, i.e. at T + i + 3 for the wave whose A row entered at T: this is the
// fp_flags port contract of mxu.md 5, a pulse, column mask applied, nothing in a cycle without a flagged wave) and their OR; the sem_inc
// sequence ({idx, amt} of every instruction with a semaphore, in order) and that no sem_inc comes before the acc_done of its rows; acc_last
// on exactly the last row of every instruction (ins.hex); the final value of every one of the 64 semaphores (sem.hex: the preloaded ones
// and 32 .. 63 included); the period of an M = 1 instruction with a bound (CHKP) and the total cycle count against the recorded one
// (CHKC, exp.hex row 12, within 2 % either way); no fault, no ACC hazard, the program finished within the time limit; the row counts of every
// vector file; counters that say the FIFO-full paths were taken (EXP_CS_FULL, EXP_INF_FULL: the column-scale one reads the loader state by
// its name, the in-flight one counts against the spec depth of mxu.md 4.5, INF_DEPTH = 4, not against the RTL's own full flag).
// CHKP and CHKC are regression bounds of this RTL, not properties of the spec. CHKP: the period bounds (9 / 13 / 26 cycles dense, 9 / 14
// sparse) were measured at SPM_LAT 2 (t16x: 3), ACC_LAT 8, ideal ports (STALL_PCT 0, no DONE_EXTRA) and hold for no other parameter set (the
// bound grows by about a cycle per SPM_LAT, and is ACC_LAT + 1 at ACC_LAT >= 24). It is checked with ideal ports and with +BANK=1 (own
// banks per port: every placement of the instruction that follows the mxu.md 7 rules, A 256 B and W 2 KiB aligned, table pitch, gave the
// same period) and NOT with +BANK=2: with shared banks an A lane can meet a W lane in the same bank, and whether it does depends on where
// the allocator puts the instruction (6 of 10 placements cost one more cycle, review TR2-1), i.e. on every case ahead of it.
// CHKC: the exp.hex row 12 of a program holds the count and the configuration it was measured in ({ACC_LAT, CS_DEPTH, SPM_LAT, count});
// the check runs only when the instance has exactly that configuration (those three, STALL_PCT 0, DONE_EXTRA 0, A_BLOCK 0, RDYV 0, no
// bank model) and prints a "skipped" line otherwise (a different CS_DEPTH changes the count by 4-16 %, a bank model by the conflicts of
// the layout). It is two-sided: more than 2 % below the record fails too, so that a stale count (a faster RTL, a shorter program) is
// re-measured and does not loosen the bound. A legal but slower redesign fails by design: re-measure (mxu.md 7), update the generator.
// SPARSE = 1: the SPM is the windows of exp.hex rows 8 .. 11 and 14 in a 4 MiB address space (garbage elsewhere); ACCR = 2048 rows.
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
  parameter int CS_DEPTH  = 4,
  parameter int NFLG      = 1,       // rows of <pre>flg.hex (ACC rows of the whole program)
  parameter int DONE_EXTRA = 0,      // > 0: acc_done comes ACC_LAT + (0 .. DONE_EXTRA) cycles after the acc_last row (in order)
  parameter int SPARSE    = 0,       // 1: windowed SPM (see the header), SPMB = rows of spm.hex, the MXU's SPM_BYTES is SPM_TOP
  parameter int SPM_TOP   = 0,
  parameter int CHKP      = 0,       // 1: the period bound (maxp of ins.hex) of an M = 1 instruction is checked (needs SPM_LAT 2 (t16x 3), ACC_LAT 8, ideal ports or +BANK=1; skipped with +BANK=2)
  parameter int CHKC      = 0,       // 1: the total cycle count is within 2 % of exp.hex row 12, if the instance has the configuration recorded there
  parameter int EXP_CS_FULL  = 0,    // 1: the column-scale FIFO must have been full (a tile waited for a slot) at least once
  parameter int EXP_INF_FULL = 0,    // 1: the in-flight FIFO must have been full with a head that differs from the instruction behind it
  parameter int RDYV      = 0,       // 1: a port's ready is low while its request has no valid lane (M-23); +RDYV=n overrides
  parameter int PERF      = 0,       // 1: report the distribution of the gaps between consecutive output rows (M = 1: the tile period)
  parameter string PRE    = ""
) (
  output logic done,
  output int   errors
);
  localparam int LB = DIM;
  localparam int KB = 4 * DIM;
  localparam int SPMX = (SPM_TOP != 0) ? SPM_TOP : SPMB;     // the MXU's SPM_BYTES
  logic clk = 0, rst_n = 0;
  always #5 clk = ~clk;

  // ---------------- images (the row counts and widths of the files are checked by fcheck below)
  logic [7:0]            spm  [0:SPMB-1];
  logic [255:0]          dtab [0:NTD-1];
  logic [128+6*64+32-1:0] prog [0:NINS-1];
  logic [DIM*32-1:0]     acc0 [0:ACCR-1];
  logic [DIM*32-1:0]     acc1 [0:ACCR-1];
  logic [63:0]           expv [0:14];           // flags, sem[1..5], ordering pair {A, B} (6 bits each), semaphore preload {idx, value}, 4 windows {base, size}, {config, cycles}, preload 2, window 5
  logic [31:0]           semx [0:63];           // the final value of every semaphore
  logic [63:0]           insx [0:NINS-1];         // per instruction {maxp[7:0], nrows[23:0], ssem[5:0], sinc[3:0], 22'b0}
  logic [DIM*4-1:0]      flg  [0:NFLG-1];
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

  mxu #(.DIM(DIM), .SPM_LAT(SPM_LAT), .ACC_LAT(ACC_LAT), .CS_DEPTH(CS_DEPTH), .ACC_ROWS(ACCR), .SPM_BYTES(SPMX)) dut (.*);

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
  int pre_idx = 0;            // semaphore preload (exp.hex row 7): sem[pre_idx] starts at pre_val (a wait across 2^32)
  logic [31:0] pre_val = 32'd0;
  int pre_idx2 = 0;           // a second one (exp.hex row 13): a wait across 2^31
  logic [31:0] pre_val2 = 32'd0;
  always @(posedge clk) begin
    if (!rst_n) begin
      for (int i = 0; i < 64; i++) sem[i] <= (i == pre_idx && pre_idx != 0) ? pre_val : (i == pre_idx2 && pre_idx2 != 0) ? pre_val2 : 32'd0;
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
  localparam int NWIN = 5;
  int win_base [NWIN];
  int win_size [NWIN];
  int win_off  [NWIN];
  function automatic logic [7:0] rb(input int addr, input int garbage);
    if (!SPARSE) begin
      if (addr >= 0 && addr < SPMB) return spm[addr];
      return 8'hEE;
    end
    if (addr < 0 || addr >= SPMX) return 8'hEE;
    for (int w = 0; w < NWIN; w++)
      if (win_size[w] != 0 && addr >= win_base[w] && addr < win_base[w] + win_size[w]) return spm[win_off[w] + addr - win_base[w]];
    return addr[0] ? 8'h7F : 8'hFF;           // inside the SPM, outside every window: the NaN-valued garbage of the dense image
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
      if (a_req_addr[22*l +: 22] % LB != 0 || int'(a_req_addr[22*l +: 22]) + LB > SPMX) begin errors++; $display("%s: bad A lane addr %0d", PRE, a_req_addr[22*l +: 22]); end
    end
    for (int l = 0; l < 8; l++) if (w_req_v[l] && w_req_ready) begin
      if (w_req_addr[22*l +: 22] % LB != 0 || int'(w_req_addr[22*l +: 22]) + LB > SPMX) begin errors++; $display("%s: bad W lane addr %0d", PRE, w_req_addr[22*l +: 22]); end
    end
    if (s_req_v && s_req_ready && (s_req_addr % LB != 0 || int'(s_req_addr) + LB > SPMX)) begin errors++; $display("%s: bad S addr %0d", PRE, s_req_addr); end
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
  logic spur_done = 1'b0;         // I-9 (review part I): one acc_done with nothing in flight, right after reset; it must be ignored
  // acc_done: a queue of the cycles at which the pulses are due (ACC_LAT after the acc_last row, plus a random extra delay in order)
  longint done_at [0:1023];
  int dq_wr = 0, dq_rd = 0;
  longint last_sched = -1;
  logic done_pulse = 1'b0;
  assign acc_done = done_pulse | spur_done;
  always @(posedge clk) if (spur_done && sem_inc_valid) begin errors++; $display("%s: sem_inc for an acc_done with nothing in flight", PRE); end
  longint row_last [0:ACCR-1];
  int done_cnt = 0;               // acc_done pulses of the ACC model so far (the one of this cycle included)
  logic [3:0] flags_or = 4'd0;
  int rows_seen = 0;
  longint rowcyc [0:4095];
  // instruction attribution of the ACC rows and the sem_inc sequence (ins.hex)
  int ins_rows [0:NINS-1], ins_maxp [0:NINS-1], ins_sidx [0:NINS-1], ins_samt [0:NINS-1], need_done [0:NINS-1], sem_ins [0:NINS-1];
  int nsem = 0, sem_ptr = 0, row_ins = 0, row_in = 0, total_rows = 0;
  logic prev_last = 1'b1;
  longint maxgap = 0;              // largest gap between rows of one instruction with a period bound
  // flags: the history of fp_flags and the flags the waves that have left the array so far expect for the cycles they cover
  logic [3:0] fobs [0:127];
  logic [3:0] fexp [0:127];
  initial for (int i = 0; i < 128; i++) begin fobs[i] = 4'd0; fexp[i] = 4'd0; end
  // coverage of the FIFO-full paths
  localparam int INF_DEPTH = 4;      // the in-flight instruction FIFO of mxu.md 4.5
  int cs_full_cyc = 0, inf_full_cyc = 0, inf_diff_cyc = 0;
  int fx;
  always @(posedge clk) begin
    cyc <= cyc + 1;
    if (rst_n) begin
      if (done_pulse) begin done_cnt++; dq_rd++; end
      // ---- retirement: the sem_inc sequence, and never before the acc_done that covers the rows of the instruction
      if (sem_inc_valid && !spur_done) begin
        if (sem_ptr >= nsem) begin errors++; $display("%s: unexpected sem_inc (%0d, %0d)", PRE, sem_inc_idx, sem_inc_amt); end
        else begin
          if (sem_inc_idx != ins_sidx[sem_ins[sem_ptr]] || sem_inc_amt != ins_samt[sem_ins[sem_ptr]]) begin
            errors++; if (errors < 12) $display("%s: sem_inc #%0d is (%0d, %0d), instruction %0d has (%0d, %0d)", PRE, sem_ptr, sem_inc_idx, sem_inc_amt,
                                                  sem_ins[sem_ptr], ins_sidx[sem_ins[sem_ptr]], ins_samt[sem_ins[sem_ptr]]);
          end
          if (done_cnt < need_done[sem_ins[sem_ptr]]) begin
            errors++; if (errors < 12) $display("%s: sem_inc of instruction %0d at cycle %0d after %0d acc_done pulses, %0d needed", PRE, sem_ins[sem_ptr], cyc, done_cnt, need_done[sem_ins[sem_ptr]]);
          end
          sem_ptr++;
        end
      end
      // ---- coverage
      // the loader waits in L_CALC for a column-scale slot (t_cs_en, cs_cnt at the depth): the state is read by its name (enum method, no
      // encoding; the hierarchical enum item `dut.L_CALC` is an internal error of Verilator 5.052, TR2-11). The in-flight FIFO is counted
      // as full at the depth of the SPEC (mxu.md 4.5: 4), not at the RTL's own full flag: a FIFO of any other depth must not satisfy it (TR2-3)
      if (dut.lst.name() == "L_CALC" && dut.t_cs_en && dut.cs_cnt >= CS_DEPTH) cs_full_cyc++;
      if (dut.inf_cnt > INF_DEPTH) begin errors++; if (errors < 12) $display("%s: the in-flight FIFO holds %0d entries, the spec depth is %0d", PRE, dut.inf_cnt, INF_DEPTH); end
      if (dut.inf_cnt >= INF_DEPTH) begin
        inf_full_cyc++;
        if (dut.inf_head != dut.inf_in && dut.inf_head[9:4] != 6'd0 && dut.inf_head[3:0] != 4'd0) inf_diff_cyc++;
      end
      // ---- flags: record this cycle, then the waves that leave the array now add their PE rows' flags to the cycles c - DIM + 1 .. c
      fobs[cyc % 128] = fp_flags;
      flags_or <= flags_or | fp_flags;
      if (acc_valid) begin
        if (row_ins >= NINS) begin errors++; if (errors < 12) $display("%s: ACC row %0d beyond the last instruction", PRE, rows_seen); end
        else begin
          if (acc_last !== (row_in == ins_rows[row_ins] - 1)) begin
            errors++; if (errors < 12) $display("%s: acc_last = %b on row %0d of instruction %0d (%0d rows)", PRE, acc_last, row_in, row_ins, ins_rows[row_ins]);
          end
          if (rows_seen > 0 && !prev_last && ins_maxp[row_ins] != 0) begin
            if (cyc - last_row_cyc > maxgap) maxgap = cyc - last_row_cyc;
            if (CHKP != 0 && bank_mode != 2 && cyc - last_row_cyc > ins_maxp[row_ins]) begin
              errors++; $display("%s: pass period %0d cycles > %0d (instruction %0d)", PRE, cyc - last_row_cyc, ins_maxp[row_ins], row_ins);
            end
          end
          row_in++;
          if (row_in >= ins_rows[row_ins]) begin
            row_in = 0; row_ins++;
            while (row_ins < NINS && ins_rows[row_ins] == 0) row_ins++;
          end
        end
        prev_last = acc_last;
        if (rows_seen < NFLG)
          for (int i = 0; i < DIM; i++) begin
            fx = (cyc - DIM + 1 + i) % 128; if (fx < 0) fx += 128;
            fexp[fx] = fexp[fx] | flg[rows_seen][4*i +: 4];
          end
        if (acc_last) begin
          done_at[dq_wr % 1024] = cyc + ACC_LAT + (DONE_EXTRA > 0 ? $urandom_range(0, DONE_EXTRA) : 0);
          if (done_at[dq_wr % 1024] <= last_sched) done_at[dq_wr % 1024] = last_sched + 1;     // in order, one pulse per cycle
          last_sched = done_at[dq_wr % 1024];
          dq_wr++;
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
      // all waves that can flag cycle x = cyc - DIM + 1 have left the array by now
      if (cyc >= 8 + DIM) begin
        fx = (cyc - DIM + 1) % 128;
        if (fobs[fx] !== fexp[fx]) begin
          errors++; if (errors < 12) $display("%s: fp_flags %h at cycle %0d, expected %h", PRE, fobs[fx], cyc - DIM + 1, fexp[fx]);
        end
        fexp[fx] = 4'd0;
      end
    end
    done_pulse <= rst_n && (dq_rd != dq_wr) && (done_at[dq_rd % 1024] == cyc + 1);
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
  // Row checks of the vector files (review T-12), whatever the vector lock says and without relying on the warnings of $readmemh: the file
  // must have exactly `rows` lines, all of the same width (a row with fewer hex digits is padded with zeros by $readmemh) `width` (0: any)
  task automatic fcheck(input string name, input int rows, input int width);
    int fd, n, w, c;
    reg [8*700-1:0] line;
    fd = $fopen({PRE, name, ".hex"}, "r");
    if (fd == 0) begin errors++; $display("%s: cannot open %s.hex", PRE, name); return; end
    n = 0;
    while (!$feof(fd)) begin
      c = $fgets(line, fd);
      if (c > 1) begin
        n++;
        if (width != 0 && c - 1 != width) begin errors++; if (errors < 12) $display("%s: %s.hex row %0d has %0d hex digits, %0d expected", PRE, name, n, c - 1, width); end
      end
    end
    $fclose(fd);
    if (n != rows) begin errors++; $display("%s: %s.hex has %0d rows, %0d expected", PRE, name, n, rows); end
  endtask
  int wcum, nrb;
  longint chkc_cnt;
  logic chkc_conf;
  initial begin
    done = 0; errors = 0;
    for (int i = 0; i < NWIN; i++) begin win_base[i] = 0; win_size[i] = 0; win_off[i] = 0; end
    $readmemh({PRE, "spm.hex"}, spm);
    $readmemh({PRE, "dtab.hex"}, dtab);
    $readmemh({PRE, "prog.hex"}, prog);
    $readmemh({PRE, "acc0.hex"}, acc0);
    $readmemh({PRE, "acc1.hex"}, acc1);
    $readmemh({PRE, "exp.hex"}, expv);
    $readmemh({PRE, "ins.hex"}, insx);
    $readmemh({PRE, "flg.hex"}, flg);
    $readmemh({PRE, "sem.hex"}, semx);
    fcheck("spm", SPMB, 2);                fcheck("dtab", NTD, 64);               fcheck("prog", NINS, (128 + 6*64 + 32) / 4);
    fcheck("acc0", ACCR, DIM * 8);         fcheck("acc1", ACCR, DIM * 8);         fcheck("exp", 15, 0);
    fcheck("ins", NINS, 16);               fcheck("flg", NFLG, DIM);               fcheck("sem", 64, 8);
    for (int r = 0; r < ACCR; r++) for (int c = 0; c < DIM; c++) accm[r*DIM + c] = acc0[r][32*c +: 32];
    // semaphore preload and SPM windows
    if (expv[7][37:32] != 6'd0) begin pre_idx = int'(expv[7][37:32]); pre_val = expv[7][31:0]; end
    if (expv[13][37:32] != 6'd0) begin pre_idx2 = int'(expv[13][37:32]); pre_val2 = expv[13][31:0]; end
    wcum = 0;
    for (int w = 0; w < NWIN; w++) begin                    // windows 1 .. 4: exp.hex rows 8 .. 11, window 5: row 14
      win_base[w] = int'(expv[(w < 4) ? 8 + w : 14][63:32]); win_size[w] = int'(expv[(w < 4) ? 8 + w : 14][31:0]); win_off[w] = wcum; wcum += win_size[w];
    end
    // per instruction expectations: rows, period bound, semaphore, and how many acc_done pulses its sem_inc must wait for
    nrb = 0;
    for (int i = 0; i < NINS; i++) begin
      ins_maxp[i] = int'(insx[i][63:56]); ins_rows[i] = int'(insx[i][55:32]);
      ins_sidx[i] = int'(insx[i][31:26]); ins_samt[i] = int'(insx[i][25:22]);
      if (ins_rows[i] != 0) nrb++;
      need_done[i] = nrb;          // rows: its own pulse is the nrb-th; no rows (M = 0): after the nrb pulses of the earlier ones
      if (ins_sidx[i] != 0 && ins_samt[i] != 0) begin sem_ins[nsem] = i; nsem++; end
      total_rows += ins_rows[i];
    end
    row_ins = 0; while (row_ins < NINS && ins_rows[row_ins] == 0) row_ins++;
    if (total_rows != NFLG) begin errors++; $display("%s: ins.hex has %0d rows of ACC data, flg.hex %0d", PRE, total_rows, NFLG); end
    repeat (4) @(posedge clk);
    rst_n = 1;
    // one acc_done pulse (a full cycle, changed at the falling edge) while no instruction is in flight: the first instruction needs
    // at least the two TD reads before it enters the FIFO, so the FIFO is empty here. A contract violation of the ACC, which the
    // MXU must still survive (the unguarded pop wrapped inf_cnt to 7 and stopped the MXU for good).
    @(negedge clk); spur_done = 1'b1;
    if (dut.inf_cnt != 0 || dut.inf_push) begin errors++; $display("%s: the spurious acc_done pulse met a non-empty FIFO (test setup)", PRE); end
    @(negedge clk); spur_done = 1'b0;
    if (dut.inf_cnt != 0) begin errors++; $display("%s: acc_done with nothing in flight moved inf_cnt to %0d", PRE, dut.inf_cnt); end
    // wait for all completions (every instruction with a semaphore, then the in-flight FIFO empty) or the time limit
    fork
      begin : w_done
        wait (qi == NINS && !q_valid && sem_ptr == nsem && dut.inf_cnt == 0);
      end
      begin : w_time
        repeat (`ifdef QUICK 6000 `else 40000 `endif) @(posedge clk);
        errors++; $display("%s: TIMEOUT (qi=%0d sem_inc %0d of %0d inf=%0d)", PRE, qi, sem_ptr, nsem, dut.inf_cnt);
      end
    join_any
    disable w_done; disable w_time;
    repeat (DIM + 20) @(posedge clk);
    if (fault_valid) begin errors++; $display("%s: fault cause %0d", PRE, fault_cause); end
    if (rows_seen != total_rows) begin errors++; $display("%s: %0d ACC rows, %0d expected", PRE, rows_seen, total_rows); end
    if (done_cnt != dq_wr) begin errors++; $display("%s: %0d acc_done pulses for %0d acc_last rows", PRE, done_cnt, dq_wr); end
    if (EXP_CS_FULL != 0 && cs_full_cyc == 0) begin errors++; $display("%s: the column-scale FIFO was never full (CS_DEPTH %0d)", PRE, CS_DEPTH); end
    if (EXP_INF_FULL != 0 && inf_diff_cyc == 0) begin errors++; $display("%s: the in-flight FIFO was never full with a head that differs from the next instruction", PRE); end
    for (int r = 0; r < ACCR; r++)
      for (int c = 0; c < DIM; c++)
        if (accm[r*DIM + c] !== acc1[r][32*c +: 32]) begin
          errors++; if (errors < 12) $display("%s: ACC[%0d][%0d] = %h exp %h", PRE, r, c, accm[r*DIM + c], acc1[r][32*c +: 32]);
        end
    if (flags_or !== expv[0][3:0]) begin errors++; $display("%s: fp_flags OR %h exp %h", PRE, flags_or, expv[0][3:0]); end
    for (int i = 0; i < 64; i++)
      if (sem[i] !== semx[i]) begin errors++; if (errors < 12) $display("%s: sem[%0d] = %0d exp %0d", PRE, i, sem[i], semx[i]); end
    // retirement in order: the first increment of sem[A] (an instruction with rows) comes before the first one of sem[B] (an M = 0
    // instruction queued behind it)
    if (expv[6] != 0 && (first_inc[expv[6][11:6]] < 0 || first_inc[expv[6][5:0]] < 0 || first_inc[expv[6][11:6]] >= first_inc[expv[6][5:0]])) begin
      errors++; $display("%s: sem[%0d] incremented (cycle %0d) before sem[%0d] (cycle %0d): out-of-order completion", PRE, expv[6][5:0], first_inc[expv[6][5:0]], expv[6][11:6], first_inc[expv[6][11:6]]);
    end
    if (rows_seen == 0) begin errors++; $display("%s: no rows reached the ACC", PRE); end
    // total time of the ideal instances (TR-7, TR2-2): within 2 % of the cycle count recorded in exp.hex row 12, if the instance has the
    // configuration recorded with it (the count depends on all of these: SPM_LAT, CS_DEPTH, ACC_LAT, and with a bank model on the layout)
    if (CHKC != 0) begin
      chkc_cnt = longint'(expv[12][31:0]);
      chkc_conf = (int'(expv[12][39:32]) == SPM_LAT) && (int'(expv[12][47:40]) == CS_DEPTH) && (int'(expv[12][55:48]) == ACC_LAT) &&
                  STALL_PCT == 0 && DONE_EXTRA == 0 && A_BLOCK == 0 && rdyv_mode == 0 && bank_mode == 0;
      if (chkc_cnt == 0) begin errors++; $display("%s: no cycle count recorded in exp.hex row 12", PRE); end
      else if (!chkc_conf) $display("%s: CHKC skipped: this instance (SPM_LAT %0d, CS_DEPTH %0d, ACC_LAT %0d, stalls %0d, DONE_EXTRA %0d, A_BLOCK %0d, RDYV %0d, bank model %0d) is not the recorded one (SPM_LAT %0d, CS_DEPTH %0d, ACC_LAT %0d, ideal ports)",
                                    PRE, SPM_LAT, CS_DEPTH, ACC_LAT, STALL_PCT, DONE_EXTRA, A_BLOCK, rdyv_mode, bank_mode, expv[12][39:32], expv[12][47:40], expv[12][55:48]);
      else if (cyc * 100 > chkc_cnt * 102) begin
        errors++; $display("%s: %0d cycles, more than 2 %% above the recorded %0d", PRE, cyc, chkc_cnt);
      end else if (cyc * 100 < chkc_cnt * 98) begin
        errors++; $display("%s: %0d cycles, more than 2 %% below the recorded %0d: a stale count, re-measure (gen_mxu_vectors.py IDEAL_CYCLES)", PRE, cyc, chkc_cnt);
      end
    end
    $display("%s: %0d ACC rows in %0d cycles, flags %h; cs FIFO full %0d cycles, in-flight FIFO full %0d (head differs %0d), largest bounded row gap %0d",
             PRE, rows_seen, cyc, flags_or, cs_full_cyc, inf_full_cyc, inf_diff_cyc, maxgap);
    if (bank_mode != 0)
      $display("%s: bank model %0d: beats A %0d W %0d S %0d; beats that took > 1 cycle A %0d W %0d S %0d; cycles waited A %0d W %0d S %0d", PRE, bank_mode,
               bk_beats[0], bk_beats[1], bk_beats[2], bk_slow[0], bk_slow[1], bk_slow[2], bk_wait[0], bk_wait[1], bk_wait[2]);
    if (PERF) perf_report();
    done = 1;
  end
endmodule

// Legality faults: every line is an illegal instruction (plus its two TDs) run after a reset. The MXU must raise the cause, stop
// taking instructions and touch neither the SPM nor the ACC. A line with cause 0 is a legal instruction (T-2: extents that end exactly at
// the end of the SPM / the ACC): no fault, a context is pushed, and it runs until it has drained (TR-3: acc_done ACC_LAT cycles after the
// acc_last row, as the ACC does, then the in-flight FIFO empty): every lane it requests lies inside the SPM, every ACC row is below ACCR,
// exactly one acc_last and exactly as many ACC rows as the case has (N tiles x K tiles x M, which the generator puts in the address field of
// a cause-0 line, TR2-6). The SPM answers zeros; only the addresses and the row counts are judged.
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
  logic s_req_v, acc_valid, acc_rescale, acc_overwrite, acc_use_cs, acc_last, acc_done, sem_inc_valid, fault_valid;
  logic [21:0] s_req_addr;
  logic [10:0] acc_row;
  logic [DIM*32-1:0] acc_part, acc_cs;
  logic [6:0] acc_ncols;
  logic [31:0] acc_rowscale;
  logic [63:0] fault_addr;
  logic [5:0] sem_inc_idx;
  // the file must have exactly N rows of the width of a mem word
  task automatic fcheck(input int rows);
    int fd, n, c;
    reg [8*700-1:0] line;
    fd = $fopen(FILE, "r");
    n = 0;
    while (fd != 0 && !$feof(fd)) begin
      c = $fgets(line, fd);
      if (c > 1) begin
        n++;
        if (c - 1 != (4 + 64 + 256 + 256 + 128 + 6*64 + 32 + 3) / 4) begin errors++; $display("%s row %0d has %0d hex digits", FILE, n, c - 1); end
      end
    end
    if (fd != 0) $fclose(fd);
    if (n != rows) begin errors++; $display("%s has %0d rows, %0d expected", FILE, n, rows); end
  endtask
  logic saw_ctx;
  always @(posedge clk) begin
    if (dut.u_dec.ctx_push) saw_ctx <= 1'b1;
    if (cause_exp == 4'd0 && q_valid && q_ready) q_valid <= 1'b0;          // a legal instruction is taken once
  end
  // the ACC's acc_done: exactly ACC_LAT cycles after the acc_last row (ACC_LAT = the MXU's default, acc.md; none until a row is the last)
  localparam int ACC_LAT = 8;
  logic [ACC_LAT-1:0] dsr = '0;
  always @(posedge clk) dsr <= rst_n ? {dsr[ACC_LAT-2:0], acc_valid && acc_last} : '0;
  assign acc_done = dsr[ACC_LAT-1];
  int n_done = 0, n_last = 0, bad_row = 0, n_rows = 0;
  always @(posedge clk) begin
    if (!rst_n) begin n_done <= 0; n_last <= 0; n_rows <= 0; end
    else begin
      if (acc_done) n_done <= n_done + 1;
      if (acc_valid && acc_last) n_last <= n_last + 1;
      if (acc_valid) n_rows <= n_rows + 1;
    end
    if (rst_n && acc_valid && int'(acc_row) >= ACCR) bad_row++;
  end
  mxu #(.DIM(DIM), .ACC_ROWS(ACCR), .SPM_BYTES(SPMB)) dut (
    .clk, .rst_n, .q_valid, .q_ready, .q_instr, .q_regs, .q_wval, .sem_rd_idx, .sem_rd_val(32'd0),
    .dtab_req, .dtab_idx, .dtab_gnt, .dtab_rvalid, .dtab_data,
    .a_req_v, .a_req_addr, .a_req_ready(1'b1), .a_rsp_data('0), .w_req_v, .w_req_addr, .w_req_ready(1'b1), .w_rsp_data('0),
    .s_req_v, .s_req_addr, .s_req_ready(1'b1), .s_rsp_data('0),
    .acc_valid, .acc_row, .acc_part, .acc_ncols, .acc_rescale, .acc_overwrite, .acc_use_cs, .acc_rowscale, .acc_cs, .acc_last,
    .acc_done, .sem_inc_valid, .sem_inc_idx, .sem_inc_amt, .fp_flags, .fault_valid, .fault_cause, .fault_addr);
  always @(posedge clk) begin
    dtab_gnt <= dtab_req && !dtab_gnt && !dtab_rvalid;
    dtab_rvalid <= dtab_gnt;
    if (dtab_gnt) dtab_data <= (dut.u_dec.dst == 4'd1) ? td_a : td_w;
  end
  int spm_touch = 0, bad_lane = 0, tw;
  always @(posedge clk) if (|a_req_v || |w_req_v || s_req_v || acc_valid || sem_inc_valid) spm_touch++;
  always @(posedge clk) begin
    for (int l = 0; l < 4; l++) if (a_req_v[l] && int'(a_req_addr[22*l +: 22]) + LB > SPMB) bad_lane++;
    for (int l = 0; l < 8; l++) if (w_req_v[l] && int'(w_req_addr[22*l +: 22]) + LB > SPMB) bad_lane++;
    if (s_req_v && int'(s_req_addr) + LB > SPMB) bad_lane++;
  end
  initial begin
    done = 0; errors = 0;
    $readmemh(FILE, mem);
    fcheck(N);
    for (int k = 0; k < N; k++) begin
      rst_n = 0; q_valid = 0; spm_touch = 0; bad_lane = 0; bad_row = 0; saw_ctx = 1'b0;
      repeat (3) @(posedge clk);
      {cause_exp, addr_exp, td_w, td_a, q_instr, q_regs, q_wval} = mem[k];
      #1 rst_n = 1; q_valid = 1;
      repeat (40) @(posedge clk);
      if (cause_exp == 4'd0) begin
        // run to completion: the acc_done of the one instruction has come and the in-flight FIFO is empty (or it faulted / timed out)
        tw = 0;
        while (!(n_done != 0 && dut.inf_cnt == 0) && !fault_valid && tw < 20000) begin @(posedge clk); tw++; end
        repeat (DIM + 20) @(posedge clk);
        if (fault_valid) begin errors++; $display("legal case %0d (%s): fault cause %0d at %h", k, FILE, fault_cause, fault_addr); end
        else if (!saw_ctx) begin errors++; $display("legal case %0d (%s): no context was pushed", k, FILE); end
        else if (!(n_done == 1 && dut.inf_cnt == 0)) begin errors++; $display("legal case %0d (%s): did not drain (%0d acc_done, inf_cnt %0d, %0d rows)", k, FILE, n_done, dut.inf_cnt, n_rows); end
        if (n_last != 1 || n_rows == 0) begin errors++; $display("legal case %0d (%s): %0d acc_last rows, %0d rows", k, FILE, n_last, n_rows); end
        else if (int'(addr_exp) != n_rows) begin errors++; $display("legal case %0d (%s): %0d ACC rows, %0d expected", k, FILE, n_rows, addr_exp); end
        if (bad_lane != 0) begin errors++; $display("legal case %0d (%s): %0d lane requests outside the SPM", k, FILE, bad_lane); end
        if (bad_row != 0) begin errors++; $display("legal case %0d (%s): %0d ACC rows at or above ACCR %0d", k, FILE, bad_row, ACCR); end
      end else begin
        if (!fault_valid || fault_cause !== cause_exp) begin
          errors++; $display("fault case %0d (%s): valid %b cause %0d expected %0d", k, FILE, fault_valid, fault_cause, cause_exp);
        end else if (fault_addr !== addr_exp) begin
          errors++; $display("fault case %0d (%s): cause %0d FAULT_ADDR %h expected %h", k, FILE, fault_cause, fault_addr, addr_exp);
        end
        if (spm_touch != 0) begin errors++; $display("fault case %0d (%s): %0d SPM / ACC / semaphore actions", k, FILE, spm_touch); end
        if (q_ready) begin errors++; $display("fault case %0d (%s): queue still popping", k, FILE); end
      end
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
  mxu_tc #(.DIM(`PDIM), .NINS(`CNT_PERF_PROG), .NTD(`CNT_PERF_DTAB), .SPMB(`CNT_PERF_SPM), .ACCR(`CNT_PERF_ACC0), .NFLG(`CNT_PERF_FLG), .STALL_PCT(0), .PERF(1), .PRE("")) t (.done(d), .errors(e));
  initial begin
    wait (d);
    if (e == 0) $display("PASS mxu"); else $display("FAIL mxu: %0d errors", e);
    $finish;
  end
endmodule
`else
// the sizes of the programs of scripts/gen_mxu_vectors.py (counts.svh)
`define P8   .DIM(8),  .NINS(`CNT_MXU_8_PROG),  .NTD(`CNT_MXU_8_DTAB),  .SPMB(`CNT_MXU_8_SPM),  .ACCR(`CNT_MXU_8_ACC0),  .NFLG(`CNT_MXU_8_FLG),  .PRE("mxu_8_")
`define P16  .DIM(16), .NINS(`CNT_MXU_16_PROG), .NTD(`CNT_MXU_16_DTAB), .SPMB(`CNT_MXU_16_SPM), .ACCR(`CNT_MXU_16_ACC0), .NFLG(`CNT_MXU_16_FLG), .PRE("mxu_16_")
`define P32  .DIM(32), .NINS(`CNT_MXU_32_PROG), .NTD(`CNT_MXU_32_DTAB), .SPMB(`CNT_MXU_32_SPM), .ACCR(`CNT_MXU_32_ACC0), .NFLG(`CNT_MXU_32_FLG), .PRE("mxu_32_")
`define P8X  .DIM(8),  .NINS(`CNT_MXU_8X_PROG),  .NTD(`CNT_MXU_8X_DTAB),  .SPMB(`CNT_MXU_8X_SPM),  .ACCR(`CNT_MXU_8X_ACC0),  .NFLG(`CNT_MXU_8X_FLG),  .PRE("mxu_8x_"),  .SPARSE(1), .SPM_TOP(4194304)
`define P16X .DIM(16), .NINS(`CNT_MXU_16X_PROG), .NTD(`CNT_MXU_16X_DTAB), .SPMB(`CNT_MXU_16X_SPM), .ACCR(`CNT_MXU_16X_ACC0), .NFLG(`CNT_MXU_16X_FLG), .PRE("mxu_16x_"), .SPARSE(1), .SPM_TOP(4194304)
// an instance that is not selected (debug defines below) is a constant "done, no errors"
`define INST(I, NAME, PARAMS) \
  if (SELM[I]) begin : NAME \
    mxu_tc #PARAMS t (.done(dn[I]), .errors(erv[32*I +: 32])); \
  end else begin : NAME \
    assign dn[I] = 1'b1; \
    assign erv[32*I +: 32] = 32'd0; \
  end
// instances: *s = 30 % random grant stalls, *r = ready needs a valid lane, *l = other SPM_LAT / CS_DEPTH (T-6), *d = late acc_done
// (T-5 / H-2), *x = sparse SPM and 2048 ACC rows (T-7). CHKP / CHKC: the ideal instances (t8, t16, t32, t8x, t16x) check the period bound of the
// M = 1 instruction (T-10) and the total cycle count (TR-7), for SPM_LAT 2 (t16x: 3) and ACC_LAT 8 only (see the header). t8r: SPM_LAT 5, CS_DEPTH 3 (TR-4).
// Debug defines: -DQUICK (8 x 8 only), -DONLY16, -DONLY32 (the instance of that size and its faults only), -DNOSLOW (no DIM 16 stall / RDYV runs).
module tb_mxu;
  localparam int NI = 13;
  logic [NI-1:0] dn;
  logic [32*NI-1:0] erv;
`ifdef ONLY32
  localparam logic [NI-1:0] SELM = 13'h0020;
`elsif ONLY16
  localparam logic [NI-1:0] SELM = 13'h0002;
`elsif QUICK
  localparam logic [NI-1:0] SELM = 13'h0001;
`else
  localparam logic [NI-1:0] SELM = '1;
`endif
  `INST(0,  t8,   (`P8,   .STALL_PCT(0),  .CHKP(1), .CHKC(1)))
  `INST(1,  t16,  (`P16,  .STALL_PCT(0),  .CHKP(1), .CHKC(1)))
  `INST(2,  t8s,  (`P8,   .STALL_PCT(30), .ACC_LAT(24)))
  `INST(3,  t16s, (`P16,  .STALL_PCT(30)))
  `INST(4,  t8b,  (`P8,   .STALL_PCT(0),  .A_BLOCK(40)))
  `INST(5,  t32,  (`P32,  .STALL_PCT(0),  .CHKP(1), .CHKC(1)))
  // M-23: SPM whose ready is low for a request with no valid lane (the MXU must never wait for the grant of an empty beat)
  // TR-4: and SPM_LAT 5 with CS_DEPTH 3 (a depth that is not a power of two: the FIFO pointers wrap by compare, not by width)
  `INST(6,  t8r,  (`P8,   .STALL_PCT(30), .RDYV(1), .SPM_LAT(5), .CS_DEPTH(3)))
  `INST(7,  t16r, (`P16,  .STALL_PCT(0),  .RDYV(1)))
  // T-6: SPM_LAT 1 and 3 with CS_DEPTH 2 (the column-scale FIFO fills: EXP_CS_FULL counts it)
  `INST(8,  t8l,  (`P8,   .STALL_PCT(0),  .SPM_LAT(1), .CS_DEPTH(2), .EXP_CS_FULL(1)))
  `INST(9,  t16l, (`P16,  .STALL_PCT(0),  .SPM_LAT(3), .CS_DEPTH(2), .EXP_CS_FULL(1)))
  // T-5 / H-2: acc_done ACC_LAT + 0 .. 60 cycles after the last row: the in-flight FIFO fills with different entries
  `INST(10, t8d,  (`P8,   .STALL_PCT(0),  .DONE_EXTRA(60), .EXP_INF_FULL(1)))
  // T-7: operands above 2 MiB, ACC bases above 1024
  `INST(11, t8x,  (`P8X,  .STALL_PCT(0),  .CHKP(1), .CHKC(1)))
  `INST(12, t16x, (`P16X, .STALL_PCT(0),  .SPM_LAT(3), .CHKP(1), .CHKC(1)))
  logic df8, df16;
  int ef8, ef16;
`ifdef QUICK
  mxu_fault_tc #(.DIM(8), .N(`CNT_MXU_FAULT_8), .SPMB(`CNT_MXU_8_SPM), .ACCR(`CNT_MXU_8_ACC0), .FILE("mxu_fault_8.hex")) tf8 (.done(df8), .errors(ef8));
  assign df16 = 1, ef16 = 0;
`elsif ONLY16
  assign df8 = 1, ef8 = 0;
  mxu_fault_tc #(.DIM(16), .N(`CNT_MXU_FAULT_16), .SPMB(`CNT_MXU_16_SPM), .ACCR(`CNT_MXU_16_ACC0), .FILE("mxu_fault_16.hex")) tf16 (.done(df16), .errors(ef16));
`elsif ONLY32
  assign df8 = 1, ef8 = 0, df16 = 1, ef16 = 0;
`else
  mxu_fault_tc #(.DIM(8), .N(`CNT_MXU_FAULT_8), .SPMB(`CNT_MXU_8_SPM), .ACCR(`CNT_MXU_8_ACC0), .FILE("mxu_fault_8.hex")) tf8 (.done(df8), .errors(ef8));
  mxu_fault_tc #(.DIM(16), .N(`CNT_MXU_FAULT_16), .SPMB(`CNT_MXU_16_SPM), .ACCR(`CNT_MXU_16_ACC0), .FILE("mxu_fault_16.hex")) tf16 (.done(df16), .errors(ef16));
`endif
  int etot;
  initial begin
    wait (&dn && df8 && df16);
    etot = ef8 + ef16;
    for (int i = 0; i < NI; i++) etot += int'(erv[32*i +: 32]);
    if (etot == 0) $display("PASS mxu");
    else begin
      $write("FAIL mxu: %0d errors (faults 8 / 16: %0d / %0d; instances", etot, ef8, ef16);
      for (int i = 0; i < NI; i++) $write(" %0d", int'(erv[32*i +: 32]));
      $display(")");
    end
    $finish;
  end
endmodule
`endif
