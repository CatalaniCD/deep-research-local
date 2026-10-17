"""Command-line interface for exercising the research pipeline end to end.

Examples:
    deep-research scope "best coffee shops in San Francisco"
    deep-research research --brief "Compare espresso machines under $500"
    deep-research run "state of solid-state batteries in 2026"
    deep-research --log-level DEBUG run "..." --yes

Graph modules are imported lazily so --help stays fast and logging is configured
before the graph emits its build logs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

from .log_config import setup_logging


def _new_session() -> str:
    return uuid.uuid4().hex[:8]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deep-research",
        description="Test the deep-research pipeline (scope -> supervisor -> report) from the CLI.",
    )
    parser.add_argument("--log-level", default=None,
                        help="Logging level (DEBUG/INFO/WARNING). Default: $LOG_LEVEL or INFO.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scope = sub.add_parser("scope", help="Run only the scoping step (clarification / brief).")
    p_scope.add_argument("query", help="Research question.")
    p_scope.add_argument("--session-id", default=None,
                         help="Reuse an existing clarification session (thread id).")
    p_scope.add_argument("--json", action="store_true", help="Emit raw JSON.")

    p_research = sub.add_parser("research", help="Run supervisor research + final report for a brief.")
    p_research.add_argument("--brief", required=True, help="Research brief text.")
    p_research.add_argument("--json", action="store_true", help="Emit raw JSON.")

    p_run = sub.add_parser("run", help="Scope (interactive clarification) then run research.")
    p_run.add_argument("query", help="Research question.")
    p_run.add_argument("--session-id", default=None, help="Reuse an existing session id.")
    p_run.add_argument("--yes", action="store_true", help="Do not prompt before running research.")
    p_run.add_argument("--json", action="store_true", help="Emit raw JSON at the end.")
    return parser


async def _scope_once(query: str, session_id: str) -> dict:
    from langchain_core.messages import HumanMessage

    from .graph import scope_research, thread_config

    result = await scope_research.ainvoke(
        {"messages": [HumanMessage(content=query)]},
        config=thread_config(session_id),
    )
    out_msgs = result.get("messages", [])
    last = out_msgs[-1].content if out_msgs else ""
    brief = result.get("research_brief")
    return {
        "session_id": session_id,
        "need_clarification": brief is None,
        "question": "" if brief else last,
        "verification": last if brief else "",
        "research_brief": brief,
    }


async def _cmd_scope(args) -> int:
    session_id = args.session_id or _new_session()
    res = await _scope_once(args.query, session_id)
    if args.json:
        print(json.dumps(res, indent=2))
        return 0
    if res["need_clarification"]:
        print(f"[session {session_id}] clarification needed:\n{res['question']}")
        return 0
    print(f"[session {session_id}] {res['verification']}\n")
    print("Research brief:\n" + (res["research_brief"] or ""))
    return 0


async def _cmd_research(args) -> int:
    from .graph import run_research

    result = await run_research(args.brief)
    if args.json:
        print(json.dumps({
            "iterations": result["iterations"],
            "notes_count": len(result["notes"]),
            "final_report": result["final_report"],
        }, indent=2))
        return 0
    print(result["final_report"])
    return 0


async def _cmd_run(args) -> int:
    from .graph import run_research

    session_id = args.session_id or _new_session()
    query = args.query
    while True:
        res = await _scope_once(query, session_id)
        if not res["need_clarification"]:
            break
        print(f"\n{res['question']}")
        if args.yes:
            print("--yes set but the model still needs clarification; rerun without --yes.", file=sys.stderr)
            return 2
        try:
            query = input("> ").strip()
        except EOFError:
            return 2
        if not query:
            return 2

    print(f"\n[session {session_id}] {res['verification'] or 'Brief ready.'}")
    print("\nResearch brief:\n" + (res["research_brief"] or "") + "\n")

    if not args.yes:
        try:
            if input("Proceed with research? [Y/n] ").strip().lower() not in ("", "y", "yes"):
                return 0
        except EOFError:
            return 2

    result = await run_research(res["research_brief"])
    if args.json:
        print(json.dumps({
            "iterations": result["iterations"],
            "notes_count": len(result["notes"]),
            "final_report": result["final_report"],
        }, indent=2))
        return 0
    print("\n" + "=" * 80 + "\n")
    print(result["final_report"])
    return 0


_DISPATCH = {
    "scope": _cmd_scope,
    "research": _cmd_research,
    "run": _cmd_run,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level)
    try:
        return asyncio.run(_DISPATCH[args.command](args))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
