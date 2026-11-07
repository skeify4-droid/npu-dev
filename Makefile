# Top-level flow. Usage: make <target> [BLOCK=...] [CFG=...]
# Heavy jobs run through scripts/memrun so peak RSS is logged to logs/.
SHELL := /bin/bash
ROOT  := $(abspath .)
RTL_DIR := rtl
BUILD := $(ROOT)/build
ENV_SETUP ?= /Volumes/EVO970PLUS/claude/env_setup
EDA_HOME  ?= /Volumes/EVO970PLUS/eda
PY    ?= $(if $(wildcard $(EDA_HOME)/venv/bin/python),$(EDA_HOME)/venv/bin/python,python3)
ifneq ($(wildcard $(ENV_SETUP)/env.sh),)
  include $(ENV_SETUP)/config.sh
  export EDA_HOME
  export PATH := $(EDA_HOME)/venv/bin:$(PATH)
endif
# SymbiYosys lives under EDA_HOME/tools (not on the default PATH).
export PATH := $(EDA_HOME)/tools/sby/bin:$(PATH)

COMMON_SV := $(wildcard $(RTL_DIR)/common/*.sv)
ARITH_SV  := $(sort $(wildcard $(RTL_DIR)/arith/*.sv))
# Parameter variants of the arith blocks that `make lint` checks besides the defaults: <top>:<-G options joined by ,>
ARITH_LINT_VAR := cvt_fp32_fp:-GFMT=1 cvt_fp32_fp:-GFMT=2 cvt_fp32_fp:-GFMT=3 cvt_fp32_fp:-GFMT=4 \
  cvt_dec:-GFMT=0,-GTO_BF16=1 cvt_dec:-GFMT=1 cvt_dec:-GFMT=1,-GTO_BF16=1 cvt_dec:-GFMT=3 cvt_dec:-GFMT=4,-GTO_BF16=1 \
  cvt_fp32_int:-GBITS=8,-GSIGNED=0 cvt_fp32_int:-GBITS=16 cvt_fp32_int:-GBITS=32 cvt_fp32_int:-GBITS=32,-GSIGNED=0 \
  mx_dequant_bf16:-GELEM=1 mx_dequant_bf16:-GSCALE=1 fold_key:-GELEM=1 fold_quant:-GELEM=1 fold_quant:-GSCALE=1 \
  fold_amax:-GN=5 fold_amax:-GN=64 fold_amax:-GN=1 mxu_pe:-GREG_DOT=0,-GREG_OUT=0 mxu_pe:-GREG_DOT=0 mxu_pe:-GREG_OUT=0
UNIT_TB   := $(wildcard tb/unit/tb_*.sv)

.PHONY: help lint unit arith-vec pytest models fetch-models sweep numerics hbm links isa formal e2e syn cdc-sta clean
help:
	@echo "targets: lint unit pytest models fetch-models sweep numerics hbm links isa formal e2e syn cdc-sta clean"

# Lint every RTL file, -Wall, as its own top (block lint).
lint:
	@for f in $(COMMON_SV); do \
	  top=$$(basename $$f .sv); \
	  echo "== lint $$top"; \
	  verilator --lint-only -Wall --top-module $$top $$f || exit 1; \
	done
	@for f in $(ARITH_SV); do \
	  top=$$(basename $$f .sv); \
	  echo "== lint $$top"; \
	  verilator --lint-only -Wall --top-module $$top $(ARITH_SV) || exit 1; \
	done
	@for v in $(ARITH_LINT_VAR); do \
	  top=$${v%%:*}; opts=$$(echo "$${v#*:}" | tr ',' ' '); \
	  echo "== lint $$top $$opts"; \
	  verilator --lint-only -Wall --top-module $$top $$opts $(ARITH_SV) || exit 1; \
	done

# Test vectors of the arith testbenches (exact reference sw/npumodel/arith_ref.py -> build/arith/*.hex, about 12 s).
arith-vec: $(BUILD)/arith/.stamp
$(BUILD)/arith/.stamp: scripts/gen_arith_vectors.py sw/npumodel/arith_ref.py sw/npumodel/numerics.py
	$(PY) scripts/gen_arith_vectors.py $(BUILD)/arith >/dev/null && touch $@

# Unit tests: one iverilog simulation per tb_*.sv, paired with the RTL it names (rtl/common/<dut>.sv, or rtl/arith/<dut>.sv
# compiled together with every arith file). Simulations run from build/ so the arith testbenches find arith/*.hex.
unit: $(BUILD) arith-vec
	@for tb in $(UNIT_TB); do \
	  name=$$(basename $$tb .sv); dut=$${name#tb_}; \
	  echo "== unit $$name"; \
	  if [ -f $(RTL_DIR)/common/$$dut.sv ]; then src=$(RTL_DIR)/common/$$dut.sv; \
	  elif [ -f $(RTL_DIR)/arith/$$dut.sv ]; then src="$(ARITH_SV)"; \
	  else echo "unit: no RTL for $$name"; exit 1; fi; \
	  iverilog -g2012 -o $(BUILD)/$$name $$tb $$src 2>$(BUILD)/$$name.cmp; rc=$$?; \
	  grep -v "sorry: constant selects" $(BUILD)/$$name.cmp; [ $$rc -eq 0 ] || { echo "unit FAILED (compile): $$name"; exit 1; }; \
	  (cd $(BUILD) && vvp -n $$name) 2>&1 | grep -v "Not enough words" | tee $(BUILD)/$$name.out; \
	  grep -q "^PASS" $(BUILD)/$$name.out || { echo "unit FAILED: $$name"; exit 1; }; \
	done
	@echo "unit: ALL PASS"

# Python tests (descriptor schema and ingestion). Needs the venv from env_setup.
pytest:
	$(PY) -m pytest -q tb/unit/test_descriptors.py tb/unit/test_analytic.py tb/unit/test_isa.py tb/unit/test_noc.py tb/unit/test_numerics.py \
	  tb/unit/test_arith.py tb/unit/test_hbm.py tb/unit/test_links.py

# Re-ingest config snapshots into models/descriptors and regenerate docs/reports/model_survey.md.
models:
	PYTHONPATH=sw $(PY) -m npumodel.ingest
	PYTHONPATH=sw $(PY) -m npumodel.report

# Phase 1 analytical sweeps -> docs/reports/phase1_sweeps.md, docs/spec/plots/*.svg, build/phase1/*.csv (seconds, <50 MB).
sweep:
	PYTHONPATH=sw:sim scripts/memrun sweep $(PY) -m analytic.sweep

# Phase 2 numerics experiments -> docs/reports/numerics_experiments.md (about 40 s, <400 MB).
numerics:
	PYTHONPATH=sw:sim scripts/memrun numerics $(PY) -m analytic.numerics_exp

# Phase 2 HBM controller model -> docs/reports/hbm_experiments.md (about 60 s, <50 MB).
hbm:
	PYTHONPATH=sw:sim scripts/memrun hbm $(PY) -m analytic.hbm --report

# Phase 2 link layer and collective schedules -> docs/reports/links_experiments.md (about 1 s).
links:
	PYTHONPATH=sw:sim scripts/memrun links $(PY) -m analytic.links

# ISA: validate isa/isa.yaml, regenerate isa/gen/{isa_pkg.sv,isa.h,isa_defs.py} and docs/spec/isa_ref.md, lint the SV package.
isa:
	$(PY) isa/isa.py
	verilator --lint-only -Wall isa/gen/isa_pkg.sv

# Re-download config.json snapshots from Hugging Face (network). Review the diff before committing.
fetch-models:
	scripts/fetch_configs.sh

# Formal: every tb/formal/*.sby (all of its tasks), SymbiYosys with yices. Work in build/formal/<name>.
FORMAL_SBY := $(wildcard tb/formal/*.sby)
formal: $(BUILD)
	@test -n "$(FORMAL_SBY)" || { echo "formal: no .sby files"; exit 0; }
	@for f in $(FORMAL_SBY); do \
	  name=$$(basename $$f .sby); \
	  echo "== formal $$name"; \
	  (cd tb/formal && $(ROOT)/scripts/memrun formal_$$name sby -f --prefix $(BUILD)/formal/$$name $$(basename $$f)) >/dev/null || { tail -30 logs/formal_$$name.log; echo "formal FAILED: $$name"; exit 1; }; \
	  echo "   $$(grep -c 'DONE (PASS' logs/formal_$$name.log) tasks PASS"; \
	done
	@echo "formal: ALL PASS"

e2e:
	@echo "e2e: needs compiler and TINY config (Phase 3+)"

# Synthesis of one block with Yosys (native). Usage: make syn BLOCK=trivial_add [PARAMS="WIDTH=32 DEPTH=8"]
# PARAMS overrides top-level parameters (chparam); the log is logs/syn_<BLOCK>.log (cell counts at the end).
# An rtl/arith block is read together with every arith file (the blocks instantiate each other).
syn: $(BUILD)
	@test -n "$(BLOCK)" || { echo "usage: make syn BLOCK=<module> [PARAMS=\"P=v ...\"]"; exit 2; }
	scripts/memrun syn_$(BLOCK) yosys -q -p "read_verilog -sv $(if $(wildcard $(RTL_DIR)/arith/$(BLOCK).sv),$(ARITH_SV),$(RTL_DIR)/common/$(BLOCK).sv); hierarchy -top $(BLOCK); $(foreach p,$(PARAMS),chparam -set $(word 1,$(subst =, ,$(p))) $(word 2,$(subst =, ,$(p))) $(BLOCK); )synth -top $(BLOCK) -flatten; tee -o $(BUILD)/syn_$(BLOCK).stat stat; stat" 

# CDC constraint check of the afifo header text (OrbStack VM: ORFS ASAP7 synthesis + OpenSTA, six configurations, and
# one through floorplan and CTS with three controls).
# Results in build/cdc_sta/summary.txt. Heavy: nothing else (Verilator, flat Yosys, formal) at the same time.
cdc-sta: $(BUILD)
	scripts/memrun cdc_sta scripts/cdc_sta/run.sh $(CDC_CFGS) || { tail -20 logs/cdc_sta.log; exit 1; }
	@grep -E "^cdc-sta:" logs/cdc_sta.log

$(BUILD):
	mkdir -p $(BUILD)

clean:
	rm -rf $(BUILD) logs/*.log logs/*.mem.log
