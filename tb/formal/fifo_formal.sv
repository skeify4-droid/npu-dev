// Formal wrappers for fifo (SymbiYosys, yosys read_verilog -formal, Verilog-2005 style properties).
//
//   fifo_formal_cnt   : no overflow, no underflow, count / full / empty / in_ready / out_valid against a shadow count.
//                       Proved by BMC and k-induction.
//   fifo_formal_order : data out in order, tracking one tagged entry from its push to its pop, and a stalled output
//                       keeps its data. Unbounded proof with abc pdr (k-induction
//                       cannot do it: the stored data is internal state it would have to be told about).
//
// The environment is free: in_valid, in_data, out_ready and rst_n are unconstrained inputs. The only assumption
// is that the design starts in reset.
`ifndef FDEPTH
`define FDEPTH 4
`endif
`ifndef FFT
`define FFT 0
`endif

module fifo_formal_cnt #(
  parameter WIDTH = 4,
  parameter DEPTH = `FDEPTH,
  parameter FT    = `FFT
) (
  input                 clk,
  input                 rst_n,
  input                 in_valid,
  input  [WIDTH-1:0]    in_data,
  input                 out_ready
);
  localparam CW = $clog2(DEPTH + 1);
  wire             in_ready, out_valid, full, empty;
  wire [WIDTH-1:0] out_data;
  wire [CW-1:0]    count;

  fifo #(.WIDTH(WIDTH), .DEPTH(DEPTH), .FALLTHROUGH(FT)) dut (
    .clk(clk), .rst_n(rst_n), .in_valid(in_valid), .in_ready(in_ready), .in_data(in_data),
    .out_valid(out_valid), .out_ready(out_ready), .out_data(out_data),
    .count(count), .full(full), .empty(empty));

  reg init = 1;
  always @(posedge clk) init <= 0;
  always @(*) if (init) assume (!rst_n);

  wire push = in_valid && in_ready;
  wire pop  = out_valid && out_ready;
  reg [CW:0] sh_cnt = 0;                       // model: pushes - pops, one bit wider than the DUT's count
  always @(posedge clk) begin
    if (!rst_n) sh_cnt <= 0;
    else        sh_cnt <= sh_cnt + push - pop;
  end

  always @(*) begin
    if (rst_n) begin
      assert (sh_cnt <= DEPTH);                                    // never overflows, never underflows
      assert (count == sh_cnt);
      assert (full  == (sh_cnt == DEPTH));
      assert (empty == (sh_cnt == 0));
      assert (in_ready == (sh_cnt < DEPTH));
      assert (out_valid == ((sh_cnt != 0) || (FT && in_valid)));
      if (push) assert (sh_cnt < DEPTH);                           // a push is only accepted with room
      if (pop && sh_cnt == 0) assert (FT && in_valid);             // an empty pop is only a fall-through bypass
    end
  end
  // non-vacuity
  always @(posedge clk) if (rst_n) begin
    cover (sh_cnt == DEPTH);
    cover (sh_cnt == DEPTH && pop);
    cover (sh_cnt == 1 && pop && !push);
    if (FT) cover (sh_cnt == 0 && pop && push);
  end
endmodule

module fifo_formal_order #(
  parameter WIDTH = 4,
  parameter DEPTH = `FDEPTH,
  parameter FT    = `FFT
) (
  input                 clk,
  input                 rst_n,
  input                 in_valid,
  input  [WIDTH-1:0]    in_data,
  input                 out_ready,
  input                 pick                   // free choice of which push is tagged
);
  localparam CW = $clog2(DEPTH + 1);
  wire             in_ready, out_valid, full, empty;
  wire [WIDTH-1:0] out_data;
  wire [CW-1:0]    count;

  fifo #(.WIDTH(WIDTH), .DEPTH(DEPTH), .FALLTHROUGH(FT)) dut (
    .clk(clk), .rst_n(rst_n), .in_valid(in_valid), .in_ready(in_ready), .in_data(in_data),
    .out_valid(out_valid), .out_ready(out_ready), .out_data(out_data),
    .count(count), .full(full), .empty(empty));

  reg init = 1;
  always @(posedge clk) init <= 0;
  always @(*) if (init) assume (!rst_n);

  wire push = in_valid && in_ready;
  wire pop  = out_valid && out_ready;

  reg        p_ok = 0, p_stall = 0;
  reg [WIDTH-1:0] p_data;
  always @(posedge clk) begin
    p_ok    <= rst_n;
    p_stall <= out_valid && !out_ready;
    p_data  <= out_data;
  end

  // a stalled output stays valid with the same data
  always @(posedge clk) if (p_ok && rst_n && p_stall) begin
    assert (out_valid);
    assert (out_data == p_data);
  end

  reg             tag_on = 0;                  // a tagged entry is in the FIFO
  reg [CW-1:0]    tpos;                        // entries ahead of it (start of cycle)
  reg [WIDTH-1:0] tdata;
  reg             done = 0;                    // for the cover: a tagged entry came out
  reg             deep = 0;                    // for the cover: it had to wait behind DEPTH-1 others

  always @(posedge clk) begin
    if (!rst_n) begin
      tag_on <= 0;
    end else if (tag_on) begin
      if (pop) begin
        if (tpos == 0) begin
          assert (out_data == tdata);          // in order: the tagged word is the one popped
          tag_on <= 0;
          done   <= 1;
        end else tpos <= tpos - 1'b1;
      end
    end else if (pick && push) begin
      if (pop && !(count != 0)) begin
        assert (out_data == in_data);          // fall-through bypass
        done <= 1;
      end else begin
        tag_on <= 1;
        tdata  <= in_data;
        tpos   <= count - (pop ? 1'b1 : 1'b0);
        if (count == DEPTH - 1 + (pop ? 1 : 0)) deep <= 1;
      end
    end
  end
  // the tagged word is where the model says it is
  always @(posedge clk) if (rst_n && tag_on) assert (tpos < count);

  always @(posedge clk) begin
    cover (done);
    cover (done && deep);
  end
endmodule
