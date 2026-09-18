from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote_plus
import re

import httpx


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str


def search_web(query: str, *, max_results: int = 5, timeout: float = 15.0) -> list[dict[str, str]]:
    """Search the public web through DuckDuckGo's HTML endpoint.

    This is intentionally read-only: results are returned to the agent and no
    web page is executed or used to modify the project.
    """
    query = query.strip()
    if not query:
        raise ValueError("query must not be empty")
    if max_results < 1 or max_results > 20:
        raise ValueError("max_results must be between 1 and 20")

    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    response = httpx.get(
        url,
        headers={"User-Agent": "KardecAgent/0.1 (+local coding agent)"},
        timeout=timeout,
        follow_redirects=True,
    )
    response.raise_for_status()
    html = response.text

    results: list[dict[str, str]] = []
    blocks = re.findall(r'<div[^>]+class="[^"]*result[^"]*"[^>]*>(.*?)</div>\s*</div>', html, re.S | re.I)
    for block in blocks:
        link = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S | re.I)
        snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</a?>', block, re.S | re.I)
        if not link:
            continue
        clean = lambda value: re.sub(r"<[^>]+>", "", value).strip()
        result = WebResult(clean(link.group(2)), link.group(1), clean(snippet.group(1)) if snippet else "")
        results.append(result.__dict__)
        if len(results) >= max_results:
            break

    return results
