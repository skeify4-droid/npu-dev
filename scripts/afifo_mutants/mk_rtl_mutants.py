"""RTL mutants of rtl/common/afifo.sv for the formal tasks and tb_afifo: wrong synchronizer depth, flags from the wrong
stage, wrong full mask, reset synchronizers of one stage or bypassed, and pointer synchronizers that stop after the first
full / drain (c7_*: write side, r_*: read side, *_rst: the shadow state is cleared by a reset input).
Usage: mk_rtl_mutants.py [outdir]   (default build/afifo_mutants/rtl; writes <outdir>/<name>/afifo.sv and orig/afifo.sv)
Run them with run_rtl_mutants.sh. A mutant that passes every sby task and tb_afifo is a gap in the properties."""
import sys, os
R = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
src = open(os.path.join(R, 'rtl/common/afifo.sv')).read()
FM = "localparam logic [AW:0] FULL_MASK = (AW + 1)'(3) << (AW - 1);"
PP = '  logic push, pop;'
WF = PP + "\n  logic m_full = 1'b0;\n  always_ff @(posedge wr_clk) if (wrst_n && !wr_ready) m_full <= 1'b1;\n  wire m_frz = m_full || (wrst_n && !wr_ready);"
WZ = PP + "\n  logic m_full = 1'b0;\n  always_ff @(posedge wr_clk) if (wrst_n && !wr_ready) m_full <= 1'b1;"
RF = PP + ("\n  logic m_seen = 1'b0, m_emp = 1'b0;\n  always_ff @(posedge rd_clk) begin if (rrst_n && rd_valid) m_seen <= 1'b1;"
           " if (rrst_n && m_seen && !rd_valid) m_emp <= 1'b1; end\n  wire m_frzr = m_emp || (rrst_n && m_seen && !rd_valid);")
RFR = PP + ("\n  logic m_seen, m_emp;\n  always_ff @(posedge rd_clk or negedge arst_n) if (!arst_n) begin m_seen <= 1'b0; m_emp <= 1'b0; end"
            " else begin if (rrst_n && rd_valid) m_seen <= 1'b1; if (rrst_n && m_seen && !rd_valid) m_emp <= 1'b1; end\n"
            "  wire m_frzr = m_emp || (rrst_n && m_seen && !rd_valid);")
M = {
 'sync_w_bypass':  [('wgray_r2 <= wgray_r1;', 'wgray_r2 <= wgray;')],
 'sync_r_bypass':  [('rgray_w2 <= rgray_w1;', 'rgray_w2 <= rgray;')],
 'sync_wr_bypass': [('wgray_r2 <= wgray_r1;', 'wgray_r2 <= wgray;'), ('rgray_w2 <= rgray_w1;', 'rgray_w2 <= rgray;')],
 'sync_w_flag_r1': [('assign rd_valid = rrst_n && (rgray != wgray_r2);', 'assign rd_valid = rrst_n && (rgray != wgray_r1);')],
 'sync_r_flag_w1': [('(wgray != (rgray_w2 ^ FULL_MASK))', '(wgray != (rgray_w1 ^ FULL_MASK))')],
 'mask_msb_only':  [(FM, "localparam logic [AW:0] FULL_MASK = (AW + 1)'(1) << AW;")],
 'mask_off_hi':    [(FM, "localparam logic [AW:0] FULL_MASK = (AW + 1)'(3) << AW;")],
 'mask_off_lo':    [(FM, "localparam logic [AW:0] FULL_MASK = ((AW + 1)'(3) << (AW - 1)) >> 1;")],
 'mask_none':      [(FM, "localparam logic [AW:0] FULL_MASK = '0;")],
 'rst_w_1stage':   [("else         begin wr_rs0 <= 1'b1; wrst_n <= wr_rs0; end", "else         begin wr_rs0 <= 1'b1; wrst_n <= 1'b1; end")],
 'rst_r_1stage':   [("else         begin rd_rs0 <= 1'b1; rrst_n <= rd_rs0; end", "else         begin rd_rs0 <= 1'b1; rrst_n <= 1'b1; end")],
 'rst_w_bypass':   [('always_ff @(posedge wr_clk or negedge wrst_n) begin\n    if (!wrst_n) begin', 'always_ff @(posedge wr_clk or negedge arst_n) begin\n    if (!arst_n) begin'),
                    ('assign wr_ready = wrst_n &&', 'assign wr_ready = arst_n &&')],
 'rst_r_bypass':   [('always_ff @(posedge rd_clk or negedge rrst_n) begin\n    if (!rrst_n) begin', 'always_ff @(posedge rd_clk or negedge arst_n) begin\n    if (!arst_n) begin'),
                    ('assign rd_valid = rrst_n &&', 'assign rd_valid = arst_n &&')],
 'rst_own_only':   [('assign arst_n = wr_rst_n & rd_rst_n;', 'assign arst_n = wr_rst_n & rd_rst_n;\n  wire m_w = wr_rst_n, m_r = rd_rst_n;'),
                    ('always_ff @(posedge wr_clk or negedge arst_n) begin\n    if (!arst_n) begin wr_rs0', 'always_ff @(posedge wr_clk or negedge m_w) begin\n    if (!m_w) begin wr_rs0'),
                    ('always_ff @(posedge rd_clk or negedge arst_n) begin\n    if (!arst_n) begin rd_rs0', 'always_ff @(posedge rd_clk or negedge m_r) begin\n    if (!m_r) begin rd_rs0')],
 'c7_w2_zero':     [(PP, WZ), ('rgray_w2 <= rgray_w1;', "rgray_w2 <= m_full ? '0 : rgray_w1;")],
 'c7_w1_zero':     [(PP, WZ), ('rgray_w1 <= rgray;', "rgray_w1 <= m_full ? '0 : rgray;")],
 'c7_w2_hold':     [(PP, WZ), ('rgray_w2 <= rgray_w1;', 'rgray_w2 <= m_full ? rgray_w2 : rgray_w1;')],
 'c7_w1_freeze':   [(PP, WF), ('rgray_w1 <= rgray;', 'rgray_w1 <= m_frz ? rgray_w1 : rgray;')],
 'c7_w2_freeze':   [(PP, WF), ('rgray_w2 <= rgray_w1;', 'rgray_w2 <= m_frz ? rgray_w2 : rgray_w1;')],
 'c7_w12_freeze':  [(PP, WF), ('rgray_w1 <= rgray;', 'rgray_w1 <= m_frz ? rgray_w1 : rgray;'), ('rgray_w2 <= rgray_w1;', 'rgray_w2 <= m_frz ? rgray_w2 : rgray_w1;')],
 # read-side mirror (new): the write pointer stops crossing after the FIFO first drains
 'r_r1_freeze':    [(PP, RF), ('wgray_r1 <= wgray;', 'wgray_r1 <= m_frzr ? wgray_r1 : wgray;')],
 'r_r2_freeze':    [(PP, RF), ('wgray_r2 <= wgray_r1;', 'wgray_r2 <= m_frzr ? wgray_r2 : wgray_r1;')],
 # reset-clearing variant: the shadow state is cleared by arst_n, so a reset input assertion un-freezes the crossing
 'r_r1_freeze_rst': [(PP, RFR), ('wgray_r1 <= wgray;', 'wgray_r1 <= m_frzr ? wgray_r1 : wgray;')],
 'r_r2_freeze_rst': [(PP, RFR), ('wgray_r2 <= wgray_r1;', 'wgray_r2 <= m_frzr ? wgray_r2 : wgray_r1;')],
 'r_r12_freeze':   [(PP, RF), ('wgray_r1 <= wgray;', 'wgray_r1 <= m_frzr ? wgray_r1 : wgray;'), ('wgray_r2 <= wgray_r1;', 'wgray_r2 <= m_frzr ? wgray_r2 : wgray_r1;')],
}
out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(R, 'build/afifo_mutants/rtl'))
os.makedirs(out, exist_ok=True); os.chdir(out)
os.makedirs('orig', exist_ok=True); open('orig/afifo.sv', 'w').write(src)
for name, reps in M.items():
    s = src
    for a, b in reps:
        n = s.count(a)
        if n != 1: sys.exit(f'{name}: pattern found {n} times: {a!r}')
        s = s.replace(a, b)
    os.makedirs(name, exist_ok=True); open(f'{name}/afifo.sv', 'w').write(s)
print('ok', len(M))
