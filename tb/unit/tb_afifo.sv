// Self-checking testbench for afifo (iverilog -g2012).
// Several clock ratios run side by side, each with its own DUT, queue reference model, random valid/ready stalls,
// probability phases (fill, drain, saturate) and resets in the middle. Checks: ordering, no loss, no duplication,
// no overflow (wr_ready never with the model full), no phantom data (rd_valid only with data in flight), and a final
// drain that must empty the model.
`timescale 1ps/1ps

module afifo_chk #(
  parameter int WIDTH = 16,
  parameter int DEPTH = 8,
  parameter int WH    = 500,        // write clock half period (ps)
  parameter int RH    = 500,        // read clock half period (ps)
  parameter int WOFF  = 0,          // write clock start offset (ps)
  parameter int ID    = 0
) (
  input  logic run,
  output int   errors,
  output int   n_push, n_pop, n_full, n_valid_idle,
  output logic done
);
  logic wr_clk = 0, rd_clk = 0, wr_rst_n = 0, rd_rst_n = 0;
  logic wr_valid = 0, wr_ready, rd_valid, rd_ready = 0;
  logic [WIDTH-1:0] wr_data = 0, rd_data;

  afifo #(.WIDTH(WIDTH), .DEPTH(DEPTH)) dut (.*);

  initial begin #(WOFF); forever #(WH) wr_clk = ~wr_clk; end
  initial begin forever #(RH) rd_clk = ~rd_clk; end

  int pin = 0, pout = 0;
  logic [WIDTH-1:0] q [$];
  logic [WIDTH-1:0] ref_next = 0;
  logic [WIDTH-1:0] exp;
  logic push, pop;

  initial begin errors = 0; n_push = 0; n_pop = 0; n_full = 0; n_valid_idle = 0; done = 0; end

  // write side: monitor and driver
  always @(posedge wr_clk) begin
    if (wr_rst_n === 1'b1) begin
      if (wr_ready && q.size() >= DEPTH) begin errors++; $display("[%0d] overflow: wr_ready with %0d in flight", ID, q.size()); end
      if (!wr_ready) n_full++;
      push = wr_valid && wr_ready;
      if (push) begin q.push_back(wr_data); n_push++; end
      if (!wr_valid || wr_ready) begin
        if ($urandom_range(99) < pin) begin wr_valid <= 1; wr_data <= ref_next; ref_next <= ref_next + 1'b1; end
        else wr_valid <= 0;
      end
    end else begin
      wr_valid <= 0;
    end
  end

  // read side: monitor and driver
  always @(posedge rd_clk) begin
    if (rd_rst_n === 1'b1) begin
      pop = rd_valid && rd_ready;
      if (rd_valid && q.size() == 0) begin errors++; $display("[%0d] phantom word 0x%h", ID, rd_data); end
      if (pop && q.size() > 0) begin
        exp = q[0];
        if (rd_data !== exp) begin errors++; $display("[%0d] data 0x%h != expected 0x%h", ID, rd_data, exp); end
        void'(q.pop_front());
        n_pop++;
      end
      if (!rd_valid) n_valid_idle++;
      rd_ready <= ($urandom_range(99) < pout);
    end else begin
      rd_ready <= 0;
    end
  end

  // both resets asserted asynchronously, released at independent clock edges
  task automatic do_reset(input int wcycles, input int rcycles);
    #(37);
    wr_rst_n = 0; rd_rst_n = 0;
    q.delete();
    repeat (wcycles) @(posedge wr_clk);
    #1 wr_rst_n = 1;
    if (rcycles > wcycles) begin
      repeat (rcycles - wcycles) @(posedge rd_clk);
    end
    #1 rd_rst_n = 1;
  endtask

  task automatic phase(input int pi, input int po, input int quanta);   // one quantum is 250 ns
    pin = pi; pout = po;
    #(quanta * 250000);
  endtask

  initial begin
    wait (run);
    do_reset(4, 6);
    for (int r = 0; r < 4; r++) begin
      phase(50, 50, 8);
      phase(100, 0, 4);      // fill until full, back to back
      phase(0, 100, 4);      // drain until empty
      phase(100, 100, 4);    // saturate both sides
      phase(100, 30, 4);
      phase(20, 100, 4);
      phase(70, 70, 4);
      phase(100, 10, 3);     // mostly full
      if (r % 2 == 0) do_reset(5 + r, 3 + 2 * r);      // reset while full, different release orders
      else begin phase(100, 100, 1); do_reset(3, 8 + r); end
      phase(60, 60, 3);
    end
    // final drain: stop writing, read at full rate, everything in flight must come out
    phase(0, 100, 20);
    if (q.size() != 0) begin errors++; $display("[%0d] %0d words lost in flight at the end", ID, q.size()); end
    if (n_push < 500 || n_pop < 500) begin errors++; $display("[%0d] too little traffic: push %0d pop %0d", ID, n_push, n_pop); end
    if (n_full == 0) begin errors++; $display("[%0d] never full", ID); end
    if (n_valid_idle == 0) begin errors++; $display("[%0d] never empty", ID); end
    done = 1;
  end
endmodule

module tb_afifo;
  localparam int N = 9;
  logic run = 0;
  int   errs [N], npush [N], npop [N], nfull [N], nidle [N];
  logic dn [N];

  //                 W   D   WH   RH  WOFF ID
  afifo_chk #(16, 8,  667, 833,   0, 0) c0 (run, errs[0], npush[0], npop[0], nfull[0], nidle[0], dn[0]);  // 1.5 : 1.2 (wr faster)
  afifo_chk #(16, 8,  500, 500, 123, 1) c1 (run, errs[1], npush[1], npop[1], nfull[1], nidle[1], dn[1]);  // 1 : 1, phase offset
  afifo_chk #(16, 8,  500, 500,   0, 2) c2 (run, errs[2], npush[2], npop[2], nfull[2], nidle[2], dn[2]);  // 1 : 1, aligned edges
  afifo_chk #(16, 8, 2500, 500,  41, 3) c3 (run, errs[3], npush[3], npop[3], nfull[3], nidle[3], dn[3]);  // slow writer (5x)
  afifo_chk #(16, 8,  500, 2500, 41, 4) c4 (run, errs[4], npush[4], npop[4], nfull[4], nidle[4], dn[4]);  // slow reader (5x)
  afifo_chk #(32, 8,  997, 1013, 5, 5) c5 (run, errs[5], npush[5], npop[5], nfull[5], nidle[5], dn[5]);  // near 1:1, drifting phase
  afifo_chk #(1,  2,  700, 500,  17, 6) c6 (run, errs[6], npush[6], npop[6], nfull[6], nidle[6], dn[6]); // DEPTH 2, 1 bit
  afifo_chk #(8,  16, 500, 523,  77, 7) c7 (run, errs[7], npush[7], npop[7], nfull[7], nidle[7], dn[7]); // DEPTH 16
  afifo_chk #(24, 4,  833, 667,   9, 8) c8 (run, errs[8], npush[8], npop[8], nfull[8], nidle[8], dn[8]); // DEPTH 4, 1.2 : 1.5 (wr slower)

  int total;
  initial begin
    #100 run = 1;
    wait (dn[0] && dn[1] && dn[2] && dn[3] && dn[4] && dn[5] && dn[6] && dn[7] && dn[8]);
    total = 0;
    for (int i = 0; i < N; i++) total += errs[i];
    if (total == 0) $display("PASS tb_afifo");
    else            $fatal(1, "FAIL tb_afifo: %0d errors", total);
    $finish;
  end
endmodule
