// acc_update: one lane of the ACC input (numerics.md N-3, step 4; microarch.md 2.2). Combinational; core/acc
// registers and pipelines it.
//   acc_r = rescale ? RNE(acc * rowscale) : acc          (RESCALE, once before the first K tile)
//   p     = use_cs  ? RNE(part * cs)      : part         (column scale of the K tile's partial sum)
//   y     = overwrite ? p : RNE(acc_r + p)               (OVERWRITE on the first tile, else accumulate)
// All FP32 with DAZ / FTZ. flags = OR of the flags of the three operations, except NAN_IN: the lane is one arithmetic
// unit (N-5), so NAN_IN is raised only for a NaN among its external operands that it uses (acc when rescaled or added,
// rowscale when rescaled, part when scaled or added, cs when use_cs), never for a NaN that its own multiplier made
// (Inf x 0 is INVALID and the add that consumes the NaN adds nothing).
module acc_update (
  input  logic [31:0] acc,
  input  logic [31:0] part,
  input  logic [31:0] rowscale,
  input  logic [31:0] cs,
  input  logic        rescale,
  input  logic        use_cs,
  input  logic        overwrite,
  output logic [31:0] y,
  output logic [3:0]  flags
);
  logic [31:0] acc_m, part_m, sum;
  logic [3:0]  f_rs, f_cs, f_add;

  fp32_mul u_rs  (.a(acc),  .b(rowscale), .y(acc_m),  .flags(f_rs));
  fp32_mul u_cs  (.a(part), .b(cs),       .y(part_m), .flags(f_cs));

  logic [31:0] acc_r, p;
  assign acc_r = rescale ? acc_m : acc;
  assign p     = use_cs  ? part_m : part;

  fp32_add u_add (.a(acc_r), .b(p), .y(sum), .flags(f_add));

  // NaN-ness of the add's operands that did not come from a multiplier of this lane (those are in f_rs / f_cs already)
  logic acc_nan, part_nan, nan_add;
  assign acc_nan  = (acc[30:23]  == 8'hFF) && (acc[22:0]  != 23'd0);
  assign part_nan = (part[30:23] == 8'hFF) && (part[22:0] != 23'd0);
  assign nan_add  = (!rescale && acc_nan) || (!use_cs && part_nan);

  assign y     = overwrite ? p : sum;
  assign flags = (rescale ? f_rs : 4'd0) | (use_cs ? f_cs : 4'd0) |
                 (overwrite ? 4'd0 : {f_add[3] & nan_add, f_add[2:0]});
endmodule
