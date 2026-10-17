# Deep Research
## Commands
- Run: `./run.sh` (API :8000 + UI :3000; loads `.env` else `.dev.vars`)
- API: `uvicorn deep_research.server:app --port 8000` (from repo root, `PYTHONPATH=.`)
- UI: `npm --prefix ui install && npm --prefix ui start`
- CLI: `deep-research run "query"` (or `python -m deep_research.cli`); `scope`/`research`/`run` subcommands, `--log-level DEBUG` for verbose.
- Test: `python -m pytest -q`
- Install: `pip install -e .`
## Conventions
- Python 3.11+, useful runtime code lives in the `deep_research/` package; unused/tutorial code in `samples/`.
- All graph logic in `deep_research/graph.py`; prompts only in `deep_research/prompts.py`; state schemas in `deep_research/state_*.py`.
- Never log API keys; `.env` not committed.
## Architecture
- `deep_research/graph.py` is the single source of graph truth: scope (`clarify_with_user` -> `write_research_brief`) -> supervisor (fan-out `researcher_agent` w/ Tavily) -> `final_report_generation`.
- Scope + full graphs share a `MemorySaver` checkpointer; session = `thread_id` (API `session_id`).
- All nodes async; `deep_research/utils.py` uses `AsyncTavilyClient` + `ainvoke` so I/O never blocks the loop.
- `deep_research/server.py` is transport-only (FastAPI :8000); `deep_research/cli.py` is the headless test path; `deep_research/jobs.py` is the in-memory job store; `ui/` Express proxies `/api/*` to :8000.
- Logging via `deep_research/log_config.py` (`setup_logging`, `timed`, `kv`); nodes log `node=<name> start/done elapsed=...`. Never log keys.
## Models (OpenRouter)
- All models via `models.init_model(role)`; `OPENROUTER_API_KEY` set -> `ChatOpenAI` at `OPENROUTER_BASE_URL` (default `https://openrouter.ai/api/v1`).
- Model pick: `RESEARCH/SUPERVISOR/SCOPE/SUMMARY/COMPRESS/WRITER_MODEL`, else `MODEL`, else builtin default. Never hardcode `init_chat_model(...)` in graph files.
- Caps via env: `MAX_TOOL_ITERATIONS`, `MAX_RESEARCHER_ITERATIONS`, `MAX_CONCURRENT_RESEARCHERS`.
- Outbound LLM calls are throttled by a shared `AsyncRateLimiter` (`RATE_LIMIT_RPM`, default 18); every model call awaits `limiter.acquire()` in `graph.py`/`utils.py`.
## Things the agent gets wrong
- Forgets `.format(date=...)` on `research_agent_prompt` and `research_topic=...` on compress message — always format.
- Uses `response['results']` without `.get()` guard — always guard.
- Returns raw strings for `MessagesState.messages` — use `AIMessage`.
- Calls sync LangChain/Tavily APIs in async nodes — use `ainvoke` / `AsyncTavilyClient`.
- `ui/server.js`: never register a body parser (`express.json()`) before the `/api` proxy — it consumes the body and the upstream hangs on `Content-Length`. Mount the proxy before any parser.
- FastAPI: register literal routes (`/research/stream`) before parameterized ones (`/research/{job_id}`), or the literal route is shadowed.
## Verifying your work
- Run `python -m pytest -q` and `PYTHONPATH=. python -c "import deep_research.graph; print('ok')"` before reporting done. Paste output. Never weaken tests.
