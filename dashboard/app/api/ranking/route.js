import { q, json } from "../../../lib/db";
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const rows = await q(
      `SELECT r.rank, r.issue_id, r.complaint_count, r.severity_sum, r.mean_severity::text AS mean_severity,
              r.priority_score, i.issue_name, i.summary, i.top_entities
         FROM ranking r LEFT JOIN issues i USING (issue_id) ORDER BY r.rank`
    );
    return json({ ranking: rows });
  } catch (e) {
    return json({ error: String(e.message || e) }, 500);
  }
}
