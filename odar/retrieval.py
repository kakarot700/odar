"""Resilient zero-cost retrieval layer (ODAR production).

* **Transport hardening** - pooled ``requests.Session`` with a ``urllib3``
  retry circuit, plus the failure-class-aware :class:`odar.retry.RetryPolicy`.
* **SSRF-safe fetching** - :class:`PageExtractor` resolves and validates
  every URL and every redirect hop against the deny policy in
  :mod:`odar.url_safety` before reading a single response byte; responses are
  size-capped and content-type restricted.
* **Trust boundary** - every fetched page is scanned for prompt-injection
  signatures and sanitized (invisible/control characters stripped) before it
  may enter any prompt or claim pool.
* **Search cascade** (first tier that yields *gated* evidence wins):
  1. ``ddgs`` (or legacy ``duckduckgo_search``)'s ``DDGS().text(...)`` (transparent fallback to
     the successor ``ddgs`` package),
  2. direct DuckDuckGo HTML endpoint scraper with redirect resolution,
  3. Wikipedia Search API + OpenSearch fallback with REST summary
     extraction.
* **Relevance gate** (poka-yoke against off-topic clutter and homonyms):
  hits must clear a bigram + content-token relevance score before entering
  the evidence context.
* **Extraction** - raw HTML cleaned with ``trafilatura`` into compact text
  capped at ``max_chars`` (default **18,000**) to prevent context bloat; a
  naive tag-stripper is used if trafilatura is unavailable.

Every public method is exception-safe: failures degrade to empty results
plus structured error envelopes, never raised exceptions.
"""

from __future__ import annotations

import html
import logging
import random
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry as _RetryImpl

    Retry: Any = _RetryImpl
    _HAS_RETRY = True
except Exception:  # pragma: no cover - import guard
    Retry = None
    _HAS_RETRY = False

from odar.evidence import content_hash
from odar.loop_breaker import STOPWORDS, tokenize
from odar.pinned import pinned_resolution
from odar.schemas import ExtractedPage, SearchHit
from odar.trust import sanitize_external_text, scan_for_injection
from odar.url_safety import (
    ALLOWED_CONTENT_TYPES,
    MAX_REDIRECTS,
    MAX_RESPONSE_BYTES,
    UnsafeURLError,
    validate_url,
)

logger = logging.getLogger("odar.retrieval")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 ODAR/2.0 (+research-engine)"
)

# Wikimedia robot policy requires a descriptive UA with contact info; spoofed
# browser UAs trigger intermittent 403 bursts on the API.
WIKIPEDIA_UA = "ODAR-Research-Engine/2.0 (zero-cost research; odar@example.org) python-requests"

# --- optional third-party backends ------------------------------------------ #
try:  # primary extraction backend
    import trafilatura as _trafilatura

    _HAS_TRAFILATURA = True
except Exception:  # pragma: no cover - import guard
    _trafilatura = None
    _HAS_TRAFILATURA = False

_DDGS: Any = None
_DDGS_BACKEND: Optional[str] = None
try:  # maintained successor package: multi-backend (bing, brave, ddg, mojeek, ...)
    from ddgs import DDGS as _SuccessorDDGS

    _DDGS = _SuccessorDDGS
    _DDGS_BACKEND = "ddgs"
except Exception:  # pragma: no cover - import guard
    try:  # legacy package name; 8.x is hard-wired to a single (bing) backend
        from duckduckgo_search import DDGS as _ClassicDDGS

        _DDGS = _ClassicDDGS
        _DDGS_BACKEND = "duckduckgo_search"
    except Exception:
        _DDGS = None
        _DDGS_BACKEND = None


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")


def strip_tags(fragment: str) -> str:
    """Remove HTML tags, unescape entities, collapse whitespace."""
    text = html.unescape(_TAG_RE.sub(" ", fragment))
    return re.sub(r"\s+", " ", text).strip()


def build_pooled_session(
    user_agent: str = USER_AGENT,
    pool_size: int = 8,
    retries: int = 3,
    backoff_factor: float = 0.5,
) -> requests.Session:
    """Create a connection-pooled session with an exponential-backoff retry
    circuit for idempotent failures (429/5xx)."""
    session = requests.Session()
    session.headers.setdefault("User-Agent", user_agent)
    if _HAS_RETRY:
        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            status=retries,
            backoff_factor=backoff_factor,  # exponential: {backoff} * 2^(n-1)
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST", "HEAD"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size, max_retries=retry)
    else:  # pragma: no cover - urllib3 always present with requests
        adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def jittered_backoff(attempt: int, base: float = 0.5, cap: float = 2.5) -> None:
    """Sleep ``base * 2^attempt`` seconds plus uniform jitter (capped).

    The cap keeps worst-case per-query latency bounded when endpoints are
    being throttled (poka-yoke against unbounded retry accumulation).
    """
    delay = min(cap, base * (2**attempt)) + random.uniform(0.0, 0.3)
    time.sleep(delay)


class ZeroCostSearch:
    """DuckDuckGo + Wikipedia cascading search with graceful degradation."""

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        max_retries: int = 2,
        backoff_s: float = 1.5,
        request_timeout: float = 12.0,
    ) -> None:
        self.session = session or build_pooled_session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self.max_retries = max(1, int(max_retries))
        self.backoff_s = float(backoff_s)
        self.request_timeout = float(request_timeout)
        self.relevance_gate = 3
        self._wiki_cache: Dict[str, Any] = {}
        self.stats: Dict[str, int] = {
            "ddgs_calls": 0,
            "ddgs_failures": 0,
            "html_fallbacks": 0,
            "wikipedia_calls": 0,
            "wikipedia_opensearch": 0,
            "gated_out": 0,
        }

    # ------------------------------------------------------------------ #
    def text(self, query: str, max_results: int = 6) -> List[SearchHit]:
        """Return up to ``max_results`` hits for ``query`` (never raises).

        Cascades DDGS -> DDG HTML endpoint -> Wikipedia API/OpenSearch.
        Hits from every tier pass the bigram/content-token relevance gate;
        cascading stops early once enough gated evidence accumulates.
        Results are ranked by relevance score.
        """
        query = (query or "").strip()
        if not query:
            return []
        query_tokens = {t for t in tokenize(query) if t not in STOPWORDS and len(t) > 2}
        ordered_tokens = [t for t in tokenize(query) if t not in STOPWORDS and len(t) > 2]
        query_bigrams = {(ordered_tokens[i], ordered_tokens[i + 1]) for i in range(len(ordered_tokens) - 1)}

        collected: List[SearchHit] = []
        seen_urls: Set[str] = set()
        for tier in (self._search_ddgs, self._search_html_fallback, self._search_wikipedia):
            try:
                hits = tier(query, max_results)
            except Exception as exc:  # poka-yoke: a dead tier never kills search
                logger.warning("search tier failed: %s: %s", type(exc).__name__, exc)
                hits = []
            for hit in hits:
                if hit.url in seen_urls:
                    continue
                seen_urls.add(hit.url)
                collected.append(hit)
            if len(query_tokens) >= 2:
                gated = [
                    h
                    for h in collected
                    if self._relevance_score(query_tokens, query_bigrams, h) >= self.relevance_gate
                ]
                if len(gated) >= max_results:
                    break

        scored: List[Tuple[int, int, SearchHit]] = [
            (self._relevance_score(query_tokens, query_bigrams, hit), index, hit)
            for index, hit in enumerate(collected)
        ]
        if len(query_tokens) >= 2:
            gated_scored = [item for item in scored if item[0] >= self.relevance_gate]
            dropped = len(scored) - len(gated_scored)
            if dropped:
                self.stats["gated_out"] += dropped
                logger.info("relevance gate dropped %d off-topic hit(s) for %r", dropped, query[:60])
            scored = gated_scored
        scored.sort(key=lambda item: (-item[0], item[1]))
        sanitized: List[SearchHit] = []
        for _, _, hit in scored[:max_results]:
            # Trust boundary: search titles/snippets are untrusted external
            # content; strip invisibles/control chars before any downstream
            # consumer (prompt, claim pool, report) can see them.
            sanitized.append(
                SearchHit(
                    url=hit.url,
                    title=sanitize_external_text(hit.title, max_chars=300),
                    snippet=sanitize_external_text(hit.snippet, max_chars=1000),
                    engine=hit.engine,
                )
            )
        return sanitized

    # ------------------------------------------------------------------ #
    @staticmethod
    def _relevance_score(query_tokens: Set[str], query_bigrams: Set[Tuple[str, str]], hit: SearchHit) -> int:
        """Relevance of a hit to the query: bigram matches weigh double."""
        text_tokens = tokenize(f"{hit.title} {hit.snippet}")
        token_overlap = len(set(text_tokens) & query_tokens)
        bigram_matches = sum(
            1 for i in range(len(text_tokens) - 1) if (text_tokens[i], text_tokens[i + 1]) in query_bigrams
        )
        return 2 * bigram_matches + token_overlap

    # ------------------------------------------------------------------ #
    # Tier 1: DDGS library
    # ------------------------------------------------------------------ #
    def _search_ddgs(self, query: str, max_results: int) -> List[SearchHit]:
        if _DDGS is None:
            return []
        for attempt in range(self.max_retries):
            try:
                self.stats["ddgs_calls"] += 1
                raw_results = _DDGS().text(query, max_results=max_results) or []
                hits: List[SearchHit] = []
                for row in raw_results:
                    if not isinstance(row, dict):
                        continue
                    url = row.get("href") or row.get("url") or row.get("link") or ""
                    title = str(row.get("title") or "").strip()
                    snippet = str(row.get("body") or row.get("snippet") or "").strip()
                    if url:
                        hits.append(SearchHit(url=url, title=title, snippet=snippet, engine=_DDGS_BACKEND))
                if hits:
                    return hits
                # Empty result sets are a common soft rate-limit signal:
                # back off before the next attempt instead of hammering.
                if attempt + 1 < self.max_retries:
                    jittered_backoff(attempt, base=self.backoff_s / 2.0)
            except Exception as exc:  # rate limits, network hiccups, etc.
                self.stats["ddgs_failures"] += 1
                logger.warning("DDGS attempt %s failed: %s: %s", attempt + 1, type(exc).__name__, exc)
                jittered_backoff(attempt, base=self.backoff_s / 2.0)
        return []

    # ------------------------------------------------------------------ #
    # Tier 2: DuckDuckGo HTML endpoint scraper
    # ------------------------------------------------------------------ #
    def _search_html_fallback(self, query: str, max_results: int) -> List[SearchHit]:
        """Scrape DuckDuckGo's lightweight HTML endpoint (no JS required)."""
        for attempt in range(2):
            try:
                self.stats["html_fallbacks"] += 1
                response = self.session.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": query},
                    timeout=self.request_timeout,
                )
                if response.status_code != 200:
                    response = self.session.get(
                        "https://html.duckduckgo.com/html/",
                        params={"q": query},
                        timeout=self.request_timeout,
                    )
                if response.status_code == 200:
                    return self.parse_ddg_html(response.text, max_results)
            except Exception as exc:
                logger.warning("DDG HTML fallback failed: %s: %s", type(exc).__name__, exc)
            jittered_backoff(attempt)
        return []

    @staticmethod
    def parse_ddg_html(page_html: str, max_results: int) -> List[SearchHit]:
        """Parse result anchors/snippets out of DDG HTML (unit-testable)."""
        hits: List[SearchHit] = []
        link_pattern = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
        snippet_pattern = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)
        links = link_pattern.findall(page_html)
        snippets = snippet_pattern.findall(page_html)
        for index, (href, anchor_html) in enumerate(links[:max_results]):
            url = ZeroCostSearch._resolve_ddg_redirect(href)
            if not url:
                continue
            title = strip_tags(anchor_html)
            snippet = strip_tags(snippets[index]) if index < len(snippets) else ""
            hits.append(SearchHit(url=url, title=title, snippet=snippet, engine="ddg-html"))
        return hits

    @staticmethod
    def _resolve_ddg_redirect(href: str) -> str:
        href = html.unescape(href).strip()
        if href.startswith("//"):
            href = "https:" + href
        parsed = urlparse(href)
        if "duckduckgo.com" in (parsed.netloc or "") and parsed.path.startswith("/l/"):
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            return unquote(target) if target else ""
        return href if parsed.scheme in ("http", "https") else ""

    # ------------------------------------------------------------------ #
    # Tier 3: Wikipedia Search API + OpenSearch + REST summaries
    # ------------------------------------------------------------------ #
    def _search_wikipedia(self, query: str, max_results: int) -> List[SearchHit]:
        """``list=search`` first (full query, then an 8-word truncated retry -
        search indexes degrade on long queries).  If the search index yields
        nothing (it throttles under load), a deeper sub-tier resolves titles
        via ``opensearch`` on progressively shorter phrases and lifts
        snippets from the REST summary endpoint, which stays available even
        when search degrades."""
        candidates = [query]
        words = query.split()
        if len(words) > 8:
            candidates.append(" ".join(words[:8]))
        for candidate in candidates:
            hits = self._wikipedia_request(candidate, max_results)
            if hits:
                return hits
        return self._wikipedia_opensearch(query, max_results)

    def _wikipedia_opensearch(self, query: str, max_results: int) -> List[SearchHit]:
        content_words = [w for w in query.split() if w.lower() not in STOPWORDS] or query.split()
        titles: List[str] = []
        for n in range(min(4, len(content_words)), 1, -1):
            phrase = " ".join(content_words[:n])
            found = self._opensearch_request(phrase)
            if found:
                titles = found[:max_results]
                break
        hits: List[SearchHit] = []
        for title in titles:
            snippet = self._rest_summary(title)
            hits.append(
                SearchHit(
                    url="https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_")),
                    title=title,
                    snippet=snippet,
                    engine="wikipedia",
                )
            )
        if hits:
            self.stats["wikipedia_opensearch"] += 1
        return hits

    def _opensearch_request(self, phrase: str) -> List[str]:
        key = "opensearch:" + phrase.lower().strip()
        cached = self._wiki_cache.get(key)
        if cached is not None:
            return list(cached)
        titles: List[str] = []
        try:
            for attempt in range(2):
                response = self.session.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "opensearch",
                        "search": phrase,
                        "limit": "5",
                        "redirects": "resolve",
                        "format": "json",
                    },
                    headers={"User-Agent": WIKIPEDIA_UA},
                    timeout=self.request_timeout,
                )
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list):
                        titles = [t for t in data[1] if isinstance(t, str)][:5]
                        break
                jittered_backoff(attempt)
        except Exception as exc:
            logger.warning("opensearch failed: %s: %s", type(exc).__name__, exc)
        self._wiki_cache[key] = titles
        return list(titles)

    def _rest_summary(self, title: str) -> str:
        key = "summary:" + title.lower().strip()
        cached = self._wiki_cache.get(key)
        if cached is not None:
            return cached[0].snippet if cached else ""
        extract = ""
        try:
            for attempt in range(2):
                response = self.session.get(
                    "https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(title.replace(" ", "_")),
                    headers={"User-Agent": WIKIPEDIA_UA},
                    timeout=self.request_timeout,
                )
                if response.status_code == 200:
                    extract = str(response.json().get("extract") or "")[:600]
                    break
                jittered_backoff(attempt)
        except Exception as exc:
            logger.warning("REST summary failed: %s: %s", type(exc).__name__, exc)
        hit = SearchHit(url="", title=title, snippet=extract, engine="wikipedia")
        self._wiki_cache[key] = [hit] if extract else []
        return extract

    def _wikipedia_request(self, query: str, max_results: int) -> List[SearchHit]:
        key = query.lower().strip()
        cached = self._wiki_cache.get(key)
        if cached is not None:
            return cached[:max_results]
        hits: List[SearchHit] = []
        try:
            self.stats["wikipedia_calls"] += 1
            params = {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": min(max(1, max_results), 10),
            }
            for attempt in range(2):
                response = self.session.get(
                    "https://en.wikipedia.org/w/api.php",
                    params=params,
                    headers={"User-Agent": WIKIPEDIA_UA},
                    timeout=self.request_timeout,
                )
                if response.status_code == 200:
                    hits = self.parse_wikipedia_payload(response.json(), max_results)
                    break
                logger.warning(
                    "Wikipedia API HTTP %s (attempt %d/2) for %r",
                    response.status_code,
                    attempt + 1,
                    query[:60],
                )
                jittered_backoff(attempt)
        except Exception as exc:
            logger.warning("Wikipedia fallback failed: %s: %s", type(exc).__name__, exc)
        self._wiki_cache[key] = hits
        return hits[:max_results]

    @staticmethod
    def parse_wikipedia_payload(data: Any, max_results: int) -> List[SearchHit]:
        """Convert a Wikipedia ``action=query&list=search`` payload to hits."""
        hits: List[SearchHit] = []
        if not isinstance(data, dict):
            return hits
        rows = (data.get("query") or {}).get("search") or []
        for row in rows[:max_results]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            if not title:
                continue
            url = "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"))
            snippet = strip_tags(str(row.get("snippet") or ""))
            hits.append(SearchHit(url=url, title=title, snippet=snippet, engine="wikipedia"))
        return hits


class PageExtractor:
    """Fetch a URL and distil it to capped, clean text via trafilatura.

    Security posture:

    * every URL and every redirect hop is validated against the SSRF deny
      policy (private ranges, loopback, link-local, cloud metadata, internal
      hostnames) **after** DNS resolution;
    * redirects are followed manually (max ``MAX_REDIRECTS``) with per-hop
      re-validation, so a redirect to an internal address is refused;
    * response bodies are streamed with a hard byte cap and content-type
      restrictions;
    * fetched text is scanned for prompt-injection signatures and sanitized
      before it may enter any prompt or claim pool.
    """

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        max_chars: int = 18000,
        request_timeout: float = 12.0,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        max_redirects: int = MAX_REDIRECTS,
        cancellation_token: Optional[Any] = None,
    ) -> None:
        self.session = session or build_pooled_session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self.max_chars = int(max_chars)
        self.request_timeout = float(request_timeout)
        self.max_response_bytes = int(max_response_bytes)
        self.max_redirects = int(max_redirects)
        self.token = cancellation_token

    # ------------------------------------------------------------------ #
    def _safe_fetch(self, url: str) -> Tuple[int, str, str, str]:
        """Fetch with per-hop SSRF validation.

        Returns ``(status, final_url, content_type, text)``; raises
        :class:`UnsafeURLError` for policy violations and ``RuntimeError``
        for transport problems.
        """
        current = url
        for _hop in range(self.max_redirects + 1):
            if self.token is not None and self.token.is_cancelled:
                raise RuntimeError("fetch cancelled")
            safe = validate_url(current, resolve_dns=True)  # SSRF gate (re-runs each hop)
            # Anti-rebinding: the connection may only use the VALIDATED IPs.
            with pinned_resolution(safe.host, safe.resolved_ips):
                response = self.session.get(
                    current,
                    timeout=self.request_timeout,
                    allow_redirects=False,
                    stream=True,
                )
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location", "")
                response.close()
                if not location:
                    raise RuntimeError("redirect without Location header")
                current = urljoin(current, location)
                continue
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            declared_length = response.headers.get("Content-Length")
            if (
                declared_length
                and declared_length.isdigit()
                and int(declared_length) > self.max_response_bytes
            ):
                response.close()
                raise RuntimeError(f"declared body size {declared_length} exceeds limit")
            if content_type and content_type not in ALLOWED_CONTENT_TYPES:
                response.close()
                raise RuntimeError(f"content-type '{content_type}' not allowed")
            chunks: List[bytes] = []
            total = 0
            try:
                for chunk in response.iter_content(chunk_size=65536):
                    total += len(chunk)
                    if total > self.max_response_bytes:
                        raise RuntimeError("response body exceeded byte cap")
                    chunks.append(chunk)
            finally:
                response.close()
            body = b"".join(chunks).decode("utf-8", "replace")
            return response.status_code, current, content_type, body
        raise RuntimeError(f"too many redirects (> {self.max_redirects})")

    # ------------------------------------------------------------------ #
    def extract(self, url: str) -> ExtractedPage:
        """Return a capped text envelope for ``url`` (never raises)."""
        url = (url or "").strip()
        if not url:
            return ExtractedPage(url=url, ok=False, error="invalid or missing URL")
        try:
            validate_url(url, resolve_dns=False)  # cheap structural gate first
        except UnsafeURLError as exc:
            return ExtractedPage(url=url, ok=False, error=f"unsafe URL rejected: {exc}", quarantined=True)
        try:
            status, final_url, content_type, raw_html = self._safe_fetch(url)
        except UnsafeURLError as exc:
            # Redirect chain or DNS resolved somewhere forbidden.
            return ExtractedPage(url=url, ok=False, error=f"unsafe fetch refused: {exc}", quarantined=True)
        except Exception as exc:
            return ExtractedPage(url=url, ok=False, error=f"{type(exc).__name__}: {exc}")

        if status >= 400:
            return ExtractedPage(
                url=url, ok=False, error=f"HTTP {status}", http_status=status, resolved_url=final_url
            )

        title_match = _TITLE_RE.search(raw_html)
        title = strip_tags(title_match.group(1))[:200] if title_match else ""

        text = ""
        engine = "none"
        if _HAS_TRAFILATURA:
            try:
                extracted = _trafilatura.extract(
                    raw_html,
                    include_comments=False,
                    include_tables=True,
                    favor_precision=True,
                )
                if extracted:
                    text = extracted
                    engine = "trafilatura"
            except Exception as exc:
                logger.warning("trafilatura failed for %s: %s", url, exc)
        if not text:
            text = self._naive_extract(raw_html)
            engine = "naive-tag-strip"

        text = _WHITESPACE_RE.sub(" ", text).strip()
        if len(text) > self.max_chars:  # context-bloat cap
            text = text[: self.max_chars].rsplit(" ", 1)[0] + " [...capped]"

        # Trust boundary: scan before this content can reach any prompt.
        scan = scan_for_injection(text)
        text = sanitize_external_text(text, max_chars=self.max_chars)
        quarantined = scan.detected
        if quarantined:
            logger.warning("prompt-injection signatures detected at %s: %s", final_url, scan.findings[:2])
        return ExtractedPage(
            url=url,
            ok=bool(text),
            text=text,
            title=sanitize_external_text(title, max_chars=200),
            chars=len(text),
            engine=engine,
            error=None if text else "no extractable text",
            resolved_url=final_url,
            http_status=status,
            content_hash=content_hash(text),
            content_type=content_type,
            quarantined=quarantined,
            injection_findings=scan.findings[:4],
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _naive_extract(raw_html: str) -> str:
        cleaned = _SCRIPT_STYLE_RE.sub(" ", raw_html)
        cleaned = re.sub(r"<(header|footer|nav|form|aside)[^>]*>.*?</\1>", " ", cleaned, flags=re.S | re.I)
        cleaned = strip_tags(cleaned)
        lines = [line.strip() for line in cleaned.splitlines()]
        return "\n".join(line for line in lines if len(line) > 25)
