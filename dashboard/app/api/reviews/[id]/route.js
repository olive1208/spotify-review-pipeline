import { q, json } from "../../../../lib/db";
export const dynamic = "force-dynamic";

export async function GET(_req, { params }) {
  try {
    const [r] = await q("SELECT * FROM reviews WHERE review_id = $1", [params.id.trim()]);
    if (!r) return json({ error: "Review ID not found among the classified sample." }, 404);
    return json({ review: r });
  } catch (e) {
    return json({ error: String(e.message || e) }, 500);
  }
}
