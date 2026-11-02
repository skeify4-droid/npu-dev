// Self-checking testbench for sram_1rw (iverilog -g2012): random reads and writes against a per-lane reference array.
// Checks the documented behavior: 1-cycle read latency, byte masks, rdata unchanged in a write cycle and while en is
// low (no-change mode). Lanes never written are not compared.
`timescale 1ns/1ps

module sram_1rw_chk #(
  parameter int WIDTH = 32,
  parameter int DEPTH = 16,
  parameter int GRAN  = 8,
  parameter int ID    = 0
) (
  input  logic clk,
  input  int   pen,           // percent chance of an access per cycle
  input  int   pwr,           // percent of accesses that are writes
  input  int   hot,           // percent chance the address comes from a 2-word hot set
  output int   errors, n_read, n_write, n_partial, n_hold
);
  localparam int AW = (DEPTH > 1) ? $clog2(DEPTH) : 1;
  localparam int NB = WIDTH / GRAN;
  logic             en = 0, we = 0;
  logic [AW-1:0]    addr = 0;
  logic [WIDTH-1:0] wdata = 0, rdata;
  logic [NB-1:0]    be = 0;

  sram_1rw #(.WIDTH(WIDTH), .DEPTH(DEPTH), .GRAN(GRAN)) dut (.*);

  logic [WIDTH-1:0] mdata [DEPTH];
  logic [NB-1:0]    mknown [DEPTH];
  logic [WIDTH-1:0] cmp_data = '0;
  logic [NB-1:0]    cmp_known = '0;      // lanes of the held read value that are defined
  logic             have_exp = 0;

  function automatic logic [WIDTH-1:0] rand_word();
    logic [WIDTH+31:0] r;
    for (int i = 0; i < WIDTH; i += 32) r[i +: 32] = $urandom;
    return r[WIDTH-1:0];
  endfunction

  initial begin
    errors = 0; n_read = 0; n_write = 0; n_partial = 0; n_hold = 0;
    for (int a = 0; a < DEPTH; a++) begin mdata[a] = '0; mknown[a] = '0; end
  end

  always @(posedge clk) begin
    if (have_exp) begin
      for (int b = 0; b < NB; b++)
        if (cmp_known[b] && rdata[b*GRAN +: GRAN] !== cmp_data[b*GRAN +: GRAN]) begin
          errors++; $display("[%0d] rdata lane %0d = %h, expected %h", ID, b, rdata[b*GRAN +: GRAN], cmp_data[b*GRAN +: GRAN]);
        end
    end
    if (en && !we) begin
      cmp_data = mdata[addr]; cmp_known = mknown[addr]; have_exp = 1; n_read++;
    end else if (en && we) begin
      n_write++; n_hold++;                                              // rdata must keep the last read value
      if (be != '0 && be != '1) n_partial++;
      for (int b = 0; b < NB; b++) if (be[b]) begin
        mdata[addr][b*GRAN +: GRAN] = wdata[b*GRAN +: GRAN];
        mknown[addr][b] = 1'b1;
      end
    end else n_hold++;
    en    <= ($urandom_range(99) < pen);
    we    <= ($urandom_range(99) < pwr);
    addr  <= ($urandom_range(99) < hot) ? AW'($urandom_range(1) % DEPTH) : AW'($urandom_range(DEPTH - 1));
    wdata <= rand_word();
    be    <= (NB)'($urandom) | ((($urandom_range(3) == 0)) ? '0 : (($urandom_range(2) == 0) ? '1 : '0));
  end
endmodule

module tb_sram_1rw;
  localparam int NC = 5;
  logic clk = 0;
  always #5 clk = ~clk;
  int pen = 80, pwr = 50, hot = 0;
  int errs [NC], nrd [NC], nwr [NC], npart [NC], nhold [NC];

  sram_1rw_chk #(32, 16, 8, 0) c0 (clk, pen, pwr, hot, errs[0], nrd[0], nwr[0], npart[0], nhold[0]);
  sram_1rw_chk #(32, 13, 8, 1) c1 (clk, pen, pwr, hot, errs[1], nrd[1], nwr[1], npart[1], nhold[1]);
  sram_1rw_chk #(16, 2, 16, 2) c2 (clk, pen, pwr, hot, errs[2], nrd[2], nwr[2], npart[2], nhold[2]);
  sram_1rw_chk #(64, 8, 8, 3)  c3 (clk, pen, pwr, hot, errs[3], nrd[3], nwr[3], npart[3], nhold[3]);
  sram_1rw_chk #(8, 1, 8, 4)   c4 (clk, pen, pwr, hot, errs[4], nrd[4], nwr[4], npart[4], nhold[4]);

  int total;
  initial begin
    for (int ph = 0; ph < 7; ph++) begin
      case (ph)
        0: begin pen = 80;  pwr = 50;  hot = 0;   end
        1: begin pen = 100; pwr = 100; hot = 0;   end     // fill
        2: begin pen = 100; pwr = 0;   hot = 0;   end     // read back
        3: begin pen = 100; pwr = 50;  hot = 100; end     // write then read the same word back to back
        4: begin pen = 40;  pwr = 50;  hot = 50;  end     // idle gaps
        5: begin pen = 100; pwr = 30;  hot = 80;  end
        6: begin pen = 100; pwr = 70;  hot = 0;   end
      endcase
      repeat (2000) @(posedge clk);
    end
    total = 0;
    for (int i = 0; i < NC; i++) begin
      total += errs[i];
      if (nrd[i] < 1000 || nwr[i] < 1000 || nhold[i] < 100 || (i != 2 && i != 4 && npart[i] < 100)) begin
        total++; $display("coverage hole in config %0d: reads %0d writes %0d holds %0d partial %0d", i, nrd[i], nwr[i], nhold[i], npart[i]);
      end
    end
    if (total == 0) $display("PASS tb_sram_1rw");
    else            $fatal(1, "FAIL tb_sram_1rw: %0d errors", total);
    $finish;
  end
endmodule
