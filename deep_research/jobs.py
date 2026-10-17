"""In-memory job store for local single-user runs."""
from __future__ import annotations
import logging
import uuid
from typing import Any

logger = logging.getLogger("deep_research.jobs")

_jobs: dict[str, dict[str, Any]] = {}

def create_job(research_brief: str) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:8]
    job = {
        "jobId": job_id,
        "status": "queued",
        "research_brief": research_brief,
        "iterations": 0,
        "topics": [],
        "notes": [],
        "final_report": None,
        "error": None,
    }
    _jobs[job_id] = job
    logger.info("job=%s created brief_chars=%d pending=%d", job_id, len(research_brief), len(_jobs))
    return job

def get_job(job_id: str) -> dict[str, Any] | None:
    job = _jobs.get(job_id)
    if job is None:
        logger.warning("job=%s get miss (unknown id)", job_id)
    else:
        logger.debug("job=%s get status=%s", job_id, job.get("status"))
    return job

def update_job(job_id: str, **fields: Any) -> dict[str, Any] | None:
    job = _jobs.get(job_id)
    if job is None:
        logger.warning("job=%s update miss keys=%s", job_id, sorted(fields))
        return None
    old_status = job.get("status")
    job.update(fields)
    if "status" in fields and fields["status"] != old_status:
        logger.info("job=%s status %s -> %s", job_id, old_status, fields["status"])
    else:
        logger.debug("job=%s updated keys=%s", job_id, sorted(fields))
    return job
