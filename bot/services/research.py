"""
Lightweight research helper (Tavily).

Optional feature: lets the admin pull a quick, current summary of how platforms
detect duplicate / reposted content, so the editing recipes can be kept relevant.
Fails soft — if no API key is set, the feature politely reports it's disabled.
"""

from __future__ import annotations

import logging

import aiohttp

from ..config import settings

log = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"
DEFAULT_QUERY = (
    "latest methods social media platforms use to detect duplicate "
    "or reposted short-form video content 2026"
)


async def research(query: str = DEFAULT_QUERY) -> str:
    """Return a short markdown summary, or a friendly disabled/error message."""
    if not settings.tavily_api_key:
        return "ℹ️ Research is disabled — set TAVILY_API_KEY in .env to enable it."

    payload = {
        "api_key": settings.tavily_api_key,
        "query": query,
        "search_depth": "basic",
        "max_results": 5,
        "include_answer": True,
    }
    try:
        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(TAVILY_URL, json=payload) as resp:
                if resp.status != 200:
                    return f"⚠️ Research API returned HTTP {resp.status}."
                data = await resp.json()
    except Exception as exc:
        log.warning("research failed: %s", exc)
        return "⚠️ Research request failed (network/timeout)."

    answer = (data.get("answer") or "").strip()
    results = data.get("results", [])[:3]

    lines = ["🔎 *Research summary*"]
    if answer:
        lines.append(f"\n{answer}")
    if results:
        lines.append("\n*Sources:*")
        for r in results:
            title = r.get("title", "source")
            url = r.get("url", "")
            lines.append(f"• [{title}]({url})")
    return "\n".join(lines) if len(lines) > 1 else "No results found."
