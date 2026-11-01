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
    assert 1 << f["tag"] >= m["dma_outstanding_reads"]                   # one tag per read request, at most one per credit (Y-3)
    assert f["addr"] == isa["memmap"]["gpa_bits"]
    assert 1 << f["len"] > 2048 and 1 << f["blen"] > 2048                # 2 KiB requests and bursts
    assert 1 << f["rsem"] >= m["semaphores"]
    assert f["ini_node"] == I.width(isa["memmap"]["node_field"])
    assert (f["hop_x"], f["hop_y"]) == (f["ini_x"], f["ini_y"]) == (4, 3)  # x 0..9 (leaves included), y 0..5
    mc = I.fmt_fields(isa, "T_DMAMC")
    assert [f[k] for k in ("mc_x0", "mc_x1", "mc_y0", "mc_y1")] == [I.width(mc[k]) for k in ("x0", "x1", "y0", "y1")]
    assert 1 << f["gidx"] >= 2048 // 256                                 # granules of one 2 KiB read
    err = I.unpack(isa, "noc_header", I.pack(isa, "noc_header", type="ERR", tag=0x1FF, err_wr=1, cause="LINK"))
    assert err["err_wr"] == 1 and err["tag"] == 0x1FF                     # V-10: ERR says which tag space its tag is in
    for t in ("RD_REQ", "RD_RSP", "WR", "WR_ACK", "SEM", "ERR"):         # Y-10, X-13: requests carry the epoch, and every
        assert I.unpack(isa, "noc_header", I.pack(isa, "noc_header", type=t, epoch=1))["epoch"] == 1   # response echoes it
    with pytest.raises(I.IsaError):
        I.pack(isa, "noc_header", type="MC_WR", epoch=1)                 # posted multicast: no response, no epoch


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


def test_spm_bank_strides_over_tile_bases():
    """V-8: the padded pitches are conflict-free for two-row stationary loads at every 2 KiB-aligned tile base (the rule in
    memmap.md section 4), and every one except 8 KiB + 512 B also at every 256 B-aligned base. At 8 KiB + 512 B half of the
    256 B-aligned bases collide, which is why the rule asks for 2 KiB."""
    def pair_ok(pitch, base):
        rb = lambda b: {I.spm_bank(b + o) for o in range(0, 256, 64)}
        return all(not (rb(base + i * pitch) & rb(base + (i + 1) * pitch)) for i in range(0, 128, 2))
    padded = [(1 << k) + (1024 if k == 14 else 512) for k in range(11, 18)]
    for p in padded:
        assert all(pair_ok(p, b) for b in range(0, 1 << 17, 2048))
        bad = sum(not pair_ok(p, b) for b in range(0, 1 << 17, 256))
        assert bad == (256 if p == 8192 + 512 else 0)
    assert pair_ok(8192 + 512, 0) and not pair_ok(8192 + 512, 0x200)       # the verifier's example base
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


# ---------------------------------------------------------------- DMA staging ring (microarch.md section 2.5, review W-1)
# Entries for isa.staging_ring: (scale bytes, raw bytes, data responses, data bytes, elements[, HBM address of the data]).
# Every entry respects the DMA's sizing rule, at most a quarter of the ring (review Z-3).
def _nvfp4_bf16(n):           # 2 KiB NVFP4 data reads, 256 B of scales each, every granule converted from its response
    return [(256, 0, 8, 2048, 4096)] * n


def _fold_split(n, tiles=16):  # MXFP4 -> E4M3 fold with every 128 B tile across a granule: 8 B scales + 128 B raw per tile
    return [(8 * tiles, 128 * tiles, tiles // 2 + 1, 128 * tiles, 256 * tiles)] * n


def _fold_tiles(ring):        # fold tiles per entry within a quarter of the ring (136 B per split tile)
    return min(16, ring // 4 // 136)


def _engram(n):               # MXFP8 rows with inline scales (264 B): no scale read, two granules, staged raw
    return [(0, 264, 2, 264, 256)] * n


def _mixed(n, tiles=16):
    return [e for a, b in zip(_nvfp4_bf16(n), _fold_split(n, tiles)) for e in (a, b)]


RING_STREAMS = {
    "fold_split": lambda t: [_fold_split(150, t)],
    "mixed_one_dq": lambda t: [_mixed(150, t)],
    "mixed_four_dqs": lambda t: [_nvfp4_bf16(200), _fold_split(80, t), _mixed(60, t), _engram(300)],
}


@pytest.mark.parametrize("name", sorted(RING_STREAMS))
def test_staging_ring_atomic_rule_never_deadlocks(name):
    """W-1: with a unit's scales and raw slot taken together at scale-read issue, greedy scale read-ahead over a mixed
    split / non-split stream always completes, at several ring sizes and round trips, in one DQ or shared by four."""
    for ring in (4096, 16384):
        for rt in (100, 500, 1000):
            r = I.staging_ring(RING_STREAMS[name](_fold_tiles(ring)), ring=ring, rt=rt)
            assert r["done"] and r["deadlock"] is None and r["peak"] <= ring, (ring, rt, r)


def test_staging_ring_size_and_entry_bounds():
    """Z-3: the ring is at least 2 KiB (below it, block-format conversion faults ILLEGAL_FIELD), and the DMA forms entries
    of at most a quarter of the ring. The quarter is a rate target, not a liveness condition: entries up to the whole ring
    still complete (sizing="ring"), and only an entry larger than the ring can never be allocated."""
    assert I.RING_MIN == 2048 and 4 * 272 <= I.RING_MIN                     # the largest single unit (an Engram row) fits a quarter
    with pytest.raises(ValueError):
        I.staging_ring([_engram(4)], ring=1024)
    with pytest.raises(ValueError):                                          # 2,176 B entries above a quarter of 4 KiB
        I.staging_ring([_fold_split(4)], ring=4096)
    r = I.staging_ring(RING_STREAMS["mixed_four_dqs"](16), ring=4096, sizing="ring")
    assert r["done"], r
    with pytest.raises(ValueError):                                          # an entry the ring can never hold
        I.staging_ring([_fold_split(2, tiles=32)], ring=4096, sizing="ring")


def test_staging_ring_scale_read_tags():
    """Z-8: a scale read takes ceil(scale bytes / 256) tags, like a data read of that size: a 2 KiB scale read needs 8."""
    e = [(2048, 0, 8, 2048, 4096)] * 3
    assert I.staging_ring([e], tags=8)["done"]
    assert not I.staging_ring([e], tags=7)["done"]


def test_staging_ring_atomic_rule_random_streams():
    """W-1: random mixes of entry kinds and sizes (each within a quarter of the ring), 1-4 DQs, 4-16 KiB rings: never a
    deadlock."""
    rng = random.Random(11)
    for _ in range(25):
        ring = rng.choice((4096, 8192, 16384))
        sizes = [t for t in (1, 2, 4, 8, 16) if t <= _fold_tiles(ring)]
        streams = []
        for _ in range(rng.randint(1, 4)):
            s = []
            for _ in range(rng.randint(5, 60)):
                tiles = rng.choice(sizes)
                s.append(rng.choice((_nvfp4_bf16(1)[0], _fold_split(1, tiles)[0], _engram(1)[0], (32, 0, 1, 256, 512))))
            streams.append(s)
        r = I.staging_ring(streams, ring=ring, rt=rng.choice((50, 300, 800)))
        assert r["done"], r


@pytest.mark.parametrize("rule", ["split", "capped"])
@pytest.mark.parametrize("name", ["fold_split", "mixed_one_dq", "mixed_four_dqs"])
def test_staging_ring_rejected_rules_deadlock(rule, name):
    """W-1: the text before the fix ('split': raw slot taken at data-read issue) fills the ring with scales whose data can
    never be issued (the verifier's probe: deadlock at cycle 687 for the all-split fold). Capping the read-ahead so that
    the largest raw slot still fits ('capped') also deadlocks in a ring, because a freed raw slot behind newer scale
    entries is reclaimed only in allocation order."""
    r = I.staging_ring(RING_STREAMS[name](16), rule=rule)
    assert not r["done"] and r["deadlock"] is not None
    if rule == "split" and name == "fold_split":
        assert r["deadlock"] == 687


def test_staging_ring_delivery_limited():
    """Z-2, Y-2: with the HBM share as a FIFO server (the core's backlog adds to the round trip; one response per granule)
    and greedy issue up to 512 credits, the paced rates do not hold: the NVFP4 weight stream becomes ring-bound and the
    paged NVFP4 KV gather (32 B scale read + 256 B row) tag-bound, about 29% short at an 845-cycle round trip (the third
    verifier's per-granule model: 129.6 / 110.3 / 103.4). The pace rule (DMA_PACE 72 KiB, counted by format) gives the paced
    rates back. Under a flat 72 KiB (the Z-2 rule) MXFP4 and MXFP8 weights, which the ring covers far beyond 845 cycles, are
    bound by the limit alone: about 136 and 69 at L = 1,000 (the fourth verifier: 134.7 / 69.0); counted by format they keep
    the full rate to 1,200 cycles. A larger ring alone fixes only the NVFP4 weights. Plain reads are not paced, so a plain
    stream keeps the full share at 1,000 cycles."""
    need = 87 / (0.5 + 1 / 16)
    w, kv, plain = [_nvfp4_bf16(1500)], [[(32, 0, 1, 256, 512)] * 9000], [[(0, 0, 8, 2048, 2048)] * 1500]
    mx = {"MXFP4": ([[(128, 0, 8, 2048, 4096)] * 1500], 87 / (0.5 + 1 / 32)),
          "MXFP8": ([[(64, 0, 8, 2048, 2048)] * 1500], 87 / (1 + 1 / 32))}
    srv = dict(hbm="server")
    pace = dict(hbm="server", inflight=I.PACE_DEFAULT)
    for rt in (500, 845):
        assert I.staging_ring(w, rt=rt, **srv)["rate"] < 0.93 * need
        assert I.staging_ring(kv, rt=rt, **srv)["rate"] < 0.9 * need
        assert I.staging_ring(w, rt=rt, **pace)["rate"] >= 0.96 * need
        assert I.staging_ring(kv, rt=rt, **pace)["rate"] >= 0.98 * need
        assert I.staging_ring(w, rt=rt, ring=24576, **srv)["rate"] >= 0.99 * need          # a larger ring: weights only
        assert I.staging_ring(kv, rt=rt, ring=32768, **srv)["rate"] < 0.9 * need
    assert I.staging_ring(w, rt=845, **srv)["rate"] < 0.8 * need
    assert I.staging_ring(kv, rt=845, **srv)["rate"] < 0.75 * need
    for rt, theirs in ((500, 129.6), (845, 110.3), (1000, 103.4)):                         # the verifier's per-granule model
        assert abs(I.staging_ring(w, rt=rt, **srv)["rate"] - theirs) < 0.03 * theirs
    assert I.staging_ring(w, rt=845, rsp="request", **srv)["rate"] > 1.05 * 110.3          # the model before Y-3: 119
    for f, (st, nd) in mx.items():
        assert I.staging_ring(st, rt=1000, fmt=[f], **srv)["rate"] >= 0.99 * nd           # tags only: full rate
        flat = I.staging_ring(st, rt=1000, fmt=[f], pace="flat", **pace)["rate"]
        theirs = {"MXFP4": 134.7, "MXFP8": 69.0}[f]
        assert flat < 0.86 * nd and abs(flat - theirs) < 0.05 * theirs, (f, flat)
        for rt in (500, 845, 1000, 1200):
            assert I.staging_ring(st, rt=rt, fmt=[f], **pace)["rate"] >= 0.99 * nd, (f, rt)
    for rt in (845, 1000):                                                                  # NVFP4: weighting changes nothing
        assert I.staging_ring(w, rt=rt, pace="flat", **pace)["rate"] == I.staging_ring(w, rt=rt, **pace)["rate"]
    assert I.staging_ring(plain, rt=1000, **pace)["bw"] >= 0.98 * 87


def test_staging_ring_pace_cap():
    """Y-2: the limit is min(DMA_PACE, 4.5 x the ring). The cap is the NVFP4 ring coverage times the share (4.5 x 16 KiB =
    72 KiB), so a smaller ring needs no runtime change: at 8 KiB it gives the KV gather 130 instead of 104 at L = 500. The
    limit derived from the ring alone (the fourth verifier's proposal) is rejected: the KV gather is tag-bound, its best
    limit is about 72 KiB at any ring size, and 4.5 x a 32 KiB ring (144 KiB) drops it to about 110 at L = 845."""
    need = 87 / (0.5 + 1 / 16)
    kv = [[(32, 0, 1, 256, 512)] * 9000]
    pace = dict(hbm="server", inflight=I.PACE_DEFAULT)
    assert I.PACE_DEFAULT == 9 * 16384 // 2
    capped = I.staging_ring(kv, ring=8192, rt=500, **pace)["rate"]
    flat = I.staging_ring(kv, ring=8192, rt=500, pace="flat", **pace)["rate"]
    assert capped >= 0.82 * need and flat < 0.7 * need, (capped, flat)
    assert I.staging_ring(kv, ring=32768, rt=845, **pace)["rate"] >= 0.98 * need            # the CSR keeps 72 KiB
    derived = I.staging_ring(kv, ring=32768, rt=845, hbm="server", inflight=9 * 32768 // 2)["rate"]
    assert derived < 0.75 * need, derived


def test_staging_ring_entry_size():
    """Y-1: an entry without raw slots covers at most one data request. Its scales stay in the ring for two round trips
    plus the time its own data takes to stream, so the ring must hold scale rate x 2 L plus about one entry's scales.
    Quarter-size scale-only entries (4 KiB of NVFP4 scales, 32 KiB of data), which the Z-3 rule allowed, run at about 124
    elements/cycle at L = 845 instead of 153 (paced) and 123 with DMA_PACE (the fourth verifier's own model: 123.1 / 123.8);
    the formula predicts each size within 3%. The rule rejects such entries (sizing="rate")."""
    need = 87 / (0.5 + 1 / 16)
    sc_rate = 87 * 32 / 288                                         # NVFP4 scale bytes per cycle at the share
    rates = {}
    for g in (8, 16, 64, 128):                                      # granules per entry: 256 B .. 4 KiB of scales
        st = [[(32 * g, 0, g, 256 * g, 512 * g)] * (12000 // g)]
        rates[g] = I.staging_ring(st, rt=845, sizing="quarter")["rate"]
        pred = need * min(1.0, 16384 / (sc_rate * 2 * 845 + 32 * g))
        assert abs(rates[g] - pred) < 0.03 * pred, (g, rates[g], pred)
        if g > 8:
            with pytest.raises(ValueError):
                I.staging_ring(st, rt=845)
    assert rates[8] >= 0.98 * need and rates[128] < 0.82 * need
    big = [[(4096, 0, 128, 32768, 65536)] * 90]
    assert I.staging_ring(big, rt=845, sizing="quarter", hbm="server", inflight=I.PACE_DEFAULT)["rate"] < 0.82 * need


def test_staging_ring_tag_arbitration():
    """Y-3: a read takes one tag and one credit per granule (one per RD_RSP). Data bytes per cycle per DQ at L = 845 with
    DMA_PACE, while the streams overlap:
    - arb="greedy" (ready includes free credits): one-credit requests (a KV gather: 32 B scale read, 256 B row) take every
      freed credit, and a 2 KiB plain read, which needs 8 at once, starves: about 4-6 B/cycle of 87 (the fourth verifier:
      1-4); with two KV-gather DQs about 3.
    - arb="hold" (the turn held for one request, the verifier's proposal): round-robin per request gives a 2 KiB read 8x
      the bytes of a one-granule read, so the KV gather starves instead (about 5 against 81).
    - arb="fair" (the rule): the winner keeps its turn until it has taken 8 credits, holding for credits and pace: credits
      are shared evenly (KV about 28 + 3 B/cycle of scales, plain 56; two KV DQs and plain about 19 / 19 / 40).
    - Ring space starves the same way: next to the KV gather's 32 B entries a weight stream's 256 B entries never fit
      (ring_turn=False: about 0). With the ring turn (DQs waiting to allocate take turns of 256 B of ring) both get about
      37."""
    kv, pl, w = [(32, 0, 1, 256, 512)] * 9000, [(0, 0, 8, 2048, 2048)] * 1500, _nvfp4_bf16(1500)
    run = dict(hbm="server", inflight=I.PACE_DEFAULT, rt=845)
    greedy = I.staging_ring([kv, pl], arb="greedy", **run)["bw_dq"]
    assert greedy[1] < 0.1 * 87 and greedy[0] > 0.7 * 87, greedy
    assert I.staging_ring([kv, kv[:6000], pl], arb="greedy", **run)["bw_dq"][2] < 0.06 * 87
    hold = I.staging_ring([kv, pl], arb="hold", **run)["bw_dq"]
    assert hold[0] < 0.1 * 87, hold
    fair = I.staging_ring([kv, pl], **run)["bw_dq"]
    assert min(fair) >= 0.3 * 87 and sum(fair) >= 0.9 * 87, fair
    three = I.staging_ring([kv, kv[:6000], pl], **run)["bw_dq"]
    assert min(three) >= 0.2 * 87, three
    assert I.staging_ring([w, kv], ring_turn=False, **run)["bw_dq"][0] < 0.05 * 87
    wk = I.staging_ring([w, kv], **run)["bw_dq"]
    assert min(wk) >= 0.4 * 87, wk
    assert I.staging_ring([kv[:300], pl[:50], w[:60], kv[:300]], rt=300)["done"]           # four DQs, paced


def test_staging_ring_pace_hold_and_formats():
    """X-8: the issue turn is held for pace only against converting DQs. With DMA_PACE 16 KiB at L = 845 the pace binds;
    held against every DQ (pace_hold="all", the text before X-8) a plain DQ gets one turn per paced request: NVFP4 weights
    + plain 2 KiB reads about 17 / 17 B/cycle and KV gather + plain 17 / 34 (the fifth verifier's own model: 17 / 17 and
    17 / 34). Under the rule the plain DQ issues while the holder waits for pace: about 10 / 76 and 11 / 75, as with
    "greedy" (9 / 77, 11 / 75), and at the default DMA_PACE nothing changes (41 / 41, 28 / 56).
    X-10: the ring turn is counted in data granules of the allocated entries (8, one data request), not in 256 B of ring,
    which is one NVFP4 entry but four MXFP8 entries: NVFP4 + MXFP8 weights in two DQs share 17 / 66 under the byte
    quantum and about 40 / 40 under the rule; with a plain DQ 9 / 38 / 38 against 28 / 28 / 28. KV, plain and NVFP4 mixes
    are unchanged (test_staging_ring_tag_arbitration).
    X-14: the reads of an entry whose data is one granule count their bytes in full; longer entries by format. A per-row
    MXFP4 gather (16 B scale read + 256 B row) runs at about 110 elements/cycle at L = 845 by format and 154 by entry shape,
    while MX weight streams keep the full rate to 1,200 cycles either way."""
    pl, w, kv = [(0, 0, 8, 2048, 2048)] * 1500, _nvfp4_bf16(1500), [(32, 0, 1, 256, 512)] * 9000
    tight = dict(hbm="server", inflight=16384, rt=845)
    for rd, lo in ((w, 0.1), (kv, 0.1)):
        held = I.staging_ring([rd, pl], pace_hold="all", **tight)["bw_dq"]
        assert held[1] < 0.45 * 87, held                                    # plain throttled by the paced stream
        rule = I.staging_ring([rd, pl], **tight)["bw_dq"]
        greedy = I.staging_ring([rd, pl], arb="greedy", **tight)["bw_dq"]
        assert rule[1] >= 0.85 * 87 and rule[0] >= lo * 87 and abs(rule[1] - greedy[1]) < 0.03 * 87, (rule, greedy)
    run = dict(hbm="server", inflight=I.PACE_DEFAULT, rt=845)
    for pair in ([w, pl], [kv, pl]):
        assert I.staging_ring(pair, pace_hold="all", **run)["bw_dq"] == I.staging_ring(pair, **run)["bw_dq"]
    m8, m4 = [(64, 0, 8, 2048, 2048)] * 1500, [(128, 0, 8, 2048, 4096)] * 1500
    by_bytes = I.staging_ring([w, m8], fmt=["NVFP4", "MXFP8"], ring_q="bytes", **run)["bw_dq"]
    assert by_bytes[0] < 0.25 * 87 and by_bytes[1] > 0.7 * 87, by_bytes
    for pair, f in (([w, m8], ["NVFP4", "MXFP8"]), ([w, m4], ["NVFP4", "MXFP4"])):
        r = I.staging_ring(pair, fmt=f, **run)["bw_dq"]
        assert min(r) >= 0.42 * 87 and abs(r[0] - r[1]) < 0.05 * 87, (f, r)
    three = I.staging_ring([w, m8, pl], fmt=["NVFP4", "MXFP8", None], **run)["bw_dq"]
    assert min(three) >= 0.3 * 87, three
    rows = [[(16, 0, 1, 256, 512)] * 9000]                                  # MXFP4 rows: 512 elements, 16 B of scales
    need = 87 / (0.5 + 1 / 32)
    fmt_rate = I.staging_ring(rows, fmt=["MXFP4"], pace="format", **run)["rate"]
    shape_rate = I.staging_ring(rows, fmt=["MXFP4"], **run)["rate"]
    assert fmt_rate < 0.75 * need and shape_rate > 0.9 * need, (fmt_rate, shape_rate)
    for st, f, nd in ((m4, "MXFP4", need), (m8, "MXFP8", 87 / (1 + 1 / 32))):
        assert I.staging_ring([st], fmt=[f], rt=1200, hbm="server", inflight=I.PACE_DEFAULT)["rate"] >= 0.99 * nd


def test_staging_ring_writes_in_turns():
    """X-11: a write packet (at most 256 B, no credit) counts one credit toward the 8-credit turn, and the turn is not
    held for write burst ids, which acks free. Read literally ("until it has taken 8 granule credits"), a DQ that wins the
    turn with a write keeps it until its writes run out: a 1 MiB SPM-to-SPM write (4,096 packets, a MoE dispatch) next to
    a weight prefetch leaves the reader nothing while it runs (no common window) and delays it by about 4,096 cycles.
    Under the rule the reader keeps about its rate alone (NVFP4 weights 73 of 77 B/cycle of data, plain reads 87) and the
    writes take the rest of the issue slots."""
    wr = [("write", 1 << 20)]
    run = dict(hbm="server", inflight=I.PACE_DEFAULT, rt=845)
    for rd in (_nvfp4_bf16(600), [(0, 0, 8, 2048, 2048)] * 600):
        alone = I.staging_ring([rd], **run)
        old = I.staging_ring([rd, wr], write_turn=False, **run)
        assert old["bw_dq"] is None and old["cycle"] > alone["cycle"] + 3800, (old, alone["cycle"])
        new = I.staging_ring([rd, wr], **run)
        assert new["bw_dq"][0] >= 0.9 * 87 * rd[0][3] / (rd[0][3] + rd[0][0]), new["bw_dq"]
        assert new["cycle"] < alone["cycle"] + 100 and new["bw_dq"][1] > 0.3 * 256
    assert I.staging_ring([[(32, 0, 1, 256, 512)] * 2000, wr], **run)["done"]


def test_staging_ring_bw_dq_window():
    """X-15 (b): bw_dq is measured from the latest DQ's 5% mark to the earliest DQ's 95% mark of its own data. A DQ that
    finishes before another one has started gives no common window (None), not a rate of 0 (the earlier 25-75% fallback
    reported a plain DQ that ran as 0)."""
    kv, pl = [(32, 0, 1, 256, 512)] * 9000, [(0, 0, 8, 2048, 2048)] * 60
    run = dict(hbm="server", inflight=I.PACE_DEFAULT, rt=845)
    assert I.staging_ring([kv, pl], **run)["bw_dq"] is None
    both = I.staging_ring([kv[:3000], pl * 10], **run)["bw_dq"]
    assert both is not None and min(both) > 0.2 * 87, both


# Semaphore programs (review X-7): transfers of several DQs with cross-DQ SIGNAL / wait pairs and front-end gaps.
def _sem_transfer(rng, kind, ring, base):
    n = rng.randint(3, 16)
    if kind == "fold":                            # contiguous MXFP4 fold tiles from byte 64: raw slots and scales
        return [("fmt", "MXFP4")] + _fold_contig(ring, n=n // 4 + 1, base=base)
    return {"nvfp4": [("fmt", "NVFP4")] + _nvfp4_bf16(n), "mxfp8": [("fmt", "MXFP8")] + [(64, 0, 8, 2048, 2048)] * n,
            "kv": [("fmt", "NVFP4")] + [(32, 0, 1, 256, 512)] * (4 * n),
            "engram": [("fmt", "MXFP8")] + [(8, 0, 1, 256, 256)] * (2 * n),
            "inline": [("fmt", "MXFP8")] + _engram(n), "plain": [(0, 0, 8, 2048, 2048)] * n,
            "write": [("write", 256 * 4 * n)]}[kind]


def _sem_program(rng, ring, ndq=4):
    """Transfers in one global order, each in a random DQ, each followed by a SIGNAL; a wait names the SIGNAL of an
    earlier transfer of another DQ, so the program cannot deadlock by itself."""
    ntr = sum(rng.randint(1, 4) for _ in range(ndq))
    dq_of = [rng.randrange(ndq) for _ in range(ntr)]
    streams = [[] for _ in range(ndq)]
    for k, d in enumerate(dq_of):
        if rng.random() < 0.15:
            streams[d].append(("gap", rng.randint(1, 2000)))
        prev = [j for j in range(k) if dq_of[j] != d]
        if prev and rng.random() < 0.6:
            streams[d].append(("wait", rng.choice(prev), 1))
        kind = rng.choice(("nvfp4", "mxfp8", "kv", "engram", "inline", "fold", "plain", "write"))
        streams[d] += _sem_transfer(rng, kind, ring, (d << 30) + (k << 24))
        streams[d].append(("signal", k, 1))
    return streams


def test_staging_ring_ring_turn_passes_when_idle():
    """X-7: the ring turn passes whenever its holder cannot start an entry for a reason other than ring space, credits,
    pace or the issue turn: its stream has ended, a semaphore wait, SIGNAL or gap is at its head, or its next read is
    plain or a write. Under the text before X-7 (the turn kept until its 256 B quantum, ring_pass="quantum") and under the
    reference's end-of-stream pass ("end") a DQ that waits on a semaphore inside its turn deadlocks the ring: 2 KiB ring,
    DQ0 streams NVFP4 weights, DQ1 gathers 37 KV rows, waits on F, then gathers 37 more, DQ2 gathers 200 KV rows and sets F
    once they complete. DQ1 takes the turn, allocates 160 B and waits; DQ0 and DQ2 queue behind it. Nothing moves after
    cycle 3,649 at L = 500 (the fifth verifier's own model: 3,628), at 300 / 500 / 845 with 2 and 4 KiB rings. The turn is
    taken only when a DQ waits for the ring (ring_take="contended"), so at 16 KiB nothing queues and the end-of-stream pass
    completes; the quantum rule still hangs there once the weight stream ends inside a turn. Random four-DQ programs with
    cross-DQ waits (each deadlock-free by itself) deadlock under both old rules and never under the rule."""
    kv1 = (32, 0, 1, 256, 512)
    prog = [_nvfp4_bf16(400), [kv1] * 37 + [("wait", "F", 1)] + [kv1] * 37, [kv1] * 200 + [("signal", "F", 1)]]
    srv = dict(hbm="server", inflight=I.PACE_DEFAULT)
    r = I.staging_ring(prog, ring=2048, rt=500, ring_pass="end", **srv)
    assert not r["done"] and abs(r["deadlock"] - 3628) < 0.02 * 3628, r
    for ring in (2048, 4096):
        for rt in (300, 500, 845):
            for rp in ("quantum", "end"):
                assert not I.staging_ring(prog, ring=ring, rt=rt, ring_pass=rp, **srv)["done"], (ring, rt, rp)
            ok = I.staging_ring(prog, ring=ring, rt=rt, **srv)
            assert ok["done"] and ok["sems"]["F"] == 1, (ring, rt)
    assert I.staging_ring(prog, ring=16384, rt=500, ring_pass="end", **srv)["done"]
    assert not I.staging_ring(prog, ring=16384, rt=500, ring_pass="quantum", **srv)["done"]
    ends = [_nvfp4_bf16(400), [kv1] * 5]          # no semaphore: the KV DQ queues, takes the turn and its stream ends at 160 B
    assert not I.staging_ring(ends, ring=2048, ring_pass="quantum")["done"]
    assert I.staging_ring(ends, ring=2048, ring_pass="end")["done"] and I.staging_ring(ends, ring=2048)["done"]
    rng = random.Random(7)
    hangs = {"quantum": 0, "end": 0, "idle": 0}
    for _ in range(24):
        ring = rng.choice((2048, 4096, 8192, 16384))
        kw = dict(ring=ring, rt=rng.choice((50, 300, 845)), tags=rng.choice((8, 16, 512)), sizing="quarter")
        if rng.random() < 0.5:
            kw.update(hbm="server", inflight=rng.choice((None, 1024, I.PACE_DEFAULT)))
        prog = _sem_program(rng, ring)
        for rp in hangs:
            hangs[rp] += not I.staging_ring(prog, ring_pass=rp, **kw)["done"]
    assert hangs["idle"] == 0 and hangs["end"] > 0 and hangs["quantum"] > hangs["end"], hangs


def test_staging_ring_pace_below_one_request():
    """Y-6: a converting read may always issue when no converting read is in flight. Under the literal rule ("at most
    DMA_PACE bytes in flight") a 1 KiB limit never issues the first 2 KiB data read and the core hangs; with the rule the
    stream completes, one read at a time. The counter needs 18 bits: with the limit off, 512 granules of converting reads
    weigh 2^17 B."""
    w = [_nvfp4_bf16(200)]
    assert not I.staging_ring(w, hbm="server", inflight=1024, pace_idle=False)["done"]
    assert I.staging_ring(w, hbm="server", inflight=1024)["done"]
    r = I.staging_ring([_nvfp4_bf16(1500)], ring=32768, rt=1000, hbm="server")
    assert r["peak_infl"] == 1 << 17                                # 2^17: one more than a 17-bit counter holds


# Entries with HBM byte ranges (review Z-1). Each DQ streams from its own 1 GiB region.
def _index_key(ring, n=60, base=0, first=2):
    """DeepSeek MXFP4 index-key rows (64 B, 4 B of scales each) from row `first` of a page, one scale read per entry of up
    to 2 KiB of scales (512 rows), within a quarter of the ring. Rows are non-split; entries end mid-granule."""
    rows = min(2048, ring // 4) // 4
    a = base + 64 * first
    return [(4 * rows, 0, 0, 64 * rows, 128 * rows, a + i * 64 * rows) for i in range(n)]


def _fold_contig(ring, n=60, base=0, start=64):
    """Contiguous MXFP4 fold tiles (128 B, 8 B of scales) from byte 64: every other tile crosses a granule (staged raw)."""
    tiles = (ring // 4) // 72 // 2 * 2
    out = []
    for i in range(n):
        a = base + start + i * 128 * tiles
        split = sum(1 for j in range(tiles) if (a + 128 * j) % 256 + 128 > 256)
        out.append((8 * tiles, 128 * split, 0, 128 * tiles, 256 * tiles, a))
    return out


Z1_ARB = dict(arb="shrink", ring_turn=False)     # the arbitration before Y-3, under which the Z-1 numbers were taken


@pytest.mark.parametrize("kind", ["index_key", "fold_contig"])
def test_staging_ring_entry_boundaries(kind):
    """Z-1: every data read belongs to exactly one entry. With entries that end mid-granule, the rejected reading ('shared':
    one read of the boundary granule, issued once both entries' scales are in the ring) deadlocks with four DQs: the ring's
    oldest entry is its DQ's only one and waits for that DQ's next entry, which cannot be allocated, and in-order reclaim
    lets nothing pass (the verifier's probe: cycle 1,715 / 5,976 for the index key at 4 / 16 KiB). Cutting the read at the
    entry boundary (two partial reads) always completes. One DQ cannot deadlock this way: its next entry is the next
    allocation."""
    mk = _index_key if kind == "index_key" else _fold_contig
    for ring in (4096, 16384):
        four = [mk(ring, base=d << 30) for d in range(4)]
        cut = I.staging_ring(four, ring=ring, boundary="cut", sizing="quarter")
        # The deadlock depends on timing (the fourth verifier saw 16 KiB complete with a queue, and the Y-3 arbitration
        # avoids it there for the index key), so the Z-1 numbers are reproduced with the arbitration of that session.
        # Cutting is the rule because it removes the cause; under the rule's arbitration "shared" still deadlocks at 4 KiB.
        shared = I.staging_ring(four, ring=ring, boundary="shared", sizing="quarter", **Z1_ARB)
        if ring == 4096:
            assert not I.staging_ring(four, ring=ring, boundary="shared", sizing="quarter")["done"]
        assert cut["done"] and cut["peak"] <= ring, (ring, cut)
        assert not shared["done"] and shared["deadlock"] is not None, (ring, shared)
        assert I.staging_ring(four[:1], ring=ring, boundary="shared", sizing="quarter")["done"]
        plans = I._ring_reads(four[0], "cut")
        assert all(len(r[2]) == 1 for p in plans for r in p)                       # cut: one entry per read
        assert any(len(r[2]) == 2 for p in I._ring_reads(four[0], "shared") for r in p)
    if kind == "index_key":
        r = I.staging_ring([_index_key(4096, base=d << 30) for d in range(4)], ring=4096, boundary="shared", sizing="quarter",
                           **Z1_ARB)
        assert abs(r["deadlock"] - 1715) <= 5                                      # the verifier's number, own model


def test_staging_ring_sizing():
    """W-1: 16 KiB is sized for scales. NVFP4 -> BF16 runs at the HBM share's rate (87 B/cycle: 87 / (1/2 + 1/16) =
    155 elements/cycle) up to a round trip of about 845 cycles; 8 KiB does not. When every fold tile is split the ring,
    not HBM, sets the rate (about 28 elements/cycle), which is why the compiler keeps fold tiles inside a granule."""
    share_rate = 87 / (0.5 + 1 / 16)
    for rt in (500, 845):
        assert I.staging_ring([_nvfp4_bf16(1200)], rt=rt)["rate"] >= 0.98 * share_rate
    assert I.staging_ring([_nvfp4_bf16(1200)], ring=8192, rt=845)["rate"] < 0.6 * share_rate
    split = I.staging_ring([_fold_split(150)])["rate"]
    assert 20 < split < 40
    eng = I.staging_ring([_engram(1500)])["rate"] / 256                        # Engram rows per cycle
    assert 0.10 < eng < 0.15


def test_rd_rsp_layout():
    """Y-5: partial-granule responses. A read request may start and end inside a granule (a cut read, Z-1); it gets one
    RD_RSP per granule it touches, gidx counted from the granule of its first byte. The payload is the whole granule,
    aligned, and its valid bytes are the request's range inside it, taken from the tag-table entry (RD_RSP has no len).
    The target reads only the 32 B bursts the range touches. Random requests reassemble exactly in SPM."""
    G = 0x1234_5000                                     # a 2 KiB-aligned HBM address
    a, b = I.rd_rsp_layout(G, 128, 0x100), I.rd_rsp_layout(G + 128, 128, 0x900)   # one granule cut at byte 128
    assert a == [dict(gidx=0, lo=0, hi=128, flits=1, part=0, spm=0x100, bursts=[0, 1, 2, 3])]
    assert b == [dict(gidx=0, lo=128, hi=256, flits=1, part=1, spm=0x900, bursts=[4, 5, 6, 7])]
    assert b[0]["lo"] * 8 // 4 == 256                   # the second half's first NVFP4 element (tag-table element offset)
    r = I.rd_rsp_layout(G + 8, 264, 0x2000)             # an Engram row from byte 8: two granules
    assert [(x["gidx"], x["lo"], x["hi"], x["spm"], x["bursts"]) for x in r] == \
        [(0, 8, 256, 0x2000, list(range(8))), (1, 0, 16, 0x2000 + 248, [0])]
    r0, r1 = I.rd_rsp_layout(G, 264, 0), I.rd_rsp_layout(G + 264, 264, 0)   # contiguous rows: burst 0 of granule 1 twice
    assert r0[-1]["bursts"] == [0] and r1[0]["bursts"][0] == 0 and r1[0]["lo"] == 8
    assert all(x["lo"] == 0 and x["hi"] == 256 for x in I.rd_rsp_layout(G, 2048, 0))
    assert [x["gidx"] for x in I.rd_rsp_layout(G, 2048, 0)] == list(range(8))
    with pytest.raises(I.IsaError):
        I.rd_rsp_layout(G + 2040, 16, 0)                # crosses a 2 KiB block
    rng = random.Random(5)
    hbm = bytes(rng.randrange(256) for _ in range(4096))
    for _ in range(200):
        lo = rng.randrange(2048)
        n = rng.randint(1, 2048 - lo)
        spm = bytearray(4096)
        for x in I.rd_rsp_layout(lo, n, 1000):
            gb = (lo // 256 + x["gidx"]) * 256         # the granule the payload holds
            if x["flits"] == 1:                         # X-9: one flit, the half that holds the valid bytes
                assert x["lo"] // 128 == (x["hi"] - 1) // 128 == x["part"]
                flit = hbm[gb + 128 * x["part"]:gb + 128 * x["part"] + 128]
                payload = bytes(128 * x["part"]) + flit + bytes(128 - 128 * x["part"])
            else:
                assert x["lo"] // 128 != (x["hi"] - 1) // 128 and x["part"] is None
                payload = hbm[gb:gb + 256]
            spm[x["spm"]:x["spm"] + x["hi"] - x["lo"]] = payload[x["lo"]:x["hi"]]
            assert all(gb + 32 * k < lo + n and gb + 32 * k + 32 > lo for k in x["bursts"])
        assert bytes(spm[1000:1000 + n]) == hbm[lo:lo + n]
    # X-9: a KV row's 32 B scale read and an Engram row's 8 B scale read are 1 flit; the 256 B row is 2
    assert [x["flits"] for x in I.rd_rsp_layout(G + 3 * 32, 32, 0) + I.rd_rsp_layout(G + 5 * 8, 8, 0)] == [1, 1]
    assert [x["flits"] for x in I.rd_rsp_layout(G + 256, 256, 0)] == [2]
    assert I.rd_rsp_layout(G + 120, 16, 0)[0]["flits"] == 2              # 16 B across the halves: 2 flits


def test_rd_rsp_pieces_and_destination_runs():
    """X-12 (b): a response's valid range is cut at conversion-unit boundaries, and each piece is placed from its ring
    entry (first unit, unit size, raw-slot base), not from one ring offset per tag. Contiguous MXFP4 fold tiles (128 B)
    from byte 64: every granule after the first carries the 64 B tail of a split tile (raw slot k), a whole tile (it
    converts from this response) and the 64 B head of the next split tile (raw slot k + 1). Random entries of 128, 256 or
    264 B units, cut into requests at 2 KiB blocks, in any response order, fill every raw slot with exactly its unit.
    X-12 (a): a read request is cut where its SPM destination run ends. Two adjacent 512 B HBM rows read into SPM rows
    padded to 576 B: merged into one 1 KiB request, row 1 would land at dst + 512 instead of dst + 576, silently."""
    G = 0x1234_5000
    tiles = 6
    reqs = I.rd_rsp_layout(G + 64, 128 * tiles, 0, unit=(G + 64, 128, 0x400))
    got = [[(p["unit"], p["lo"], p["hi"], p["raw"]) for p in r["pieces"]] for r in reqs]
    assert got == [[(0, 64, 192, None), (1, 192, 256, 0x400)],
                   [(1, 0, 64, 0x440), (2, 64, 192, None), (3, 192, 256, 0x480)],
                   [(3, 0, 64, 0x4C0), (4, 64, 192, None), (5, 192, 256, 0x500)],
                   [(5, 0, 64, 0x540)]]
    rng = random.Random(9)
    hbm = bytes(rng.randrange(256) for _ in range(1 << 15))
    for _ in range(150):
        size = rng.choice((128, 256, 264))
        u0 = rng.randrange(0, 1 << 14, 8)
        nu = rng.randint(1, 12)
        raw, ring = 0x100, bytearray(1 << 14)
        split = [(u0 + u * size) // 256 != (u0 + (u + 1) * size - 1) // 256 for u in range(nu)]
        whole = {}
        a, end = u0, u0 + nu * size
        resp = []
        while a < end:                                  # requests of the entry, cut at 2 KiB blocks
            n = min(end - a, 2048 - a % 2048)
            resp += [(a, r) for r in I.rd_rsp_layout(a, n, 0, unit=(u0, size, raw))]
            a += n
        rng.shuffle(resp)                               # responses of one request arrive in any order
        for a, r in resp:
            gb = (a // 256 + r["gidx"]) * 256
            for p in r["pieces"]:
                if p["raw"] is None:                    # a whole unit: one piece, inside this granule
                    assert not split[p["unit"]] and p["off"] == 0 and p["hi"] - p["lo"] == size
                    whole[p["unit"]] = hbm[gb + p["lo"]:gb + p["hi"]]
                else:
                    ring[p["raw"]:p["raw"] + p["hi"] - p["lo"]] = hbm[gb + p["lo"]:gb + p["hi"]]
        pitch = -(-size // 16) * 16
        for u in range(nu):
            src = hbm[u0 + u * size:u0 + (u + 1) * size]
            if split[u]:
                k = sum(split[:u])
                assert bytes(ring[raw + k * pitch:raw + k * pitch + size]) == src, (size, u0, u)
            else:
                assert whole[u] == src
    H = 0x2000                                                        # HBM rows at offset H of the random image
    rows = [(H + 512 * i, 0x2000 + 576 * i, 512) for i in range(4)]    # s1 = 512 into SPM rows of pitch 576
    reqs = I.read_requests(rows)
    assert reqs == [(H + 512 * i, 512, 0x2000 + 576 * i) for i in range(4)]
    assert I.read_requests([(H + 512 * i, 0x2000 + 512 * i, 512) for i in range(4)]) == [(H, 2048, 0x2000)]
    spm = bytearray(1 << 14)
    for a, n, d in reqs:
        for x in I.rd_rsp_layout(a, n, d):
            gb = (a // 256 + x["gidx"]) * 256
            spm[x["spm"]:x["spm"] + x["hi"] - x["lo"]] = hbm[gb + x["lo"]:gb + x["hi"]]
    assert all(bytes(spm[0x2000 + 576 * i:0x2000 + 576 * i + 512]) == hbm[H + 512 * i:H + 512 * i + 512] for i in range(4))
    bad = I.rd_rsp_layout(H, 1024, 0x2000)                            # the merged request the rule forbids
    assert bad[2]["spm"] == 0x2000 + 512 != 0x2000 + 576


# ---------------------------------------------------------------- non-paged block operands (memmap.md section 6, review W-5)
def test_block_addr_requires_block_aligned_base(isa):
    """W-5: an NVFP4 weight slice that starts at element 16 of a row (block 1) gets block 1's scales for every element,
    from scale_reg = the scale of the base element's block. A slice that starts at element 8 faults ILLEGAL_FIELD: before,
    its element 8 (row element 16, block 1) was converted with block 0's scale."""
    K = 4096                                    # elements per row: 2,048 B of data, 256 B of scales
    td = _td(isa, dtype="NVFP4", space="GLOBAL", n0=256, n1=4, s1=K // 2, aux0=K // 16)
    w, sc = 0x2000_0000, 0x3000_0000            # the tensor's data and its scale array
    for first in (16, 512):                     # slice start, in elements
        for i1 in range(4):
            for i0 in range(256):
                d, s = I.block_addr(isa, td, w + first // 2, sc + first // 16, i0, i1)
                e = first + i0                  # element index in the full row
                assert d == w + i1 * K // 2 + e // 2
                assert s == sc + i1 * K // 16 + e // 16
    with pytest.raises(I.IsaError):
        I.block_addr(isa, td, w + 8 // 2, sc, 0, 0)                            # element 8: inside block 0
    with pytest.raises(I.IsaError):
        I.block_addr(isa, dict(td, s1=K // 2 + 4), w, sc, 0, 1)                # a row pitch that is not whole blocks
    bf = _td(isa, dtype="BF16", space="GLOBAL", n0=8, n1=1, s1=6)
    assert I.block_addr(isa, bf, 0x100 + 2, 0, 3, 0) == (0x100 + 2 + 6, None)  # no block format, no rule


def test_engram_inline_gather(isa):
    """Z-4: a DMA_GATHER of inline-scaled MXFP8 rows (SCALE_INLINE: 256 B of elements, then 8 B of scales, 264 B per row;
    a table a runtime repacks, since the Engram table as published keeps a separate scale array, review Y-7). The base and the row pitch s1 = 264 need no block alignment, because every row starts its own first block; the
    scale of element i0 is at the row start + 256 + floor(i0 / 32). Before Z-4, isa.block_addr faulted ILLEGAL_FIELD at row 0.
    A row that crosses a 2 KiB block is two reads, both in the row's inline entry (no scale read; 264 B raw slot)."""
    td = _td(isa, dtype="MXFP8", space="GLOBAL", flags=("SCALE_INLINE",), n0=256, n1=1, s1=264, row_bytes=264)
    assert I.inline_row_bytes(isa, td) == 264
    table = 0x40_0000_0008                      # any byte address
    for idx in (0, 1, 7, 12345, (1 << 20) + 3):
        row = table + idx * 264
        for i0 in (0, 31, 32, 255):
            assert I.block_addr(isa, td, table, 0, i0, idx) == (row + i0, row + 256 + i0 // 32)
            assert I.inline_addr(isa, td, table, i0, idx) == (row + i0, row + 256 + i0 // 32)
    sep = _td(isa, dtype="MXFP8", space="GLOBAL", n0=256, n1=1, s1=264, aux0=8)   # separate scale array: W-5 still applies
    with pytest.raises(I.IsaError):
        I.block_addr(isa, sep, table, 0, 0, 1)
    for bad in (dict(s1=256), dict(row_bytes=256), dict(n0=250, s1=264, row_bytes=0), dict(n2=2)):
        with pytest.raises(I.IsaError):
            I.inline_addr(isa, dict(td, **bad), table, 0, 0)
    assert I.inline_addr(isa, dict(td, row_bytes=0), table, 0, 1)[0] == table + 264         # DMA_COPY: row_bytes unused
    with pytest.raises(I.IsaError):                                          # Y-7: a gather's row_bytes is the row's size
        I.block_addr(isa, dict(td, row_bytes=0), table, 0, 0, 1, gather=True)
    with pytest.raises(I.IsaError):                                          # PAGED keeps scales in the page's region
        I.inline_addr(isa, dict(td, flags=td["flags"] | 1 << isa["enums"]["td_flags"]["PAGED"]), table, 0, 0)
    # Ring entries of the gather: one row each, staged raw (inline scales are part of the row's bytes).
    idx = random.Random(3).sample(range(1 << 20), 1500) + [7]               # row 7 crosses a 2 KiB block (1,848 .. 2,112)
    rows = [table - 8 + i * 264 for i in idx]
    assert rows[-1] // 2048 != (rows[-1] + 263) // 2048
    stream = [(0, 264, 0, 264, 256, a) for a in rows]
    plan = I._ring_reads(stream, "cut")[-1]
    assert len(plan) == 2 and len({r[0] // 8 for r in plan}) == 2 and all(r[2][0][0] == len(rows) - 1 for r in plan)
    assert I.staging_ring([stream], ring=2048)["done"]                       # the minimum ring: live, about 7 rows held
    assert 0.10 < I.staging_ring([stream])["rate"] / 256 < 0.15               # 16 KiB: about 0.12 rows per cycle


def test_engram_separate_array_gather(isa):
    """Y-7: the Engram table as published (DeepSeek: about 384 M rows x 256 FP8 per layer, an E8M0 scale per 32) is stored as
    is: 256 B rows and a separate scale array of 8 B rows (s1 = 256, aux0 = 8), so no load-time repack. A gather row is one
    aligned granule plus an 8 B scale read (one 32 B burst): 288 B of HBM per row, 13.8 KB per token for 48 rows (two
    layers x 24), the same bytes as 264 B inline rows at 8 B-aligned offsets (9 bursts). The rows are not split, so they
    are not staged raw, and the gather runs at more than twice the inline rate (about 0.33 against 0.12 rows/cycle at L =
    500; its one-granule entries are paced in full, X-14)."""
    td = _td(isa, dtype="MXFP8", space="GLOBAL", n0=256, n1=1, s1=256, aux0=8, row_bytes=256)
    table, scales = 0x40_0000_0000, 0x50_0000_0000
    for idx in (0, 1, 12345, (1 << 20) + 3):
        for i0 in (0, 31, 32, 255):
            assert I.block_addr(isa, td, table, scales, i0, idx, gather=True) == \
                (table + idx * 256 + i0, scales + idx * 8 + i0 // 32)
    row = I.rd_rsp_layout(table + 12345 * 256, 256, 0)
    sc = I.rd_rsp_layout(scales + 12345 * 8, 8, 0)
    per_row = 32 * (len(row[0]["bursts"]) + len(sc[0]["bursts"]))
    assert per_row == 288 and 48 * per_row == 13824
    inline = [I.rd_rsp_layout(8 + i * 264, 264, 0) for i in range(4)]       # inline rows from an 8 B-aligned base
    assert all(32 * sum(len(x["bursts"]) for x in r) == 288 for r in inline)
    idx = random.Random(4).sample(range(1 << 20), 1500)
    stream = [(8, 0, 1, 256, 256, table + i * 256) for i in idx]
    eng = [(0, 264, 0, 264, 256, table + 8 + i * 264) for i in idx]
    run = dict(hbm="server", inflight=I.PACE_DEFAULT, fmt=["MXFP8"], rt=500)
    sep, inl = I.staging_ring([stream], **run)["rate"] / 256, I.staging_ring([eng], **run)["rate"] / 256
    assert sep > 2 * inl and 0.10 < inl < 0.15, (sep, inl)


# ---------------------------------------------------------------- batched append and page bounds (memmap.md section 6, W-4)
def test_paged_scatter_append(isa):
    """W-4 (b): a batch-decode append of the DeepSeek MXFP4 index key is one DMA_SCATTER per TD. The data scatter has one
    index per sequence (64 B rows). The scale scatter needs 32 / aux0 = 8 consecutive indices per sequence, from the
    block's first row, so each sequence's block is one aligned 32 B packet; one index per sequence gives 4 B packets
    (HBM_ALIGN)."""
    a = APPENDS["deepseek_index_key"]
    common = dict(space="GLOBAL", s1=a["s1"], aux0=a["aux0"], page_shift=a["page_shift"], sc_off=a["sc_off"])
    td_data = _td(isa, dtype="UINT8", flags=("PAGED",), n0=64, n1=1, row_bytes=64, **common)
    td_sc = _td(isa, dtype="UINT8", flags=("PAGED", "SCALE_REGION"), n0=4, n1=1, row_bytes=4, **common)
    ptr = I.unpack(isa, "paged_ptr", I.pack(isa, "paged_ptr", node=0x7F, row=0, byte=0))
    rows = 1 << a["page_shift"]
    # four sequences, each with its own pages (logical rows seq * 64K + t), token positions anywhere in a block
    seqs = [(0, 6027), (1, 300), (2, 1023), (3, 8)]
    frames = {}
    for s, t in seqs:
        frames[((s << 16) + t) >> a["page_shift"]] = 0x100000 + 17 * s
    logical = [(s << 16) + t for s, t in seqs]

    def scatter(td, idx):
        runs = [(I.paged_addr(isa, td, ptr, frames, r, i0)[0], 1) for r in idx for i0 in range(td["n0"])]
        return I.write_packets(runs)

    data = scatter(td_data, logical)
    assert len(data) == 4 and all(x % 32 == 0 and n == 64 for x, n in data)
    per_blk = 32 // a["aux0"]
    blocks = [r - r % per_blk + j for r in logical for j in range(per_blk)]
    sc = scatter(td_sc, blocks)
    assert len(sc) == 4 and all(x % 32 == 0 and n == 32 for x, n in sc)
    for (s, t), (x, _) in zip(seqs, sc):
        q = t & (rows - 1)
        assert x == (0x100000 + 17 * s) * 4096 + a["sc_off"] * 256 + (q - q % per_blk) * a["aux0"]
    one = scatter(td_sc, logical)                                            # one index per sequence: the W-4 failure
    assert all(n == 4 for _, n in one)


def test_paged_scale_rows_tile_whole_blocks(isa):
    """W-4 (c): a page's scale rows must tile whole aligned 32 B blocks (aux0 divides 32 or is a multiple of it, and
    rows per page >= 32 / aux0), else ILLEGAL_FIELD. With page_shift 2 and 4 B rows every block would span two pages
    and be two 16 B writes."""
    ptr = I.unpack(isa, "paged_ptr", I.pack(isa, "paged_ptr", node=0, row=0, byte=0))
    frames = {i: 0x200 + i for i in range(64)}
    base = dict(space="GLOBAL", s1=64, n0=4, n1=1, sc_off=1)
    for aux0, ps, ok in ((4, 3, True), (4, 2, False), (8, 2, True), (8, 1, False), (32, 0, True), (64, 0, True),
                         (12, 4, False), (0, 4, False)):
        td = _td(isa, dtype="UINT8", flags=("PAGED", "SCALE_REGION"), aux0=aux0, page_shift=ps, **base)
        if ok:
            I.paged_addr(isa, td, ptr, frames, 1)
        else:
            with pytest.raises(I.IsaError):
                I.paged_addr(isa, td, ptr, frames, 1)
    # the same bound for a block-format read of the page, and its row pitch in whole blocks (W-5)
    td = _td(isa, dtype="MXFP4", flags=("PAGED",), aux0=4, page_shift=2, **dict(base, n0=128))
    with pytest.raises(I.IsaError):
        I.paged_addr(isa, td, ptr, frames, 1)
    td = _td(isa, dtype="MXFP4", flags=("PAGED",), aux0=4, page_shift=3, **dict(base, n0=128, s1=72))
    with pytest.raises(I.IsaError):
        I.paged_addr(isa, td, ptr, frames, 1)
