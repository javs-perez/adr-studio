import asyncio
import base64
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

from backend.settings import REPO_PATTERN
from backend.store import record_evidence

RESEARCH_OWNER = os.environ.get("ADR_RESEARCH_OWNER", "")


async def gh(*args):
    process = await asyncio.create_subprocess_exec(
        "gh", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError(stderr.decode(errors="replace").strip() or "GitHub request failed")
    return stdout.decode()


def repo_name(repo):
    if (
        not REPO_PATTERN.fullmatch(repo)
        or not RESEARCH_OWNER
        or repo.split("/")[0].lower() != RESEARCH_OWNER.lower()
    ):
        raise ValueError("Research is limited to repositories owned by the ADR repository's owner.")
    return repo


async def import_pr(url, selected_path=None):
    match = re.fullmatch(
        r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(\d+)(?:/(?:files|changes))?/?",
        url,
    )
    if not match:
        raise ValueError("Use a github.com/<owner>/<repository>/pull/<number> URL.")
    repo, number = match.groups()
    pr = json.loads(
        await gh(
            "pr",
            "view",
            number,
            "--repo",
            repo,
            "--json",
            "title,headRefOid,files,url",
        )
    )
    candidates = [f["path"] for f in pr["files"] if f["path"].endswith(".md")]
    adr_candidates = [p for p in candidates if "adr" in p.lower()]
    candidates = adr_candidates or candidates
    if selected_path and selected_path not in candidates:
        raise ValueError("The selected document is not a Markdown file in this PR.")
    if not selected_path and len(candidates) != 1:
        return {"candidates": candidates}
    path = selected_path or candidates[0]
    sha = pr["headRefOid"]
    body = await gh(
        "api",
        f"repos/{repo}/contents/{quote(path, safe='/')}?ref={sha}",
        "-H",
        "Accept: application/vnd.github.raw+json",
    )
    if len(body) > 200000:
        raise ValueError("This document exceeds the 200,000-character review limit.")
    title = next((line[2:] for line in body.splitlines() if line.startswith("# ")), pr["title"])
    return {
        "repo": repo,
        "path": path,
        "sha": sha,
        "body": body,
        "title": title,
        "url": f"https://github.com/{repo}/blob/{sha}/{path}",
        "pr_url": pr["url"],
    }


async def search_github(query, repo=None):
    if not query.strip() or len(query) > 500:
        raise ValueError("Provide a search query between 1 and 500 characters.")
    # Owner scope is enforced independently of the model's query.
    query = re.sub(r"\b(?:org|repo|user):\S+", "", query, flags=re.IGNORECASE).strip()
    if not RESEARCH_OWNER:
        raise ValueError("The research owner was not configured for this session.")
    if repo:
        scope = f"repo:{repo_name(repo)}"
    else:
        owner = json.loads(await gh("api", f"users/{RESEARCH_OWNER}"))
        qualifier = "org" if owner["type"] == "Organization" else "user"
        scope = f"{qualifier}:{RESEARCH_OWNER}"
    result = json.loads(
        await gh(
            "api", "--method", "GET", "search/code", "-f", f"q={query} {scope}", "-f", "per_page=8"
        )
    )
    return {
        "total": result["total_count"],
        "incomplete": result.get("incomplete_results", False),
        "matches": [
            {"repo": item["repository"]["full_name"], "path": item["path"], "url": item["html_url"]}
            for item in result["items"]
            if item["repository"]["owner"]["login"].lower() == RESEARCH_OWNER.lower()
        ],
        "note": "Search matches are leads, not evidence. Read relevant files before citing.",
    }


async def read_github_file(repo, path, start_line=1, end_line=220, job_id=None):
    repo_name(repo)
    if path.startswith("/") or ".." in Path(path).parts or not path.strip():
        raise ValueError("Use a repository-relative file path.")
    if not (1 <= start_line <= end_line and end_line - start_line < 400):
        raise ValueError("Read 1-400 lines at a time using positive line numbers.")
    # Resolve the default branch to an immutable commit for trustworthy citations.
    commit = json.loads(await gh("api", f"repos/{repo}/commits/HEAD"))["sha"]
    result = json.loads(
        await gh("api", f"repos/{repo}/contents/{quote(path, safe='/')}?ref={commit}")
    )
    if not isinstance(result, dict) or result.get("encoding") != "base64":
        raise ValueError("The path must be a text file supported by the GitHub contents API.")
    content = base64.b64decode(result["content"]).decode("utf-8")
    lines = content.splitlines()
    excerpt = "\n".join(
        f"{i}: {line}" for i, line in enumerate(lines[start_line - 1 : end_line], start_line)
    )
    url = f"https://github.com/{repo}/blob/{commit}/{quote(path, safe='/')}#L{start_line}-L{min(end_line, len(lines))}"
    record_evidence(job_id, url, f"{repo}/{path}", excerpt)
    return {"url": url, "total_lines": len(lines), "content": excerpt}
