// Synchronous single-port SRAM wrapper (1RW): one address, either a read or a write per cycle, byte-write mask,
// 1-cycle read latency. Behavioral model by default; `SRAM_MACRO selects a technology macro (stub, see below).
// This is the module for the single-port fallback of the scratchpad (microarch.md section 2.3).
//
// Ports
//   en, we, addr, wdata, be (one mask bit per GRAN data bits; 0 keeps the lane), rdata (registered)
//
// Defined behavior (choices, listed as open questions in docs/reports/status.md, "Phase 3: common blocks")
//   - en = 1, we = 0: read; rdata shows the word one clock edge later.
//   - en = 1, we = 1: write the lanes selected by be. rdata does NOT change in a write cycle (no-change mode):
//     it keeps the value of the last read. So "read-during-write" does not exist here: there is only one address,
//     and a write cycle returns nothing.
//   - en = 0: nothing happens, rdata holds.
//   - Read latency is exactly 1.
//   - Contents are not reset: a location read before it was written returns X in simulation.
//   - Addresses >= DEPTH (DEPTH not a power of two) are out of contract.
//
// Parameters
//   WIDTH  data width in bits
//   DEPTH  words (>= 1)
//   GRAN   bits per write-mask bit (default 8); WIDTH must be a multiple of GRAN
//
// Macro branch (stub). With `SRAM_MACRO defined the array is replaced by `sram_macro_1rw`, a wrapper with this
// module's port list and parameters that Phase 6 provides for the ASAP7 fakeram. It does not exist yet, so defining
// SRAM_MACRO fails at elaboration, on purpose. Whether the real macro leaves its output unchanged on a write needs
// checking against the macro model there.
module sram_1rw #(
  parameter int  WIDTH = 32,
  parameter int  DEPTH = 16,
  parameter int  GRAN  = 8,
  localparam int AW    = (DEPTH > 1) ? $clog2(DEPTH) : 1,
  localparam int NB    = WIDTH / GRAN
) (
  input  logic             clk,
  input  logic             en,
  input  logic             we,
  input  logic [AW-1:0]    addr,
  input  logic [WIDTH-1:0] wdata,
  input  logic [NB-1:0]    be,
  output logic [WIDTH-1:0] rdata
);
  if (WIDTH % GRAN != 0) begin : g_bad_gran
    $error("sram_1rw: WIDTH must be a multiple of GRAN");
  end

`ifdef SRAM_MACRO
  sram_macro_1rw #(.WIDTH(WIDTH), .DEPTH(DEPTH), .GRAN(GRAN)) u_macro (
    .clk(clk), .en(en), .we(we), .addr(addr), .wdata(wdata), .be(be), .rdata(rdata));
`else
  logic [WIDTH-1:0] mem [DEPTH];

  always_ff @(posedge clk) begin
    if (en && !we) rdata <= mem[addr];
    if (en && we) begin
      for (int b = 0; b < NB; b++) begin
        if (be[b]) mem[addr][b*GRAN +: GRAN] <= wdata[b*GRAN +: GRAN];
      end
    end
  end
`endif
endmodule
