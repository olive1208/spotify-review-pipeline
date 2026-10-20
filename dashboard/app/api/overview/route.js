import { q, json } from "../../../lib/db";
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const [run] = await q("SELECT run_id, label_config, scope, details, loaded_at FROM run_info LIMIT 1");
    const summary = Object.fromEntries((await q("SELECT key, value FROM summary")).map((r) => [r.key, Number(r.value)]));
    const topics = await q("SELECT * FROM topic_aggregates ORDER BY completed DESC");
    const monthly = await q("SELECT * FROM monthly ORDER BY month");
    return json({ run, summary, topics, monthly });
  } catch (e) {
    return json({ error: String(e.message || e) }, 500);
  }
}
