# Independent review of the MXU (commit 241c816)

Scope of this part (**S**): `docs/spec/mxu.md` against the frozen architecture: microarch.md §2.2 and §5, numerics.md N-3 / N-5 /
N-8, arith.md "Integration contract" items 1-5, isa.md §3-5 (and §8, which (a) needs), isa_ref.md `MXU_MM`, memmap.md §4. No RTL was
read for this part. `docs/reports/status.md` was read only after the findings below were written (comparison at the end).

Method. Reading, plus independent checks in `scripts/mxu_review/spec_checks.py` (run:
`scripts/memrun mxu_review_spec python3 scripts/mxu_review/spec_checks.py`, 0.2 s, 33 MB; output in `logs/mxu_review_spec.log`). The
script does not import `arith_ref.py` and does not reuse `gen_mxu_vectors.py`:
- the SPM bank hash is re-derived from memmap.md §4 (`bank = addr[10:6] ^ addr[15:11]`), and the beat addresses are copied from
  mxu.md §4.3;
- the PE chain is a float64 / float32 model written from numerics.md N-3 (exact dot product in float64, then one RNE rounding to
  FP32 per PE, FP32 adds);
- `numerics.py` (the golden model) is imported only to show what its `mxu_mm` and `fold_e4m3` do.

Labels:
- **Confirmed**: I reproduced it with the script or by direct arithmetic on the documents.
- **Suspected**: follows from the text, but I did not reproduce it.
- Severity: **H** gives wrong results, a deadlock, or contradicts a frozen number in a way that matters; **M** is a contradiction or a gap
  that a block still to be built has to resolve; **L** is a wording problem or an inconsistency inside the document.

## Summary

| # | Sev | Topic | Status | Spec change needed before |
|---|---|---|---|---|
| S-1 | H | Column scales: no layout on the DMA side, "DMA pads N" not possible, `4N % LB` rule faults ragged N | confirmed (text + golden shapes) | `core/dma`, compiler |
| S-2 | H | K tile and accumulation order depend on `DIM`; `mxu_mm` is FULL-only; the 256-element fold tile does not match the MXU K tile in TINY/MID | confirmed (numeric example) | `core/acc` tests, `core/dma`, e2e on TINY |
| S-3 | H | Lane = `DIM` bytes changes the SPM of TINY/MID for every client; legality depends on the config; gang grant not in the SPM interface | confirmed (text) | `core/spm` |
| S-4 | M | Load beats collide at the frozen padded pitches (V-8 was checked for a different beat shape); A rows are not always conflict-free | confirmed (script) | compiler placement rules; `core/spm` |
| S-5 | M | Tile period of about 39 cycles against the frozen 512 B/cycle claims; the decode margin of the BF16 path is 1.16x, not 2.2x | confirmed arithmetic; the 39 itself is an extrapolation | microarch.md §2.2 / §6, numerics D1 |
| S-6 | M | `BF16X3` faults, but the ISA lists it as implemented | confirmed (text) | ISA (isa.yaml) |
| S-7 | M | `M = 0` faults and stops the core, while the ISA's own MoE example passes a data-dependent token count | confirmed (text) | ISA, compiler |
| S-8 | M | "No flags" on A / W faults `WRAP`, which memmap.md §7 uses for sliding-window KV | confirmed (text) | ISA / memmap |
| S-9 | M | `FAULT_ADDR` contents, 32-bit width, fault priority, in-flight instructions, missing `core/seq` fault port | confirmed (text) | `core/seq`, golden ISA model |
| S-10 | M | SPM `RANGE` is defined on bytes read, but the MXU reads past its operands by design | suspected | `core/mxu` / golden ISA model |
| S-11 | **H for `core/acc`** | `acc_*` stream vs. `core/acc` in microarch.md §5: port shape, no `ready`, `ACC_LAT` binding, flag-before-`acc_done`, where `cs` is applied, masked writes | confirmed (text) | **`core/acc`** |
| S-12 | L | DTAB read and prefetch relative to the semaphore wait; cross-instruction gap | suspected | `core/mxu` doc |
| S-13 | L | Inconsistencies inside mxu.md | confirmed | mxu.md |

**Before `core/acc` is built, these need a spec change: S-11 (all items), the part of S-2 that concerns the ACC (row width `DIM` in
TINY/MID, a golden model with a `DIM` parameter for the ACC/MXU tests), and the `ACC_LAT` contract in S-11(iii).** Fix S-1, S-3 and
S-4 before `core/dma` and `core/spm`. Fix S-6, S-7 and S-8 in the ISA before the compiler emits `MXU_MM`.

---

## S-1 (H, confirmed) The column-scale contract between DMA and MXU is not defined, and the stated workaround cannot work

**Where.**
- mxu.md:107: the rule "with `cs_reg != 0`, `4·N` a multiple of `LB` (...; the DMA pads `N` when it writes the scales)".
- mxu.md:130-131: the address formula "`R[cs_reg] + 4·(t·N + DIM·j)`".
- On the DMA side: isa.md:234 (`aux_reg`), isa.md:255-261 (block-scale contract), isa_ref.md:47 ("FP8-target conversions write FP32 column
  scales to SPM[R[aux_reg]]").
- On the MXU side: isa_ref.md:58 ("column scales FP32 [K tiles x N] at SPM[cs_reg]").

**What is wrong.**
1. **The producer's layout is not defined.** The ISA says only that the DMA writes "one FP32 column scale per (output row, 256-deep K
   tile) to `aux_reg`". It gives no order, no pitch and no alignment. The MXU side (isa_ref, mxu.md) assumes `[KT x N]`, row-major,
   pitch exactly `N`. The golden models disagree with each other. `numerics.fold_e4m3` on W `[N=3, K=512]` returns `cs` of shape
   `(3, 2)`, which is `[N, KT]`. `numerics.mxu_mm` documents and indexes `cs` as `[K tiles, N]` (`cs[t]`). Script output:
   "numerics.fold_e4m3 on W [N=3, K=512] returns cs of shape (3, 2) ([N, KT])". A DMA that converts a weight row by row produces
   the `[N, KT]` order naturally. No instruction transposes it.
2. **"The DMA pads N" has no basis in the ISA, and it contradicts mxu.md's own formula.** `aux_reg` is a bare SPM offset. No TD and no
   field carries a pitch for the scale array, so the DMA cannot pad. If it padded the row pitch to `N' = roundup(N, LB/4)`, the MXU,
   which addresses with pitch `N` (mxu.md:131), would read row `t > 0` misaligned. The only consistent reading is that the compiler
   issues `MXU_MM` with `N'` columns. In that case the MXU reads `N' - N` extra W rows (trans) or columns (non-trans). Those bytes
   belong to whatever lies next in SPM. With `ncols = N'`, their NaN / Inf products are flagged (`fp_flags` is column-masked only by
   `ncols`), so `FP_STATUS` gets spurious bits.
3. **The new rule faults legal-looking programs with a ragged N.** In FULL, `4·N % 64 == 0` means N is a multiple of 16. Failing
   input:
   - FULL; `Q·K^T` over an NVFP4 K-cache converted with `NVFP4_BF16`, which writes `R[ts_reg]` as column scales (isa.md:260, arith.md
     contract item 6). So `cs_reg != 0`.
   - `trans_w = 1`, W = `[N = 40 keys, K = 128]` BF16 (the ragged last 40 keys of a sequence).
   - Result: `ILLEGAL_FIELD`, and the core stops.

   No ISA text forbids this. `MXU_MM` has no `n_reg`-style override for N, so the compiler cannot even change N per call without a new
   TD.

**How I know.** Reading, plus the shapes the script prints.

**Proposed fix.**
- Define the scale array once, in isa.yaml, for both `DMA_COPY` and `MXU_MM`. For example: `[KT x N]` with row pitch
  `CSP = roundup(4·N, 64)` bytes. The DMA writes with that pitch, and the MXU reads at `R[cs_reg] + t·CSP + 4·DIM·j`.
- Or let the MXU take a 4 B-aligned `cs` start: a lane rotate on the 64 B scale port. That removes the N rule.
- Make `fold_e4m3` return the ISA's orientation, or document the transpose.

## S-2 (H, confirmed) The K tile and the accumulation order depend on DIM; the golden model and the fold assume FULL

**Where.**
- mxu.md:34-35: "K tile ... `4·DIM` / `2·DIM`", which is 32 / 16 in TINY.
- numerics.md:186: "It passes PE rows 0..63".
- isa_ref.md:58 and isa.md:298-299: "K tiles (256 FP8 or 128 BF16 deep)", "N (64 wide)".
- The fold tile: numerics.md:103-114, isa.md:255-256 ("256-deep K tile").
- mxu.md:5, mxu.md:207-209: the claim that "the top-level test compares the ACC image ... with `mxu_mm(A, W, mode, cs, acc,
  rowscale)`".
- numerics.md:387 (N-8: "MXU, bit-exact against `mxu_mm`").
- microarch.md:635: TINY is "unit and e2e RTL".

**What is wrong.**
1. **The accumulation order changes with DIM.** In FULL a K = 64 FP8 instruction is one K tile: a chain of 16 PE rows from +0. In TINY
   it is two tiles of 8 PE rows, and their partials are added in the ACC. These round differently. Confirmed example (my own model,
   seed 1): M = 4, K = 64, N = 8, random E4M3 operands. 15 of 32 outputs differ between DIM 64 and DIM 8. For `out[0,0]`, FULL gives
   `0xC7FA24B8` and TINY gives `0xC7FA24B9`. `numerics.mxu_mm` has no DIM or tile parameter (signature
   `mxu_mm(A, W, mode, cs, acc, rowscale)`) and equals the FULL order (script: "equals the FULL order: True, equals the TINY order:
   False"). So mxu.md:207-209 cannot hold at DIM 8 / 16, the only sizes `tb_mxu` runs. The author's test therefore cannot be comparing
   with `mxu_mm` there. N-8 has no golden model for TINY, the configuration of every RTL e2e test, and status.md:1086-1087 plans the
   Phase 3 GEMM test against `mxu_mm`.
2. **The fold tile does not match the MXU tile below FULL.** The DMA writes one column scale per 256-element tile, a frozen part of the
   fold numerics and of `cvt_source_check`. In TINY the MXU applies `cs[t]` per 32-element tile, so `KT_mxu = 8·KT_fold`. The MXU then
   reads `cs` rows the DMA never wrote, and every block-scaled GEMM on TINY/MID is wrong unless someone defines the mapping (the DMA
   replicates each scale `256/KTILE` times, or the fold tile becomes `4·DIM` per config, which changes the fold's numerics). The same
   holds for `NVFP4_BF16`'s "one per 128-deep BF16 K tile" (isa.yaml:70).
3. **ACC row width.** ACC rows are "rows of 64 FP32" (memmap.md:118, microarch.md:783); in TINY/MID the MXU writes `DIM`-wide rows.
   The ISA keeps "field widths ... of FULL" (microarch.md:641) but says nothing about semantic widths.

**How I know.** Script, section S-order (operands saved in `build/mxu_review/order_A.npy`, `order_W.npy`).

**Proposed fix.**
- numerics.md N-3: "PE rows 0..DIM-1, K tile 4·DIM / 2·DIM, N tile DIM".
- Give `numerics.mxu_mm` a `dim` parameter; the FULL default is unchanged.
- State the fold-tile mapping for reduced configs in isa.md §5 and microarch.md §4.
- State the ACC row width per config.

## S-3 (H, confirmed) "Lane = DIM bytes" silently redefines the SPM and makes legality depend on the config

**Where.**
- mxu.md:27-30 ("A `TINY` / `MID` SPM therefore has `DIM`-byte banks (core/spm parameter)").
- mxu.md:107 (alignment "multiples of `LB`").
- mxu.md:179-182 ("one grant takes all valid lanes", "exactly `SPM_LAT` cycles after the grant").
- mxu.md:240 (M-8).
- Against: microarch.md:110-111 and :721 (64 B per bank access, the bank hash, client ports "addr 22 + 64 B lanes"); memmap.md:113-121
  (32 banks of 64 B, 32 B granularity, "tile bases in SPM are 64 B aligned"); microarch.md:635-641 (configs change sizes, not the
  bank width).

**What is wrong.**
1. **Every SPM client changes in TINY/MID.** 8 B banks in TINY change the bank hash (which presumes `addr[10:6]` = 64 B), the DMA's
   512 B/cycle port (8 lanes of 64 B would become 64 lanes of 8 B), the VPU's 512 B/cycle ports, the NI target and the "sub-32 B
   writes use byte masks" granularity. None of that is in microarch.md, and the decision is local to the MXU (the brief: "this is my
   choice, not a Phase 2 decision").
2. **Legality depends on the config.** The MXU checks multiples of `LB`: 8 B in TINY, 64 B in FULL. Failing input: an A base of
   `0x1008` passes in TINY and faults `ILLEGAL_FIELD` in FULL. A program that passes the TINY e2e tests can fault on the real
   machine, and the golden ISA model would need `DIM` to predict faults. memmap's 64 B base rule is the stronger one and applies in
   every config. The `s1` alignment rule is new; neither memmap nor the ISA states it.
3. **The gang grant is not part of the SPM interface.** The MXU asks for 4 (A) or 8 (W) lanes "granted together", with the response
   exactly `SPM_LAT` later. microarch.md §2.3 / §5 has per-bank round-robin arbitration of 64 B lanes, 1R per bank. When two lanes of
   one gang map to the same bank (S-4 shows this happens at frozen pitches), the banks cannot serve them in one cycle. A `core/spm` that
   grants a gang "when all its banks are free" therefore never grants it, and the MXU deadlocks. Otherwise `core/spm` must split the
   gang over two cycles and realign the data to keep the fixed latency. mxu.md:225 ("the cost is a stall in that beat") assumes the
   second behaviour without stating it as a `core/spm` requirement. The `tb_mxu` SPM has no bank model (mxu.md:204), so this was never
   exercised.

**How I know.** Reading.

**Proposed fix.**
- Keep 64 B banks in every config.
- Let `core/spm` (or a thin adapter) serve `DIM`-byte MXU lanes out of 64 B banks.
- State one alignment rule in isa.yaml `MXU_MM` that does not depend on the config: 64 B for the bases, `s1`, `cs` and `rs`.
- Add the gang-grant semantics (split, realign, fixed latency) to microarch.md §5 `core/spm`.

## S-4 (M, confirmed) The load beats collide at the frozen padded pitches

**Where.**
- The pitch rule, frozen by review V-8: microarch.md:111-117 and memmap.md:113-114. It was designed for "a stationary load of two
  256 B rows per cycle".
- mxu.md §4.3 changes the beat to 8 rows x 64 B (trans, and non-trans FP8) or 4 rows x 128 B (non-trans BF16).
- mxu.md:223-226 says this costs "one bank collision per beat".
- mxu.md:233 (M-2) says the A read is "one contiguous `KB` bytes (conflict-free)".

**Confirmed numbers.** Script section S-banks: cycles per FULL tile, 32 = conflict-free, 2 KiB-aligned bases (the best case
memmap allows), any `t` and `j`.

| W shape and pitch | cycles per tile |
|---|---|
| trans FP8, K = 2048 → padded 2 KiB + 512 | **48** (16 of 32 beats collide, lanes 0 and 7) |
| trans BF16, K = 1024 → padded 2 KiB + 512 | **48** |
| non-trans FP8, N = 2048 → padded 2.5 KiB | **48** |
| non-trans FP8, N = 640 / 576 | 40 / 33-34 |
| non-trans BF16, N = 512 (MLA V, 1 KiB rows) | 32-**64** (mean 48) |
| 4 KiB + 512, 8 KiB + 512, 16 KiB + 1 KiB; unpadded 2304, 3072, 6144, 7168, 14 KiB | 32 |

- Concrete beat: trans, base 0, pitch 2560, beat 1 (rows 8-15). Its lanes `0x05000` and `0x09600` both map to bank 10. Rows `c` and
  `c + 7` are 17,920 B apart, which aliases in the hash.
- So mxu.md's "one collision per beat" is wrong in both directions: half the beats collide, which costs 48 cycles per tile, not 64.
- The frozen padding rule is no longer conflict-free for the beat shape this MXU uses.
- A rows: with only 64 B alignment required, 6 of every 1,024 64 B-aligned starts collide (for example `0x7FC0` / `0x8000`, both bank
  16, across a 32 KiB boundary of the hash). There are none at 256 B-aligned starts. So M-2's "conflict-free" holds only if
  A bases and `s1` are 256 B aligned, a rule nobody states.

**Proposed fix.** Pick one:
- re-derive the pitch rule (microarch.md §2.3, `test_isa.py`) for the 8-row beat;
- reorder the beat (for example two rows x 4 lanes, the V-8 shape) so the frozen rule still holds;
- add the conflict cost to the period model.

Also require 256 B alignment of the A row starts, or drop "conflict-free" from M-2.

## S-5 (M; the arithmetic is confirmed, the 39 cycles is an extrapolation) The tile period against the frozen 512 B/cycle claims

**Where.**
- The frozen claims: microarch.md:84-86 ("A tile loads in 32 cycles while the previous tile streams"), microarch.md:99 (decode "2.9x
  ... FP8, 1.5x for BF16"), microarch.md:824 (§6), numerics.md:322-326 (D1: "every stage of the weight path keeps a 1.60x margin"),
  status.md:191 ("The MXU is never the decode bottleneck").
- mxu.md:79, mxu.md:213-220.

**Numbers** (script section S-rate; tile = 16 KiB; need per microarch.md §6: 193 elements/cycle at peak share, 164 at 0.85; NVFP4 at
0.5625 B/element: 181 / 154):

| period (cycles) | B/cycle | FP8 margin (peak / 0.85) | BF16 elements/cycle | NVFP4 → BF16 margin (peak / 0.85) |
|---|---|---|---|---|
| 32 (frozen port rate) | 512 | 2.65x / 3.12x | 256 | 1.41x / 1.66x |
| 39 (mxu.md extrapolation) | 420 | 2.18x / 2.56x | 210 | **1.16x / 1.36x** |
| 48 (S-4, 2.5 KiB pitch) | 341 | 1.77x / 2.08x | 171 | **0.94x** / 1.11x |

- mxu.md's "2.2x the 193 B/cycle decode need" counts FP8 elements. That fits MXFP4 / MXFP8, which always fold (D1). But D1 sends
  NVFP4 decode GEMMs down the **exact BF16 path**, where one element is 2 B. There the margin is 1.16x at peak share, not 2.2x.
- The MXU then replaces the DMA SPM write port (248 elements/cycle) as the narrowest stage.
- Combined with S-4 (K = 1024 BF16 at a 2.5 KiB pitch), it is below the HBM share (0.94x).

**Basis of the 39.** It is a one-parameter fit (`k = 8`) to one measured point (DIM 32), with no residual and no check at DIM 8 or
16.

**A further inconsistency.** mxu.md:220 says "the load hides behind `M >= 32`, the period is `M`". The bound at mxu.md:217 gives
`P >= 39.5` at FULL, so for `32 <= M < 40` the period is about 39.5, not `M`.

**Proposed fix.** Restate the decode margins (microarch.md §2.2, §6; numerics D1; status.md:191) with the measured period and the
bank-conflict cost, or close the gap (mxu.md §9: a third buffer, or a conflict-aware beat order). Before the perf simulator's MXU
efficiency model is calibrated, simulate at least DIM 64 at `M = 1` (lint-clean, but never run).

## S-6 (M, confirmed) BF16X3 is an ISA instruction that the MXU faults

**Where.** mxu.md:102, mxu.md:243-246. Against: isa.md:305-306, isa_ref.md:58 ("mode BF16X3 emulates FP32 with three BF16 passes"),
isa.md:339 (coverage table: "`VMATVEC` / `MXU_MM BF16X3`"), numerics.md:197-202.

**What is wrong.**
- The ISA presents `BF16X3` as implemented; the RTL faults it with `ILLEGAL_FIELD`, which stops the core.
- D2 shows that the only user, the mHC mixer, needs two `NORMAL` passes. So the honest fix is in the ISA, the same way `SCALE_INLINE`
  was handled: mark `BF16X3` "reserved in v1 (`ILLEGAL_FIELD`)" in isa.yaml and in isa.md §5 / §6.
- What stays open even then: the ISA never says which operand dtypes `BF16X3` takes (FP32 A and W?). With FP32 operands, an A row of
  one 128-deep K tile is 512 B, so it no longer streams "one row per cycle" on the 256 B/cycle port.

## S-7 (M, confirmed) M = 0 faults, but the ISA's own MoE example produces it

**Where.** mxu.md:103, :106 (`1 <= M`, `ILLEGAL_FIELD`). Against: isa.md:298 ("for any M"), isa.md:351-355 (§7: `MXU_MM ... m=R11`,
where `R11` is a token count from `VBUCKET` and `LD`).

**Failing trace.** A decode step routes no token to the core's expert. `VBUCKET` writes count 0, `LD R11` loads it, and `MXU_MM`
gets `m=R11` with `R[11] = 0`. It faults `ILLEGAL_FIELD` and the core stops. A compiler can branch around the call, but it must then
also fake the `ssem` increment that later instructions wait for.

**Proposed fix.** Define `M = 0` as a no-op that waits, completes and increments `ssem`. Also define how `R[m_reg]` (64 bits) is
compared. If `M` is truncated to the 16 bits of a TD `n1`, then `R[m_reg] = 2^16 + 1` silently computes one row. Say "64-bit unsigned,
compared in full; RANGE beyond the ACC".

## S-8 (M, confirmed) "No flags" on the operands faults the WRAP ring buffers of memmap.md §7

**Where.** mxu.md:103-104. Against: memmap.md:272 ("sliding-window KV uses the TD `WRAP` flag (dim-1 index modulo n1), so a 128-token
window needs no copy"), isa.md:334.

**What is wrong.** If the window is in SPM and is the `K^T` operand (`trans_w = 1`), `MXU_MM` faults. Either define `WRAP` for the
MXU's W row index (and A's), or say in memmap.md §7 that `WRAP` is a DMA-only feature, so a window is always copied into a linear SPM
tile first. That costs a copy per step, which "needs no copy" denies.

## S-9 (M, confirmed) FAULT_ADDR and the fault interface are not consistent with the ISA

**Where.** mxu.md:95-96, :113, :194. Against: isa.yaml:187 ("`FAULT_ADDR` holds the address or the instruction index"), isa.md:364-371,
microarch.md:73, microarch.md:670-679 (`core/seq` ports).

1. **What FAULT_ADDR holds.** For `ILLEGAL_FIELD` with no address (`acc_mode = 3`, mode 2-7, a dtype mismatch, a K mismatch, `M = 0`),
   "the offending operand's address" is undefined. The ISA's alternative, "the instruction index", is not available: an MQ entry does
   not capture the PC (microarch.md:69-71).
2. **"(or the ACC row for RANGE)".** This does not say which `RANGE` (SPM or ACC). The CP cannot tell an ACC row from an SPM offset in
   the same CSR without decoding the instruction.
3. **Width.** `fault_addr` is 32 bits. A `TD_SPACE` fault on a `GLOBAL` operand truncates its 49-bit GPA. Example: `R[a_reg] =
   0x7F_0000_0000_1000` gives `FAULT_ADDR = 0x0000_1000`.
4. **Priority.** No order is defined among simultaneous faults (for example a `GLOBAL` W that is also misaligned). The golden ISA model
   cannot predict `FAULT_CAUSE`.
5. **Undefined values.** Values 2-7 of the 3-bit `mode` field, non-zero reserved bits `[38:0]` of `T_MXU`, and `rs_reg != 0` without
   `RESCALE` are not mentioned. They are probably faulted or ignored; the ISA never defined them, and mxu.md should.
6. **Instructions already in flight.** "The MXU stops taking instructions" (mxu.md:96). isa.md §8: "a fault stops the core (front
   end and engines)". The spec does not say whether earlier MXU instructions already in flight (FIFO depth 4) finish and signal.
7. **No fault input on `core/seq`.** microarch.md §5 gives `core/seq` no fault input from any engine. mxu.md's `fault_*` port has no
   destination in the architecture.

## S-10 (M, suspected) RANGE is defined on bytes read, and the MXU over-reads

**Where.** mxu.md:109 ("every byte the instruction reads is inside the SPM").

**What is wrong.** The MXU reads past its operands by design:
- 4 lanes of `cs` per pass whatever N is (mxu.md:147);
- whole rowscale lanes past M;
- non-trans W bytes `[j·DIM, +DIM)` past N.

Failing input (FULL): N = 16, KT = 1, a 64 B `cs` array at SPM offset `4 MiB - 64`. The operand is in range, but the pass reads 256 B,
so it faults `RANGE` under the stated rule. If the RTL checks the operand extent instead, the lanes past the end wrap the 22-bit
address, which is harmless. I did not check which one the RTL does (no RTL in this part).

**Proposed fix.** Define `RANGE` on the operand extents. State that over-reads are discarded and may wrap.

## S-11 (H for `core/acc`, confirmed) The acc_* stream against what microarch.md §5 says core/acc accepts

**Where.** mxu.md:183-197, :153-158 (`ACC_LAT`), :163-164 (`acc_done`). Against: microarch.md:702 (MXU `acc_rmw`: "64 x 32 read + 64 x 32
write per cycle, fixed latency"), :783 (ACC `mxu_rmw`: "fixed latency (read 2 cycles)"), :785 (`conflict`: "same-row MXU write and VPU
access ...: the VPU stalls"), :786 (ACC `fp_flags`), :662-664 (flags no later than the completion `sem_inc`), arith.md:140-145
(contract item 5).

1. **The port shape is different.** microarch.md has a read/write port pair, so the MXU would see ACC read data. mxu.md has a one-way
   push stream (`row`, `part` 2,048 b, `ncols`, three controls, `rowscale`, `cs` 2,048 b, `last`) and a new `acc_done` input that
   microarch.md does not have. Both agree that `acc_update` sits in `core/acc`. microarch.md's `acc_rmw` / `mxu_rmw` rows must be
   rewritten to the stream.
2. **No `ready` needs more than "same-row conflict".** The MXU does one RMW per cycle and the VPU does 4R + 4W rows per cycle. If the
   ACC SRAM is banked, an MXU row and VPU rows on the same bank but different rows also conflict. microarch.md stalls the VPU only for
   the same row. `core/acc` must give the MXU priority on every SRAM port conflict, or the stream needs a `ready`, which mxu.md rules
   out.
3. **`ACC_LAT` is an MXU parameter (default 8) that nothing ties to the ACC.** The hazard-free claim (mxu.md:155-158, M-6, P3) needs
   `ACC_LAT >= ` the real RMW round trip: read 2 cycles + `fp32_mul` + `fp32_add` + write. Failing trace: `core/acc` built with a
   10-cycle round trip while the MXU keeps `ACC_LAT = 8`. At `M = 1`, two back-to-back K-tile passes on the same row are 8 cycles
   apart, so the second reads the row before the first's write: silent wrong sums. Make it one parameter owned by `core/acc`, with an
   elaboration-time assertion.
4. **Flags against completion.** The ACC's `fp_flags` (contract item 5) and the MXU's `sem_inc` (driven by `acc_done`) come from two
   blocks. microarch.md:662 requires the flags of the instruction no later than its `sem_inc`. Neither document requires the ACC to
   drive the flags of the `acc_last` row no later than `acc_done`.
5. **Where `cs` is applied.** `acc_cs` is held only until the first row of the next pass (mxu.md:189). With `M < ACC_LAT`, rows of two
   passes are inside an 8-deep ACC pipeline together (`M = 4`: rows 0-3 of pass p, a 4-cycle gap, then pass p+1). If the column-scale
   multiply sits after the ACC read, the ACC needs a 2,048-bit `cs` copy per stage. If it is applied to `part` in the input stage
   (allowed by N-3: `P_t × cs` does not depend on ACC), no copy is needed. The spec should say which.
6. **Masked writes.** "The ACC writes and flags only [columns < `ncols`]" (mxu.md:186) commits `core/acc` to word-masked row writes,
   which microarch.md does not mention.
7. **Row width.** The ACC row width is `DIM` in TINY/MID (S-2.3).

Items 1-7 are what `core/acc` must be specified against. The brief's own note confirms that none of them was tested ("`core/acc`
does not exist yet").

## S-12 (L, suspected) Ordering details that mxu.md does not state

- mxu.md does not say that an instruction reads DTAB, and that the next instruction's weight prefetch (mxu.md:165) starts, only after
  that instruction's own semaphore wait. isa.md §4 allows a DTAB write ordered by "wait on the DTAB-writing DMA's completion", which
  may be the instruction's own `wsem`. `tb_mxu` checks that an instruction starts only after its wait; the spec should state the rule.
- §4.4 (b) speaks of "the previous pass". It should say "including the last pass of the previous instruction", which the `ACCUM`
  test relies on.

## S-13 (L, confirmed) Inconsistencies inside mxu.md

- mxu.md:3 says "`BF16X3` and the formal properties are open"; §6 (mxu.md:205) reports the formal properties passing.
- mxu.md:79 says "about 40 cycles per tile for `FULL`"; §7 says about 39.
- mxu.md:107 cites "64 B in `FULL`; memmap.md §4", but memmap.md §4 requires 64 B in every config (S-3).
- mxu.md:224 says "one bank collision per beat"; the computed figure is half the beats (S-4).
- M-9 is listed before M-8 in §8.

---

## Comparison with status.md (read after the above)

status.md "Phase 3: core/mxu" (lines 1093-1126) and the brief already name:
- the `4N % LB` rule and "the DMA must pad `N`" (status.md:1110), but as a solved gap. S-1 shows that the pad cannot be expressed in
  the ISA and that the layout is undefined on the producer side;
- the extrapolated period (status.md:1114-1115), compared with "a decode need of 164-193" in FP8 terms only. S-5 adds that the D1 decode
  path is BF16 (1.16x), and that status.md:191 ("never the decode bottleneck") and microarch.md's 2.9x / 1.5x / D1's 1.60x are now
  stale;
- the bank-conflict note for the compiler (status.md:1116). S-4 quantifies it (48 cycles per tile at the frozen 2.5 KiB pitch, not one
  collision per beat) and shows that the frozen V-8 rule no longer covers the beat shape;
- `BF16X3` open, and `core/acc` "must accept the `acc_*` stream" (status.md:1119). S-6 adds that the ISA has to change. S-11 lists what
  `core/acc` must accept and where microarch.md §5 disagrees.

Not in status.md or the brief:
- S-2: DIM-dependent order, `mxu_mm` FULL-only, fold tile vs MXU tile in TINY/MID. status.md:1086-1087 still plans the GEMM test
  against `mxu_mm`.
- S-3: the SPM of every client changes in TINY/MID, legality depends on the config, the gang grant can deadlock.
- S-7 (`M = 0`), S-8 (`WRAP`), S-9 (`FAULT_ADDR`, priority, `core/seq` port), S-10 (`RANGE` over-reads), S-11 (iii) (`ACC_LAT`
  binding) and S-11 (iv) (flags before `acc_done`).
