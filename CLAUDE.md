# NPU project rules

Project: HBM3e LLM-inference NPU. RTL, verification, synthesis, and a performance simulator on macOS (16 GB).
Full plan: `docs/PLAN.md`. Environment: `/Volumes/EVO970PLUS/claude/env_setup` (source `env.sh`).

## Memory discipline (mandatory)
- Run one heavy job at a time. Never run the OrbStack VM (OpenROAD) together with a large Verilator build or flat Yosys synthesis.
- Wrap every heavy command with `scripts/memrun <logname> <cmd...>`. It logs peak RSS and wall time to `logs/`.
- Yosys: keep blocks hierarchical. Flat synthesis above about 0.9 M cells is out of budget (see `docs/reports/capacity.md`).
- Verilator: `-j 4`. Never build the `FULL` configuration. Trace selected scopes only.
- Performance simulator: stream traces to disk, keep RSS ≤ 4 GB.
- Work directories for OpenROAD must be on the SSD (`/Volumes/EVO970PLUS/...`), not `/tmp`.

## Tools
- Lint: `make lint`. Unit: `make unit`. Formal: `make formal`. Synthesis: `make syn BLOCK=<module>`.
- Simulator (Icarus) and Verilator run on macOS. Yosys and Icarus take `sv2v` output for complex SV.
- OpenROAD: `eda-run` / `orfs-make` (VM). Do not change the pinned OpenROAD/ORFS/Yosys commits.

## RTL conventions
- Synthesizable SV: packages, structs, enums, and parameters are allowed. No classes or interfaces in synthesizable code.
- Each block `rtl/<group>/<name>.sv` has a testbench `tb/unit/tb_<name>.sv` that prints `PASS <name>` on success.
- Packed ports for anything crossing a flattened boundary (Yosys reads packed vectors most reliably).
- Lint clean under `verilator --lint-only -Wall`.

## Generated files
- `syn/gen/` and `build/` are generated. Regenerate with the generator scripts; don't edit them by hand.
- The ISA source of truth will be `isa/isa.yaml` (Phase 2). Generators emit the SV, C++, and Python.

## Git
- Commit only when asked. Keep `logs/` and `build/` out of git.
