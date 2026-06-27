import re
import html
import httpx
from html.parser import HTMLParser
from urllib.parse import urlparse
from datetime import datetime
from bs4 import BeautifulSoup
from collections import Counter

SEARXNG_URL = "http://localhost:8081/search"


class AgentInterrupted(Exception):
    pass


def trunc(text: str, max_len: int = 150) -> str:
    """Helper to truncate text with ellipsis."""
    return text if len(text) <= max_len else text[:max_len] + "..."


class SearxHTMLParser(HTMLParser):
    """Extract result title/url/snippet from SearXNG HTML when JSON is disabled."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self.current = None
        self.capture = None
        self.in_h3 = False
        self.h3_link = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get("class", "").split())
        if tag == "article" and "result" in classes:
            self.current = {"title": "", "url": "", "content": ""}
        elif self.current is not None and tag == "h3":
            self.in_h3 = True
        elif self.current is not None and self.in_h3 and tag == "a":
            self.h3_link = True
            if not self.current["url"]:
                self.current["url"] = attrs.get("href", "")
            self.capture = "title"
        elif self.current is not None and tag == "p" and "content" in classes:
            self.capture = "content"

    def handle_endtag(self, tag):
        if tag == "article" and self.current is not None:
            if self.current.get("title") and self.current.get("url"):
                for key in ("title", "content"):
                    self.current[key] = re.sub(r"\s+", " ", self.current[key]).strip()
                self.results.append(self.current)
            self.current = None
            self.capture = None
            self.in_h3 = False
            self.h3_link = False
        elif tag == "h3":
            self.in_h3 = False
            self.h3_link = False
            if self.capture == "title":
                self.capture = None
        elif tag == "p" and self.capture == "content":
            self.capture = None

    def handle_data(self, data):
        if self.current is not None and self.capture in ("title", "content"):
            self.current[self.capture] += data


def _search_variants(query: str, current: bool = True) -> list:
    """Generate minimal query variants. Kept to 1-2 to ensure 90% faster searches."""
    q = query.strip()
    variants = [q]
    if current and not re.search(
        r"\b20\d{2}\b|latest|current|2026|recent|today|updated", q, re.I
    ):
        variants.append(f"{q} 2026")
    return variants


def _searx_json_search(client: httpx.Client, query: str, max_results: int) -> tuple:
    r = client.get(
        SEARXNG_URL,
        params={
            "q": query,
            "format": "json",
            "language": "en-US",
            "categories": "general",
        },
        timeout=0.8,
    )
    if r.status_code == 403:
        return [], "json-disabled"
    r.raise_for_status()
    data = r.json()
    results = []
    for res in data.get("results", [])[:max_results]:
        url = res.get("url", "")
        title = html.unescape(res.get("title") or "(no title)")
        content = html.unescape(res.get("content") or "")
        if url and title:
            results.append(
                {"title": title, "url": url, "content": content, "query": query}
            )
    return results, "json"


def _searx_html_search(client: httpx.Client, query: str, max_results: int) -> tuple:
    r = client.get(
        SEARXNG_URL,
        params={
            "q": query,
            "language": "en-US",
            "categories": "general",
        },
        timeout=0.8,
    )
    r.raise_for_status()
    parser = SearxHTMLParser()
    parser.feed(r.text)
    results = parser.results[:max_results]
    for res in results:
        res["query"] = query
    return results, "html"


def _score_result(res: dict, query: str) -> int:
    title = res.get("title", "").lower()
    content = res.get("content", "").lower()
    host = urlparse(res.get("url", "")).netloc.lower()
    qwords = set(re.findall(r"[a-z0-9_]{3,}", query.lower()))
    score = sum(3 for w in qwords if w in title) + sum(
        1 for w in qwords if w in content
    )
    if any(
        host.endswith(h)
        for h in (
            "github.com",
            "developer.mozilla.org",
            "docs.python.org",
            "pypi.org",
            "npmjs.com",
            "threejs.org",
        )
    ):
        score += 6
    if any(
        word in title + " " + content
        for word in ("2026", "latest", "updated", "documentation", "example")
    ):
        score += 2
    return score


def _mojeek_search(client: httpx.Client, query: str, max_results: int) -> list:
    """Fallback search using Mojeek (no API)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = client.get(
            "https://www.mojeek.com/search",
            params={"q": query},
            headers=headers,
            timeout=0.8,
        )
        r.raise_for_status()

        results = []
        links = re.findall(r'<a class="title" [^>]+ href="([^"]+)">([^<]+)</a>', r.text)
        snippets = re.findall(r'<p class="s">(.*?)</p>', r.text, re.DOTALL)

        for i in range(min(len(links), len(snippets), max_results)):
            url, title = links[i]
            snippet = re.sub(r"<[^>]+>", "", snippets[i])
            results.append(
                {
                    "title": html.unescape(title).strip(),
                    "url": url.strip(),
                    "content": html.unescape(snippet).strip(),
                    "query": query,
                }
            )
        return results
    except Exception:
        return []


def _ddg_lite_search(client: httpx.Client, query: str, max_results: int) -> list:
    """Fallback search using DuckDuckGo Lite (no JavaScript, scrape-friendly)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    try:
        from urllib.parse import unquote
        # DuckDuckGo Lite expects URL-encoded form data
        r = client.post(
            "https://lite.duckduckgo.com/lite/",
            data={"q": query},
            headers=headers,
            timeout=0.8,
        )
        r.raise_for_status()
        
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        
        link_tags = soup.find_all("a", class_="result-link")
        for link_tag in link_tags:
            parent_tr = link_tag.find_parent("tr")
            if parent_tr and "result-sponsored" in parent_tr.get("class", []):
                continue
                
            title = link_tag.get_text(strip=True)
            url = link_tag.get("href", "")
            
            if not title or not url:
                continue
            if "duckduckgo.com/y.js" in url or "duckduckgo-help-pages" in url or title.lower() == "more info":
                continue
                
            if "/l/?uddg=" in url:
                m = re.search(r"uddg=([^&]+)", url)
                if m:
                    url = unquote(m.group(1))
            
            snippet = ""
            if parent_tr:
                next_tr = parent_tr.find_next_sibling("tr")
                if next_tr:
                    snippet_tag = next_tr.find(class_="result-snippet")
                    if snippet_tag:
                        snippet = snippet_tag.get_text(strip=True)
                    else:
                        next_next_tr = next_tr.find_next_sibling("tr")
                        if next_next_tr:
                            snippet_tag = next_next_tr.find(class_="result-snippet")
                            if snippet_tag:
                                snippet = snippet_tag.get_text(strip=True)
                                
            results.append({
                "title": title,
                "url": url,
                "content": snippet,
                "query": query
            })
            if len(results) >= max_results:
                break
        return results
    except Exception:
        return []


def search_web(query: str, max_results: int = 6, current: bool = True) -> str:
    """
    Agentic SearXNG search with Mojeek and DuckDuckGo Lite fallbacks.
    """
    diagnostics = []
    all_results = []
    seen_urls = set()
    variants = _search_variants(query, current)
    headers = {
        "User-Agent": "local-agent/1.0 (+agentic-loop)",
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
    }

    try:
        with httpx.Client(headers=headers, follow_redirects=True) as client:
            for i, variant in enumerate(variants):
                if len(all_results) > 0:
                    break
                try:
                    results, mode = _searx_json_search(client, variant, max_results)
                    if mode == "json-disabled":
                        results, mode = _searx_html_search(client, variant, max_results)

                    if results:
                        diagnostics.append(f"{mode}:{variant}:{len(results)}")
                        for res in results:
                            url = res.get("url", "")
                            if url and url not in seen_urls:
                                seen_urls.add(url)
                                res["score"] = _score_result(res, query)
                                all_results.append(res)
                except Exception as e:
                    diagnostics.append(f"searx:{variant}:{type(e).__name__}")
                    continue

            if not all_results:
                diagnostics.append("searx-failed:trying-mojeek")
                for variant in variants[:2]:
                    mojeek_results = _mojeek_search(client, variant, max_results)
                    if mojeek_results:
                        diagnostics.append(f"mojeek:{variant}:{len(mojeek_results)}")
                        for res in mojeek_results:
                            url = res.get("url", "")
                            if url and url not in seen_urls:
                                seen_urls.add(url)
                                res["score"] = _score_result(res, query)
                                all_results.append(res)
                    if len(all_results) >= max_results:
                        break

            if not all_results:
                diagnostics.append("mojeek-failed:trying-ddg")
                for variant in variants[:2]:
                    ddg_results = _ddg_lite_search(client, variant, max_results)
                    if ddg_results:
                        diagnostics.append(f"ddg:{variant}:{len(ddg_results)}")
                        for res in ddg_results:
                            url = res.get("url", "")
                            if url and url not in seen_urls:
                                seen_urls.add(url)
                                res["score"] = _score_result(res, query)
                                all_results.append(res)
                    if len(all_results) >= max_results:
                        break
    except KeyboardInterrupt:
        raise AgentInterrupted()

    all_results.sort(key=lambda r: r.get("score", 0), reverse=True)
    all_results = all_results[:max_results]

    if not all_results:
        return (
            "No results found after trying SearXNG, Mojeek, and DuckDuckGo Lite.\n"
            "Diagnostics: " + "; ".join(diagnostics[:8]) + "\n"
            "Possible issues: Search engines are rate-limiting or blocking automated access."
        )

    lines = [
        f"WEB SEARCH: {query}",
        f"DATE CONTEXT: {datetime.now().date().isoformat()}",
        "USE: Treat these as external/current context. Prefer primary docs over forums.",
        "",
    ]
    for i, res in enumerate(all_results, 1):
        lines += [
            f"{i}. {res.get('title', '(no title)')}",
            f"   URL: {res.get('url', '')}",
            f"   SNIPPET: {trunc(res.get('content', ''), 300)}",
            "",
        ]
    return "\n".join(lines)


def simple_rag(text: str, query: str, chunk_size: int = 1000, top_k: int = 3) -> str:
    """
    A lightweight, no-dependency keyword-based RAG function.
    Splits text into chunks, scores them by TF-IDF-lite (term frequency),
    and returns the highest scoring chunks to save context.
    """
    if not query:
        # If no query, just return the first few chunks
        return text[: chunk_size * top_k]

    chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
    if not chunks:
        return ""

    # Tokenize query
    q_words = set(re.findall(r"\w+", query.lower()))
    if not q_words:
        return "\n...\n".join(chunks[:top_k])

    scored_chunks = []
    for i, chunk in enumerate(chunks):
        c_words = re.findall(r"\w+", chunk.lower())
        c_counts = Counter(c_words)
        score = sum(c_counts.get(qw, 0) for qw in q_words)
        # Give a small boost to earlier chunks (usually contain intros/abstracts)
        score += 1.0 / (i + 1)
        scored_chunks.append((score, chunk))

    # Sort by score descending
    scored_chunks.sort(key=lambda x: x[0], reverse=True)

    # Take top K
    best_chunks = [c for _, c in scored_chunks[:top_k]]
    return "\n...[RAG Context Break]...\n".join(best_chunks)


def web_fetch(url: str, query: str = "") -> str:
    """
    Read the content of a URL and perform a context-friendly RAG extraction.
    If a query is provided, it extracts only the most relevant chunks.
    """
    spa_blocklist = [
        "substack.com",
        "medium.com",
        "fortune.com",
        "stanford.edu",
        "aeon.co",
        "bloomberg.com",
        "nytimes.com",
        "theatlantic.com",
        "nautil.us",
        "quantamagazine.org",
    ]
    if any(domain in url for domain in spa_blocklist):
        return f"SKIP_FETCH: URL belongs to a known SPA/JS-heavy domain ({url}). Rely on search snippets instead."

    try:
        is_raw = any(
            x in url
            for x in [
                "raw.githubusercontent.com",
                "gist.githubusercontent.com",
                ".txt",
                ".py",
                ".js",
                ".html",
            ]
        )

        if "github.com" in url and "/blob/" in url:
            url = url.replace("github.com", "raw.githubusercontent.com").replace(
                "/blob/", "/"
            )
            is_raw = True

        resp = httpx.get(url, timeout=20, follow_redirects=True)
        resp.raise_for_status()
        text = resp.text

        if is_raw:
            return text[:12000]

        # Use BeautifulSoup to strip out boilerplate HTML
        soup = BeautifulSoup(text, "html.parser")

        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header", "aside"]):
            script.extract()

        clean_text = soup.get_text(separator="\n")

        # Clean up excessive newlines
        lines = (line.strip() for line in clean_text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        clean_text = "\n".join(chunk for chunk in chunks if chunk)

        if len(clean_text) < 3000:
            final_content = (
                f"--- EXTRACTED PAGE (Full) ---\n{clean_text}\n--- END PAGE ---"
            )
            _append_to_second_brain(
                url, soup.title.string if soup.title else url, final_content
            )
            return final_content

        # Apply RAG
        rag_content = simple_rag(clean_text, query, chunk_size=1500, top_k=3)

        header = (
            f"--- EXTRACTED RAG KNOWLEDGE GRAPH (Query: '{query}') ---\n"
            if query
            else "--- EXTRACTED PAGE SUMMARY ---\n"
        )
        final_content = header + rag_content + "\n--- END RAG ---"
        _append_to_second_brain(
            url, soup.title.string if soup.title else url, final_content
        )
        return final_content

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"FATAL ERROR 404: The page {url} does not exist. DO NOT RETRY THIS URL. Abandon this specific link and search for an alternative."
        return f"FATAL ERROR HTTP {e.response.status_code} fetching URL: {e}. DO NOT RETRY THIS URL."
    except Exception as e:
        return f"FATAL ERROR fetching URL: {e}. DO NOT RETRY THIS URL."


def _append_to_second_brain(url: str, title: str, content: str):
    """Silently append extracted knowledge to the local second brain."""
    try:
        title_str = str(title).strip().replace("\n", " ")
        with open("second_brain.md", "a", encoding="utf-8") as f:
            f.write(f"\n\n## {title_str}\n")
            f.write(f"**URL:** {url}\n")
            f.write(f"**Date:** {datetime.now().date().isoformat()}\n\n")
            f.write(content)
            f.write("\n---\n")
    except Exception:
        pass


def search_second_brain(query: str, top_k: int = 5) -> str:
    """
    Search the local 'Second Brain' which automatically stores all previously fetched web pages.
    """
    try:
        with open("second_brain.md", "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            return "Second brain is currently empty."
        return simple_rag(content, query, chunk_size=1500, top_k=top_k)
    except FileNotFoundError:
        return "Second brain is currently empty."


def scout_website(url: str, depth: int = 1) -> str:
    """Fetch a URL and automatically extract and fetch top internal links for deep context."""
    try:
        base_content = web_fetch(url)
        if base_content.startswith("ERROR") or base_content.startswith("SKIP_FETCH"):
            return base_content

        # Simple regex to find hrefs
        links = re.findall(r'href=[\'"]?([^\'" >]+)', base_content)
        parsed_base = urlparse(url)
        base_domain = f"{parsed_base.scheme}://{parsed_base.netloc}"

        valid_links = []
        for l in links:
            if l.startswith("http") and parsed_base.netloc in l:
                valid_links.append(l)
            elif l.startswith("/"):
                valid_links.append(f"{base_domain}{l}")

        # Basic deduplication
        valid_links = list(dict.fromkeys(valid_links))

        # Limit to top 3 links to avoid huge context bloat
        top_links = valid_links[:3]
        if not top_links:
            return base_content

        results = [base_content, f"\n--- SCOUTED LINKS (Depth {depth}) ---"]
        for link in top_links:
            results.append(f"\n=> {link}:\n" + web_fetch(link))

        return "\n".join(results)
    except Exception as e:
        return f"ERROR scouting URL {url}: {e}"


# -- Preflight Search Decision (lightweight, minimal) ---------------------


def should_preflight(user_message: str, mode: str = "general") -> bool:
    """
    Decide whether to run a lightweight preflight web search.
    Triggers ONLY on explicit search intent -- not for general technical terms.
    Defaults to False: web search is opt-in, not automatic.
    """
    text = user_message.lower()

    # Local-only tasks -- never search
    local_only = [
        "inspect loop.py",
        "where is",
        "find in this file",
        "current working directory",
        "list the python files",
        "git status",
        "read my file",
        "summarize",
        "show me the code",
        "refactor this",
        "explain this code",
        "fix bug in",
        "edit ",
    ]
    if any(phrase in text for phrase in local_only):
        return False

    # In general mode, only search on explicit request
    if mode == "general":
        explicit_search = [
            "search for",
            "search the web",
            "look up",
            "find on the web",
            "what is the current",
            "latest news",
            "today's",
            "google ",
        ]
        return any(term in text for term in explicit_search)

    # For coding mode: short prompts are likely quick local tasks, skip search
    if len(text) < 60:
        return False

    # Only trigger on clear external-reference signals
    external_signals = [
        "latest version",
        "current api",
        "recent changes",
        "documentation for",
        "how to use",
        "install ",
        "pypi",
        "npm",
        "github.com/",
        "error ",
        "bug ",
        "tutorial",
        "example code",
        "reference",
        "changelog",
    ]

    return any(term in text for term in external_signals)


def preflight_query(user_message: str, mode: str = "general") -> str:
    """Simple query preparation -- no aggressive suffix injection."""
    q = user_message.strip()
    if len(q) > 200:
        q = q[:200]
    return q


def browse_web(url: str, selector: str = "body", action: str = "scrape", value: str = None) -> str:
    """Browse or scrape a website using Playwright.
    
    Args:
        url: The website URL to load.
        selector: CSS selector to query/interact with (default 'body').
        action: Action to perform: 'scrape' (text content), 'click' (click element), or 'fill' (type value).
        value: The text value to input (only required for 'fill').
        
    Returns:
        The text content of the target element, or page summary after interaction.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "ERROR: Playwright is not installed. Run 'pip install playwright' and 'playwright install'."
        
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            page.set_default_timeout(10000) # 10 seconds
            page.goto(url, wait_until="domcontentloaded")
            
            if action == "scrape":
                page.wait_for_selector(selector, timeout=5000)
                element = page.query_selector(selector)
                if not element:
                    return f"ERROR: Element matching selector '{selector}' not found."
                text = element.inner_text()
                browser.close()
                return text.strip()
                
            elif action == "click":
                page.wait_for_selector(selector, timeout=5000)
                page.click(selector)
                page.wait_for_timeout(2000)
                title = page.title()
                content = page.locator("body").inner_text()
                browser.close()
                return f"Success: Clicked '{selector}'. New page title: '{title}'.\nContent summary:\n{content[:1500]}"
                
            elif action == "fill":
                if not value:
                    return "ERROR: 'value' parameter is required for 'fill' action."
                page.wait_for_selector(selector, timeout=5000)
                page.fill(selector, value)
                page.press(selector, "Enter")
                page.wait_for_timeout(2000)
                title = page.title()
                content = page.locator("body").inner_text()
                browser.close()
                return f"Success: Filled '{selector}' with '{value}' and pressed Enter. Page title: '{title}'.\nContent summary:\n{content[:1500]}"
                
            else:
                browser.close()
                return f"ERROR: Unsupported action '{action}'. Supported actions: scrape, click, fill."
    except Exception as e:
        return f"ERROR: Playwright operation failed: {e}"
