"""Canonical LangGraph definitions for the deep research pipeline.

Single source of truth for every graph:
  1. scope graph     — clarify_with_user -> write_research_brief (checkpointed session)
  2. researcher graph — iterative Tavily search + compress (one sub-topic)
  3. supervisor graph — fans out researcher sub-agents, aggregates notes
  4. full graph       — scope -> supervisor -> final report

All nodes are async and all models come from models.init_model(role), so the
pipeline never blocks the API event loop and honours OPENROUTER / per-role env.
The scope + full graphs share a MemorySaver checkpointer keyed by thread_id so a
clarification session keeps its conversation history across requests.

Every node logs start/done with timing and key counts (LOG_LEVEL=DEBUG for more).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    filter_messages,
    get_buffer_string,
)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from .log_config import kv, timed
from .models import get_model
from .rate_limit import limiter
from .settings import get_settings
from .prompts import (
    clarify_with_user_instructions,
    compress_research_human_message,
    compress_research_system_prompt,
    final_report_generation_prompt,
    lead_researcher_prompt,
    research_agent_prompt,
    transform_messages_into_research_topic_prompt,
)
from .state_multi_agent_supervisor import (
    ConductResearch,
    ResearchComplete,
    SupervisorState,
)
from .state_research import ResearcherOutputState, ResearcherState
from .state_scope import (
    AgentInputState,
    AgentState,
    ClarifyWithUser,
    ResearchQuestion,
)
from .utils import get_today_str, tavily_search, think_tool

logger = logging.getLogger("deep_research.graph")

# ===== CONFIGURATION =====

# Models, timeout, caps, rate limit and Tavily params are read live from
# deep_research.settings at call time (see graph + utils nodes), so edits made in
# the UI Settings panel apply to the next run without a restart.

# Models come from the shared factory (models.get_model), never hardcoded here.
logger.info("graph=build settings=%s", kv(**get_settings().to_dict()))

# ===== SESSION STORE (checkpointer) =====

# Local single-user in-memory checkpointer. thread_id == session_id from the API.
CHECKPOINTER = MemorySaver()


def thread_config(session_id: str) -> dict:
    """Build the LangGraph config that binds a run to a session thread."""
    return {"configurable": {"thread_id": session_id}}


# ===== SCOPE NODES =====

async def clarify_with_user(
    state: AgentState,
) -> Command[Literal["write_research_brief", "__end__"]]:
    """Decide whether to ask a clarifying question or proceed to the brief."""
    message_count = len(state.get("messages", []))
    structured_output_model = get_model("scope").with_structured_output(ClarifyWithUser)
    await limiter.acquire()
    with timed(logger, "node=clarify_with_user", messages=message_count):
        response = await structured_output_model.ainvoke([
            HumanMessage(content=clarify_with_user_instructions.format(
                messages=get_buffer_string(messages=state["messages"]),
                date=get_today_str(),
            ))
        ])
    logger.info("node=clarify_with_user outcome=%s question_chars=%d verification_chars=%d",
                "clarification" if response.need_clarification else "proceed",
                len(response.question or ""), len(response.verification or ""))
    if response.need_clarification:
        return Command(
            goto=END,
            update={"messages": [AIMessage(content=response.question)]},
        )
    return Command(
        goto="write_research_brief",
        update={"messages": [AIMessage(content=response.verification)]},
    )


async def write_research_brief(state: AgentState) -> dict:
    """Turn the conversation into a concrete research brief for the supervisor."""
    message_count = len(state.get("messages", []))
    structured_output_model = get_model("scope").with_structured_output(ResearchQuestion)
    await limiter.acquire()
    with timed(logger, "node=write_research_brief", messages=message_count):
        response = await structured_output_model.ainvoke([
            HumanMessage(content=transform_messages_into_research_topic_prompt.format(
                messages=get_buffer_string(state.get("messages", [])),
                date=get_today_str(),
            ))
        ])
    logger.info("node=write_research_brief brief_chars=%d brief=%s",
                len(response.research_brief or ""), response.research_brief)
    return {
        "research_brief": response.research_brief,
        "supervisor_messages": [HumanMessage(content=f"{response.research_brief}.")],
    }


def build_scope_graph(checkpointer=None):
    builder = StateGraph(AgentState, input_schema=AgentInputState)
    builder.add_node("clarify_with_user", clarify_with_user)
    builder.add_node("write_research_brief", write_research_brief)
    builder.add_edge(START, "clarify_with_user")
    builder.add_edge("write_research_brief", END)
    return builder.compile(checkpointer=checkpointer)


# ===== RESEARCHER NODES =====

tools = [tavily_search, think_tool]
tools_by_name = {tool.name: tool for tool in tools}


async def llm_call(state: ResearcherState) -> dict:
    """Ask the researcher model for the next search/reflection step."""
    iteration = state.get("tool_call_iterations", 0) + 1
    await limiter.acquire()
    with timed(logger, "node=llm_call", iteration=iteration,
               messages=len(state.get("researcher_messages", []))):
        response = await get_model("research").bind_tools(tools).ainvoke(
            [SystemMessage(content=research_agent_prompt.format(date=get_today_str()))]
            + state["researcher_messages"]
        )
    tool_calls = [tc["name"] for tc in (response.tool_calls or [])]
    logger.info("node=llm_call iteration=%d tool_calls=%s content_chars=%d",
                iteration, tool_calls, len(str(response.content or "")))
    return {
        "researcher_messages": [response],
        "tool_call_iterations": iteration,
    }


async def tool_node(state: ResearcherState) -> dict:
    """Execute every tool call from the last researcher message, in parallel."""
    tool_calls = state["researcher_messages"][-1].tool_calls
    names = [tc["name"] for tc in tool_calls]
    with timed(logger, "node=tool_node", iteration=state.get("tool_call_iterations", 0),
               tools=names):
        observations = await asyncio.gather(*[
            tools_by_name[tool_call["name"]].ainvoke(tool_call["args"])
            for tool_call in tool_calls
        ])
    logger.info("node=tool_node results=%s",
                [f"{n}:{len(str(o))}c" for n, o in zip(names, observations)])
    return {
        "researcher_messages": [
            ToolMessage(
                content=observation,
                name=tool_call["name"],
                tool_call_id=tool_call["id"],
            )
            for observation, tool_call in zip(observations, tool_calls)
        ]
    }


def should_continue(state: ResearcherState) -> Literal["tool_node", "compress_research"]:
    """Continue the search loop, or compress once the budget/answer is reached."""
    iterations = state.get("tool_call_iterations", 0)
    if iterations >= get_settings().max_tool_iterations:
        logger.info("route=researcher next=compress_research reason=max_tool_iterations iterations=%d", iterations)
        return "compress_research"
    if state["researcher_messages"][-1].tool_calls:
        logger.debug("route=researcher next=tool_node iterations=%d", iterations)
        return "tool_node"
    logger.info("route=researcher next=compress_research reason=no_tool_calls iterations=%d", iterations)
    return "compress_research"


async def compress_research(state: ResearcherState) -> dict:
    """Compress the researcher transcript into notes the supervisor can merge."""
    messages = state.get("researcher_messages", [])
    system_message = compress_research_system_prompt.format(date=get_today_str())
    human_message = compress_research_human_message.format(
        research_topic=state.get("research_topic", "")
    )
    await limiter.acquire()
    with timed(logger, "node=compress_research", topic=state.get("research_topic", ""),
               messages=len(messages)):
        response = await get_model("compress").ainvoke(
            [SystemMessage(content=system_message)] + list(messages) + [HumanMessage(content=human_message)]
        )
    raw_notes = [
        str(m.content)
        for m in filter_messages(messages, include_types=["tool", "ai"])
    ]
    logger.info("node=compress_research compressed_chars=%d raw_notes=%d",
                len(str(response.content or "")), len(raw_notes))
    return {
        "compressed_research": str(response.content),
        "raw_notes": ["\n".join(raw_notes)],
    }


def build_researcher_agent(checkpointer=None):
    builder = StateGraph(ResearcherState, output_schema=ResearcherOutputState)
    builder.add_node("llm_call", llm_call)
    builder.add_node("tool_node", tool_node)
    builder.add_node("compress_research", compress_research)
    builder.add_edge(START, "llm_call")
    builder.add_conditional_edges(
        "llm_call",
        should_continue,
        {"tool_node": "tool_node", "compress_research": "compress_research"},
    )
    builder.add_edge("tool_node", "llm_call")
    builder.add_edge("compress_research", END)
    return builder.compile(checkpointer=checkpointer)


# ===== SUPERVISOR NODES =====

def get_notes_from_tool_calls(messages: list) -> list[str]:
    """Extract compressed research notes from ToolMessages in supervisor history."""
    return [tool_msg.content for tool_msg in filter_messages(messages, include_types="tool")]


async def supervisor(state: SupervisorState) -> Command[Literal["supervisor_tools"]]:
    """Decide the next research topics (or completion) and delegate."""
    supervisor_messages = state.get("supervisor_messages", [])
    iteration = state.get("research_iterations", 0) + 1
    system_message = lead_researcher_prompt.format(
        date=get_today_str(),
        max_concurrent_research_units=get_settings().max_concurrent_researchers,
        max_researcher_iterations=get_settings().max_researcher_iterations,
    )
    await limiter.acquire()
    with timed(logger, "node=supervisor", iteration=iteration, messages=len(supervisor_messages)):
        response = await get_model("supervisor").bind_tools(
            [ConductResearch, ResearchComplete, think_tool]).ainvoke(
            [SystemMessage(content=system_message)] + supervisor_messages
        )
    tool_calls = [tc["name"] for tc in (response.tool_calls or [])]
    topics = [
        tc["args"].get("research_topic", "")
        for tc in (response.tool_calls or []) if tc["name"] == "ConductResearch"
    ]
    logger.info("node=supervisor iteration=%d tool_calls=%s topics=%d",
                iteration, tool_calls, len(topics))
    for topic in topics:
        logger.debug("node=supervisor delegate topic=%s", topic)
    return Command(
        goto="supervisor_tools",
        update={
            "supervisor_messages": [response],
            "research_iterations": iteration,
        },
    )


async def supervisor_tools(
    state: SupervisorState,
) -> Command[Literal["supervisor", "__end__"]]:
    """Execute supervisor tool calls: reflections + parallel research sub-agents."""
    supervisor_messages = state.get("supervisor_messages", [])
    research_iterations = state.get("research_iterations", 0)
    most_recent_message = supervisor_messages[-1]

    tool_messages: list[ToolMessage] = []
    all_raw_notes: list[str] = []
    next_step = "supervisor"
    should_end = False

    exceeded_iterations = research_iterations >= get_settings().max_researcher_iterations
    no_tool_calls = not most_recent_message.tool_calls
    research_complete = any(
        tool_call["name"] == "ResearchComplete"
        for tool_call in most_recent_message.tool_calls
    )

    if exceeded_iterations or no_tool_calls or research_complete:
        should_end = True
        next_step = END
        logger.info("node=supervisor_tools end=true reason=%s iteration=%d",
                    "max_iterations" if exceeded_iterations else
                    "research_complete" if research_complete else "no_tool_calls",
                    research_iterations)
    else:
        try:
            think_tool_calls = [
                tc for tc in most_recent_message.tool_calls if tc["name"] == "think_tool"
            ]
            conduct_research_calls = [
                tc for tc in most_recent_message.tool_calls if tc["name"] == "ConductResearch"
            ]
            logger.info("node=supervisor_tools iteration=%d reflections=%d research_calls=%d",
                        research_iterations, len(think_tool_calls), len(conduct_research_calls))

            for tool_call in think_tool_calls:
                observation = await think_tool.ainvoke(tool_call["args"])
                tool_messages.append(ToolMessage(
                    content=observation,
                    name=tool_call["name"],
                    tool_call_id=tool_call["id"],
                ))

            if conduct_research_calls:
                topics = [tc["args"]["research_topic"] for tc in conduct_research_calls]
                with timed(logger, "node=supervisor_tools fan_out", count=len(conduct_research_calls)):
                    tool_results = await asyncio.gather(*[
                        researcher_agent.ainvoke({
                            "researcher_messages": [
                                HumanMessage(content=tc["args"]["research_topic"])
                            ],
                            "research_topic": tc["args"]["research_topic"],
                        })
                        for tc in conduct_research_calls
                    ])
                logger.info("node=supervisor_tools research_done results=%d", len(tool_results))
                for topic, result in zip(topics, tool_results):
                    logger.info("node=supervisor_tools topic_done topic=%s compressed_chars=%d raw_notes=%d",
                                topic, len(str(result.get("compressed_research", ""))),
                                len(result.get("raw_notes", [])))

                tool_messages.extend([
                    ToolMessage(
                        content=result.get("compressed_research", "Error synthesizing research report"),
                        name=tool_call["name"],
                        tool_call_id=tool_call["id"],
                    )
                    for result, tool_call in zip(tool_results, conduct_research_calls)
                ])

                all_raw_notes = [
                    "\n".join(result.get("raw_notes", []))
                    for result in tool_results
                ]
        except Exception as e:  # never leak keys; surface in logs, end cleanly
            logger.exception("node=supervisor_tools error: %s", e)
            should_end = True
            next_step = END

    if should_end:
        notes = get_notes_from_tool_calls(supervisor_messages)
        logger.info("node=supervisor_tools exit notes=%d", len(notes))
        return Command(
            goto=next_step,
            update={
                "notes": notes,
                "research_brief": state.get("research_brief", ""),
            },
        )
    return Command(
        goto=next_step,
        update={"supervisor_messages": tool_messages, "raw_notes": all_raw_notes},
    )


def build_supervisor_agent(checkpointer=None):
    builder = StateGraph(SupervisorState)
    builder.add_node("supervisor", supervisor)
    builder.add_node("supervisor_tools", supervisor_tools)
    builder.add_edge(START, "supervisor")
    return builder.compile(checkpointer=checkpointer)


# ===== FINAL REPORT =====

async def final_report_generation(state: AgentState) -> dict:
    """Synthesize all supervisor notes into the final report."""
    notes = state.get("notes", [])
    final_report_prompt = final_report_generation_prompt.format(
        research_brief=state.get("research_brief", ""),
        findings="\n".join(notes),
        date=get_today_str(),
    )
    await limiter.acquire()
    with timed(logger, "node=final_report_generation", notes=len(notes),
               findings_chars=len("\n".join(notes))):
        final_report = await get_model("writer").ainvoke([HumanMessage(content=final_report_prompt)])
    logger.info("node=final_report_generation report_chars=%d", len(str(final_report.content or "")))
    return {
        "final_report": final_report.content,
        "messages": [AIMessage(content="Here is the final report: " + str(final_report.content))],
    }


def build_full_graph(checkpointer=None):
    builder = StateGraph(AgentState, input_schema=AgentInputState)
    builder.add_node("clarify_with_user", clarify_with_user)
    builder.add_node("write_research_brief", write_research_brief)
    builder.add_node("supervisor_subgraph", supervisor_agent)
    builder.add_node("final_report_generation", final_report_generation)
    builder.add_edge(START, "clarify_with_user")
    builder.add_edge("write_research_brief", "supervisor_subgraph")
    builder.add_edge("supervisor_subgraph", "final_report_generation")
    builder.add_edge("final_report_generation", END)
    return builder.compile(checkpointer=checkpointer)


# ===== COMPILED GRAPHS =====

researcher_agent = build_researcher_agent()
supervisor_agent = build_supervisor_agent()
scope_research = build_scope_graph(checkpointer=CHECKPOINTER)
agent = build_full_graph(checkpointer=CHECKPOINTER)

logger.info("graph=ready scope=1 researcher=1 supervisor=1 full=1 checkpointer=%s",
            type(CHECKPOINTER).__name__)


# ===== CONVENIENCE ORCHESTRATION (used by the API + CLI; keeps transport thin) =====

async def run_research(research_brief: str) -> dict:
    """Run supervisor research then final report. Returns {notes, iterations, final_report}."""
    logger.info("research=start brief_chars=%d brief=%s", len(research_brief or ""), research_brief)
    with timed(logger, "research=supervisor_agent", brief_chars=len(research_brief or "")):
        sup_result = await supervisor_agent.ainvoke({
            "research_brief": research_brief,
            "supervisor_messages": [HumanMessage(content=f"{research_brief}.")],
            "research_iterations": 0,
        })
    notes = sup_result.get("notes", [])
    logger.info("research=supervisor_done iterations=%d notes=%d",
                sup_result.get("research_iterations", 0), len(notes))
    with timed(logger, "research=final_report", notes=len(notes)):
        final = await final_report_generation({
            "research_brief": research_brief,
            "notes": notes,
            "messages": [],
        })
    report_chars = len(str(final.get("final_report", "")))
    logger.info("research=done iterations=%d notes=%d report_chars=%d",
                sup_result.get("research_iterations", 0), len(notes), report_chars)
    return {
        "notes": notes,
        "iterations": sup_result.get("research_iterations", 0),
        "final_report": final.get("final_report", ""),
    }
