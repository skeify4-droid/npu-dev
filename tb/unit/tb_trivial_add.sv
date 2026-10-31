// Self-checking testbench for trivial_add (iverilog -g2012).
module tb_trivial_add;
  localparam int W = 8;
  logic clk = 0, rst_n = 0;
  logic [W-1:0] a, b;
  logic [W:0] sum;
  int errors = 0;
  trivial_add #(.W(W)) dut (.clk(clk), .rst_n(rst_n), .a(a), .b(b), .sum(sum));
  always #5 clk = ~clk;
  initial begin
    a = 0; b = 0;
    repeat (2) @(posedge clk);
    rst_n = 1;
    for (int i = 0; i < 512; i++) begin
      a = $urandom; b = $urandom;
      @(posedge clk); #1;
      if (sum !== ({1'b0, a} + {1'b0, b})) begin
        errors++;
        $display("MISMATCH a=%0d b=%0d sum=%0d", a, b, sum);
      end
    end
    if (errors == 0) $display("PASS tb_trivial_add");
    else             $fatal(1, "FAIL tb_trivial_add: %0d errors", errors);
    $finish;
  end
endmodule
