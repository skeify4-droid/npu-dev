"""ISA tooling: load and validate isa/isa.yaml, encode / decode instruction words, emit SV / C++ / Markdown.

    python isa/isa.py            # validate and regenerate isa/gen/isa_pkg.sv, isa/gen/isa.h, isa/gen/isa_defs.py,
                                 # docs/spec/isa_ref.md
    python isa/isa.py --check    # validate only
"""
import os
import sys
from collections import deque

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OPCODE = (127, 120)
TILE_OPERANDS = (90, 0)
# Bit layouts other than instructions and the TD, generated the same way: {bits, [select], fields}.
LAYOUTS = ("paged_ptr", "noc_header", "dma_list_entry")


class IsaError(Exception):
    pass


def load(path=None):
    with open(path or os.path.join(HERE, "isa.yaml")) as f:
        isa = yaml.safe_load(f)
    validate(isa)
    return isa


def width(r):
    return r[0] - r[1] + 1


def fmt_fields(isa, fmt):
    """All fields of a format including the sync block, as {name: (msb, lsb)}."""
    f = isa["formats"][fmt]
    if f and "fields" in f:
        out = dict(isa["sync"]) if f.get("sync") else {}
        out.update({k: tuple(v) for k, v in f["fields"].items()})
        return out
    return {k: tuple(v) for k, v in (f or {}).items()}


def is_tile(isa, fmt):
    f = isa["formats"][fmt]
    return bool(f) and "fields" in f


def _check_disjoint(ranges, what):
    used = {}
    for name, (m, l) in ranges.items():
        if m < l:
            raise IsaError(f"{what}: field {name} has msb < lsb")
        for b in range(l, m + 1):
            if b in used:
                raise IsaError(f"{what}: fields {used[b]} and {name} overlap at bit {b}")
            used[b] = name


def _enum_fits(isa, ename, bits, what):
    vals = isa["enums"][ename].values()
    if max(vals) >= 1 << bits:
        raise IsaError(f"{what}: enum {ename} needs more than {bits} bits")
    if len(set(vals)) != len(vals):
        raise IsaError(f"enum {ename} has duplicate values")


def layout_fields(isa, name):
    """Fields of a layout section as {field: {"bits": (msb, lsb), "types": set of type names or None, "enum": str or None}}."""
    out = {}
    for k, v in isa[name]["fields"].items():
        if isinstance(v, dict):
            out[k] = {"bits": tuple(v["bits"]), "types": set(v["types"]) if "types" in v else None, "enum": v.get("enum")}
        else:
            out[k] = {"bits": tuple(v), "types": None, "enum": None}
    return out


def layout_types(isa, name):
    """Type names of a layout with a `select` field (from that field's enum), else None."""
    sel = isa[name].get("select")
    if not sel:
        return None
    return list(isa["enums"][layout_fields(isa, name)[sel]["enum"]])


def _validate_layout(isa, name):
    lay = isa[name]
    bits = lay["bits"]
    fields = layout_fields(isa, name)
    types = layout_types(isa, name)
    sel = lay.get("select")
    if sel and (sel not in fields or not fields[sel]["enum"] or fields[sel]["types"] is not None):
        raise IsaError(f"{name}: select field {sel} must exist, be an enum and be valid for every type")
    for k, f in fields.items():
        m, l = f["bits"]
        if m < l or l < 0:
            raise IsaError(f"{name}: field {k} has a bad range")
        if m >= bits:
            raise IsaError(f"{name}: field {k} beyond bit {bits - 1}")
        if f["types"] is not None:
            if types is None:
                raise IsaError(f"{name}: field {k} lists types but the layout has no select field")
            bad = f["types"] - set(types)
            if bad:
                raise IsaError(f"{name}: field {k} names unknown types {sorted(bad)}")
        if f["enum"]:
            _enum_fits(isa, f["enum"], width(f["bits"]), f"{name} field {k}")
    every = set(types or ["*"])
    names = list(fields)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            (ma, la), (mb, lb) = fields[a]["bits"], fields[b]["bits"]
            if la <= mb and lb <= ma:                     # the bit ranges overlap
                ta, tb = fields[a]["types"] or every, fields[b]["types"] or every
                if ta & tb:
                    raise IsaError(f"{name}: fields {a} and {b} overlap for {sorted(ta & tb)}")


def validate(isa):
    wb = isa["word_bits"]
    m = isa["machine"]
    if wb != 128:
        raise IsaError("word_bits must be 128")
    for e, vals in isa["enums"].items():
        if len(set(vals.values())) != len(vals):
            raise IsaError(f"enum {e} has duplicate values")
    sync = {k: tuple(v) for k, v in isa["sync"].items()}
    _check_disjoint(dict(sync, opcode=OPCODE), "sync")
    if min(l for _, l in sync.values()) <= TILE_OPERANDS[0]:
        raise IsaError("sync block must sit above the tile operand area")
    ft = isa["field_types"]
    for fmt in isa["formats"]:
        fields = fmt_fields(isa, fmt)
        _check_disjoint(dict(fields, opcode=OPCODE), f"format {fmt}")
        for name, r in fields.items():
            if r[0] >= wb:
                raise IsaError(f"format {fmt}: {name} beyond bit {wb - 1}")
            if is_tile(isa, fmt) and name not in sync and r[0] > TILE_OPERANDS[0]:
                raise IsaError(f"format {fmt}: operand {name} overlaps the sync block")
            if name not in ft:
                raise IsaError(f"format {fmt}: field {name} has no field_types entry")
            t = ft[name]
            if t == "reg" and width(r) != (m["scalar_regs"] - 1).bit_length():
                raise IsaError(f"format {fmt}: register field {name} must be {(m['scalar_regs'] - 1).bit_length()} bits")
            if t == "sem" and (1 << width(r)) < m["semaphores"]:
                raise IsaError(f"format {fmt}: {name} cannot name {m['semaphores']} semaphores")
            if t == "td" and (1 << width(r)) < m["td_entries"]:
                raise IsaError(f"format {fmt}: {name} cannot name {m['td_entries']} descriptors")
            if t.startswith("enum:"):
                _enum_fits(isa, t[5:], width(r), f"format {fmt} field {name}")
    ops = {}
    for name, ins in isa["instructions"].items():
        op = ins["op"]
        if not 0 <= op < 256:
            raise IsaError(f"{name}: opcode out of range")
        if op in ops:
            raise IsaError(f"{name}: opcode 0x{op:02x} already used by {ops[op]}")
        ops[op] = name
        fmt = ins["fmt"]
        if fmt not in isa["formats"]:
            raise IsaError(f"{name}: unknown format {fmt}")
        q = ins["queue"]
        if q == "DQ":
            if "dq" not in fmt_fields(isa, fmt):
                raise IsaError(f"{name}: queue DQ needs a dq field")
        elif q not in isa["queues"]:
            raise IsaError(f"{name}: unknown queue {q}")
        if (q == "FE") == is_tile(isa, fmt):
            raise IsaError(f"{name}: front-end instructions use S_ formats, queued ones T_ formats")
        if "func_enum" in ins:
            _enum_fits(isa, ins["func_enum"], width(fmt_fields(isa, fmt)["func"]), name)
        if "imm_fields" in ins:
            imm = fmt_fields(isa, fmt)["imm"]
            sub = {k: tuple(v) for k, v in ins["imm_fields"].items()}
            _check_disjoint(sub, f"{name} imm")
            if max(r[0] for r in sub.values()) >= width(imm):
                raise IsaError(f"{name}: imm sub-field beyond the imm field")
    _check_disjoint({k: tuple(v) for k, v in isa["td"]["fields"].items()}, "td")
    if max(v[0] for v in isa["td"]["fields"].values()) >= 8 * m["td_bytes"]:
        raise IsaError("td beyond td_bytes")
    _enum_fits(isa, "dtype", width(isa["td"]["fields"]["dtype"]), "td dtype")
    _enum_fits(isa, "space", width(isa["td"]["fields"]["space"]), "td space")
    for name in LAYOUTS:
        if name in isa:
            _validate_layout(isa, name)
    mm = isa.get("memmap")
    if mm:
        regs = sorted((r["base"], r["base"] + r.get("stride", r["size"]) * r.get("count", 1), n) for n, r in mm["regions"].items())
        for (b0, e0, n0), (b1, e1, n1) in zip(regs, regs[1:]):
            if b1 < e0:
                raise IsaError(f"memmap: {n0} overlaps {n1}")
        if regs[-1][1] > 1 << (mm["node_field"][1]):
            raise IsaError("memmap: regions exceed the die-local address space")
        for n, r in mm["regions"].items():
            if "stride" in r and r["size"] > r["stride"]:
                raise IsaError(f"memmap: {n} size exceeds its stride")
        cc = sorted((r["offset"], r["offset"] + r["size"], n) for n, r in mm["corectl"].items())
        for (b0, e0, n0), (b1, e1, n1) in zip(cc, cc[1:]):
            if b1 < e0:
                raise IsaError(f"memmap corectl: {n0} overlaps {n1}")
        if cc and cc[-1][1] > mm["regions"]["CORECTL"]["size"]:
            raise IsaError("memmap corectl: beyond the CORECTL block")
        di = isa.get("dtype_info", {})
        if set(di) != set(isa["enums"]["dtype"]):
            raise IsaError("dtype_info must list every dtype")
        for k, d in di.items():
            if "block" in d and (d.get("scale") not in di or di[d["scale"]]["bits"] != 8):
                raise IsaError(f"dtype_info {k}: a block format needs a 1-byte scale dtype")
        blocks = [r["block"] for r in mm.get("diectl", {}).values()]
        if len(set(blocks)) != len(blocks) or any(not 0 <= x < mm["regions"]["DIECTL"]["count"] for x in blocks):
            raise IsaError("memmap diectl: block indices must be unique and inside DIECTL")
    return True


# ---------------------------------------------------------------- encode / decode
def _mask(r):
    return (1 << width(r)) - 1


def encode(isa, name, **vals):
    ins = isa["instructions"][name]
    fields = fmt_fields(isa, ins["fmt"])
    ft = isa["field_types"]
    w = ins["op"] << OPCODE[1]
    for k, v in vals.items():
        if k not in fields:
            raise IsaError(f"{name}: no field {k}")
        r = fields[k]
        t = ft[k]
        if isinstance(v, str):
            if not t.startswith("enum:") and not (k == "func" and "func_enum" in ins):
                raise IsaError(f"{name}.{k}: symbolic value for a numeric field")
            en = t[5:] if t.startswith("enum:") else ins["func_enum"]
            v = isa["enums"][en][v]
        if t in ("sint",):
            lo, hi = -(1 << (width(r) - 1)), (1 << (width(r) - 1)) - 1
            if not lo <= v <= hi:
                raise IsaError(f"{name}.{k}={v} out of signed range")
            v &= _mask(r)
        elif not 0 <= v <= _mask(r):
            raise IsaError(f"{name}.{k}={v} does not fit {width(r)} bits")
        w |= v << r[1]
    return w


def decode(isa, word):
    op = (word >> OPCODE[1]) & 0xFF
    by_op = {i["op"]: n for n, i in isa["instructions"].items()}
    if op not in by_op:
        raise IsaError(f"illegal opcode 0x{op:02x}")
    name = by_op[op]
    ins = isa["instructions"][name]
    out = {}
    for k, r in fmt_fields(isa, ins["fmt"]).items():
        v = (word >> r[1]) & _mask(r)
        if isa["field_types"][k] == "sint" and v >> (width(r) - 1):
            v -= 1 << width(r)
        out[k] = v
    return name, out


def pack(isa, name, **vals):
    """Pack a layout (paged_ptr, noc_header, dma_list_entry). Enum fields take names. With a select field, only fields
    valid for the selected type may be given."""
    fields = layout_fields(isa, name)
    sel = isa[name].get("select")
    tname = None
    if sel:
        t = vals.get(sel, 0)
        en = isa["enums"][fields[sel]["enum"]]
        tname = t if isinstance(t, str) else {v: k for k, v in en.items()}[t]
        vals = dict(vals, **{sel: tname})
    w = 0
    for k, v in vals.items():
        if k not in fields:
            raise IsaError(f"{name}: no field {k}")
        f = fields[k]
        if tname is not None and f["types"] is not None and tname not in f["types"]:
            raise IsaError(f"{name}: field {k} is not valid for {tname}")
        if isinstance(v, str):
            if not f["enum"]:
                raise IsaError(f"{name}.{k}: symbolic value for a numeric field")
            v = isa["enums"][f["enum"]][v]
        if not 0 <= v <= _mask(f["bits"]):
            raise IsaError(f"{name}.{k}={v} does not fit {width(f['bits'])} bits")
        w |= v << f["bits"][1]
    return w


def unpack(isa, name, word):
    """Inverse of pack: the fields valid for the word's type (all fields if the layout has no select field)."""
    fields = layout_fields(isa, name)
    sel = isa[name].get("select")
    tname = None
    if sel:
        en = isa["enums"][fields[sel]["enum"]]
        code = (word >> fields[sel]["bits"][1]) & _mask(fields[sel]["bits"])
        tname = {v: k for k, v in en.items()}.get(code)
        if tname is None:
            raise IsaError(f"{name}: unknown type code {code}")
    out = {}
    for k, f in fields.items():
        if tname is not None and f["types"] is not None and tname not in f["types"]:
            continue
        out[k] = (word >> f["bits"][1]) & _mask(f["bits"])
    return out


def spm_bank(addr):
    """SPM bank of a byte offset (memmap.md section 4): addr[10:6] ^ addr[15:11]."""
    return ((addr >> 6) & 31) ^ ((addr >> 11) & 31)


def sem_ready(value, target, bits=32):
    """Semaphore wait condition (isa.md section 3): value has reached target in serial-number order, i.e.
    (int32)(value - target) >= 0. Correct across wrap-around while |value - target| < 2^31."""
    d = (value - target) & ((1 << bits) - 1)
    return d < 1 << (bits - 1)


def paged_addr(isa, td, ptr, frames, r_off, i0=0, i2=0, i3=0):
    """Reference for memmap.md section 6: (byte address, scale byte address or None) of element (i0, r_off, i2, i3) of a
    PAGED TD. td and ptr are unpacked TD and paged_ptr fields; frames maps a page number to its PTE frame; r_off is i1
    (DMA_COPY) or index[i] (gather / scatter). A SCALE_REGION TD addresses the scale rows themselves (no scale address).
    The scale is located from the whole in-row offset (dims 0, 2, 3 and the pointer's byte; review finding V-4)."""
    dt = {v: k for k, v in isa["enums"]["dtype"].items()}[td["dtype"]]
    info = isa["dtype_info"][dt]
    region = td["flags"] >> isa["enums"]["td_flags"]["SCALE_REGION"] & 1
    if td["flags"] >> isa["enums"]["td_flags"]["SCALE_INLINE"] & 1:
        raise IsaError("ILLEGAL_FIELD: a PAGED operand keeps its scales in the page's scale region, not inline")
    if region or "block" in info:
        # review W-4 (c): a page's scale rows tile whole aligned 32 B blocks, so a tail block never spans two pages
        a = td["aux0"]
        if not a or (32 % a and a % 32) or (a << td["page_shift"]) < 32:
            raise IsaError("ILLEGAL_FIELD: aux0 must divide 32 or be a multiple of it, and rows per page >= 32 / aux0")
        if "block" in info and (8 * td["s1"]) % (info["block"] * info["bits"]):
            raise IsaError("ILLEGAL_FIELD: s1 must be a multiple of the block's bytes")      # review W-5
    r = ptr["row"] + r_off
    page, q = r >> td["page_shift"], r & ((1 << td["page_shift"]) - 1)
    start = frames[page] * 4096
    sc_base = start + td["sc_off"] * 256 + q * td["aux0"]
    o_bits = 8 * (ptr["byte"] + i2 * td["s2"] + i3 * td["s3"]) + i0 * info["bits"]
    row_len = td["aux0"] if region else td["s1"]
    if o_bits + info["bits"] > 8 * row_len:
        raise IsaError("RANGE: dims 0, 2, 3 leave the row")
    if region:
        return sc_base + o_bits // 8, None
    if "block" not in info:
        return start + q * td["s1"] + o_bits // 8, None
    blk_bits = info["block"] * info["bits"]
    if (8 * (ptr["byte"] + i2 * td["s2"] + i3 * td["s3"])) % blk_bits:
        raise IsaError("ILLEGAL_FIELD: byte, s2, s3 must be multiples of the block's bytes")
    return start + q * td["s1"] + o_bits // 8, sc_base + o_bits // blk_bits


def block_addr(isa, td, base, scale, i0=0, i1=0, i2=0, i3=0):
    """Reference for a non-paged operand (memmap.md section 6, review findings V-4, W-5): (byte address, scale byte address
    or None) of element (i0, i1, i2, i3). base = R[src_reg] or R[dst_reg], scale = R[scale_reg], the address of the scale
    of the base element's block in the operand's scale array (row stride TD aux0). Block formats need base, s1, s2 and s3
    to be multiples of the block's bytes (ILLEGAL_FIELD), so the base element starts a block and o has no in-block part.
    SCALE_INLINE operands keep their scales in the row and have no such rule: see inline_addr (review Z-4)."""
    if td["flags"] >> isa["enums"]["td_flags"]["SCALE_INLINE"] & 1:
        return inline_addr(isa, td, base, i0, i1)                     # review Z-4: no block-aligned base or s1
    dt = {v: k for k, v in isa["enums"]["dtype"].items()}[td["dtype"]]
    info = isa["dtype_info"][dt]
    o_bits = 8 * (i2 * td["s2"] + i3 * td["s3"]) + i0 * info["bits"]
    addr = base + i1 * td["s1"] + o_bits // 8
    if "block" not in info:
        return addr, None
    blk_bits = info["block"] * info["bits"]
    if any((8 * v) % blk_bits for v in (base, td["s1"], td["s2"], td["s3"])):
        raise IsaError("ILLEGAL_FIELD: base, s1, s2, s3 must be multiples of the block's bytes")
    return addr, scale + i1 * td["aux0"] + o_bits // blk_bits


def inline_row_bytes(isa, td):
    """Bytes of one SCALE_INLINE row (memmap.md section 6, review Z-4): n0 elements, then their n0 / block scales."""
    info = isa["dtype_info"][{v: k for k, v in isa["enums"]["dtype"].items()}[td["dtype"]]]
    return td["n0"] * info["bits"] // 8 + td["n0"] // info["block"]


def inline_addr(isa, td, base, i0=0, i1=0):
    """Reference for a SCALE_INLINE operand (memmap.md section 6, review finding Z-4): (byte address, scale byte address)
    of element i0 of row i1. A row is n0 elements followed by their n0 / block one-byte scales (an Engram MXFP8 row:
    256 B + 8 B = 264 B), so element i0's scale is at the row start + n0 * esize + floor(i0 / block). The row starts at
    base + i1 * s1 (DMA_COPY) or base + index[i] * s1 (gather; pass i1 = index[i]); base and s1 need no alignment, because
    every row starts its own first block. ILLEGAL_FIELD: not a block format, PAGED, n2 or n3 > 1 (a row is one dim-0 run),
    n0 not whole blocks, s1 shorter than the row, or a nonzero row_bytes other than the row's size."""
    dt = {v: k for k, v in isa["enums"]["dtype"].items()}[td["dtype"]]
    info = isa["dtype_info"][dt]
    fl = isa["enums"]["td_flags"]
    if "block" not in info or td["flags"] >> fl["PAGED"] & 1:
        raise IsaError("ILLEGAL_FIELD: SCALE_INLINE needs a non-paged block format")
    if max(td["n2"], 1) > 1 or max(td["n3"], 1) > 1 or not td["n0"] or td["n0"] % info["block"]:
        raise IsaError("ILLEGAL_FIELD: a SCALE_INLINE row is one dim-0 run of whole blocks")
    rb = inline_row_bytes(isa, td)
    if td["s1"] < rb or td["row_bytes"] not in (0, rb):
        raise IsaError("ILLEGAL_FIELD: s1 and row_bytes must fit the inline row (n0 elements + n0 / block scales)")
    if not 0 <= i0 < td["n0"]:
        raise IsaError("RANGE: i0 outside the row")
    row = base + i1 * td["s1"]
    return row + i0 * info["bits"] // 8, row + td["n0"] * info["bits"] // 8 + i0 // info["block"]


def write_packets(runs, granule=256):
    """Reference for the DMA's write packets (isa.yaml DMA_COPY, review finding V-5): runs are (address, bytes) in the
    destination TD's order; adjacent runs merge, and packets never cross a `granule` boundary. Returns (address, bytes)."""
    merged = []
    for a, n in runs:
        if merged and merged[-1][0] + merged[-1][1] == a:
            merged[-1][1] += n
        else:
            merged.append([a, n])
    out = []
    for a, n in merged:
        while n:
            k = min(n, granule - a % granule)
            out.append((a, k))
            a, n = a + k, n - k
    return out


class _Ring:
    """Circular SPM ring with in-order reclaim: space comes back only when the oldest allocation is freed. An allocation
    is contiguous; if it does not fit before the end, it starts at 0 and the end is left unused until then."""

    def __init__(self, size, unit):
        self.size, self.unit = size, unit
        self.allocs = []            # [start, bytes, freed], oldest first
        self.used = 0

    def _place(self, n, allocs):
        if n > self.size:
            return None
        if not allocs:
            return 0
        head, last = allocs[0][0], allocs[-1]
        tail = last[0] + last[1]
        if last[0] >= head:         # not wrapped: free at the end, then before the head
            if self.size - tail >= n:
                return tail
            return 0 if head >= n else None
        return tail if head - tail >= n else None

    def fits(self, *sizes):
        """Would these allocations, in order, all fit now? (_place needs only the oldest and the newest allocation.)"""
        ends = [self.allocs[0], self.allocs[-1]] if self.allocs else []
        for n in sizes:
            n = -(-n // self.unit) * self.unit
            s = self._place(n, ends)
            if s is None:
                return False
            ends = [ends[0] if ends else [s, n, False], [s, n, False]]
        return True

    def alloc(self, n):
        n = -(-n // self.unit) * self.unit
        s = self._place(n, self.allocs)
        if s is None:
            return None
        a = [s, n, False]
        self.allocs.append(a)
        self.used += n
        return a

    def free(self, a):
        a[2] = True
        while self.allocs and self.allocs[0][2]:
            self.used -= self.allocs.pop(0)[1]


RING_MIN = 2048          # smallest DMA_STAGE size for block-format conversion (review Z-3); below it ILLEGAL_FIELD
PACE_DEFAULT = 72 * 1024  # DMA_PACE reset value: bytes of converting reads in flight (review Z-2)
PACE_WEIGHT = {"NVFP4": 1.0, "MXFP4": 0.5, "MXFP8": 0.25}   # pace="format": bytes counted by format (review Y-2)


def _granule_pieces(lo, hi, granule=256):
    """(granule index, bytes) of every granule that the byte range [lo, hi) touches."""
    out = []
    while lo < hi:
        g = lo // granule
        b = min(hi, (g + 1) * granule)
        out.append((g, b - lo))
        lo = b
    return out


def _ring_reads(stream, boundary):
    """Data reads of one DQ's entries. Per entry a list of reads [granule or None, bytes, parts, deps]: parts = [(entry, bytes)]
    that the response delivers, deps = the entries whose scales must be in the ring before it is issued."""
    if not stream:
        return []
    if len(stream[0]) == 5:                                   # no addresses: `reads` equal reads, each in its own entry
        return [[[None, e[3] / e[2], [(i, e[3] / e[2])], (i,)] for _ in range(e[2])] for i, e in enumerate(stream)]
    pieces = [_granule_pieces(e[5], e[5] + e[3]) for e in stream]
    plans = [[] for _ in stream]
    last = None
    for i, ps in enumerate(pieces):
        for j, (g, b) in enumerate(ps):
            if boundary == "shared" and j == 0 and last is not None and last[0] == g and last[3][-1] == i - 1:
                last[1] += b                                  # one read of a granule two entries share (rejected, Z-1)
                last[2].append((i, b))
                last[3] += (i,)
            else:                                             # "cut": the shared granule is two partial reads
                last = [g, b, [(i, b)], (i,)]
                plans[i].append(last)
    return plans


def staging_ring(streams, ring=16384, rule="atomic", rt=500, share=87.0, conv=256, tags=512, unit=16, max_req=8,
                 boundary="cut", hbm="paced", inflight=None, sizing="rate", arb="fair", rsp="granule", fmt=None,
                 pace="format", pace_idle=True, ring_turn=True, ring_q_bytes=256):
    """Reference for the DMA staging ring (microarch.md section 2.5, review findings V-1, W-1, Z-1 to Z-3, Z-8, Y-1 to Y-3, Y-6): a cycle
    model of one core's inbound conversion path that tracks only what the ring allocation rule decides.

    streams: one list per DQ of entries in issue order. An entry is the scale reads of a run of whole conversion units and
    the data reads of those units: (scale, raw, reads, data, elems[, addr]) = bytes of out-of-line block scales (0: inline
    or none, no scale read), bytes of split units staged raw (0: every unit converts from its own response), data
    responses, data bytes, elements converted, and optionally the HBM byte address of the entry's first data byte (its
    data is contiguous). With addr, `reads` is ignored: the reads are the granule pieces of [addr, addr + data), grouped
    into requests inside one 2 KiB block. An entry with raw > 0 is converted from the ring once all its data has landed.
    `tags` counts granule credits (review Y-3: a request takes one tag, from a 512-entry table that never binds first, and
    one credit per granule): a scale read takes ceil(scale / 256) (review Z-8, scale reads assumed aligned); an entry whose
    scales cross a 2 KiB block is several scale reads, allocated together, which the model counts as one request.
    rule, when ring space is taken:
      "atomic"  (the rule, W-1): the scales and every raw slot of the entry, one allocation, when its scale read is
                issued (when its first data read is issued if it has no scale read). Its data reads need only tags.
      "split"   (the text before W-1): the scales at scale-read issue, the raw slots at the first data read.
      "capped"  (rejected): as "split", but a scale read is issued only if the largest raw allocation of any entry
                still fits after it.
    boundary (entries with addresses only), what a granule holding the end of one entry and the start of the next is:
      "cut"     (the rule, Z-1): two partial reads, one in each entry. Every data read belongs to exactly one entry.
      "shared"  (rejected): one read, owned by the older entry and issued once the scales of both are in the ring.
    hbm, how the core's HBM share is spent:
      "paced"   the share is an issue budget (`share` B/cycle) and every response returns exactly `rt` cycles after issue.
      "server"  (delivery-limited, Z-2): requests issue greedily (tags, `inflight`) and reach a FIFO server of `share`
                B/cycle half a round trip later; a response returns half a round trip after the server finishes it. So rt
                (plus the request's service time) is the minimum round trip, and the core's own queue adds to it.
    rsp ("server" only): "granule" (the rule: one RD_RSP per granule piece, served in order, each freeing one credit) or
    "request" (the model before Y-3: a request's responses return together).
    inflight: DMA_PACE, the most bytes of converting reads in flight (scale and data reads of entries that use the ring;
    review Z-2; None: tags only). Plain reads (no scales, no raw slot) are limited by tags only. pace:
      "format"  (the rule, Y-2): bytes are weighted by the DQ's format (fmt, a list of PACE_WEIGHT keys per DQ; None:
                NVFP4), and the limit is min(inflight, 4.5 x ring).
      "flat"    (the Z-2 rule): bytes in full, no cap.
    pace_idle: a converting read may issue when none is in flight (the rule, Y-6); False is the literal limit, which
    never issues a read larger than a nonzero limit.
    sizing, the DMA's entry-size rules (rate rules; liveness needs only entry <= ring):
      "rate"    (the rule, Y-1): an entry without raw slots covers at most one data request (data <= 2 KiB, and with an
                address inside one 2 KiB block); an entry with raw slots is at most a quarter of the ring.
      "quarter" (the Z-3 rule): every entry at most a quarter of the ring.
      "ring"    every entry at most the ring.
    The ring must be at least RING_MIN bytes.
    arb, how the DQs share the request issue (review Y-3):
      "fair"    (the rule): the round-robin winner is a DQ whose next request is ready (its scales landed, or its ring
                space can be taken); its request is formed whole, and the DQ keeps the turn until it has taken max_req
                credits, holding it while its request waits for credits or pace. Ring space is never held for.
      "hold"    (rejected, the fourth verifier's proposal): as "fair", but the turn passes after one request, so
                multi-credit requests starve one-credit ones.
      "greedy"  (rejected): a DQ whose request lacks credits or pace passes the turn, so one-credit requests starve
                multi-credit ones.
      "shrink"  (rejected; the model before Y-3): a data request shrinks to the free credits and pace.
    ring_turn (the rule, Y-3): DQs that cannot start their next entry queue for the ring, and the head allocates for
    ring_q_bytes of ring before the turn passes; data reads of allocated entries still issue. False: whoever fits
    allocates, so small entries can starve large ones.
    Per cycle at most one request is issued, round-robin over the DQs that have one ready. Within its turn a DQ reads
    scales ahead as far as the ring allows (greedy read-ahead: its scale read goes before its own data reads whenever its
    space can be taken), and otherwise issues its oldest entry's data reads, in order, once their scales have landed (at
    most max_req granules per request). Responses free their tags on arrival (they sink unconditionally), the converter
    takes `conv` elements per cycle oldest first, and an entry's space is freed once all its elements are converted.
    Returns dict(done, cycle, rate, bw, bw_dq, deadlock, peak, peak_infl): rate is elements per cycle and bw delivered
    data bytes per cycle, between 25% and 75% of the elements; bw_dq is per DQ, over the part of that window in which every
    DQ still has data to receive (where the streams overlap); deadlock is the cycle of the last progress if
    the model stops with work left; peak_infl is the largest weighted pace count."""
    if rule not in ("atomic", "split", "capped") or boundary not in ("cut", "shared") or hbm not in ("paced", "server") \
            or sizing not in ("rate", "quarter", "ring") or arb not in ("shrink", "greedy", "hold", "fair") \
            or rsp not in ("request", "granule") or pace not in ("flat", "format"):
        raise ValueError((rule, boundary, hbm, sizing, arb, rsp, pace))
    if ring < RING_MIN:
        raise ValueError(f"a ring below {RING_MIN} B faults ILLEGAL_FIELD (review Z-3)")
    lim = ring if sizing == "ring" else ring // 4

    def rnd(n):
        return -(-n // unit) * unit

    for s in streams:
        for e in s:
            if rnd(e[0] + e[1]) > lim:
                raise ValueError(f"entry of {rnd(e[0] + e[1])} B above {lim} B (the DMA shortens it; review Z-3)")
            if sizing == "rate" and e[1] == 0 and e[0] and (e[3] > 2048 or
                                                            (len(e) > 5 and e[5] // 2048 != (e[5] + e[3] - 1) // 2048)):
                raise ValueError("an entry without raw slots covers at most one data request (review Y-1)")
    wts = [PACE_WEIGHT[f] if pace == "format" and f else 1.0 for f in (fmt or [None] * len(streams))]
    if inflight is not None and pace == "format":
        inflight = min(inflight, ring * 9 // 2)   # the hardware cap, 4.5 x DMA_STAGE (review Y-2): 72 KiB at 16 KiB
    rg = _Ring(ring, unit)
    rmax = max([e[1] for s in streams for e in s] + [0])
    total = sum(e[4] for s in streams for e in s)
    plans = [_ring_reads(s, boundary) for s in streams]
    nxt = [0] * len(streams)                 # per DQ: next entry to start
    started = [deque() for _ in streams]     # per DQ: started entries with data reads left, oldest first
    states = [dict() for _ in streams]       # per DQ: entry index -> state, from its start
    arrivals = {}                            # cycle -> list of (kind, tags, bytes, payload)
    convq = []                               # [entry state, elements ready]
    budget, free_tags, rr, infl, srv, hold, peak_infl, used = 0.0, tags, 0, 0, 0.0, None, 0, 0
    done_el, t, last, peak, got = 0, 0, 0, 0, 0
    got_dq = [0] * len(streams)
    ring_q = deque()                         # ring_turn: DQs waiting to allocate, in order
    ring_own = [None, 0]                     # ring_turn: the DQ holding the allocation turn, ring bytes it has taken
    dq_data = [sum(e[3] for e in st) for st in streams]
    first_done = None                        # (cycle, got_dq) when the first DQ received its last data byte
    marks = {}
    idle = rt + int(2 * max_req * 256 / share) + 4

    def new_state(d, i):
        e = streams[d][i]
        return dict(i=i, d=d, scale=e[0], raw=e[1], data=e[3], elems=e[4], ri=0, landed=0, conv=0, sc_ok=e[0] == 0,
                    allocs=[], raw_ok=e[1] == 0 or rule == "atomic")

    def ready(d, i):
        st = states[d].get(i)
        return st is not None and st["sc_ok"]

    def room(n, w):
        """Pace check for n bytes of a read of pace weight w (0: plain). A converting read may always issue when none is
        in flight (pace_idle, review Y-6); pace_idle=False is the literal 'at most DMA_PACE bytes', which hangs on a
        limit below one request."""
        return not w or inflight is None or (pace_idle and infl == 0) or infl + n * w <= inflight

    def _may_alloc(d, need):
        """Ring turn: a DQ that cannot start its next entry (it does not fit, or another DQ waits) joins a queue, and only
        the head allocates. Data reads still issue, and allocated entries need only tags, so the ring drains and the
        head's entry fits (entry <= ring)."""
        if not need:
            return True
        if not ring_turn:
            return rg.fits(need)
        own = ring_own[0]
        if (own == d or (own is None and (not ring_q or ring_q[0] == d))) and rg.fits(need):
            return True
        if own != d and d not in ring_q:
            ring_q.append(d)
        return False

    def _allocated(d, n):
        if ring_own[0] != d:
            ring_own[:] = [d, 0]
            if ring_q and ring_q[0] == d:
                ring_q.popleft()
        ring_own[1] += n
        if ring_own[1] >= ring_q_bytes:      # the turn passes after ring_q_bytes of ring
            ring_own[:] = [None, 0]

    def _ring_release(d):
        """The allocation turn passes when its holder has no entry left to allocate."""
        if ring_own[0] == d and (nxt[d] >= len(streams[d]) or not streams[d][nxt[d]][0] + streams[d][nxt[d]][1]):
            ring_own[:] = [None, 0]

    def _issue_whole(d):
        """arb 'greedy' / 'hold' (review Y-3): the DQ's next request is formed whole (scale read, or up to max_req granules
        of data in one 2 KiB block), never shrunk to the free tags or pace. Ring space is a readiness condition (a DQ
        whose next entry cannot be allocated offers its data reads instead); tags and pace are not. Returns the request,
        None (nothing ready) or 'hold' (ready, but tags or pace are short: 'hold' keeps the turn, 'greedy' passes it)."""
        cand = None
        if nxt[d] < len(streams[d]):
            e = streams[d][nxt[d]]
            need = e[0] + (e[1] if rule == "atomic" else 0)
            ok = (e[0] > 0 or not started[d]) and _may_alloc(d, need)
            if ok and need and rule == "capped" and e[0] and not rg.fits(e[0], rmax):
                ok = False
            if ok and e[0]:
                cand = ("scale", max(1, -(-e[0] // 256)), e[0], (e[0] + e[1] > 0) * wts[d])
            elif ok:                                 # no scale read: allocate at its turn, then offer its data read
                st2 = new_state(d, nxt[d])
                st2["allocs"] += [rg.alloc(need)] if need else []
                _allocated(d, need)
                states[d][nxt[d]] = st2
                nxt[d] += 1
                started[d].append(st2)
        if cand is None:
            while started[d] and started[d][0]["ri"] == len(plans[d][started[d][0]["i"]]):
                started[d].popleft()
            st = started[d][0] if started[d] else None
            if st is None or not st["sc_ok"]:
                return None
            if not st["raw_ok"]:
                a = rg.alloc(st["raw"])
                if a is None:
                    return None
                st["allocs"].append(a)
                st["raw_ok"] = True
            plan = plans[d][st["i"]]
            if not all(ready(d, x) for x in plan[st["ri"]][3]):
                return None
            blk = None if plan[st["ri"]][0] is None else plan[st["ri"]][0] // 8
            j, nb = st["ri"], 0
            while j < len(plan) and j - st["ri"] < max_req:
                g, b, parts, deps = plan[j]
                if j > st["ri"] and ((blk is not None and g // 8 != blk) or not all(ready(d, x) for x in deps)):
                    break
                nb, j = nb + b, j + 1
            cand = ("data", j - st["ri"], nb, (st["scale"] + st["raw"] > 0) * wts[d])
        kind, k, nb, w = cand
        if free_tags < k or not room(nb, w):
            return "hold" if arb in ("hold", "fair") else None
        if kind == "scale":
            e = streams[d][nxt[d]]
            st2 = new_state(d, nxt[d])
            need = e[0] + (e[1] if rule == "atomic" else 0)
            st2["allocs"].append(rg.alloc(need))
            _allocated(d, need)
            states[d][nxt[d]] = st2
            nxt[d] += 1
            started[d].append(st2)
            return ("scale", k, nb, st2, w, None)
        st = started[d][0]
        plan = plans[d][st["i"]]
        pcs = [(b, [(states[d][x], y) for x, y in parts]) for g, b, parts, deps in plan[st["ri"]:st["ri"] + k]]
        st["ri"] += k
        if st["ri"] == len(plan):
            started[d].popleft()
        return ("data", k, nb, [p for _, ps in pcs for p in ps], w, pcs)

    while done_el < total:
        t += 1
        if hbm == "paced":
            budget = min(budget + share, max_req * 256 + share)
        for kind, k, nb, pay, cv, _ in arrivals.pop(t, ()):
            free_tags += k
            infl -= nb * cv
            last = t
            if kind == "scale":
                pay["sc_ok"] = True
                continue
            got += nb
            for st, b in pay:
                got_dq[st["d"]] += b
                if first_done is None and dq_data[st["d"]] and got_dq[st["d"]] >= dq_data[st["d"]] - 1e-9:
                    first_done = (t, list(got_dq))
                st["landed"] += b
                if st["raw"] == 0:
                    convq.append([st, st["elems"] * b / st["data"]])
                elif st["landed"] >= st["data"] - 1e-9:
                    convq.append([st, st["elems"]])
        c = conv
        while c > 0 and convq:
            q = convq[0]
            n = min(c, q[1])
            q[1] -= n
            c -= n
            q[0]["conv"] += n
            done_el += n
            last = t
            if q[1] <= 1e-9:
                convq.pop(0)
            if q[0]["conv"] >= q[0]["elems"] - 1e-9:
                for a in q[0]["allocs"]:
                    rg.free(a)
                q[0]["allocs"] = []
        for f in (0.25, 0.75):
            if f not in marks and done_el >= f * total:
                marks[f] = (t, done_el, got, list(got_dq))
        order = [] if hbm == "paced" and budget <= 0 else [hold] if hold is not None else \
            [(rr + j) % len(streams) for j in range(len(streams))]
        for d in order:                                                           # issue: one request per cycle
            _ring_release(d)
            if arb == "shrink":
                req = None
                if nxt[d] < len(streams[d]):         # read ahead: start the next entry
                    e = streams[d][nxt[d]]
                    stags = -(-e[0] // 256)
                    need = e[0] + (e[1] if rule == "atomic" else 0)
                    ok = (e[0] > 0 or not started[d]) and free_tags >= max(1, stags) and \
                        room(e[0], (e[0] + e[1] > 0) * wts[d]) and _may_alloc(d, need)
                    if ok and need and rule == "capped" and e[0] and not rg.fits(e[0], rmax):
                        ok = False
                    a = rg.alloc(need) if ok and need else None
                    if a is not None:
                        _allocated(d, need)
                    if ok and (a is not None or not need):
                        st2 = new_state(d, nxt[d])
                        st2["allocs"] += [a] if a else []
                        states[d][nxt[d]] = st2
                        nxt[d] += 1
                        started[d].append(st2)
                        if e[0]:
                            req = ("scale", stags, e[0], st2, wts[d], None)
                while req is None and started[d] and started[d][0]["ri"] == len(plans[d][started[d][0]["i"]]):
                    started[d].popleft()            # an entry whose reads all belong to an older entry (shared)
                st = started[d][0] if started[d] else None
                if req is None and st is not None and st["sc_ok"] and free_tags > 0:
                    if not st["raw_ok"]:
                        a = rg.alloc(st["raw"])
                        if a is not None:
                            st["allocs"].append(a)
                            st["raw_ok"] = True
                    plan = plans[d][st["i"]]
                    cv = (st["scale"] + st["raw"] > 0) * wts[d]
                    if st["raw_ok"] and all(ready(d, x) for x in plan[st["ri"]][3]) and room(plan[st["ri"]][1], cv):
                        blk = None if plan[st["ri"]][0] is None else plan[st["ri"]][0] // 8
                        k, nb, pay, pcs = 0, 0, [], []
                        while st["ri"] < len(plan) and k < min(max_req, free_tags):
                            g, b, parts, deps = plan[st["ri"]]
                            if k and ((blk is not None and g // 8 != blk) or not all(ready(d, x) for x in deps)
                                      or not room(nb + b, cv)):
                                break               # a request stays in one 2 KiB block (one HBM leaf)
                            k, nb = k + 1, nb + b
                            pcs.append((b, [(states[d][x], y) for x, y in parts]))
                            pay += pcs[-1][1]
                            st["ri"] += 1
                        if st["ri"] == len(plan):
                            started[d].popleft()
                        req = ("data", k, nb, pay, cv, pcs)
            else:
                req = _issue_whole(d)
                if req == "hold":
                    hold = d
                    break
                if req is None:
                    if hold == d:
                        hold, used = None, 0
                    continue
                used += req[1]                  # "fair": the turn lasts until it has taken max_req credits
                hold = d if arb == "fair" and used < max_req else None
                used = used if hold is not None else 0
            if req is not None:
                kind, k, nb, pay, cv, pcs = req
                free_tags -= k
                infl += nb * cv
                peak_infl = max(peak_infl, infl)
                if hbm == "paced":
                    budget -= nb
                    arrivals.setdefault(t + rt, []).append(req)
                elif rsp == "granule" and kind == "data":   # one response per granule piece, one tag each (RD_RSP)
                    for b, ps in pcs:
                        srv = max(srv, t + rt / 2) + b / share
                        arrivals.setdefault(int(-(-(srv + rt / 2) // 1)), []).append(("data", 1, b, ps, cv, None))
                else:                           # the share serves requests in arrival order, half a round trip out
                    srv = max(srv, t + rt / 2) + nb / share
                    arrivals.setdefault(int(-(-(srv + rt / 2) // 1)), []).append(req)
                rr = (d + 1) % len(streams)
                last = t
                break
        peak = max(peak, rg.used)
        if t - last > idle and not arrivals:
            return dict(done=False, cycle=t, rate=0.0, bw=0.0, deadlock=last, peak=peak, peak_infl=peak_infl)
    (t0, e0, g0, q0), (t1, e1, g1, q1) = marks[0.25], marks[0.75]
    w = max(1, t1 - t0)
    if first_done is not None and t0 < first_done[0] < t1:  # per DQ: only while every DQ still has data to receive
        t2, q2 = first_done
    else:
        t2, q2 = t1, q1
    return dict(done=True, cycle=t, rate=(e1 - e0) / w, bw=(g1 - g0) / w, deadlock=None, peak=peak, peak_infl=peak_infl,
                bw_dq=[(b - a) / max(1, t2 - t0) for a, b in zip(q0, q2)])


def queue_of(isa, name, fields):
    q = isa["instructions"][name]["queue"]
    return f"DQ{fields['dq']}" if q == "DQ" else q


# ---------------------------------------------------------------- address decode (reference model for RTL and simulator)
def _bits(v, r):
    return (v >> r[1]) & ((1 << width(r)) - 1)


def decode_addr(isa, gpa, self_node=0):
    """Global address -> dict(node, region, index, offset[, hbm fields]). Raises IsaError on a hole."""
    mm = isa["memmap"]
    node = _bits(gpa, mm["node_field"])
    if node == mm["node_self"]:
        node = self_node
    elif node >= mm["max_nodes"]:
        raise IsaError(f"node {node} does not exist")
    off = gpa & ((1 << mm["node_field"][1]) - 1)
    for name, r in mm["regions"].items():
        span = r.get("stride", r["size"]) * r.get("count", 1)
        if r["base"] <= off < r["base"] + span:
            if name == "HBM_RSV":
                raise IsaError(f"address 0x{gpa:x} is in reserved HBM space")
            rel = off - r["base"]
            idx, o = divmod(rel, r["stride"]) if "stride" in r else (0, rel)
            if o >= r["size"]:
                raise IsaError(f"address 0x{gpa:x} is in a hole of {name}")
            out = {"node": node, "region": name, "index": idx, "offset": o}
            if name == "HBM":
                out.update(hbm_fields(isa, o))
            return out
    raise IsaError(f"address 0x{gpa:x} is unmapped")


def hbm_fields(isa, off):
    """Die-local HBM byte offset -> stack, sid, pc, bg, ba, row, col (32 B burst index), leaf (memmap.md section 3)."""
    h = isa["memmap"]["hbm"]
    f = {k: tuple(v) for k, v in h["fields"].items()}
    stack, so = divmod(off, h["stack_bytes"])
    sid, p = divmod(so, h["sid_bytes"])
    row = _bits(p, f["row"])
    pc = _bits(p, f["pc"]) ^ ((p >> 13) & 0x1F) ^ (row & 0x1F)
    bg = _bits(p, f["bg"]) ^ ((row >> 5) & 0x3)
    ba = _bits(p, f["ba"]) ^ ((row >> 7) & 0x3)
    col = (_bits(p, f["col_hi"]) << 3) | _bits(p, f["col_lo"])
    lf = h["leaf_of_pc"]
    return {"stack": stack, "sid": sid, "pc": pc, "bg": bg, "ba": ba, "row": row, "col": col,
            "leaf": 4 * stack + ((pc >> lf[1]) & ((1 << width(lf)) - 1))}


# ---------------------------------------------------------------- emitters
HDR = "generated by isa/isa.py from isa/isa.yaml; do not edit"


def _id(s):
    return s.upper().replace("-", "_")


def emit_sv(isa):
    L = [f"// {HDR}", "/* verilator lint_off UNUSEDPARAM */", "package isa_pkg;",
         f"  localparam int ISA_WORD_BITS = {isa['word_bits']};"]
    for k, v in isa["machine"].items():
        if isinstance(v, int):
            L.append(f"  localparam int ISA_{_id(k)} = {v};")
    L.append("  // opcodes")
    for n, i in sorted(isa["instructions"].items(), key=lambda x: x[1]["op"]):
        L.append(f"  localparam logic [7:0] OP_{n} = 8'h{i['op']:02X};")
    L.append("  // queues")
    for n, q in isa["queues"].items():
        L.append(f"  localparam int Q_{n} = {q['id']};")
    L.append("  // field positions: <FORMAT>_<FIELD>_MSB / _LSB")
    L.append(f"  localparam int OPCODE_MSB = {OPCODE[0]};")
    L.append(f"  localparam int OPCODE_LSB = {OPCODE[1]};")
    for k, (m, l) in isa["sync"].items():
        L.append(f"  localparam int SYNC_{_id(k)}_MSB = {m};")
        L.append(f"  localparam int SYNC_{_id(k)}_LSB = {l};")
    for fmt, f in isa["formats"].items():
        items = (f or {}).get("fields", f or {}) if f else {}
        for k, (m, l) in items.items():
            L.append(f"  localparam int {fmt}_{_id(k)}_MSB = {m};")
            L.append(f"  localparam int {fmt}_{_id(k)}_LSB = {l};")
    for k, (m, l) in isa["td"]["fields"].items():
        L.append(f"  localparam int TD_{_id(k)}_MSB = {m};")
        L.append(f"  localparam int TD_{_id(k)}_LSB = {l};")
    for lay in LAYOUTS:
        if lay in isa:
            L.append(f"  // {lay}")
            L.append(f"  localparam int {_id(lay)}_BITS = {isa[lay]['bits']};")
            for k, f in layout_fields(isa, lay).items():
                L.append(f"  localparam int {_id(lay)}_{_id(k)}_MSB = {f['bits'][0]};")
                L.append(f"  localparam int {_id(lay)}_{_id(k)}_LSB = {f['bits'][1]};")
    L.append("  // enums")
    for e, vals in isa["enums"].items():
        bits = max(1, max(vals.values()).bit_length())
        for n, v in vals.items():
            L.append(f"  localparam logic [{bits - 1}:0] {_id(e)}_{n} = {bits}'d{v};")
    mm = isa.get("memmap")
    if mm:
        L.append("  // memory map (die-local offsets)")
        L.append(f"  localparam int GPA_BITS = {mm['gpa_bits']};")
        L.append(f"  localparam int NODE_MSB = {mm['node_field'][0]};")
        L.append(f"  localparam int NODE_LSB = {mm['node_field'][1]};")
        L.append(f"  localparam logic [6:0] NODE_SELF = 7'h{mm['node_self']:02X};")
        for n, r in mm["regions"].items():
            L.append(f"  localparam logic [41:0] MM_{n}_BASE = 42'h{r['base']:X};")
            L.append(f"  localparam logic [41:0] MM_{n}_SIZE = 42'h{r['size']:X};")
            if "stride" in r:
                L.append(f"  localparam logic [41:0] MM_{n}_STRIDE = 42'h{r['stride']:X};")
        for n, r in mm["corectl"].items():
            L.append(f"  localparam logic [19:0] CORECTL_{n} = 20'h{r['offset']:X};")
        for n, r in mm.get("diectl", {}).items():
            L.append(f"  localparam int DIECTL_{n} = {r['block']};")
        h = mm["hbm"]
        L.append(f"  localparam logic [41:0] HBM_STACK_BYTES = 42'h{h['stack_bytes']:X};")
        L.append(f"  localparam logic [41:0] HBM_SID_BYTES = 42'h{h['sid_bytes']:X};")
        for k, (m, l) in h["fields"].items():
            L.append(f"  localparam int HBM_{_id(k)}_MSB = {m};")
            L.append(f"  localparam int HBM_{_id(k)}_LSB = {l};")
    L.append("endpackage")
    L.append("/* verilator lint_on UNUSEDPARAM */")
    return "\n".join(L) + "\n"


def emit_h(isa):
    L = [f"// {HDR}", "#pragma once", "#include <cstdint>", "", "namespace npu::isa {", "",
         "struct Field { int msb, lsb; };", f"constexpr int kWordBits = {isa['word_bits']};"]
    for k, v in isa["machine"].items():
        if isinstance(v, int):
            L.append(f"constexpr int64_t k_{k} = {v};")
    L.append("")
    L.append("enum class Op : uint8_t {")
    for n, i in sorted(isa["instructions"].items(), key=lambda x: x[1]["op"]):
        L.append(f"  {n} = 0x{i['op']:02X},")
    L.append("};")
    L.append("enum class Queue : uint8_t {")
    for n, q in isa["queues"].items():
        L.append(f"  {n} = {q['id']},")
    L.append("};")
    L.append(f"constexpr Field kOpcode{{{OPCODE[0]}, {OPCODE[1]}}};")
    for k, (m, l) in isa["sync"].items():
        L.append(f"constexpr Field kSync_{k}{{{m}, {l}}};")
    for fmt, f in isa["formats"].items():
        items = (f or {}).get("fields", f or {}) if f else {}
        for k, (m, l) in items.items():
            L.append(f"constexpr Field k{fmt}_{k}{{{m}, {l}}};")
    for k, (m, l) in isa["td"]["fields"].items():
        L.append(f"constexpr Field kTd_{k}{{{m}, {l}}};")
    for lay in LAYOUTS:
        if lay in isa:
            cam = "".join(p.capitalize() for p in lay.split("_"))
            L.append(f"constexpr int k{cam}Bits = {isa[lay]['bits']};")
            for k, f in layout_fields(isa, lay).items():
                L.append(f"constexpr Field k{cam}_{k}{{{f['bits'][0]}, {f['bits'][1]}}};")
    for e, vals in isa["enums"].items():
        L.append(f"enum class {''.join(p.capitalize() for p in e.split('_'))} : uint16_t {{")
        for n, v in vals.items():
            L.append(f"  {n} = {v},")
        L.append("};")
    mm = isa.get("memmap")
    if mm:
        L.append(f"constexpr uint8_t kNodeSelf = 0x{mm['node_self']:02X};")
        L.append(f"constexpr Field kNode{{{mm['node_field'][0]}, {mm['node_field'][1]}}};")
        for n, r in mm["regions"].items():
            L.append(f"constexpr uint64_t kMm{n.capitalize()}Base = 0x{r['base']:X}ull;")
            L.append(f"constexpr uint64_t kMm{n.capitalize()}Size = 0x{r['size']:X}ull;")
            if "stride" in r:
                L.append(f"constexpr uint64_t kMm{n.capitalize()}Stride = 0x{r['stride']:X}ull;")
        for n, r in mm["corectl"].items():
            L.append(f"constexpr uint64_t kCorectl{n.capitalize()} = 0x{r['offset']:X}ull;")
        for n, r in mm.get("diectl", {}).items():
            L.append(f"constexpr int kDiectl{n.capitalize()} = {r['block']};")
        h = mm["hbm"]
        L.append(f"constexpr uint64_t kHbmStackBytes = 0x{h['stack_bytes']:X}ull;")
        L.append(f"constexpr uint64_t kHbmSidBytes = 0x{h['sid_bytes']:X}ull;")
        for k, (m, l) in h["fields"].items():
            L.append(f"constexpr Field kHbm_{k}{{{m}, {l}}};")
    L += ["", "}  // namespace npu::isa", ""]
    return "\n".join(L)


def emit_py(isa):
    """Plain-Python constants (no YAML needed): what the compiler, simulator and cocotb tests import."""
    import pprint
    pick = {"VERSION": isa["version"], "WORD_BITS": isa["word_bits"], "OPCODE": list(OPCODE),
            "MACHINE": isa["machine"], "QUEUES": {n: q["id"] for n, q in isa["queues"].items()},
            "OPCODES": {n: i["op"] for n, i in sorted(isa["instructions"].items(), key=lambda x: x[1]["op"])},
            "SYNC": {k: list(v) for k, v in isa["sync"].items()},
            "FORMATS": {fmt: {k: list(v) for k, v in fmt_fields(isa, fmt).items()} for fmt in isa["formats"]},
            "TD_FIELDS": {k: list(v) for k, v in isa["td"]["fields"].items()},
            "LAYOUTS": {lay: {"bits": isa[lay]["bits"], "select": isa[lay].get("select"),
                              "fields": {k: {"bits": list(f["bits"]), "types": sorted(f["types"]) if f["types"] else None,
                                             "enum": f["enum"]} for k, f in layout_fields(isa, lay).items()}}
                        for lay in LAYOUTS if lay in isa},
            "ENUMS": isa["enums"], "MEMMAP": isa.get("memmap", {})}
    L = [f"# {HDR}", f'"""ISA v{isa['version']} constants generated from isa/isa.yaml (docs/spec/isa_ref.md)."""', ""]
    for k, v in pick.items():
        L.append(f"{k} = " + pprint.pformat(v, width=120, sort_dicts=False))
        L.append("")
    return "\n".join(L)


def emit_md(isa):
    L = [f"# ISA reference (v{isa['version']})", "", f"<!-- {HDR} -->", "",
         "Generated from `isa/isa.yaml`; the programming model is in [isa.md](isa.md). "
         f"Every instruction is one {isa['word_bits']}-bit word with the opcode in bits [127:120].", ""]
    by_q = {}
    for n, i in isa["instructions"].items():
        by_q.setdefault(i["queue"], []).append((n, i))
    names = {"FE": "Front end", "DQ": "DMA (queue DQ0-DQ3 from the dq field)", "MQ": "MXU", "VQ": "VPU / SFU / top-k"}
    for q in ("FE", "DQ", "MQ", "VQ"):
        L += [f"## {names[q]}", "", "| Opcode | Name | Format | Description |", "|---|---|---|---|"]
        for n, i in sorted(by_q.get(q, []), key=lambda x: x[1]["op"]):
            extra = ""
            if "func_enum" in i:
                extra += f" `func` = `{i['func_enum']}`."
            if "imm_fields" in i:
                extra += " imm: " + ", ".join(f"`{k}` [{v[0]}:{v[1]}]" for k, v in i["imm_fields"].items()) + "."
            d = " ".join(i["desc"].split()).replace("|", "\\|")
            L.append(f"| 0x{i['op']:02X} | `{n}` | {i['fmt']} | {d}{extra} |")
        L.append("")
    L += ["## Formats", "", "Tile formats (T_) carry the sync block: " + ", ".join(f"`{k}` [{m}:{l}]" for k, (m, l) in isa["sync"].items()) + ".", "",
          "| Format | Fields |", "|---|---|"]
    for fmt, f in isa["formats"].items():
        fields = (f or {}).get("fields", f or {}) if f else {}
        L.append(f"| {fmt} | " + (", ".join(f"`{k}` [{m}:{l}]" for k, (m, l) in sorted(fields.items(), key=lambda x: -x[1][0])) or "(opcode only)") + " |")
    L += ["", "## Tile descriptor (DTAB entry, 256 bits)", "", "| Field | Bits |", "|---|---|"]
    for k, (m, l) in isa["td"]["fields"].items():
        L.append(f"| `{k}` | [{m}:{l}] |")
    titles = {"paged_ptr": "Paged pointer (base register of a PAGED operand, memmap.md section 6)",
              "noc_header": "NoC packet header (head-flit sideband, noc.md section 2)",
              "dma_list_entry": "DMA_LIST entry (32 B in SPM, isa.md section 5)"}
    for lay in LAYOUTS:
        if lay not in isa:
            continue
        fields = layout_fields(isa, lay)
        sel = isa[lay].get("select")
        L += ["", f"## {titles.get(lay, lay)}, {isa[lay]['bits']} bits", ""]
        if sel:
            L += [f"`{sel}` selects the layout; a field marked with types is valid only for those types, so fields of disjoint "
                  "type sets share bits.", "", "| Field | Bits | Valid for |", "|---|---|---|"]
        else:
            L += ["| Field | Bits |", "|---|---|"]
        for k, f in sorted(fields.items(), key=lambda x: (x[1]["bits"][1], x[0])):
            ext = f" (`{f['enum']}`)" if f["enum"] else ""
            if sel:
                L.append(f"| `{k}`{ext} | [{f['bits'][0]}:{f['bits'][1]}] | {', '.join(sorted(f['types'])) if f['types'] else 'all'} |")
            else:
                L.append(f"| `{k}`{ext} | [{f['bits'][0]}:{f['bits'][1]}] |")
    L += ["", "## Enumerations", ""]
    for e, vals in isa["enums"].items():
        L.append(f"- `{e}`: " + ", ".join(f"{n} = {v}" for n, v in vals.items()))
    L.append("")
    return "\n".join(L)


def write_all(isa):
    gen = os.path.join(HERE, "gen")
    os.makedirs(gen, exist_ok=True)
    outs = {os.path.join(gen, "isa_pkg.sv"): emit_sv(isa), os.path.join(gen, "isa.h"): emit_h(isa),
            os.path.join(gen, "isa_defs.py"): emit_py(isa),
            os.path.join(ROOT, "docs", "spec", "isa_ref.md"): emit_md(isa)}
    for p, s in outs.items():
        with open(p, "w") as f:
            f.write(s)
    return list(outs)


if __name__ == "__main__":
    isa = load()
    n = len(isa["instructions"])
    print(f"isa.yaml v{isa['version']}: {n} instructions, {len(isa['formats'])} formats, {len(isa['enums'])} enums: valid")
    if "--check" not in sys.argv:
        for p in write_all(isa):
            print("wrote", os.path.relpath(p, ROOT))
