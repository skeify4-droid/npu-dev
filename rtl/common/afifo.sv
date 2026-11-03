// Dual-clock (asynchronous) FIFO, valid/ready in each clock domain. Used at the HBM controller: clk (1.5 GHz) to
// clk_mc (1.2 GHz), microarch.md section 5.
//
// Structure (Cummings style): binary write/read pointers with one extra wrap bit, registered Gray-coded copies that
// cross the domains through two-flop synchronizers (wr -> rd for the empty flag, rd -> wr for the full flag). The
// storage is an array written in the wr_clk domain and read combinationally in the rd_clk domain: the read address
// only moves onto an entry after the write pointer that covers it has crossed the synchronizer, so the data is
// stable by then.
//
// Parameters
//   WIDTH  data width in bits
//   DEPTH  entries, a power of two >= 2 (default 8); anything else is an elaboration error
//
// Resets
//   - wr_rst_n and rd_rst_n are raw asynchronous, active-low resets; either one resets the whole FIFO. Their AND
//     (arst_n) asserts the reset of both domains at once (asynchronously), and each domain releases through its own
//     two-flop reset synchronizer: a domain leaves reset on the second of its clock edges after both inputs are high.
//     So the release needs no ordering, overlap or synchronous timing from the integrator, and a reset of one side
//     alone clears both pointers together, under the reset-domain rule below.
//   - A reset of either side drops the words in flight. wr_ready and rd_valid are 0 while their domain is in reset. A
//     word pushed while the read domain is still leaving reset is kept.
//   - Storage is not reset. rd_data is undefined while rd_valid is 0.
//   - Reset-domain rule. A one-sided reset is a reset-domain crossing. Asserting either input clears both domains'
//     pointers, and with them wr_ready, rd_valid and rd_data, asynchronously: at any time relative to each domain's
//     clock, not on its edges. So across every assertion of either input, the logic on each side (what drives wr_valid
//     / rd_ready and samples wr_ready / rd_valid / rd_data) must be either
//       (a) in reset itself, asserted no later than the FIFO input (its release is free), or
//       (b) idle: wr_valid (rd_ready) is 0 and no flop samples wr_ready (rd_valid, rd_data) or anything derived from
//           them, from before the assertion until its FIFO domain has left reset (two of its clock edges after both
//           inputs are high).
//     For the side whose own reset is the asserted input, (a) is the usual wiring; the other side is the crossing.
//     Otherwise its consumer can capture rd_valid = 1 with rd_data a mix of two entries (a word that was never
//     pushed), or go metastable with no synchronizer behind it. Neither formal nor the unit tb can see this (both model
//     the reset as a clean step), so it is an integration rule.
//     A side held idle under (b) has no port that shows when the other input asserts or when its own FIFO domain has
//     left reset (wr_ready / rd_valid low look like full / empty), and the release depends on the other input. So the
//     idle window needs a handshake with the logic that owns the other reset, or a fixed wait after that input's
//     release long enough for two of this side's clock edges.
//   - The side that is not reset has no port that says its words were dropped: wr_ready low looks like full, rd_valid
//     low like empty. A requester that tracks outstanding words (a tag table waiting for responses) must be reset with
//     the FIFO, as in (a), or be told by a separate signal. Open: the HBM controller's clk_mc reset sequence
//     (hbm.md has one rst_n; status note, Phase 3 open question 9).
//
// Clock-domain crossings: constraints for STA in Phase 6. Command options and behaviour are checked against the OpenSTA
// in the pinned OpenROAD (18f08d7) on ASAP7 netlists of this block from ORFS synthesis (flattened under a wrapper, and
// as top); nothing here is checked by simulation, lint or formal. T_wr / T_rd are the clock periods. U is the margin
// the exceptions below leave by hand: -ignore_clock_latency also ignores set_clock_uncertainty and clock latency (the
// required time does not move), so U = period jitter + wr_clk skew + rd_clk skew, the skews taken between the
// registers each item names (Gray: across the launching bits and across the capturing bits; mem: between the mem
// registers and the Gray source registers, and between wgray_r1 and the consumer's register). Every wr_clk <-> rd_clk
// path of this block ends at a wgray_r1 / rgray_w1 D pin, starts at a mem register, or runs through the arst_n net;
// the three items below cover these by endpoint, by startpoint and by net.
//   - Never set_clock_groups (-asynchronous or -exclusive) on wr_clk / rd_clk and never set_false_path between them
//     (-from / -to either clock, with or without -setup / -hold). In this OpenSTA an -asynchronous clock group or a
//     false path removes the set_max_delay below without a warning (the path turns unconstrained; after a clock group,
//     set_max_delay -reset_path does not restore it); a -hold false path removes the set_min_delay. The one clock-group
//     form that keeps both bounds here is set_clock_groups -asynchronous -allow_paths; nothing below needs it.
//   - Gray pointers, by endpoint (<wgray_r1 D pins>: the D pins of all AW+1 wgray_r1 flops; likewise rgray_w1):
//       set_max_delay -ignore_clock_latency -from [get_clocks wr_clk] -to <wgray_r1 D pins> T_wr-U
//       set_min_delay -ignore_clock_latency -from [get_clocks wr_clk] -to <wgray_r1 D pins> 0
//       set_max_delay -ignore_clock_latency -from [get_clocks rd_clk] -to <rgray_w1 D pins> T_rd-U
//       set_min_delay -ignore_clock_latency -from [get_clocks rd_clk] -to <rgray_w1 D pins> 0
//     By endpoint, not -from the wgray / rgray registers: the Gray MSB is the binary MSB, so Yosys merges wgray[AW]
//     into wbin[AW] and rgray[AW] into rbin[AW] (flattened or kept hierarchy alike: AW wgray flops, and wgray_r1[AW].D
//     comes from wbin[AW]). A -from <wgray regs> form leaves that bit under the default cross-clock check. The max
//     delay includes the capture flop's setup. Each bit then arrives within T_wr-U (T_rd-U) of its launching edge and
//     not before it; with U covering jitter and the skew across the launching and across the capturing flops, the
//     bits of two successive increments cannot overtake one another, so a synchronizer never captures a code two steps
//     away (a pointer value that never existed). With U so defined that is the whole bus-skew requirement, and no
//     separate skew check is needed (this OpenSTA has no bus-skew command). The min delay replaces the cross-clock
//     hold check, which has no meaning between unrelated clocks.
//   - mem -> consumer: the array is written in wr_clk and read through the DEPTH:1 read mux into the consumer's
//     register outside this block. A word is used two rd_clk edges after the edge that captured its write pointer in
//     wgray_r1 at the earliest, so the budget from a mem register's clock pin to the consumer's D (setup included) is
//     2*T_rd-U; the Gray crossing's own delay only adds slack.
//       top level (afifo inside the design):
//       set_max_delay -ignore_clock_latency -from <mem regs> -to [get_clocks rd_clk] 2*T_rd-U
//       set_min_delay -ignore_clock_latency -from <mem regs> -to [get_clocks rd_clk] 0
//     No -through: in the rd_clk domain the mem registers reach only the read mux. Not -through the rd_data pins
//     either: after a normal (not -hier) link_design there are no hierarchical pins, and a flattened netlist has no
//     rd_data net; OpenSTA then drops the whole exception with only a warning (STA-0472).
//       block level (afifo as top; <consumer share> is the consumer's part of the budget, outside the block):
//       set_output_delay <consumer share> -clock rd_clk [get_ports rd_data*]
//       set_max_delay -ignore_clock_latency -from <mem regs> -to [get_ports rd_data*] 2*T_rd-U
//       set_min_delay -ignore_clock_latency -from <mem regs> -to [get_ports rd_data*] 0
//     OpenSTA subtracts the output delay from the max delay itself, so the max delay is the whole budget (taking the
//     consumer's share off it as well would count it twice). The output delay also times rbin -> rd_data, which is
//     unconstrained without it. In both forms rbin -> read mux -> rd_data stays an ordinary rd_clk path (-from
//     <mem regs> leaves it out).
//   - Reset paths. arst_n (the wr_rst_n & rd_rst_n gate) releases asynchronously to both clocks and asserts
//     asynchronously by design (reset-domain rule above). The only exception, scoped to that net:
//       set_false_path -through <arst_n net>
//     It removes recovery / removal at the clear pins of the four reset-synchronizer flops (wr_rs0, wrst_n, rd_rs0,
//     rrst_n) and any path through their clear arcs. Recovery / removal from wrst_n to the write-domain pointer flops
//     and from rrst_n to the read-domain ones are ordinary same-clock checks and must stay: no blanket false path to
//     reset pins (such as -to */RESETN) and none from the raw reset ports beyond this net.
//     Clear arcs (RESETN -> Q): sta_preset_clear_arcs_enabled is 0 by default in this OpenSTA, so these arcs are not in
//     the timing graph and no assertion path through a flop's clear is timed at all; this text assumes nothing about
//     the setting. With it set to 1, wrst_n -> clear arc of wgray / wbin[AW] -> wgray_r1 D and rrst_n -> rgray /
//     rbin[AW] -> rgray_w1 D appear as cross-clock paths; they end at the Gray endpoints and the endpoint form above
//     covers them. Checked with both settings.
//   - Minimum pulse width. A low pulse on arst_n must clear all four reset-synchronizer flops; a runt that clears one
//     domain and not the other brings back phantom words. The cell limit is small (ASAP7 DFFASRHQNx1 RESETN low
//     pulse: 25.6 ps at TT with a fast input slew, more with a slow one), but this OpenSTA does not check it:
//     set_min_pulse_width on a RESETN pin is accepted and never reported (only clock pins are checked). So it is a
//     rule on the reset sources: wr_rst_n / rd_rst_n come from a register or another glitch-free source, and each
//     low pulse lasts at least one period of the slower clock.
//   - Names and synthesis. The constraints above name only the wgray_r1 / rgray_w1 flops, the mem registers and the
//     arst_n net, and these survive flattening (ORFS flat: u_f.wgray_r1[i], u_f.mem[j][k], and u_f.arst_n still
//     drives exactly the four synchronizer RESETN pins after CTS), so a kept hierarchy is optional. To keep one anyway,
//     ORFS SYNTH_KEEP_MODULES must be *afifo: the module elaborates as $paramod$<hash>\afifo, and "afifo" matches
//     nothing (Yosys warns 'Selection "afifo" did not match any module' and flattens it). Never list it in
//     SYNTH_RETIME_MODULES. set_dont_touch in OpenROAD (no resizing, buffering or removal): the synchronizer flops
//     (wgray_r1/r2, rgray_w1/w2, wr_rs0/wrst_n, rd_rs0/rrst_n), the Gray source flops (wgray[AW-1:0] and wbin[AW],
//     rgray[AW-1:0] and rbin[AW]; there is no wgray[AW] / rgray[AW] flop, see above) and the arst_n net. The pinned
//     ORFS has no variable for dont-touch, so Phase 6 adds it in the design's floorplan script.
//
// Latency and throughput: a word pushed at a wr_clk edge shows on rd_valid two rd_clk edges after the edge that
// registers the write pointer into the read domain's synchronizer; a freed entry reaches wr_ready two wr_clk edges
// after the read pointer moves. The credit round trip limits throughput by DEPTH. Measured with both clocks equal and
// both sides always ready: DEPTH 2 gives 1/3 word per cycle, DEPTH 4 gives 2/3, DEPTH 8 (the default) gives 1 (997 of
// 1000 cycles). Size DEPTH for the slower clock's round trip of about 6 cycles.
module afifo #(
  parameter int WIDTH = 8,
  parameter int DEPTH = 8
) (
  // write domain
  input  logic             wr_clk,
  input  logic             wr_rst_n,
  input  logic             wr_valid,
  output logic             wr_ready,
  input  logic [WIDTH-1:0] wr_data,
  // read domain
  input  logic             rd_clk,
  input  logic             rd_rst_n,
  output logic             rd_valid,
  input  logic             rd_ready,
  output logic [WIDTH-1:0] rd_data
);
  localparam int AW = $clog2(DEPTH);

  if (DEPTH < 2 || (DEPTH & (DEPTH - 1)) != 0) begin : g_bad_depth
    $error("afifo: DEPTH must be a power of two >= 2");
  end

  logic [WIDTH-1:0] mem [DEPTH];

  // reset: either input asserts both domains; each domain releases synchronously to its own clock
  logic arst_n;
  logic wr_rs0, rd_rs0;                  // first synchronizer stage
  logic wrst_n, rrst_n;                  // domain resets (second stage)
  assign arst_n = wr_rst_n & rd_rst_n;

  always_ff @(posedge wr_clk or negedge arst_n) begin
    if (!arst_n) begin wr_rs0 <= 1'b0; wrst_n <= 1'b0; end
    else         begin wr_rs0 <= 1'b1; wrst_n <= wr_rs0; end
  end
  always_ff @(posedge rd_clk or negedge arst_n) begin
    if (!arst_n) begin rd_rs0 <= 1'b0; rrst_n <= 1'b0; end
    else         begin rd_rs0 <= 1'b1; rrst_n <= rd_rs0; end
  end

  // write domain state
  logic [AW:0] wbin, wgray;
  logic [AW:0] rgray_w1, rgray_w2;       // read pointer (Gray) synchronized into wr_clk
  // read domain state
  logic [AW:0] rbin, rgray;
  logic [AW:0] wgray_r1, wgray_r2;       // write pointer (Gray) synchronized into rd_clk

  // full: the Gray write pointer equals the synchronized read pointer with its top two bits inverted
  localparam logic [AW:0] FULL_MASK = (AW + 1)'(3) << (AW - 1);
  logic push, pop;
  logic [AW:0] wbin_n, rbin_n, wgray_n, rgray_n;

  assign wr_ready = wrst_n && (wgray != (rgray_w2 ^ FULL_MASK));
  assign rd_valid = rrst_n && (rgray != wgray_r2);
  assign rd_data  = mem[rbin[AW-1:0]];

  assign push   = wr_valid && wr_ready;
  assign pop    = rd_valid && rd_ready;
  assign wbin_n = wbin + (AW + 1)'(push);
  assign rbin_n = rbin + (AW + 1)'(pop);
  assign wgray_n = wbin_n ^ (wbin_n >> 1);       // binary to Gray
  assign rgray_n = rbin_n ^ (rbin_n >> 1);

  always_ff @(posedge wr_clk or negedge wrst_n) begin
    if (!wrst_n) begin
      wbin     <= '0;
      wgray    <= '0;
      rgray_w1 <= '0;
      rgray_w2 <= '0;
    end else begin
      wbin     <= wbin_n;
      wgray    <= wgray_n;
      rgray_w1 <= rgray;
      rgray_w2 <= rgray_w1;
    end
  end

  always_ff @(posedge wr_clk) begin
    if (push) mem[wbin[AW-1:0]] <= wr_data;
  end

  always_ff @(posedge rd_clk or negedge rrst_n) begin
    if (!rrst_n) begin
      rbin     <= '0;
      rgray    <= '0;
      wgray_r1 <= '0;
      wgray_r2 <= '0;
    end else begin
      rbin     <= rbin_n;
      rgray    <= rgray_n;
      wgray_r1 <= wgray;
      wgray_r2 <= wgray_r1;
    end
  end

`ifdef FORMAL
  // ---------------------------------------------------------------------------------------------------------------
  // Formal-only properties (not compiled in synthesis, lint or simulation; tb/formal/afifo.sby, multiclock).
  // Pointers have AW+1 bits, so distances are taken modulo 2^(AW+1) and are meaningful while they stay <= DEPTH.
  // Chain, in unwrapped terms:   srw2 <= srw1 <= rbin <= wbin   (read pointer as seen by the writer, behind)
  //                              rbin <= sw2 <= sw1 <= wbin     (write pointer as seen by the reader, behind)
  // wbin - srw2 <= DEPTH is what the full flag enforces, and rd_valid is exactly sw2 != rbin.
  function [AW:0] g2b(input [AW:0] g);
    integer i;
    begin
      g2b[AW] = g[AW];
      for (i = AW - 1; i >= 0; i = i - 1) g2b[i] = g2b[i + 1] ^ g[i];
    end
  endfunction

  wire [AW:0] f_srw2 = g2b(rgray_w2), f_srw1 = g2b(rgray_w1);
  wire [AW:0] f_sw2  = g2b(wgray_r2), f_sw1  = g2b(wgray_r1);
  wire [AW:0] f_da2 = wbin - f_srw2, f_da1 = wbin - f_srw1, f_dc = wbin - rbin;     // writer-side chain
  wire [AW:0] f_e2  = f_sw2 - rbin,  f_e1  = f_sw1 - rbin;                          // reader-side chain

  always @(*) begin
    // Gray registers follow their binary pointers
    assert (wgray == (wbin ^ (wbin >> 1)));
    assert (rgray == (rbin ^ (rbin >> 1)));
    // pointer chains stay ordered and within one FIFO depth
    assert (f_da2 <= DEPTH);
    assert (f_da1 <= f_da2);
    assert (f_dc  <= f_da1);
    assert (f_e2  <= f_e1);
    assert (f_e1  <= f_dc);
    // flags are exact functions of the synchronized pointers
    assert (wr_ready == (wrst_n && f_da2 != DEPTH));
    assert (rd_valid == (rrst_n && f_e2 != 0));
    // reset synchronizers: the second stage is released only after the first
    assert (!wrst_n || wr_rs0);
    assert (!rrst_n || rd_rs0);
    // both domains are cleared together. While the writer is in reset the reader (released or not) has seen no write,
    // so its pointers are zero too; while the reader is in reset the writer has seen no read (it may have pushed).
    assert (wrst_n || (wbin == 0 && rgray_w1 == 0 && rgray_w2 == 0 && rbin == 0 && wgray_r1 == 0 && wgray_r2 == 0));
    assert (rrst_n || (rbin == 0 && wgray_r1 == 0 && wgray_r2 == 0 && rgray_w1 == 0 && rgray_w2 == 0));
    // no overflow, no underflow: a push needs a free entry, a pop an occupied one
    assert (!wr_ready || f_dc < DEPTH);
    assert (!rd_valid || f_dc > 0);
  end

  // One tagged entry (a free, constant pointer value): the data pushed at that pointer is the data popped at it.
  (* anyconst *) reg [AW:0] f_tag;
  reg [WIDTH-1:0] f_tdata;
  always @(posedge wr_clk) if (wrst_n && push && wbin == f_tag) f_tdata <= wr_data;
  // while the tagged entry is occupied (between rbin and wbin) the array holds its data
  wire [AW:0] f_tocc = f_tag - rbin;
  wire        f_tin  = (f_tocc < f_dc);
  wire        f_twr  = wrst_n && wr_valid && wr_ready && wbin == f_tag;     // being written this edge
  always @(*) begin
    if (f_tin && !f_twr) assert (mem[f_tag[AW-1:0]] == f_tdata);
    if (rd_valid && rbin == f_tag) assert (rd_data == f_tdata);
  end
`endif
endmodule
