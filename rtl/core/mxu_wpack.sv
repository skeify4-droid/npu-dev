// mxu_wpack: one stationary-load beat -> the 128-word window of mxu_array (docs/spec/mxu.md 3, 4.3). Combinational.
//
// A beat is 8 SPM lanes of LB = DIM bytes (the w_rd response). `lane_v[l]` marks a requested lane (column < N for trans,
// k-row < K otherwise); everything else, and the K / N tails, becomes +0 words (arith.md contract item 3).
//
//   trans = 1  lane l = one column, bytes [g*LB, +LB) of its K row: word w of the lane (4 bytes, byte 0 lowest) goes to
//              window word l*16 + w; bytes >= `nbytes` of the lane are zero (K tail).
//   trans = 0, FP8   lane l = k-row 4h + q of PE-row pair member h, DIM bytes = columns 0..DIM-1: window word h*DIM + c has
//              byte q = byte c of lane 4h + q; columns >= `ncols` are zero.
//   trans = 0, BF16  lane 2*kk + half = k-row kk (0..3) of the beat, DIM/2 BF16 values of column half*DIM/2 + e:
//              window word h*DIM + c, h = kk / 2, q = kk % 2: halfword q = the BF16 of column c of k-row kk;
//              columns >= `ncols` are zero.
module mxu_wpack #(
  parameter int DIM = 8,
  localparam int LB  = DIM,
  localparam int GRP = DIM / 4
) (
  input  logic                 trans,
  input  logic                 bf16,
  input  logic [7:0]           lane_v,
  input  logic [7:0]           nbytes,     // trans: valid bytes of each lane (0..LB)
  input  logic [7:0]           ncols,      // non-trans: valid columns (0..DIM)
  input  logic [8*LB*8-1:0]    lanes,
  output logic [128*32-1:0]    win
);
  logic [7:0] byt [8][LB];
  for (genvar l = 0; l < 8; l++) begin : g_l
    for (genvar b = 0; b < LB; b++) begin : g_b
      assign byt[l][b] = lanes[(l*LB + b)*8 +: 8];
    end
  end

  always_comb begin
    win = '0;
    if (trans) begin
      for (int l = 0; l < 8; l++)
        for (int w = 0; w < GRP; w++)
          for (int q = 0; q < 4; q++)
            if (lane_v[l] && (4*w + q) < int'(nbytes)) win[(l*16 + w)*32 + 8*q +: 8] = byt[l][4*w + q];
    end else if (!bf16) begin
      for (int h = 0; h < 2; h++)
        for (int c = 0; c < DIM; c++)
          for (int q = 0; q < 4; q++)
            if (lane_v[4*h + q] && c < int'(ncols)) win[(h*DIM + c)*32 + 8*q +: 8] = byt[4*h + q][c];
    end else begin
      for (int h = 0; h < 2; h++)
        for (int c = 0; c < DIM; c++)
          for (int q = 0; q < 2; q++) begin
            if (lane_v[2*(2*h + q) + c/(DIM/2)] && c < int'(ncols)) begin
              win[(h*DIM + c)*32 + 16*q +: 8]     = byt[2*(2*h + q) + c/(DIM/2)][2*(c % (DIM/2))];
              win[(h*DIM + c)*32 + 16*q + 8 +: 8] = byt[2*(2*h + q) + c/(DIM/2)][2*(c % (DIM/2)) + 1];
            end
          end
    end
  end
endmodule
