// Formal wrappers for sram_1r1w and sram_1rw (SymbiYosys). One tracked location and lane (free constants): a shadow
// copy of that lane follows the writes, and every read of that location must return the shadow value as it was
// BEFORE the same edge's write (read-first for sram_1r1w), one edge later. rd_data / rdata must hold when no read
// is issued (and for sram_1rw also in a write cycle). Addresses are constrained to < DEPTH.
`ifndef FDEPTH
`define FDEPTH 4
`endif

module sram_1r1w_formal #(
  parameter WIDTH = 8,
  parameter DEPTH = `FDEPTH,
  parameter GRAN  = 4
) (
  input                     clk,
  input                     wr_en,
  input  [AW-1:0]           wr_addr,
  input  [WIDTH-1:0]        wr_data,
  input  [NB-1:0]           wr_be,
  input                     rd_en,
  input  [AW-1:0]           rd_addr
);
  localparam AW = (DEPTH > 1) ? $clog2(DEPTH) : 1;
  localparam NB = WIDTH / GRAN;
  wire [WIDTH-1:0] rd_data;
  sram_1r1w #(.WIDTH(WIDTH), .DEPTH(DEPTH), .GRAN(GRAN)) dut (
    .clk(clk), .wr_en(wr_en), .wr_addr(wr_addr), .wr_data(wr_data), .wr_be(wr_be),
    .rd_en(rd_en), .rd_addr(rd_addr), .rd_data(rd_data));

  (* anyconst *) reg [AW-1:0] ta;                 // tracked address and lane
  (* anyconst *) reg [3:0]    tl;
  wire [3:0] lane = tl % NB;
  always @(*) begin
    assume (ta < DEPTH);
    assume (wr_addr < DEPTH);
    assume (rd_addr < DEPTH);
  end

  reg             s_valid = 0;                    // the tracked lane has been written
  reg [GRAN-1:0]  s_data;
  always @(posedge clk) if (wr_en && wr_addr == ta && wr_be[lane]) begin
    s_valid <= 1'b1;
    s_data  <= wr_data[lane*GRAN +: GRAN];
  end

  // what a read issued at this edge must return: the shadow before this edge's write
  reg             p_chk = 0;                      // previous edge read the tracked word with a defined lane
  reg [GRAN-1:0]  p_exp;
  reg             p_have = 0;                     // some read has happened: rd_data is defined
  reg [WIDTH-1:0] p_rd;
  always @(posedge clk) begin
    if (rd_en) begin
      p_chk  <= (rd_addr == ta) && s_valid;
      p_exp  <= s_data;
      p_have <= 1'b1;
    end
    p_rd <= rd_data;
  end
  always @(*) begin
    if (p_chk) assert (rd_data[lane*GRAN +: GRAN] == p_exp);
  end
  // rd_data holds when no read was issued at the previous edge
  reg p_rden = 0, p_started = 0;
  always @(posedge clk) begin p_rden <= rd_en; p_started <= 1; end
  always @(*) if (p_started && p_have && !p_rden) assert (rd_data == p_rd);

  always @(posedge clk) begin
    cover (p_chk);
    cover (p_chk && rd_en && wr_en && rd_addr == wr_addr);           // same-address read and write in one cycle
    cover (rd_en && wr_en && rd_addr == ta && wr_addr == ta && wr_be[lane] && s_valid);
  end
endmodule

module sram_1rw_formal #(
  parameter WIDTH = 8,
  parameter DEPTH = `FDEPTH,
  parameter GRAN  = 4
) (
  input                     clk,
  input                     en,
  input                     we,
  input  [AW-1:0]           addr,
  input  [WIDTH-1:0]        wdata,
  input  [NB-1:0]           be
);
  localparam AW = (DEPTH > 1) ? $clog2(DEPTH) : 1;
  localparam NB = WIDTH / GRAN;
  wire [WIDTH-1:0] rdata;
  sram_1rw #(.WIDTH(WIDTH), .DEPTH(DEPTH), .GRAN(GRAN)) dut (
    .clk(clk), .en(en), .we(we), .addr(addr), .wdata(wdata), .be(be), .rdata(rdata));

  (* anyconst *) reg [AW-1:0] ta;
  (* anyconst *) reg [3:0]    tl;
  wire [3:0] lane = tl % NB;
  always @(*) begin
    assume (ta < DEPTH);
    assume (addr < DEPTH);
  end

  reg            s_valid = 0;
  reg [GRAN-1:0] s_data;
  always @(posedge clk) if (en && we && addr == ta && be[lane]) begin
    s_valid <= 1'b1;
    s_data  <= wdata[lane*GRAN +: GRAN];
  end

  reg            p_chk = 0;
  reg [GRAN-1:0] p_exp;
  reg            p_have = 0;
  reg [WIDTH-1:0] p_r;
  reg            p_rd = 0, p_started = 0;
  always @(posedge clk) begin
    p_rd <= en && !we;
    p_started <= 1;
    if (en && !we) begin
      p_chk  <= (addr == ta) && s_valid;
      p_exp  <= s_data;
      p_have <= 1'b1;
    end
    p_r <= rdata;
  end
  always @(*) begin
    if (p_chk) assert (rdata[lane*GRAN +: GRAN] == p_exp);
    if (p_started && p_have && !p_rd) assert (rdata == p_r);          // no change in a write cycle or when idle
  end

  always @(posedge clk) begin
    cover (p_chk);
    cover (p_chk && en && we);
  end
endmodule
