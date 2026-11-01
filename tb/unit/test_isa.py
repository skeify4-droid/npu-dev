"""isa/isa.yaml: validation, encode/decode round trip, generated files up to date, address map (docs/spec/isa.md, memmap.md)."""
import copy
import os
import random
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "isa"))
import isa as I  # noqa: E402


@pytest.fixture(scope="module")
def isa():
    return I.load()


def test_valid_and_complete(isa):
    names = set(isa["instructions"])
    plan = {"DMA_COPY", "DMA_GATHER", "DMA_SCATTER", "MXU_MM", "VEW", "VTOPK", "SWAIT", "SIGNAL", "DMA_LIST",
            "HC_MIX", "HC_SINKHORN", "HC_PRE", "HC_POST", "VROUTE", "VSOFTMAX", "VQUANT", "VTRINV"}
    assert plan <= names
    assert len(names) >= 50


def _random_value(isa, ins, k, r, rng):
    t = isa["field_types"][k]
    w = I.width(r)
    if t == "sint":
        return rng.randint(-(1 << (w - 1)), (1 << (w - 1)) - 1)
    if t.startswith("enum:"):
        return rng.choice(list(isa["enums"][t[5:]].values()))
    if k == "func" and "func_enum" in ins:
        return rng.choice(list(isa["enums"][ins["func_enum"]].values()))
    return rng.randint(0, (1 << w) - 1)


def test_round_trip(isa):
    rng = random.Random(1)
    for name, ins in isa["instructions"].items():
        fields = I.fmt_fields(isa, ins["fmt"])
        for _ in range(50):
            vals = {k: _random_value(isa, ins, k, r, rng) for k, r in fields.items()}
            w = I.encode(isa, name, **vals)
            assert 0 <= w < 1 << 128
            n2, v2 = I.decode(isa, w)
            assert n2 == name and v2 == vals


def test_symbolic_and_range(isa):
    w = I.encode(isa, "DMA_COPY", dq=2, cvt="MXFP4_E4M3", wop="ADD_F32", src_reg=3, dst_reg=4)
    name, f = I.decode(isa, w)
    assert name == "DMA_COPY" and f["cvt"] == isa["enums"]["cvt"]["MXFP4_E4M3"] and I.queue_of(isa, name, f) == "DQ2"
    assert I.decode(isa, I.encode(isa, "VEW", func="SWIGLU"))[1]["func"] == isa["enums"]["vew_func"]["SWIGLU"]
    with pytest.raises(I.IsaError):
        I.encode(isa, "ADDI", rd=32)
    with pytest.raises(I.IsaError):
        I.encode(isa, "MXU_MM", wimm=200)
    with pytest.raises(I.IsaError):
        I.decode(isa, 0xFF << 120)


def test_validator_rejects(isa):
    bad = copy.deepcopy(isa)
    bad["formats"]["T_MXU"]["fields"]["trans_w"] = [40, 40]          # overlaps mode
    with pytest.raises(I.IsaError):
        I.validate(bad)
    bad = copy.deepcopy(isa)
    bad["instructions"]["VEW"]["op"] = isa["instructions"]["MXU_MM"]["op"]
    with pytest.raises(I.IsaError):
        I.validate(bad)
    bad = copy.deepcopy(isa)
    bad["enums"]["cvt"]["TOO_BIG"] = 40                                # cvt field is 5 bits
    with pytest.raises(I.IsaError):
        I.validate(bad)
    bad = copy.deepcopy(isa)
    bad["memmap"]["regions"]["GSRAM"]["base"] = isa["memmap"]["regions"]["SPM"]["base"]
    with pytest.raises(I.IsaError):
        I.validate(bad)


def test_generated_files_current(isa):
    for path, text in [(os.path.join(ROOT, "isa", "gen", "isa_pkg.sv"), I.emit_sv(isa)),
                       (os.path.join(ROOT, "isa", "gen", "isa.h"), I.emit_h(isa)),
                       (os.path.join(ROOT, "isa", "gen", "isa_defs.py"), I.emit_py(isa)),
                       (os.path.join(ROOT, "docs", "spec", "isa_ref.md"), I.emit_md(isa))]:
        assert open(path).read() == text, f"{path} is stale: run make isa"


def test_address_regions(isa):
    mm = isa["memmap"]
    assert I.decode_addr(isa, (0x7F << 42) | 0x123, self_node=5)["node"] == 5
    spm = mm["regions"]["SPM"]
    d = I.decode_addr(isa, (3 << 42) | (spm["base"] + 7 * spm["stride"] + 100))
    assert (d["node"], d["region"], d["index"], d["offset"]) == (3, "SPM", 7, 100)
    with pytest.raises(I.IsaError):
        I.decode_addr(isa, mm["regions"]["HBM_RSV"]["base"])
    with pytest.raises(I.IsaError):
        I.decode_addr(isa, mm["regions"]["CORECTL"]["base"] + mm["regions"]["CORECTL"]["size"])
    with pytest.raises(I.IsaError):
        I.decode_addr(isa, 64 << 42)


def test_hbm_map(isa):
    h = isa["memmap"]["hbm"]
    assert 4 * h["stack_bytes"] == isa["memmap"]["regions"]["HBM"]["size"]
    assert 32 * 16 * h["rows"] * 1024 * 3 == h["stack_bytes"]
    rng = random.Random(2)
    seen = set()
    for _ in range(20000):                       # the map is one-to-one on 32 B bursts
        a = rng.randrange(0, 4 * h["stack_bytes"]) & ~31
        f = I.hbm_fields(isa, a)
        assert 0 <= f["row"] < h["rows"] and f["pc"] < 32 and f["col"] < 32
        key = (f["stack"], f["sid"], f["pc"], f["bg"], f["ba"], f["row"], f["col"])
        assert key not in seen
        seen.add(key)
    for base in (0, 0x1234000, h["stack_bytes"] + 0x80000, 3 * h["stack_bytes"] - 8192):
        base &= ~8191
        pcs = [I.hbm_fields(isa, base + 256 * g)["pc"] for g in range(32)]
        assert sorted(pcs) == list(range(32))   # an 8 KB stripe touches every pseudo-channel once
        for blk in range(4):                     # a 2 KB block stays on one leaf router (one read request)
            leaves = {I.hbm_fields(isa, base + 2048 * blk + o)["leaf"] for o in range(0, 2048, 32)}
            assert len(leaves) == 1
    # a 64 KB stride (same pc bits) still spreads over pseudo-channels
    assert len({I.hbm_fields(isa, 65536 * i)["pc"] for i in range(32)}) >= 16


def test_python_constants_match(isa):
    """The generated Python module carries the same opcodes, enums and memory map as the YAML."""
    ns = {}
    exec(open(os.path.join(ROOT, "isa", "gen", "isa_defs.py")).read(), ns)
    assert ns["OPCODES"] == {n: i["op"] for n, i in isa["instructions"].items()}
    assert ns["ENUMS"] == isa["enums"]
    assert ns["MEMMAP"]["hbm"]["fields"] == isa["memmap"]["hbm"]["fields"]
    assert ns["ENUMS"]["fault_cause"]["HBM_ALIGN"] == isa["enums"]["fault_cause"]["HBM_ALIGN"]


@pytest.mark.parametrize("lay", [l for l in I.LAYOUTS if l in I.load()])
def test_layout_fields_fit(isa, lay):
    """Every field of a generated layout fits its width, fields that share bits never share a type, and every field
    round-trips through pack / unpack at its maximum value."""
    fields = I.layout_fields(isa, lay)
    bits = isa[lay]["bits"]
    assert all(f["bits"][0] < bits for f in fields.values())
    types = I.layout_types(isa, lay) or [None]
    sel = isa[lay].get("select")
    for t in types:
        valid = {k: f for k, f in fields.items() if t is None or f["types"] is None or t in f["types"]}
        used = {}
        for k, f in valid.items():
            for b in range(f["bits"][1], f["bits"][0] + 1):
                assert b not in used, f"{lay} {t}: {k} overlaps {used.get(b)}"
                used[b] = k
        vals = {k: (1 << I.width(f["bits"])) - 1 for k, f in valid.items() if k != sel and not f["enum"]}
        vals.update({k: max(isa["enums"][f["enum"]].values()) for k, f in valid.items() if k != sel and f["enum"]})
        if sel:
            vals[sel] = t
        w = I.pack(isa, lay, **vals)
        assert 0 <= w < 1 << bits
        back = I.unpack(isa, lay, w)
        if sel:
            back[sel] = t
            vals[sel] = t
        assert back == {k: vals.get(k, 0) for k in back}


def test_noc_header_widths(isa):
    """The header fields cover what the rest of the spec puts in them (noc.md section 2)."""
    f = {k: I.width(v["bits"]) for k, v in I.layout_fields(isa, "noc_header").items()}
    m = isa["machine"]
    assert isa["noc_header"]["bits"] == 128                              # flit_t header sideband (microarch.md section 5)
    assert 1 << f["tag"] >= m["dma_outstanding_reads"]                   # one tag per read granule in flight
    assert f["addr"] == isa["memmap"]["gpa_bits"]
    assert 1 << f["len"] > 2048 and 1 << f["blen"] > 2048                # 2 KiB requests and bursts
    assert 1 << f["rsem"] >= m["semaphores"]
    assert f["ini_node"] == I.width(isa["memmap"]["node_field"])
    assert (f["hop_x"], f["hop_y"]) == (f["ini_x"], f["ini_y"]) == (4, 3)  # x 0..9 (leaves included), y 0..5
    mc = I.fmt_fields(isa, "T_DMAMC")
    assert [f[k] for k in ("mc_x0", "mc_x1", "mc_y0", "mc_y1")] == [I.width(mc[k]) for k in ("x0", "x1", "y0", "y1")]
    assert 1 << f["gidx"] >= 2048 // 256                                 # granules of one 2 KiB read


def test_layout_validator_rejects(isa):
    bad = copy.deepcopy(isa)
    bad["noc_header"]["fields"]["gidx"]["types"] = ["RD_RSP", "WR"]      # WR also uses addr at the same bits
    with pytest.raises(I.IsaError):
        I.validate(bad)
    bad = copy.deepcopy(isa)
    bad["noc_header"]["fields"]["rsv"]["bits"] = [128, 122]
    with pytest.raises(I.IsaError):
        I.validate(bad)
    bad = copy.deepcopy(isa)
    bad["paged_ptr"]["fields"]["row"] = [42, 20]                         # overlaps node
    with pytest.raises(I.IsaError):
        I.validate(bad)
    with pytest.raises(I.IsaError):
        I.pack(isa, "noc_header", type="RD_RSP", addr=1)                 # addr is not a response field


def test_td_paging_and_scale_fields(isa):
    """I-2: page shift, scale stride and scale-region offset are separate fields; T_DMA has page-table and tensor-scale
    operands, so no operand has two meanings."""
    td = isa["td"]["fields"]
    assert {"aux0", "row_bytes", "sc_off", "page_shift"} <= set(td)
    assert I.width(td["page_shift"]) == 5 and 256 << I.width(td["sc_off"]) >= 1 << 20
    dma = I.fmt_fields(isa, "T_DMA")
    assert {"idx_reg", "scale_reg", "aux_reg", "pt_reg", "ts_reg"} <= set(dma)
    assert "SCALE_REGION" in isa["enums"]["td_flags"]
    pp = I.layout_fields(isa, "paged_ptr")
    assert pp["node"]["bits"] == tuple(isa["memmap"]["node_field"])


def test_dma_list_entry(isa):
    """I-12(a): the 32 B DMA_LIST entry holds a full transfer with absolute addresses and an optional semaphore wait."""
    f = {k: I.width(v["bits"]) for k, v in I.layout_fields(isa, "dma_list_entry").items()}
    m = isa["machine"]
    assert isa["dma_list_entry"]["bits"] == 8 * 32
    assert f["src"] == f["dst"] == isa["memmap"]["gpa_bits"]
    assert 1 << f["idx"] >= m["spm_bytes"]
    assert f["n"] == I.width(isa["td"]["fields"]["n1"])
    assert f["wval"] == f["val"] == m["sem_bits"] and 1 << f["wsem"] >= m["semaphores"]
    assert 1 << f["src_td"] >= m["td_entries"] and 1 << f["rsem"] >= m["semaphores"]
    mc = I.fmt_fields(isa, "T_DMAMC")
    assert [f[k] for k in ("mc_x0", "mc_x1", "mc_y0", "mc_y1")] == [I.width(mc[k]) for k in ("x0", "x1", "y0", "y1")]
    assert set(isa["enums"]["list_op"]) == {"COPY", "GATHER", "SCATTER", "MCAST", "SIGNAL"}
    w = I.pack(isa, "dma_list_entry", op="SIGNAL", wsem=5, wval=0xFFFFFFF0, dst=(3 << 42) | 0x20020012100, val=2048, mode=0)
    assert I.unpack(isa, "dma_list_entry", w)["val"] == 2048


def test_semaphore_serial_compare():
    """I-4: waits compare as serial numbers, so byte-counting semaphores may wrap past 2^32."""
    assert I.sem_ready(5, 5) and I.sem_ready(6, 5) and not I.sem_ready(4, 5)
    assert I.sem_ready(0x00000010, 0xFFFFFFF0)              # wrapped past the target
    assert not I.sem_ready(0xFFFFFFF0, 0x00000010)          # not there yet across the wrap
    assert not I.sem_ready(0, 1 << 31) and I.sem_ready(0, (1 << 31) + 1)   # the 2^31 window


def test_diectl_and_corectl(isa):
    """I-15(e): every DIECTL block names the leaf that serves it; START / STOP have CORECTL offsets."""
    leaves = {f"{e}{i}" for e, n in (("N", 8), ("S", 8), ("E", 4), ("W", 4)) for i in range(1, n + 1)}
    dc = isa["memmap"]["diectl"]
    assert all(r["leaf"] in leaves for r in dc.values())
    assert sorted(r["block"] for r in dc.values()) == list(range(15))
    assert [dc[f"D2D{r}"]["leaf"] for r in range(4)] == ["E1", "E2", "E3", "E4"]
    assert [dc[f"C2C{r}"]["leaf"] for r in range(4)] == ["W1", "W2", "W3", "W4"]
    assert {"START", "STOP"} <= set(isa["memmap"]["corectl"])
    bad = copy.deepcopy(isa)
    bad["memmap"]["corectl"]["STOP"]["offset"] = bad["memmap"]["corectl"]["START"]["offset"]
    with pytest.raises(I.IsaError):
        I.validate(bad)


def test_spm_bank_strides():
    """I-13(a), memmap.md section 4: row starts at power-of-two pitches do not all spread, and a stationary load of two
    256 B rows per cycle collides at 2, 4 and 64 KiB pitches; padding the pitch (512 B, or 1 KiB at 16 KiB) removes it."""
    def pair_ok(pitch):
        rb = lambda base: {I.spm_bank(base + o) for o in range(0, 256, 64)}
        return all(not (rb(i * pitch) & rb((i + 1) * pitch)) for i in range(0, 128, 2))
    spread = {p: len({I.spm_bank(i * p) for i in range(64)}) for p in (2048, 4096, 8192, 16384, 32768, 65536)}
    assert spread == {2048: 32, 4096: 16, 8192: 8, 16384: 4, 32768: 2, 65536: 1}
    assert [pair_ok(1 << k) for k in range(11, 17)] == [False, False, True, True, True, False]
    for k in range(11, 18):
        assert pair_ok((1 << k) + (1024 if k == 14 else 512))
    assert all(len({I.spm_bank(b + o) for o in range(0, 2048, 64)}) == 32 for b in range(0, 1 << 22, 2048 * 7))


def _td(isa, **f):
    """Pack a TD from symbolic dtype / space / flag names and return its unpacked fields (what the DMA reads from DTAB)."""
    en = isa["enums"]
    flags = sum(1 << en["td_flags"][x] for x in f.pop("flags", ()))
    w = I.pack(isa, "td", dtype=en["dtype"][f.pop("dtype")], space=en["space"][f.pop("space")], flags=flags, **f)
    return I.unpack(isa, "td", w)


def _append(isa, td, ptr, frames, rows):
    """Write packets of a paged append: every byte of rows ptr.row .. + rows - 1, in TD order (dim 0 inside dim 1)."""
    runs = [(I.paged_addr(isa, td, ptr, frames, r, i0)[0], 1) for r in range(rows) for i0 in range(td["n0"])]
    return I.write_packets(runs)


# Worked examples of memmap.md section 6 (review findings V-4, V-5): one token appended per layer and step.
APPENDS = {
    # DeepSeek MXFP4 index key: 128 elements = 64 B + 4 B of E8M0 scales; 1024 rows per page = 64 KiB + 4 KiB = 17 frames.
    "deepseek_index_key": dict(fmt="MXFP4", n=128, s1=64, aux0=4, page_shift=10, sc_off=256, frames=17),
    # MiniMax NVFP4 KV: 4 groups x 128 = 256 B + 32 B of E4M3 scales; 128 rows per page = 32 KiB + 4 KiB = 9 frames.
    "minimax_kv": dict(fmt="NVFP4", n=512, s1=256, aux0=32, page_shift=7, sc_off=128, frames=9),
    # MiniMax NVFP4 index key: 128 elements = 64 B + 8 B; 1024 rows per page = 64 KiB + 8 KiB = 18 frames.
    "minimax_index_key": dict(fmt="NVFP4", n=128, s1=64, aux0=8, page_shift=10, sc_off=256, frames=18),
}


@pytest.mark.parametrize("name", sorted(APPENDS))
def test_paged_append_walk(isa, name):
    """V-5: the data row and the 32 B-aligned block of scale rows of a new token are each written in 32 B-aligned packets
    (no HBM_ALIGN), through the TD, paged_ptr and T_DMA fields; without merging adjacent rows the scale block would fault."""
    a = APPENDS[name]
    rows = 1 << a["page_shift"]
    row_b = a["n"] * isa["dtype_info"][a["fmt"]]["bits"] // 8
    assert row_b == a["s1"] and rows * a["s1"] <= a["sc_off"] * 256                      # data rows, then the scale region
    assert a["sc_off"] * 256 + rows * a["aux0"] <= a["frames"] * 4096
    assert a["aux0"] == a["n"] // isa["dtype_info"][a["fmt"]]["block"]
    common = dict(space="GLOBAL", s1=a["s1"], aux0=a["aux0"], page_shift=a["page_shift"], sc_off=a["sc_off"])
    td_data = _td(isa, dtype="UINT8", flags=("PAGED",), n0=row_b, n1=1, **common)          # raw bytes: cvt NONE moves no scales
    per_blk = 32 // a["aux0"]                                                               # scale rows per 32 B block
    td_sc = _td(isa, dtype="UINT8", flags=("PAGED", "SCALE_REGION"), n0=a["aux0"], n1=per_blk, **common)
    t = 5003 + rows                                                                         # token position (logical row)
    frames = {t >> a["page_shift"]: 0x123450}
    ptr = I.unpack(isa, "paged_ptr", I.pack(isa, "paged_ptr", node=0x7F, row=t, byte=0))
    ptr_blk = I.unpack(isa, "paged_ptr", I.pack(isa, "paged_ptr", node=0x7F, row=t - t % per_blk, byte=0))
    data = _append(isa, td_data, ptr, frames, 1)
    sc = _append(isa, td_sc, ptr_blk, frames, per_blk)
    q = t & (rows - 1)
    page = 0x123450 * 4096
    assert data == [(page + q * a["s1"], row_b)]
    assert sc == [(page + a["sc_off"] * 256 + (q - q % per_blk) * a["aux0"], 32)]
    assert all(x % 32 == 0 and n % 32 == 0 for x, n in data + sc)                           # HBM_ALIGN holds
    if per_blk > 1:                                                                         # the old rule: one packet per row
        assert all(n < 32 for _, n in [(I.paged_addr(isa, td_sc, ptr_blk, frames, r)[0], a["aux0"]) for r in range(per_blk)])
    # The two T_DMA instructions (SPM -> HBM, cvt NONE, rsem S0 because the destination is HBM, completion on ssem).
    for dst_td, src_reg, dst_reg in ((7, 20, 21), (8, 22, 23)):
        w = I.encode(isa, "DMA_COPY", dq=1, src_reg=src_reg, src_td=dst_td + 16, dst_reg=dst_reg, dst_td=dst_td,
                     cvt="NONE", wop="WRITE", pt_reg=24, ssem=9, sinc=1)
        name_, f = I.decode(isa, w)
        assert name_ == "DMA_COPY" and f["pt_reg"] == 24 and f["rsem"] == 0 and f["scale_reg"] == f["ts_reg"] == 0


def test_paged_scale_uses_whole_in_row_offset(isa):
    """V-4: reading the 4 groups of a MiniMax NVFP4 KV row with n0 = 128, n2 = 4, s2 = 64 B gives group g the scales at
    8 g .. 8 g + 7 of the row's 32 B scale row; the formula before the fix (byte + i0 only) gave every group group 0's."""
    td = _td(isa, dtype="NVFP4", space="GLOBAL", flags=("PAGED",), n0=128, n1=1, n2=4, s1=256, s2=64, aux0=32,
             page_shift=7, sc_off=128)
    ptr = I.unpack(isa, "paged_ptr", I.pack(isa, "paged_ptr", node=0x7F, row=130, byte=0))
    frames = {1: 0x400}
    sc0 = 0x400 * 4096 + 128 * 256 + 2 * 32
    for g in range(4):
        for i0 in (0, 15, 16, 127):
            d, s = I.paged_addr(isa, td, ptr, frames, 0, i0, g)
            assert d == 0x400 * 4096 + 2 * 256 + g * 64 + i0 // 2
            assert s == sc0 + 8 * g + i0 // 16
            assert (s == sc0 + i0 // 16) == (g == 0)                                         # the old formula's address
    bad = dict(td, s2=60)                                                                    # a group not on a block boundary
    with pytest.raises(I.IsaError):
        I.paged_addr(isa, bad, ptr, frames, 0, 0, 1)
    with pytest.raises(I.IsaError):                                                         # dims leave the 256 B row
        I.paged_addr(isa, dict(td, n2=5), ptr, frames, 0, 0, 4)
