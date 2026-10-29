import asyncio
import json
import logging
import shutil
import sqlite3
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend import ai, sources
from backend.store import ROOT, connect, initialize, now, snapshot, uid

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app):
    initialize()
    with connect() as db:
        db.execute(
            "UPDATE jobs SET status = 'failed', error = 'Interrupted by server restart. Retry to continue.'"
            " WHERE status IN ('running', 'queued')"
        )
        db.execute("UPDATE messages SET status = 'failed' WHERE status = 'streaming'")
    yield
    tasks = list(ai.TASKS.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="ADR Studio", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.middleware("http")
async def local_requests(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        return JSONResponse({"detail": "Only same-origin requests are allowed."}, status_code=403)
    if request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"detail": "Cross-site access is not allowed."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    )
    return response


class ImportRequest(BaseModel):
    url: str
    path: str | None = None


class Anchor(BaseModel):
    quote: str = Field(min_length=1, max_length=20000)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    prefix: str = Field(default="", max_length=80)
    suffix: str = Field(default="", max_length=80)
    sha: str

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Selection end must follow its start.")
        return self


class ThreadRequest(BaseModel):
    anchor: Anchor
    message: str = Field(min_length=1, max_length=20000)
    ask_ai: bool = True


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    ask_ai: bool = True


class ResearchRequest(BaseModel):
    selection: str | None = Field(default=None, max_length=20000)
    initial: bool = False


class ThreadUpdate(BaseModel):
    resolved: bool


class ProvocationUpdate(BaseModel):
    status: Literal["open", "saved", "dismissed"]


class FeedbackUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=180)
    body: str | None = Field(default=None, max_length=10000)
    included: bool | None = None


def require_state(document_id):
    state = snapshot(document_id)
    if state is None:
        raise HTTPException(404, "Document not found")
    return state


def make_job(db, document_id, kind, thread_id=None, selection=None):
    job_id = uid()
    db.execute(
        "INSERT INTO jobs (id, document_id, thread_id, kind, selection, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (job_id, document_id, thread_id, kind, selection, now(), now()),
    )
    if thread_id and kind == "chat":
        db.execute(
            "INSERT INTO messages VALUES (?, ?, 'assistant', '', 'streaming', ?, ?)",
            (uid(), thread_id, job_id, now()),
        )
    return job_id


def add_user_message(db, thread_id, message):
    if not message.strip():
        raise HTTPException(422, "Write a message first.")
    db.execute(
        "INSERT INTO messages VALUES (?, ?, 'user', ?, 'complete', NULL, ?)",
        (uid(), thread_id, message.strip(), now()),
    )


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "copilot": bool(shutil.which("copilot")),
        "github": bool(shutil.which("gh")),
    }


@app.get("/api/documents")
def documents():
    with connect() as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT id, title, repo, path, sha, created_at FROM documents ORDER BY created_at DESC"
            )
        ]


@app.post("/api/import")
async def import_document(body: ImportRequest):
    try:
        result = await sources.import_pr(body.url, body.path)
    except (ValueError, RuntimeError, OSError, TimeoutError) as error:
        raise HTTPException(400, str(error) or "GitHub request timed out.") from error
    if "candidates" in result:
        return result
    with connect() as db:
        existing = db.execute(
            "SELECT id FROM documents WHERE repo = ? AND path = ? AND sha = ?",
            (result["repo"], result["path"], result["sha"]),
        ).fetchone()
        document_id = existing["id"] if existing else uid()
        if not existing:
            db.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    result["title"],
                    result["repo"],
                    result["path"],
                    result["sha"],
                    result["body"],
                    result["url"],
                    result["pr_url"],
                    now(),
                ),
            )
    return {"id": document_id}


@app.get("/api/documents/{document_id}")
def document(document_id: str):
    return require_state(document_id)


@app.post("/api/documents/{document_id}/threads")
async def create_thread(document_id: str, body: ThreadRequest):
    state = require_state(document_id)
    if body.anchor.sha != state["document"]["sha"]:
        raise HTTPException(409, "This selection belongs to another document revision.")
    thread_id, job_id = uid(), None
    with connect() as db:
        db.execute(
            "INSERT INTO threads VALUES (?, ?, ?, 0, ?)",
            (thread_id, document_id, body.anchor.model_dump_json(), now()),
        )
        add_user_message(db, thread_id, body.message)
        if body.ask_ai:
            job_id = make_job(db, document_id, "chat", thread_id)
    if job_id:
        ai.launch(job_id)
    return {"thread_id": thread_id, "job_id": job_id}


@app.post("/api/threads/{thread_id}/messages")
async def create_message(thread_id: str, body: MessageRequest):
    job_id = None
    try:
        with connect() as db:
            thread = db.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
            if thread is None:
                raise HTTPException(404, "Thread not found")
            active = db.execute(
                "SELECT id FROM jobs WHERE thread_id = ? AND status IN ('queued','running')",
                (thread_id,),
            ).fetchone()
            if active:
                raise HTTPException(409, "Wait for the current reply or stop it first.")
            add_user_message(db, thread_id, body.message)
            if body.ask_ai:
                job_id = make_job(db, thread["document_id"], "chat", thread_id)
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, "A reply is already running.") from error
    if job_id:
        ai.launch(job_id)
    return {"job_id": job_id}


@app.patch("/api/threads/{thread_id}")
def update_thread(thread_id: str, body: ThreadUpdate):
    with connect() as db:
        result = db.execute(
            "UPDATE threads SET resolved = ? WHERE id = ?", (body.resolved, thread_id)
        )
        if result.rowcount == 0:
            raise HTTPException(404, "Thread not found")
    return {"ok": True}


@app.post("/api/documents/{document_id}/research")
async def research(document_id: str, body: ResearchRequest):
    require_state(document_id)
    try:
        with connect() as db:
            previous = db.execute(
                "SELECT id, status FROM jobs WHERE document_id = ? AND kind = 'research'"
                " AND archived = 0"
                " ORDER BY created_at DESC LIMIT 1",
                (document_id,),
            ).fetchone()
            if previous and (body.initial or previous["status"] in ("running", "queued")):
                return {"job_id": previous["id"]}
            job_id = make_job(db, document_id, "research", selection=body.selection)
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, "Research is already running.") from error
    ai.launch(job_id)
    return {"job_id": job_id}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    if task := ai.TASKS.get(job_id):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    else:
        with connect() as db:
            if not db.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone():
                raise HTTPException(404, "Job not found")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str):
    with connect() as db:
        job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise HTTPException(404, "Job not found")
        if job["archived"]:
            raise HTTPException(409, "This research run is archived. Start fresh research instead.")
        if job["status"] not in ("failed", "cancelled"):
            raise HTTPException(409, "Only failed or cancelled jobs can be retried.")
        if db.execute(
            "SELECT id FROM jobs WHERE document_id = ? AND kind = ?"
            " AND (thread_id IS ? OR thread_id = ?) AND status IN ('queued','running')",
            (job["document_id"], job["kind"], job["thread_id"], job["thread_id"]),
        ).fetchone():
            raise HTTPException(409, "A job is already running.")
        new_id = make_job(db, job["document_id"], job["kind"], job["thread_id"], job["selection"])
    ai.launch(new_id)
    return {"job_id": new_id}


@app.patch("/api/provocations/{provocation_id}")
def update_provocation(provocation_id: str, body: ProvocationUpdate):
    with connect() as db:
        result = db.execute(
            "UPDATE provocations SET status = ? WHERE id = ?", (body.status, provocation_id)
        )
        if result.rowcount == 0:
            raise HTTPException(404, "Provocation not found")
    return {"ok": True}


@app.post("/api/threads/{thread_id}/feedback")
async def promote_feedback(thread_id: str):
    with connect() as db:
        thread = db.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
        if thread is None:
            raise HTTPException(404, "Thread not found")
        existing = db.execute(
            "SELECT id FROM feedback_items WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if existing:
            return {"feedback_id": existing["id"]}
        if db.execute(
            "SELECT id FROM jobs WHERE thread_id = ? AND status IN ('queued','running')",
            (thread_id,),
        ).fetchone():
            raise HTTPException(409, "Wait for this conversation's current job or stop it first.")
        if not db.execute(
            "SELECT id FROM messages WHERE thread_id = ? AND role = 'user' AND status = 'complete'",
            (thread_id,),
        ).fetchone():
            raise HTTPException(422, "Add a comment before creating feedback.")
        title = json.loads(thread["anchor"])["quote"].strip()[:180]
        feedback_id = uid()
        db.execute(
            "INSERT INTO feedback_items"
            " (id, document_id, thread_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (feedback_id, thread["document_id"], thread_id, title or "ADR feedback", now(), now()),
        )
        job_id = make_job(db, thread["document_id"], "feedback", thread_id)
    ai.launch(job_id)
    return {"feedback_id": feedback_id, "job_id": job_id}


@app.patch("/api/feedback/{feedback_id}")
def update_feedback(feedback_id: str, body: FeedbackUpdate):
    values = body.model_dump(exclude_none=True)
    if "title" in values and not values["title"].strip():
        raise HTTPException(422, "Feedback needs a title.")
    values["updated_at"] = now()
    columns = ", ".join(f"{key} = ?" for key in values)
    with connect() as db:
        result = db.execute(
            f"UPDATE feedback_items SET {columns} WHERE id = ?", (*values.values(), feedback_id)
        )
        if not result.rowcount:
            raise HTTPException(404, "Feedback item not found")
    return {"ok": True}


@app.post("/api/feedback/{feedback_id}/draft")
async def draft_feedback(feedback_id: str):
    try:
        with connect() as db:
            item = db.execute(
                "SELECT * FROM feedback_items WHERE id = ?", (feedback_id,)
            ).fetchone()
            if item is None:
                raise HTTPException(404, "Feedback item not found")
            job_id = make_job(db, item["document_id"], "feedback", item["thread_id"])
    except sqlite3.IntegrityError as error:
        raise HTTPException(409, "A job is already running for this conversation.") from error
    ai.launch(job_id)
    return {"job_id": job_id}


@app.get("/api/documents/{document_id}/feedback/export")
def export_feedback(document_id: str):
    state = require_state(document_id)
    if not state["feedback_review"]:
        raise HTTPException(422, "Save and include at least one feedback item first.")
    return Response(
        state["feedback_review"],
        media_type="text/markdown",
        headers={"Content-Disposition": 'attachment; filename="adr-feedback.md"'},
    )


@app.get("/api/documents/{document_id}/export")
def export(document_id: str):
    state = require_state(document_id)
    doc = state["document"]
    lines = [
        f"# Review: {doc['title']}",
        "",
        f"Source: {doc['url']}",
        f"PR: {doc['pr_url']}",
        f"Revision: `{doc['sha']}`",
        "",
        "## Provocations",
    ]
    for p in state["provocations"]:
        lines.extend(
            [
                "",
                f"### {p['rank']}. {p['title']} [{p['status']}]",
                "",
                "> " + p["quote"].replace("\n", "\n> "),
                "",
                p["evidence"],
                "",
                p["question"],
                "",
                *[
                    f"- [{s['title']}]({s['url']}) (retrieved {s['retrieved_at']})"
                    for s in p["sources"]
                ],
            ]
        )
        if p["ranking"]:
            lines += ["", f"**AI-assessed priority: {p['score']:g}/100**", ""]
            for criterion, assessment in p["ranking"].items():
                weight = state["ranking_policy"]["weights"][criterion]
                lines.append(
                    f"- {criterion.replace('_', ' ').title()} ({weight}%): "
                    f"{assessment['score']}/4 — {assessment['reason']}"
                )
            lines += ["", "Every criterion must score at least 2/4. Scores are not probabilities."]
    lines += ["", "## Conversations"]
    for thread in state["threads"]:
        lines += [
            "",
            f"### {'Resolved' if thread['resolved'] else 'Open'} thread",
            "",
            "> " + thread["anchor"]["quote"].replace("\n", "\n> "),
            "",
        ]
        for message in thread["messages"]:
            lines += [
                f"**{'You' if message['role'] == 'user' else 'Copilot'} ({message['status']}):**",
                "",
                message["content"],
                "",
            ]
    if state["feedback_review"]:
        lines += ["", state["feedback_review"]]
    lines += ["## Research runs", ""]
    if state["archived_research_count"]:
        lines += [
            f"{state['archived_research_count']} vault-exposed research run(s) archived and excluded.",
            "",
        ]
    for job in state["jobs"]:
        if job["kind"] == "research":
            lines += [
                f"- {job['created_at']} — {job['status']}: {job['answer'] or job['error'] or job['activity']}"
            ]
    return Response(
        "\n".join(lines),
        media_type="text/markdown",
        headers={"Content-Disposition": 'attachment; filename="adr-review.md"'},
    )


DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/")
def index():
    if not (DIST / "index.html").exists():
        raise HTTPException(
            503, "Frontend not built. Run: cd frontend && npm install && npm run build"
        )
    return FileResponse(DIST / "index.html")
