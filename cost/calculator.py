"""100-review cost and runtime calculator.

DEFAULT = OFFLINE REPLAY (no API key, no model calls):
    python cost/calculator.py
    python cost/calculator.py --rates cost/rates_doubled_example.csv   # what-if on prices
    python cost/calculator.py --budget 5 --workers 4                   # what-if on controls

PAID PILOT (explicit, separate command; runs the real pipeline twice on data/cost_100.csv):
    python cost/calculator.py pilot --execute
"""
import argparse
import csv
import json
import math
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from pipeline.common import file_sha, read_json, read_jsonl, write_json  # noqa: E402  (no API client imported)

ITEMS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
ITEM_RATE = {"input_tokens": "input", "cache_read_input_tokens": "cache_read",
             "cache_creation_input_tokens": "cache_write", "output_tokens": "output"}


def load_rates(path):
    rates, meta = {}, {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rates.setdefault(r["model"], {})[r["billing_item"]] = float(r["usd_per_unit"]) / float(r["unit_size"])
            meta[(r["model"], r["billing_item"])] = r
    return rates, meta


# ----------------------------------------------------------------------------------------------
# PILOT (paid, explicit)
# ----------------------------------------------------------------------------------------------
def pilot(args):
    if not args.execute:
        sys.exit("Refusing to make paid calls. Add --execute to run the real 100-review pilot.")
    inp = ROOT / "data" / "cost_100.csv"
    manifest = read_json(ROOT / "data" / "manifest.json")
    expected = manifest["files"]["cost_100.csv"]["sha256"]
    if file_sha(inp) != expected:
        sys.exit("cost_100.csv checksum does not match manifest.json")
    run_dir = ROOT / "runs" / "pilot_100"
    if run_dir.exists():
        shutil.rmtree(run_dir)  # empty result cache for the cold run
    cmd = [sys.executable, "-m", "pipeline", "run", "--input", str(inp), "--run", str(run_dir),
           "--workers", "1", "--verify-sample", str(args.verify_sample)]
    timings = {}
    for phase in ("cold", "warm"):
        before = len(read_jsonl(run_dir / "calls.jsonl")) if (run_dir / "calls.jsonl").exists() else 0
        t0 = time.time()
        subprocess.run(cmd + ["--timing-out", str(run_dir / f"timing_{phase}.json")], cwd=ROOT, check=True)
        wall = time.time() - t0
        after = len(read_jsonl(run_dir / "calls.jsonl"))
        timings[phase] = {"wall_clock_s": round(wall, 3), "new_call_attempts": after - before,
                          "stage_seconds": read_json(run_dir / f"timing_{phase}.json")}
        if phase == "cold":
            cold_calls = after
    calls = read_jsonl(run_dir / "calls.jsonl")
    for i, c in enumerate(calls):
        c["pilot_run"] = "cold" if i < cold_calls else "warm"
    with open(HERE / "pilot_calls.jsonl", "w", encoding="utf-8") as f:
        for c in calls:
            f.write(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n")
    from pipeline.stages import Run, final_records
    run = Run(run_dir)
    with open(HERE / "pilot_records.jsonl", "w", encoding="utf-8") as f:
        for rec, _ in final_records(run):
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    rep = read_json(run_dir / "ingestion_report.json")
    write_json(HERE / "pilot_measurements.json", {
        "input": "data/cost_100.csv", "input_sha256": file_sha(inp), "manifest_sha256": expected,
        "rows": rep["rows"], "distinct_texts": rep["in_scope_distinct_texts"], "workers": 1,
        "label_config": run.label_config, "verify_sample": args.verify_sample, "runs": timings,
        "result_cache": "cold = new empty run folder; warm = same folder, unchanged settings",
        "measured_at": time.strftime("%Y-%m-%d %H:%M:%S %z")})
    for name in ("memo/memo.md", "verify/verify_report.json", "rank/ranking.csv"):
        dst = HERE / "pilot_outputs" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(run_dir / name, dst)
    print("Pilot saved under cost/. Now run the offline replay:  python cost/calculator.py")


# ----------------------------------------------------------------------------------------------
# REPLAY (offline)
# ----------------------------------------------------------------------------------------------
def replay(args):
    rates, meta = load_rates(args.rates)
    s = read_json(HERE / "settings.json")
    for k in ("budget_usd", "workers", "fallback_fraction", "output_token_cap"):
        if getattr(args, k, None) is not None:
            s[k] = getattr(args, k)
    m = read_json(HERE / "pilot_measurements.json")
    calls = read_jsonl(HERE / "pilot_calls.jsonl")
    recs = read_jsonl(HERE / "pilot_records.jsonl")

    # ---- usage.csv : one row per billed item per call --------------------------------------
    usage_rows, cost_by = [], defaultdict(float)
    unknown = []
    for c in calls:
        r = rates.get(c["model"])
        for item in ITEMS:
            units = c.get(item, 0) or 0
            if not units:
                continue
            if r is None or ITEM_RATE[item] not in r:
                unknown.append(f"{c['request_id']}:{item}")
                continue
            per = r[ITEM_RATE[item]]
            cost = units * per
            mm = meta[(c["model"], ITEM_RATE[item])]
            usage_rows.append([c["request_id"], c["pilot_run"], c["role"], c["model"], c["outcome"], item, units,
                               mm["usd_per_unit"], mm["unit_size"], f"{cost:.10f}", mm["source_url"], mm["checked_date"]])
            cost_by[(c["pilot_run"], c["role"])] += cost
    with open(HERE / "usage.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["request_id", "pilot_run", "role", "model", "outcome", "billing_item", "billed_units",
                    "usd_per_unit", "unit_size", "item_cost_usd", "rate_source", "rate_checked"])
        w.writerows(usage_rows)

    # ---- measured tables -------------------------------------------------------------------
    stat = defaultdict(Counter)
    for c in calls:
        k = (c["pilot_run"], c["role"])
        stat[k]["attempts"] += 1
        stat[k][c["outcome"]] += 1
        stat[k]["reviews_sent"] += len(c["review_ids"])
        for item in ITEMS:
            stat[k][item] += c.get(item, 0) or 0
    status = Counter(r["status"] for r in recs)
    cold_cost = sum(v for (run, _), v in cost_by.items() if run == "cold")
    warm_cost = sum(v for (run, _), v in cost_by.items() if run == "warm")
    completed = status.get("completed", 0)
    cold_wall = m["runs"]["cold"]["wall_clock_s"]
    warm_wall = m["runs"]["warm"]["wall_clock_s"]
    warm_enrich_calls = sum(1 for c in calls if c["pilot_run"] == "warm" and c["role"] == "enrich")

    # ---- projections -----------------------------------------------------------------------
    enr = stat[("cold", "enrich")]
    texts = m["distinct_texts"]
    enrich_requests = max(1, sum(1 for c in calls if c["pilot_run"] == "cold" and c["role"] == "enrich"
                                 and c.get("batch_attempt", 1) == 1))
    first_attempts_ok = sum(1 for c in calls if c["pilot_run"] == "cold" and c["role"] == "enrich"
                            and c.get("batch_attempt", 1) == 1 and c["attempt"] == 1)
    measured_retry_rate = max(0.0, enr["attempts"] / enrich_requests - 1)
    enrich_cost_per_text = cost_by[("cold", "enrich")] / texts
    verify_n = max(1, stat[("cold", "verify")]["reviews_sent"])
    verify_cost_per_review = cost_by[("cold", "verify")] / verify_n
    fixed_once = cost_by[("cold", "group")] + cost_by[("cold", "memo")]
    enrich_s = m["runs"]["cold"]["stage_seconds"].get("enrich", cold_wall)
    texts_per_s_one_worker = texts / enrich_s if enrich_s else 0
    hp = rates.get(s["fallback_model"], {})
    lp = rates.get(s["enrich_model"], {})
    price_ratio = (hp.get("output", 0) / lp["output"]) if lp.get("output") else 0

    scen_out = []
    for vol in s["volumes"]:
        for name, sc in s["scenarios"].items():
            retry = sc["extra_retry_rate"]  # on top of retries already inside the measured cost per text
            enrich_cost = vol["texts"] * enrich_cost_per_text * sc["token_multiplier"] * (1 + retry)
            fallback_cost = vol["texts"] * s["fallback_fraction"] * enrich_cost_per_text * price_ratio
            verify_cost = s["verify_sample_full_run"] * verify_cost_per_review * sc["token_multiplier"] * (1 + retry)
            fixed = fixed_once * sc["fixed_multiplier"]
            total = enrich_cost + fallback_cost + verify_cost + fixed
            eff_workers = s["workers"] * s["parallel_efficiency"]
            hours = (vol["texts"] / (texts_per_s_one_worker * eff_workers) * (1 + retry) / 3600
                     if texts_per_s_one_worker else None)
            hours = hours + s["fixed_overhead_minutes"] / 60 if hours is not None else None
            scen_out.append({"volume": vol["name"], "texts_sent": vol["texts"], "rows": vol["rows"],
                             "scenario": name, "extra_retry_rate": round(retry, 4),
                             "enrich_usd": round(enrich_cost, 4), "verify_usd": round(verify_cost, 4),
                             "fallback_usd": round(fallback_cost, 4), "group_memo_once_usd": round(fixed, 4),
                             "total_api_usd": round(total, 4), "est_hours": round(hours, 2) if hours else None,
                             "over_budget": total > s["budget_usd"]})

    out = {"rates_file": str(Path(args.rates).name), "measured": {
        "input_sha256": m["input_sha256"], "ids": len(recs), "record_status": dict(status),
        "distinct_texts": texts, "result_cache_hits_warm": completed if warm_enrich_calls == 0 else None,
        "cold_api_usd": round(cold_cost, 6), "warm_api_usd": round(warm_cost, 6),
        "cold_wall_clock_s": cold_wall, "warm_wall_clock_s": warm_wall,
        "warm_new_enrich_calls": warm_enrich_calls,
        "warm_downstream_calls": sum(1 for c in calls if c["pilot_run"] == "warm" and c["role"] != "enrich"),
        "cost_per_1000_input_rows_usd": round(cold_cost / len(recs) * 1000, 6) if recs else None,
        "cost_per_completed_record_usd": round(cold_cost / completed, 8) if completed else None,
        "throughput_rows_per_s_cold": round(len(recs) / cold_wall, 3) if cold_wall else None,
        "enrich_retry_rate": round(measured_retry_rate, 4), "unknown_cost_items": unknown},
        "controls": {k: s[k] for k in ("budget_usd", "output_token_cap", "workers", "fallback_fraction")},
        "projections": scen_out}
    write_json(HERE / "replay_result.json", out)
    write_report(out, stat, cost_by, m, s, rates, meta)
    print(json.dumps(out["measured"], indent=2))
    print(f"\n{'volume':<30}{'scenario':<14}{'API $':>10}{'hours':>8}  budget")
    for p in scen_out:
        print(f"{p['volume']:<30}{p['scenario']:<14}{p['total_api_usd']:>10.2f}{(p['est_hours'] or 0):>8.2f}  "
              f"{'OVER BUDGET' if p['over_budget'] else 'ok'}")
    print("\nWrote cost/usage.csv, cost/replay_result.json and cost/report.md (no API calls were made).")


def write_report(out, stat, cost_by, m, s, rates, meta):
    L = []
    me = out["measured"]
    L += ["# 100-review cost and runtime report", "",
          "Generated by `python cost/calculator.py` (offline replay of saved usage; no API key needed).", "",
          "## Measured pilot (real calls on data/cost_100.csv, 1 worker)", "",
          f"- Input SHA-256: `{me['input_sha256']}` (matches manifest.json)",
          f"- IDs: {me['ids']}; record status: {me['record_status']}; distinct texts: {me['distinct_texts']}",
          f"- Label config: `{m['label_config']}`; verification sample: {m['verify_sample']} reviews",
          f"- **Cold run:** ${me['cold_api_usd']:.6f} API spend, {me['cold_wall_clock_s']} s wall clock",
          f"- **Warm run (saved results, unchanged settings):** ${me['warm_api_usd']:.6f}, {me['warm_wall_clock_s']} s, "
          f"{me['warm_new_enrich_calls']} new enrichment calls, {me['warm_downstream_calls']} downstream calls",
          f"- Cost per 1,000 input rows: ${me['cost_per_1000_input_rows_usd']}; per completed record: "
          f"${me['cost_per_completed_record_usd']}; cold throughput {me['throughput_rows_per_s_cold']} rows/s",
          f"- Enrichment retry requests per first request: {me['enrich_retry_rate']} (already included in the measured cost per text)",
          f"- Cost items with no rate (shown as unknown, not zero): {me['unknown_cost_items'] or 'none'}", "",
          "### By stage (cold run)", "",
          "| stage | provider | model | attempts | ok | failed | reviews sent | input | cache read | cache write | output | API $ |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for role in ("enrich", "verify", "group", "memo"):
        st = stat[("cold", role)]
        model = s["stage_models"].get(role, "")
        L.append(f"| {role} | anthropic | {model} | {st['attempts']} | {st['succeeded']} | {st['failed']} | "
                 f"{st['reviews_sent']} | {st['input_tokens']} | {st['cache_read_input_tokens']} | "
                 f"{st['cache_creation_input_tokens']} | {st['output_tokens']} | {cost_by[('cold', role)]:.6f} |")
    L += ["", "Stage wall-clock seconds (cold): `" + json.dumps(m["runs"]["cold"]["stage_seconds"]) + "`",
          "", "Stage wall-clock seconds (warm): `" + json.dumps(m["runs"]["warm"]["stage_seconds"]) + "`", "",
          "## Rates used (editable: cost/rates.csv)", "", "| model | item | USD per unit | unit | source | checked |",
          "|---|---|---|---|---|---|"]
    for (model, item), r in sorted(meta.items()):
        if model == "mock":
            continue
        L.append(f"| {model} | {item} | {r['usd_per_unit']} | per {r['unit_size']} {r['unit']} | {r['source_url']} | {r['checked_date']} |")
    L += ["", "Formula: `item_cost = billed_units x usd_per_unit / unit_size`; total = sum of items. "
          "Anthropic reports `input_tokens` excluding cache reads/writes, so the four items are mutually exclusive. "
          "No extended thinking is used, so no reasoning tokens are added.", "",
          "Local compute (Python on a laptop, SQLite) has no API charge; electricity/hardware cost is **unknown, not zero**.",
          "", "## Controls", "", "```json", json.dumps(out["controls"], indent=2), "```", "",
          "## Projections", "", "| volume | texts sent | scenario | extra retry rate | enrich $ | verify $ | fallback $ | group+memo once $ | total API $ | est. hours | budget |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in out["projections"]:
        L.append(f"| {p['volume']} | {p['texts_sent']:,} | {p['scenario']} | {p['extra_retry_rate']} | {p['enrich_usd']:.2f} | "
                 f"{p['verify_usd']:.2f} | {p['fallback_usd']:.2f} | {p['group_memo_once_usd']:.2f} | "
                 f"**{p['total_api_usd']:.2f}** | {p['est_hours']} | {'**OVER BUDGET**' if p['over_budget'] else 'ok'} |")
    L += ["", "Assumptions (cost/settings.json): " + "; ".join(s["assumption_notes"]), "",
          "Exact-text reuse: rows sharing identical text reuse one validated result (same model, prompt and schema). "
          "The 'no reuse' row shows what it would cost to send every nonempty row.", ""]
    (HERE / "report.md").write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("pilot")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--verify-sample", type=int, default=20)
    ap.add_argument("--rates", default=str(HERE / "rates.csv"))
    ap.add_argument("--budget", dest="budget_usd", type=float)
    ap.add_argument("--workers", type=int)
    ap.add_argument("--fallback", dest="fallback_fraction", type=float)
    ap.add_argument("--output-cap", dest="output_token_cap", type=int)
    a = ap.parse_args()
    if a.cmd == "pilot":
        pilot(a)
    else:
        replay(a)


if __name__ == "__main__":
    main()
