# Spec: Local Deep Research Agent + Web UI

Source intent: `docs/intent.md`. Status: draft.
Stack lock: **Express.js + Bootstrap 5 (UI, :3000) + FastAPI sidecar (Python API, :8000)**. Tests: mocked only.

## 1. UI — 3 steps (single page, Bootstrap tabs + progress)

Single `ui/views/index.ejs` (or static HTML) with 3 panels matching pipeline:

### Step 1 — Scope
- Input: textarea `query` + [Start Research] + [Clarify] buttons.
- Displays: clarification question (from `clarify_with_user`) OR verification message + generated `research_brief` (readonly card).
- API: `POST /api/scope {query, history?} -> {need_clarification, question|verification, research_brief?}`
- Must show `research_brief` verbatim before Research can start (gate).

### Step 2 — Research (live)
- Displays: supervisor iteration count, list of `ConductResearch` topics, per-topic status (pending/running/done), sources as they arrive (title + URL).
- Streaming: SSE `GET /api/research/stream?brief=...` proxying FastAPI `astream_events`; fallback to polling `GET /api/research/:jobId` every 2s for v1 if SSE slips.
- API: `POST /api/research {research_brief} -> {jobId}` then stream/poll.
- Cancel button: `DELETE /api/research/:jobId`.

### Step 3 — Result
- Displays: final markdown report (rendered + raw toggle), Sources section `[n] Title: URL`, plus expandable `compressed_research` per sub-agent and `raw_notes` count.
- API: `GET /api/research/:jobId -> {status, final_report, notes[], raw_notes_count, supervisor_messages_summary}`.
- Export: [Copy Markdown] + [Download .md] buttons (client-side only).

## 2. Backend (package + thin transport)

- All runtime code lives in the `deep_research/` package; obsolete tutorial graphs move to `samples/` (not imported).
- `deep_research/graph.py` is the single source of graph truth: scope (`clarify_with_user` -> `write_research_brief`), `researcher_agent`, `supervisor_agent`, `final_report_generation`, plus the composed `agent`. All nodes async; all models via `models.init_model(role)`.
- Session: scope + full graphs compiled with a shared `MemorySaver` checkpointer; `thread_id` = API `session_id` so multi-turn clarification keeps history.
- `deep_research/server.py` (FastAPI, transport-only): endpoints `/scope` (`{query, messages?, session_id?}`), `/research` (start), `/research/{id}` (poll), `/research/{id}` DELETE (cancel), `/research/stream` (SSE poll). Research runs via `asyncio.create_task`, tracked and cleaned up on completion.
- Async I/O: `utils.py` uses `AsyncTavilyClient` + `ainvoke`; search queries and tool calls run via `asyncio.gather`. Caps via `MAX_TOOL_ITERATIONS`, `MAX_RESEARCHER_ITERATIONS`, `MAX_CONCURRENT_RESEARCHERS`.
- Express `ui/server.js`: serves Bootstrap static, proxies `/api/*` to `http://localhost:8000` via `http-proxy-middleware`, no business logic.

## 3. Functional requirements
- FR1: Enter query -> see clarification OR brief.
- FR2: Confirm brief -> see parallel sub-agent progress + sources.
- FR3: Completion -> see full report with numbered citations, Sources list matches `[n] Title: URL` format from `prompts.py:434-443`.
- FR4: All runs local, keys from `../.env`, never logged to UI.
- FR5: `think_tool` reflections excluded from displayed sources (already filtered in compress prompt).

## 4. Non-functionals / constraints
- Localhost only, single user, no auth, no DB (in-memory `Map<jobId, state>`).
- Node 20+, Python 3.11+, `npm install` + `pip install -e .` documented.
- Page load <1s local; research run bounded by existing `max_researcher_iterations=6`, `max_concurrent_researchers=3`.

## 5. Flags / concerns
- Express+Python split adds 2-process dev (`npm run dev` must start both + Tavily key check). Heavier than Streamlit but per user choice.
- SSE through Express proxy needs `Cache-Control: no-cache`, `X-Accel-Buffering: no` — verify in Build.
- Brief-quality eval prompts (`BRIEF_CRITERIA_PROMPT`) exist but unused — wired into test-suite, not UI v1.

## 6. Acceptance
- `npm run dev` -> open :3000 -> run `files/coffee_shops_sf.md` query -> Scope/Research/Result all populate with ≥3 sources.
- Metrics: time intent->spec (leading), requirements rework after plan (lagging).
