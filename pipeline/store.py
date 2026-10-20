"""SQLite run state. One writer (the main thread) commits after every batch, so a crash or
Ctrl+C never loses completed work and never double-counts it."""
import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS source(
  ord INTEGER PRIMARY KEY, review_id TEXT UNIQUE NOT NULL, row_sha TEXT NOT NULL,
  review_text TEXT NOT NULL, text_key TEXT NOT NULL, review_rating TEXT, review_likes TEXT,
  app_version TEXT, review_timestamp TEXT, in_scope INTEGER NOT NULL DEFAULT 0, scope_reason TEXT);
CREATE INDEX IF NOT EXISTS source_text ON source(text_key);
CREATE TABLE IF NOT EXISTS labels(
  text_key TEXT NOT NULL, label_config TEXT NOT NULL, origin_review_id TEXT NOT NULL,
  topic TEXT, intent TEXT, sentiment REAL, severity INTEGER, entities TEXT, evidence_quote TEXT,
  needs_review INTEGER, repair TEXT, request_id TEXT, phase TEXT, session_id TEXT, attempts INTEGER,
  PRIMARY KEY(text_key, label_config));
CREATE TABLE IF NOT EXISTS failures(
  text_key TEXT NOT NULL, label_config TEXT NOT NULL, origin_review_id TEXT NOT NULL,
  reason TEXT, attempts INTEGER, PRIMARY KEY(text_key, label_config));
CREATE TABLE IF NOT EXISTS sessions(
  session_id TEXT PRIMARY KEY, stage TEXT, phase TEXT, started_at TEXT, ended_at TEXT,
  completed_before INTEGER, completed_after INTEGER, status TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def meta_get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def meta_set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value)))
        self.db.commit()

    def labeled_keys(self, cfg):
        return {r[0] for r in self.db.execute("SELECT text_key FROM labels WHERE label_config=?", (cfg,))}

    def failed_keys(self, cfg):
        return {r[0] for r in self.db.execute("SELECT text_key FROM failures WHERE label_config=?", (cfg,))}

    def save_batch(self, cfg, labeled, failed):
        """labeled: list of dicts; failed: list of (text_key, origin_id, reason, attempts). One transaction."""
        with self.db:
            for r in labeled:
                self.db.execute(
                    "INSERT OR IGNORE INTO labels VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["text_key"], cfg, r["origin_review_id"], r["topic"], r["intent"], r["sentiment"],
                     r["severity"], json.dumps(r["entities"], ensure_ascii=False), r["evidence_quote"],
                     int(r["needs_review"]), r.get("repair"), r["request_id"], r["phase"], r["session_id"],
                     r["attempts"]))
                self.db.execute("DELETE FROM failures WHERE text_key=? AND label_config=?", (r["text_key"], cfg))
            for key, origin, reason, attempts in failed:
                self.db.execute("INSERT OR REPLACE INTO failures VALUES(?,?,?,?,?)", (key, cfg, origin, reason, attempts))

    def completed_ids(self, cfg):
        """All in-scope review IDs whose exact text has a saved label under this config."""
        return [r[0] for r in self.db.execute(
            "SELECT s.review_id FROM source s JOIN labels l ON l.text_key=s.text_key AND l.label_config=? "
            "WHERE s.in_scope=1 ORDER BY s.ord", (cfg,))]
