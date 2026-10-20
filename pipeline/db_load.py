"""Load saved pipeline outputs into Postgres (Neon) for the deployed dashboard. No model calls.

    python -m pipeline.db_load --run runs/full

Needs DATABASE_URL in .env (a postgresql://... connection string) and `pip install "psycopg[binary]"`.
Tables are dropped and recreated, so reloading is safe.
"""
import argparse
import csv
import json
import os

from .common import ROOT, load_env, read_json
from .stages import Run, final_records

DDL = """
DROP TABLE IF EXISTS reviews, issues, ranking, membership, topic_aggregates, monthly, summary, memo, claims, run_info;
CREATE TABLE run_info(run_id TEXT, label_config TEXT, scope TEXT, loaded_at TIMESTAMPTZ DEFAULT now(), details JSONB);
CREATE TABLE summary(key TEXT PRIMARY KEY, value BIGINT);
CREATE TABLE reviews(review_id TEXT PRIMARY KEY, status TEXT, reason TEXT, topic TEXT, intent TEXT, sentiment REAL,
  severity INT, entities JSONB, evidence_quote TEXT, needs_review BOOLEAN, cache_source_id TEXT,
  review_text TEXT, review_rating TEXT, review_timestamp TEXT, issue_id TEXT);
CREATE INDEX reviews_issue ON reviews(issue_id, severity DESC);
CREATE TABLE issues(issue_id TEXT PRIMARY KEY, topic TEXT, issue_name TEXT, summary TEXT, member_count INT,
  example_review_ids JSONB, top_entities JSONB);
CREATE TABLE ranking(rank INT, issue_id TEXT, complaint_count INT, severity_sum INT, mean_severity NUMERIC(10,6),
  priority_score INT);
CREATE TABLE membership(issue_id TEXT, review_id TEXT, PRIMARY KEY(issue_id, review_id));
CREATE TABLE topic_aggregates(topic TEXT PRIMARY KEY, completed INT, complaint INT, cancellation INT, request INT,
  praise INT, unclear INT, cc_sev1 INT, cc_sev2 INT, cc_sev3 INT, cc_sev4 INT, cc_sev5 INT);
CREATE TABLE monthly(month TEXT PRIMARY KEY, completed INT, complaints_and_cancellations INT);
CREATE TABLE memo(id INT PRIMARY KEY, memo_markdown TEXT, check_result JSONB);
CREATE TABLE claims(claim_id TEXT PRIMARY KEY, issue_id TEXT, metric TEXT, value TEXT);
"""


def rows_of(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    a = ap.parse_args()
    load_env()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Add DATABASE_URL=postgresql://... to .env first.")
    import psycopg
    run = Run(a.run)
    d = run.dir
    membership = rows_of(d / "group" / "membership.csv")
    issue_of = {m["review_id"]: m["issue_id"] for m in membership}
    texts = {r[0]: r[1:] for r in run.store.db.execute(
        "SELECT review_id, review_text, review_rating, review_timestamp FROM source WHERE in_scope=1")}
    with psycopg.connect(url) as con, con.cursor() as cur:
        cur.execute(DDL)
        rep = read_json(d / "ingestion_report.json")
        cur.execute("INSERT INTO run_info(run_id,label_config,scope,details) VALUES(%s,%s,%s,%s)",
                    (run.run_id, run.label_config, rep["scope"], json.dumps(
                        {k: rep[k] for k in ("rows", "empty_review_text", "missing_app_version", "nonempty_reviews",
                                             "distinct_nonempty_texts", "in_scope_reviews", "in_scope_distinct_texts")})))
        summ = read_json(d / "rank" / "summary.json")
        cur.executemany("INSERT INTO summary VALUES(%s,%s)", list(summ.items()))
        n = 0
        with cur.copy("COPY reviews FROM STDIN") as cp:
            for r, _ in final_records(run):
                if r["review_id"] not in texts:
                    continue  # out-of-scope and empty rows are counted in summary, not stored row by row
                t, rating, ts = texts[r["review_id"]]
                cp.write_row((r["review_id"], r["status"], r.get("reason"), r.get("topic"), r.get("intent"),
                              r.get("sentiment"), r.get("severity"), json.dumps(r.get("entities", []), ensure_ascii=False),
                              r.get("evidence_quote"), r.get("needs_review"), r.get("cache_source_id"), t, rating, ts,
                              issue_of.get(r["review_id"])))
                n += 1
        for i in read_json(d / "group" / "issues.json"):
            cur.execute("INSERT INTO issues VALUES(%s,%s,%s,%s,%s,%s,%s)",
                        (i["issue_id"], i["topic"], i["issue_name"], i["summary"], i["member_count"],
                         json.dumps(i["example_review_ids"]), json.dumps(i["top_entities"])))
        cur.executemany("INSERT INTO ranking VALUES(%s,%s,%s,%s,%s,%s)",
                        [(r["rank"], r["issue_id"], r["complaint_count"], r["severity_sum"], r["mean_severity"],
                          r["priority_score"]) for r in rows_of(d / "rank" / "ranking.csv")])
        with cur.copy("COPY membership FROM STDIN") as cp:
            for m in membership:
                cp.write_row((m["issue_id"], m["review_id"]))
        cur.executemany("INSERT INTO topic_aggregates VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [tuple(r.values()) for r in rows_of(d / "rank" / "aggregates.csv")])
        cur.executemany("INSERT INTO monthly VALUES(%s,%s,%s)", [tuple(r.values()) for r in rows_of(d / "rank" / "monthly.csv")])
        cur.execute("INSERT INTO memo VALUES(1,%s,%s)", ((d / "memo" / "memo.md").read_text(encoding="utf-8"),
                                                         json.dumps(read_json(d / "memo" / "memo_check.json"))))
        cur.executemany("INSERT INTO claims VALUES(%s,%s,%s,%s)",
                        [tuple(r.values()) for r in rows_of(d / "rank" / "claims_all.csv")])
        con.commit()
    print(f"Loaded {n} in-scope reviews, {len(membership)} memberships and all aggregates into the database.")


if __name__ == "__main__":
    main()
