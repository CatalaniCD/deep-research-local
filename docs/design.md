# Design: Express + Bootstrap UI with FastAPI sidecar

## Architecture
```
Browser (:3000, Bootstrap 5)
  -> Express ui/server.js (static + /api/* proxy)
    -> FastAPI deep_research/server.py (:8000, transport-only)
      -> deep_research/graph.py (single source of graph truth)
         -> scope graph (clarify_with_user, write_research_brief) [MemorySaver checkpointer]
         -> supervisor_agent -> researcher_agent (AsyncTavilyClient)
         -> final_report_generation
```

Why sidecar: LangGraph code is Python/async; rewriting in Node is out of scope. Express owns presentation, FastAPI owns agent invocation. Proxy keeps single origin for browser.

## File layout
```
docs/                      # intent, specs, plan, design, test
deep_research/             # runtime package (installed via pip install -e .)
  __init__.py
  graph.py                 # all graphs + checkpointer (single source of truth)
  server.py                # FastAPI: /scope, /research, /research/:id, /research/stream
  jobs.py                  # in-memory job store {id, status, brief, notes, final_report}
  models.py                # init_model(role) -> OpenRouter / legacy providers
  prompts.py               # all prompt templates
  utils.py                 # async search + summarization tools
  state_*.py               # graph state schemas
samples/                   # unused tutorial material (legacy graphs + notebooks)
ui/server.js               # Express + http-proxy-middleware -> :8000
ui/views/index.ejs    # 3-step layout (Scope | Research | Result)
ui/public/app.js      # fetch + EventSource, markdown render (marked.js CDN)
ui/public/style.css   # minimal Bootstrap overrides
tests/test_search.py  # mocked search-result suite (see test.md)
pyproject.toml        # fix packaging, deps: langgraph, langchain, tavily-python, fastapi, uvicorn
.env.example          # placeholders only: TAVILY_API_KEY, OPENROUTER_API_KEY, MODEL, caps
.env                  # local secrets (gitignored); copy from .env.example
```

## API contracts
- `POST /scope {query: str, messages?: [{role, content}], session_id?: str} -> {session_id: str, need_clarification: bool, question: str, verification: str, research_brief: str|null}`
  Implementation: `scope_research.ainvoke({messages}, config={configurable:{thread_id: session_id}})`; history persists in `MemorySaver` across turns.
- `POST /research {research_brief: str} -> {jobId: str}` — creates job, `asyncio.create_task(run_supervisor(jobId))`.
- `GET /research/:id -> {status: queued|running|done|error, iterations: int, topics: [{topic, status, sources}], final_report?: str, notes?: str[]}`
- `GET /research/stream?jobId= -> text/event-stream {type: topic_start|source|topic_done|report_chunk|done, payload}` (v1 may poll instead; SSE preferred).

## UI details (Bootstrap)
- Layout: `container` + `nav-pills` (Scope/Research/Result) + `progress` bar driven by `iterations / 6`.
- Scope panel: `form-floating textarea`, `alert-info` for verification, `alert-warning` for clarification question + reply box.
- Research panel: `list-group` of topics with `spinner-border` badges; sources appended as `list-group-item` links.
- Result panel: `marked.parse(final_report)` into `card-body`, `pre` toggle for raw MD, `details` for per-agent compressed notes.
- No build step: Bootstrap 5.3 + marked via CDN, vanilla JS `fetch`/`EventSource` (~200 lines).

## Error / edge handling
- Missing keys at FastAPI startup -> 500 with `{"error":"missing TAVILY_API_KEY"}`; Express shows `alert-danger`.
- Tavily failure per topic -> mark topic `error`, continue others (`supervisor_tools` in `deep_research/graph.py` catches, logs, and ends cleanly).
- Research timeout: 10 min job TTL, Cancel deletes task.

## Security (local)
- Bind `127.0.0.1` only, no auth v1. `.env` never served. Proxy strips `Authorization`. Note for Maintain: add allowlist + sandbox before any network exposure.
