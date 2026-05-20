from __future__ import annotations

import html as html_lib
import ipaddress
import json
import random
import re
import socket
import time
import urllib.parse
import urllib.request
from datetime import datetime

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None


URL_RE = re.compile(r"https?://[^\s)>\]\"']+", re.IGNORECASE)
MAX_FETCH_BYTES = 1_000_000


def _strip_tags(html: str) -> str:
    html = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>", " ", html or "")
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
    text = html_lib.unescape(text)
    text = re.sub(r"@font-face\s*\{[^{}]*\}", " ", text)
    text = re.sub(r"(?:[.#][A-Za-z0-9_-]+|[A-Za-z-]+)\s*\{[^{}]{0,500}\}", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class SearchService:
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    ]

    class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            SearchService._validate_public_http_url(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    def _open_public_url(self, request, timeout: int):
        opener = urllib.request.build_opener(self._SafeRedirectHandler)
        return opener.open(request, timeout=timeout)

    @staticmethod
    def _validate_public_http_url(url: str) -> urllib.parse.ParseResult:
        parsed = urllib.parse.urlparse(url or "")
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Rejected URL scheme: {parsed.scheme or 'missing'}")
        if parsed.username or parsed.password:
            raise ValueError("Rejected URL with embedded credentials.")
        host = parsed.hostname
        if not host:
            raise ValueError("Rejected URL without a host.")
        if host.lower() in {"localhost", "localhost.localdomain"}:
            raise ValueError("Rejected local host URL.")
        try:
            addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError(f"Could not resolve URL host: {exc}") from exc
        for item in addresses:
            ip = ipaddress.ip_address(item[4][0])
            if not ip.is_global:
                raise ValueError(f"Rejected non-public URL host: {host}")
        return parsed

    def read_urls_from_text(self, text: str, max_chars: int = 6000) -> str:
        sections = []
        for index, url in enumerate(URL_RE.findall(text or "")[:5], start=1):
            excerpt = self.read_url(url, max_chars=max_chars)
            if excerpt:
                sections.append(f"--- Explicit URL {index} ---\nURL: {url}\nPage Excerpt:\n{excerpt}")
        return "\n\n".join(sections)

    def search_context(self, query: str, limit: int = 4, max_chars: int = 5000) -> str:
        result = self.quick_search_context(query, limit=limit, max_chars=max_chars)
        return result.get("context", "")

    def quick_search_context(
        self,
        query: str,
        settings: dict | None = None,
        limit: int = 4,
        max_chars: int = 5000,
        read_pages: bool = True,
    ) -> dict:
        query = (query or "").strip()
        if not query:
            return {"results": [], "context": ""}

        sections = [self.current_date_line(), f"Latest user request: {query}"]
        weather_context = self.weather_context(query)
        if weather_context:
            sections.append(weather_context)

        seen_urls: set[str] = set()
        results: list[dict] = []
        for search_query in self._search_queries(query):
            for result in self.web_search(search_query, settings or {}, limit=limit):
                url = result.get("url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                result = dict(result)
                result["search_query"] = search_query
                results.append(result)
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

        if not results and not weather_context:
            return {"results": [], "context": "\n".join(sections + ["No live web results were found."])}

        excerpt_budget = max(800, max_chars // max(1, min(limit, len(results) or 1)))
        for index, result in enumerate(results[:limit], start=1):
            url = result.get("url", "")
            excerpt = self.read_url(url, max_chars=excerpt_budget) if read_pages and url else ""
            sections.append(
                f"--- Search Result {index} ---\n"
                f"Search Query: {result.get('search_query', query)}\n"
                f"Title: {result.get('title', 'Search Result')}\n"
                f"URL: {url}\n"
                f"Search Snippet: {result.get('content', '')}\n"
                f"Page Excerpt:\n{excerpt}"
            )
        return {"results": results[:limit], "context": "\n\n".join(sections)}

    def web_search(self, query: str, settings: dict | None = None, limit: int = 5) -> list[dict]:
        settings = settings or {}
        tavily_key = (settings.get("tavily_api_key") or "").strip()
        if tavily_key:
            try:
                return self._tavily(query, tavily_key, limit=limit)
            except Exception:
                pass
        return self._duckduckgo(query, limit=limit)

    @staticmethod
    def current_date_line() -> str:
        return f"Current date: {datetime.now().strftime('%B %d, %Y')}."

    def weather_context(self, query: str) -> str:
        location = self._weather_location(query)
        if not location:
            return ""
        encoded = urllib.parse.quote(location)
        url = f"https://wttr.in/{encoded}?format=j1"
        try:
            self._validate_public_http_url(url)
            req = urllib.request.Request(url, headers={"User-Agent": random.choice(self.USER_AGENTS)})
            with self._open_public_url(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
            current = (data.get("current_condition") or [{}])[0]
            area = ((data.get("nearest_area") or [{}])[0].get("areaName") or [{}])[0].get("value") or location
            region = ((data.get("nearest_area") or [{}])[0].get("region") or [{}])[0].get("value") or ""
            desc = ((current.get("weatherDesc") or [{}])[0].get("value") or "").strip()
            return "\n".join([
                f"--- Direct Weather Lookup ---",
                f"Requested location: {location}",
                f"Resolved location: {area}{', ' + region if region else ''}",
                f"Source URL: https://wttr.in/{encoded}",
                f"Current conditions: {desc or 'unknown'}",
                f"Temperature: {current.get('temp_F', '?')} F / {current.get('temp_C', '?')} C",
                f"Feels like: {current.get('FeelsLikeF', '?')} F / {current.get('FeelsLikeC', '?')} C",
                f"Humidity: {current.get('humidity', '?')}%",
                f"Wind: {current.get('windspeedMiles', '?')} mph {current.get('winddir16Point', '')}".strip(),
                f"Precipitation: {current.get('precipInches', '?')} in",
            ])
        except Exception as exc:
            return f"--- Direct Weather Lookup ---\nRequested location: {location}\nStatus: unavailable ({exc})"

    @staticmethod
    def _weather_location(query: str) -> str:
        lowered = (query or "").lower()
        if not any(word in lowered for word in ("weather", "forecast", "temperature", "conditions")):
            return ""
        cleaned = re.sub(r"https?://\S+", " ", query or "", flags=re.IGNORECASE)
        patterns = [
            r"(?:search|look up|lookup|find)\s+(.+?)\s+(?:weather|forecast|temperature|conditions)",
            r"(?:in|for|at|near)\s+(.+?)\s+(?:weather|forecast|temperature|conditions)",
            r"(?:weather|forecast|temperature|conditions)\s+(?:in|for|at|near)\s+(.+)",
        ]
        candidate = ""
        for pattern in patterns:
            match = re.search(pattern, cleaned, flags=re.IGNORECASE)
            if match:
                candidate = match.group(1)
                break
        if not candidate:
            candidate = cleaned
        candidate = re.sub(
            r"\b(can you|could you|please|search|look up|lookup|find|tell me|what is|what's|current|"
            r"the|a|an|for|in|at|near|weather|forecast|temperature|conditions|today|now|me|give|with|"
            r"and|about|info|information)\b",
            " ",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(r"[^A-Za-z0-9,\s-]", " ", candidate)
        candidate = re.sub(r"\s+", " ", candidate).strip(" ,-")
        return candidate[:80]

    def _search_queries(self, query: str) -> list[str]:
        base = self._clean_query(query)
        queries = [base]
        lowered = base.lower()
        location = self._weather_location(query)
        if location:
            queries.insert(0, f"{location} weather forecast current conditions")
        if any(word in lowered for word in ("review", "reviews", "rating", "ratings")):
            target = self._review_target(base)
            queries.insert(0, f"{target} reviews ratings")
            queries.append(f"{target} user reviews")
        if any(word in lowered for word in ("latest", "current", "new", "upcoming", "2026", "today", "now")):
            queries.append(f"{base} latest")
        if "movie" in lowered or "movies" in lowered:
            queries.append(f"{base} titles release dates")
        return self._dedupe([query for query in queries if query])

    @staticmethod
    def _clean_query(query: str) -> str:
        cleaned = re.sub(r"https?://\S+", " ", query or "", flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(can you|could you|please|search|look up|lookup|find|tell me|show me|give me)\b", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ?!.")
        return cleaned or (query or "").strip()

    @staticmethod
    def _review_target(query: str) -> str:
        target = re.sub(r"\b(find|search|look up|reviews?|ratings?|for|about|of|on|site)\b", " ", query, flags=re.IGNORECASE)
        target = re.sub(r"\s+", " ", target).strip(" ?!.")
        return target or query

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen = set()
        output = []
        for item in items:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            output.append(item)
        return output

    def _tavily(self, query: str, key: str, limit: int = 5) -> list[dict]:
        payload = json.dumps({
            "api_key": key,
            "query": query,
            "search_depth": "basic",
            "include_answer": False,
            "max_results": limit,
        }).encode("utf-8")
        request = urllib.request.Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": random.choice(self.USER_AGENTS)},
        )
        self._validate_public_http_url(request.full_url)
        with self._open_public_url(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        return [
            {"title": item.get("title", "Search Result"), "url": item.get("url", ""), "content": item.get("content", "")}
            for item in data.get("results", [])[:limit]
            if item.get("url")
        ]

    def _duckduckgo(self, query: str, limit: int = 5) -> list[dict]:
        encoded = urllib.parse.urlencode({"q": query})
        for attempt in range(3):
            try:
                request = urllib.request.Request(
                    "https://html.duckduckgo.com/html/",
                    data=encoded.encode("utf-8"),
                    headers={
                        "User-Agent": random.choice(self.USER_AGENTS),
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
                self._validate_public_http_url(request.full_url)
                with self._open_public_url(request, timeout=20) as response:
                    page = response.read().decode("utf-8", errors="ignore")
                results = self._parse_duckduckgo_html(page, limit=limit)
                time.sleep(0.2)
                return results
            except Exception:
                if attempt == 2:
                    return []
                time.sleep(0.8)
        return []

    def _parse_duckduckgo_html(self, page: str, limit: int = 5) -> list[dict]:
        if BeautifulSoup is not None:
            soup = BeautifulSoup(page or "", "html.parser")
            results = []
            for anchor in soup.select("a.result__a"):
                raw_url = anchor.get("href", "")
                url = self._clean_duckduckgo_url(raw_url)
                if not url:
                    continue
                container = anchor.find_parent(class_=re.compile(r"\bresult\b")) or anchor.parent
                snippet_node = container.select_one(".result__snippet") if container else None
                snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
                results.append({
                    "title": anchor.get_text(" ", strip=True) or "Search Result",
                    "url": url,
                    "content": snippet,
                })
                if len(results) >= limit:
                    break
            if results:
                return results

        results = []
        blocks = re.split(r'<div[^>]+class="[^"]*\bresult\b[^"]*"', page or "", flags=re.S)[1:]
        if not blocks:
            blocks = re.findall(r"<a[^>]+class=\"result__a\".*?</a>", page or "", flags=re.S)
        for block in blocks:
            anchor = re.search(r"<a[^>]+class=\"result__a\"[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", block, flags=re.S)
            if anchor:
                raw_url = anchor.group(1)
                title = _strip_tags(anchor.group(2))
            else:
                href = re.search(r"href=\"([^\"]+)\"", block)
                if not href:
                    continue
                raw_url = href.group(1)
                title = _strip_tags(block)
            snippet_match = re.search(r"<a[^>]+class=\"result__snippet\"[^>]*>(.*?)</a>", block, flags=re.S)
            if not snippet_match:
                snippet_match = re.search(r"<div[^>]+class=\"result__snippet\"[^>]*>(.*?)</div>", block, flags=re.S)
            snippet = _strip_tags(snippet_match.group(1)) if snippet_match else ""
            url = self._clean_duckduckgo_url(raw_url)
            if not url:
                continue
            results.append({"title": title or "Search Result", "url": url, "content": snippet})
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _clean_duckduckgo_url(raw_url: str) -> str:
        url = html_lib.unescape(urllib.parse.unquote(raw_url or ""))
        if url.startswith("//"):
            url = "https:" + url
        parsed = urllib.parse.urlparse(url)
        if "duckduckgo.com/l/" in url or parsed.path.startswith("/l/"):
            query = urllib.parse.parse_qs(parsed.query)
            url = query.get("uddg", [url])[0]
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return ""
        return url

    def read_url(self, url: str, max_chars: int = 4000) -> str:
        try:
            parsed = self._validate_public_http_url(url)
        except ValueError as exc:
            return f"Rejected: {exc}"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": random.choice(self.USER_AGENTS)})
            with self._open_public_url(request, timeout=20) as response:
                self._validate_public_http_url(response.geturl())
                content_type = response.headers.get("content-type", "")
                raw = response.read(min(MAX_FETCH_BYTES, max_chars * 5))
            if "application/json" in content_type:
                text = json.dumps(json.loads(raw.decode("utf-8", "replace")), indent=2)
            else:
                page = raw.decode("utf-8", errors="ignore")
                body_start = page.lower().find("<body")
                if body_start != -1:
                    page = page[body_start:]
                text = _strip_tags(page)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > max_chars:
                return text[:max_chars] + "...\n[TRUNCATED]"
            return text
        except Exception as exc:
            return f"Failed to read: {exc}"
