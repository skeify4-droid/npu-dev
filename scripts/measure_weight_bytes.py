#!/usr/bin/env python3
"""Measure, from safetensors headers only (HTTP range requests, no weights downloaded), the bytes of each model's
published total that are not language-model text weights: vision/audio tensors and tensors stored twice.
Writes models/weight_bytes.tsv (used by npumodel.ingest for the weight-size check). Needs the network.

    python3 scripts/measure_weight_bytes.py [--only slug ...]
"""
import argparse, concurrent.futures as cf, csv, datetime, json, os, struct, urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NON_TEXT = ("vision", "aligner", "image_", "model.visual", "visual.", "audio_", "speech_embeddings", "multi_modal_projector",
            "patch_merge", "pre_mm_projector", "model.vision", "model.audio", "vit_large_projector")
INDEX = {"mistral_large_4": "consolidated.safetensors.index.json"}


def get(url, a=None, b=None):
    req = urllib.request.Request(url, headers={"Range": f"bytes={a}-{b}"} if a is not None else {})
    return urllib.request.urlopen(req, timeout=60).read()


def header(url):
    n = struct.unpack("<Q", get(url, 0, 7))[0]
    h = json.loads(get(url, 8, 8 + n - 1))
    h.pop("__metadata__", None)
    return h


def measure(repo, index_name):
    base = f"https://huggingface.co/{repo}/resolve/main/"
    wm = json.loads(get(base + index_name))["weight_map"]
    files = sorted(set(wm.values()))
    with cf.ThreadPoolExecutor(8) as ex:
        hdrs = dict(zip(files, ex.map(lambda f: header(base + f), files)))
    non_text = dup = 0
    for f, h in hdrs.items():
        for k, v in h.items():
            b = v["data_offsets"][1] - v["data_offsets"][0]
            if wm.get(k) != f:          # a copy the index does not point to (still counted in the published total)
                dup += b
            elif k.startswith(NON_TEXT):
                non_text += b
    return non_text, dup


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    repos = {r["slug"]: r["repo"] for r in csv.DictReader(open(os.path.join(ROOT, "models", "repos.tsv")), delimiter="\t")}
    out_p = os.path.join(ROOT, "models", "weight_bytes.tsv")
    rows = {r["slug"]: r for r in csv.DictReader(open(out_p), delimiter="\t")} if os.path.exists(out_p) else {}
    today = datetime.date.today().isoformat()
    for slug, repo in repos.items():
        if a.only and slug not in a.only:
            continue
        nt, dup = measure(repo, INDEX.get(slug, "model.safetensors.index.json"))
        rows[slug] = {"slug": slug, "non_text_bytes": nt, "duplicate_bytes": dup,
                      "source": f"safetensors headers of all shards: vision/audio tensors; tensors stored twice ({today})"}
        print(f"{slug:24s} non_text {nt / 1e9:7.3f} GB  duplicate {dup / 1e9:7.3f} GB", flush=True)
    with open(out_p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["slug", "non_text_bytes", "duplicate_bytes", "source"], delimiter="\t", lineterminator="\n")
        w.writeheader()
        for slug in repos:
            if slug in rows:
                w.writerow(rows[slug])


if __name__ == "__main__":
    main()
