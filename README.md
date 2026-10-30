# ADR Studio

A local workspace for thinking through Architecture Decision Records (ADRs), with live AI conversations and source-backed questions.

| Left | Center | Right |
| --- | --- | --- |
| Ranked **provocations**: researched questions that might change a decision | The ADR, pinned to an immutable Git commit | Selection-linked conversations and editable review feedback |

Select a decision, a sentence, or a single word. Ask a question, investigate a precedent, and turn the useful parts of the conversation into feedback for the author.

## Requirements

- **macOS or Linux**, with Python **3.11+** and Node.js **22+**.
- **GitHub CLI** (`gh`), authenticated with access to the repositories you want to review.
- **GitHub Copilot CLI** (`copilot`), installed, authenticated, and available on `PATH`.
- A current desktop browser with the CSS Custom Highlight API, such as a recent Chromium-based browser.

Install the CLIs using their official instructions:

- [GitHub CLI](https://cli.github.com/)
- [GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli)

Authenticate them before starting:

```sh
gh auth login
copilot login
```

The app reuses these local logins. It does not need a separate model API key. AI requests use your Copilot account and its usage allowance. Your CLI must support prompts piped over standard input, JSON streaming, tool allowlists, and additional MCP configuration; update it if startup reports an unsupported flag.

## Quick start

```sh
git clone https://github.com/javs-perez/adr-studio.git
cd adr-studio

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock -e .

cd frontend
npm ci
npm run build
cd ..

./start.sh
```

Open **http://127.0.0.1:8765**. Keep the terminal running; press **Ctrl+C** to stop.

Choose **Open ADR** and paste a GitHub pull request URL:

```text
https://github.com/your-org/your-repository/pull/123
```

The app looks for changed Markdown files, preferring paths containing `adr`. If there is more than one candidate, choose the document you want. The first research run starts automatically when you open the review.

Opening a later commit creates a **separate review**. Existing highlights and conversations remain attached to their original revision.

## Reviewing an ADR

### Conversations

1. Select text in the center pane and choose **Comment**.
2. Write your question and leave **Ask Copilot** enabled for an AI response. Disable it to save a note without calling AI.
3. Continue the thread, jump back to its passage, or mark it **Resolved**.

Selection anchors include exact text, surrounding context, and the document revision. If a passage cannot be located unambiguously, the app reports that rather than attaching the comment somewhere else.

### Provocations

Provocations are questions to investigate, **not conclusions or automated approval checks**.

Copilot reads the ADR and researches relevant GitHub files. By default, research stays within the ADR repository's owner, whether that owner is an organization or an individual. Optional seed repositories help direct discovery. Search results are leads: cards must cite sources retrieved during the run.

Each card contains an ADR quotation, evidence, an actionable question, citations, and an expandable ranking explanation.

| Criterion | Weight | What it measures |
| --- | --- | --- |
| Decision impact | 40% | Could this change the architecture, rollout, or acceptance criteria? |
| Evidence quality | 30% | Is the source authoritative, applicable, and sufficiently current? |
| Novelty | 15% | Does it add something beyond the ADR and existing cards? |
| Actionability | 15% | Is there a concrete question, comparison, or experiment to pursue? |

Each criterion is AI-assessed from **0 to 4**. A candidate needs at least **2 in every criterion** to be published. The server computes the weighted score out of 100 and sorts highest first; ties favor impact, then evidence quality.

These scores are not probabilities. Citation checks establish that a source was retrieved, not that the AI interpreted it correctly. Read the evidence yourself.

- **Save** a card to bookmark it; **Dismiss** it to remove it from the default view.
- **Discuss** a card to start a conversation about its passage.
- Refresh the whole ADR, or select text and choose **Provoke** for focused research.
- Research notes show coverage and limitations. Research is bounded, not exhaustive.
- The prompt discourages reworded duplicates; the backend also rejects normalized quote/question duplicates. Semantic deduplication is not guaranteed.

### Feedback

1. Open a conversation and choose **Add feedback**.
2. In the **Feedback** tab, review the AI suggestion, choose **Use suggestion in editor**, or write your own wording.
3. Edit and **Save feedback**.
4. Choose which saved items to include, then **Combine review** or **Export feedback**.

AI suggestions are stored separately from saved feedback. Regenerating a suggestion does not overwrite your edits. The combined review uses only saved, included items, without another AI rewrite.

**Nothing is posted to GitHub automatically.** Export the Markdown and decide what to share.

## Optional integrations and research configuration

No company-specific repositories or Slack workspace are configured in a fresh checkout.

Create `.data/config.json` to customize this installation:

```json
{
  "research_repositories": [
    "your-org/engineering-handbook",
    "your-org/platform-docs"
  ],
  "slack_workspace": "",
  "datadog_enabled": false
}
```

Seed repositories must use `owner/repository` names. Only seeds matching the current ADR's owner are included in its research prompt. Restart the app after changing configuration.

Environment variables override the corresponding settings:

| Variable | Purpose |
| --- | --- |
| `ADR_DATA_DIR` | Location of `reviews.sqlite3` and `config.json`; defaults to `.data/` in the checkout |
| `ADR_RESEARCH_REPOS` | Comma-separated seed repository names |
| `ADR_SLACK_WORKSPACE` | Slack hostname, such as `your-workspace.slack.com`; empty disables Slack |
| `ADR_DATADOG_ENABLED` | Set to `true` to enable read-only Datadog tools in conversations; defaults to `false` |

### Optional Slack

Slack is **disabled by default**. To enable it:

1. Configure and authenticate a Copilot MCP server named `slack` exposing `slack_search_public` and `slack_read_thread`.
2. Confirm it is listed with `copilot mcp list`.
3. Set `slack_workspace` in local configuration, or set `ADR_SLACK_WORKSPACE`, and restart.

The app exposes only those two Slack tools. Its prompt limits research to public technical discussions and excludes DMs and sensitive topics. These topic restrictions are instructions to the model, not a substitute for your organization's access controls or data policies.

The app does not install a Slack integration or grant workspace access. Missing tools or authorization can limit research; check the run's notes and errors.

### Optional Datadog in conversations

Datadog is **disabled by default**. To enable it:

1. Configure and authenticate a Copilot MCP server named `datadog` using your organization's Datadog connection.
2. Confirm it is listed with `copilot mcp list` and can answer a read-only query in Copilot.
3. Set `"datadog_enabled": true` in local configuration, or set `ADR_DATADOG_ENABLED=true`, and restart.

Conversations can then inspect metrics, dashboards, monitors, logs, traces, hosts, and service dependencies. For example: “What does Datadog show about this service's latency over the last 24 hours?”

The app permits a fixed list of read-only tools. This includes `search_datadog_sdk` and `execute_code` for installations that expose Datadog's server-side query sandbox, as well as dedicated query and skill-guide tools when available. Copilot discovers SDK operations before composing queries. Datadog's [sandbox](https://docs.datadoghq.com/mcp_server/code_execution/) enforces read-only API access and cannot access the local filesystem, arbitrary network destinations, or raw credentials.

Dashboard/notebook editing tools, local shell execution, and other write tools are not exposed. Your existing Datadog permissions still apply; the app does not install the MCP server or grant access. The configured server must be the official Datadog MCP service; the remote sandbox's read-only enforcement is provided by Datadog, not by inspecting generated JavaScript in ADR Studio.

Datadog is available **only in chat**, not automatic or selection-based provocation research. Feedback drafting still has no tools, but can retain relevant Datadog citations already discussed in the conversation. Observations should include their time window, query scope, and units; they are not automatically evidence of a documented standard.

If authentication or a tool is unavailable, Copilot should report that limitation rather than claim it checked data. Retrieved telemetry is sent to Copilot for inference, and findings included in replies persist with the local conversation and may appear in exports.

## Data and privacy

- Reviews, messages, feedback, retrieved excerpts, and research history persist in `.data/reviews.sqlite3`.
- `.data/`, `.venv/`, `node_modules/`, build outputs, and test artifacts are excluded from Git.
- The database is local but **not encrypted by the app**. Treat it and exports as sensitive if you review private documents.
- **Local does not mean offline inference.** The ADR, conversation context, and retrieved material are sent to Copilot to produce AI responses.
- ADR Studio does **not** read an Obsidian vault, personal notes, or Microsoft 365. Historical vault-exposed research from early local versions is archived and excluded from new research; existing conversations are preserved.
- Copilot sessions disable built-in MCP servers and use a tool allowlist containing ADR Studio's GitHub research tools, plus the two Slack tools when Slack is enabled and read-only Datadog tools in chat when Datadog is enabled. Datadog is disabled for provocation research and feedback drafting. Other MCP servers in your own Copilot configuration may still start, but their tools are not offered to the model. Feedback drafting runs with no tools.
- The server binds to loopback and checks request origins. It is a single-user desktop tool, not an authenticated multi-user deployment. Do not expose it as a public server.

To back up your reviews, stop the app and copy the data directory. To relocate the checkout, stop the app, move it with its data, recreate the Python virtual environment, and reinstall from the lockfile before restarting.

## Development

The Python backend uses FastAPI and SQLite. The frontend uses React, TypeScript, and Vite. Copilot runs as a subprocess with a restricted tool allowlist and receives its prompt over standard input, so large ADRs and conversation history never appear in process arguments. A small stdio MCP server supplies GitHub research tools and records evidence in the same data directory as the app.

```text
backend/       API, persistence, research tools, ranking, Copilot integration
frontend/src/  Three-pane UI, highlights, conversations, feedback
tests/         Backend tests and isolated browser server
frontend/tests/ Browser interaction tests
```

For frontend development, run `./start.sh` in one terminal, then:

```sh
cd frontend
npm run dev
```

Use the Vite URL printed in that terminal. API requests are proxied to port 8765. Restart the Python server after backend edits.

Run checks from the repository root:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check backend tests
.venv/bin/python -m ruff format --check backend tests

cd frontend
npm run lint
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

Browser tests start an isolated server on port 8766 with temporary data and fake AI responses. They do not use your review database or consume Copilot requests.
If that port is already in use, run `ADR_TEST_PORT=18766 npm run test:e2e` from `frontend/` to use a different port.

## Troubleshooting and current limits

- **“Frontend not built”**: run `npm ci && npm run build` inside `frontend/`, then restart the backend.
- **GitHub import fails**: check `gh auth status`, repository access, and that the PR contains a Markdown ADR.
- **AI request fails**: try `copilot` directly, check authentication and tool configuration, then use **Retry**. Jobs can be stopped without deleting saved comments.
- **“Address already in use”**: stop the existing app process before starting another instance on port 8765.
- **Lost highlights after a document change**: open the desired revision as a separate review. Annotations are intentionally not migrated between commits.
- **Supported hosts**: imports use `github.com`; GitHub Enterprise Server and other hosts are not supported.
- **Input size**: imported documents are limited to 200,000 characters. Long conversations also depend on CLI and model context limits.
- **Rendering**: Markdown is rendered without raw HTML execution; document images are replaced with placeholders.

## License

[MIT](LICENSE).
