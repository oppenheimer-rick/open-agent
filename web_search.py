#!/usr/bin/env python3
"""
Zero-latency, zero-error web search for 61k-context agents.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Architecture (5-tier sequential fallback, never parallel):
  Tier 1:  ddgs (DuckDuckGo API, 9k+ ⭐)  — works, handles VQD/rate limits
  Tier 2:  DuckDuckGo Lite (direct scrape) — fresh httpx client to avoid rate-limit
  Tier 3:  Wikipedia API                  — never-fail for topical/factual queries
  Tier 4:  DuckDuckGo HTML (direct)       — last-resort fallback
  Tier 5:  SearXNG localhost              — single simple call, no retries

Cache:   LRU with 5-min TTL — repeated queries instant. FAILURES NOT CACHED.
Safety:  Input sanitized, URLs validated, output limited.
Deps:    pip install ddgs beautifulsoup4 httpx
"""

import re
import html
import time
from datetime import datetime
from urllib.parse import urlparse, unquote, quote
from collections import OrderedDict
from html.parser import HTMLParser

# ── Constants ────────────────────────────────────────────────────────────────

DDGS_TIMEOUT = 5.0  # ddgs library timeout (DDG can be slow for news)
LITE_TIMEOUT = 2.0  # DuckDuckGo Lite timeout
WIKI_TIMEOUT = 2.0  # Wikipedia API timeout
SEARXNG_TIMEOUT = 2.0  # SearXNG timeout
DDG_HTML_TIMEOUT = 4.0  # DDG HTML scrape timeout (needs 2 requests: homepage VQD + search)

# ── Persistente DDGS instance (VQD token cached across calls) ─────────────

_DDGS_INSTANCE = None


def _get_ddgs():
    global _DDGS_INSTANCE
    if _DDGS_INSTANCE is None:
        from ddgs import DDGS
        _DDGS_INSTANCE = DDGS(timeout=DDGS_TIMEOUT)
    return _DDGS_INSTANCE


# ── Input Safety ─────────────────────────────────────────────────────────────


def _sanitize_query(query: str) -> str:
    """Sanitize search query: limit length, strip control chars, prevent injection."""
    # Strip non-printable control characters
    cleaned = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', query)
    # Limit to a reasonable length
    cleaned = cleaned.strip()[:200]
    return cleaned


def _validate_url(url: str) -> bool:
    """Validate URL is safe (only http/https, no data: or javascript: URIs)."""
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url)
    return parsed.scheme in ('http', 'https') and bool(parsed.netloc)


# ── LRU Cache (zero-latency for repeated queries, NO STOP caching) ──────────


class LRUCache:
    """LRU cache with TTL. Max 32 entries, 5 min TTL. Never caches empty/STOP results."""

    def __init__(self, maxsize: int = 32, ttl: int = 300):
        self._maxsize = maxsize
        self._ttl = ttl
        self._cache: OrderedDict = OrderedDict()
        self._timestamps: dict = {}

    def get(self, key: str):
        if key not in self._cache:
            return None
        if time.monotonic() - self._timestamps.get(key, 0) > self._ttl:
            self._cache.pop(key, None)
            self._timestamps.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def set(self, key: str, value: str):
        # NEVER cache STOP or ERROR messages — let the agent retry in a new session
        if value.startswith("STOP:") or value.startswith("ERROR:"):
            return
        self._cache[key] = value
        self._timestamps[key] = time.monotonic()
        if len(self._cache) > self._maxsize:
            oldest = next(iter(self._cache))
            self._cache.pop(oldest, None)
            self._timestamps.pop(oldest, None)


_search_cache = LRUCache()


# ── Tier 1: ddgs (DuckDuckGo API — PRIMARY) ────────────────────────────────


def _ddgs_search(query: str, max_results: int) -> list[dict] | None:
    """Primary search via persistent ddgs instance. Proven reliable."""
    try:
        ddgs = _get_ddgs()
        raw = list(ddgs.text(query, max_results=max_results))

        results = []
        for r in raw:
            title = (r.get("title") or "").strip()
            url = (r.get("href") or "").strip()
            body = (r.get("body") or "").strip()
            if title and url and _validate_url(url):
                results.append({"title": title, "url": url, "content": body})
        return results if results else None
    except Exception:
        return None


# ── Tier 2: DuckDuckGo Lite (fresh httpx client per call) ──────────────────


def _extract_url(redirect_url: str) -> str:
    """Extract actual URL from DuckDuckGo's redirect wrapper."""
    if "uddg=" in redirect_url:
        from urllib.parse import parse_qs
        parsed = urlparse(redirect_url.replace("//", "https://", 1) if redirect_url.startswith("//") else redirect_url)
        qs = parse_qs(parsed.query)
        if "uddg" in qs:
            return unquote(qs["uddg"][0])
    return redirect_url


def _lite_search(query: str, max_results: int) -> list[dict] | None:
    """
    DuckDuckGo Lite — fresh httpx client per call to avoid rate-limiting cookies.
    The persistent primp client was getting 202 (challenge page).
    httpx with a fresh UA handle avoids the challenge page.
    """
    import httpx
    from bs4 import BeautifulSoup

    try:
        # Use fresh client per call to avoid accumulated cookies/state
        with httpx.Client(timeout=LITE_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query},
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.5",
                },
            )

            # Lite returns 200 or 202 (redirect is OK, page still has links)
            if resp.status_code not in (200, 202):
                return None

            soup = BeautifulSoup(resp.text, "html.parser")
            # Lite uses a table layout, look for all <a> with href containing http
            links = soup.select("a[href*=http]")
            snippets = soup.select("td.snippet, .result-snippet")

            results = []
            for i, a in enumerate(links[:max_results]):
                title = a.get_text(strip=True)
                href = str(a.get("href") or "")
                url = _extract_url(href)
                if not _validate_url(url):
                    continue
                snippet = ""
                if i < len(snippets):
                    snippet = snippets[i].get_text(strip=True)
                if title and url:
                    results.append({"title": title, "url": url, "content": snippet})
            return results if results else None
    except Exception:
        return None


# ── Tier 3: Wikipedia API (never-fail fallback for factual/topical queries) ─


def _wiki_search(query: str, max_results: int) -> list[dict] | None:
    """
    Wikipedia search API. Always returns something for most queries.
    Free, no API key, no rate limits for reasonable usage.
    """
    import httpx

    try:
        resp = httpx.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": max_results,
                "srprop": "snippet",
            },
            headers={"User-Agent": "HermesAgent/1.0 (agentic-search)"},
            timeout=WIKI_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get("query", {}).get("search", []):
            title = item.get("title", "").strip()
            snippet = re.sub(r"<[^>]+>", "", item.get("snippet", "")).strip()
            # Construct Wikipedia URL
            url = f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
            if title and _validate_url(url):
                results.append({"title": title, "url": url, "content": snippet})
        return results if results else None
    except Exception:
        return None


# ── Tier 4: Direct DuckDuckGo HTML scrape (last-resort DDG fallback) ────────


def _ddg_html_search(query: str, max_results: int) -> list[dict] | None:
    """
    Direct DuckDuckGo HTML search. Uses the main duckduckgo.com search page.
    This is the most basic DDG endpoint — harder to block but returns HTML.
    """
    import httpx
    from bs4 import BeautifulSoup

    try:
        # First get a VQD token from the homepage
        with httpx.Client(timeout=DDG_HTML_TIMEOUT, follow_redirects=True) as client:
            home_resp = client.get(
                "https://duckduckgo.com/",
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                },
            )

            # Parse VQD token from the page
            vqd_match = re.search(r'vqd=([\w-]+)', home_resp.text)
            vqd = vqd_match.group(1) if vqd_match else ""

            if not vqd:
                # Fall back to the old endpoint — POST with form data
                resp = client.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": query},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            else:
                resp = client.get(
                    "https://duckduckgo.com/",
                    params={"q": query, "vqd": vqd, "ia": "web"},
                )

            soup = BeautifulSoup(resp.text, "html.parser")

            # Try multiple result selectors (different DDG HTML layouts)
            results = []
            for article in soup.select("article, .result, .web-result")[:max_results]:
                link = article.select_one("a[href]")
                if not link:
                    continue
                url = link.get("href", "")
                title = link.get_text(strip=True)
                snippet_el = article.select_one(".snippet, .result-snippet, .content")
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                if url and title:
                    url = _extract_url(url)
                    if not _validate_url(url):
                        continue
                    results.append({"title": title, "url": url, "content": snippet})

            return results if results else None
    except Exception:
        return None


# ── Tier 5: SearXNG (single call, simple message) ─────────────────────────


def _searxng_search(query: str, max_results: int) -> list[dict] | None:
    """Search local SearXNG. Single call, 2s timeout, no retries. Simple."""
    import httpx

    try:
        with httpx.Client(timeout=SEARXNG_TIMEOUT, follow_redirects=True) as client:
            r = client.get(
                "http://localhost:8081/search",
                params={"q": query, "format": "json", "language": "en-US"},
                headers={"User-Agent": "HermesAgent/1.0"},
            )

            # If JSON fails, try HTML
            if r.status_code in (403, 404, 406):
                r = client.get(
                    "http://localhost:8081/search",
                    params={"q": query, "language": "en-US"},
                )
                r.raise_for_status()
                # Simple HTML parsing — no complex parser
                from html.parser import HTMLParser

                class SimpleParser(HTMLParser):
                    def __init__(self):
                        super().__init__(convert_charrefs=True)
                        self.results = []
                        self._current = None
                        self._capture = None

                    def handle_starttag(self, tag, attrs):
                        attrs = dict(attrs)
                        classes = str(attrs.get("class", "") or "")
                        if tag == "article" and "result" in classes:
                            self._current = {"title": "", "url": "", "content": ""}
                        elif self._current and tag == "h3" and self._current:
                            self._capture = "title"
                            for k, v in attrs:
                                if k == "href" and not self._current["url"]:
                                    self._current["url"] = v
                        elif self._current and tag == "p" and "content" in str(classes or ""):
                            self._capture = "content"

                    def handle_endtag(self, tag):
                        if tag == "article" and self._current:
                            if self._current.get("title") and self._current.get("url"):
                                self.results.append(self._current)
                            self._current = None
                        elif self._capture and tag in ("h3", "p"):
                            self._capture = None

                    def handle_data(self, data):
                        if self._current and self._capture:
                            self._current[self._capture] += data

                parser = SimpleParser()
                parser.feed(r.text)
                parsed = parser.results[:max_results]
                for res in parsed:
                    res["content"] = html.unescape(res.get("content", ""))
                return parsed if parsed else None

            r.raise_for_status()
            data = r.json()
            results = []
            for res in data.get("results", [])[:max_results]:
                url = res.get("url", "")
                title = html.unescape(res.get("title") or "").strip()
                content = html.unescape(res.get("content") or "").strip()
                if url and title and _validate_url(url):
                    results.append({"title": title, "url": url, "content": content})
            return results if results else None
    except Exception:
        return None


def _playwright_search(query: str, max_results: int) -> list[dict] | None:
    """Fallback search using Playwright browser to fetch and render Yahoo Search."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="en-US"
            )
            page = context.new_page()
            page.add_init_script("delete Object.getPrototypeOf(navigator).webdriver")
            
            # Speed optimization: block images, fonts, and media
            def block_resources(route):
                if route.request.resource_type in ["image", "font", "media"]:
                    route.abort()
                else:
                    route.continue_()
            page.route("**/*", block_resources)
            page.set_default_timeout(10000)
            
            # Go directly to Yahoo search results page
            search_url = f"https://search.yahoo.com/search?q={quote(query)}"
            page.goto(search_url, wait_until="domcontentloaded")
            
            html_content = page.content()
            browser.close()
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, "html.parser")
            
            results = []
            for algo in soup.select(".algo")[:max_results]:
                link = algo.select_one("a[href]")
                title_el = algo.select_one("h3")
                snippet_el = algo.select_one(".compText, .compText ~ div")
                
                if link and title_el:
                    url = link.get("href", "")
                    title = title_el.get_text(strip=True)
                    snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                    
                    if url and _validate_url(url):
                        results.append({"title": title, "url": url, "content": snippet})
            return results if results else None
    except Exception:
        return None


# ── Background Warmup (ddgs only — Lite/wikis need no warmup) ──────────────

_warmed = False


def _warmup():
    global _warmed
    if _warmed:
        return
    _warmed = True
    try:
        ddgs = _get_ddgs()
        next(iter(ddgs.text("warmup", max_results=1)), None)
    except Exception:
        pass


import threading
_threading_warmup = threading.Thread(target=_warmup, daemon=True)
_threading_warmup.start()


# ── Formatter ────────────────────────────────────────────────────────────────


def _score_result(res: dict, query: str) -> int:
    """Quick relevance score for dedup and ranking."""
    title = res.get("title", "").lower()
    content = res.get("content", "").lower()
    host = urlparse(res.get("url", "")).netloc.lower()
    qwords = set(re.findall(r"[a-z0-9_]{3,}", query.lower()))
    score = sum(3 for w in qwords if w in title) + sum(1 for w in qwords if w in content)
    if any(
        host.endswith(h)
        for h in ("github.com", "developer.mozilla.org", "docs.python.org", "pypi.org", "npmjs.com", "wikipedia.org")
    ):
        score += 6
    return score


def _format_results(query: str, results: list[dict]) -> str:
    """Compact context-efficient output. Dedup by URL, score-rank, cap at 3."""
    seen: set[str] = set()
    unique = []
    for r in results:
        u = r.get("url", "")
        if u and u not in seen:
            seen.add(u)
            r["score"] = _score_result(r, query)
            unique.append(r)
    unique.sort(key=lambda x: x.get("score", 0), reverse=True)
    unique = unique[:3]

    # Safety: strip any HTML from output
    lines = [f"SEARCH: {query}", f"DATE: {datetime.now().date().isoformat()}", ""]
    for i, r in enumerate(unique, 1):
        snippet = re.sub(r"\s+", " ", r.get("content", "")).strip()
        # Strip HTML tags from snippet
        snippet = re.sub(r"<[^>]+>", "", snippet)
        lines.append(f"{i}. {html.unescape(r.get('title', '(no title)'))}")
        lines.append(f"   URL: {r.get('url', '')}")
        if snippet:
            lines.append(f"   {html.unescape(snippet[:120])}")
        lines.append("")
    return "\n".join(lines)


# ── Public API ───────────────────────────────────────────────────────────────


def search_web(query: str, max_results: int = 3, current: bool = True) -> str:
    """
    Web search — 5-tier sequential fallback with LRU cache and zero error leakage.

    Tier 1:  ddgs (DuckDuckGo API — 9k+ ⭐, handles VQD + rate limits)
    Tier 2:  DuckDuckGo Lite (direct scrape, fresh client per call)
    Tier 3:  Wikipedia API (never-fail for factual queries)
    Tier 4:  DuckDuckGo HTML (deep fallback with VQD token)
    Tier 5:  SearXNG localhost (single simple call)

    Cache:   Same query within 5 min returns instantly. FAILURES NOT CACHED.
    Safety:  Input sanitized, URLs validated, HTML stripped from output.
    Errors:  All caught. Returns "STOP: …" on systematic failure.
    """
    # Sanitize input
    query = _sanitize_query(query)
    if not query or not query.strip():
        return "STOP: Empty query after sanitization."

    # Check cache first (zero latency) — only successful results cached
    cache_key = f"{query}:{max_results}"
    cached = _search_cache.get(cache_key)
    if cached is not None:
        return cached

    # Run ALL tiers and collect ALL results — merge for source diversity
    tiers = [
        ("ddgs", _ddgs_search),
        ("Lite", _lite_search),
        ("SearXNG", _searxng_search),
        ("DDG HTML", _ddg_html_search),
        ("Playwright Search", _playwright_search),
        ("Wiki", _wiki_search),
    ]

    all_results = []
    used_tiers = []

    for name, func in tiers:
        try:
            results = func(query, max_results)
            if results and isinstance(results, list):
                all_results.extend(results)
                used_tiers.append(name)
                break # Stop searching subsequent engines to optimize search latency
        except Exception:
            continue

    if not all_results:
        # Don't cache failures — allow retry later
        return (
            "STOP: All search engines returned empty (tried ddgs, DuckDuckGo Lite, "
            "Wikipedia, DuckDuckGo HTML, SearXNG). "
            "Search is currently unavailable. Answer from your existing knowledge."
        )

    # Deduplicate by URL (keep first occurrence — earlier tiers win)
    seen = set()
    deduped = []
    for r in all_results:
        url = str(r.get("url", "")).rstrip("/")
        if url and url not in seen:
            seen.add(url)
            deduped.append(r)

    # Score for source diversity: Wikipedia lower priority, news sites higher
    def _score(r):
        url = str(r.get("url", ""))
        title = str(r.get("title", ""))
        score = 0
        if "wikipedia.org" in url:
            score -= 1
        if "news" in url or "news" in title.lower():
            score += 1
        if "blog" in url or "blog" in title.lower():
            score += 0.5
        return score

    # Sort by diversity score, then keep original order for ties
    deduped.sort(key=lambda r: _score(r), reverse=True)

    # Truncate to max_results, but keep at least 1
    merged = deduped[:max(max_results, 1)]

    # If all results are from Wikipedia, add a note
    is_all_wiki = all("wikipedia.org" in str(r.get("url", "")) for r in merged)

    result = _format_results(query, merged)

    if is_all_wiki:
        result += (
            "\nNOTE: Results are all from Wikipedia (primary search engines did not respond). "
            "This may not reflect current news."
        )

    _search_cache.set(cache_key, result)
    return result


# ── Web Fetch ────────────────────────────────────────────────────────────────


def _spa_blocked(url: str) -> bool:
    """Known JS-heavy domains that resist scraping."""
    blocked = [
        "substack.com", "medium.com", "fortune.com", "bloomberg.com",
        "nytimes.com", "theatlantic.com", "reuters.com",
    ]
    return any(d in url for d in blocked)


def web_fetch(url: str, query: str = "") -> str:
    """Fetch a URL and return compact text content. Returns 'ERROR: …' on any error."""
    is_raw = any(x in url for x in [
        "raw.githubusercontent.com", "gist.githubusercontent.com",
        ".txt", ".py", ".js", ".md",
    ])

    # If it is a known JS-heavy / protected site, try Playwright directly (unless raw)
    if _spa_blocked(url) and not is_raw:
        pw_res = browse_web(url, action="scrape")
        if not pw_res.startswith("STOP:"):
            return f"--- PAGE (Rendered): {url} ---\n{pw_res[:4000]}\n--- END ---"
        # If Playwright failed, log and we will still try HTTPX as fallback

    import httpx
    from bs4 import BeautifulSoup

    try:
        if "github.com" in url and "/blob/" in url:
            url = url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")

        resp = httpx.get(
            url, timeout=5.0, follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            },
        )
        resp.raise_for_status()
        text = resp.text

        if is_raw:
            return text[:4000]

        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.extract()

        clean = soup.get_text(separator="\n")
        clean = "\n".join(
            chunk.strip()
            for line in clean.splitlines()
            for chunk in line.split("  ")
            if chunk.strip()
        )

        if not clean.strip():
            # Clean text is empty, try Playwright fallback
            pw_res = browse_web(url, action="scrape")
            if not pw_res.startswith("STOP:"):
                return f"--- PAGE (Rendered): {url} ---\n{pw_res[:4000]}\n--- END ---"
            return "ERROR: Page appears to be empty or JS-rendered."

        return f"--- PAGE: {url} ---\n{clean[:4000]}\n--- END ---"

    except Exception as e:
        # Fallback to Playwright if HTTPX failed (except for raw text URLs)
        if not is_raw:
            pw_res = browse_web(url, action="scrape")
            if not pw_res.startswith("STOP:"):
                return f"--- PAGE (Rendered): {url} ---\n{pw_res[:4000]}\n--- END ---"
        
        # Determine actual error reason for error message
        if isinstance(e, httpx.HTTPStatusError):
            return f"ERROR: HTTP {e.response.status_code} — cannot fetch {url}."
        elif isinstance(e, httpx.ConnectError):
            return f"ERROR: Cannot reach {urlparse(url).netloc} (connection error)."
        else:
            return f"ERROR: Failed to fetch URL: {e}."


# ── RAG helper ───────────────────────────────────────────────────────────────


def simple_rag(text: str, query: str, chunk_size: int = 1000, top_k: int = 3) -> str:
    """Lightweight keyword RAG — split text, score chunks by query term frequency."""
    if not query:
        return text[: chunk_size * top_k]
    from collections import Counter

    chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
    if not chunks:
        return ""

    q_words = set(re.findall(r"\w+", query.lower()))
    if not q_words:
        return "\n...\n".join(chunks[:top_k])

    scored = []
    for i, chunk in enumerate(chunks):
        c_words = re.findall(r"\w+", chunk.lower())
        c_counts = Counter(c_words)
        score = sum(c_counts.get(qw, 0) for qw in q_words) + 1.0 / (i + 1)
        scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    return "\n...[RAG]...\n".join(c for _, c in scored[:top_k])


# ── Second Brain ─────────────────────────────────────────────────────────────


def _append_to_second_brain(url: str, title: str, content: str):
    try:
        with open("second_brain.md", "a", encoding="utf-8") as f:
            f.write(
                f"\n\n## {title.strip().replace(chr(10), ' ')}\n"
                f"**URL:** {url}\n"
                f"**Date:** {datetime.now().date().isoformat()}\n\n{content}\n---\n"
            )
    except Exception:
        pass


def search_second_brain(query: str, top_k: int = 5) -> str:
    """Search locally cached knowledge base."""
    try:
        with open("second_brain.md", "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            return "Second brain is empty."
        return simple_rag(content, query, chunk_size=1500, top_k=top_k)
    except FileNotFoundError:
        return "Second brain is empty."


# ── Scout Website ────────────────────────────────────────────────────────────


def scout_website(url: str, depth: int = 1) -> str:
    """Fetch a URL, extract top internal links, fetch those too."""
    base = web_fetch(url)
    if base.startswith("STOP") or base.startswith("ERROR"):
        return base

    links = re.findall(r'href=[\'"]?([^\'">]+)', base)
    parsed = urlparse(url)
    base_domain = f"{parsed.scheme}://{parsed.netloc}"

    valid = []
    for l in links:
        if l.startswith("http") and parsed.netloc in l:
            valid.append(l)
        elif l.startswith("/"):
            valid.append(f"{base_domain}{l}")

    valid = list(dict.fromkeys(valid))[:3]
    if not valid:
        return base

    parts = [base, "\n--- SCOUTED LINKS ---"]
    for link in valid:
        parts.append(f"\n=> {link}:\n" + web_fetch(link))
    return "\n".join(parts)


# ── Preflight Search Decision ────────────────────────────────────────────────


def should_preflight(user_message: str, mode: str = "general") -> bool:
    """Only trigger search on explicit external-reference signals."""
    text = user_message.lower()

    local_only = [
        "inspect ", "where is", "find in this file",
        "current working directory", "list the", "git status",
        "read my file", "summarize", "show me the code",
        "refactor this", "explain this code", "fix bug in", "edit ",
    ]
    if any(p in text for p in local_only):
        return False

    if mode == "general":
        explicit = [
            "search for", "search the web", "look up",
            "find on the web", "what is the current",
            "latest news", "today's", "google ",
        ]
        return any(t in text for t in explicit)

    if len(text) < 60:
        return False

    signals = [
        "latest version", "current api", "recent changes",
        "documentation for", "how to use", "install ", "pypi",
        "npm", "github.com/", "error ", "bug ", "tutorial",
        "example code", "reference", "changelog",
    ]
    return any(t in text for t in signals)


def preflight_query(user_message: str, mode: str = "general") -> str:
    q = user_message.strip()
    return q[:200] if len(q) > 200 else q


# ── Playwright Browser ───────────────────────────────────────────────────────


def browse_web(url: str, selector: str = "body", action: str = "scrape", value: str = None) -> str:
    """Browse via Playwright with anti-bot fingerprint bypass and stylesheet/image blocking for maximum speed."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "STOP: Playwright not installed. Run: pip install playwright && playwright install"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="en-US"
            )
            
            # Anti-bot bypass: remove webdriver signature
            page = context.new_page()
            page.add_init_script("delete Object.getPrototypeOf(navigator).webdriver")
            
            # Speed optimization: block images, stylesheets, media, and fonts
            def block_resources(route):
                if route.request.resource_type in ["image", "stylesheet", "font", "media"]:
                    route.abort()
                else:
                    route.continue_()
            
            page.route("**/*", block_resources)
            page.set_default_timeout(10000)
            
            # Load page
            page.goto(url, wait_until="domcontentloaded")
            
            if action == "scrape":
                # Graceful fallback if target selector is not found in 3s
                try:
                    page.wait_for_selector(selector, timeout=3000)
                    el = page.query_selector(selector)
                except Exception:
                    el = page.query_selector("body")
                
                if not el:
                    browser.close()
                    return "STOP: Selector not found and body fallback failed."
                
                text = el.inner_text()
                clean_lines = [l.strip() for l in text.splitlines() if l.strip()]
                clean_text = "\n".join(clean_lines)
                browser.close()
                return clean_text[:6000]

            elif action == "click":
                try:
                    page.wait_for_selector(selector, timeout=3000)
                    page.click(selector)
                    page.wait_for_timeout(1500)
                except Exception as click_err:
                    browser.close()
                    return f"STOP: Click failed on selector '{selector}': {click_err}"
                
                title = page.title()
                content = page.locator("body").inner_text()
                browser.close()
                return f"Clicked '{selector}'. Page: '{title}'.\n{content[:2000]}"

            elif action == "fill":
                if not value:
                    browser.close()
                    return "STOP: 'value' required for fill action."
                try:
                    page.wait_for_selector(selector, timeout=3000)
                    page.fill(selector, value)
                    page.press(selector, "Enter")
                    page.wait_for_timeout(1500)
                except Exception as fill_err:
                    browser.close()
                    return f"STOP: Fill failed on selector '{selector}': {fill_err}"
                
                title = page.title()
                content = page.locator("body").inner_text()
                browser.close()
                return f"Filled '{selector}'. Page: '{title}'.\n{content[:2000]}"

            browser.close()
            return f"STOP: Unsupported action '{action}'."

    except Exception as e:
        return f"STOP: Playwright error: {e}"
