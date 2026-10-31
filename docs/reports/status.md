
## Phase 0 (repo skeleton, toolchain, capacity calibration)

### Changes
- Repo skeleton, `.gitignore`, `CLAUDE.md` (memory rules, conventions), `Makefile` (`lint`, `unit`, `syn BLOCK=`; `formal`/`e2e` are placeholders).
- `scripts/memrun`: wraps a command with `/usr/bin/time -l`, logs peak RSS and wall time to `logs/`.
- Trivial block `rtl/common/trivial_add.sv` with `tb/unit/tb_trivial_add.sv`.
- Calibration tooling: `scripts/gen_pe_array.py`, `gen_pe_pr.py`, `verilator_calib.sh`, `orfs_calib.sh`, `tb/calib/pe_main.cpp`, `syn/orfs/pe_pr_*.mk`.
- Venv extras: numpy, ml_dtypes, pyyaml, pandas, plotly, zstandard.
- `docs/PLAN.md`: measured caps (flat synthesis about 0.9 M cells; P&R block 16×16, about 230 k cells).

### Test results
- `make lint`: clean (Verilator 5.052, `-Wall`). `make unit`: `PASS tb_trivial_add`.
- Yosys flat: 32×32 array = 918 k cells, 5.0 GB, 88 s.
- Verilator: builds under 2 s at every size, peak RSS flat at 0.23 GB (may undercount).
- ORFS Nangate45 in the 12 GB VM: 8×8 = 3 min 9 s, 2.4 GB; 16×16 = 13 min 41 s, 5.9 GB; 32×32 not run (extrapolates above 14 GB).
- Details: `docs/reports/capacity.md`.

### Next steps
- Phase 1 onward per `docs/PLAN.md`; fill `isa/isa.yaml`, `tb/formal`, `tb/e2e`.
- Re-check Verilator memory on a larger design.
- Verify target model configs (still unverified).
