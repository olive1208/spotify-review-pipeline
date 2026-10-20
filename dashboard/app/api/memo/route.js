import { q, json } from "../../../lib/db";
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const [m] = await q("SELECT memo_markdown, check_result FROM memo WHERE id = 1");
    const claims = await q("SELECT * FROM claims ORDER BY claim_id");
    return json({ memo: m?.memo_markdown || "", check: m?.check_result || {}, claims });
  } catch (e) {
    return json({ error: String(e.message || e) }, 500);
  }
}
