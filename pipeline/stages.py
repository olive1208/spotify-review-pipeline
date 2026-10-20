"""The six pipeline stages. Code decides what runs next; models only read language.

prepare  -> code only: read every row, hash, profile, find duplicate texts, choose scope, queue work
enrich   -> model role 1 (classifier) in requests of <= 50 distinct texts, validated by code
verify   -> model role 2 (independent re-labeler) on a seeded random sample; code compares
group    -> code assigns membership by topic; model role 3 only names issues from a bounded sample
rank     -> code only: priority = complaint_count x mean_severity = severity_sum
memo     -> model role 4 writes from saved aggregates + bounded evidence; code checks claims
"""
import hashlib
import json
import math
import random
import re
import sys
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait
from datetime import datetime, timezone
from pathlib import Path

from .common import (FIELDS, INTENTS, ROOT, SENTIMENTS, SHORT_TEXT_CHARS, TOPICS, file_sha, mean_string,
                     read_json, read_jsonl, read_rows, row_sha, seeded_shuffle, text_key, write_csv, write_json,
                     write_jsonl)
from .llm import LLM, BudgetExceeded, CallFailed, load_rates
from .store import Store


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def prompt(name):
    text = (ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    return text, hashlib.sha256(text.encode()).hexdigest()[:8]


class Run:
    def __init__(self, run_dir, config_path=None):
        self.dir = Path(run_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cfg = read_json(config_path or ROOT / "config.json")
        self.store = Store(self.dir / "state.db")
        self.calls_path = self.dir / "calls.jsonl"
        self.rates = load_rates(ROOT / "cost" / "rates.csv")
        self.run_id = self.store.meta_get("run_id") or self._new_run_id()
        _, h = prompt(self.cfg["prompt_versions"]["enrich"])
        self.label_config = (f"{self.cfg['models']['enrich']}+{self.cfg['prompt_versions']['enrich']}-{h}"
                             f"+{self.cfg['schema_version']}+b{self.cfg['enrich_batch_size']}")

    def _new_run_id(self):
        rid = "run-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        self.store.meta_set("run_id", rid)
        return rid

    def log(self, event, **kw):
        rec = {"time": now(), "run_id": self.run_id, "event": event, **kw}
        with open(self.dir / "run_log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        print(f"[{rec['time']}] {event} " + " ".join(f"{k}={v}" for k, v in kw.items() if not isinstance(v, (list, dict))))

    def spent_so_far(self):
        if not self.calls_path.exists():
            return 0.0
        return sum(c.get("cost_usd") or 0 for c in read_jsonl(self.calls_path))

    def llm(self):
        return LLM(self.calls_path, self.rates, self.cfg["spend_limit_usd"], spent_before=self.spent_so_far(),
                   max_attempts=self.cfg["api_attempts_per_call"])


# --------------------------------------------------------------------------------------------
# 1. PREPARE (code only)
# --------------------------------------------------------------------------------------------
def prepare(run, input_csv, sample=None, include=(), seed=None):
    t0 = time.time()
    st = run.store
    if st.db.execute("SELECT COUNT(*) FROM source").fetchone()[0]:
        run.log("prepare_skipped", reason="source already loaded in this run directory")
        return read_json(run.dir / "ingestion_report.json")
    rows, ids = 0, set()
    profile = Counter()
    months, ratings = Counter(), Counter()
    text_counts = Counter()
    batch = []
    for i, row in enumerate(read_rows(input_csv)):
        rows += 1
        if row["review_id"] in ids:
            profile["duplicate_review_ids"] += 1
        ids.add(row["review_id"])
        empty = not row["review_text"].strip()
        profile["empty_review_text"] += empty
        profile["missing_app_version"] += not row["app_version"].strip()
        months[row["review_timestamp"][:7]] += 1
        ratings[row["review_rating"]] += 1
        k = text_key(row["review_text"])
        if not empty:
            text_counts[k] += 1
        batch.append((i, row["review_id"], row_sha(row), row["review_text"], k, row["review_rating"],
                      row["review_likes"], row["app_version"], row["review_timestamp"], 0, None))
        if len(batch) >= 20000:
            st.db.executemany("INSERT INTO source VALUES(?,?,?,?,?,?,?,?,?,?,?)", batch)
            batch = []
    st.db.executemany("INSERT INTO source VALUES(?,?,?,?,?,?,?,?,?,?,?)", batch)
    st.db.commit()

    # ---- scope: everything nonempty, or a seeded sample that always includes the required files ----
    nonempty = [r[0] for r in st.db.execute("SELECT review_id FROM source WHERE trim(review_text)<>'' ORDER BY ord")]
    if sample and sample < len(nonempty):
        must = []
        for path in include:
            must += [r["review_id"] for r in read_rows(path)]
        must_set = set(must) & set(nonempty)
        rest = seeded_shuffle(set(nonempty) - must_set, seed)
        chosen = must_set | set(rest[: sample - len(must_set)])
        scope_desc = (f"seeded random sample of {sample} nonempty reviews (seed '{seed}'), always including "
                      f"{len(must_set)} IDs from {[Path(p).name for p in include]}")
    else:
        chosen = set(nonempty)
        scope_desc = "all nonempty reviews"
    st.db.execute("CREATE TEMP TABLE chosen(review_id TEXT PRIMARY KEY)")
    st.db.executemany("INSERT INTO chosen VALUES(?)", ((x,) for x in chosen))
    st.db.execute("UPDATE source SET in_scope=1 WHERE review_id IN (SELECT review_id FROM chosen)")
    st.db.execute("UPDATE source SET scope_reason='empty_review_text' WHERE trim(review_text)=''")
    st.db.execute("UPDATE source SET scope_reason='out_of_scope_not_in_100k_sample' "
                  "WHERE in_scope=0 AND trim(review_text)<>''")
    st.db.commit()
    distinct_in_scope = st.db.execute("SELECT COUNT(DISTINCT text_key) FROM source WHERE in_scope=1").fetchone()[0]
    report = {
        "input_file": str(Path(input_csv).name), "input_sha256": file_sha(input_csv), "rows": rows,
        "unique_review_ids": len(ids), "duplicate_review_ids": profile["duplicate_review_ids"],
        "empty_review_text": profile["empty_review_text"], "missing_app_version": profile["missing_app_version"],
        "nonempty_reviews": len(nonempty), "distinct_nonempty_texts": len(text_counts),
        "nonempty_rows_with_repeated_text": sum(c for c in text_counts.values() if c > 1),
        "reviews_by_month": dict(sorted(months.items())), "reviews_by_rating": dict(sorted(ratings.items())),
        "scope": scope_desc, "in_scope_reviews": len(chosen), "in_scope_distinct_texts": distinct_in_scope,
        "out_of_scope_nonempty_reviews": len(nonempty) - len(chosen),
        "seconds": round(time.time() - t0, 1),
        "notes": ["Rows are read with a CSV parser that handles multiline quoted text; values are preserved exactly.",
                  "Empty texts are quarantined with reason empty_review_text; missing app_version alone does not block classification."],
    }
    write_json(run.dir / "ingestion_report.json", report)
    write_json(run.dir / "data_manifest.json", {"run_id": run.run_id, "input_csv": str(input_csv),
                                               "input_sha256": report["input_sha256"], "config": run.cfg,
                                               "label_config": run.label_config, "created_at": now()})
    run.log("prepare_done", rows=rows, in_scope=len(chosen), distinct=distinct_in_scope, seconds=report["seconds"])
    return report


# --------------------------------------------------------------------------------------------
# 2. ENRICH (model role: classifier)
# --------------------------------------------------------------------------------------------
ENRICH_TOOL = {
    "name": "record_labels",
    "description": "Record one label set per review.",
    "input_schema": {
        "type": "object",
        "properties": {"results": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "i": {"type": "integer"},
                "topic": {"type": "string", "enum": list(TOPICS)},
                "intent": {"type": "string", "enum": list(INTENTS)},
                "sentiment": {"type": "number", "enum": list(SENTIMENTS)},
                "severity": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                "entities": {"type": "array", "items": {"type": "string"}},
                "quote": {"type": "string"},
                "needs_review": {"type": "boolean"}},
            "required": ["i", "topic", "intent", "sentiment", "severity", "entities", "quote", "needs_review"]}}},
        "required": ["results"]},
}


def validate_item(item, text, short):
    """Return (clean_label, problem). Code, not the model, decides what is valid."""
    try:
        topic, intent = item["topic"], item["intent"]
        if topic not in TOPICS or intent not in INTENTS:
            return None, "label_not_allowed"
        sev = item["severity"]
        if isinstance(sev, float) and sev.is_integer():
            sev = int(sev)
        if type(sev) is not int or not 1 <= sev <= 5:
            return None, "severity_invalid"
        sent = float(item["sentiment"])
        if not math.isfinite(sent) or not -1 <= sent <= 1:
            return None, "sentiment_invalid"
        nr = item["needs_review"]
        if not isinstance(nr, bool):
            return None, "needs_review_invalid"
        ents = []
        for e in item.get("entities") or []:
            if isinstance(e, str) and e.strip() and e.strip().lower() not in ents:
                ents.append(e.strip().lower())
        quote = item.get("quote") or ""
        if short:
            quote = text
        elif not quote.strip() or quote not in text:
            q2 = quote.strip()
            if q2 and q2 in text:
                quote = q2
            else:
                return {"topic": topic, "intent": intent, "sentiment": sent, "severity": sev, "entities": ents,
                        "evidence_quote": quote, "needs_review": nr}, "quote_not_in_text"
        return {"topic": topic, "intent": intent, "sentiment": sent, "severity": sev, "entities": ents,
                "evidence_quote": quote, "needs_review": nr}, None
    except (KeyError, TypeError, ValueError):
        return None, "missing_or_malformed_fields"


def enrich_request(run, llm, items, phase, session_id, attempt_no, system):
    """items: list of (text_key, origin_id, text). Returns (labeled, retry_items_with_reason)."""
    payload = [{"i": n, "text": t, "short": len(t) <= SHORT_TEXT_CHARS} for n, (_, _, t) in enumerate(items)]
    user = "Label these reviews:\n" + json.dumps(payload, ensure_ascii=False)
    try:
        out, crec = llm.call(role="enrich", model=run.cfg["models"]["enrich"], system=system, user=user, tool=ENRICH_TOOL,
                       max_tokens=run.cfg["enrich_max_output_tokens"], review_ids=[o for _, o, _ in items],
                       phase=phase, label_config=run.label_config, run_id=run.run_id,
                       extra={"session_id": session_id, "batch_attempt": attempt_no})
    except CallFailed as e:
        return [], [(it, f"call_failed: {str(e)[:120]}") for it in items], None
    rid = crec["request_id"]
    results = out.get("results") if isinstance(out, dict) else None
    if not isinstance(results, list):
        return [], [(it, "no_results_list") for it in items], rid
    by_i = {}
    for r in results:
        if isinstance(r, dict) and isinstance(r.get("i"), int) and 0 <= r["i"] < len(items) and r["i"] not in by_i:
            by_i[r["i"]] = r
    labeled, retry = [], []
    for n, it in enumerate(items):
        key, origin, text = it
        if n not in by_i:
            retry.append((it, "missing_from_output"))
            continue
        clean, problem = validate_item(by_i[n], text, len(text) <= SHORT_TEXT_CHARS)
        if problem:
            retry.append((it, problem, clean))
            continue
        labeled.append({"text_key": key, "origin_review_id": origin, **clean, "phase": phase,
                        "session_id": session_id, "attempts": attempt_no, "request_id": rid})
    return labeled, retry, rid


def enrich(run, workers=None, max_batches=None, retry_failed=False):
    st, cfg = run.store, run.cfg
    workers = min(workers or cfg["workers"], cfg["max_workers_allowed"])
    system, _ = prompt(cfg["prompt_versions"]["enrich"])
    labeled_keys = st.labeled_keys(run.label_config)
    failed_keys = set() if retry_failed else st.failed_keys(run.label_config)
    # one origin (first in source order) per distinct in-scope text
    pending, seen = [], set()
    for key, rid, text in st.db.execute("SELECT text_key, review_id, review_text FROM source "
                                        "WHERE in_scope=1 ORDER BY ord"):
        if key in seen:
            continue
        seen.add(key)
        if key not in labeled_keys and key not in failed_keys:
            pending.append((key, rid, text))
    llm = run.llm() if pending else None  # load the API client first (slow first import on some machines)
    already = len(labeled_keys)
    phase = "resume" if already else "initial"
    session_id = "s-" + uuid.uuid4().hex[:8]
    completed_before = st.completed_ids(run.label_config)
    if phase == "resume" and completed_before:
        snap_dir = run.dir / "checkpoints"
        snap = {"run_id": run.run_id, "session_id": session_id, "taken_at": now(), "label_config": run.label_config,
                "completed_ids": completed_before}
        write_json(snap_dir / f"before_{session_id}.json", snap)
        if not (snap_dir / "first_resume_before.json").exists():
            write_json(snap_dir / "first_resume_before.json", snap)
    st.db.execute("UPDATE sessions SET status='abandoned_before_any_call' WHERE status='running'")
    st.db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?)",
                  (session_id, "enrich", phase, now(), None, len(completed_before), None, "running", None))
    st.db.commit()
    run.log("enrich_start", phase=phase, session=session_id, pending_texts=len(pending),
            already_labeled_texts=already, workers=workers, label_config=run.label_config)
    if not pending:
        _close_session(run, session_id, "nothing_to_do")
        return {"phase": phase, "new_labels": 0}

    bs = cfg["enrich_batch_size"]
    batches = [pending[i:i + bs] for i in range(0, len(pending), bs)]
    if max_batches:
        batches = batches[:max_batches]
    done_batches, new_labels, quarantined = 0, 0, 0
    status = "completed"
    t0 = time.time()

    def work(batch):
        labeled, retry, _ = enrich_request(run, llm, batch, phase, session_id, 1, system)
        failed = []
        if retry:  # bounded: one retry for invalid / missing items
            run.log("enrich_retry", items=len(retry), reasons=dict(Counter(r[1].split(":")[0] for r in retry)),
                    ids=[r[0][1] for r in retry])
            again = [r[0] for r in retry]
            reasons = {r[0][0]: r for r in retry}
            l2, r2, _ = enrich_request(run, llm, again, phase, session_id, 2, system)
            labeled += l2
            for r in r2:
                it, reason = r[0], r[1]
                clean = r[2] if len(r) > 2 else None
                if reason == "quote_not_in_text" and clean and len(it[2]) <= 600:
                    clean = dict(clean, evidence_quote=it[2], needs_review=True)
                    labeled.append({"text_key": it[0], "origin_review_id": it[1], **clean, "phase": phase,
                                    "session_id": session_id, "attempts": 2,
                                    "repair": "model quote not an exact substring twice; whole review used as quote"})
                else:
                    failed.append((it[0], it[1], reason, 2))
        return labeled, failed

    stop = False
    with ThreadPoolExecutor(max_workers=workers) as ex:
        queue = list(batches)
        running = set()
        try:
            while queue or running:
                while queue and len(running) < workers and not stop:
                    running.add(ex.submit(work, queue.pop(0)))
                if not running:
                    break
                # short timeout so Ctrl+C is noticed promptly (Windows blocks signals during untimed waits)
                finished, running = wait(running, timeout=0.5, return_when=FIRST_COMPLETED)
                for fut in finished:
                    try:
                        labeled, failed = fut.result()
                    except BudgetExceeded as e:
                        run.log("budget_stop", detail=str(e))
                        stop, status = True, "stopped_budget"
                        continue
                    for r in labeled:
                        r.setdefault("request_id", None)
                    st.save_batch(run.label_config, labeled, failed)  # atomic per batch
                    done_batches += 1
                    new_labels += len(labeled)
                    quarantined += len(failed)
                    if done_batches % 10 == 0 or not (queue or running):
                        el = time.time() - t0
                        run.log("enrich_progress", batches=f"{done_batches}/{len(batches)}", new_labels=new_labels,
                                failed=quarantined, spent_usd=round(llm.spent, 4), elapsed_s=round(el, 1))
                if stop:
                    queue = []
        except KeyboardInterrupt:
            status = "interrupted"
            run.log("interrupt_received", detail="Ctrl+C: no new batches; waiting for in-flight batches to save")
            queue = []
            for fut in running:
                try:
                    labeled, failed = fut.result()
                    st.save_batch(run.label_config, labeled, failed)
                    new_labels += len(labeled)
                except Exception:
                    pass
    if max_batches and status == "completed" and len(pending) > max_batches * bs:
        status = "stopped_max_batches"
    _close_session(run, session_id, status)
    after = st.completed_ids(run.label_config)
    run.log("enrich_end", status=status, new_labels=new_labels, failed=quarantined, completed_ids=len(after),
            spent_usd=round(llm.spent, 4), seconds=round(time.time() - t0, 1))
    if status == "interrupted":
        sys.exit(130)
    return {"phase": phase, "status": status, "new_labels": new_labels}


def _close_session(run, sid, status):
    after = len(run.store.completed_ids(run.label_config))
    run.store.db.execute("UPDATE sessions SET ended_at=?, completed_after=?, status=? WHERE session_id=?",
                         (now(), after, status, sid))
    run.store.db.commit()


# --------------------------------------------------------------------------------------------
# Final records (one per source ID) -- used by every later stage and by the export
# --------------------------------------------------------------------------------------------
def final_records(run):
    st = run.store
    labels = {r[0]: r for r in st.db.execute(
        "SELECT text_key, origin_review_id, topic, intent, sentiment, severity, entities, evidence_quote, "
        "needs_review, repair FROM labels WHERE label_config=?", (run.label_config,))}
    failures = {r[0]: r for r in st.db.execute(
        "SELECT text_key, reason, attempts FROM failures WHERE label_config=?", (run.label_config,))}
    for (rid, sha, text, key, in_scope, reason, rating, ts) in st.db.execute(
            "SELECT review_id, row_sha, review_text, text_key, in_scope, scope_reason, review_rating, "
            "review_timestamp FROM source ORDER BY ord"):
        base = {"review_id": rid, "source_sha256": sha}
        if not text.strip():
            yield {**base, "status": "quarantined", "reason": "empty_review_text"}, None
        elif not in_scope:
            yield {**base, "status": "quarantined", "reason": reason or "out_of_scope"}, None
        elif key in labels:
            _, origin, topic, intent, sent, sev, ents, quote, nr, repair = labels[key]
            rec = {**base, "status": "completed", "topic": topic, "intent": intent, "sentiment": float(sent),
                   "severity": int(sev), "entities": json.loads(ents), "evidence_quote": quote,
                   "needs_review": bool(nr), "label_config": run.label_config}
            if origin != rid:
                rec["cache_source_id"] = origin
            yield rec, {"rating": rating, "timestamp": ts, "repair": repair}
        elif key in failures:
            yield {**base, "status": "quarantined", "reason": "model_stage_failed: " + str(failures[key][1])}, None
        else:
            yield {**base, "status": "quarantined", "reason": "not_classified_run_stopped_before_completion"}, None


# --------------------------------------------------------------------------------------------
# 3. VERIFY (model role: independent auditor)
# --------------------------------------------------------------------------------------------
VERIFY_TOOL = {
    "name": "record_audit", "description": "Record independent labels.",
    "input_schema": {"type": "object", "properties": {"results": {"type": "array", "items": {
        "type": "object", "properties": {
            "i": {"type": "integer"}, "topic": {"type": "string", "enum": list(TOPICS)},
            "intent": {"type": "string", "enum": list(INTENTS)},
            "severity": {"type": "integer", "enum": [1, 2, 3, 4, 5]}, "confident": {"type": "boolean"}},
        "required": ["i", "topic", "intent", "severity", "confident"]}}}, "required": ["results"]}}


def run_verifier(run, items, phase="verify", out_name="verify", llm=None):
    """items: list of (review_id, text). The verifier never sees the first prediction."""
    cfg = run.cfg
    system, h = prompt(cfg["prompt_versions"]["verify"])
    vconfig = f"{cfg['models']['verify']}+{cfg['prompt_versions']['verify']}-{h}"
    cache_path = run.dir / out_name / "verifier_predictions.jsonl"
    cached = {}
    if cache_path.exists():
        cached = {r["review_id"]: r for r in read_jsonl(cache_path) if r.get("verify_config") == vconfig}
    todo = [it for it in items if it[0] not in cached]
    llm = llm or run.llm()
    bs = cfg["verify_batch_size"]
    for b in range(0, len(todo), bs):
        batch = todo[b:b + bs]
        payload = [{"i": n, "text": t} for n, (_, t) in enumerate(batch)]
        try:
            out, _ = llm.call(role="verify", model=cfg["models"]["verify"], system=system,
                           user="Label these reviews:\n" + json.dumps(payload, ensure_ascii=False), tool=VERIFY_TOOL,
                           max_tokens=cfg["verify_max_output_tokens"], review_ids=[r for r, _ in batch],
                           phase=phase, label_config=vconfig, run_id=run.run_id)
        except CallFailed as e:
            run.log("verify_batch_failed", error=str(e)[:200])
            continue
        for r in out.get("results", []):
            n = r.get("i")
            if isinstance(n, int) and 0 <= n < len(batch) and r.get("topic") in TOPICS and r.get("intent") in INTENTS \
                    and r.get("severity") in (1, 2, 3, 4, 5):
                cached[batch[n][0]] = {"review_id": batch[n][0], "topic": r["topic"], "intent": r["intent"],
                                       "severity": r["severity"], "confident": bool(r.get("confident")),
                                       "verify_config": vconfig}
        for rid, _ in batch:  # remember items the verifier skipped so a warm rerun does not pay again
            cached.setdefault(rid, {"review_id": rid, "no_result": True, "verify_config": vconfig})
        write_jsonl(cache_path, list(cached.values()))
    return cached, vconfig


def compare(first, second):
    rows, agree = [], Counter()
    for rid, a in first.items():
        b = second.get(rid)
        if not b or b.get("no_result"):
            continue
        t, i, s = a["topic"] == b["topic"], a["intent"] == b["intent"], a["severity"] == b["severity"]
        agree["n"] += 1
        agree["topic"] += t
        agree["intent"] += i
        agree["severity"] += s
        agree["sev_abs"] += abs(a["severity"] - b["severity"])
        agree["all3"] += t and i and s
        if not (t and i and s):
            rows.append([rid, a["topic"], b["topic"], a["intent"], b["intent"], a["severity"], b["severity"],
                         b.get("confident", ""), a.get("evidence_quote", "")[:160]])
    n = agree["n"] or 1
    summary = {"compared": agree["n"], "topic_agreement": round(agree["topic"] / n, 4),
               "intent_agreement": round(agree["intent"] / n, 4),
               "severity_exact_agreement": round(agree["severity"] / n, 4),
               "severity_mean_abs_diff": round(agree["sev_abs"] / n, 4),
               "all_three_agree": round(agree["all3"] / n, 4), "disagreements": len(rows)}
    return summary, rows


def verify(run, sample_size=None):
    cfg = run.cfg
    recs = {r["review_id"]: r for r, _ in final_records(run) if r["status"] == "completed" and "cache_source_id" not in r}
    texts = dict(run.store.db.execute("SELECT review_id, review_text FROM source WHERE in_scope=1"))
    n = min(sample_size or cfg["verify_sample_size"], len(recs))
    # stable sample: lowest SHA-256(seed:id); adding a few records later barely changes it
    sample = sorted(recs, key=lambda r: hashlib.sha256(f"{cfg['verify_seed']}:{r}".encode()).hexdigest())[:n]
    preds, vconfig = run_verifier(run, [(rid, texts[rid]) for rid in sample])
    summary, rows = compare({rid: recs[rid] for rid in sample}, preds)
    summary.update({"declared_sample": f"{n} completed original (non-cache) records with the lowest SHA-256(seed:review_id)",
                    "seed": cfg["verify_seed"], "verify_config": vconfig, "verified": len(preds)})
    write_json(run.dir / "verify" / "verify_report.json", summary)
    write_csv(run.dir / "verify" / "disagreements.csv",
              ["review_id", "enrich_topic", "verify_topic", "enrich_intent", "verify_intent", "enrich_severity",
               "verify_severity", "verifier_confident", "evidence_quote"], rows)
    run.log("verify_done", **{k: v for k, v in summary.items() if not isinstance(v, str)})
    return summary


# --------------------------------------------------------------------------------------------
# 4. GROUP (code assigns membership; model only names the issues)
# --------------------------------------------------------------------------------------------
GROUP_TOOL = {
    "name": "name_issues", "description": "Name each complaint group.",
    "input_schema": {"type": "object", "properties": {"issues": {"type": "array", "items": {
        "type": "object", "properties": {
            "issue_id": {"type": "string"}, "issue_name": {"type": "string"}, "summary": {"type": "string"},
            "example_review_ids": {"type": "array", "items": {"type": "string"}}},
        "required": ["issue_id", "issue_name", "summary", "example_review_ids"]}}}, "required": ["issues"]}}


def issue_id_for(topic):
    return "ISS-" + topic.upper()


def group(run):
    cfg = run.cfg
    members = defaultdict(list)
    recs = {}
    for r, _ in final_records(run):
        if r["status"] == "completed" and r["intent"] in ("complaint", "cancellation"):
            members[issue_id_for(r["topic"])].append(r["review_id"])
            recs[r["review_id"]] = r
    membership = sorted((iid, rid) for iid, ids in members.items() for rid in ids)
    write_csv(run.dir / "group" / "membership.csv", ["issue_id", "review_id"], membership)

    # bounded evidence pack for naming: originals only, seeded sample per issue
    packs = []
    for iid in sorted(members):
        ids = [x for x in members[iid] if "cache_source_id" not in recs[x]]
        pick = seeded_shuffle(ids, cfg["verify_seed"] + iid)[:cfg["group_examples_per_issue"]]
        ents = Counter(e for x in members[iid] for e in recs[x]["entities"]).most_common(8)
        packs.append({"issue_id": iid, "topic": recs[members[iid][0]]["topic"], "member_count": len(members[iid]),
                      "top_entities": [e for e, _ in ents],
                      "sample": [{"review_id": x, "quote": recs[x]["evidence_quote"][:200]} for x in pick]})
    system, h = prompt(cfg["prompt_versions"]["group"])
    user = "Complaint groups:\n" + json.dumps(packs, ensure_ascii=False)
    key = hashlib.sha256((cfg["models"]["group"] + h + user).encode()).hexdigest()
    cache = run.dir / "group" / "issue_names.json"
    if cache.exists() and read_json(cache).get("input_hash") == key:
        names = read_json(cache)
        run.log("group_cache_hit", detail="same inputs and settings; no group model call")
    else:
        try:
            out, _ = run.llm().call(role="group", model=cfg["models"]["group"], system=system, user=user, tool=GROUP_TOOL,
                                 max_tokens=cfg["group_max_output_tokens"], review_ids=[], phase="group",
                                 label_config=f"{cfg['models']['group']}+{cfg['prompt_versions']['group']}-{h}",
                                 run_id=run.run_id, extra={"input_artifact": "group/evidence_pack.json"})
            issues = out.get("issues", [])
        except CallFailed as e:
            run.log("group_naming_failed", error=str(e)[:200])
            issues = []
        valid_ids = {iid: {s["review_id"] for s in p["sample"]} for iid, p in ((p["issue_id"], p) for p in packs)}
        clean = {}
        for it in issues:
            iid = it.get("issue_id")
            if iid in valid_ids:  # reject invented issues / review IDs
                clean[iid] = {"issue_name": str(it.get("issue_name", ""))[:80], "summary": str(it.get("summary", ""))[:400],
                              "example_review_ids": [x for x in it.get("example_review_ids", []) if x in valid_ids[iid]][:4]}
        names = {"input_hash": key, "issues": clean}
        write_json(cache, names)
    write_json(run.dir / "group" / "evidence_pack.json", packs)
    issues = []
    for p in packs:
        n = names["issues"].get(p["issue_id"], {})
        issues.append({"issue_id": p["issue_id"], "topic": p["topic"], "member_count": p["member_count"],
                       "issue_name": n.get("issue_name") or p["topic"], "summary": n.get("summary", ""),
                       "example_review_ids": n.get("example_review_ids", []), "top_entities": p["top_entities"]})
    write_json(run.dir / "group" / "issues.json", issues)
    run.log("group_done", issues=len(issues), memberships=len(membership))
    return issues


# --------------------------------------------------------------------------------------------
# 5. RANK (code only; reproducible without any model call)
# --------------------------------------------------------------------------------------------
def rank(run):
    sev = {r["review_id"]: r["severity"] for r, _ in final_records(run) if r["status"] == "completed"}
    members = defaultdict(list)
    import csv
    with open(run.dir / "group" / "membership.csv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            members[row["issue_id"]].append(sev[row["review_id"]])
    table = []
    for iid, vals in members.items():
        table.append({"issue_id": iid, "complaint_count": len(vals), "severity_sum": sum(vals),
                      "mean_severity": mean_string(sum(vals), len(vals)), "priority_score": sum(vals)})
    table.sort(key=lambda x: (-x["priority_score"], x["issue_id"]))
    for i, row in enumerate(table, 1):
        row["rank"] = i
    cols = ["rank", "issue_id", "complaint_count", "severity_sum", "mean_severity", "priority_score"]
    write_csv(run.dir / "rank" / "ranking.csv", cols, [[r[c] for c in cols] for r in table])
    # claims: every issue-level number the memo may cite
    claims = []
    for r in table:
        for m, short in (("complaint_count", "count"), ("severity_sum", "sevsum"), ("mean_severity", "mean"),
                         ("priority_score", "score")):
            claims.append([f"C-{r['issue_id']}-{short}", r["issue_id"], m, str(r[m])])
    write_csv(run.dir / "rank" / "claims_all.csv", ["claim_id", "issue_id", "metric", "value"], claims)
    aggregates(run)
    run.log("rank_done", issues=len(table), top=table[0]["issue_id"] if table else None)
    return table


def aggregates(run):
    """Extra code-computed tables for the memo and dashboard (counts by topic x intent, severity, month)."""
    by_topic = defaultdict(Counter)
    overall = Counter()
    month = defaultdict(Counter)
    sevdist = defaultdict(Counter)
    for r, meta in final_records(run):
        overall["source_rows"] += 1
        if r["status"] == "quarantined":
            overall["quarantined:" + r["reason"].split(":")[0]] += 1
            continue
        overall["completed"] += 1
        overall["cache_reuse"] += "cache_source_id" in r
        overall["needs_review"] += r["needs_review"]
        overall["intent:" + r["intent"]] += 1
        by_topic[r["topic"]][r["intent"]] += 1
        by_topic[r["topic"]]["total"] += 1
        if r["intent"] in ("complaint", "cancellation"):
            sevdist[r["topic"]][r["severity"]] += 1
            month[meta["timestamp"][:7]]["complaints"] += 1
        month[meta["timestamp"][:7]]["completed"] += 1
    rows = []
    for t in TOPICS:
        c = by_topic.get(t, Counter())
        rows.append([t, c["total"], c["complaint"], c["cancellation"], c["request"], c["praise"], c["unclear"],
                     *[sevdist[t][s] for s in range(1, 6)]])
    write_csv(run.dir / "rank" / "aggregates.csv",
              ["topic", "completed", "complaint", "cancellation", "request", "praise", "unclear",
               "cc_sev1", "cc_sev2", "cc_sev3", "cc_sev4", "cc_sev5"], rows)
    write_csv(run.dir / "rank" / "monthly.csv", ["month", "completed", "complaints_and_cancellations"],
              [[m, month[m]["completed"], month[m]["complaints"]] for m in sorted(month)])
    write_json(run.dir / "rank" / "summary.json", dict(sorted(overall.items())))


# --------------------------------------------------------------------------------------------
# 6. MEMO (model role: writer) + code check of every number
# --------------------------------------------------------------------------------------------
MEMO_TOOL = {"name": "write_memo", "description": "Return the memo.",
             "input_schema": {"type": "object", "properties": {"memo_markdown": {"type": "string"}},
                              "required": ["memo_markdown"]}}


def memo(run):
    import csv
    cfg = run.cfg
    ranking = list(csv.DictReader(open(run.dir / "rank" / "ranking.csv", encoding="utf-8")))
    claims = list(csv.DictReader(open(run.dir / "rank" / "claims_all.csv", encoding="utf-8")))
    summary = read_json(run.dir / "rank" / "summary.json")
    issues = {i["issue_id"]: i for i in read_json(run.dir / "group" / "issues.json")}
    recs = {r["review_id"]: r for r, _ in final_records(run) if r["status"] == "completed"}
    evidence = []
    for row in ranking[:6]:
        iid = row["issue_id"]
        ids = list(issues[iid]["example_review_ids"])
        pool = [x for x, r in recs.items() if r["intent"] in ("complaint", "cancellation") and issue_id_for(r["topic"]) == iid
                and "cache_source_id" not in r and r["severity"] >= 3]
        for x in seeded_shuffle(pool, cfg["verify_seed"] + "memo" + iid):
            if len(ids) >= cfg["memo_evidence_per_issue"]:
                break
            if x not in ids:
                ids.append(x)
        evidence.append({"issue_id": iid, "issue_name": issues[iid]["issue_name"], "summary": issues[iid]["summary"],
                         "reviews": [{"review_id": x, "severity": recs[x]["severity"],
                                      "quote": recs[x]["evidence_quote"][:220]} for x in ids if x in recs]})
    scope = read_json(run.dir / "ingestion_report.json")
    inputs = {"overall_summary": summary, "scope": {k: scope[k] for k in ("rows", "scope", "in_scope_reviews")},
              "ranking": ranking, "claims": claims, "evidence_pack": evidence}
    write_json(run.dir / "memo" / "memo_inputs.json", inputs)
    system, h = prompt(cfg["prompt_versions"]["memo"])
    user = json.dumps(inputs, ensure_ascii=False)
    key = hashlib.sha256((cfg["models"]["memo"] + h + user).encode()).hexdigest()
    cache = run.dir / "memo" / "memo_cache.json"
    if cache.exists() and read_json(cache).get("input_hash") == key:
        text = read_json(cache)["memo_markdown"]
        run.log("memo_cache_hit", detail="same inputs and settings; no memo model call")
    else:
        out, _ = run.llm().call(role="memo", model=cfg["models"]["memo"], system=system, user=user, tool=None,
                             max_tokens=cfg["memo_max_output_tokens"], review_ids=[], phase="memo",
                             label_config=f"{cfg['models']['memo']}+{cfg['prompt_versions']['memo']}-{h}",
                             run_id=run.run_id, extra={"input_artifact": "memo/memo_inputs.json"})
        text = out["text"]
        write_json(cache, {"input_hash": key, "memo_markdown": text})
    (run.dir / "memo").mkdir(exist_ok=True)
    (run.dir / "memo" / "memo.md").write_text(text, encoding="utf-8")
    check = check_memo(text, claims, summary, ranking, recs, issues, inputs["scope"])
    write_json(run.dir / "memo" / "memo_check.json", check)
    cited = [c for c in claims if c["claim_id"] in set(check["cited_claim_ids"])]
    write_csv(run.dir / "memo" / "claims.csv", ["claim_id", "issue_id", "metric", "value"],
              [[c["claim_id"], c["issue_id"], c["metric"], c["value"]] for c in (cited or claims)])
    run.log("memo_done", cited_claims=len(check["cited_claim_ids"]), problems=len(check["problems"]))
    return check


def check_memo(text, claims, summary, ranking, recs, issues, scope=None):
    by_id = {c["claim_id"]: c for c in claims}
    problems = []
    cited = re.findall(r"\[(C-[A-Z0-9_-]+?-(?:count|sevsum|mean|score))\]", text)
    for cid in cited:
        if cid not in by_id:
            problems.append(f"cited claim {cid} does not exist")
    # each cited claim's value must appear right before it
    for m in re.finditer(r"([0-9][0-9,]*(?:\.[0-9]+)?)[^\[\n0-9]{0,40}\[(C-[^\]]+)\]", text):
        num, cid = m.group(1).replace(",", ""), m.group(2)
        if cid in by_id and num != by_id[cid]["value"]:
            problems.append(f"number {m.group(1)} next to [{cid}] but saved value is {by_id[cid]['value']}")
    allowed = {c["value"] for c in claims} | {str(v) for v in summary.values()} | {str(v) for v in (scope or {}).values()}
    uuid_re = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    plain = re.sub(r"\[C-[^\]]+\]", " ", re.sub(uuid_re, " ", text))  # IDs are checked separately below
    for n in re.findall(r"(?<![\w.-])([0-9][0-9,]*(?:\.[0-9]+)?)(?![\w-])", plain):
        v = n.replace(",", "")
        if len(v) < 2 or re.fullmatch(r"20[0-9]{2}", v) or v in allowed:
            continue  # single digits (list numbering, "2-3 reviews") and years are not data claims
        problems.append(f"number {n} is not a saved claim, summary or scope value")
    for iid in set(re.findall(r"ISS-[A-Z]+", text)):
        if iid not in issues:
            problems.append(f"unknown issue id {iid}")
    for rid in set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)):
        if rid not in recs:
            problems.append(f"unknown review id {rid}")
    return {"cited_claim_ids": sorted(set(cited)), "problems": problems,
            "note": "Code checks IDs and numbers; a person still checks whether the argument is sound."}
