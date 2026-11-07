// Self-checking testbench for acc_update (iverilog -g2012): RESCALE / column scale / OVERWRITE / accumulate, bit-exact
// against sw/npumodel/arith_ref.py (vectors: scripts/gen_arith_vectors.py).
`timescale 1ns/1ps
module tb_acc_update;
  localparam int MAXN = 25000;
  logic [166:0] mem [0:MAXN-1];
  logic [31:0] acc, part, rs, cs, y;
  logic rescale, use_cs, overwrite;
  logic [3:0] flags;
  int errors = 0, n = 0;

  acc_update dut (.acc(acc), .part(part), .rowscale(rs), .cs(cs), .rescale(rescale), .use_cs(use_cs), .overwrite(overwrite),
                  .y(y), .flags(flags));

  initial begin
    $readmemh("arith/acc_update.hex", mem);
    for (int i = 0; i < MAXN; i++) begin
      if (^mem[i] === 1'bx) break;
      acc = mem[i][166:135]; part = mem[i][134:103]; rs = mem[i][102:71]; cs = mem[i][70:39];
      rescale = mem[i][38]; use_cs = mem[i][37]; overwrite = mem[i][36];
      #1;
      n++;
      if (y !== mem[i][35:4] || flags !== mem[i][3:0]) begin
        errors++;
        if (errors <= 10) $display("MISMATCH acc=%h part=%h rs=%h cs=%h ctl=%b%b%b y=%h f=%h exp y=%h f=%h", acc, part, rs, cs, rescale, use_cs, overwrite, y, flags, mem[i][35:4], mem[i][3:0]);
      end
    end
    if (n < 1000) begin errors++; $display("too few vectors: %0d", n); end
    if (errors == 0) $display("PASS acc_update (%0d vectors)", n);
    else $display("FAIL acc_update: %0d errors of %0d", errors, n);
    $finish;
  end
endmodule
