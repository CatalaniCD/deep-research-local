# Test: Search-result suite (mocked only, v1)

Scope lock: **mocked Tavily, no live API cost**. File: `tests/test_search.py` (~250 lines). Run: `pytest -q`.

## Cases

### T1 — deduplicate_search_results (`utils.py:111`)
- Duplicate URL across 2 query responses -> single entry kept (first wins).
- Empty `results` list -> `{}` without KeyError (requires hardening current `response['results']` access).
- Mock: hand-built `[{results:[{url,title,content}]}, ...]`, no network.

### T2 — process_search_results summarization branch
- `raw_content=""` -> uses `content` as-is, no LLM call (patch `summarize_webpage_content` with assert-not-called).
- `raw_content` present -> calls summarizer once, stores `title`+summarized `content`.
- Mock: `unittest.mock.patch("deep_research.utils.summarize_webpage_content", return_value="<summary>ok</summary>")`.

### T3 — format_search_output (`utils.py:156`)
- Empty dict -> contains "No valid search results".
- 2 sources -> contains `--- SOURCE 1`, `--- SOURCE 2`, `URL:`, `SUMMARY:` and 80-dash separator.
- Citation order stable (insertion order).

### T4 — tavily_search end-to-end (mocked client)
- Patch `tavily_client.search` to return 2 canned results with `raw_content`; patch summarizer to echo.
- Assert output string contains both titles + URLs.
- Assert `max_results`/`topic` forwarded to client.

### T5 — summarize_webpage_content fallback (`utils.py:78`)
- Structured model raises -> returns first 1000 chars + `...` (no throw).
- Short content + exception -> returns as-is.

### T6 — compress filtering (regression for think_tool leak)
- Build fake `researcher_messages` [Human, AI(tool_calls tavily), Tool(tavily result), AI(think), Tool(think result)].
- Patch `compress_model.invoke` to capture input messages; assert `think_tool` content excluded OR assert compress prompt contains exclusion instruction (`prompts.py:315-322`). Prefer output-contract test: compressed report contains Tavily facts, not "Reflection recorded".

### T7 — supervisor notes extraction (`deep_research/graph.py: get_notes_from_tool_calls`)
- `get_notes_from_tool_calls([Human, Tool("note A"), Tool("note B")]) == ["note A","note B"]`.

## Fixtures / mocks
- `tests/fixtures/tavily_sample.json`: 3 results (1 duplicate URL, 1 missing `raw_content`).
- All LLM calls patched; no `init_chat_model` at import (lazy-import or `pytest --ignore` needs env guard — add `conftest.py` that sets dummy `OPENAI_API_KEY=test`).
- Failing-test-first rule: write T1/T3 (pure) first, commit, then harden `utils.py` (`response.get('results',[])`).

## Gate
- `pytest -q` green, no network (`pytest-socket --disable-sockets` optional).
- Every future Tavily incident -> new permanent case here (continuous-eval seed).
- Leading metric: first-pass success. Lagging: review time, change failure rate.
