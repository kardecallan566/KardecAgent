from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse
import html
import re
import socket

import httpx


MAX_PAGE_CHARS = 30_000
MAX_REDIRECTS = 3
BLOCKED_SCHEMES = {"file", "data", "javascript", "vbscript"}
BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "metadata.google.internal"}
PRIVATE_HOST_SUFFIXES = (".local", ".internal", ".localhost")
BLOCKED_METADATA_IPS = {"169.254.169.254", "100.100.100.200"}


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class WebPage:
    url: str
    final_url: str
    title: str
    text: str
    truncated: bool
    untrusted_content: bool = True


def _clean(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", value)).replace("\xa0", " ").strip()


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme.lower() in BLOCKED_SCHEMES or parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Only HTTP and HTTPS URLs are allowed.")
    if parsed.username is not None or parsed.password is not None:
        raise PermissionError("URLs containing embedded credentials are blocked.")
    if not parsed.hostname:
        raise ValueError("URL has no hostname.")
    host = parsed.hostname.lower().rstrip(".")
    if host in BLOCKED_HOSTNAMES or host.endswith(PRIVATE_HOST_SUFFIXES):
        raise PermissionError("Private/local web destinations are blocked.")

    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Could not resolve web destination.") from exc

    for item in addresses:
        address = ip_address(item[4][0])
        if str(address) in BLOCKED_METADATA_IPS or address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified:
            raise PermissionError("Web destination resolves to a private or reserved address.")


def _safe_search_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if parsed.hostname and parsed.hostname.endswith("duckduckgo.com"):
        # DuckDuckGo result links can contain a redirect target in uddg.
        target = parse_qs(parsed.query).get("uddg", [None])[0]
        if target:
            return unquote(target)
    return raw_url


def search_web(query: str, *, max_results: int = 5, timeout: float = 15.0) -> list[dict[str, str]]:
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
    results: list[dict[str, str]] = []
    blocks = re.findall(r'<div[^>]+class="[^"]*result[^"]*"[^>]*>(.*?)</div>\s*</div>', response.text, re.S | re.I)
    for block in blocks:
        link = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S | re.I)
        snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</a?>', block, re.S | re.I)
        if not link:
            continue
        title = _clean(link.group(2))
        result_url = _safe_search_url(html.unescape(link.group(1)))
        results.append(WebResult(title, result_url, _clean(snippet.group(1)) if snippet else "").__dict__)
        if len(results) >= max_results:
            break
    return results


def fetch_web_page(url: str, *, timeout: float = 15.0, max_chars: int = MAX_PAGE_CHARS) -> dict[str, object]:
    """Fetch text from a public HTTP(S) page without executing page content.

    Security boundary:
    - blocks non-HTTP schemes and local/private destinations;
    - validates every redirect destination;
    - never executes JavaScript or downloads active content;
    - limits redirects, response size and extracted text.
    """
    if max_chars < 1 or max_chars > MAX_PAGE_CHARS:
        raise ValueError(f"max_chars must be between 1 and {MAX_PAGE_CHARS}")
    _validate_url(url)

    current = url
    headers = {
        "User-Agent": "KardecAgent/0.1 (+local coding agent)",
        "Accept": "text/html,text/plain,application/xhtml+xml;q=0.9",
    }
    with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
        for _ in range(MAX_REDIRECTS + 1):
            _validate_url(current)
            response = client.get(current)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("Redirect response has no location.")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            break
        else:
            raise ValueError("Too many web redirects.")

    content_type = response.headers.get("content-type", "").lower()
    if not any(kind in content_type for kind in ("text/html", "text/plain", "application/xhtml+xml")):
        raise PermissionError("Only HTML/text web pages are allowed.")
    raw = response.text
    if len(raw) > max_chars * 4:
        raw = raw[:max_chars * 4]

    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
    title = _clean(title_match.group(1)) if title_match else current
    text_content = re.sub(r"<(script|style|noscript|iframe|object|embed|svg)[^>]*>.*?</\1>", " ", raw, flags=re.I | re.S)
    text_content = re.sub(r"<[^>]+>", " ", text_content)
    text_content = re.sub(r"\s+", " ", html.unescape(text_content)).strip()
    truncated = len(text_content) > max_chars
    text_content = text_content[:max_chars]

    return WebPage(url, current, title, text_content, truncated, True).__dict__
