# ORFS config (Nangate45) for P&R memory calibration of pe_pr_8. Run via scripts/orfs_calib.sh 8
export DESIGN_NAME = pe_pr_8
export PLATFORM    = nangate45
export VERILOG_FILES = $(NPU_ROOT)/syn/gen/pe_array_8x8.sv $(NPU_ROOT)/syn/gen/pe_pr_8.sv
export SDC_FILE      = $(NPU_ROOT)/syn/orfs/pe_pr.sdc
export CORE_UTILIZATION ?= 45
export PLACE_DENSITY_LB_ADDON = 0.20
export SYNTH_REPEATABLE_BUILD ?= 1
export SYNTH_HIERARCHICAL = 0
