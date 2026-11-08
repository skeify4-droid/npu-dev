#!/usr/bin/env python3
"""Lock file of the MXU test vectors: scripts/mxu_vectors.lock (committed) lists every file of build/mxu with its row count
and SHA-256, the same mechanism as scripts/arith_vectors.lock (scripts/arith_lock.py does the work; standard library only).

  mxu_lock.py verify [DIR]     exit 1 unless DIR (default build/mxu) holds exactly the locked files, byte for byte
  mxu_lock.py write  [DIR]     (re)write the lock from DIR; `gen_mxu_vectors.py --lock` does this after generating

`verify` rejects a missing, extra, changed, short or long file (rows and hash), a zero row count, a counts.svh that is not
the locked one or whose `define CNT_<file> <rows>` lines differ from the locked row counts. A change of the generator,
arith_ref.py, numerics.py or NumPy shows up as a diff of the lock; a stale or hand-edited build/mxu cannot pass `make unit`.
The random streams of NumPy's `default_rng` are not guaranteed across NumPy versions (re-check, then `--lock`).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arith_lock as L  # noqa: E402

LOCK = os.path.join(L.ROOT, "scripts", "mxu_vectors.lock")
DEFAULT_DIR = os.path.join(L.ROOT, "build", "mxu")
HEADER = """# Locked test vectors of the MXU unit tests (tb_mxu_array, tb_mxu): file, rows, SHA-256. Written by `scripts/gen_mxu_vectors.py --lock`,
# checked by `make unit` (scripts/mxu_lock.py verify). Do not edit; a diff of this file is a change of the vectors.
"""


def write(d=DEFAULT_DIR):
    L.write_lock(d, LOCK, HEADER)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    d = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DIR
    if cmd == "verify":
        bad = L.verify(d, LOCK)
        for b in bad:
            print("mxu_lock:", b)
        if bad:
            print(f"mxu_lock: {d} differs from scripts/mxu_vectors.lock ({len(bad)} problems); "
                  "run make mxu-vec, or scripts/gen_mxu_vectors.py --lock after an intended change")
            sys.exit(1)
        print(f"mxu_lock: {d} matches scripts/mxu_vectors.lock ({len(L.read_lock(LOCK))} files)")
    elif cmd == "write":
        write(d)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
