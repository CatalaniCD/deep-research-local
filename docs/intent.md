# Intent: Local Deep Research Agent with Web UI

Author: User + agent. Status: draft.
Date: 2026-10-16

## Problem
Existing `deep_research` codebase implements a LangGraph multi-agent research pipeline (scope -> supervisor -> parallel researchers -> report) but has no web UI, no local run path, no tests, and broken packaging (`deep_research_from_scratch` imports vs `deep_research/` dir, no `pyproject.toml`). Cannot demo Scope / Research / Result steps or verify search-result quality.

## Proposed outcome
A basic local web app that runs the existing pipeline and visualizes the 3 stages:
1. **Scope** — user query + clarification question + generated `research_brief`.
2. **Research** — supervisor iterations, sub-agent topics, Tavily queries, sources found (live streaming).
3. **Result** — compressed findings + final markdown report with citations + sources list.

Plus a minimal test-suite for agent search results (unit + mocked integration + brief-quality evals reusing `BRIEF_CRITERIA_PROMPT` / `BRIEF_HALLUCINATION_PROMPT`).

## Affected users and systems
- Users: local developer / demo user (single user, localhost only).
- Systems: `research_agent_full.py` (`clarify_with_user`, `supervisor_subgraph`, `final_report_generation`), `utils.py` (`tavily_search`, `summarize_webpage_content`), `prompts.py`, future `app/` UI + `tests/`.

## Constraints
- Localhost only, no auth, no deploy pipeline in this intent.
- Must work with existing LangGraph graphs; no rewrite of supervisor logic.
- API keys via `.env` (`TAVILY_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`).
- Out of scope: multi-user, persistence DB, production deploy, MCP filesystem UI (phase 2).
- Must fix packaging (`pyproject.toml`, correct imports) as prerequisite.

## Open questions
1. UI framework: Streamlit (fastest) vs Chainlit (chat-native) vs FastAPI+HTMX/Next.js (extensible)? Recommendation in `design.md` — default to Streamlit unless chat UX preferred.
2. Streaming: poll LangGraph `astream_events` vs simple `ainvoke` + staged display for v1?
3. Test scope v1: mocked Tavily only, or allow 5-10 live eval queries with cost cap?
4. Model pinning: keep `claude-sonnet-4-6` / `gpt-4.1` or allow `OPENAI_ONLY=true` local fallback?

## Acceptance (for spec.md gate)
- `docs/spec.md` defines Scope/Research/Result screens, API/backend wiring, and test-suite cases.
- Product owner accepts this intent via merge.
