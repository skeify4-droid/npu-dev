// Formal wrapper for afifo (SymbiYosys multiclock). The internal properties live in rtl/common/afifo.sv under
// `ifdef FORMAL because they need the internal pointers. This wrapper supplies the environment:
//   - wr_clk and rd_clk are free inputs (any interleaving of edges, including simultaneous ones);
//   - wr_valid, wr_data, rd_ready and both resets are free. The only assumption is that the design starts in reset
//     (both inputs low in the first step). Either reset may assert or release at any time, alone or skewed against
//     the other: afifo combines them internally.
//   - `define PHANTOM (tasks ph*): a port-level check that needs no internals. Pushes are counted in wr_clk and pops in
//     rd_clk (one driver per domain), both counters cleared while either reset input is low. Then a word on rd_valid
//     must have been pushed and not popped (no phantom word), and wr_ready must mean a free entry (no overflow). On the
//     afifo before the reset synchronizers (resets used directly per domain), this fails with the wr reset asserted a
//     few rd_clk edges before the rd reset.
`ifndef FDEPTH
`define FDEPTH 4
`endif
module afifo_formal #(
  parameter WIDTH = 2,
  parameter DEPTH = `FDEPTH
) (
  input              wr_clk,
  input              rd_clk,
  input              wr_rst_n,
  input              rd_rst_n,
  input              wr_valid,
  input  [WIDTH-1:0] wr_data,
  input              rd_ready
);
  wire             wr_ready, rd_valid;
  wire [WIDTH-1:0] rd_data;

  afifo #(.WIDTH(WIDTH), .DEPTH(DEPTH)) dut (
    .wr_clk(wr_clk), .wr_rst_n(wr_rst_n), .wr_valid(wr_valid), .wr_ready(wr_ready), .wr_data(wr_data),
    .rd_clk(rd_clk), .rd_rst_n(rd_rst_n), .rd_valid(rd_valid), .rd_ready(rd_ready), .rd_data(rd_data));

  reg init = 1;
  always @($global_clock) init <= 0;
  always @(*) if (init) assume (!wr_rst_n && !rd_rst_n);

  // Port-level shadow state: pushes counted in wr_clk, pops in rd_clk, both cleared while either reset input is low.
  // Asserted in the ph* tasks only; the covers below use it in every task.
  localparam CW = $clog2(DEPTH) + 2;
  wire arst_n = wr_rst_n && rd_rst_n;
  reg [CW-1:0] npush = 0, npop = 0;
  always @(posedge wr_clk or negedge arst_n)
    if (!arst_n) npush <= 0; else if (wr_valid && wr_ready) npush <= npush + 1'b1;
  always @(posedge rd_clk or negedge arst_n)
    if (!arst_n) npop <= 0;  else if (rd_valid && rd_ready) npop <= npop + 1'b1;
  wire [CW-1:0] occ = npush - npop;
`ifdef PHANTOM
  always @(*) begin
    assert (occ <= DEPTH);
    if (rd_valid) assert (occ != 0);             // no phantom word
    if (wr_ready) assert (occ < DEPTH);          // no overflow
  end
`endif

  // Non-vacuity (cover task), all at the ports. "_q" is the value in the step before; a running FIFO is one that has
  // both inputs high in the step before, words inside and at least one pop done, so a one-sided reset of it is not
  // the tail of the initial reset.
  //   c0  a word goes through;
  //   c1  the FIFO fills: wr_ready has been high since the last reset input assertion and is low now, no reset since
  //       (nothing needs an rd_clk edge: this trace can fill the FIFO with rd_clk never edging);
  //   c2  the write input alone asserts while the reader shows a word (rd_valid in the step before);
  //   c3  the read input alone asserts while the reader shows a word (rd_valid in the step before);
  //   c4  a word pushed at a wr_clk edge after which the read domain is still in reset (f_rdq < 2, so rrst_n is still
  //       low: strictly before it rises, not on the rd_clk edge that releases it) is popped later, no reset between;
  //   c5  a word goes through after a write-only reset of a running FIFO;
  //   c6  a word goes through after a read-only reset of a running FIFO;
  //   c7  the FIFO fills and wr_ready returns, no reset input asserted in between: a pop has crossed back through
  //       rgray_w1 / rgray_w2 (c1 does not need that).
  reg f_wr_up = 0;                               // c1: wr_ready seen high, no reset input asserted since
  always @($global_clock) f_wr_up <= arst_n && (f_wr_up || wr_ready);
  reg f_full = 0;                                // c7: c1 seen (full), no reset input asserted since
  always @($global_clock) f_full <= arst_n && (f_full || (f_wr_up && !wr_ready));
  reg          f_arst_q = 0, f_rdv_q = 0;        // c2, c3, c5, c6: values in the step before
  reg [CW-1:0] f_occ_q = 0, f_npush_q = 0, f_npop_q = 0;
  always @($global_clock) begin
    f_arst_q <= arst_n; f_rdv_q <= rd_valid; f_occ_q <= occ; f_npush_q <= npush; f_npop_q <= npop;
  end
  reg [1:0] f_rdq = 0;                           // c4: rd_clk edges since both inputs went high, saturating at 2
  always @(posedge rd_clk or negedge arst_n)     //     (rrst_n == (f_rdq == 2) holds in every step)
    if (!arst_n) f_rdq <= 0; else if (f_rdq != 2'd2) f_rdq <= f_rdq + 1'b1;
  reg          f_early = 0;                      // c4: npush moved this step and the read domain is still in reset
  reg [CW-1:0] f_eidx = 0;                       //     after it; f_eidx is that word's push number
  always @($global_clock)
    if (!arst_n) f_early <= 0;
    else if (!f_early && npush != f_npush_q && f_rdq != 2'd2) begin f_early <= 1; f_eidx <= f_npush_q; end
  reg f_wo = 0, f_ro = 0;                        // c5, c6: one-sided assertion of a running FIFO seen
  always @($global_clock) begin
    if (f_arst_q && f_occ_q != 0 && f_npop_q != 0 && !wr_rst_n && rd_rst_n) f_wo <= 1;
    if (f_arst_q && f_rdv_q      && f_npop_q != 0 && wr_rst_n && !rd_rst_n) f_ro <= 1;
  end
  always @(*) begin
    cover (rd_valid && rd_ready && rd_rst_n);                                  // c0
    cover (f_wr_up && !wr_ready && arst_n);                                    // c1
    cover (f_arst_q && f_rdv_q && !wr_rst_n && rd_rst_n);                      // c2
    cover (f_arst_q && f_rdv_q && wr_rst_n && !rd_rst_n);                      // c3
    cover (f_early && rd_valid && rd_ready && npop == f_eidx);                 // c4
    cover (f_wo && arst_n && rd_valid && rd_ready);                            // c5
    cover (f_ro && arst_n && rd_valid && rd_ready);                            // c6
    cover (f_full && wr_ready && arst_n);                                      // c7
  end
endmodule
