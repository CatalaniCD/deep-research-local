"""FastAPI sidecar wrapping the deep_research graphs. Localhost only.

Transport-only: no graph logic lives here. Scope runs on a checkpointed
session thread (session_id), research runs as a tracked asyncio task.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from .jobs import create_job, get_job, update_job
from .log_config import kv, setup_logging
from .settings import get_settings, openrouter_key_set, set_openrouter_api_key, update_settings

# Configure logging before graph modules emit their build logs.
setup_logging()

from .graph import (  # noqa: E402
    run_research,
    scope_research,
    thread_config,
)

logger = logging.getLogger("deep_research.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    cfg = {
        "openrouter": bool(os.getenv("OPENROUTER_API_KEY")),
        "models": {r: getattr(s, f"{r}_model") for r in ("supervisor", "research", "compress", "writer", "summary", "scope")},
        "timeout": s.model_timeout,
        "caps": {
            "tool": s.max_tool_iterations,
            "supervisor": s.max_researcher_iterations,
            "concurrent": s.max_concurrent_researchers,
        },
        "rate_limit_rpm": s.rate_limit_rpm,
        "tavily": {
            "depth": s.tavily_search_depth,
            "raw": s.tavily_include_raw,
            "max_results": s.tavily_max_results,
            "key": bool(os.getenv("TAVILY_API_KEY")),
        },
    }
    logger.info("api=start config=%s", cfg)
    yield
    logger.info("api=shutdown pending_tasks=%d", len(_tasks))
    for task in list(_tasks.values()):
        task.cancel()
    if _tasks:
        await asyncio.gather(*_tasks.values(), return_exceptions=True)


app = FastAPI(title="deep-research local api", lifespan=lifespan)


class ScopeRequest(BaseModel):
    query: str
    messages: list[dict] | None = None
    session_id: str | None = None


class ResearchRequest(BaseModel):
    research_brief: str


@app.get("/health")
def health() -> dict:
    return {"ok": True}


def _settings_payload() -> dict:
    """Public settings view; exposes only whether an API key is set, never its value."""
    payload = get_settings().to_dict()
    payload["openrouter_key_set"] = openrouter_key_set()
    return payload


@app.get("/settings")
def read_settings() -> dict:
    """Current runtime settings (models, timeout, caps, rate limit, Tavily)."""
    return _settings_payload()


@app.put("/settings")
async def write_settings(payload: dict) -> dict:
    """Partial update; persists to settings.json and applies live.

    A non-empty ``openrouter_api_key`` updates the stored key (kept in
    ``.secrets.json``); the value is never echoed back or logged.
    """
    api_key = payload.pop("openrouter_api_key", None)
    if api_key:
        set_openrouter_api_key(api_key)
    update_settings(payload)
    logger.info("api=settings update fields=%s key_updated=%s",
                sorted(payload.keys()), bool(api_key))
    return _settings_payload()


@app.post("/scope")
async def scope(req: ScopeRequest) -> dict:
    start = time.perf_counter()
    session_id = req.session_id or uuid.uuid4().hex[:8]
    if req.session_id:
        # Checkpointed session: history already persisted, append only the new turn.
        msgs = [HumanMessage(content=req.query)]
    else:
        msgs = [HumanMessage(content=m["content"]) for m in (req.messages or []) if m.get("content")]
        msgs.append(HumanMessage(content=req.query))
    logger.info("api=scope session=%s new=%s query=%s messages=%d", session_id,
                req.session_id is None, req.query[:200].replace("\n", " "), len(msgs))
    try:
        result = await scope_research.ainvoke({"messages": msgs}, config=thread_config(session_id))
    except Exception:
        logger.exception("api=scope error session=%s query=%s elapsed=%.2fs",
                         session_id, req.query[:120].replace("\n", " "), time.perf_counter() - start)
        raise HTTPException(500, "scope failed; see server logs")
    out_msgs = result.get("messages", [])
    last = out_msgs[-1].content if out_msgs else ""
    brief = result.get("research_brief")
    elapsed = time.perf_counter() - start
    if brief:
        logger.info("api=scope done session=%s outcome=brief_ready brief_chars=%d elapsed=%.2fs",
                    session_id, len(brief), elapsed)
        return {"session_id": session_id, "need_clarification": False,
                "question": "", "verification": last, "research_brief": brief}
    logger.info("api=scope done session=%s outcome=needs_clarification elapsed=%.2fs", session_id, elapsed)
    return {"session_id": session_id, "need_clarification": True,
            "question": last, "verification": "", "research_brief": None}


async def _run_research(job_id: str) -> None:
    try:
        job = get_job(job_id)
        if job is None:
            logger.warning("job=%s run_aborted reason=unknown_id", job_id)
            return
        brief = job["research_brief"]
        update_job(job_id, status="running")
        logger.info("job=%s status=running", job_id)
        result = await run_research(brief)
        update_job(job_id, notes=result["notes"], iterations=result["iterations"])
        logger.info("job=%s research_done iterations=%d notes=%d", job_id,
                    result["iterations"], len(result["notes"]))
        update_job(job_id, status="done", final_report=result["final_report"])
        logger.info("job=%s status=done report_chars=%d", job_id, len(str(result["final_report"])))
    except asyncio.CancelledError:
        update_job(job_id, status="cancelled")
        logger.info("job=%s status=cancelled", job_id)
    except Exception:  # keep job inspectable; never leak keys
        logger.exception("job=%s status=error", job_id)
        update_job(job_id, status="error", error="research failed; see server logs")


_tasks: dict[str, asyncio.Task] = {}


@app.post("/research")
async def start_research(req: ResearchRequest) -> dict:
    if not req.research_brief.strip():
        logger.warning("api=research rejected reason=empty_brief")
        raise HTTPException(400, "research_brief required")
    job = create_job(req.research_brief)
    job_id = job["jobId"]
    s = get_settings()
    logger.info("research_start job=%s brief_chars=%d models=%s", job_id, len(req.research_brief),
                {r: getattr(s, f"{r}_model") for r in ("supervisor", "research", "compress", "writer", "summary", "scope")})
    task = asyncio.create_task(_run_research(job_id))
    _tasks[job_id] = task
    task.add_done_callback(lambda _t, jid=job_id: _tasks.pop(jid, None))
    return {"jobId": job_id}


@app.get("/research/stream")
async def stream_research(jobId: str) -> StreamingResponse:
    """Minimal SSE: polls job store (streaming graph events is phase-2)."""
    logger.info("api=stream connect job=%s", jobId)

    async def gen():
        while True:
            job = get_job(jobId)
            if job is None:
                logger.warning("api=stream unknown job=%s", jobId)
                yield 'event: error\ndata: {"error":"unknown job"}\n\n'
                return
            yield f"event: status\ndata: {{\"status\": \"{job['status']}\"}}\n\n"
            if job["status"] in ("done", "error", "cancelled"):
                logger.info("api=stream disconnect job=%s final=%s", jobId, job["status"])
                return
            await asyncio.sleep(2)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/research/{job_id}")
def poll_research(job_id: str) -> dict:
    job = get_job(job_id)
    if job is None:
        logger.warning("api=poll unknown job=%s", job_id)
        raise HTTPException(404, "unknown job")
    logger.debug("api=poll job=%s status=%s", job_id, job.get("status"))
    return job


@app.delete("/research/{job_id}")
async def cancel_research(job_id: str) -> dict:
    task = _tasks.get(job_id)
    if task and not task.done():
        task.cancel()
        logger.info("job=%s status=cancelled via=api (task killed)", job_id)
    elif get_job(job_id) is None:
        logger.warning("api=cancel unknown job=%s", job_id)
        raise HTTPException(404, "unknown job")
    else:
        logger.info("job=%s cancel via=api (already finished)", job_id)
    update_job(job_id, status="cancelled")
    return {"ok": True}
