#!/usr/bin/env python3
"""Extract the STA commands from the rtl/common/afifo.sv header literally and write them as Tcl.

Usage: extract.py <afifo.sv> <outdir> [--twr 667] [--trd 833] [--ug 25] [--um 25] [--share 300] [--cmin -10]

Every header line of the form "//       set_<cmd> ..." is a constraint command. Placeholders are replaced by Tcl
variables that sel.tcl sets ($WR1D, $RW1D: the D pins of the wgray_r1 / rgray_w1 flops; $mem: the mem registers;
$ARST: the arst_n net; $SYNCN: the synchronizer-internal nets; $SYNCFF: the synchronizer and Gray source flops) and the
periods by numbers. Output:
  cons_top.tcl  the timing commands plus the top-level mem form (afifo inside a design)
  cons_blk.tcl  the timing commands plus the block-level mem form (afifo as top)
  cons_dt_net.tcl, cons_dt_inst.tcl  the set_dont_touch commands on nets (floorplan on) and on instances (after CTS)
Fails if a placeholder is left over or a form is missing, so a change to the header's form shows up here.
"""
import argparse
import re
import sys

ap = argparse.ArgumentParser()
ap.add_argument('src')
ap.add_argument('outdir')
ap.add_argument('--twr', type=int, default=667)
ap.add_argument('--trd', type=int, default=833)
ap.add_argument('--ug', type=int, default=25)
ap.add_argument('--um', type=int, default=25)
ap.add_argument('--share', type=int, default=300)
ap.add_argument('--cmin', type=int, default=-10)
a = ap.parse_args()

cmds = [re.sub(r'^//\s+', '', l).rstrip() for l in open(a.src) if re.match(r'^//       set_[a-z_]+ ', l)]
sub = [('<wgray_r1 D pins>', '$WR1D'), ('<rgray_w1 D pins>', '$RW1D'), ('<mem regs>', '$mem'),
       ('<arst_n net>', '$ARST'), ('<synchronizer nets>', '$SYNCN'), ('<synchronizer and Gray source flops>', '$SYNCFF'),
       ('<consumer share>', str(a.share)), ('<consumer min>', str(a.cmin)),
       ('2*T_rd-U_m', str(2 * a.trd - a.um)), ('T_wr-U_g', str(a.twr - a.ug)), ('T_rd-U_g', str(a.trd - a.ug))]
out = []
for c in cmds:
    for x, y in sub:
        c = c.replace(x, y)
    if '<' in c or 'T_' in c:
        sys.exit(f'extract: unresolved placeholder in: {c}')
    out.append(c)
dt = [c for c in out if c.startswith('set_dont_touch ')]
out = [c for c in out if c not in dt]
blk = [c for c in out if 'rd_data' in c]
top = [c for c in out if '-to [get_clocks rd_clk]' in c and '$mem' in c]
com = [c for c in out if c not in blk and c not in top]
dtn = [c for c in dt if '$SYNCFF' not in c]
dti = [c for c in dt if '$SYNCFF' in c]
for name, l in (('common', com), ('top-level mem', top), ('block-level mem', blk), ('dont_touch net', dtn),
                ('dont_touch instance', dti)):
    if not l:
        sys.exit(f'extract: no {name} commands found in {a.src}')
if not any('$WR1D' in c for c in com) or not any('$ARST' in c for c in com):
    sys.exit('extract: Gray endpoint or arst_n command missing')
open(f'{a.outdir}/cons_top.tcl', 'w').write('\n'.join(com + top) + '\n')
open(f'{a.outdir}/cons_blk.tcl', 'w').write('\n'.join(com + blk) + '\n')
open(f'{a.outdir}/cons_dt_net.tcl', 'w').write('\n'.join(dtn) + '\n')
open(f'{a.outdir}/cons_dt_inst.tcl', 'w').write('\n'.join(dti) + '\n')
print(f'extract: {len(cmds)} commands (common {len(com)}, top {len(top)}, block {len(blk)}, dont_touch {len(dtn)} net '
      f'+ {len(dti)} instance); T_wr {a.twr} T_rd {a.trd} U_g {a.ug} U_m {a.um} share {a.share} cmin {a.cmin}')
