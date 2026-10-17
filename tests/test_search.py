"""Mocked-only search-result suite (see docs/test.md). No network."""
from unittest.mock import AsyncMock, patch

from deep_research import utils
from deep_research.utils import (
    deduplicate_search_results,
    format_search_output,
    process_search_results,
    summarize_webpage_content,
    tavily_search,
)
from deep_research.graph import get_notes_from_tool_calls
from langchain_core.messages import HumanMessage, ToolMessage


def _resp(results):
    return {"results": results}

def _r(url, title="T", content="C", raw=None):
    d = {"url": url, "title": title, "content": content}
    if raw is not None:
        d["raw_content"] = raw
    return d

# T1
def test_deduplicate_keeps_first_and_guards():
    out = deduplicate_search_results([
        _resp([_r("https://a/x", "A1", "c1"), _r("https://b/y", "B", "c2")]),
        _resp([_r("https://a/x", "A2", "c3")]),
        {"no_results_key": True},
        _resp([{"title": "no-url"}]),
    ])
    assert set(out) == {"https://a/x", "https://b/y"}
    assert out["https://a/x"]["title"] == "A1"

# T2
async def test_process_uses_content_when_no_raw_and_patches_summarizer():
    uniq = {"https://a/x": _r("https://a/x", "A", "plain", raw="")}
    with patch("deep_research.utils.summarize_webpage_content", new=AsyncMock()) as m:
        out = await process_search_results(uniq)
        m.assert_not_called()
    assert out["https://a/x"]["content"] == "plain"

async def test_process_summarizes_when_raw_present():
    uniq = {"https://a/x": _r("https://a/x", "A", "plain", raw="long raw")}
    with patch("deep_research.utils.summarize_webpage_content", new=AsyncMock(return_value="<summary>ok</summary>")) as m:
        out = await process_search_results(uniq)
        m.assert_awaited_once_with("long raw")
    assert out["https://a/x"] == {"title": "A", "content": "<summary>ok</summary>"}

# T3
def test_format_empty_and_two_sources():
    assert "No valid search results" in format_search_output({})
    out = format_search_output({
        "https://a/1": {"title": "One", "content": "c1"},
        "https://a/2": {"title": "Two", "content": "c2"},
    })
    assert "--- SOURCE 1" in out and "--- SOURCE 2" in out
    assert "URL: https://a/1" in out and "SUMMARY:" in out
    assert "-" * 80 in out

# T4 — Tavily honors runtime settings (raw content -> per-page summaries).
async def test_tavily_search_end_to_end_mocked(monkeypatch):
    from deep_research.settings import get_settings
    monkeypatch.setattr(get_settings(), "tavily_include_raw", True)
    canned = _resp([_r("https://a/1", "One", "c1", raw="raw1"),
                    _r("https://a/2", "Two", "c2", raw="")])
    with patch.object(utils.tavily_client, "search", new=AsyncMock(return_value=canned)) as s, \
         patch("deep_research.utils.summarize_webpage_content", new=AsyncMock(return_value="SUM")) as summ:
        out = await tavily_search.ainvoke({"query": "coffee sf"})
    s.assert_awaited_once()
    assert "One" in out and "Two" in out
    assert "https://a/1" in out
    summ.assert_awaited_once_with("raw1")

# T5
async def test_summarize_fallback_on_error():
    long_text = "x" * 1500
    with patch("deep_research.utils.get_model") as gm:
        gm.return_value.with_structured_output.side_effect = RuntimeError("boom")
        out = await summarize_webpage_content(long_text)
    assert out.endswith("...") and len(out) <= 1005

# T6 — compress must not leak think_tool (contract on prompt + filter usage)
def test_compress_prompt_excludes_think_tool():
    from deep_research.prompts import compress_research_system_prompt
    assert "think_tool" in compress_research_system_prompt
    assert "Exclude" in compress_research_system_prompt

# T7
def test_get_notes_from_tool_calls():
    msgs = [HumanMessage(content="q"),
            ToolMessage(content="note A", tool_call_id="1"),
            ToolMessage(content="note B", tool_call_id="2")]
    assert get_notes_from_tool_calls(msgs) == ["note A", "note B"]
