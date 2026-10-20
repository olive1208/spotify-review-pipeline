"""One wrapper for every model call.

Code owns: spend ledger (spent + reserved), bounded retries with backoff/jitter,
call logging (every attempt, including failures), and structured-output parsing.
"""
import json
import os
import random
import threading
import time
import uuid
from datetime import datetime, timezone

from .common import load_env


class BudgetExceeded(Exception):
    pass


class CallFailed(Exception):
    pass


class InjectedFault(Exception):
    """Raised on purpose (PIPELINE_FAULT=role:n) to test retry handling without a real outage."""


TRANSIENT = ("RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError",
             "OverloadedError", "ServiceUnavailableError", "InjectedFault")


def load_rates(path):
    """rates.csv -> {model: {item: usd_per_token}}"""
    import csv
    rates = {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            per = float(r["usd_per_unit"]) / float(r["unit_size"])
            rates.setdefault(r["model"], {})[r["billing_item"]] = per
    return rates


def call_cost(rates, model, usage):
    """Mutually exclusive billing items. Anthropic's input_tokens already excludes cache reads/writes."""
    r = rates.get(model)
    if r is None:
        return None
    return (usage.get("input_tokens", 0) * r.get("input", 0)
            + usage.get("cache_read_input_tokens", 0) * r.get("cache_read", 0)
            + usage.get("cache_creation_input_tokens", 0) * r.get("cache_write", 0)
            + usage.get("output_tokens", 0) * r.get("output", 0))


class LLM:
    def __init__(self, calls_path, rates, spend_limit_usd, spent_before=0.0, max_attempts=3):
        load_env()
        self.calls_path = calls_path
        self.rates = rates
        self.limit = spend_limit_usd
        self.spent = spent_before
        self.reserved = 0.0
        self.max_attempts = max_attempts
        self.lock = threading.Lock()
        self.mock = os.environ.get("PIPELINE_MOCK") == "1"
        self.fault_budget = {}
        for part in filter(None, os.environ.get("PIPELINE_FAULT", "").split(",")):
            role, n = part.split(":")
            self.fault_budget[role] = int(n)
        if not self.mock:
            import anthropic
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise SystemExit("ANTHROPIC_API_KEY is missing. Put it in .env (see .env.example).")
            self.client = anthropic.Anthropic(max_retries=0, timeout=180)

    # ---- spend control -------------------------------------------------
    def _reserve(self, model, est_input_tokens, max_tokens):
        r = self.rates.get(model, {})
        worst = est_input_tokens * r.get("input", 0) + max_tokens * r.get("output", 0)
        with self.lock:
            if self.spent + self.reserved + worst > self.limit:
                raise BudgetExceeded(f"spent ${self.spent:.4f} + reserved ${self.reserved:.4f} + next ${worst:.4f} "
                                     f"> limit ${self.limit:.2f}")
            self.reserved += worst
        return worst

    def _settle(self, reservation, cost):
        with self.lock:
            self.reserved -= reservation
            self.spent += cost or 0.0

    def _log(self, rec):
        line = json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n"
        with self.lock:
            with open(self.calls_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())

    # ---- the call ------------------------------------------------------
    def call(self, *, role, model, system, user, tool, max_tokens, review_ids, phase, label_config,
             run_id, extra=None):
        """Return (parsed tool input dict, call record). Raises CallFailed after bounded attempts, BudgetExceeded on cap."""
        est_in = (len(system) + len(user)) // 3 + 50
        last_err = None
        for attempt in range(1, self.max_attempts + 1):
            reservation = self._reserve(model, est_in, max_tokens)
            started = time.time()
            rec = {"request_id": None, "role": role, "review_ids": list(review_ids), "model": model,
                   "phase": phase, "label_config": label_config, "run_id": run_id, "attempt": attempt,
                   "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "max_tokens": max_tokens, "input_tokens": 0, "output_tokens": 0,
                   "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
            if extra:
                rec.update(extra)
            cost = 0.0
            try:
                if self.fault_budget.get(role, 0) > 0:
                    self.fault_budget[role] -= 1
                    raise InjectedFault("Simulated temporary API failure (PIPELINE_FAULT)")
                if self.mock:
                    from .mock import mock_response
                    rec["model"] = model = "mock"
                    parsed, usage, rid, stop, headers = mock_response(role, user, tool, max_tokens)
                else:
                    kwargs = dict(model=model, max_tokens=max_tokens,
                                  system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                                  messages=[{"role": "user", "content": user}])
                    if tool is not None:  # forced structured output via a tool schema
                        kwargs.update(tools=[tool], tool_choice={"type": "tool", "name": tool["name"]})
                    raw = self.client.messages.with_raw_response.create(**kwargs)
                    resp = raw.parse()
                    headers = {k: raw.headers.get(k) for k in (
                        "anthropic-ratelimit-requests-limit", "anthropic-ratelimit-requests-remaining",
                        "anthropic-ratelimit-output-tokens-limit", "anthropic-ratelimit-output-tokens-remaining",
                        "anthropic-ratelimit-input-tokens-limit", "anthropic-ratelimit-input-tokens-remaining")
                        if raw.headers.get(k) is not None}
                    u = resp.usage
                    usage = {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
                             "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
                             "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0}
                    rid, stop = resp.id, resp.stop_reason
                    if tool is not None:
                        parsed = next((b.input for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
                    else:  # plain text answer (memo); thinking blocks, if any, are ignored
                        txt = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
                        parsed = {"text": txt} if txt else None
                rec.update(usage)
                rec["request_id"] = rid
                rec["stop_reason"] = stop
                rec["ratelimit"] = headers
                cost = call_cost(self.rates, model, usage)
                rec["cost_usd"] = round(cost, 8) if cost is not None else None
                if parsed is None or stop == "max_tokens":
                    rec["outcome"] = "failed"
                    rec["error"] = f"no complete structured output (stop_reason={stop})"
                    last_err = rec["error"]
                    # Invalid output is not a transient error: one retry at most is handled by the caller.
                    return_none = True
                else:
                    rec["outcome"] = "succeeded"
                    return_none = False
                rec["duration_s"] = round(time.time() - started, 3)
                self._settle(reservation, cost)
                self._log(rec)
                if return_none:
                    raise CallFailed(last_err)
                return parsed, rec
            except (CallFailed, BudgetExceeded):
                raise
            except Exception as e:  # API/network error
                name = type(e).__name__
                status = getattr(e, "status_code", None)
                rec.update(outcome="failed", error=f"{name}: {str(e)[:200]}", http_status=status,
                           request_id=rec["request_id"] or f"local-{uuid.uuid4()}",
                           duration_s=round(time.time() - started, 3), cost_usd=0.0,
                           note="failed before a response; provider usage unavailable, logged as 0 tokens")
                self._settle(reservation, 0.0)
                self._log(rec)
                last_err = rec["error"]
                transient = name in TRANSIENT or status in (408, 409, 429, 500, 502, 503, 504, 529)
                if not transient or attempt == self.max_attempts:
                    raise CallFailed(last_err)
                time.sleep(min(30, 2 ** attempt) + random.uniform(0, 1.5))
        raise CallFailed(last_err)
