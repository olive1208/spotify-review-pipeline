// Backend-only database access. DATABASE_URL is a server environment variable and never reaches the browser.
import pg from "pg";

let pool;
export function db() {
  if (!pool) {
    const url = process.env.DATABASE_URL;
    if (!url) throw new Error("DATABASE_URL is not set");
    pool = new pg.Pool({
      connectionString: url,
      ssl: url.includes("localhost") || url.includes("host=/") ? false : { rejectUnauthorized: false },
      max: 3,
    });
  }
  return pool;
}

export async function q(sql, params = []) {
  const { rows } = await db().query(sql, params);
  return rows;
}

export function json(data, status = 200) {
  return Response.json(data, { status, headers: { "Cache-Control": "s-maxage=60, stale-while-revalidate=600" } });
}
