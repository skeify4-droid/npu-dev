#!/usr/bin/env bash
# CDC constraint check for rtl/common/afifo.sv (make cdc-sta). Runs in the OrbStack VM: ORFS ASAP7 synthesis of each
# configuration, then OpenSTA checks of the constraint text taken literally from the afifo header (extract.py):
#   - excl_*: every wr_clk <-> rd_clk path ends at a wgray_r1 / rgray_w1 D pin, starts at a mem register, or runs
#     through arst_n (false-path the three, nothing may cross; leave one out, its category must come back);
#   - text: with the header commands as written, every crossing endpoint is in the path-delay group (max and min),
#     recovery / removal is gone at the reset synchronizers and kept at wrst_n -> wbin, rbin -> consumer is an
#     ordinary rd_clk path, and the Gray MSB is merged as the header says.
# Each check runs with sta_preset_clear_arcs_enabled 0 and 1. Results: build/cdc_sta/summary.txt.
# Not covered: post-CTS netlists, set_dont_touch, kept hierarchy (see docs/reports/status.md, Phase 3).
# Usage: scripts/cdc_sta/run.sh [name:kind:WIDTH:DEPTH ...]   (default: the six configurations below)
# Memory: the VM is the heavy job; do not run Verilator, Yosys-flat or formal at the same time.
set -euo pipefail
R=$(cd "$(dirname "$0")/../.." && pwd)
source /Volumes/EVO970PLUS/claude/env_setup/env.sh
CFGS=("$@")
[ ${#CFGS[@]} -gt 0 ] || CFGS=(w8d8:top:8:8 w8d2:top:8:2 w8d16:top:8:16 w1d8:top:1:8 w64d8:top:64:8 blk_w8d8:blk:8:8)
mkdir -p "$R/logs"
cd "$R"
rc=0
eda-run "/usr/bin/time -v -o $R/logs/cdc_sta.vm_time.log bash $R/scripts/cdc_sta/inner.sh $R $EDA_HOME ${CFGS[*]}" || rc=$?
grep -E "Elapsed|Maximum resident" "$R/logs/cdc_sta.vm_time.log" || true
exit $rc
