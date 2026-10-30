"""Isolated browser fixture; real HTTP/storage/UI, deterministic fake AI only."""

import json
import os
import tempfile

import uvicorn

temporary = tempfile.TemporaryDirectory(prefix="adr-studio-browser-")
os.environ["ADR_DATA_DIR"] = temporary.name

from backend import ai, store  # noqa: E402
from backend.app import app  # noqa: E402
from tests.test_app import ranking  # noqa: E402

store.initialize()
with store.connect() as db:
    db.execute(
        "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "fixture",
            "A test decision",
            "example-org/service-api",
            "doc/adr/test.md",
            "a" * 40,
            "# A test decision\n\nFirst Redis is a cache. Second **Redis** is persistent.\n\n"
            "## Decision\n\nChoose **bounded caching** for resilience.\n\n"
            "## Consequences\n\nWe must document freshness.",
            "https://github.com/example-org/service-api/blob/" + "a" * 40 + "/doc/adr/test.md",
            "https://github.com/example-org/service-api/pull/42",
            store.now(),
        ),
    )


async def fake_cli(job, state):
    if job["kind"] == "chat":
        return "Let's explore that passage. Bounded caching requires an explicit freshness policy."
    if job["kind"] == "feedback":
        return json.dumps(
            {
                "title": "Clarify the freshness guarantee",
                "body": "Could the ADR state the maximum acceptable cache staleness?",
            }
        )
    source = (
        "https://github.com/example-org/engineering-docs/blob/" + "b" * 40 + "/docs/cache.md#L1-L5"
    )
    store.record_evidence(
        job["id"], source, "example-org/engineering-docs/docs/cache.md", "1: Document freshness."
    )
    return json.dumps(
        {
            "summary": "Browser fixture research, not real company evidence.",
            "provocations": [
                {
                    "title": "A smaller clarification",
                    "category": "question",
                    "quote": "document freshness",
                    "evidence": "The test source asks for a freshness policy.",
                    "question": "Can we clarify the wording?",
                    "sources": [source],
                    "ranking": ranking(2, 2, 2, 2),
                },
                {
                    "title": "What freshness guarantee do readers get?",
                    "category": "tradeoff",
                    "quote": "bounded caching",
                    "evidence": "The test source asks for a freshness policy.",
                    "question": "Should we define maximum staleness?",
                    "sources": [source],
                    "ranking": ranking(4, 4, 3, 3),
                },
            ],
        }
    )


ai.run_cli = fake_cli
if __name__ == "__main__":
    try:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=int(os.environ.get("ADR_TEST_PORT", "8766")),
            log_level="warning",
        )
    finally:
        temporary.cleanup()
