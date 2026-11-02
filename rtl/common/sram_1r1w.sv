// Synchronous SRAM wrapper, one read port and one write port (1R1W), byte-write mask, 1-cycle read latency.
// Behavioral model by default; `SRAM_MACRO selects a technology macro (stub, see below).
//
// Ports
//   write: wr_en, wr_addr, wr_data, wr_be (one mask bit per GRAN data bits; a 0 bit keeps that lane)
//   read : rd_en, rd_addr -> rd_data, valid one clock edge later (registered output)
//
// Defined behavior (choices, listed as open questions in docs/reports/status.md, "Phase 3: common blocks")
//   - Read-during-write, same address in the same cycle: the read returns the OLD word (read-first). The write takes
//     effect for reads issued in the next cycle. A masked write returns the whole old word, all lanes.
//   - rd_data holds its last value while rd_en is 0.
//   - Read latency is exactly 1: rd_en at edge t gives rd_data after edge t.
//   - Contents are not reset and not initialized: a location read before it was written returns X in simulation
//     (arbitrary in formal). Nothing may depend on it.
//   - Addresses >= DEPTH (only possible when DEPTH is not a power of two) are out of contract: reads return X and
//     writes are dropped in simulation.
//
// Parameters
//   WIDTH  data width in bits
//   DEPTH  words (>= 1)
//   GRAN   bits per write-mask bit (default 8: byte write); WIDTH must be a multiple of GRAN
//
// Macro branch (stub). With `SRAM_MACRO defined, the array is replaced by an instance of module `sram_macro_1r1w`, which
// has exactly this module's port list and parameters and implements the same read-first, 1-cycle semantics on a
// fakeram / vendor macro. That wrapper does not exist yet: macro names, sizes and the real read-during-write
// behavior of the ASAP7 fakeram come with the Phase 6 flow, and a 1R1W macro may need a bypass register added there to
// keep the read-first rule. Defining SRAM_MACRO without that module fails at elaboration, on purpose.
module sram_1r1w #(
  parameter int  WIDTH = 32,
  parameter int  DEPTH = 16,
  parameter int  GRAN  = 8,
  localparam int AW    = (DEPTH > 1) ? $clog2(DEPTH) : 1,
  localparam int NB    = WIDTH / GRAN
) (
  input  logic             clk,
  input  logic             wr_en,
  input  logic [AW-1:0]    wr_addr,
  input  logic [WIDTH-1:0] wr_data,
  input  logic [NB-1:0]    wr_be,
  input  logic             rd_en,
  input  logic [AW-1:0]    rd_addr,
  output logic [WIDTH-1:0] rd_data
);
  if (WIDTH % GRAN != 0) begin : g_bad_gran
    $error("sram_1r1w: WIDTH must be a multiple of GRAN");
  end

`ifdef SRAM_MACRO
  sram_macro_1r1w #(.WIDTH(WIDTH), .DEPTH(DEPTH), .GRAN(GRAN)) u_macro (
    .clk(clk), .wr_en(wr_en), .wr_addr(wr_addr), .wr_data(wr_data), .wr_be(wr_be),
    .rd_en(rd_en), .rd_addr(rd_addr), .rd_data(rd_data));
`else
  logic [WIDTH-1:0] mem [DEPTH];

  always_ff @(posedge clk) begin
    if (rd_en) rd_data <= mem[rd_addr];                 // nonblocking: sees the array before this edge's write (read-first)
    if (wr_en) begin
      for (int b = 0; b < NB; b++) begin
        if (wr_be[b]) mem[wr_addr][b*GRAN +: GRAN] <= wr_data[b*GRAN +: GRAN];
      end
    end
  end
`endif
endmodule
