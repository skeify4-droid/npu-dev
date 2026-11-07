#!/usr/bin/env python3
"""Lock file of the arith test vectors: scripts/arith_vectors.lock (committed) lists every file of build/arith with its
row count and SHA-256. Standard library only, so `make unit`, run_arith_mutants.py and the reviewer's run_tb.sh can call it
without the Python environment of the generator.

  arith_lock.py verify [DIR]          exit 1 unless DIR (default build/arith) holds exactly the locked files, byte for byte
  arith_lock.py write  [DIR]          (re)write the lock from DIR; gen_arith_vectors.py --lock does this after generating

What `verify` rejects: a missing, extra, changed, short or long file (rows and hash), a zero row count, a counts.svh that
is not the one the lock names or whose `define CNT_<file> <rows>` lines differ from the locked row counts, and a lock that
lists nothing. A generator or reference change therefore shows up as a diff of the lock in review, and a stale, edited or
copied build/ cannot pass `make unit`.

Caveat: the random streams of NumPy's `default_rng` are not guaranteed across NumPy versions; after an upgrade the
regenerated files differ from the lock (correctly: re-check, then `gen_arith_vectors.py --lock`).
"""
import hashlib
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
LOCK = os.path.join(ROOT, "scripts", "arith_vectors.lock")
DEFAULT_DIR = os.path.join(ROOT, "build", "arith")
HEADER = """# Locked test vectors of the rtl/arith unit tests: file, rows, SHA-256. Written by `scripts/gen_arith_vectors.py --lock`,
# checked by `make unit` (scripts/arith_lock.py verify). Do not edit; a diff of this file is a change of the vectors.
"""


def scan(d):
    """name -> (rows, sha256) for every .hex file and counts.svh of d."""
    out = {}
    for f in sorted(os.listdir(d)):
        if not (f.endswith(".hex") or f == "counts.svh"):
            continue
        data = open(os.path.join(d, f), "rb").read()
        out[f] = (data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0), hashlib.sha256(data).hexdigest())
    return out


def read_lock(path=LOCK):
    lock = {}
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, rows, sha = line.split()
        lock[name] = (int(rows), sha)
    return lock


def write_lock(d=DEFAULT_DIR, path=LOCK):
    cur = scan(d)
    if not cur or "counts.svh" not in cur or any(r < 1 for r, _ in cur.values()):
        sys.exit(f"arith_lock: refusing to lock {d}: empty, no counts.svh, or a file with zero rows")
    with open(path, "w") as f:
        f.write(HEADER)
        for name, (rows, sha) in cur.items():
            f.write(f"{name} {rows} {sha}\n")
    print(f"arith_lock: wrote {os.path.relpath(path, ROOT)}: {len(cur)} files, {sum(r for n, (r, _) in cur.items() if n != 'counts.svh')} rows")


def verify(d=DEFAULT_DIR, path=LOCK):
    """List of problems (empty when d is exactly the locked vector set)."""
    bad = []
    if not os.path.isfile(path):
        return [f"no lock file {path}: run scripts/gen_arith_vectors.py --lock"]
    lock = read_lock(path)
    if not lock or "counts.svh" not in lock:
        return [f"{path} is empty or lists no counts.svh"]
    if not os.path.isdir(d):
        return [f"{d} does not exist (make arith-vec)"]
    cur = scan(d)
    for name, (rows, sha) in lock.items():
        if rows < 1:
            bad.append(f"{name}: the lock has a zero row count")
        if name not in cur:
            bad.append(f"{name}: missing")
        elif cur[name] != (rows, sha):
            bad.append(f"{name}: differs from the lock (rows {cur[name][0]} vs {rows}, sha {cur[name][1][:12]} vs {sha[:12]})")
    for name in cur:
        if name not in lock:
            bad.append(f"{name}: not in the lock (unexpected file)")
    if "counts.svh" in cur:     # explicit, so that the message names the macro (the hash alone already implies it)
        macros = dict((m, int(v)) for m, v in re.findall(r"`define\s+CNT_(\w+)\s+(\d+)", open(os.path.join(d, "counts.svh")).read()))
        for name, (rows, _) in lock.items():
            if name.endswith(".hex") and macros.get(name[:-4].upper()) != rows:
                bad.append(f"counts.svh: CNT_{name[:-4].upper()} is {macros.get(name[:-4].upper())}, the locked row count is {rows}")
        for m, v in macros.items():
            if v < 1:
                bad.append(f"counts.svh: CNT_{m} is {v} (a zero count)")
    return bad


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    d = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DIR
    if cmd == "verify":
        bad = verify(d)
        for b in bad:
            print("arith_lock:", b)
        if bad:
            print(f"arith_lock: {d} differs from scripts/arith_vectors.lock ({len(bad)} problems); "
                  "run make arith-vec, or scripts/gen_arith_vectors.py --lock after an intended change")
            sys.exit(1)
        print(f"arith_lock: {d} matches scripts/arith_vectors.lock ({len(read_lock())} files)")
    elif cmd == "write":
        write_lock(d)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
