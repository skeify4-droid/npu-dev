#!/usr/bin/env bash
# CDC constraint check for rtl/common/afifo.sv (make cdc-sta). Runs in the OrbStack VM: ORFS ASAP7 synthesis of each
# configuration, then OpenSTA checks of the constraint text taken literally from the afifo header (extract.py):
#   - excl_*: every wr_clk <-> rd_clk path ends at a wgray_r1 / rgray_w1 D pin, starts at a mem register, or runs
#     through arst_n (false-path the three, nothing may cross; leave one out, its category must come back);
#   - text: with the header commands as written, every crossing endpoint is in the path-delay group (max and min),
#     recovery / removal is gone at the reset synchronizers and kept at wrst_n -> wbin, rbin -> consumer is an
#     ordinary rd_clk path, and the Gray MSB is merged as the header says.
# Each check runs with sta_preset_clear_arcs_enabled 0 and 1. Results: build/cdc_sta/summary.txt.
# Every run first checks the sizes of the selections (sets_*: AW+1 Gray bits, four reset-synchronizer flops, 2 nets per
# synchronizer pair, ...) so that no check passes over an empty set, and the values the report prints (val_*: the max
# delays 667-U_g, 833-U_g, 2*833-U_m, the min delays 0, library setup / hold or the block-level output delay).
# cts*: one configuration through ORFS floorplan and CTS (hold margin 40 ps) with the header text as the flow's SDC and
#   its set_dont_touch commands in PRE_FLOORPLAN_TCL / POST_CTS_TCL (gen.py): the header's checks again on 4_cts.odb /
#   4_cts.sdc, no repair_timing run on the crossing endpoints (two "RSZ-0098 No setup violations" in the logs), no
#   buffers on the synchronizer nets, dont_touch flags in the database, and hold slack >= 0 at the D pins of the
#   synchronizer second stages (post_s2_hold), timed at the flow's own corner (BC / FF libraries from the platform config)
#   with placement parasitics. The cts configuration runs on through global and detail route and the same checks run on
#   5_1_grt and 5_route. Controls: ctsc (SDC with the two clocks only: floorplan / CTS repair the 12 false violations),
#   ctsn (no dont_touch: hold buffers on the synchronizer nets), ctsi (instance dont_touch from floorplan: CTS-0137).
# Not covered: kept hierarchy, steps after route (see docs/reports/status.md, Phase 3).
# The scripts are copied into the work directory first; CDC_SRC / CDC_B run the checks on another afifo.sv in another work
# directory (mutation runs of the checks themselves).
# Usage: scripts/cdc_sta/run.sh [name:kind:WIDTH:DEPTH ...]   (default: the configurations below)
# Memory: the VM is the heavy job; do not run Verilator, Yosys-flat or formal at the same time.
set -euo pipefail
R=$(cd "$(dirname "$0")/../.." && pwd)
source /Volumes/EVO970PLUS/claude/env_setup/env.sh
CFGS=("$@")
[ ${#CFGS[@]} -gt 0 ] || CFGS=(w8d8:top:8:8 w8d2:top:8:2 w8d16:top:8:16 w1d8:top:1:8 w64d8:top:64:8 blk_w8d8:blk:8:8
      w8d8cts:cts:8:8 w8d8ctsc:ctsc:8:8 w8d8ctsn:ctsn:8:8 w8d8ctsi:ctsi:8:8)
mkdir -p "$R/logs"
cd "$R"
rc=0
# CDC_SRC / CDC_B: another afifo.sv and work directory (mutation runs of the checks, see inner.sh); eda-run does not pass the
# environment into the VM, so they go on the command line.
ENVP=""; for v in CDC_SRC CDC_B; do [ -z "${!v:-}" ] || ENVP="$ENVP $v=${!v}"; done
eda-run "$ENVP /usr/bin/time -v -o $R/logs/cdc_sta.vm_time.log bash $R/scripts/cdc_sta/inner.sh $R $EDA_HOME ${CFGS[*]}" || rc=$?
grep -E "Elapsed|Maximum resident" "$R/logs/cdc_sta.vm_time.log" || true
exit $rc
