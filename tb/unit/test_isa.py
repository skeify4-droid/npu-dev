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
