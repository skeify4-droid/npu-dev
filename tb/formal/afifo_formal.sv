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

`ifdef PHANTOM
  localparam CW = $clog2(DEPTH) + 2;
  wire arst_n = wr_rst_n && rd_rst_n;
  reg [CW-1:0] npush = 0, npop = 0;
  always @(posedge wr_clk or negedge arst_n)
    if (!arst_n) npush <= 0; else if (wr_valid && wr_ready) npush <= npush + 1'b1;
  always @(posedge rd_clk or negedge arst_n)
    if (!arst_n) npop <= 0;  else if (rd_valid && rd_ready) npop <= npop + 1'b1;
  wire [CW-1:0] occ = npush - npop;
  always @(*) begin
    assert (occ <= DEPTH);
    if (rd_valid) assert (occ != 0);             // no phantom word
    if (wr_ready) assert (occ < DEPTH);          // no overflow
  end
`endif

  // non-vacuity (cover task): a word goes through, the FIFO fills, a word goes through after a one-sided reset
  reg wr_only_reset = 0;
  always @($global_clock) if (!wr_rst_n && rd_rst_n) wr_only_reset <= 1;
  always @(*) begin
    cover (rd_valid && rd_ready && rd_rst_n);
    cover (!wr_ready && wr_rst_n);
    cover (wr_only_reset && rd_valid && rd_ready);
  end
endmodule
