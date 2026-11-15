#!/usr/bin/env python3
"""Review part TR (independent review of the test changes of commit 914ee30, mxu_review.md part TR): new one-change mutants of
rtl/core/mxu*.sv aimed at the checks that part T's responses added to tb_mxu (acc_done / sem_inc retirement, acc_last, per-cycle
fp_flags, legal-boundary fault cases at DIM 8 and 16, K = n KTILE + 1, CS_DEPTH / SPM_LAT, sparse SPM and 2048 ACC rows, the
semaphore wrap, the period bound).

Written after reading the diff of 914ee30 and the RTL, before any run; `pred` is the prediction made then (kill / survive / equiv).
No mutant of part T (tq_mutants.py) or of the author (mxu_mutants.py) is repeated. Same machinery as tq_mutants.py: each mutant is a
copy of rtl/ in build/mxu_review_tr/mut/<name>/rtl (core copied, arith and common symlinked) with exactly one textual change, judged
by the real recipe

    make unit RTL_DIR=<copy>/rtl UNIT_TB="tb/unit/tb_mxu_array.sv tb/unit/tb_mxu.sv" BUILD=<worker copy>

(vector locks, compile in build/mxu, grep for PASS and for vector-file warnings). Mutants of mxu_array's flag path are also run as
<name>@mxu with UNIT_TB = tb_mxu.sv alone, to see whether the new per-cycle flag check of tb_mxu kills them without tb_mxu_array.

Usage (a worker peaks at about 2.3 GB):
  python3 scripts/mxu_review/tr_mutants.py check | list
  scripts/memrun tr_mutants python3 scripts/mxu_review/tr_mutants.py run [--jobs N] [--no-baseline] [NAME ...]
Results: build/mxu_review_tr/results.tsv (name, area, pred, result, seconds, verdict, checks that fired per instance, first line).
"""
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WORK = os.path.join(ROOT, "build", "mxu_review_tr")
TBS = "tb/unit/tb_mxu_array.sv tb/unit/tb_mxu.sv"
TB_MXU = "tb/unit/tb_mxu.sv"
TIMEOUT = 3000
D, X, A = "mxu_dec.sv", "mxu.sv", "mxu_array.sv"
M = []


def mut(name, area, f, old, new, pred, why, both=False):
    M.append(dict(name=name, area=area, file=f, old=old, new=new, pred=pred, why=why, tbs=TBS))
    if both:      # also against tb_mxu alone (the array test would kill it first)
        M.append(dict(name=name + "@mxu", area=area, file=f, old=old, new=new, pred=both, why=why + " [tb_mxu alone]", tbs=TB_MXU))


# ------------------------------------------------------------------ retirement: acc_done, the in-flight FIFO, sem_inc (mxu.sv)
mut("tr_inf_head_newest", "retire", X, "assign inf_head = inf_mem[inf_rd];", "assign inf_head = inf_mem[inf_wr - 2'd1];", "kill",
    "sem_inc takes the newest in-flight entry instead of the oldest: wrong {idx, amt} whenever two instructions are in flight")
mut("tr_pop_row_or_done", "retire", X, "assign inf_pop = acc_done && (inf_cnt != 3'd0);",
    "assign inf_pop = (acc_done || (acc_valid && acc_last)) && (inf_cnt != 3'd0);", "kill",
    "two retirements per instruction (at the acc_last row and at acc_done): early and extra sem_inc")
mut("tr_pop_keep_last", "retire", X, "assign inf_pop = acc_done && (inf_cnt != 3'd0);", "assign inf_pop = acc_done && (inf_cnt > 3'd1);",
    "kill", "the last in-flight instruction never retires: sem_inc missing at the end, TIMEOUT")
mut("tr_zinc_with_pop", "retire", X, ".inf_empty(inf_cnt == 3'd0)", ".inf_empty(inf_cnt == 3'd0 || (inf_cnt == 3'd1 && acc_done))",
    "kill", "an M = 0 instruction completes in the cycle of the previous acc_done: z_inc and inf_pop coincide and the earlier sem_inc is lost (S-7 case: #9 ssem 3, #10 M = 0)")
mut("tr_inf_room3", "retire", X, ".inf_room(inf_cnt < 3'd4)", ".inf_room(inf_cnt < 3'd3)", "kill",
    "in-flight FIFO used to depth 3 only: correct and slower; only the EXP_INF_FULL coverage counter of t8d (inf_cnt == 4) can see it")
mut("tr_sem_amt_b2", "retire", X, "assign sem_inc_amt   = sem_inc_w[3:0];", "assign sem_inc_amt   = {sem_inc_w[3], 1'b0, sem_inc_w[1:0]};",
    "kill", "bit 2 of sinc dropped: sinc 15 / 11 / 9 differ (T-9 values)")
mut("tr_sem_idx_b4", "retire", X, "assign sem_inc_idx   = sem_inc_w[9:4];", "assign sem_inc_idx   = {sem_inc_w[9], 1'b0, sem_inc_w[7:4]};",
    "kill", "bit 4 of ssem dropped: ssem 63 becomes 47")
mut("tr_sem_amt0", "retire", X, "assign sem_inc_valid = (inf_pop || z_inc) && (sem_inc_w[9:4] != 6'd0) && (sem_inc_w[3:0] != 4'd0);",
    "assign sem_inc_valid = (inf_pop || z_inc) && (sem_inc_w[9:4] != 6'd0);", "equiv",
    "a sem_inc with amount 0 for ssem != 0, sinc = 0: adds 0; no program has such an instruction, so the sequence check cannot see it")
# ------------------------------------------------------------------ acc_last
mut("tr_last_first_row", "acc_last", X, "assign a_lasti = a_lastp && s_last;", "assign a_lasti = a_first && s_last;", "kill",
    "acc_last on the first row of the last pass: acc_done (and sem_inc) before the last rows are written")
mut("tr_last_every_pass_jlast", "acc_last", X, "tk_last <= last_tile;", "tk_last <= (l_j == c_NT - 16'd1);", "kill",
    "acc_last on every K pass of the last N tile: several acc_done per instruction")
mut("tr_acc_last_unqual", "acc_last", X, "assign acc_last      = y_valid && x_lasti;", "assign acc_last      = x_lasti;", "equiv",
    "acc_last not qualified by acc_valid: the payload is contract only in the acc_valid cycle (mxu.md 5), the testbench looks only there")
# ------------------------------------------------------------------ flags
mut("tr_flags_reg1", "flags", X, "assign fp_flags      = arr_flags;",
    "logic [3:0] tr_fq; always_ff @(posedge clk) tr_fq <= arr_flags; assign fp_flags = tr_fq;", "kill",
    "fp_flags registered once (one cycle late): still before acc_done / sem_inc (acc.md ACC-6), so legal by the completion contract; "
    "the exact-cycle check fails it")
mut("tr_flags_validonly", "flags", X, "assign fp_flags      = arr_flags;", "assign fp_flags      = y_valid ? arr_flags : 4'd0;", "kill",
    "flags dropped in cycles without an output row: the PE rows of a wave report before its row leaves, in bubbles they are lost")
mut("tr_arr_mask_le", "flags", A, "(NCW'(j) < sn[i+2])", "(NCW'(j) <= sn[i+2])", "kill",
    "column N (the first dead one) flagged: BF16 Inf against its zero W is INV", both="kill")
mut("tr_arr_mask_stage", "flags", A, "(NCW'(j) < sn[i+2])", "(NCW'(j) < sn[i+1])", "kill",
    "the column mask of row i taken from the wave behind (sideband stage i+1)", both="survive")
mut("tr_arr_valid_stage", "flags", A, "assign masked[j] = (sv[i+2] &&", "assign masked[j] = (sv[i+1] &&", "kill",
    "the valid of row i taken from the wave behind: flags of a wave followed by a bubble are lost, a bubble's slot reports", both="kill")
mut("tr_arr_row_drop_last", "flags", A, "for (int i = 0; i < ROWS; i++) flags = flags | rq[i];",
    "for (int i = 0; i < ROWS - 1; i++) flags = flags | rq[i];", "kill", "the flags of the bottom PE row are never reported", both="kill")
# ------------------------------------------------------------------ legality comparators (mxu_dec), DIM 8 and 16
mut("tr_w_ext_norow", "legal", D, "w_ext  = 96'(r_w) + 96'(w_prod) + 96'(wk_b);", "w_ext  = 96'(r_w) + 96'(w_prod);", "survive",
    "W extent without the length of its last row: a W whose last row starts inside the SPM and runs past the end is not a RANGE "
    "fault; every illegal W case overruns by >= 480 B (base at SPM, or rows of 1 MiB)")
mut("tr_w_bf16_elem", "legal", D, "wk_b   = bf16 ? {w_elem, 1'b0} : {1'b0, w_elem};", "wk_b   = {1'b0, w_elem};", "survive",
    "BF16 W row counted in elements, not bytes: no BF16 W fault case")
mut("tr_a_range_tol31", "legal", D, "f_r_a   = !m_zero && (a_ext > 96'(SPM_BYTES));", "f_r_a   = !m_zero && (a_ext > 96'(SPM_BYTES) + 96'd31);",
    "survive", "an A overrun of 1 .. 31 B is not a fault: the smallest A overrun of the fault list is 32 B (BF16 K = 48, and 4 DIM at DIM 8)")
mut("tr_rs_ext_2m", "legal", D, "rs_ext = 96'(r_rs) + 96'({vM[RW-1:0], 2'd0});", "rs_ext = 96'(r_rs) + 96'({vM[RW-1:0], 1'd0});", "kill",
    "control: rowscale extent 2 M bytes; the 17-value case overruns by 4 B")
mut("tr_cs_ext_2n", "legal", D, "cs_ext = 96'(r_cs) + 96'(cs_prod) + 96'(vN) * 96'd4;", "cs_ext = 96'(r_cs) + 96'(cs_prod) + 96'(vN) * 96'd2;",
    "kill", "control: last scale row counted as 2 N bytes; the N = 17 case overruns by 4 B")
mut("tr_ktf_bf16", "legal", D, "(bf16 ? 17'd127 : 17'd255)", "(bf16 ? 17'd126 : 17'd255)", "survive",
    "rows of the BF16 scale array one short when K = 128 n + 1 (only the cs RANGE check uses vKTF): no such case")
mut("tr_acc_nt7", "legal", D, "(NTW+RW+1)'(vNT * vM[RW-1:0]);", "(NTW+RW+1)'(NTW'((17'(vN) + 17'd7) >> LD) * vM[RW-1:0]);", "kill",
    "the ACC check rounds N / DIM up with DIM 8's constant: exact at DIM 8, one tile short at DIM 16 for N mod 16 in 1 .. 8; "
    "only tf16 (case 49, N = 72) sees it")
mut("tr_acc_ge16", "legal", D, "(acc_sum > (NTW+RW+1)'(ACC_ROWS))", "(acc_sum > (NTW+RW+1)'(ACC_ROWS - ((DIM == 16) ? 1 : 0)))", "kill",
    "the ACC comparator on the boundary at DIM 16 only: the legal cases of mxu_fault_16 that end at ACCR fault")
mut("tr_wait_signed", "legal", D, "(32'($signed(sem_rd_val - q_wval)) < 32'h8000_0000)", "($signed(sem_rd_val) >= $signed(q_wval))",
    "survive", "the wait compares signed values instead of the serial number: identical across 2^32 (the T-9 case), wrong across 2^31")
mut("tr_kt_bf16_ceil", "legal", D, "(bf16 ? 17'(2 * DIM - 1) : 17'(4 * DIM - 1))", "(bf16 ? 17'(2 * DIM - 2) : 17'(4 * DIM - 1))", "kill",
    "BF16 K tiles one short when K = n 2 DIM + 1 (T-3 cases K = 2 D + 1, 4 D + 1)")
# ------------------------------------------------------------------ address and row counters (sparse windows, T-7)
mut("tr_a_carry16", "address", X, "s_addr <= s_addr + s_a_pitch;", "s_addr <= {s_addr[21:16], 16'(s_addr[15:0] + s_a_pitch[15:0])};",
    "survive", "the A row adder drops the carry into bit 16: no A operand of any program crosses a 64 KiB boundary (tr_vec_scan.py)")
mut("tr_accrow_carry8", "address", X, "s_accrow <= s_accrow + 11'd1;", "s_accrow <= {s_accrow[10:8], s_accrow[7:0] + 8'd1};", "survive",
    "the ACC row counter drops the carry into bit 8: no pass of any program crosses a multiple of 256 rows (sparse bases 1030, 1500, "
    "2016, 1100: tr_vec_scan.py)")
mut("tr_accbase_carry8", "address", X, "s_accrow <= tk_acc_base + 11'(64'(tk_j) * 64'(tk_M));",
    "s_accrow <= {tk_acc_base[10:8], 8'(tk_acc_base[7:0] + 8'(64'(tk_j) * 64'(tk_M)))};", "survive",
    "the pass start acc_base + j M drops the carry into bit 8: never crossed")
mut("tr_a_swap2021", "address", X, "assign a_lane_addr[22*l +: 22] = s_addr + 22'(l * LB);",
    "assign a_lane_addr[22*l +: 22] = {s_addr[20], s_addr[21], s_addr[19:0]} + 22'(l * LB);", "survive",
    "A address bits 20 and 21 swapped: the sparse A window 0x3E0000 has bits 17 .. 21 all set, the dense ones all clear")
mut("tr_cs_shift32", "address", X, "64'(c_csp) * (64'(l_t) >> CS_SHIFT)", "64'(c_csp) * (64'(l_t) >> ((DIM == 32) ? CS_SHIFT + 1 : CS_SHIFT))",
    "survive", "DIM 32 uses one scale row per 4 passes instead of 2: the DIM 32 column-scale case has one fold tile (K = 256, 2 passes)")
# ------------------------------------------------------------------ CS_DEPTH / SPM_LAT (T-6)
mut("tr_cs_rd_pow2", "fifo", X, "if (cs_free_ev) cs_rd <= (cs_rd == $bits(cs_rd)'(CS_DEPTH - 1)) ? '0 : cs_rd + 1'b1;",
    "if (cs_free_ev) cs_rd <= cs_rd + 1'b1;", "survive",
    "cs read pointer wraps at 2^bits, not at CS_DEPTH: equivalent at CS_DEPTH 2 and 4 (the only ones run), wrong at 3")
mut("tr_cs_full_le", "fifo", X, "if (!t_cs_en || cs_cnt < CS_DEPTH[$clog2(CS_DEPTH):0]) lst <= L_RUN;",
    "if (!t_cs_en || cs_cnt <= CS_DEPTH[$clog2(CS_DEPTH):0]) lst <= L_RUN;", "kill",
    "one column-scale tile more than CS_DEPTH: overwrites a slot in use (t8l / t16l fill the FIFO)")
mut("tr_cs_depth_m1", "fifo", X, "if (!t_cs_en || cs_cnt < CS_DEPTH[$clog2(CS_DEPTH):0]) lst <= L_RUN;",
    "if (!t_cs_en || cs_cnt < CS_DEPTH[$clog2(CS_DEPTH):0] - 1) lst <= L_RUN;", "kill",
    "one slot of the cs FIFO unused: correct and slower; only the EXP_CS_FULL counter (cs_cnt >= CS_DEPTH) can see it")
mut("tr_atag_cap4", "fifo", X, "= a_tag[SPM_LAT-1];", "= a_tag[(SPM_LAT > 4) ? 3 : SPM_LAT-1];", "survive",
    "the A response tag is taken at latency 4 when SPM_LAT >= 5: equivalent at SPM_LAT 1 .. 4, the instances run 1, 2, 3")
# ------------------------------------------------------------------ gaps and period (T-4, T-10)
mut("tr_gap_plus1_m2", "period", X, "gap_cnt <= (s_Mrows >= 16'(ACC_LAT)) ? 8'd0 : 8'(ACC_LAT) - 8'(s_Mrows);",
    "gap_cnt <= (s_Mrows >= 16'(ACC_LAT)) ? 8'd0 : 8'(ACC_LAT) - 8'(s_Mrows) + 8'(s_Mrows > 16'd1);", "survive",
    "one idle cycle more after every pass with 2 <= M < ACC_LAT: slower, the period bound covers M = 1 only")
mut("tr_gapx_m7_short", "period", X, "if (s_last && s_Mrows >= 16'd2) gap_cnt <= 8'(GAP_X);",
    "if (s_last && s_Mrows >= 16'd2) gap_cnt <= 8'(GAP_X) - 8'(s_Mrows == 16'd7);", "kill",
    "control for T-4: the boundary gap one cycle short after an M = 7 instruction (ACC_LAT - 1 at ACC_LAT 8)")


def check():
    ok = True
    names = set()
    for m in M:
        assert m["name"] not in names, m["name"]
        names.add(m["name"])
        n = open(os.path.join(ROOT, "rtl", "core", m["file"])).read().count(m["old"])
        if n != 1:
            print("BAD %s: `old` occurs %d times in %s" % (m["name"], n, m["file"]))
            ok = False
    print("%d mutant runs (%d distinct mutants), %s" % (len(M), len([m for m in M if "@" not in m["name"]]),
                                                       "all `old` strings unique" if ok else "ERRORS"))
    return ok


def make_copy(m):
    d = os.path.join(WORK, "mut", m["name"] if m else "none", "rtl")
    shutil.rmtree(os.path.dirname(d), ignore_errors=True)
    os.makedirs(d)
    shutil.copytree(os.path.join(ROOT, "rtl", "core"), os.path.join(d, "core"))
    for g in ("arith", "common"):
        os.symlink(os.path.join(ROOT, "rtl", g), os.path.join(d, g))
    if m:
        p = os.path.join(d, "core", m["file"])
        s = open(p).read()
        assert s.count(m["old"]) == 1
        open(p, "w").write(s.replace(m["old"], m["new"]))
    return d


def worker_build(k):
    """build/mxu_review_tr/w<k>: a copy of build/arith and build/mxu (time stamps kept) for worker k."""
    b = os.path.join(WORK, "w%d" % k)
    if not os.path.isdir(os.path.join(b, "mxu")):
        shutil.rmtree(b, ignore_errors=True)
        os.makedirs(b)
        for g in ("arith", "mxu"):
            subprocess.run(["cp", "-Rp", os.path.join(ROOT, "build", g), b], check=True)
    return b


# check categories of tb_mxu / tb_mxu_array messages (for "which check killed it")
CATS = [("seq", r"sem_inc #\d+ is"), ("early", r"sem_inc of instruction .* acc_done pulses"), ("extra_inc", r"unexpected sem_inc"),
        ("acc_last", r"acc_last = "), ("flag_cyc", r"fp_flags [0-9a-fx]+ at cycle"), ("flag_or", r"fp_flags OR"),
        ("acc_data", r"ACC\[\d+\]\[\d+\] ="), ("hazard", r"ACC hazard"), ("period", r"pass period"), ("timeout", r"TIMEOUT"),
        ("fault", r"fault case \d+"), ("legal", r"legal case \d+"), ("lane", r"bad [AWS] (lane )?addr"), ("cs_cov", r"column-scale FIFO was never full"),
        ("inf_cov", r"in-flight FIFO was never full"), ("sem_final", r"sem\[\d+\] = "), ("order", r"out-of-order"), ("rows", r"ACC rows, \d+ expected"),
        ("ndone", r"acc_done pulses for"), ("acc_range", r"ACC row \d+ out of range"), ("beyond", r"beyond the last instruction"),
        ("wait", r"started before its wait"), ("spur", r"acc_done with nothing in flight"), ("array", r"^FAIL mxu_array|MISMATCH"),
        ("fault_port", r": fault cause")]


def categorize(out):
    hit = {}
    for ln in out.splitlines():
        pre = ln.split(":")[0] if ln.startswith(("mxu_", "fault", "legal")) else ""
        for c, rx in CATS:
            if re.search(rx, ln):
                if c in ("fault", "legal"):
                    pre = "tf16" if "fault_16" in ln else "tf8"
                hit.setdefault(c, set()).add(pre.rstrip("_") or "-")
    return ";".join("%s(%s)" % (c, ",".join(sorted(v))) for c, v in sorted(hit.items()))


def run_one(m, k=0):
    d = make_copy(m)
    b = worker_build(k)
    t0 = time.time()
    tbs = m["tbs"] if m else TBS
    try:
        r = subprocess.run(["make", "unit", "RTL_DIR=" + d, "UNIT_TB=" + tbs, "BUILD=" + b], cwd=ROOT, capture_output=True, text=True,
                           timeout=TIMEOUT)
        out, rc = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        rc = "timeout"
    dt = time.time() - t0
    name = m["name"] if m else "none"
    open(os.path.join(WORK, "mut", name, "make_unit.log"), "w").write(out)
    passed = rc == 0 and "unit: ALL PASS" in out
    first = ""
    for ln in out.splitlines():
        if ln.startswith(("FAIL", "unit FAILED", "MISMATCH", "mxu_lock")) or "TIMEOUT" in ln or "error" in ln.lower():
            first = ln.strip()[:160]
            break
    if "unit FAILED (compile)" in out:
        return "compile_error", dt, "", first
    return ("survived" if passed else ("timeout" if rc == "timeout" else "killed")), dt, categorize(out), first


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "check":
        sys.exit(0 if check() else 1)
    if cmd == "list":
        for m in M:
            print("%-28s %-8s %-8s %s" % (m["name"], m["area"], m["pred"], m["why"]))
        return
    if cmd != "run":
        sys.exit(__doc__)
    if not check():
        sys.exit(1)
    args = sys.argv[2:]
    jobs, w0, base = 1, 0, True
    if "--jobs" in args:
        i = args.index("--jobs")
        jobs = int(args[i + 1])
        del args[i:i + 2]
    if "--w0" in args:
        i = args.index("--w0")
        w0 = int(args[i + 1])
        del args[i:i + 2]
    if "--no-baseline" in args:
        args.remove("--no-baseline")
        base = False
    want = args
    os.makedirs(os.path.join(WORK, "mut"), exist_ok=True)
    res_path = os.path.join(WORK, "results.tsv")
    done = set(l.split("\t")[0] for l in open(res_path)) if os.path.exists(res_path) and not want else set()
    todo = [m for m in M if (not want or m["name"] in want) and m["name"] not in done]
    import queue
    import threading
    q = queue.Queue()
    if base:
        q.put(None)
    for m in todo:
        q.put(m)
    print("%d runs queued (%d already in results.tsv), %d jobs" % (q.qsize(), len(done), jobs), flush=True)
    lock = threading.Lock()

    def work(k):
        while True:
            try:
                m = q.get_nowait()
            except queue.Empty:
                return
            res, dt, cats, first = run_one(m, k)
            if m is None:
                line = "\t".join(["none", "-", "survive", res, "%.0f" % dt, "baseline", cats, first])
            else:
                hit = {"kill": res != "survived", "survive": res == "survived", "equiv": res == "survived"}[m["pred"]]
                line = "\t".join([m["name"], m["area"], m["pred"], res, "%.0f" % dt, "ok" if hit else "PREDICTION WRONG", cats, first])
            with lock:
                with open(res_path, "a") as fh:
                    fh.write(line + "\n")
                print(line, flush=True)
    th = [threading.Thread(target=work, args=(w0 + k,)) for k in range(jobs)]
    for t in th:
        t.start()
    for t in th:
        t.join()


if __name__ == "__main__":
    main()
