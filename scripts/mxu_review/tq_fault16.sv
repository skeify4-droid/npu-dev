// Review part T (test quality): run the author's mxu_fault_tc (tb/unit/tb_mxu.sv) on build/mxu/mxu_fault_16.hex, which
// gen_mxu_vectors.py writes and scripts/mxu_vectors.lock locks but no testbench instantiates. Compile with tb_mxu.sv, top tq_fault16.
module tq_fault16;
  logic d8, d16;
  int e8, e16;
  mxu_fault_tc #(.DIM(8),  .N(`CNT_MXU_FAULT_8),  .SPMB(`CNT_MXU_8_SPM),  .ACCR(`CNT_MXU_8_ACC0),  .FILE("mxu_fault_8.hex"))  t8  (.done(d8),  .errors(e8));
  mxu_fault_tc #(.DIM(16), .N(`CNT_MXU_FAULT_16), .SPMB(`CNT_MXU_16_SPM), .ACCR(`CNT_MXU_16_ACC0), .FILE("mxu_fault_16.hex")) t16 (.done(d16), .errors(e16));
  initial begin
    wait (d8 && d16);
    $display("TQ_FAULT errors DIM 8: %0d, DIM 16: %0d", e8, e16);
    $finish;
  end
endmodule
