"""Command line entry point.

Paid commands (they call the model API):
  python -m pipeline run --input data/cost_100.csv --run runs/pilot
Offline commands (no API key, no model calls):
  python -m pipeline rank --run runs/full          # rebuild ranking from saved membership + records
  python -m pipeline status --run runs/full
  python -m pipeline export --run runs/full --full data/spotify_reviews_18months.csv --out grading
"""
import argparse
import json
import sys
import time

from . import stages
from .common import write_json

ALL = ["prepare", "enrich", "verify", "group", "rank", "memo"]


def main():
    ap = argparse.ArgumentParser(prog="python -m pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run stages on an input CSV (calls the model API)")
    r.add_argument("--input", required=True)
    r.add_argument("--run", required=True, help="run folder, e.g. runs/full")
    r.add_argument("--stages", default=",".join(ALL))
    r.add_argument("--sample", type=int, help="classify a seeded sample of N nonempty reviews")
    r.add_argument("--include", nargs="*", default=[], help="CSV files whose IDs must be in the sample")
    r.add_argument("--workers", type=int)
    r.add_argument("--max-batches", type=int, help="stop enrichment after N batches (controlled interruption)")
    r.add_argument("--verify-sample", type=int)
    r.add_argument("--retry-failed", action="store_true")
    r.add_argument("--timing-out", help="write stage wall-clock seconds to this JSON file")
    for name in ("rank", "status"):
        s = sub.add_parser(name)
        s.add_argument("--run", required=True)
    pb = sub.add_parser("publish", help="copy saved run outputs into results/ (tracked by git)")
    pb.add_argument("--run", required=True)
    pb.add_argument("--out", default="results")
    e = sub.add_parser("export")
    e.add_argument("--run", required=True)
    e.add_argument("--full", required=True)
    e.add_argument("--out", default="grading")
    a = ap.parse_args()

    run = stages.Run(a.run)
    if a.cmd == "run":
        want = a.stages.split(",")
        timing, t_all = {}, time.time()
        for st in ALL:
            if st not in want:
                continue
            t0 = time.time()
            if st == "prepare":
                stages.prepare(run, a.input, sample=a.sample, include=a.include, seed=run.cfg["sample_seed"])
            elif st == "enrich":
                res = stages.enrich(run, workers=a.workers, max_batches=a.max_batches, retry_failed=a.retry_failed)
                if res.get("status") in ("stopped_budget", "stopped_max_batches"):
                    print(f"Enrichment stopped ({res['status']}); later stages skipped. Rerun the same command to resume.")
                    timing[st] = round(time.time() - t0, 3)
                    break
            elif st == "verify":
                stages.verify(run, a.verify_sample)
            elif st == "group":
                stages.group(run)
            elif st == "rank":
                stages.rank(run)
            elif st == "memo":
                stages.memo(run)
            timing[st] = round(time.time() - t0, 3)
        timing["end_to_end_wall_clock_s"] = round(time.time() - t_all, 3)
        print("Stage seconds:", json.dumps(timing))
        if a.timing_out:
            write_json(a.timing_out, timing)
        write_summary(run, timing)
    elif a.cmd == "rank":
        table = stages.rank(run)
        for row in table[:10]:
            print(row)
    elif a.cmd == "status":
        write_summary(run, None, show=True)
    elif a.cmd == "publish":
        publish(run, a.out)
    elif a.cmd == "export":
        from .export import export
        export(run, a.out, a.full)


def publish(run, out):
    import gzip
    import shutil
    from pathlib import Path
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    write_summary(run, None)
    for name in ("ingestion_report.json", "data_manifest.json", "run_summary.json", "run_log.jsonl", "calls.jsonl"):
        if (run.dir / name).exists():
            shutil.copy(run.dir / name, out / name)
    for folder in ("verify", "group", "rank", "memo", "checkpoints"):
        if (run.dir / folder).exists():
            shutil.copytree(run.dir / folder, out / folder, dirs_exist_ok=True)
    n_enr = n_q = 0
    with gzip.open(out / "enriched.jsonl.gz", "wt", encoding="utf-8") as fe, \
            open(out / "quarantine.jsonl", "w", encoding="utf-8") as fq:
        oos = 0
        for rec, meta in stages.final_records(run):
            if rec["status"] == "completed":
                fe.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_enr += 1
            elif rec["reason"].startswith("out_of_scope"):
                oos += 1
            else:
                fq.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_q += 1
    write_json(out / "quarantine_summary.json", {
        "quarantined_detail_rows_in_quarantine_jsonl": n_q, "out_of_scope_not_in_sample": oos,
        "note": "Out-of-scope rows are listed one by one in grading/records.jsonl.gz with reason "
                "out_of_scope_not_in_100k_sample; they are accounted for but not classified."})
    print(f"Published {n_enr} enriched records, {n_q} detailed quarantines ({oos} out-of-scope rows summarized) to {out}/")


def write_summary(run, timing, show=False):
    from collections import Counter
    st = Counter(r["status"] for r, _ in stages.final_records(run))
    calls = []
    if run.calls_path.exists():
        from .common import read_jsonl
        calls = read_jsonl(run.calls_path)
    by_role = {}
    for c in calls:
        d = by_role.setdefault(c["role"], Counter())
        d["attempts"] += 1
        d[c["outcome"]] += 1
        d["input_tokens"] += c.get("input_tokens", 0)
        d["output_tokens"] += c.get("output_tokens", 0)
        d["cache_read_input_tokens"] += c.get("cache_read_input_tokens", 0)
        d["cost_usd"] += c.get("cost_usd") or 0
    sessions = [dict(zip(["session_id", "stage", "phase", "started_at", "ended_at", "completed_before",
                          "completed_after", "status", "note"], r))
                for r in run.store.db.execute("SELECT * FROM sessions ORDER BY started_at")]
    summary = {"run_id": run.run_id, "label_config": run.label_config, "record_status": dict(st),
               "calls_by_role": {k: {kk: (round(vv, 6) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                                 for k, v in by_role.items()},
               "total_cost_usd": round(sum(c.get("cost_usd") or 0 for c in calls), 6),
               "spend_limit_usd": run.cfg["spend_limit_usd"], "enrich_sessions": sessions}
    if timing:
        summary["last_command_stage_seconds"] = timing
    write_json(run.dir / "run_summary.json", summary)
    if show:
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
