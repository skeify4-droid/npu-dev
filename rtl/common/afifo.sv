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
//   - wr_rst_n and rd_rst_n are raw asynchronous, active-low resets; either one resets the whole FIFO. Their AND asserts
//     the reset of both domains at once (asynchronously), and each domain releases through its own two-flop reset
//     synchronizer: a domain leaves reset on the second of its clock edges after both inputs are high. So the resets
//     need no ordering, overlap or synchronous release from the integrator, and a reset of one side alone (for example
//     a clk_mc domain reset by the HBM controller) clears both pointers together.
//   - A reset of either side drops the words in flight. wr_ready and rd_valid are 0 while their domain is in reset.
//   - Storage is not reset. rd_data is undefined while rd_valid is 0.
//
// Clock-domain crossings (constraints for STA, Phase 6). Three paths cross, all need a max delay, none a false path:
//   - wgray -> wgray_r1 and rgray -> rgray_w1: set_max_delay -datapath_only of about one launching-clock period and a
//     bus-skew limit below one period. A false path would let bits of successive increments arrive out of order, so
//     a synchronizer could capture a code two steps away (a pointer value that never existed).
//   - mem -> rd_data (written in wr_clk, read through the read mux into rd_clk logic): the same max delay. The data must
//     settle before the write pointer that covers it has passed both synchronizer flops.
//   - the raw resets into the other domain's reset synchronizer (asynchronous assertion only; release is synchronized).
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
