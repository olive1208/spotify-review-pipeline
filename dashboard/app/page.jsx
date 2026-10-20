"use client";
import { useEffect, useState } from "react";

const fmt = (n) => (n === undefined || n === null ? "–" : Number(n).toLocaleString("en-US"));

function useApi(path) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    if (!path) return;
    setData(null);
    fetch(path)
      .then((r) => r.json())
      .then((d) => (d.error ? setErr(d.error) : setData(d)))
      .catch((e) => setErr(String(e)));
  }, [path]);
  return [data, err];
}

// Small, safe Markdown renderer (headings, bold, lists, paragraphs). Claim IDs become links to the claims table.
function Markdown({ text }) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (s) =>
    esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`(.+?)`/g, "<code>$1</code>")
      .replace(/\[(C-[A-Z0-9_-]+)\]/g, '<a class="claim" href="#claims" title="claim ID">[$1]</a>');
  const out = [];
  let list = null;
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const li = line.match(/^\s*[-*]\s+(.*)/) || line.match(/^\s*\d+\.\s+(.*)/);
    if (li) {
      list = list || [];
      list.push(`<li>${inline(li[1])}</li>`);
      continue;
    }
    if (list) {
      out.push(`<ul>${list.join("")}</ul>`);
      list = null;
    }
    if (/^###\s/.test(line)) out.push(`<h4>${inline(line.slice(4))}</h4>`);
    else if (/^##\s/.test(line)) out.push(`<h3>${inline(line.slice(3))}</h3>`);
    else if (/^#\s/.test(line)) out.push(`<h2>${inline(line.slice(2))}</h2>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push(`<ul>${list.join("")}</ul>`);
  return <div className="md" dangerouslySetInnerHTML={{ __html: out.join("") }} />;
}

function Kpi({ label, value, note }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {note && <div className="kpi-note">{note}</div>}
    </div>
  );
}

function Bars({ rows, valueKey, labelKey, max }) {
  const m = max || Math.max(1, ...rows.map((r) => Number(r[valueKey])));
  return (
    <div className="bars">
      {rows.map((r) => (
        <div className="bar-row" key={r[labelKey]}>
          <span className="bar-label">{r[labelKey]}</span>
          <span className="bar-track">
            <span className="bar-fill" style={{ width: `${(100 * Number(r[valueKey])) / m}%` }} />
          </span>
          <span className="bar-value">{fmt(r[valueKey])}</span>
        </div>
      ))}
    </div>
  );
}

function Issue({ id }) {
  const [d, err] = useApi(id ? `/api/issues/${id}` : null);
  if (!id) return null;
  if (err) return <p className="error">{err}</p>;
  if (!d) return <p className="muted">Loading {id}…</p>;
  return (
    <div className="card issue">
      <h3>
        {d.issue.issue_id}: {d.issue.issue_name}
      </h3>
      <p>{d.issue.summary}</p>
      <p className="muted">
        Rank {d.rank.rank} · {fmt(d.rank.complaint_count)} complaint/cancellation reviews · mean severity{" "}
        {d.rank.mean_severity} · priority score {fmt(d.rank.priority_score)} · common terms:{" "}
        {(d.issue.top_entities || []).join(", ") || "none"}
      </p>
      <div className="two">
        <div>
          <h4>Severity of member reviews</h4>
          <Bars rows={d.severity.map((s) => ({ k: `severity ${s.severity}`, n: s.n }))} valueKey="n" labelKey="k" />
        </div>
        <div>
          <h4>Intent</h4>
          <Bars rows={d.intents.map((s) => ({ k: s.intent, n: s.n }))} valueKey="n" labelKey="k" />
        </div>
      </div>
      <h4>Evidence (highest severity first; original reviews, not cache copies)</h4>
      <table>
        <thead>
          <tr>
            <th>review_id</th>
            <th>sev</th>
            <th>intent</th>
            <th>evidence quote (exact text)</th>
          </tr>
        </thead>
        <tbody>
          {[...d.examples, ...d.top.filter((t) => !d.examples.some((e) => e.review_id === t.review_id))].map((r) => (
            <tr key={r.review_id}>
              <td className="mono">{r.review_id}</td>
              <td>{r.severity}</td>
              <td>{r.intent}</td>
              <td>{r.evidence_quote}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Lookup() {
  const [id, setId] = useState("");
  const [path, setPath] = useState(null);
  const [d, err] = useApi(path);
  return (
    <div className="card">
      <h3>Trace one review</h3>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setPath(`/api/reviews/${encodeURIComponent(id.trim())}`);
        }}
      >
        <input value={id} onChange={(e) => setId(e.target.value)} placeholder="paste a review_id" />
        <button>Look up</button>
      </form>
      {err && <p className="error">{err}</p>}
      {d && (
        <dl className="record">
          {Object.entries(d.review).map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{typeof v === "object" && v !== null ? JSON.stringify(v) : String(v ?? "")}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

export default function Page() {
  const [ov, ovErr] = useApi("/api/overview");
  const [rk] = useApi("/api/ranking");
  const [memo] = useApi("/api/memo");
  const [open, setOpen] = useState(null);

  if (ovErr) return <main><p className="error">Backend error: {ovErr}</p></main>;
  if (!ov) return <main><p className="muted">Loading data from the database…</p></main>;
  const s = ov.summary;
  const det = ov.run?.details || {};
  const cc = (s["intent:complaint"] || 0) + (s["intent:cancellation"] || 0);
  const quarantined = Object.entries(s).filter(([k]) => k.startsWith("quarantined:"));

  return (
    <main>
      <header>
        <h1>Spotify review priorities</h1>
        <p className="muted">
          Where should next quarter&apos;s product effort go? Google Play reviews, May 2022 – Nov 2023. Every number on
          this page is read from the database by the backend; nothing here calls a model.
        </p>
        <p className="scope">
          <strong>Scope:</strong> {ov.run?.scope}. Run <code>{ov.run?.run_id}</code>, labels{" "}
          <code>{ov.run?.label_config}</code>.
        </p>
      </header>

      <section className="kpis">
        <Kpi label="Source rows accounted for" value={fmt(s.source_rows)} note={`${fmt(det.distinct_nonempty_texts)} distinct nonempty texts`} />
        <Kpi label="Reviews classified" value={fmt(s.completed)} note={`${fmt(s.cache_reuse)} reused an identical text's result`} />
        <Kpi label="Complaints + cancellations" value={fmt(cc)} note={`${fmt(s["intent:cancellation"])} say they will leave`} />
        <Kpi label="Flagged needs_review" value={fmt(s.needs_review)} note="ambiguous or hard to read" />
      </section>
      <p className="muted small">
        Not classified:{" "}
        {quarantined.map(([k, v]) => `${k.replace("quarantined:", "")} ${fmt(v)}`).join(" · ")}
      </p>

      <section className="card">
        <h2>AI-generated recommendation</h2>
        <p className="muted small">
          Written by the memo model from the saved ranking, claims table and a bounded evidence pack. Code checked every
          cited claim ID and number: {memo?.check?.problems?.length === 0 ? "no problems found" : `${memo?.check?.problems?.length ?? "…"} problem(s)`}.
        </p>
        {memo ? <Markdown text={memo.memo} /> : <p className="muted">Loading…</p>}
      </section>

      <section className="card">
        <h2>Issue ranking (baseline)</h2>
        <p className="muted small">
          priority score = complaint count × mean severity = sum of severity, over complaint and cancellation reviews.
          Ties: issue ID ascending. Click a row for evidence.
        </p>
        <table className="rank">
          <thead>
            <tr>
              <th>#</th>
              <th>issue</th>
              <th>name</th>
              <th className="num">complaints</th>
              <th className="num">mean severity</th>
              <th className="num">score</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {(rk?.ranking || []).map((r) => (
              <tr key={r.issue_id} onClick={() => setOpen(r.issue_id)} className={open === r.issue_id ? "sel" : ""}>
                <td>{r.rank}</td>
                <td className="mono">{r.issue_id}</td>
                <td>{r.issue_name}</td>
                <td className="num">{fmt(r.complaint_count)}</td>
                <td className="num">{r.mean_severity}</td>
                <td className="num">{fmt(r.priority_score)}</td>
                <td className="spark">
                  <span style={{ width: `${(100 * r.priority_score) / (rk.ranking[0]?.priority_score || 1)}%` }} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <Issue id={open} />

      <section className="two">
        <div className="card">
          <h2>Classified reviews by topic</h2>
          <table>
            <thead>
              <tr>
                <th>topic</th>
                <th className="num">all</th>
                <th className="num">complaint</th>
                <th className="num">cancel</th>
                <th className="num">request</th>
                <th className="num">praise</th>
                <th className="num">unclear</th>
              </tr>
            </thead>
            <tbody>
              {ov.topics.map((t) => (
                <tr key={t.topic}>
                  <td>{t.topic}</td>
                  <td className="num">{fmt(t.completed)}</td>
                  <td className="num">{fmt(t.complaint)}</td>
                  <td className="num">{fmt(t.cancellation)}</td>
                  <td className="num">{fmt(t.request)}</td>
                  <td className="num">{fmt(t.praise)}</td>
                  <td className="num">{fmt(t.unclear)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card">
          <h2>Complaints + cancellations by month</h2>
          <p className="muted small">Classified sample only. First and last months are partial.</p>
          <Bars rows={ov.monthly} valueKey="complaints_and_cancellations" labelKey="month" />
        </div>
      </section>

      <Lookup />

      <section className="card" id="claims">
        <h2>Claims table</h2>
        <p className="muted small">Every issue-level number the memo may cite, computed by code from saved records.</p>
        <table>
          <thead>
            <tr>
              <th>claim_id</th>
              <th>issue</th>
              <th>metric</th>
              <th className="num">value</th>
            </tr>
          </thead>
          <tbody>
            {(memo?.claims || []).map((c) => (
              <tr key={c.claim_id}>
                <td className="mono">{c.claim_id}</td>
                <td className="mono">{c.issue_id}</td>
                <td>{c.metric}</td>
                <td className="num">{c.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <footer className="muted small">
        Limits: reviews are self-selected and historical; cancellation language is stated intent, not observed churn;
        no revenue or plan data exists in the source.
      </footer>
    </main>
  );
}
