# Memory map (Phase 2)

Status: **reviewed 2026-11-01** (Phase 2 self-review, [spec_review.md](../reports/spec_review.md)).
- Source of truth for the numbers: the `memmap` section of `isa/isa.yaml`.
- `isa/isa.py` generates the SV and C++ constants from it, and `decode_addr` / `hbm_fields` are the reference decoders.
- `tb/unit/test_isa.py` checks the properties claimed here.

## 1. Global addresses

A global physical address (GPA) is 49 bits: `[48:42]` node, `[41:0]` die-local offset (4 TiB per die).

| Node value | Meaning |
|---|---|
| 0-63 | die `2 * package + die` (up to 32 packages, the perf simulator's upper bound) |
| 0x7F | this die (position-independent code: kernels compiled once run on every die) |
| 64-126 | reserved, access faults |

The source network interface resolves the node:
- **own node:** the local mesh;
- **same package:** the D2D bridge on the source's row;
- **same die index, other package:** the C2C port to that package;
- **anything else:** a fault (`RAIL`). Cross-rail transfers are two DMA operations through a staging buffer (noc.md §6).

There is no virtual memory. Kernels, the compiler and the runtime use physical addresses. The only translation is the KV page table in
the DMA (§6).

## 2. Die-local map

| Offset | Size | Region | Notes |
|---|---|---|---|
| `0x000_0000_0000` | 144 GiB | HBM | stack s at `s * 36 GiB`, stack-contiguous (§3) |
| `0x024_0000_0000` | to 2 TiB | reserved HBM | faults (room for 16-Hi or 6-stack variants) |
| `0x200_0000_0000` | 64 x 4 MiB | SPM of core c at `+ c * 4 MiB` | core id c = `(y - 1) * 8 + (x - 1)` for tile (x, y); 32 populated |
| `0x200_1000_0000` | 64 x 1 MiB | GSRAM slice c at `+ c * 1 MiB` | slice c lives in core tile c |
| `0x200_2000_0000` | 64 x 1 MiB | CORECTL of core c at `+ c * 1 MiB` | IMEM, DTAB, semaphores, CSRs (§5); 128 KiB used |
| `0x200_3000_0000` | 16 x 1 MiB | DIECTL block i | 0 CP, 1 host interface, 2-5 HBM controllers 0-3, 6 D2D bridge, 7 C2C bridge, 8 NoC |
| everything else | | unmapped | faults |

Capacity note:
- The HBM numbers are binary: 24 Gb dies, so 36 GiB per stack (38.65 GB) and 144 GiB per die.
- The Phase 1 roofline used 36e9 B per stack (`sim/analytic/hw.py`), so its capacity results carry a 7% margin. The frozen package
  counts are not changed by this.

## 3. HBM address mapping

**Stack-contiguous, no cross-stack interleave *(P2)*.** One core's network interface carries 256 B/cycle, less than a third of one
stack's 820 B/cycle, so a single stream never needs more than one stack. Balance across stacks therefore comes from placement, not hashing.
- **Stack-affine placement:**
  - The compiler places data that a core streams in the stack of that core's quadrant:
    - stack 0 for cores with x 1-4 and y 1-2;
    - stack 1 for x 5-8, y 1-2;
    - stack 2 for x 1-4, y 3-4;
    - stack 3 for x 5-8, y 3-4.
  - That covers expert and dense weight shards, KV pages of the sequences it attends, and its spill buffers.
  - Each stack then serves 8 cores at 102 B/cycle each. Mesh load peaks at 80%, at the leaf links (noc.md §5).
- **Shared tensors:** tensors read by every core (embeddings, broadcast sources) can go anywhere. Fully interleaved access still fits
  (85% peak link load).
- Rejected: a hardware interleave across stacks (or an aliased interleaved window). It adds a second address view to verify, and it
  forces every stream through the mesh bisection. It buys nothing for statically compiled kernels.

Inside a stack, offset `o`:
- SID (stack ID, 3 per 12-Hi stack) = `o div 12 GiB` (two comparators);
- `p = o mod 12 GiB`, decoded below.

| Bits of p | Field | Notes |
|---|---|---|
| [4:0] | byte in a 32 B burst | HBM3 pseudo-channel: 32 bits x BL8 |
| [7:5] | column, low 3 bits | a 256 B granule = 8 bursts in one row |
| [12:8] | pseudo-channel (PC), before hashing | consecutive 256 B granules go to different PCs |
| [14:13] | column, high 2 bits | 1 KiB row = 32 bursts |
| [16:15] | bank group, before hashing | |
| [18:17] | bank, before hashing | |
| [33:19] | row | 0 .. 24,575 (24 Gb die: 1.5 x a power of two) |

Hashes:
- `pc = p[12:8] ^ p[17:13] ^ row[4:0]`
- `bg = p[16:15] ^ row[6:5]`
- `ba = p[18:17] ^ row[8:7]`

They are bijective because the XOR sources are kept as fields.

Properties (all tested):
- **An 8 KiB stripe touches all 32 PCs once.**
- **A 2 KiB-aligned block stays on one HBM leaf router** (`leaf = pc[4:3]`, constant inside the block). One read request covers it,
  which keeps request overhead at 1/16 of response flits (noc.md §5).
- **A 256 B granule is 8 row hits** in one bank.
- Strides of 64 KiB still spread over at least 16 PCs (the `p[17:13]` term).
- The map is one-to-one on 32 B bursts.

Per stack: 32 PCs x 16 banks (4 bank groups x 4) x 3 SIDs x 24,576 rows x 1 KiB = 36 GiB.
The timing table, scheduler and refresh are in [hbm.md](hbm.md) (H-3, H-4). Reduced configurations (`TINY` 4 PCs, `MID` 8) fold the
PC index into the PCs present and move the unused PC bits into the row, so the map stays one-to-one and 2 KiB blocks stay on one
leaf (hbm.md H-1).

## 4. On-chip SRAM

- **SPM:**
  - Byte-addressed, 4 MiB, 32 banks of 64 B with `bank = addr[10:6] ^ addr[15:11]`.
  - Instructions address the local SPM with offsets (space `SPM`). Other cores' SPMs are reached through the GPA window.
  - Granularity: 32 B (the DMA handles any size; sub-32 B writes use byte masks).
- **GSRAM:** slice-contiguous 1 MiB windows. Software spreads hot data over slices itself.
- **ACC:** not memory-mapped. Only MXU and VPU instructions reach it (space `ACC`, addressed in rows of 64 FP32).
- **Alignment:** tile bases in SPM are 64 B aligned. Read requests do not cross a 2 KiB boundary; the DMA splits them.

## 5. Core control block (CORECTL, per core)

| Offset | Size | Contents |
|---|---|---|
| `0x00000` | 64 KiB | IMEM (4,096 instructions); writable while the core is stopped, or by DMA overlay followed by `FENCE` |
| `0x10000` | 8 KiB | DTAB: 256 tile descriptors x 32 B |
| `0x12000` | 256 B | semaphores 0-63, 32-bit: a write sets, a read returns the value |
| `0x12100` | 256 B | semaphore add window: a write (or DMA `ADD`) adds; this is what `SIGNAL` and `rsem` use |
| `0x13000` | 4 KiB | CSRs (isa.yaml `csr` enum, 8 B each), plus `START` (write the PC to start) and `STOP` |

## 6. KV pages

- A page table is an array of 32-bit PTEs in SPM. The DMA reads it in `PAGED` mode.
- PTE bits:
  - `[29:0]`: frame number in 4 KiB units of the die-local space (covers 4 TiB);
  - `[30]`: valid;
  - `[31]`: reserved.
- Page size: `2^page_shift` bytes (TD `aux0`), at least 4 KiB. The node comes from the base address, so pages may sit on another die of
  the same package or rail.
- The runtime (CP) owns the tables. Kernels see them as SPM arrays loaded by DMA.
- Layout rule:
  - One page holds a run of consecutive tokens for one layer and one KV group, with token stride `s1`.
  - Index keys (MiniMax, DeepSeek, Step region summaries) get their own pages, so the index scan streams contiguous bytes.

## 7. Layouts the ISA assumes

- **Weights:** HF `[out, in]` row-major. The MXU loads them column-wise (`trans_w = 1`).
  - Block-scaled formats keep their scale arrays as published.
  - The DMA produces E4M3 data plus FP32 column scales per 256-deep K tile, or BF16.
- **Hyper-connection residual stream:** `[T][n][d_slice]` BF16 in SPM, where `d_slice = d / cores` along the hidden dimension.
  - `HC_PRE`/`HC_POST` read the n streams of one token contiguously.
  - DeepSeek: 1,024 tokens x 4 x 160 x 2 B = 1.25 MiB per core (HC-1).
- **MoE dispatch:** routing writes `[T x k]` ids and weights. `VBUCKET` turns them into per-expert offsets and a permutation, and
  `DMA_GATHER` builds each expert's token block.
- **Ring buffers:** sliding-window KV uses the TD `WRAP` flag (dim-1 index modulo n1), so a 128-token window needs no copy.

## 8. Access rules and faults

| Initiator | HBM | SPM / GSRAM | CORECTL | DIECTL |
|---|---|---|---|---|
| Core DMA | read, write | read, write, add, max | read, write (IMEM only while stopped, or with an explicit `FENCE`) | no |
| Core front end (`LD`/`ST`) | no | own SPM only | own CSRs (`CSRR`/`CSRW`) | no |
| CP / host | read, write | read, write | read, write | read, write |

- An access to an unmapped, reserved or forbidden address returns an error response.
- The initiating core stops with `FAULT_CAUSE`/`FAULT_ADDR` set and sends an interrupt to the CP.
- Write-add on an HBM address is a fault (`HBM_WADD`): HBM has no read-modify-write path.
- HBM writes must be 32 B aligned in address and size, otherwise `HBM_ALIGN` (no write masks; hbm.md H-2). The compiler pads layouts
  where a per-token item is smaller (for example, NVFP4 KV scales are stored for all KV heads of a token together).
- Fault codes: isa.yaml `fault_cause`.

## 9. Ordering

- The network does not order packets, even between the same two endpoints: planes and virtual channels are chosen per packet.
- Programs get ordering from three mechanisms only:
  1. **Write acknowledgements.** `SIGNAL`, `FENCE` and `HALT` wait for every earlier write of their scope to be acknowledged, which gives
     release semantics.
  2. **Byte-counting semaphores.** A write with `rsem` adds its payload size to the destination core's semaphore after the data is
     written. A consumer waiting for `>= bytes` sees all the data regardless of arrival order (put-with-signal).
  3. **Reads after a semaphore wait** see every write that the semaphore accounted for.
- There are no caches, so there is nothing to keep coherent.
