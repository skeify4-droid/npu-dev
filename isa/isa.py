"""ISA tooling: load and validate isa/isa.yaml, encode / decode instruction words, emit SV / C++ / Markdown.

    python isa/isa.py            # validate and regenerate isa/gen/isa_pkg.sv, isa/gen/isa.h, isa/gen/isa_defs.py,
                                 # docs/spec/isa_ref.md
    python isa/isa.py --check    # validate only
"""
import os
import sys

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
