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
- **anything else:** a fault (`RAIL`). Cross-rail transfers are two DMA operations through a staging buffer (noc.md §6). That includes
  hashed-table gathers: rows on the other rail of another package are gathered by the sibling die and pushed on
  (hyper_connections.md G-4, review finding I-8).

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
| `0x200_3000_0000` | 16 x 1 MiB | DIECTL block i | 0 CP, 1 host interface, 2-5 HBM controllers 0-3, 6-9 D2D bridges, 10-13 C2C bridges, 14 NoC (table below) |
| everything else | | unmapped | faults |

**DIECTL blocks** (`isa.yaml` `memmap.diectl`; review finding I-15). The source network interface routes a DIECTL access to the
endpoint of the leaf listed here:

| Block | Holds | Leaf |
|---|---|---|
| 0 | command processor; `MAILBOX` at offset 0: 64 x 8 B, slot c written by core c (`IRQ`, `HALT`, fault code), each write interrupts the CP | W2 |
| 1 | host interface | W3 |
| 2-5 | HBM controller of stack 0-3 (hbm.md H-7), through its port-0 leaf | N1, N5, S1, S5 |
| 6-9 | D2D bridge of sub-link 0-3 (rows 1-4) | E1-E4 |
| 10-13 | C2C bridge of leaf W1-W4 (2 ports each; the peer routing table) | W1-W4 |
| 14 | NoC configuration and counters | W2 |
| 15 | reserved | |

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
PC index into the PCs present, move the unused PC bits into the row and XOR them into the bank, so the map stays one-to-one, 2 KiB
blocks stay on one leaf and streams stay at full rate (hbm.md H-1, review finding I-10).

## 4. On-chip SRAM

- **SPM:**
  - Byte-addressed, 4 MiB, 32 banks of 64 B with `bank = addr[10:6] ^ addr[15:11]` (`isa.spm_bank`). Row pitches that are powers
    of two from 2 KiB up are padded by 512 B (1 KiB at 16 KiB) so that two-row loads do not collide (microarch.md §2.3).
  - Instructions address the local SPM with offsets (space `SPM`). Other cores' SPMs are reached through the GPA window.
  - Granularity: 32 B (the DMA handles any size; sub-32 B writes use byte masks).
- **GSRAM:** slice-contiguous 1 MiB windows. Software spreads hot data over slices itself.
- **ACC:** not memory-mapped. Only MXU and VPU instructions reach it (space `ACC`, addressed in rows of 64 FP32).
- **Alignment:** tile bases in SPM are 64 B aligned. A stationary-load (MXU W) tile with a padded pitch starts on a 2 KiB boundary,
  the only base alignment at which every padded pitch is conflict-free (microarch.md §2.3, review finding V-8). Read requests
  and write bursts do not cross a 2 KiB boundary; the DMA splits them (review finding V-10).

## 5. Core control block (CORECTL, per core)

| Offset | Size | Contents |
|---|---|---|
| `0x00000` | 64 KiB | IMEM (4,096 instructions); writable while the core is stopped, or by DMA overlay followed by `FENCE` |
| `0x10000` | 8 KiB | DTAB: 256 tile descriptors x 32 B; read by an engine when an instruction starts, so DMA updates follow isa.md §4 |
| `0x12000` | 256 B | semaphores 0-63, 32-bit: a write sets, a read returns the value |
| `0x12100` | 256 B | semaphore add window: a plain write (`wop = WRITE`) adds its 32-bit word as an unsigned integer, mod 2^32; this is what `SIGNAL` and `rsem` use. `ADD_F32` / `ADD_BF16` / `MAX_F32` to CORECTL fault `ADDR_FORBIDDEN` (review finding I-15) |
| `0x13000` | 4 KiB | CSRs (isa.yaml `csr` enum, 8 B each) |
| `0x14000` | 8 B | `START`: write a PC (instruction index) to start the core there (CP / host) |
| `0x14008` | 8 B | `STOP`: write to stop the core after its queues drain (CP / host) |

## 6. KV pages

Revised for review finding I-2: pages are counted in rows, never split a row, and carry the block scales of their own rows.

- A page table is an array of 32-bit PTEs in SPM at `R[pt_reg]`. The DMA reads it in `PAGED` mode.
- PTE bits:
  - `[29:0]`: frame number in 4 KiB units of the die-local space (covers 4 TiB), the start of the page;
  - `[30]`: valid (`PTE_INVALID` otherwise);
  - `[31]`: reserved.
- **A page is `2^page_shift` rows** (TD `page_shift`; a row is one token's entry with stride `s1`). Rows never cross a page. A page
  occupies `ceil(bytes / 4 KiB)` consecutive frames from its PTE's frame, so its size need not be a power of two: a 576 B MLA row
  works (64 rows = 36 KiB = 9 frames).
- **Paged pointer.** The base register of a PAGED operand is `isa.yaml` `paged_ptr`: `node` [48:42], `row` [41:20] (first logical
  row), `byte` [19:0] (offset inside the row, for example one KV group of a row that holds several). The node may name another die of
  the same package or rail.
- **Address of element (i0, i1, i2, i3)** of a PAGED TD:
  - logical row `r = row + i1` (`DMA_COPY`), or `r = row + index[i]` (`DMA_GATHER` / `DMA_SCATTER`);
  - page `r >> page_shift`, row in the page `q = r & (2^page_shift - 1)`;
  - in-row offset `o = byte + i0 * esize + i2 * s2 + i3 * s3`, address `= frame * 4 KiB + q * s1 + o`.
  - Dims 0, 2 and 3 address inside one row: `o` must stay within `s1` (`RANGE` otherwise).
  - Reference: `isa.paged_addr`, tested in `test_isa.py`.
- **Scale region** (block formats: NVFP4, MXFP4, MXFP8):
  - Each page holds its rows' data first and then their block scales, at `sc_off * 256 B` from the page start, with scale row
    stride `aux0`. So one PTE translates both, and data and scales never sit in different pages.
  - The scale of an element of row q is the byte at `frame * 4 KiB + sc_off * 256 + q * aux0 + floor(o / block_bytes)`, from
    the whole in-row offset `o` *(review finding V-4)*. The rule before used `byte` and `i0` only, so a TD with n2 > 1 over the
    groups of a row converted every group with group 0's scales.
    - `block_bytes` is a block's size in the row: 8 B (NVFP4), 16 B (MXFP4), 32 B (MXFP8). Every scale is one byte (`isa.yaml`
      `dtype_info`).
    - `byte`, `s1`, `s2` and `s3` must be multiples of `block_bytes` (`ILLEGAL_FIELD` otherwise), so every dim-0 run starts on a
      block (`s1` added by review finding W-5).
    - **The scale rows of a page tile whole aligned 32 B blocks** *(review finding W-4 c)*: `aux0` divides 32 or is a multiple
      of it, and a page has at least 32 / `aux0` rows (`page_shift` ≥ log2(32 / `aux0`)). Otherwise `ILLEGAL_FIELD`, for every
      PAGED TD of a block format or with `SCALE_REGION`. A tail block (§8) therefore never spans two pages: with
      `page_shift` 2 and 4 B rows, every block would be two 16 B writes and fault `HBM_ALIGN`.
  - A TD with `SCALE_REGION` addresses the scale rows themselves: `frame * 4 KiB + sc_off * 256 + q * aux0 + byte + i0 + i2 * s2 +
    i3 * s3`, inside `aux0`. Its dtype is `UINT8`. The KV write path uses it: the data rows and the scale rows of a new token are
    two transfers, and `cvt = NONE` never moves scales on its own.
  - Non-paged block-format operands (weights) keep the published separate scale array `[rows, K / block]`, row stride `aux0`,
    unless the TD has `SCALE_INLINE` (below).
    `R[scale_reg]` points at the scale of the base element's block. The scale of element (i0, i1, i2, i3) is at
    `R[scale_reg] + i1 * aux0 + floor(o / block_bytes)`, with `o = i0 * esize + i2 * s2 + i3 * s3`.
    - **The base element starts a block** *(review finding W-5)*: the base address, `s1`, `s2` and `s3` are multiples of
      `block_bytes` (`ILLEGAL_FIELD` otherwise). `o` has no in-block part, so without the rule an NVFP4 slice that starts at
      element 8 of a row would convert its element 8 (row element 16, block 1) with block 0's scale, silently. A weight
      slice therefore starts on a block, and a fold-converted slice on a 256-element tile (microarch.md §2.5).
    - Rejected: adding the base's in-block offset to `o`. It would make the formula general, but a dim-0 run that starts
      inside a block can also split a conversion unit across granules, and no layout needs it.
    - Reference: `isa.block_addr`, tested in `test_isa.py` (`test_block_addr_requires_block_aligned_base`).
  - **Inline scales (`SCALE_INLINE`) *(review finding Z-4)*.** A row holds its n0 elements and then their n0 / block one-byte
    scales, so an Engram MXFP8 row is 256 B + 8 B = 264 B. The scale of element i0 is at the row start + n0 * esize +
    floor(i0 / block). The row starts at the base + i1 * `s1` (`DMA_COPY`) or the base + index[i] * `s1` (`DMA_GATHER`).
    - The base and `s1` need no block alignment: every row starts its own first block, so the block rule above (written for
      a separate scale array) does not apply. Applied to `s1` = 264 it would fault every n-gram gather (W-5 had done that).
    - `ILLEGAL_FIELD` otherwise: a `SCALE_INLINE` operand is non-paged and in a block format; a row is one dim-0 run of
      whole blocks (n2 = n3 = 1, n0 a multiple of the block); `s1` is at least the row's size; a gather's `row_bytes` is
      the row's size (264). `scale_reg` is unused.
    - Reference: `isa.inline_addr` (`isa.block_addr` uses it for a `SCALE_INLINE` TD), tested in `test_isa.py`
      (`test_engram_inline_gather`). The DMA stages each row raw and converts it whole (microarch.md §2.5, inline entries).
- **Writing scale rows *(review finding V-5)*.** A DMA write packet holds contiguous destination bytes. Adjacent runs merge,
  including consecutive rows of one page, up to a 256 B granule boundary, and `HBM_ALIGN` is checked per packet (microarch.md
  §2.5). So a `SCALE_REGION` TD with n1 = 32 / `aux0` rows, starting at a row that is a multiple of that count, writes one aligned
  32 B block. This is the write that the tail-block rewrite (§8) needs. Before, each 4 B or 8 B row was its own write and faulted
  `HBM_ALIGN`.
- The runtime (CP) owns the tables. Kernels see them as SPM arrays loaded by DMA.
- Layout rule:
  - One page holds a run of consecutive tokens for one layer, with token stride `s1`. A row is one KV group, or all KV groups of the
    token (then `byte` selects the group).
  - Examples: MiniMax NVFP4 KV, 4 groups of 128 per row: 256 B of elements + 32 B of scales per token, 128 rows per page (vLLM's
    block size) = 32 KiB + 4 KiB = 9 frames. DeepSeek NVFP4 global KV (one 512-wide head): 256 B + 32 B per token.
  - Index keys (MiniMax, DeepSeek, Step region summaries) get their own pages, so the index scan streams contiguous bytes.

**Worked example: appending one token** (review findings V-4, V-5; `test_isa.py` `test_paged_append_walk` and
`test_paged_scale_uses_whole_in_row_offset` compute every address below from packed TD and `paged_ptr` fields).

| | DeepSeek index key (MXFP4) | MiniMax KV, K or V (NVFP4) |
|---|---|---|
| Row | 128 elements: 64 B + 4 B of E8M0 scales | 4 KV groups x 128: 256 B + 32 B of E4M3 scales |
| Page | 1,024 rows (`page_shift` 10): data 64 KiB, scales at `sc_off` 256 (64 KiB), 4 KiB; 17 frames, none wasted | 128 rows (`page_shift` 7): data 32 KiB, scales at `sc_off` 128 (32 KiB), 4 KiB; 9 frames |
| Data TD (`KD`) | `GLOBAL`, `PAGED`, `UINT8`, n0 = 64, n1 = 1, s1 = 64 | `GLOBAL`, `PAGED`, `UINT8`, n0 = 256, n1 = 1, s1 = 256 |
| Scale TD (`KS`) | `PAGED` + `SCALE_REGION`, `UINT8`, n0 = 4, n1 = 8, `aux0` 4; page fields as `KD` | `PAGED` + `SCALE_REGION`, `UINT8`, n0 = 32, n1 = 1, `aux0` 32 |
| Paged pointers | data `R21 = {0x7F, t, 0}`; scales `R23 = {0x7F, t & ~7, 0}`, the block's first row | `R21 = R23 = {0x7F, t, 0}` |
| SPM sources | `VQUANT` writes the 64 B of elements; the 4 B of scales go into the SPM copy of the current block at `(t mod 8) * 4` | `VQUANT` writes the 256 B of elements and the 32 B of scales |
| HBM writes (q = t mod rows per page) | one 64 B packet at `frame * 4 KiB + q * 64`; one 32 B packet at `+ 64 KiB + (q & ~7) * 4` (8 rows merged) | one 256 B packet at `frame * 4 KiB + q * 256`; one 32 B packet at `+ 32 KiB + q * 32` |

```
    DMA_COPY dq=1 src=R20 td=KD_SPM dst=R21 td=KD cvt=NONE wop=WRITE pt=R24  ssem=S9 sinc=1   # the data row
    DMA_COPY dq=1 src=R22 td=KS_SPM dst=R23 td=KS cvt=NONE wop=WRITE pt=R24  ssem=S9 sinc=1   # the scale block / scale row
```
- `R24` is the SPM offset of the layer's page table. `rsem` is S0, because the destination is HBM (d2d_c2c.md L-6). `scale_reg`,
  `aux_reg` and `ts_reg` are R0, because `cvt = NONE` moves no scales.
- Completion: S9 reaches its base + 2 once both writes are acknowledged. A reader on another core waits for a `SIGNAL` sent after
  them (L-6).
- **Batched append *(review finding W-4 b)*.** A batch-decode step appends one token to each of B sequences, as one `DMA_SCATTER`
  per TD. The index list holds logical rows (each sequence's rows are a range of the layer's page table).
  - The data scatter has one index per sequence (a 64 B or 256 B row each).
  - The scale scatter has **32 / `aux0` consecutive indices per sequence**, from the first row of the sequence's current block,
    and its SPM source is the B block copies, 32 B each. Each block is then one aligned 32 B packet (merged rows of one page).
    One index per sequence would give B packets of `aux0` bytes, which fault `HBM_ALIGN`. With `aux0` = 32 (MiniMax KV) the
    two forms are the same.
  - DeepSeek index key, B = 4 (`test_isa.py` `test_paged_scatter_append`): four 64 B data packets, and four 32 B scale packets
    from 32 indices.
- The DeepSeek block's later rows are written as their tokens arrive (tail-block rewrite, §8). MiniMax's own NVFP4 index key
  (64 B + 8 B per token) is the same case with 4 rows per block. The network does not keep issue order (§9), so two writes of
  one block are one write (several tokens in one step) or the second waits for the first one's ack (`ssem`), from one owner
  core per block (hbm.md H-2, review finding W-4 a). A batched scatter append waits for the previous step's scatter the same way.
- Reading back:
  - The DeepSeek index scan reads TD `MXFP4`, `PAGED`, n0 = 128, `sc_off` 256, `aux0` 4. Element i0 of row q has its scale at
    `frame * 4 KiB + 64 KiB + q * 4 + floor(i0 / 32)`.
  - The MiniMax KV gather (`DMA_GATHER`, `cvt NVFP4_BF16`, `ts_reg` = the layer's FP32 tensor scale) reads all 4 groups with TD
    `NVFP4`, n0 = 128, n2 = 4, s2 = 64 B. Group g, element i0 has its scale at `+ 32 KiB + q * 32 + 8 g + floor(i0 / 16)`; before
    V-4, every group got group 0's 8 scales.
  - Both reads stage the scales before the data (microarch.md §2.5, review finding V-1).

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
- HBM writes must be 32 B aligned in address and size, otherwise `HBM_ALIGN` (no write masks; hbm.md H-2). The scale rows of a page
  (§6) can be smaller than 32 B per token (8 B for one 128-wide NVFP4 KV group, 4 B for an MXFP4 index key). They are not padded:
  the compiler rewrites the aligned 32 B block that holds the new token's scales from a copy in SPM (tail-block rewrite, hbm.md H-2,
  review finding I-14). That block is one 32 B write packet (§6, review finding V-5). Two writes of one block are ordered by
  an ack, not by the controller's merging (review finding W-4 a).
- Fault codes: isa.yaml `fault_cause`.

## 9. Ordering

- The network does not order packets, even between the same two endpoints: planes and virtual channels are chosen per packet.
- Programs get ordering from three mechanisms only:
  1. **Write acknowledgements.** `SIGNAL`, `FENCE` and `HALT` wait for every earlier write of their scope to be acknowledged, which gives
     release semantics. Multicast writes are posted: these wait only for their injection (isa.md §3).
  2. **Byte-counting semaphores.** A write with `rsem` adds its payload size to the destination core's semaphore after the data is
     written. A consumer waiting for `>= bytes` sees all the data regardless of arrival order (put-with-signal).
  3. **Reads after a semaphore wait** see every write that the semaphore accounted for. This is the only ordering of multicast data.
- There are no caches, so there is nothing to keep coherent.
