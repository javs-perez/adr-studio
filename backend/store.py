import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import uuid4

from backend.ranking import MINIMUM, SCALE_MAX, WEIGHTS, sort_key
from backend.settings import DATA, settings
from backend.settings import ROOT as ROOT

DB = DATA / "reviews.sqlite3"


def now():
    return datetime.now(UTC).isoformat()


def uid():
    return uuid4().hex


@contextmanager
def connect():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(DB, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize():
    with connect() as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, repo TEXT NOT NULL,
                path TEXT NOT NULL, sha TEXT NOT NULL, body TEXT NOT NULL,
                url TEXT NOT NULL, pr_url TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(repo, path, sha)
            );
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                anchor TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                thread_id TEXT REFERENCES threads(id), kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', activity TEXT NOT NULL DEFAULT '',
                error TEXT, answer TEXT NOT NULL DEFAULT '', selection TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id),
                role TEXT NOT NULL, content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'complete', job_id TEXT REFERENCES jobs(id),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence (
                job_id TEXT NOT NULL REFERENCES jobs(id), url TEXT NOT NULL,
                title TEXT NOT NULL, excerpt TEXT NOT NULL, retrieved_at TEXT NOT NULL,
                PRIMARY KEY(job_id, url)
            );
            CREATE TABLE IF NOT EXISTS provocations (
                id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                job_id TEXT NOT NULL REFERENCES jobs(id), title TEXT NOT NULL,
                category TEXT NOT NULL, quote TEXT NOT NULL, evidence TEXT NOT NULL,
                question TEXT NOT NULL, sources TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback_items (
                id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                thread_id TEXT NOT NULL UNIQUE REFERENCES threads(id),
                title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
                suggested_title TEXT NOT NULL DEFAULT '', suggested_body TEXT NOT NULL DEFAULT '',
                included INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_live_thread_job ON jobs(thread_id)
                WHERE thread_id IS NOT NULL AND status IN ('queued', 'running');
            CREATE UNIQUE INDEX IF NOT EXISTS one_live_research ON jobs(document_id)
                WHERE kind = 'research' AND status IN ('queued', 'running');
        """)
        for table, column, definition in (
            ("jobs", "archived", "INTEGER NOT NULL DEFAULT 0"),
            ("provocations", "ranking", "TEXT"),
            ("provocations", "score", "REAL"),
        ):
            columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        # Retain historical results, but never reuse a vault-exposed research run.
        db.execute("""
            UPDATE jobs SET archived = 1
            WHERE kind = 'research' AND id IN (
                SELECT job_id FROM evidence WHERE url LIKE 'obsidian:%'
                UNION SELECT job_id FROM provocations WHERE sources LIKE '%obsidian:%'
            )
        """)


def allowed_source(url):
    parsed = urlsplit(url)
    return bool(
        parsed.scheme == "https"
        and (
            (
                parsed.netloc == "github.com"
                and re.match(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/blob/", parsed.path)
            )
            or (
                settings.slack_workspace
                and parsed.netloc == settings.slack_workspace
                and parsed.path.startswith("/archives/")
            )
        )
    )


def record_evidence(job_id, url, title, excerpt):
    if not allowed_source(url):
        raise ValueError("Research sources must be GitHub files or the configured Slack workspace.")
    if not job_id:
        return
    with connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO evidence VALUES (?, ?, ?, ?, ?)",
            (job_id, url, title, excerpt[:24000], now()),
        )


def snapshot(document_id):
    with connect() as db:
        row = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if row is None:
            return None
        threads = []
        for row in db.execute(
            "SELECT * FROM threads WHERE document_id = ? ORDER BY created_at", (document_id,)
        ):
            thread = dict(row)
            thread["anchor"] = json.loads(thread["anchor"])
            thread["resolved"] = bool(thread["resolved"])
            thread["messages"] = [
                dict(m)
                for m in db.execute(
                    "SELECT * FROM messages WHERE thread_id = ? ORDER BY created_at", (row["id"],)
                )
            ]
            threads.append(thread)
        document = dict(
            db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        )
        provocations = [
            {
                **dict(p),
                "sources": json.loads(p["sources"]),
                "ranking": json.loads(p["ranking"]) if p["ranking"] else None,
            }
            for p in db.execute(
                "SELECT p.* FROM provocations p JOIN jobs j ON p.job_id = j.id"
                " WHERE p.document_id = ? AND j.archived = 0 ORDER BY p.created_at",
                (document_id,),
            )
        ]
        provocations.sort(key=sort_key)
        for rank, card in enumerate(provocations, 1):
            card["rank"] = rank
        jobs = [
            dict(j)
            for j in db.execute(
                "SELECT * FROM jobs WHERE document_id = ? AND archived = 0"
                " ORDER BY created_at DESC LIMIT 30",
                (document_id,),
            )
        ]
        archived_count = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE document_id = ? AND kind = 'research' AND archived = 1",
            (document_id,),
        ).fetchone()[0]
        feedback = [
            {**dict(row), "included": bool(row["included"]), "anchor": json.loads(row["anchor"])}
            for row in db.execute(
                "SELECT f.*, t.anchor FROM feedback_items f JOIN threads t ON f.thread_id = t.id"
                " WHERE f.document_id = ? ORDER BY f.created_at, f.id",
                (document_id,),
            )
        ]
    return {
        "document": document,
        "threads": threads,
        "provocations": provocations,
        "jobs": jobs,
        "archived_research_count": archived_count,
        "ranking_policy": {"weights": WEIGHTS, "scale_max": SCALE_MAX, "minimum": MINIMUM},
        "feedback_items": feedback,
        "feedback_review": combined_feedback(document, feedback),
        "slack_enabled": bool(settings.slack_workspace),
    }


def combined_feedback(document, items):
    included = [item for item in items if item["included"] and item["body"].strip()]
    if not included:
        return ""
    lines = [f"# Feedback on {document['title']}", "", f"ADR revision: {document['url']}"]
    for item in included:
        lines += [
            "",
            f"## {' '.join(item['title'].split())}",
            "",
            "> " + item["anchor"]["quote"].replace("\n", "\n> "),
            "",
            item["body"].strip(),
        ]
    return "\n".join(lines) + "\n"
