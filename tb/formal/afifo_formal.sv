// Formal wrapper for afifo (SymbiYosys multiclock). The properties live in rtl/common/afifo.sv under `ifdef FORMAL
// because they need the internal pointers. This wrapper only supplies the environment:
//   - wr_clk and rd_clk are free inputs (any interleaving of edges, including simultaneous ones);
//   - wr_valid, wr_data, rd_ready and both resets are free, except that the design starts in reset and the two
//     resets are asserted together (releases are independent). That is the usage the afifo header requires.
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

  reg init = 1, wq = 0, rq = 0;
  always @($global_clock) begin
    init <= 0;
    wq   <= wr_rst_n;
    rq   <= rd_rst_n;
  end
  always @(*) begin
    if (init) assume (!wr_rst_n && !rd_rst_n);
    if (wq && !wr_rst_n) assume (!rd_rst_n);      // a reset of one domain is a reset of both
    if (rq && !rd_rst_n) assume (!wr_rst_n);
  end

  // non-vacuity (cover task): a word goes through, the FIFO fills, a reset happens in the middle
  always @(*) begin
    cover (rd_valid && rd_ready && rd_rst_n);
    cover (!wr_ready && wr_rst_n);
  end
endmodule
