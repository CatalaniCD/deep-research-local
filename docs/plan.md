# Plan: Build local UI + search-result tests

Source: `docs/specs.md` + `docs/design.md`. Mode: build from accepted spec.

## Phase 0 — Fix prerequisites (½ day, blocks all)
1. Add `pyproject.toml` (package `deep_research`, deps pinned), `.env.example`, fix imports `deep_research_from_scratch` -> `deep_research`.
   - Restructure: useful runtime code into `deep_research/` package (`graph.py`, `server.py`, `jobs.py`, `models.py`, `prompts.py`, `utils.py`, `state_*.py`); unused tutorial graphs + notebooks into `samples/`.
2. Format `research_agent_prompt.format(date=...)` + `compress_research_human_message.format(research_topic=...)` in `deep_research/graph.py`.
3. Add `tool_call_iterations` guard (`MAX_TOOL_ITERATIONS`) in `should_continue`.
4. Verify: `python -c "import deep_research.graph; print('ok')"`.

## Phase 1 — FastAPI sidecar (½ day)
1. Create `deep_research/server.py` + `deep_research/jobs.py` per design contracts; graphs live in `deep_research/graph.py`.
2. Endpoints call graphs with `await ... .ainvoke()`; scope uses a `MemorySaver` `thread_id` session; `/stream` polls the job store (SSE events phase-2).
3. Verify: `uvicorn deep_research.server:app --port 8000` + `curl POST /scope` + `curl POST /research`.

## Phase 2 — Express + Bootstrap UI (1 day)
1. `npm init -y; npm i express http-proxy-middleware ejs` in `ui/`.
2. `ui/server.js` (static + proxy), `views/index.ejs`, `public/app.js` (Scope->Research->Result flow, SSE with poll fallback).
3. Verify: `npm run dev` (concurrently runs uvicorn + node), manual run with `files/coffee_shops_sf.md` query.

## Phase 3 — Test-suite, mocked only (½ day)
See `docs/test.md`. `pytest tests/test_search.py -q` must pass in CI-less local run.

## Phase 4 — Polish + commit gate
1. `README` run instructions, `make dev` (starts both), `make test`.
2. Human gate: demo Scope/Research/Result + review `REVIEW.md` passes (bugs, source citations, no key leak).

## Worktree / sessions
- Single engineer: max 2 sessions (api + ui). Shared `docs/*.md` as handoff.
- Commands for CLAUDE.md: `Build: pip install -e .`, `UI: npm --prefix ui install && npm --prefix ui start`, `API: uvicorn deep_research.server:app --port 8000`, `Test: pytest -q`.

## Metrics
- Leading: concurrent sessions with review quality held; first-pass `pytest` success.
- Lagging: merged changes/engineer, rework after plan.
