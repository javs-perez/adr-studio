import asyncio
import hashlib
import json
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

from backend import ai, research_mcp, sources, store
from backend import app as app_module
from backend import settings as settings_module
from backend.app import app
from backend.ranking import WEIGHTS
from backend.settings import DATA, settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path)
    monkeypatch.setattr(store, "DB", tmp_path / "test.sqlite3")
    monkeypatch.setattr(ai, "launch", lambda _: None)
    monkeypatch.setattr(sources, "RESEARCH_OWNER", "example-org")
    monkeypatch.setattr(settings, "slack_workspace", "")
    monkeypatch.setattr(settings, "datadog_enabled", False)
    monkeypatch.setattr(settings, "research_repositories", [])

    async def import_pr(url, path=None):
        return {
            "title": "Test ADR",
            "repo": "example-org/service-api",
            "path": "doc/adr.md",
            "sha": "a" * 40,
            "body": "# Test ADR\n\nUse **Redis** as a cache.",
            "url": "https://github.com/example-org/service-api/blob/" + "a" * 40 + "/doc/adr.md",
            "pr_url": "https://github.com/example-org/service-api/pull/42",
        }

    monkeypatch.setattr(sources, "import_pr", import_pr)
    with TestClient(app) as result:
        yield result


def document(client):
    return client.post("/api/import", json={"url": "test"}).json()["id"]


def thread(client, document_id, ask_ai=False):
    return client.post(
        f"/api/documents/{document_id}/threads",
        json={
            "anchor": {
                "quote": "Redis",
                "start": 13,
                "end": 18,
                "prefix": "Test ADR\nUse ",
                "suffix": " as a cache.",
                "sha": "a" * 40,
            },
            "message": "Why Redis?",
            "ask_ai": ask_ai,
        },
    )


def ranking(impact=3, evidence=3, novelty=3, action=3):
    return {
        key: {"score": score, "reason": f"Specific justification for {key}."}
        for key, score in zip(WEIGHTS, (impact, evidence, novelty, action), strict=True)
    }


def candidate(source, title="Cache review", question="Could it apply here?", scores=None):
    return {
        "title": title,
        "category": "precedent",
        "quote": "**Redis**",
        "evidence": "An internal guide describes caching.",
        "question": question,
        "sources": [source],
        "ranking": scores or ranking(),
    }


def research_run(client, doc):
    job_id = client.post(f"/api/documents/{doc}/research", json={}).json()["job_id"]
    source = "https://github.com/example-org/engineering-docs/blob/" + "b" * 40 + "/cache.md#L1-L10"
    store.record_evidence(job_id, source, "Caching guide", "Use bounded caching")
    with store.connect() as db:
        job = dict(db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
    return job, source


def test_import_reuses_revision_but_preserves_other_revisions(client, monkeypatch):
    first = document(client)
    assert document(client) == first
    original = sources.import_pr

    async def new_revision(url, path=None):
        result = await original(url, path)
        return {**result, "sha": "b" * 40}

    monkeypatch.setattr(sources, "import_pr", new_revision)
    assert document(client) != first
    assert len(client.get("/api/documents").json()) == 2
    assert client.get(f"/api/documents/{first}").json()["document"]["sha"] == "a" * 40


def test_note_thread_reply_resolve_and_export_persist(client):
    doc = document(client)
    created = thread(client, doc).json()
    assert created["job_id"] is None
    tid = created["thread_id"]
    assert (
        client.post(
            f"/api/threads/{tid}/messages", json={"message": "Follow up", "ask_ai": False}
        ).status_code
        == 200
    )
    client.patch(f"/api/threads/{tid}", json={"resolved": True})
    state = client.get(f"/api/documents/{doc}").json()
    assert state["threads"][0]["resolved"]
    assert [m["content"] for m in state["threads"][0]["messages"]] == ["Why Redis?", "Follow up"]
    exported = client.get(f"/api/documents/{doc}/export")
    assert "Why Redis?" in exported.text
    assert "### Resolved thread" in exported.text
    assert "a" * 40 in exported.text
    assert "attachment" in exported.headers["content-disposition"]


def test_anchor_revision_validation_and_blank_message_rollback(client):
    doc = document(client)
    payload = {
        "anchor": {"quote": "Redis", "start": 1, "end": 6, "sha": "wrong"},
        "message": "why",
        "ask_ai": False,
    }
    assert client.post(f"/api/documents/{doc}/threads", json=payload).status_code == 409
    payload["anchor"]["sha"] = "a" * 40
    payload["message"] = "   "
    assert client.post(f"/api/documents/{doc}/threads", json=payload).status_code == 422
    assert not client.get(f"/api/documents/{doc}").json()["threads"]


def test_live_chat_exclusion_and_retry_keeps_history(client):
    doc = document(client)
    created = thread(client, doc, ask_ai=True).json()
    tid, job = created["thread_id"], created["job_id"]
    assert (
        client.post(f"/api/threads/{tid}/messages", json={"message": "second"}).status_code == 409
    )
    ai.set_job(job, status="failed", error="Example failure")
    ai.update_reply(job, "partial", "failed")
    retry = client.post(f"/api/jobs/{job}/retry")
    assert retry.status_code == 200
    assert client.post(f"/api/jobs/{job}/retry").status_code == 409
    state = store.snapshot(doc)
    retry_job = next(j for j in state["jobs"] if j["id"] == retry.json()["job_id"])
    prompt = ai.prompt_for(retry_job, state)
    assert "Why Redis?" in prompt
    assert '"content": "partial"' not in prompt


def test_initial_research_is_idempotent_even_after_failure(client):
    doc = document(client)
    first = client.post(f"/api/documents/{doc}/research", json={"initial": True}).json()
    assert client.post(f"/api/documents/{doc}/research", json={"initial": True}).json() == first
    ai.set_job(first["job_id"], status="failed", error="network unavailable")
    assert client.post(f"/api/documents/{doc}/research", json={"initial": True}).json() == first
    assert client.post(f"/api/jobs/{first['job_id']}/retry").status_code == 200


def test_research_requires_exact_quote_and_read_sources_atomically(client):
    doc = document(client)
    job_id = client.post(f"/api/documents/{doc}/research", json={}).json()["job_id"]
    state = store.snapshot(doc)
    job = state["jobs"][0]
    source = "https://github.com/example-org/engineering-docs/blob/" + "b" * 40 + "/cache.md#L1-L10"
    store.record_evidence(job_id, source, "Caching guide", "Use bounded caching")
    result = {
        "summary": "One precedent read.",
        "provocations": [
            {
                "title": "Reuse a cache pattern?",
                "category": "precedent",
                "quote": "**Redis**",
                "evidence": "An internal guide describes caching.",
                "question": "Could it apply here?",
                "sources": [source],
                "ranking": ranking(),
            }
        ],
    }
    invalid = {
        **result["provocations"][0],
        "title": "Unsupported",
        "sources": ["https://fake.test"],
    }
    result["provocations"].append(invalid)
    with pytest.raises(ValueError, match="unread source"):
        ai.parse_research(json.dumps(result), job, state["document"]["body"])
    assert not store.snapshot(doc)["provocations"]
    result["provocations"].pop()
    result["provocations"][0]["quote"] = "not in the ADR"
    with pytest.raises(ValueError, match="exact ADR text"):
        ai.parse_research(json.dumps(result), job, state["document"]["body"])
    result["provocations"][0]["quote"] = "**Redis**"
    ai.parse_research(json.dumps(result), job, state["document"]["body"])
    card = store.snapshot(doc)["provocations"][0]
    assert card["sources"][0]["excerpt"] == "Use bounded caching"
    assert (
        client.patch(f"/api/provocations/{card['id']}", json={"status": "saved"}).status_code == 200
    )


def test_restart_marks_interrupted_work_without_removing_comments(client):
    doc = document(client)
    thread(client, doc, ask_ai=True)
    with TestClient(app) as restarted:
        state = restarted.get(f"/api/documents/{doc}").json()
        assert state["jobs"][0]["status"] == "failed"
        assert state["threads"][0]["messages"][0]["content"] == "Why Redis?"
        assert state["threads"][0]["messages"][1]["status"] == "failed"


def test_cross_site_and_unknown_host_are_rejected(client):
    assert client.get("/api/documents", headers={"Host": "attacker.test"}).status_code == 400
    assert (
        client.post(
            "/api/import", json={"url": "test"}, headers={"Origin": "https://attacker.test"}
        ).status_code
        == 403
    )
    assert client.get("/api/documents", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/api/documents/missing").status_code == 404


def test_sources_scope_and_no_vault_capability(monkeypatch):
    monkeypatch.setattr(settings, "slack_workspace", "")
    monkeypatch.setattr(settings, "datadog_enabled", False)
    for repo in ("other/private", "github/repo/../../other", "github/repo?x=y"):
        with pytest.raises(ValueError):
            sources.repo_name(repo)
    for url in (
        "obsidian://open?vault=notes&file=test.md",
        "file:///private.md",
        "https://github.com/other/repo",
        "https://github.com.attacker.test/github/repo",
    ):
        with pytest.raises(ValueError, match="GitHub"):
            store.record_evidence(None, url, "Invalid", "Should never be read")
    tools = asyncio.run(research_mcp.server.list_tools())
    assert {tool.name for tool in tools} == {"search_github", "read_github_file"}
    assert not hasattr(sources, "VAULT")
    assert not hasattr(sources, "read_vault")
    assert not hasattr(sources, "search_vault")
    command = ai.command("test")
    allowlist = next(arg for arg in command if arg.startswith("--available-tools="))
    assert "vault" not in allowlist
    assert "--no-custom-instructions" in command
    assert "--disable-builtin-mcps" in command
    assert "-p" not in command and "--prompt" not in command
    assert [arg for arg in command if arg.startswith("--disable-mcp-server")] == [
        "--disable-mcp-server=datadog",
        "--disable-mcp-server=slack",
    ]


@pytest.mark.parametrize("owner_type,qualifier", [("Organization", "org"), ("User", "user")])
def test_research_scope_follows_imported_owner(monkeypatch, owner_type, qualifier):
    monkeypatch.setattr(sources, "RESEARCH_OWNER", "example-org")
    calls = []

    async def gh(*args):
        calls.append(args)
        if args[1] == "users/example-org":
            return json.dumps({"type": owner_type})
        return json.dumps({"total_count": 0, "items": []})

    monkeypatch.setattr(sources, "gh", gh)
    asyncio.run(sources.search_github("cache org:another-company repo:other/private"))
    assert f"q=cache {qualifier}:example-org" in calls[-1]
    asyncio.run(sources.search_github("cache ORG:another-company Repo:other/private User:x"))
    assert f"q=cache {qualifier}:example-org" in calls[-1]
    assert sources.repo_name("example-org/service-api") == "example-org/service-api"
    with pytest.raises(ValueError, match="owner"):
        sources.repo_name("another-company/service")
    command = ai.command("test", owner="example-org")
    config = json.loads(command[command.index("--additional-mcp-config") + 1])
    env = config["mcpServers"]["adr-research"]["env"]
    assert env["ADR_RESEARCH_OWNER"] == "example-org"
    assert env["ADR_DATA_DIR"] == str(DATA) and DATA.is_absolute()


def test_slack_is_opt_in_and_workspace_scoped(monkeypatch):
    monkeypatch.setattr(settings, "slack_workspace", "")
    assert "--disable-mcp-server=slack" in ai.command("test")
    assert not store.allowed_source("https://example.slack.com/archives/C123/p123")
    monkeypatch.setattr(settings, "slack_workspace", "example.slack.com")
    assert store.allowed_source("https://example.slack.com/archives/C123/p123")
    assert not store.allowed_source("https://different.slack.com/archives/C123/p123")
    command = ai.command("test")
    assert "--allow-url=example.slack.com" in command
    assert "--disable-mcp-server=slack" not in command


def test_ranking_gates_sorting_deduplication_and_export(client):
    doc = document(client)
    job, source = research_run(client, doc)
    cards = [
        candidate(source, "Clarification", "Clarify this?", ranking(2, 3, 2, 3)),
        candidate(source, "Weak evidence", "An unsupported major change?", ranking(4, 1, 4, 4)),
        candidate(source, "Decision changing", "Change the decision?", ranking(4, 4, 3, 3)),
        candidate(source, "Duplicate", "CHANGE the decision!", ranking(4, 4, 3, 3)),
    ]
    summary = ai.parse_research(
        json.dumps({"summary": "Research", "provocations": cards}),
        job,
        store.snapshot(doc)["document"]["body"],
    )
    state = store.snapshot(doc)
    assert [p["title"] for p in state["provocations"]] == ["Decision changing", "Clarification"]
    assert [p["score"] for p in state["provocations"]] == [92.5, 61.25]
    assert [p["rank"] for p in state["provocations"]] == [1, 2]
    assert "Published 2; withheld 1" in summary and "1 duplicate(s)" in summary
    best = state["provocations"][0]
    client.patch(f"/api/provocations/{best['id']}", json={"status": "dismissed"})
    ai.set_job(job["id"], status="complete")
    next_job, _ = research_run(client, doc)
    summary = ai.parse_research(
        json.dumps({"summary": "Refresh", "provocations": [cards[2]]}),
        next_job,
        state["document"]["body"],
    )
    assert "Published 0" in summary and "1 duplicate(s)" in summary
    assert store.snapshot(doc)["provocations"][0]["status"] == "dismissed"
    exported = client.get(f"/api/documents/{doc}/export").text
    assert exported.index("Decision changing") < exported.index("Clarification")
    assert "92.5/100" in exported and "Decision Impact (40%): 4/4" in exported
    assert "Weak evidence" not in exported
    prompt = ai.prompt_for(next_job, store.snapshot(doc))
    assert '"status": "dismissed"' in prompt


def test_ranking_ties_and_missing_assessments_are_explicit(client):
    doc = document(client)
    job, source = research_run(client, doc)
    cards = [
        candidate(source, "Less evidence", "Question A?", ranking(3, 2, 4, 4)),
        candidate(source, "More evidence", "Question B?", ranking(3, 3, 3, 3)),
    ]
    body = store.snapshot(doc)["document"]["body"]
    invalid = candidate(source)
    invalid["ranking"]["novelty"]["score"] = 5
    with pytest.raises(ValueError):
        ai.parse_research(
            json.dumps({"summary": "Research", "provocations": [cards[0], invalid]}), job, body
        )
    assert not store.snapshot(doc)["provocations"]
    del invalid["ranking"]
    with pytest.raises(ValueError):
        ai.parse_research(json.dumps({"summary": "Research", "provocations": [invalid]}), job, body)
    ai.parse_research(json.dumps({"summary": "Research", "provocations": cards}), job, body)
    assert [p["title"] for p in store.snapshot(doc)["provocations"]] == [
        "More evidence",
        "Less evidence",
    ]


def test_legacy_migration_archives_research_but_preserves_conversations(client):
    doc = document(client)
    created = thread(client, doc, ask_ai=True).json()
    job, source = research_run(client, doc)
    ai.parse_research(
        json.dumps({"summary": "Legacy", "provocations": [candidate(source)]}),
        job,
        store.snapshot(doc)["document"]["body"],
    )
    ai.set_job(job["id"], status="complete", answer="Legacy research summary")
    before_threads = store.snapshot(doc)["threads"]
    with store.connect() as db:
        for job_id in (job["id"], created["job_id"]):
            db.execute(
                "INSERT INTO evidence VALUES (?, ?, ?, ?, ?)",
                (
                    job_id,
                    "obsidian://open?file=old.md",
                    "Legacy note",
                    "Legacy excerpt",
                    store.now(),
                ),
            )
        db.execute("ALTER TABLE jobs DROP COLUMN archived")
        db.execute("ALTER TABLE provocations DROP COLUMN ranking")
        db.execute("ALTER TABLE provocations DROP COLUMN score")
    store.initialize()
    store.initialize()
    state = store.snapshot(doc)
    assert state["archived_research_count"] == 1
    assert state["provocations"] == []
    assert state["threads"] == before_threads
    assert all(j["kind"] != "research" for j in state["jobs"])
    assert "Legacy research summary" not in client.get(f"/api/documents/{doc}/export").text
    fresh = client.post(f"/api/documents/{doc}/research", json={"initial": True}).json()
    assert fresh["job_id"] != job["id"]
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM provocations").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 3
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 409
    new_job = next(j for j in store.snapshot(doc)["jobs"] if j["id"] == fresh["job_id"])
    assert "Legacy excerpt" not in ai.prompt_for(new_job, store.snapshot(doc))


def test_feedback_promotion_suggestions_editing_inclusion_and_export(client, monkeypatch):
    doc = document(client)
    tid = thread(client, doc).json()["thread_id"]
    client.patch(f"/api/threads/{tid}", json={"resolved": True})
    promoted = client.post(f"/api/threads/{tid}/feedback").json()
    feedback_id, job_id = promoted["feedback_id"], promoted["job_id"]
    assert client.post(f"/api/threads/{tid}/feedback").json()["feedback_id"] == feedback_id
    assert client.get(f"/api/documents/{doc}/feedback/export").status_code == 422
    assert len(store.snapshot(doc)["threads"][0]["messages"]) == 1
    client.patch(
        f"/api/feedback/{feedback_id}",
        json={"title": "My wording", "body": "Please document cache freshness."},
    )

    async def fake_cli(job, state):
        prompt = ai.prompt_for(job, state)
        assert "Why Redis?" in prompt
        assert "do not research or call tools" in prompt
        return json.dumps({"title": "AI alternative", "body": "Could we state maximum staleness?"})

    monkeypatch.setattr(ai, "run_cli", fake_cli)
    asyncio.run(ai.run_job(job_id))
    state = store.snapshot(doc)
    item = state["feedback_items"][0]
    assert item["title"] == "My wording" and item["body"] == "Please document cache freshness."
    assert item["suggested_title"] == "AI alternative"
    assert len(state["threads"][0]["messages"]) == 1
    assert state["threads"][0]["resolved"]
    exported = client.get(f"/api/documents/{doc}/feedback/export").text
    assert "My wording" in exported and "> Redis" in exported
    assert "AI alternative" not in exported and "maximum staleness" not in exported
    assert "My wording" in client.get(f"/api/documents/{doc}/export").text
    client.patch(f"/api/feedback/{feedback_id}", json={"included": False})
    assert not store.snapshot(doc)["feedback_review"]
    assert client.get(f"/api/documents/{doc}/feedback/export").status_code == 422
    command = ai.command("feedback", allow_research=False)
    assert "--disable-mcp-server=slack" in command
    assert "--additional-mcp-config" not in command
    with TestClient(app) as restarted:
        saved = restarted.get(f"/api/documents/{doc}").json()["feedback_items"][0]
        assert saved["body"] == item["body"] and not saved["included"]


def test_feedback_generation_conflicts_and_retry_preserve_edits(client):
    doc = document(client)
    tid = thread(client, doc, ask_ai=True).json()["thread_id"]
    assert client.post(f"/api/threads/{tid}/feedback").status_code == 409
    with store.connect() as db:
        db.execute("UPDATE jobs SET status = 'complete'")
    promoted = client.post(f"/api/threads/{tid}/feedback").json()
    fid = promoted["feedback_id"]
    assert client.post(f"/api/feedback/{fid}/draft").status_code == 409
    ai.set_job(promoted["job_id"], status="failed", error="Example failure")
    client.patch(f"/api/feedback/{fid}", json={"body": "Human text stays."})
    assert client.post(f"/api/jobs/{promoted['job_id']}/retry").status_code == 200
    assert store.snapshot(doc)["feedback_items"][0]["body"] == "Human text stays."
    assert client.patch(f"/api/feedback/{fid}", json={"title": "   "}).status_code == 422


def test_import_handles_multiple_adrs_and_pins_head(monkeypatch):
    calls = []

    async def gh(*args):
        calls.append(args)
        if args[0] == "pr":
            return json.dumps(
                {
                    "files": [{"path": "doc/adr/a.md"}, {"path": "doc/adr/b.md"}],
                    "headRefOid": "a" * 40,
                    "title": "ADRs",
                    "url": "https://github.com/example-org/service-api/pull/42",
                }
            )
        return "# Chosen ADR"

    monkeypatch.setattr(sources, "gh", gh)
    result = asyncio.run(
        sources.import_pr("https://github.com/example-org/service-api/pull/42/changes")
    )
    assert len(result["candidates"]) == 2
    result = asyncio.run(
        sources.import_pr("https://github.com/example-org/service-api/pull/42", "doc/adr/b.md")
    )
    assert result["title"] == "Chosen ADR"
    assert f"?ref={'a' * 40}" in calls[-1][1]


def test_search_results_are_limited_to_the_research_owner(monkeypatch):
    monkeypatch.setattr(sources, "RESEARCH_OWNER", "example-org")

    def item(owner, repo):
        return {
            "repository": {"full_name": f"{owner}/{repo}", "owner": {"login": owner}},
            "path": "docs/cache.md",
            "url": f"https://api.github.com/repos/{owner}/{repo}",
            "html_url": f"https://github.com/{owner}/{repo}/blob/main/docs/cache.md",
        }

    async def gh(*args):
        if args[1] == "users/example-org":
            return json.dumps({"type": "Organization"})
        items = [item("example-org", "a"), item("Example-Org", "b"), item("another-company", "c")]
        return json.dumps({"total_count": 3, "items": items})

    monkeypatch.setattr(sources, "gh", gh)
    result = asyncio.run(sources.search_github("cache"))
    assert [match["repo"] for match in result["matches"]] == ["example-org/a", "Example-Org/b"]


FAKE_CLI = """
import hashlib, json, sys
prompt = sys.stdin.buffer.read()
content = f"{len(prompt)} {hashlib.sha256(prompt).hexdigest()}"
print(json.dumps({"type": "assistant.message", "data": {"content": content}}), flush=True)
"""


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("kind", ["chat", "research", "feedback"])
def test_datadog_tools_are_read_only_opt_in_and_chat_only(monkeypatch, enabled, kind):
    monkeypatch.setattr(settings, "datadog_enabled", enabled)
    captured = []
    real_command = ai.command

    def fake_command(*args, **kwargs):
        captured.extend(real_command(*args, **kwargs))
        return [sys.executable, "-c", FAKE_CLI]

    monkeypatch.setattr(ai, "command", fake_command)
    monkeypatch.setattr(ai, "prompt_for", lambda job, state: "Sample ADR conversation")
    asyncio.run(
        ai.run_cli(
            {"id": "job", "kind": kind},
            {"document": {"repo": "example-org/service-api"}},
        )
    )
    available = next(arg for arg in captured if arg.startswith("--available-tools="))
    tools = set(available.split("=", 1)[1].split(","))
    datadog_tools = {tool for tool in tools if tool.startswith("datadog-")}
    permissions = {arg for arg in captured if arg.startswith("--allow-tool=datadog")}
    if enabled and kind == "chat":
        assert {
            "datadog-get_datadog_metric",
            "datadog-search_datadog_logs",
            "datadog-search_datadog_dashboards",
            "datadog-list_datadog_skills",
            "datadog-search_datadog_sdk",
            "datadog-execute_code",
        } <= datadog_tools
        assert permissions == {
            f"--allow-tool=datadog({tool.removeprefix('datadog-')})" for tool in datadog_tools
        }
        assert not datadog_tools & {
            "datadog-upsert_datadog_dashboard",
            "datadog-create_datadog_notebook",
            "datadog-edit_datadog_notebook",
            "datadog-delete_datadog_notebook",
            "datadog-submit_mcp_feedback",
        }
        assert not tools & {"shell", "write", "read_file"}
        assert "--disable-mcp-server=datadog" not in captured
    else:
        assert not datadog_tools and not permissions
        assert "--disable-mcp-server=datadog" in captured
    if kind == "feedback":
        assert tools == {"__no_tools__"}


def test_datadog_configuration_and_environment_override(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_module, "DATA", tmp_path)
    monkeypatch.delenv("ADR_DATADOG_ENABLED", raising=False)
    assert not settings_module.load_settings().datadog_enabled
    (tmp_path / "config.json").write_text('{"datadog_enabled": true}')
    assert settings_module.load_settings().datadog_enabled
    monkeypatch.setenv("ADR_DATADOG_ENABLED", "false")
    assert not settings_module.load_settings().datadog_enabled
    monkeypatch.setenv("ADR_DATADOG_ENABLED", "not-a-boolean")
    with pytest.raises(ValueError, match="datadog_enabled"):
        settings_module.load_settings()


def test_long_prompts_are_piped_over_stdin_instead_of_argv(monkeypatch):
    prompt = "Décision " * 40000
    captured = []
    real_command = ai.command

    def fake_command(*args, **kwargs):
        captured.append(real_command(*args, **kwargs))
        return [sys.executable, "-c", FAKE_CLI]

    monkeypatch.setattr(ai, "command", fake_command)
    monkeypatch.setattr(ai, "prompt_for", lambda job, state: prompt)
    job = {"id": "job", "kind": "research"}
    state = {"document": {"repo": "example-org/service-api"}}
    encoded = prompt.encode()
    assert len(encoded) > 128 * 1024
    answer = asyncio.run(ai.run_cli(job, state))
    assert answer == f"{len(encoded)} {hashlib.sha256(encoded).hexdigest()}"
    assert not any("Décision" in arg for arg in captured[0])


def test_cli_exiting_before_reading_prompt_reports_its_error(monkeypatch):
    script = "import sys; sys.stderr.write('error: unknown option'); sys.exit(2)"
    monkeypatch.setattr(ai, "command", lambda *args, **kwargs: [sys.executable, "-c", script])
    monkeypatch.setattr(ai, "prompt_for", lambda job, state: "x" * 4 * 1024 * 1024)
    job = {"id": "job", "kind": "chat"}
    state = {"document": {"repo": "example-org/service-api"}}
    with pytest.raises(RuntimeError, match="unknown option"):
        asyncio.run(ai.run_cli(job, state))


def test_retry_conflicts_with_any_live_job_on_the_thread(client, monkeypatch):
    doc = document(client)
    created = thread(client, doc, ask_ai=True).json()
    tid, chat = created["thread_id"], created["job_id"]
    ai.set_job(chat, status="failed", error="Example failure")
    feedback_job = client.post(f"/api/threads/{tid}/feedback").json()["job_id"]
    retry = client.post(f"/api/jobs/{chat}/retry")
    assert retry.status_code == 409 and "current job" in retry.json()["detail"]
    ai.set_job(feedback_job, status="failed", error="Example failure")
    reply = client.post(f"/api/jobs/{chat}/retry")
    assert reply.status_code == 200
    assert client.post(f"/api/jobs/{feedback_job}/retry").status_code == 409
    ai.set_job(reply.json()["job_id"], status="complete")

    def conflicting_job(*args):
        raise sqlite3.IntegrityError("UNIQUE constraint failed: jobs.thread_id")

    monkeypatch.setattr(app_module, "make_job", conflicting_job)
    retry = client.post(f"/api/jobs/{feedback_job}/retry")
    assert retry.status_code == 409 and "current job" in retry.json()["detail"]


def test_feedback_headings_stay_on_one_line(client):
    doc = document(client)
    anchor = {"quote": "Test ADR\n\nUse", "start": 2, "end": 15, "sha": "a" * 40}
    tid = client.post(
        f"/api/documents/{doc}/threads",
        json={"anchor": anchor, "message": "Clarify this.", "ask_ai": False},
    ).json()["thread_id"]
    fid = client.post(f"/api/threads/{tid}/feedback").json()["feedback_id"]
    assert store.snapshot(doc)["feedback_items"][0]["title"] == "Test ADR Use"
    client.patch(f"/api/feedback/{fid}", json={"title": "Two\nlines", "body": "Please clarify."})
    review = store.snapshot(doc)["feedback_review"]
    assert "## Two lines\n" in review
    assert "> Test ADR\n> \n> Use" in review
