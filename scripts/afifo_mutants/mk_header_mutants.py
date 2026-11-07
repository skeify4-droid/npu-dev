"""Header-text and RTL mutants of rtl/common/afifo.sv for the cdc-sta harness: each one changes a constraint command or
sentence of the header (or the netlist structure the checks select) and must make `make cdc-sta` fail on the named check,
except n_allow_paths (must pass) and r_keep_msb (survives; not checked whether it is equivalent).
Usage: mk_header_mutants.py [outdir]   (default build/afifo_mutants/hdr; writes <outdir>/<name>/afifo.sv and cfgs.tsv with
name, configurations, expectation). Run them with run_header_mutants.sh. The patterns are exact: a header edit that moves
one of them makes this script stop with the pattern, which then needs the same edit."""
import os, sys
R = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
src = open(os.path.join(R, 'rtl/common/afifo.sv')).read()
def sub(s, a, b, cnt=1):
    n = s.count(a)
    if n != cnt: sys.exit(f'pattern {a!r} found {n} (want {cnt})')
    return s.replace(a, b)
G_MAX = '//       set_max_delay -ignore_clock_latency -from [get_clocks wr_clk] -to <wgray_r1 D pins> T_wr-U_g\n'
G_MIN = '//       set_min_delay -ignore_clock_latency -from [get_clocks wr_clk] -to <wgray_r1 D pins> 0\n'
ARST = '//       set_false_path -through <arst_n net>\n'
def add(cmd): return sub(src, ARST, ARST + '//       ' + cmd + '\n')
T, C = 'w8d8:top:8:8 blk_w8d8:blk:8:8 w8d8cts:cts:8:8', 'w8d8cts:cts:8:8'
M = {}
# --- session-1 harness mutants ---
M['h_no_icl'] = (sub(src, 'set_max_delay -ignore_clock_latency', 'set_max_delay', 4), T, 'fail: wr_rd/rd_wr max')
M['h_no_icl_all'] = (sub(sub(src, 'set_max_delay -ignore_clock_latency', 'set_max_delay', 4), 'set_min_delay -ignore_clock_latency', 'set_min_delay', 4), T, 'fail')
M['h_ph_endpoint'] = (sub(src, G_MAX, G_MAX.replace('<wgray_r1 D pins>', '<rgray_w1 D pins>')), T, 'fail')
M['h_ph_period'] = (sub(src, G_MAX, G_MAX.replace('T_wr-U_g', 'T_rd-U_g')), T, 'fail: val_gray_wr_max (survived in session 1)')
M['h_swap_gray'] = (sub(src, G_MAX + G_MIN, G_MAX.replace('T_wr-U_g\n', '0\n') + G_MIN.replace(' 0\n', ' T_wr-U_g\n')), T, 'fail')
s = sub(src, '//       set_output_delay -max <consumer share>', '//       set_output_delay -XX <consumer share>')
s = sub(s, '//       set_output_delay -min <consumer min>', '//       set_output_delay -max <consumer min>')
M['h_swap_od'] = (s.replace('-XX <consumer share>', '-min <consumer share>'), 'blk_w8d8:blk:8:8', 'fail: blk_rbin_min_required')
M['h_no_arst'] = (sub(src, ARST, '//       (deleted)\n'), 'w8d8:top:8:8', 'fail: extract')
M['h_inst_not_net'] = (sub(src, '//       set_dont_touch <synchronizer nets>\n', '//       set_dont_touch <synchronizer and Gray source flops>\n'), C, 'fail')
M['h_inst_fp_prose'] = (sub(src, 'only after CTS (POST_CTS_TCL; checked through global and detail route):', 'from floorplan on (PRE_FLOORPLAN_TCL, with the nets):'), C, 'fail: extract prose (survived in session 1)')
# --- new mutants ---
M['n_min10'] = (sub(src, G_MIN, G_MIN.replace(' 0\n', ' 10\n')), T, 'fail: val_gray_wr_min')
M['n_mem_period'] = (sub(src, '-to [get_clocks rd_clk] 2*T_rd-U_m', '-to [get_clocks rd_clk] T_rd-U_g'), T, 'fail: val_cons_max (top)')
M['n_arst_port'] = (sub(src, ARST, '//       set_false_path -from [get_ports wr_rst_n]\n'), 'w8d8:top:8:8 blk_w8d8:blk:8:8', 'fail: sync_recovery')
M['n_fp_rbin_rst'] = (add('set_false_path -to [PinsNamed $rbin RESETN]'), T, 'fail: post/text rst_rrst_n_rbin')
M['n_fp_wbin_rst'] = (add('set_false_path -to [PinsNamed $wbin RESETN]'), 'w8d8:top:8:8 blk_w8d8:blk:8:8', 'fail: rst_wrst_n_wbin (control)')
M['n_fp_rgray_w1_rst'] = (add('set_false_path -to [PinsNamed $rg_w1 RESETN]'), 'w8d8:top:8:8 blk_w8d8:blk:8:8', 'fail: rst_wrst_n_rgray_w1')
M['n_async_groups'] = (add('set_clock_groups -asynchronous -group [get_clocks wr_clk] -group [get_clocks rd_clk]'), T, 'fail')
M['n_allow_paths'] = (add('set_clock_groups -asynchronous -allow_paths -group [get_clocks wr_clk] -group [get_clocks rd_clk]'), 'w8d8:top:8:8 blk_w8d8:blk:8:8', 'pass (header: -allow_paths keeps both bounds)')
M['n_mem_rbin'] = (sub(src, '-from <mem regs> -to [get_clocks rd_clk] 2*T_rd-U_m', '-from [concat <mem regs> $rbin] -to [get_clocks rd_clk] 2*T_rd-U_m'), 'w8d8:top:8:8', 'fail: rbin_cons')
M['n_dt_noarst'] = (sub(src, '//       set_dont_touch <arst_n net>\n', ''), C, 'fail: post_dont_touch')
M['n_hold_unc'] = (add('set_clock_uncertainty -hold 50 [all_clocks]'), C, 'fail: post_s2_hold')
M['n_untouch_after_cts'] = (sub(src, '//       set_dont_touch <synchronizer and Gray source flops>\n', '//       set_dont_touch <synchronizer and Gray source flops>; unset_dont_touch <synchronizer nets>\n'), C, 'fail: post_dont_touch; buffers at grt/route?')
# --- RTL mutants ---
M['r_xing'] = (sub(src, 'assign rd_valid = rrst_n && (rgray != wgray_r2);', 'assign rd_valid = rrst_n && (rgray != wgray_r2) && !wbin[0];'), 'w8d8:top:8:8', 'fail: excl_none, text_wr_rd')
M['r_keep_msb'] = (sub(src, '  logic [AW:0] wbin, wgray;\n', '  logic [AW:0] wbin;\n  (* keep *) logic [AW:0] wgray;\n'), 'w8d8:top:8:8', 'fail: msb_merge / sets')
M['r_rst_1stage'] = (sub(src, "else         begin wr_rs0 <= 1'b1; wrst_n <= wr_rs0; end", "else         begin wr_rs0 <= 1'b1; wrst_n <= 1'b1; end"), 'w8d8:top:8:8', 'fail: sets')
M['r_sync_bypass'] = (sub(src, 'wgray_r2 <= wgray_r1;', 'wgray_r2 <= wgray;'), 'w8d8:top:8:8', 'fail: sets')
out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(R, 'build/afifo_mutants/hdr'))
os.makedirs(out, exist_ok=True); os.chdir(out)
with open('cfgs.tsv', 'w') as f:
    for k, (s, cfg, exp) in M.items():
        os.makedirs(k, exist_ok=True); open(f'{k}/afifo.sv', 'w').write(s)
        f.write(f'{k}\t{cfg}\t{exp}\n')
print('ok', len(M))
