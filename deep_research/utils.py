
"""Research Utilities and Tools.

This module provides search and content processing utilities for the research agent,
including web search capabilities and content summarization tools.

All network I/O is asynchronous (AsyncTavilyClient + ainvoke) so it never blocks
the API event loop. Models are resolved through models.init_model(role).
"""

import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing_extensions import Annotated, List, Literal

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool, InjectedToolArg
from tavily import AsyncTavilyClient

from .log_config import kv, timed
from .rate_limit import limiter
from .state_research import Summary
from .prompts import summarize_webpage_prompt
from .models import init_model

logger = logging.getLogger("deep_research.utils")

# ===== UTILITY FUNCTIONS =====

def get_today_str() -> str:
    """Get current date in a human-readable format."""
    return datetime.now().strftime("%a %b %-d, %Y")

def get_current_dir() -> Path:
    """Get the current directory of the module.

    This function is compatible with Jupyter notebooks and regular Python scripts.

    Returns:
        Path object representing the current directory
    """
    try:
        return Path(__file__).resolve().parent
    except NameError:  # __file__ is not defined
        return Path.cwd()

# ===== CONFIGURATION =====

summarization_model = init_model("summary")
tavily_client = AsyncTavilyClient()

# ===== SEARCH FUNCTIONS =====

async def tavily_search_multiple(
    search_queries: List[str],
    max_results: int = 3,
    topic: Literal["general", "news", "finance"] = "general",
    include_raw_content: bool = True,
) -> List[dict]:
    """Perform search using Tavily API for multiple queries in parallel.

    Args:
        search_queries: List of search queries to execute
        max_results: Maximum number of results per query
        topic: Topic filter for search results
        include_raw_content: Whether to include raw webpage content

    Returns:
        List of search result dictionaries (input order preserved)
    """
    logger.debug("tool=tavily_search queries=%d max_results=%d topic=%s raw=%s",
                 len(search_queries), max_results, topic, include_raw_content)
    with timed(logger, "tool=tavily_search_multiple", queries=len(search_queries), topic=topic):
        responses = list(await asyncio.gather(*[
            tavily_client.search(
                query,
                max_results=max_results,
                include_raw_content=include_raw_content,
                topic=topic,
            )
            for query in search_queries
        ]))
    logger.info("tool=tavily_search_multiple responses=%s",
                [len(r.get("results", [])) for r in responses])
    return responses

async def summarize_webpage_content(webpage_content: str) -> str:
    """Summarize webpage content using the configured summarization model.

    Args:
        webpage_content: Raw webpage content to summarize

    Returns:
        Formatted summary with key excerpts
    """
    try:
        # Set up structured output model for summarization
        structured_model = summarization_model.with_structured_output(Summary)

        # Generate summary
        await limiter.acquire()
        with timed(logger, "tool=summarize_webpage", content_chars=len(webpage_content)):
            summary = await structured_model.ainvoke([
                HumanMessage(content=summarize_webpage_prompt.format(
                    webpage_content=webpage_content,
                    date=get_today_str()
                ))
            ])

        # Format summary with clear structure
        formatted_summary = (
            f"<summary>\n{summary.summary}\n</summary>\n\n"
            f"<key_excerpts>\n{summary.key_excerpts}\n</key_excerpts>"
        )
        logger.debug("tool=summarize_webpage summary_chars=%d", len(formatted_summary))
        return formatted_summary

    except Exception as e:
        logger.warning("tool=summarize_webpage fallback content_chars=%d error=%s",
                       len(webpage_content), e)
        return webpage_content[:1000] + "..." if len(webpage_content) > 1000 else webpage_content

def deduplicate_search_results(search_results: List[dict]) -> dict:
    """Deduplicate search results by URL to avoid processing duplicate content.

    Args:
        search_results: List of search result dictionaries

    Returns:
        Dictionary mapping URLs to unique results
    """
    unique_results = {}

    for response in search_results:
        for result in response.get("results", []):
            url = result.get("url")
            if url and url not in unique_results:
                unique_results[url] = result

    return unique_results

async def process_search_results(unique_results: dict) -> dict:
    """Process search results by summarizing content where available.

    Args:
        unique_results: Dictionary of unique search results

    Returns:
        Dictionary of processed results with summaries
    """
    summarized_results = {}
    summarized = 0

    for url, result in unique_results.items():
        # Use existing content if no raw content for summarization
        if not result.get("raw_content"):
            content = result["content"]
        else:
            # Summarize raw content for better processing
            content = await summarize_webpage_content(result["raw_content"])
            summarized += 1

        summarized_results[url] = {
            'title': result['title'],
            'content': content
        }

    logger.info("tool=process_search_results unique=%d summarized=%d passthrough=%d",
                len(unique_results), summarized, len(unique_results) - summarized)
    return summarized_results

def format_search_output(summarized_results: dict) -> str:
    """Format search results into a well-structured string output.

    Args:
        summarized_results: Dictionary of processed search results

    Returns:
        Formatted string of search results with clear source separation
    """
    if not summarized_results:
        return "No valid search results found. Please try different search queries or use a different search API."

    formatted_output = "Search results: \n\n"

    for i, (url, result) in enumerate(summarized_results.items(), 1):
        formatted_output += f"\n\n--- SOURCE {i}: {result['title']} ---\n"
        formatted_output += f"URL: {url}\n\n"
        formatted_output += f"SUMMARY:\n{result['content']}\n\n"
        formatted_output += "-" * 80 + "\n"

    return formatted_output

# ===== RESEARCH TOOLS =====

@tool(parse_docstring=True)
async def tavily_search(
    query: str,
    max_results: Annotated[int, InjectedToolArg] = 3,
    topic: Annotated[Literal["general", "news", "finance"], InjectedToolArg] = "general",
) -> str:
    """Fetch results from Tavily search API with content summarization.

    Args:
        query: A single search query to execute
        max_results: Maximum number of results to return
        topic: Topic to filter results by ('general', 'news', 'finance')

    Returns:
        Formatted string of search results with summaries
    """
    # Execute search for single query
    search_results = await tavily_search_multiple(
        [query],  # Convert single query to list for the internal function
        max_results=max_results,
        topic=topic,
        include_raw_content=True,
    )

    # Deduplicate results by URL to avoid processing duplicate content
    unique_results = deduplicate_search_results(search_results)

    # Process results with summarization
    summarized_results = await process_search_results(unique_results)

    # Format output for consumption
    output = format_search_output(summarized_results)
    logger.info("tool=tavily_search done query=%s sources=%d output_chars=%d",
                query, len(summarized_results), len(output))
    return output

@tool(parse_docstring=True)
def think_tool(reflection: str) -> str:
    """Tool for strategic reflection on research progress and decision-making.

    Use this tool after each search to analyze results and plan next steps systematically.
    This creates a deliberate pause in the research workflow for quality decision-making.

    When to use:
    - After receiving search results: What key information did I find?
    - Before deciding next steps: Do I have enough to answer comprehensively?
    - When assessing research gaps: What specific information am I still missing?
    - Before concluding research: Can I provide a complete answer now?

    Reflection should address:
    1. Analysis of current findings - What concrete information have I gathered?
    2. Gap assessment - What crucial information is still missing?
    3. Quality evaluation - Do I have sufficient evidence/examples for a good answer?
    4. Strategic decision - Should I continue searching or provide my answer?

    Args:
        reflection: Your detailed reflection on research progress, findings, gaps, and next steps

    Returns:
        Confirmation that reflection was recorded for decision-making
    """
    logger.debug("tool=think_tool reflection_chars=%d", len(reflection or ""))
    return f"Reflection recorded: {reflection}"
