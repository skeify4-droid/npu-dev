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
- Lint: `make lint`. Unit: `make unit`. Python tests: `make pytest`. Model descriptors: `make models` (`make fetch-models` needs network). Formal: `make formal`. Synthesis: `make syn BLOCK=<module>`.
- Analytical model / sweeps: `make sweep` (`sw/npumodel/opgraph.py`, `sim/analytic/`). Frozen architecture numbers: `docs/spec/arch.md`.
- Phase 2 specs: `docs/spec/microarch.md` (blocks, interface tables, configs), `isa.md`, `memmap.md`, `noc.md` (deadlock rules; `sim/analytic/noc.py` checks them), `numerics.md` (formats, rounding, accumulation order; golden model `sw/npumodel/numerics.py`), `hbm.md` (controller, timing table, DRAM model; `sim/analytic/hbm.py`), `d2d_c2c.md` (link layer, collective schedules; `sim/analytic/links.py`). Review record: `docs/reports/spec_review.md`. ISA: `make isa`. Experiments: `make numerics`, `make hbm`, `make links`.
- Arithmetic blocks (`rtl/arith`, spec `docs/spec/arith.md`): `make unit` compiles each `tb_<block>` with every `rtl/arith` file (compiled inside `build/arith`, so `counts.svh` is the locked one) and reads vectors from `build/arith/*.hex`, which `make arith-vec` regenerates from the exact reference `sw/npumodel/arith_ref.py` (`scripts/gen_arith_vectors.py`; `tb/unit/test_arith.py` ties the reference to `numerics.py` and `ml_dtypes`). The generator also writes `build/arith/counts.svh` (rows per file); every testbench requires exactly that many rows (never zero) and `make unit` fails on a missing, short or long vector file. `scripts/arith_vectors.lock` (committed) holds the row count and SHA-256 of every file of `build/arith`, `counts.svh` included; `make arith-vec` (so `make unit`) and `run_arith_mutants.py` fail when `build/arith` differs from it (`scripts/arith_lock.py verify`). After an intended change of the generator, `arith_ref.py`, the hand cases or NumPy, run `scripts/gen_arith_vectors.py --lock` and commit the lock: its diff is the change of the vectors. Hand-derived cases live in `scripts/arith_hand_cases.py` (data only; `test_arith.py` asserts them against `arith_ref`). After a change to `fp_round`, `fp_addu` or a guard-bit parameter, run `scripts/memrun arith_mutants python3 scripts/arith_mutants/run_arith_mutants.py` (39 one-change copies of the RTL, about 5 min; every mutant must behave as its expectation says, four are equivalent and must pass). The independent reviews' oracle and 79 mutants (44 + 35) are in `scripts/arith_review/` (README there); keep `indep.py` free of any import of `arith_ref.py`.
- MXU (`rtl/core/mxu*.sv`, spec `docs/spec/mxu.md`): `make unit` compiles `tb_mxu_array` / `tb_mxu` with `isa/gen/isa_pkg.sv`, every `rtl/core` and `rtl/arith` file, inside `build/mxu`; vectors come from `make mxu-vec` (`scripts/gen_mxu_vectors.py`, expected values from `arith_ref` on the logical A x W). `tb_mxu` has `-DQUICK` (8 x 8 only), `-DONLY16`, `-DONLY32` for debugging. `gen_mxu_vectors.py` computes every `tb_mxu` case twice (`arith_ref` chain + `acc_update`, and `sw/npumodel/numerics.mxu_mm(..., dim=DIM)`; `cs` is `[K tiles, N]` per 256 FP8 / 128 BF16 fold tile, `cs[t // (64/DIM)]` per pass) and fails if they differ. `scripts/mxu_vectors.lock` (committed; `scripts/mxu_lock.py`) holds the row count and SHA-256 of every `build/mxu` file; `make mxu-vec` (so `make unit`) fails when `build/mxu` differs from it. After an intended change of the generator, `arith_ref.py`, `numerics.py` or NumPy, run `scripts/gen_mxu_vectors.py --lock` and commit the lock. The SPM lane of the MXU ports is `DIM` bytes. Formal: `tb/formal/mxu.sby` (properties in the `ifdef FORMAL` block of `rtl/core/mxu.sv`, stubs in `tb/formal/mxu_stubs.sv`, `mxu_dec_stub.sv`; about 15 min, run it after any change of the load / stream / exit control, and keep the cover task). `tb/formal/mxu_dec.sby` proves the `M = 0` path of the real `mxu_dec` (about 90 s; its properties are the `ifdef FORMAL` block of `mxu_dec.sv`; run it after any change of `mxu_dec.sv`).
- MXU mutants (`scripts/mxu_mutants/mxu_mutants.py`, `list` shows them): one-change copies of `rtl/core` in `build/mxu_mutants/`. `tb` mode runs `tb_mxu` on each (needs `build/mxu` from `make mxu-vec`; `--quick` for DIM 8 only), `formal` mode runs `tb/formal/mxu.sby` task `bmc_a` on the control mutants (about 3 to 6 min each) and `tb/formal/mxu_dec.sby` task `bmc` on the 7 decoder mutants (`formal_dec` / `equiv_dec`, about 90 s each). Run under `scripts/memrun`, one job at a time, after any change to `rtl/core/mxu*.sv`, `tb_mxu` or `gen_mxu_vectors.py`; every mutant must behave as its expectation says (the script exits non-zero otherwise).
- Simulator (Icarus) and Verilator run on macOS. Yosys and Icarus take `sv2v` output for complex SV.
- OpenROAD: `eda-run` / `orfs-make` (VM). Do not change the pinned OpenROAD/ORFS/Yosys commits.
- afifo CDC constraints: `make cdc-sta` (VM: ORFS ASAP7 synthesis of six configurations, then OpenSTA checks of the
  header text taken literally, plus one configuration through floorplan, CTS and global/detail route with three
  controls; about 2.5 min, 1.3 GB in the VM; `scripts/cdc_sta/`, results in `build/cdc_sta/summary.txt`). The checks
  guard their own selections (set sizes) and values (max/min delay bounds), and time the post-flow results at the flow's
  corner with placement parasitics. Run it after any change to the afifo RTL or its constraint text. `CDC_SRC` / `CDC_B`
  point `scripts/cdc_sta/run.sh` at another afifo.sv and work directory (mutation runs).
- afifo mutants (`scripts/afifo_mutants/`, outputs in `build/afifo_mutants/`): `mk_rtl_mutants.py` + `run_rtl_mutants.sh` (25 RTL
  mutants against the eight afifo sby tasks and `tb_afifo`, a few minutes, no VM) and `mk_header_mutants.py` +
  `run_header_mutants.sh` (25 header / netlist mutants against `make cdc-sta`, about 18 minutes in the VM). Run them under
  `scripts/memrun` and one at a time, after any change to the afifo header, RTL, FORMAL block, `afifo.sby`, `tb_afifo` or
  `scripts/cdc_sta/`. Every mutant must fail except `n_allow_paths` (passes) and `r_keep_msb` (survives, see status.md).

## RTL conventions
- Synthesizable SV: packages, structs, enums, and parameters are allowed. No classes or interfaces in synthesizable code.
- Each block `rtl/<group>/<name>.sv` has a testbench `tb/unit/tb_<name>.sv` that prints `PASS <name>` on success.
- Packed ports for anything crossing a flattened boundary (Yosys reads packed vectors most reliably).
- Lint clean under `verilator --lint-only -Wall`.

## Generated files
- `syn/gen/` and `build/` are generated. Regenerate with the generator scripts; don't edit them by hand.
- `build/arith/*.hex` (test vectors) comes from `make arith-vec`.
- `docs/reports/phase1_sweeps.md` and `docs/spec/plots/*.svg` are generated by `make sweep`; `docs/reports/numerics_experiments.md` by `make numerics`; `docs/reports/hbm_experiments.md` by `make hbm`; `docs/reports/links_experiments.md` by `make links`; `models/weight_bytes.tsv` by `scripts/measure_weight_bytes.py` (network).
- The ISA source of truth is `isa/isa.yaml` (instructions, enums, tile descriptor, memory map). `make isa` (`isa/isa.py`) generates `isa/gen/isa_pkg.sv`, `isa/gen/isa.h`, `isa/gen/isa_defs.py` and `docs/spec/isa_ref.md`; never edit those by hand (a test fails if they are stale).

## Git
- Commit only when asked. Keep `logs/` and `build/` out of git.

## Models
- `models/configs/` are HF snapshots, `models/descriptors/` and `docs/reports/model_survey.md` are generated (`make models`). Don't edit them by hand; change `sw/npumodel/ingest.py`.
- Schema: `docs/spec/model_descriptor.md`. What was verified beyond config.json: `docs/reports/model_features.md`. Hyper-connection / gather requirements: `docs/spec/hyper_connections.md`. A new model needs an adapter in `ingest.py` plus a row in `models/repos.tsv`.
