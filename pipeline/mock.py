"""Offline fake model used ONLY for local software tests (PIPELINE_MOCK=1). Never used for submitted results."""
import json
import random
import uuid


def mock_response(role, user, tool, max_tokens):
    tool = tool or {}
    rnd = random.Random(user)
    usage = {"input_tokens": len(user) // 4 + 900, "output_tokens": 0,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    if role in ("enrich", "verify"):
        items = json.loads(user.split("\n", 1)[1])
        res = []
        for it in items:
            t = it["text"].lower()
            topic = "playback" if "play" in t or "crash" in t else "usability" if "ad" in t else "other"
            intent = "complaint" if any(w in t for w in ("bad", "worst", "hate", "can't", "not", "ads")) else "praise"
            r = {"i": it["i"], "topic": topic, "intent": intent, "severity": 3 if intent == "complaint" else 1}
            if role == "enrich":
                words = it["text"].split()
                r.update(sentiment=-0.5 if intent == "complaint" else 1, entities=["ads"] if "ad" in t else [],
                         quote="" if it["short"] else " ".join(words[:8]), needs_review=False)
                if rnd.random() < 0.03:
                    r["quote"] = "made up quote"
            else:
                r["confident"] = True
            if rnd.random() < 0.02:
                continue  # simulate a dropped item
            res.append(r)
        parsed = {"results": res}
    elif role == "group":
        packs = json.loads(user.split("\n", 1)[1])
        parsed = {"issues": [{"issue_id": p["issue_id"], "issue_name": p["topic"] + " problems",
                              "summary": "Mock summary.", "example_review_ids": [s["review_id"] for s in p["sample"][:2]]}
                             for p in packs]}
    else:
        inp = json.loads(user)
        top = inp["ranking"][0]
        parsed = {"text": f"# Recommendation\nFocus on {top['issue_id']} with {top['complaint_count']} complaints "
                                   f"[C-{top['issue_id']}-count].\n"}
    usage["output_tokens"] = len(json.dumps(parsed)) // 3
    return parsed, usage, "mock-" + uuid.uuid4().hex, "tool_use", {}
