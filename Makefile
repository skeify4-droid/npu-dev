# Top-level flow. Usage: make <target> [BLOCK=...] [CFG=...]
# Heavy jobs run through scripts/memrun so peak RSS is logged to logs/.
SHELL := /bin/bash
ROOT  := $(abspath .)
RTL_DIR := rtl
BUILD := $(ROOT)/build
PY    ?= python3
ENV_SETUP ?= /Volumes/EVO970PLUS/claude/env_setup
ifneq ($(wildcard $(ENV_SETUP)/env.sh),)
  include $(ENV_SETUP)/config.sh
  export EDA_HOME
  export PATH := $(EDA_HOME)/venv/bin:$(PATH)
endif

COMMON_SV := $(wildcard $(RTL_DIR)/common/*.sv)
UNIT_TB   := $(wildcard tb/unit/tb_*.sv)

.PHONY: help lint unit formal e2e syn clean
help:
	@echo "targets: lint unit formal e2e syn clean"

# Lint every RTL file, -Wall, as its own top (block lint).
lint:
	@for f in $(COMMON_SV); do \
	  top=$$(basename $$f .sv); \
	  echo "== lint $$top"; \
	  verilator --lint-only -Wall --top-module $$top $$f || exit 1; \
	done

# Unit tests: one iverilog simulation per tb_*.sv, paired with the RTL it names.
unit: $(BUILD)
	@for tb in $(UNIT_TB); do \
	  name=$$(basename $$tb .sv); dut=$${name#tb_}; \
	  echo "== unit $$name"; \
	  iverilog -g2012 -o $(BUILD)/$$name $$tb $(RTL_DIR)/common/$$dut.sv && \
	  (cd $(BUILD) && vvp -n $$name) | tee $(BUILD)/$$name.out; \
	  grep -q "^PASS" $(BUILD)/$$name.out || { echo "unit FAILED: $$name"; exit 1; }; \
	done
	@echo "unit: ALL PASS"

formal:
	@echo "formal: no properties yet (tb/formal empty)"

e2e:
	@echo "e2e: needs compiler and TINY config (Phase 3+)"

# Synthesis of one block with Yosys (native). Usage: make syn BLOCK=trivial_add
syn: $(BUILD)
	@test -n "$(BLOCK)" || { echo "usage: make syn BLOCK=<module>"; exit 2; }
	scripts/memrun syn_$(BLOCK) yosys -q -p "read_verilog -sv $(RTL_DIR)/common/$(BLOCK).sv; synth -top $(BLOCK) -flatten; stat" 

$(BUILD):
	mkdir -p $(BUILD)

clean:
	rm -rf $(BUILD) logs/*.log logs/*.mem.log
