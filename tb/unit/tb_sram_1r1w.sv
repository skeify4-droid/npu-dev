// Self-checking testbench for sram_1r1w (iverilog -g2012): random traffic against a per-lane reference array.
// Checks the documented behavior: 1-cycle read latency, read-first on a same-address read and write (the read returns
// the old word, all lanes), byte masks, rd_data hold while rd_en is low. Lanes never written are not compared.
`timescale 1ns/1ps

module sram_1r1w_chk #(
  parameter int WIDTH = 32,
  parameter int DEPTH = 16,
  parameter int GRAN  = 8,
  parameter int ID    = 0
) (
  input  logic clk,
  input  int   pw,            // percent chance of a write per cycle
  input  int   pr,            // percent chance of a read per cycle
  input  int   hot,           // percent chance an address is taken from a 2-word hot set (forces same-address collisions)
  output int   errors, n_rdw, n_read, n_partial
);
  localparam int AW = (DEPTH > 1) ? $clog2(DEPTH) : 1;
  localparam int NB = WIDTH / GRAN;
  logic             wr_en = 0, rd_en = 0;
  logic [AW-1:0]    wr_addr = 0, rd_addr = 0;
  logic [WIDTH-1:0] wr_data = 0, rd_data;
  logic [NB-1:0]    wr_be = 0;

  sram_1r1w #(.WIDTH(WIDTH), .DEPTH(DEPTH), .GRAN(GRAN)) dut (.*);

  logic [WIDTH-1:0] mdata [DEPTH];
  logic [NB-1:0]    mknown [DEPTH];
  logic [WIDTH-1:0] exp_data;            // expected rd_data after this edge (old word, before this edge's write)
  logic [NB-1:0]    exp_known;
  logic             have_exp = 0;        // a read has been issued since start: rd_data is defined
  logic [WIDTH-1:0] cmp_data;
  logic [NB-1:0]    cmp_known;

  function automatic [AW-1:0] pick_addr();
    if ($urandom_range(99) < hot) return AW'($urandom_range(1) % DEPTH);
    else return AW'($urandom_range(DEPTH - 1));
  endfunction

  function automatic logic [WIDTH-1:0] rand_word();
    logic [WIDTH+31:0] r;
    for (int i = 0; i < WIDTH; i += 32) r[i +: 32] = $urandom;
    return r[WIDTH-1:0];
  endfunction

  initial begin
    errors = 0; n_rdw = 0; n_read = 0; n_partial = 0;
    for (int a = 0; a < DEPTH; a++) begin mdata[a] = '0; mknown[a] = '0; end
  end

  always @(posedge clk) begin
    // 1. check rd_data (it holds the result of the read of the previous edge, or the held value)
    if (have_exp) begin
      for (int b = 0; b < NB; b++) begin
        if (cmp_known[b] && rd_data[b*GRAN +: GRAN] !== cmp_data[b*GRAN +: GRAN]) begin
          errors++; $display("[%0d] rd_data lane %0d = %h, expected %h", ID, b, rd_data[b*GRAN +: GRAN], cmp_data[b*GRAN +: GRAN]);
        end
      end
    end
    // 2. this edge: a read returns the word as it was before this edge's write
    if (rd_en) begin
      exp_data = mdata[rd_addr]; exp_known = mknown[rd_addr];
      cmp_data = exp_data; cmp_known = exp_known;                       // what rd_data must show after this edge
      have_exp = 1; n_read++;
      if (wr_en && wr_addr == rd_addr) n_rdw++;
    end                                                                  // else: cmp_* keep their value (hold)
    if (wr_en) begin
      if (wr_be != '0 && wr_be != '1) n_partial++;
      for (int b = 0; b < NB; b++) if (wr_be[b]) begin
        mdata[wr_addr][b*GRAN +: GRAN] = wr_data[b*GRAN +: GRAN];
        mknown[wr_addr][b] = 1'b1;
      end
    end
    // 3. next cycle's inputs; an idle read port gets garbage on the address
    wr_en   <= ($urandom_range(99) < pw);
    wr_addr <= pick_addr();
    wr_data <= rand_word() ;
    wr_be   <= (NB)'($urandom) | ((($urandom_range(3) == 0)) ? '0 : (($urandom_range(2) == 0) ? '1 : '0));
    rd_en   <= ($urandom_range(99) < pr);
    rd_addr <= pick_addr();
  end
endmodule

module tb_sram_1r1w;
  localparam int NC = 6;
  logic clk = 0;
  always #5 clk = ~clk;
  int pw = 50, pr = 50, hot = 0;
  int errs [NC], nrdw [NC], nrd [NC], npart [NC];

  sram_1r1w_chk #(32, 16, 8, 0) c0 (clk, pw, pr, hot, errs[0], nrdw[0], nrd[0], npart[0]);
  sram_1r1w_chk #(32, 13, 8, 1) c1 (clk, pw, pr, hot, errs[1], nrdw[1], nrd[1], npart[1]);   // DEPTH not a power of two
  sram_1r1w_chk #(16, 2, 16, 2) c2 (clk, pw, pr, hot, errs[2], nrdw[2], nrd[2], npart[2]);   // one lane
  sram_1r1w_chk #(64, 8, 8, 3)  c3 (clk, pw, pr, hot, errs[3], nrdw[3], nrd[3], npart[3]);   // wide, 8 lanes
  sram_1r1w_chk #(8, 1, 8, 4)   c4 (clk, pw, pr, hot, errs[4], nrdw[4], nrd[4], npart[4]);   // DEPTH 1
  sram_1r1w_chk #(24, 4, 4, 5)  c5 (clk, pw, pr, hot, errs[5], nrdw[5], nrd[5], npart[5]);   // 6 nibble lanes

  int total;
  initial begin
    for (int ph = 0; ph < 8; ph++) begin
      case (ph)
        0: begin pw = 50;  pr = 50;  hot = 0;   end
        1: begin pw = 100; pr = 0;   hot = 0;   end     // fill
        2: begin pw = 0;   pr = 100; hot = 0;   end     // read back
        3: begin pw = 100; pr = 100; hot = 80;  end     // same-address read and write most cycles
        4: begin pw = 80;  pr = 30;  hot = 100; end
        5: begin pw = 20;  pr = 100; hot = 100; end
        6: begin pw = 100; pr = 100; hot = 0;   end
        7: begin pw = 30;  pr = 30;  hot = 30;  end     // idle gaps: rd_data hold
      endcase
      repeat (2000) @(posedge clk);
    end
    total = 0;
    for (int i = 0; i < NC; i++) begin
      total += errs[i];
      if (nrd[i] < 2000 || nrdw[i] < 100 || (i != 2 && i != 4 && npart[i] < 100)) begin
        total++; $display("coverage hole in config %0d: reads %0d rdw %0d partial writes %0d", i, nrd[i], nrdw[i], npart[i]);
      end
    end
    if (total == 0) $display("PASS tb_sram_1r1w");
    else            $fatal(1, "FAIL tb_sram_1r1w: %0d errors", total);
    $finish;
  end
endmodule
