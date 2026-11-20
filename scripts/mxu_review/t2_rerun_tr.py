#!/usr/bin/env python3
"""Review part TR2: re-run mutants of tr_mutants.py (part TR) against the current make unit (929d820), with the TR machinery but the
work directory build/mxu_review_t2/rerun_tr (part TR's results stay untouched). Usage: scripts/memrun t2_rerun_tr python3
scripts/mxu_review/t2_rerun_tr.py run [--jobs N] [--no-baseline] NAME ...   (results: build/mxu_review_t2/rerun_tr/results.tsv)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tr_mutants as T  # noqa: E402

T.WORK = os.path.join(T.ROOT, "build", "mxu_review_t2", "rerun_tr")
if __name__ == "__main__":
    T.main()
