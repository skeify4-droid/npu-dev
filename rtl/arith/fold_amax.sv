// fold_amax: maximum of N fold keys (a balanced tree of compares, ceil(log2 N) levels). Keys packed, key i at
// [KW*i +: KW]. N need not be a power of two (the tree is padded with zero keys).
module fold_amax #(
  parameter int N  = 256,
  parameter int KW = 17
) (
  input  logic [N*KW-1:0] keys,
  output logic [KW-1:0]   amax
);
  localparam int L  = (N > 1) ? $clog2(N) : 0;
  localparam int NP = 1 << L;

  for (genvar s = 0; s <= L; s++) begin : g_lvl
    logic [(NP >> s)*KW-1:0] v;
    if (s == 0) begin : g_in
      if (NP > N) begin : g_pad
        assign v = {{((NP - N)*KW){1'b0}}, keys};
      end else begin : g_nopad
        assign v = keys;
      end
    end else begin : g_up
      for (genvar i = 0; i < (NP >> s); i++) begin : g_n
        logic [KW-1:0] lo, hi;
        assign lo = g_lvl[s-1].v[(2*i)*KW +: KW];
        assign hi = g_lvl[s-1].v[(2*i+1)*KW +: KW];
        assign v[i*KW +: KW] = (lo >= hi) ? lo : hi;
      end
    end
  end

  assign amax = g_lvl[L].v;
endmodule
