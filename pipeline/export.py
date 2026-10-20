"""Write the standardized grading/ folder described in GRADING_CONTRACT.md."""
import gzip
import importlib.util
import json
import shutil
from pathlib import Path

from .common import ROOT, read_json, read_jsonl, write_json
from .stages import final_records


def _checker():
    spec = importlib.util.spec_from_file_location("check_submission", ROOT / "data" / "check_submission.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def export(run, out_dir, full_csv):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rep = read_json(run.dir / "ingestion_report.json")
    write_json(out / "run.json", {
        "version": "a5-audit-v1", "analysis_count": rep["rows"], "analysis_sha256": rep["input_sha256"],
        "classification_input_fields": ["review_text"], "allow_multi_issue": False,
        "run_id": run.run_id, "label_config": run.label_config, "declared_scope": rep["scope"],
        "in_scope_reviews": rep["in_scope_reviews"],
        "scope_note": ("The instructor allowed classifying at least 100,000 reviews. Every source ID is still "
                       "accounted for: out-of-scope nonempty reviews are quarantined with reason "
                       "out_of_scope_not_in_100k_sample and are NOT counted as classifications.")})
    # ingestion.json must be the checker's own deterministic profile
    chk = _checker()
    chk.write_json(out / "ingestion.json", chk.profile(full_csv))

    for p in (out / "records.jsonl", out / "records.jsonl.gz"):
        if p.exists():
            p.unlink()
    n = 0
    with gzip.open(out / "records.jsonl.gz", "wt", encoding="utf-8") as f:
        for rec, _ in final_records(run):
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    shutil.copy(run.dir / "group" / "membership.csv", out / "membership.csv")
    shutil.copy(run.dir / "rank" / "ranking.csv", out / "ranking.csv")
    shutil.copy(run.dir / "memo" / "claims.csv", out / "claims.csv")

    source_ids = {r[0] for r in run.store.db.execute("SELECT review_id FROM source")}
    calls = [c for c in read_jsonl(run.calls_path)
             if c["role"] in ("enrich", "verify", "group", "memo") and set(c["review_ids"]) <= source_ids
             and c.get("phase") != "test"]
    with open(out / "calls.jsonl", "w", encoding="utf-8") as f:
        for c in calls:
            f.write(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n")

    before = run.dir / "checkpoints" / "first_resume_before.json"
    if before.exists():
        b = read_json(before)
        write_json(out / "checkpoint_before.json", {"completed_ids": b["completed_ids"], "taken_at": b["taken_at"],
                                                    "session_id": b["session_id"], "run_id": run.run_id})
    after = run.store.completed_ids(run.label_config)
    write_json(out / "checkpoint_after.json", {"completed_ids": after, "run_id": run.run_id})
    print(f"Exported {n} records and {len(calls)} calls to {out}")
