// DERIVED FILE (author, 2026-11-13): scripts/mxu_review/tb_dec_miter.sv with one more case in pick_s1 (s1 = 2^30 + small; the reviewer's
// stimulus never makes it, so mutants of the s1 width survived). Everything else is the reviewer's text. Used by mxu_mutants.py misc.
// tb_dec_miter: the author's mxu_dec against the narrowed copy mxu_dec_fix (fix_dec_narrow.py, review finding I-4), same inputs,
// every output compared every cycle (!==, so X must match too). Random, biased instructions: legal and illegal fields, register
// and TD values at 0 / 1 / small / around the SPM and ACC bounds / huge / negative strides, and extents aimed at SPM_BYTES - 1 .. + 1
// and ACC_ROWS - 1 .. + 1. A fault stops a decoder (D_FAULT), so the testbench resets both after one. Not a proof.
// +N=<instructions> (default 200000), +SEED=<n>. Prints the outcome counts per fault cause and "PASS tb_dec_miter" or the first mismatches.
`timescale 1ns/1ps
module tb_dec_miter;
  import isa_pkg::*;
  parameter int DIM       = 8;
  parameter int ACC_ROWS  = 2048;
  parameter int SPM_BYTES = 4194304;

  logic clk = 1'b0, rst_n = 1'b0;
  always #5 clk = ~clk;

  logic q_valid, inf_room, inf_empty, ctx_ready, dtab_gnt, dtab_rvalid;
  logic [127:0] q_instr;
  logic [6*64-1:0] q_regs;
  logic [31:0] q_wval, sem_rd_val;
  logic [255:0] dtab_data;
  logic [255:0] tdv [2];

  `define DEC_PORTS(p) \
    logic p``q_ready, p``dtab_req, p``ctx_push, p``z_inc, p``c_trans, p``c_bf16, p``c_cs_en, p``fault_valid; \
    logic [5:0] p``sem_rd_idx; logic [7:0] p``dtab_idx; logic [9:0] p``ctx_sync; logic [1:0] p``c_accm; \
    logic [15:0] p``c_K, p``c_M, p``c_N, p``c_KT, p``c_NT; \
    logic [21:0] p``c_a_base, p``c_a_pitch, p``c_w_base, p``c_w_pitch, p``c_cs_base, p``c_rs_base, p``c_csp; \
    logic [10:0] p``c_acc_base; logic [3:0] p``fault_cause; logic [63:0] p``fault_addr;
  `DEC_PORTS(g_)
  `DEC_PORTS(f_)
  `define DEC_CONN(p) \
    .clk, .rst_n, .q_valid, .q_ready(p``q_ready), .q_instr, .q_regs, .q_wval, .sem_rd_idx(p``sem_rd_idx), .sem_rd_val, \
    .dtab_req(p``dtab_req), .dtab_idx(p``dtab_idx), .dtab_gnt, .dtab_rvalid, .dtab_data, .inf_room, .inf_empty, .ctx_ready, \
    .ctx_push(p``ctx_push), .z_inc(p``z_inc), .ctx_sync(p``ctx_sync), .c_trans(p``c_trans), .c_bf16(p``c_bf16), \
    .c_cs_en(p``c_cs_en), .c_accm(p``c_accm), .c_K(p``c_K), .c_M(p``c_M), .c_N(p``c_N), .c_KT(p``c_KT), .c_NT(p``c_NT), \
    .c_a_base(p``c_a_base), .c_a_pitch(p``c_a_pitch), .c_w_base(p``c_w_base), .c_w_pitch(p``c_w_pitch), .c_cs_base(p``c_cs_base), \
    .c_rs_base(p``c_rs_base), .c_csp(p``c_csp), .c_acc_base(p``c_acc_base), .fault_valid(p``fault_valid), \
    .fault_cause(p``fault_cause), .fault_addr(p``fault_addr)
  mxu_dec     #(.DIM(DIM), .ACC_ROWS(ACC_ROWS), .SPM_BYTES(SPM_BYTES)) u_gold (`DEC_CONN(g_));
  mxu_dec_fix #(.DIM(DIM), .ACC_ROWS(ACC_ROWS), .SPM_BYTES(SPM_BYTES)) u_fix  (`DEC_CONN(f_));

  // ---------------------------------------------------------------- value pickers
  function automatic logic [63:0] r64();
    return {$urandom, $urandom};
  endfunction
  function automatic logic [63:0] pick_reg();          // a base register
    case ($urandom % 10)
      0: return 64'd0;
      1, 2: return 64'(($urandom % 64) * 64);
      3: return 64'(SPM_BYTES) - 64'(($urandom % 8) * 64);
      4: return 64'($urandom % SPM_BYTES) & ~64'd63;
      5: return r64();
      6: return 64'h8000_0000_0000_0000 | (r64() & 64'hFFFF_FFFF_FFFF_FFC0);
      7: return 64'(($urandom % 4096)) ;              // misaligned often
      default: return 64'($urandom % 65536) & ~64'd63;
    endcase
  endfunction
  function automatic logic [31:0] pick_s1();
    case ($urandom % 9)
      8: return 32'h4000_0000 + 32'(($urandom % 4) * 64);          // author's addition (not in the reviewer's file): bit 30 set, small low bits
      0: return 32'd0;
      1, 2: return 32'(($urandom % 64) * 64);
      3: return 32'($urandom) & ~32'd63;            // often negative
      4: return 32'(($urandom % 1024) * 64);
      5: return 32'h7FFF_FFC0;
      6: return 32'($urandom % 512);                // misaligned
      default: return 32'(64 * (1 + $urandom % 4));
    endcase
  endfunction
  function automatic logic [15:0] pick_dim();         // K or N
    case ($urandom % 8)
      0: return 16'd0;
      1, 2: return 16'(1 + $urandom % (8 * DIM));
      3: return 16'hFFFF;
      4: return 16'($urandom);
      5: return 16'(4 * DIM * (1 + $urandom % 8) + ($urandom % 3) - 1);
      default: return 16'(1 + $urandom % 300);
    endcase
  endfunction
  function automatic logic [63:0] pick_m();
    case ($urandom % 9)
      0: return 64'd0;
      1, 2: return 64'(1 + $urandom % 40);
      3: return 64'(ACC_ROWS) + 64'($urandom % 3) - 64'd1;
      4: return r64();
      5: return 64'h1_0000 + 64'($urandom % 4);     // above 16 bits
      6: return 64'h8000_0000_0000_0000;
      default: return 64'(1 + $urandom % ACC_ROWS);
    endcase
  endfunction

  // one instruction: fields, registers and the two TDs
  logic [63:0] ra, rw, racc, rm, rrs, rcs;
  task automatic make_ins(output logic [127:0] ins, output logic [6*64-1:0] regs, output logic [255:0] ta, output logic [255:0] tw);
    logic [15:0] K, N, Kw, M16;
    logic [31:0] sa, sw;
    logic        trans, bf, mreg, legal, bign;
    logic [1:0]  accm;
    logic [63:0] M, eb, ext, NT, KTF, CSP;
    int          tgt, d;
    ins   = '0;
    legal = ($urandom % 10) < 7;                         // every field legal; only the extents (RANGE) vary
    ins[127:120] = ($urandom % 50 == 0 && !legal) ? 8'($urandom) : OP_MXU_MM;
    trans = $urandom % 2;
    bf    = $urandom % 2;
    mreg  = $urandom % 3 == 0;
    accm  = ($urandom % 20 == 0 && !legal) ? 2'd3 : 2'($urandom % 3);
    ins[T_MXU_TRANS_W_MSB] = trans;
    ins[T_MXU_ACC_MODE_MSB:T_MXU_ACC_MODE_LSB] = accm;
    ins[T_MXU_MODE_MSB:T_MXU_MODE_LSB] = ($urandom % 30 == 0 && !legal) ? 3'($urandom) : 3'd0;
    ins[T_MXU_CS_REG_MSB:T_MXU_CS_REG_LSB] = ($urandom % 2) ? 5'd7 : 5'd0;
    ins[T_MXU_RS_REG_MSB:T_MXU_RS_REG_LSB] = (accm == 2'd2) ^ ($urandom % 25 == 0 && !legal) ? 5'd9 : 5'd0;
    ins[T_MXU_M_REG_MSB:T_MXU_M_REG_LSB] = mreg ? 5'd3 : 5'd0;
    ins[T_MXU_A_TD_MSB:T_MXU_A_TD_LSB] = 8'd1;
    ins[T_MXU_W_TD_MSB:T_MXU_W_TD_LSB] = 8'd2;
    ins[SYNC_SSEM_MSB:SYNC_SSEM_LSB] = 6'($urandom);
    ins[SYNC_SINC_MSB:SYNC_SINC_LSB] = 4'($urandom);
    if ($urandom % 40 == 0 && !legal) ins[38:0] = 39'($urandom);  // reserved bits
    K = pick_dim(); N = pick_dim(); Kw = ($urandom % 20 == 0) ? pick_dim() : K;
    M = pick_m(); M16 = mreg ? 16'($urandom) : 16'(M);
    if (!mreg) M = 64'(M16);
    sa = pick_s1(); sw = pick_s1();
    ra = pick_reg(); rw = pick_reg(); rcs = pick_reg(); rrs = pick_reg();
    racc = ($urandom % 3 == 0) ? r64() : 64'($urandom % (ACC_ROWS + 4));
    if (legal) begin
      if (K == 0) K = 16'd1;
      if (N == 0) N = 16'd1;
      Kw = K;
      if (M16 == 0 && !mreg && $urandom % 4 != 0) begin M16 = 16'(1 + $urandom % 64); M = 64'(M16); end
      sa[31] = 1'b0; sa[5:0] = 6'd0; sw[31] = 1'b0; sw[5:0] = 6'd0;
      ra[5:0] = 6'd0; rw[5:0] = 6'd0; rcs[5:0] = 6'd0; rrs[5:0] = 6'd0;
      if ($urandom % 4 != 0) begin                      // mostly in range; the target below moves one extent to its bound
        ra = 64'(($urandom % 1024) * 64); rw = 64'(($urandom % 1024) * 64); rcs = 64'(($urandom % 1024) * 64); rrs = 64'(($urandom % 1024) * 64);
        racc = 64'($urandom % (ACC_ROWS / 4));
        if (mreg) begin M = 64'($urandom % 64); rm = M; end
        if (sa > 32'h0010_0000) sa = 32'(($urandom % 64) * 64);
        if (sw > 32'h0010_0000) sw = 32'(($urandom % 64) * 64);
      end
    end
    // N near 2^16 with column scales and the cs extent aimed at its bound (the widest CSP; reachable past the ACC check at DIM 64)
    bign = legal && ($urandom % 4 == 0);
    if (bign) begin
      N = 16'hFFC0 + 16'($urandom % 64); K = 16'(257 + $urandom % 2000); Kw = K;
      trans = 1'b0; ins[T_MXU_TRANS_W_MSB] = 1'b0; ins[T_MXU_CS_REG_MSB:T_MXU_CS_REG_LSB] = 5'd7;
      sw = ($urandom % 2) ? 32'd64 : 32'd0; sa = 32'(($urandom % 4) * 64);
      M = 64'(1 + $urandom % 2); M16 = 16'(M);
      ra = 64'd0; rw = 64'd0; rrs = 64'd0; racc = 64'd0;
    end
    rm = mreg ? M : r64();
    eb = bf ? 64'd2 : 64'd1;
    // aim one extent at its bound (-1, 0, +1 past it) when the numbers are small enough to do it in 64 bits
    tgt = bign ? 2 : $urandom % 6; d = int'($urandom % 3) - 1;
    NT = (64'(N) + 64'(DIM) - 1) / 64'(DIM);
    KTF = (64'(K) + (bf ? 64'd127 : 64'd255)) / (bf ? 64'd128 : 64'd256);
    CSP = ((64'd4 * 64'(N) + 64'd63) / 64'd64) * 64'd64;
    if (M != 0 && M <= 64'(ACC_ROWS) && !sa[31] && !sw[31]) begin
      case (tgt)
        0: begin ext = (M - 1) * 64'(sa) + 64'(K) * eb; if (ext <= 64'(SPM_BYTES)) ra = (64'(SPM_BYTES) - ext + 64'(d)) & ~64'd63; end
        1: begin ext = trans ? (64'(N) - 1) * 64'(sw) + 64'(K) * eb : (64'(K) - 1) * 64'(sw) + 64'(N) * eb;
                 if (ext <= 64'(SPM_BYTES) && N != 0 && K != 0) rw = (64'(SPM_BYTES) - ext + 64'(d) + 64'd63) & ~64'd63; end
        2: begin ext = (KTF - 1) * CSP + 64'd4 * 64'(N); if (ext <= 64'(SPM_BYTES) && K != 0) rcs = (64'(SPM_BYTES) - ext + 64'(d) + 64'd63) & ~64'd63; end
        3: begin ext = 64'd4 * M; rrs = (64'(SPM_BYTES) - ext + 64'(d) + 64'd63) & ~64'd63; end
        4: begin if (NT * M <= 64'(ACC_ROWS) + 1) racc = 64'(ACC_ROWS) - NT * M + 64'(d); end
        default: ;
      endcase
    end
    regs = {rcs, rrs, rm, racc, rw, ra};
    ta = '0; tw = '0;
    ta[TD_DTYPE_MSB:TD_DTYPE_LSB] = ($urandom % 30 == 0 && !legal) ? 5'($urandom) : 5'(bf ? DTYPE_BF16 : DTYPE_E4M3);
    tw[TD_DTYPE_MSB:TD_DTYPE_LSB] = ($urandom % 30 == 0 && !legal) ? 5'($urandom) : 5'(bf ? DTYPE_BF16 : DTYPE_E4M3);
    ta[TD_SPACE_MSB:TD_SPACE_LSB] = ($urandom % 30 == 0 && !legal) ? 3'($urandom) : SPACE_SPM;
    tw[TD_SPACE_MSB:TD_SPACE_LSB] = ($urandom % 30 == 0 && !legal) ? 3'($urandom) : SPACE_SPM;
    ta[TD_FLAGS_MSB:TD_FLAGS_LSB] = ($urandom % 30 == 0 && !legal) ? 8'($urandom) : 8'd0;
    tw[TD_FLAGS_MSB:TD_FLAGS_LSB] = ($urandom % 30 == 0 && !legal) ? 8'($urandom) : 8'd0;
    ta[TD_N0_MSB:TD_N0_LSB] = K;
    ta[TD_N1_MSB:TD_N1_LSB] = M16;
    ta[TD_N2_MSB:TD_N2_LSB] = ($urandom % 30 == 0 && !legal) ? 16'd2 : 16'd1;
    ta[TD_N3_MSB:TD_N3_LSB] = 16'd1;
    tw[TD_N0_MSB:TD_N0_LSB] = trans ? Kw : N;
    tw[TD_N1_MSB:TD_N1_LSB] = trans ? N : Kw;
    tw[TD_N2_MSB:TD_N2_LSB] = 16'd1;
    tw[TD_N3_MSB:TD_N3_LSB] = ($urandom % 30 == 0 && !legal) ? 16'd0 : 16'd1;
    ta[TD_S1_MSB:TD_S1_LSB] = sa;
    tw[TD_S1_MSB:TD_S1_LSB] = sw;
    if ($urandom % 2) begin ta[255:112] = {$urandom, $urandom, $urandom, $urandom, 16'($urandom)}; end   // unused bits
  endtask

  // ---------------------------------------------------------------- environment
  integer N, SEED, nins = 0, nbad = 0, cyc = 0, npush = 0, nz = 0;
  integer ncause [16];
  logic [127:0] ins_n; logic [6*64-1:0] regs_n; logic [255:0] ta_n, tw_n;
  logic gnt_q;
  always @(posedge clk) cyc <= cyc + 1;
  assign dtab_gnt  = g_dtab_req && gnt_q;
  always @(posedge clk) begin
    gnt_q <= ($urandom % 4) != 0;
    dtab_rvalid <= rst_n && dtab_gnt;
    dtab_data <= dtab_gnt ? (g_dtab_idx == 8'd1 ? tdv[0] : g_dtab_idx == 8'd2 ? tdv[1] : 256'($urandom)) : {8{$urandom}};
    inf_room  <= ($urandom % 8) != 0;
    inf_empty <= ($urandom % 3) != 0;
    ctx_ready <= ($urandom % 4) != 0;
    sem_rd_val <= $urandom;
  end
  assign q_valid = 1'b1;
  assign q_instr = ins_n;
  assign q_regs  = regs_n;
  assign q_wval  = sem_rd_val - 32'($urandom % 2);   // mostly ready; the wait is also exercised (wsem random)

  always @(posedge clk) if (rst_n) begin            // outcome counts (the main loop waits inside an instruction)
    if (g_ctx_push) npush <= npush + 1;
    if (g_z_inc) nz <= nz + 1;
  end

  // compare every cycle after reset
  always @(negedge clk) if (rst_n) begin
    if ({g_q_ready, g_dtab_req, g_ctx_push, g_z_inc, g_fault_valid, g_sem_rd_idx} !== {f_q_ready, f_dtab_req, f_ctx_push, f_z_inc, f_fault_valid, f_sem_rd_idx} ||
        (g_dtab_req && g_dtab_idx !== f_dtab_idx) ||
        ((g_ctx_push || g_z_inc) && g_ctx_sync !== f_ctx_sync) ||
        (g_fault_valid && {g_fault_cause, g_fault_addr} !== {f_fault_cause, f_fault_addr}) ||
        {g_c_trans, g_c_bf16, g_c_cs_en, g_c_accm, g_c_K, g_c_M, g_c_N, g_c_KT, g_c_NT, g_c_a_base, g_c_a_pitch, g_c_w_base, g_c_w_pitch,
         g_c_cs_base, g_c_rs_base, g_c_csp, g_c_acc_base} !==
        {f_c_trans, f_c_bf16, f_c_cs_en, f_c_accm, f_c_K, f_c_M, f_c_N, f_c_KT, f_c_NT, f_c_a_base, f_c_a_pitch, f_c_w_base, f_c_w_pitch,
         f_c_cs_base, f_c_rs_base, f_c_csp, f_c_acc_base}) begin
      nbad = nbad + 1;
      if (nbad <= 5) $display("MISMATCH cycle %0d ins %0d: fault %b/%b cause %0d/%0d addr %h/%h KT %0d/%0d NT %0d/%0d csp %0d/%0d push %b/%b",
                              cyc, nins, g_fault_valid, f_fault_valid, g_fault_cause, f_fault_cause, g_fault_addr, f_fault_addr,
                              g_c_KT, f_c_KT, g_c_NT, f_c_NT, g_c_csp, f_c_csp, g_ctx_push, f_ctx_push);
    end
  end

  initial begin
    if (!$value$plusargs("N=%d", N)) N = 200000;
    if (!$value$plusargs("SEED=%d", SEED)) SEED = 1;
    void'($urandom(SEED));
    for (int i = 0; i < 16; i++) ncause[i] = 0;
    make_ins(ins_n, regs_n, ta_n, tw_n); tdv[0] = ta_n; tdv[1] = tw_n;
    repeat (2) @(posedge clk);
    #1 rst_n = 1'b1;
    while (nins < N) begin
      @(posedge clk);
      #1;
      if (g_fault_valid) begin
        ncause[g_fault_cause] = ncause[g_fault_cause] + 1;
        rst_n = 1'b0; @(posedge clk); #1 rst_n = 1'b1;
      end
      // the decoder took the instruction in the previous cycle (q_ready seen at that edge): offer the next one
      if (u_gold.dst != 0 && ins_n === u_gold.ins) begin
        nins = nins + 1;
        make_ins(ins_n, regs_n, ta_n, tw_n);
        // the TDs of the taken instruction stay until it has read both
        wait (u_gold.dst == 0 || u_gold.fault_valid || !rst_n);
        tdv[0] = ta_n; tdv[1] = tw_n;
      end
    end
    $display("instructions %0d: contexts %0d, M = 0 completions %0d, faults by cause:", nins, npush, nz);
    for (int i = 0; i < 16; i++) if (ncause[i] != 0) $display("  cause %0d: %0d", i, ncause[i]);
    if (nbad == 0) $display("PASS tb_dec_miter");
    else $display("FAIL tb_dec_miter: %0d mismatching cycles", nbad);
    $finish;
  end
endmodule
