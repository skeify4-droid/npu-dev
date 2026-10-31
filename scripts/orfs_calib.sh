#!/usr/bin/env bash
# Usage: scripts/orfs_calib.sh N  -> ORFS Nangate45 RTL->route of pe_pr_N in the VM; logs wall + peak RSS (VM /usr/bin/time -v)
set -euo pipefail
N=$1; R=$(cd "$(dirname "$0")/.." && pwd); source /Volumes/EVO970PLUS/claude/env_setup/env.sh
W=$R/work/orfs_$N; rm -rf "$W"; mkdir -p "$W" "$R/logs"
python3 "$R/scripts/gen_pe_array.py" $N "$R/syn/gen/pe_array_${N}x${N}.sv"
python3 "$R/scripts/gen_pe_pr.py" $N "$R/syn/gen/pe_pr_$N.sv"
cd "$R"
eda-run "export NPU_ROOT=$R; /usr/bin/time -v -o $R/logs/orfs_$N.time make -C $FLOW_HOME YOSYS_EXE=$EDA_HOME/yosys-linux/bin/yosys OPENROAD_EXE=$EDA_HOME/openroad/bin/openroad DESIGN_CONFIG=$R/syn/orfs/pe_pr_$N.mk WORK_HOME=$W ${2:-}" > "$R/logs/orfs_$N.log" 2>&1 || echo "FAILED rc=$?"
grep -E "Elapsed|Maximum resident" "$R/logs/orfs_$N.time" || true
