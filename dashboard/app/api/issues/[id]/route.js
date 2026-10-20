import { q, json } from "../../../../lib/db";
export const dynamic = "force-dynamic";

export async function GET(_req, { params }) {
  try {
    const id = params.id;
    const [issue] = await q("SELECT * FROM issues WHERE issue_id = $1", [id]);
    if (!issue) return json({ error: "unknown issue" }, 404);
    const [rank] = await q("SELECT rank, complaint_count, severity_sum, mean_severity::text AS mean_severity, priority_score FROM ranking WHERE issue_id=$1", [id]);
    const severity = await q(
      "SELECT r.severity, count(*)::int AS n FROM membership m JOIN reviews r USING (review_id) WHERE m.issue_id=$1 GROUP BY 1 ORDER BY 1",
      [id]
    );
    const intents = await q(
      "SELECT r.intent, count(*)::int AS n FROM membership m JOIN reviews r USING (review_id) WHERE m.issue_id=$1 GROUP BY 1",
      [id]
    );
    const examples = await q(
      `SELECT review_id, severity, intent, evidence_quote, review_timestamp FROM reviews
        WHERE review_id = ANY($1::text[])`,
      [issue.example_review_ids || []]
    );
    const top = await q(
      `SELECT r.review_id, r.severity, r.intent, r.evidence_quote, r.review_timestamp
         FROM membership m JOIN reviews r USING (review_id)
        WHERE m.issue_id = $1 AND r.cache_source_id IS NULL
        ORDER BY r.severity DESC, r.review_id LIMIT 12`,
      [id]
    );
    return json({ issue, rank, severity, intents, examples, top });
  } catch (e) {
    return json({ error: String(e.message || e) }, 500);
  }
}
