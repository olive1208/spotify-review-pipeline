"""Compare the pipeline's saved predictions with the 50 human labels (no model calls).

    python evals/evaluate_golden.py --run runs/full

The human labels are only read here, after classification. They are never sent to a model.
"""
import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from pipeline.common import TOPICS, INTENTS, write_json  # noqa: E402
from pipeline.stages import Run, final_records  # noqa: E402

SENTIMENT_TOLERANCE = 0.5  # declared before evaluation


def table(rows, labels, title):
    m = Counter(rows)
    used = [l for l in labels if any(a == l or b == l for a, b in rows)]
    out = [f"**{title}** (rows = human, columns = model)", "", "| human \\ model | " + " | ".join(used) + " |",
           "|---" * (len(used) + 1) + "|"]
    for a in used:
        out.append(f"| {a} | " + " | ".join(str(m[(a, b)] or "") for b in used) + " |")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--gold", default=str(HERE / "golden_50_labeled.csv"))
    a = ap.parse_args()
    run = Run(a.run)
    gold = list(csv.DictReader(open(a.gold, encoding="utf-8-sig", newline="")))
    ids = {g["review_id"] for g in gold}
    pred = {r["review_id"]: r for r, _ in final_records(run) if r["review_id"] in ids}
    cases, tot = [], Counter()
    topic_pairs, intent_pairs = [], []
    nr = Counter()
    entity_additions = []
    for g in gold:
        rid = g["review_id"]
        p = pred.get(rid, {})
        ok = p.get("status") == "completed"
        hs, hsev, hsent = g["topic"], int(g["severity"]), float(g["sentiment"])
        row = {"review_id": rid, "text": g["review_text"], "human_topic": hs, "model_topic": p.get("topic"),
               "human_intent": g["intent"], "model_intent": p.get("intent"), "human_severity": hsev,
               "model_severity": p.get("severity"), "human_sentiment": hsent, "model_sentiment": p.get("sentiment"),
               "human_needs_review": g["needs_review"] == "true", "model_needs_review": p.get("needs_review"),
               "model_entities": p.get("entities"), "human_entities": g["entities"],
               "model_quote": p.get("evidence_quote"), "status": p.get("status", "missing"),
               "cache_source_id": p.get("cache_source_id", "")}
        row["topic_ok"] = ok and p["topic"] == hs
        row["intent_ok"] = ok and p["intent"] == g["intent"]
        row["severity_ok"] = ok and p["severity"] == hsev
        row["sentiment_ok"] = ok and abs(p["sentiment"] - hsent) <= SENTIMENT_TOLERANCE
        row["quote_is_substring"] = ok and p["evidence_quote"] in g["review_text"]
        tot["n"] += 1
        for k in ("topic_ok", "intent_ok", "severity_ok", "sentiment_ok", "quote_is_substring"):
            tot[k] += row[k]
        if ok:
            tot["valid"] += 1
            tot["sev_abs"] += abs(p["severity"] - hsev)
            tot["sent_abs"] += abs(p["sentiment"] - hsent)
            tot["all3"] += row["topic_ok"] and row["intent_ok"] and row["severity_ok"]
            nr[(row["human_needs_review"], bool(p["needs_review"]))] += 1
            low = g["review_text"].lower()
            for e in p["entities"]:
                if e.lower() not in low:
                    entity_additions.append({"review_id": rid, "entity": e, "text": g["review_text"][:120]})
        topic_pairs.append((hs, p.get("topic", "<missing>")))
        intent_pairs.append((g["intent"], p.get("intent", "<missing>")))
        cases.append(row)
    n, v = tot["n"], tot["valid"] or 1
    summary = {
        "cases": n, "valid_predictions": tot["valid"], "missing_or_quarantined_count_as_wrong": n - tot["valid"],
        "topic_agreement": round(tot["topic_ok"] / n, 4), "intent_agreement": round(tot["intent_ok"] / n, 4),
        "severity_exact_agreement": round(tot["severity_ok"] / n, 4),
        "severity_mae": round(tot["sev_abs"] / v, 4), "sentiment_mae": round(tot["sent_abs"] / v, 4),
        "sentiment_within_tolerance": round(tot["sentiment_ok"] / n, 4), "sentiment_tolerance": SENTIMENT_TOLERANCE,
        "topic_intent_severity_all_agree": round(tot["all3"] / n, 4),
        "quote_exact_substring_rate": round(tot["quote_is_substring"] / n, 4),
        "human_ambiguous_cases(needs_review=true)": sum(1 for g in gold if g["needs_review"] == "true"),
        "needs_review_confusion(human,model)": {f"{k[0]},{k[1]}": c for k, c in nr.items()},
        "entity_strings_not_literally_in_text": len(entity_additions),
        "human_topic_counts": dict(Counter(g["topic"] for g in gold)),
        "model_topic_counts": dict(Counter(p.get("topic") for p in pred.values())),
        "run": str(a.run), "label_config": run.label_config,
        "note": "50 cases are a small diagnostic sample, not a precise accuracy estimate."}
    write_json(HERE / "golden_results.json", {"summary": summary, "cases": cases,
                                               "entity_additions_for_inspection": entity_additions})
    with open(HERE / "golden_cases.csv", "w", encoding="utf-8", newline="") as f:
        cols = [k for k in cases[0] if k != "text"] + ["text"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for c in cases:
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v) for k, v in c.items()})
    L = ["# Golden-50 evaluation", "", "```json", json.dumps(summary, indent=2, ensure_ascii=False), "```", ""]
    L += table(topic_pairs, list(TOPICS) + ["<missing>"], "Topic confusion") + [""]
    L += table(intent_pairs, list(INTENTS) + ["<missing>"], "Intent confusion") + [""]
    L += ["## Disagreements (topic, intent or severity)", "",
          "| review_id | text | human t/i/s | model t/i/s | cache |", "|---|---|---|---|---|"]
    for c in cases:
        if not (c["topic_ok"] and c["intent_ok"] and c["severity_ok"]):
            L.append(f"| {c['review_id'][:8]} | {c['text'][:90].replace('|', '/').replace(chr(10), ' ')} | "
                     f"{c['human_topic']}/{c['human_intent']}/{c['human_severity']} | "
                     f"{c['model_topic']}/{c['model_intent']}/{c['model_severity']} | {c['cache_source_id'][:8]} |")
    L += ["", "## Entities not literally present in the text (inspect: translation/normalization vs. invented)", ""]
    for e in entity_additions:
        L.append(f"- {e['review_id'][:8]}: `{e['entity']}` in \"{e['text']}\"")
    (HERE / "golden_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
