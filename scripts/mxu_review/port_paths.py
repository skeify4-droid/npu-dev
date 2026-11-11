#!/usr/bin/env python3
"""Implementability review: combinational paths from the input ports to the output ports of `mxu` (and loops).

Yosys flattens mxu + mxu_dec + mxu_wpack to generic gates with mxu_array as a blackbox (every array output comes from a register
and every array input ends in one: y / flags / swap_tap / y_valid / y_meta are flops or ORs of flops, see mxu_array.sv), writes JSON,
and this script walks the netlist bit by bit, stopping at flip-flops. It prints, for each output port, the input ports it depends on
combinationally, and checks that no `*_req_v` depends on a `*_ready` (a ready-depends-on-valid SPM arbiter would close a loop).
Usage: scripts/memrun rev_ports python3 scripts/mxu_review/port_paths.py [DIM]
"""
import collections
import json
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
OUT = os.path.join(ROOT, 'build', 'mxu_review', 'syn')


def main():
    dim = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    os.makedirs(OUT, exist_ok=True)
    j = os.path.join(OUT, 'ports%d.json' % dim)
    script = ('read_verilog -sv isa/gen/isa_pkg.sv; read_verilog -sv -lib rtl/core/mxu_array.sv; '
              'read_verilog -sv rtl/core/mxu.sv rtl/core/mxu_dec.sv rtl/core/mxu_wpack.sv; chparam -set DIM %d mxu; '
              'hierarchy -check -auto-top; synth -flatten; write_json %s' % (dim, j))
    subprocess.run(['yosys', '-q', '-p', script], cwd=ROOT, check=True)
    d = json.load(open(j))
    top = [m for m, v in d['modules'].items() if v.get('attributes', {}).get('top')][0]
    m = d['modules'][top]
    ports = m['ports']
    bit_in = {}
    for name, p in ports.items():
        if p['direction'] == 'input':
            for b in p['bits']:
                if isinstance(b, int):
                    bit_in[b] = name
    # driver graph: output bit of a combinational cell -> its input bits
    fanin = collections.defaultdict(set)
    seq = 0
    for cname, c in m['cells'].items():
        t = c['type']
        if 'DFF' in t or 'DLATCH' in t or t.startswith('$_SR') or t.startswith('$_ALDFF') or not t.startswith('$'):
            seq += 1                                   # flip-flops and the blackbox array: cut
            continue
        dirs = c['port_directions']
        ins = [b for p, bs in c['connections'].items() if dirs[p] == 'input' for b in bs if isinstance(b, int)]
        for p, bs in c['connections'].items():
            if dirs[p] == 'output':
                for b in bs:
                    if isinstance(b, int):
                        fanin[b].update(ins)
    # netnames alias bits; Yosys JSON shares bit ids, so no alias handling is needed
    memo = {}

    def srcs(b, stack):
        if b in memo:
            return memo[b]
        if b in stack:
            raise RuntimeError('combinational loop through bit %d' % b)
        stack.add(b)
        s = set()
        if b in bit_in:
            s.add(bit_in[b])
        for a in fanin.get(b, ()):
            s |= srcs(a, stack)
        stack.discard(b)
        memo[b] = s
        return s

    sys.setrecursionlimit(1000000)
    res = {}
    for name, p in sorted(ports.items()):
        if p['direction'] != 'output':
            continue
        s = set()
        for b in p['bits']:
            if isinstance(b, int):
                s |= srcs(b, set())
        res[name] = sorted(s - {'clk'})
    print('DIM %d, %d cells cut as sequential / blackbox; no combinational loop' % (dim, seq))
    for name, s in res.items():
        print('  %-14s <- %s' % (name, ', '.join(s) if s else '(registers only)'))
    bad = [(o, i) for o, s in res.items() if o.endswith('_req_v') for i in s if i.endswith('_ready')]
    print('ready -> req_v paths:', bad if bad else 'none')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
