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
import os
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

# Single DDGS engines tried in turn when "auto" returns nothing (2026-10-10:
# auto often times out on its DuckDuckGo leg from datacenter IPs while a
# single other engine still answers).  Names ddgs does not know are skipped.
DDGS_ROTATION = ("brave", "mojeek", "yahoo", "startpage", "google", "duckduckgo")
DDGS_ENGINE_TIMEOUT_S = 6
DDGS_ROTATION_BUDGET_S = 18.0

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
# Inline (phrasing) tags are dropped WITHOUT a space, so "<code>0.2</code>." or
# "0<b>.</b>2" stays "0.2." / "0.2" - a space there split figures into "0 . 2"
# and fused punctuation into "3 .".  Block tags still become whitespace.
_INLINE_TAG_RE = re.compile(
    r"</?(?:a|abbr|b|bdi|bdo|cite|code|data|dfn|em|font|i|kbd|mark|q|s|samp|small|span|strong|"
    r"sub|sup|time|tt|u|var|wbr)\b[^>]*>",
    re.I,
)


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


def broaden_query(query: str, keep: int = 4) -> str:
    """Shorter, plainer form of an over-specific query: identifiers split on
    underscores, filler words dropped, at most ``keep`` content words."""
    words = re.sub(r"[_/]+", " ", query or "").split()
    content = [w for w in words if w.lower().strip("\"'?,.") not in STOPWORDS]
    return " ".join(content[:keep]).strip()


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
        # Endpoints that block datacenter IPs (proxy 502s) must fail in one
        # round-trip, not after the pooled session's 3 transport retries.
        self._fast_session = session or build_pooled_session(retries=0)
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
    def text(self, query: str, max_results: int = 6, _broadened: bool = False) -> List[SearchHit]:
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
        for tier in (
            self._search_ddgs,
            self._search_html_fallback,
            self._search_optional,
            self._search_wikipedia,
        ):
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

        if not collected and not _broadened:
            broader = broaden_query(query)
            if broader and broader != query:
                # "Start wide, then narrow": an over-specific query that every
                # engine answers with nothing gets one shorter retry.
                self.stats["broadened_queries"] = self.stats.get("broadened_queries", 0) + 1
                return self.text(broader, max_results, _broadened=True)

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
        hits = self._search_ddgs_auto(query, max_results)
        if hits or _DDGS_BACKEND != "ddgs":
            return hits
        # "auto" draws a random subset of engines; when all of them are
        # throttled the query returns nothing.  Rotate through single engines
        # (most fail in ~0.1-2 s) under a small wall-clock budget.
        started = time.monotonic()
        for backend in DDGS_ROTATION:
            if time.monotonic() - started > DDGS_ROTATION_BUDGET_S:
                break
            try:
                self.stats["ddgs_calls"] += 1
                raw = (
                    _DDGS(timeout=DDGS_ENGINE_TIMEOUT_S).text(query, max_results=max_results, backend=backend)
                    or []
                )
            except Exception as exc:  # noqa: BLE001 - "No results found" is an exception in ddgs
                logger.info("DDGS backend %s: %s", backend, str(exc)[:80])
                continue
            hits = self._ddgs_rows(raw, engine=f"ddgs:{backend}")
            if hits:
                self.stats["ddgs_rotation_hits"] = self.stats.get("ddgs_rotation_hits", 0) + 1
                return hits
        return []

    @staticmethod
    def _ddgs_rows(raw_results: Any, engine: str) -> List[SearchHit]:
        hits: List[SearchHit] = []
        for row in raw_results or []:
            if not isinstance(row, dict):
                continue
            url = row.get("href") or row.get("url") or row.get("link") or ""
            title = str(row.get("title") or "").strip()
            snippet = str(row.get("body") or row.get("snippet") or "").strip()
            if url:
                hits.append(SearchHit(url=url, title=title, snippet=snippet, engine=engine))
        return hits

    def _search_ddgs_auto(self, query: str, max_results: int) -> List[SearchHit]:
        # With the ddgs successor package one "auto" attempt is enough: the
        # engine rotation that follows is the retry.
        attempts = 1 if _DDGS_BACKEND == "ddgs" else self.max_retries
        for attempt in range(attempts):
            try:
                self.stats["ddgs_calls"] += 1
                raw_results = _DDGS(timeout=DDGS_ENGINE_TIMEOUT_S).text(query, max_results=max_results) or []
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
                response = self._fast_session.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": query},
                    timeout=(4, 8),
                )
                if response.status_code != 200:
                    response = self._fast_session.get(
                        "https://html.duckduckgo.com/html/",
                        params={"q": query},
                        timeout=self.request_timeout,
                    )
                if response.status_code == 200:
                    return self.parse_ddg_html(response.text, max_results)
                if response.status_code in (202, 403, 429):
                    # bot challenge / block: retrying only burns wall-clock
                    logger.info("DDG HTML endpoint challenged (HTTP %s)", response.status_code)
                    return []
            except Exception as exc:
                # proxy 502 / timeout: the endpoint is unreachable from here, a
                # second attempt only adds latency to every search
                logger.warning("DDG HTML fallback failed: %s: %s", type(exc).__name__, exc)
                return []
            jittered_backoff(attempt)
        return []

    # ------------------------------------------------------------------ #
    # Opt-in tiers (free, keyless; licence terms restrict them)
    # ------------------------------------------------------------------ #
    def _search_optional(self, query: str, max_results: int) -> List[SearchHit]:
        """Bing's RSS endpoint (``ODAR_SEARCH_BING_RSS=1``; Microsoft limits it to
        personal, non-commercial use) and Marginalia's public API
        (``ODAR_SEARCH_MARGINALIA=1``; results are CC-BY-NC-SA).  Both are off by
        default so a commercial deployment never uses them by accident."""
        hits: List[SearchHit] = []
        if os.environ.get("ODAR_SEARCH_BING_RSS", "") == "1":
            hits += self._search_bing_rss(query, max_results)
        if len(hits) < max_results and os.environ.get("ODAR_SEARCH_MARGINALIA", "") == "1":
            hits += self._search_marginalia(query, max_results)
        return hits

    def _search_bing_rss(self, query: str, max_results: int) -> List[SearchHit]:
        try:
            response = self.session.get(
                "https://www.bing.com/search",
                params={"q": query, "format": "rss"},
                timeout=self.request_timeout,
            )
            if response.status_code != 200:
                return []
            self.stats["bing_rss_calls"] = self.stats.get("bing_rss_calls", 0) + 1
            return self.parse_rss_items(response.text, max_results, engine="bing-rss")
        except Exception as exc:  # noqa: BLE001
            logger.info("Bing RSS failed: %s", exc)
            return []

    @staticmethod
    def parse_rss_items(xml_text: str, max_results: int, engine: str) -> List[SearchHit]:
        hits: List[SearchHit] = []
        for item in re.findall(r"<item>(.*?)</item>", xml_text or "", re.S)[:max_results]:
            link = re.search(r"<link>(.*?)</link>", item, re.S)
            title = re.search(r"<title>(.*?)</title>", item, re.S)
            desc = re.search(r"<description>(.*?)</description>", item, re.S)
            url = html.unescape(link.group(1).strip()) if link else ""
            if url.startswith(("http://", "https://")):
                hits.append(
                    SearchHit(
                        url=url,
                        title=strip_tags(html.unescape(title.group(1))) if title else "",
                        snippet=strip_tags(html.unescape(desc.group(1))) if desc else "",
                        engine=engine,
                    )
                )
        return hits

    def _search_marginalia(self, query: str, max_results: int) -> List[SearchHit]:
        try:
            response = self.session.get(
                "https://api.marginalia.nu/public/search/" + quote(query),
                params={"count": str(max_results)},
                timeout=self.request_timeout,
            )
            if response.status_code != 200:
                return []
            rows = (response.json() or {}).get("results") or []
        except Exception as exc:  # noqa: BLE001
            logger.info("Marginalia failed: %s", exc)
            return []
        return [
            SearchHit(
                url=str(r.get("url")),
                title=str(r.get("title") or ""),
                snippet=str(r.get("description") or ""),
                engine="marginalia",
            )
            for r in rows[:max_results]
            if isinstance(r, dict) and str(r.get("url") or "").startswith(("http://", "https://"))
        ]

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


PDF_CONTENT_TYPE = "application/pdf"
MAX_PDF_BYTES = 25_000_000
MAX_PDF_PAGES = 80


def _pdf_to_text(data: bytes, max_pages: int = MAX_PDF_PAGES) -> str:
    """Extract text from a PDF body; errors come back as a NUL-prefixed marker."""
    try:
        import io

        from pypdf import PdfReader  # optional dependency (pip install odar[pdf])
    except ImportError:
        return "\x00pdf-error: PDF support needs the optional 'pypdf' package"
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return "\x00pdf-error: encrypted PDF"
        parts = []
        for page in reader.pages[: max(1, int(max_pages))]:
            parts.append(page.extract_text() or "")
        return "\n".join(parts)
    except Exception as exc:  # noqa: BLE001 - malformed PDFs are common
        return f"\x00pdf-error: unreadable PDF ({type(exc).__name__})"


_CHALLENGE_RE = re.compile(
    r"enable (?:cookies|javascript)|verify (?:you are|that you're) (?:a )?human|are you a robot|"
    r"checking your browser|just a moment|access denied|captcha",
    re.IGNORECASE,
)
_PUBMED_RE = re.compile(r"^https?://pubmed\.ncbi\.nlm\.nih\.gov/(\d+)/?(?:[?#].*)?$", re.IGNORECASE)


def _readable_mirror(url: str) -> str:
    """Official machine-readable copy of pages that wall off scripted readers.

    PubMed now answers non-browser clients with a cookie wall; NCBI's E-utilities
    serve the same abstract as plain text.
    """
    m = _PUBMED_RE.match(url)
    if m:
        return (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            f"?db=pubmed&id={m.group(1)}&rettype=abstract&retmode=text"
        )
    return url


JINA_READER = "https://r.jina.ai/"
WAYBACK_AVAILABLE = "https://archive.org/wayback/available?url="
_FALLBACK_STATUSES = frozenset({401, 403, 406, 429, 451, 503})
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((?:[^()]|\([^)]*\))*\)")


def _should_fall_back(page: ExtractedPage) -> bool:
    if page.http_status in _FALLBACK_STATUSES:
        return True
    error = (page.error or "").lower()
    if (
        page.http_status is None
        and error
        and not any(w in error for w in ("unsafe", "content-type", "cancel"))
    ):
        return True  # timeout / connection reset / TLS failure
    return "challenge" in error or "no extractable text" in error


def parse_reader_markdown(body: str) -> Tuple[str, str]:
    """(title, plain text) from a Jina Reader response; ('', '') when the reader
    itself was walled off (it reports CAPTCHA / errors in a Warning line)."""
    title = ""
    head, sep, content = (body or "").partition("Markdown Content:")
    if not sep:
        head, content = "", body or ""
    m = re.search(r"^Title:\s*(.+)$", head, re.M)
    if m:
        title = m.group(1).strip()
    if re.search(r"^Warning:.*(captcha|blocked|403|forbidden|not authorized|error)", head, re.M | re.I):
        return title, ""
    text = _MD_IMAGE_RE.sub(" ", content)
    text = _MD_LINK_RE.sub(lambda mm: mm.group(1), text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^\s{0,3}(?:#{1,6}\s+|[*+-]\s+|>\s?)", "", line).replace("**", "").replace("__", "")
        line = re.sub(r"[ \t]{2,}", " ", line).strip()
        if line and not re.fullmatch(r"[-=_*|: ]{3,}", line):
            lines.append(line)
    return title, "\n".join(lines).strip()


def parse_wayback_available(body: str) -> Tuple[str, str]:
    """(raw snapshot url, timestamp) from the availability API, or ('', '')."""
    import json as _json

    try:
        data = _json.loads(body or "{}")
    except ValueError:
        return "", ""
    closest = ((data or {}).get("archived_snapshots") or {}).get("closest") or {}
    if not closest.get("available") or str(closest.get("status", "200")) != "200":
        return "", ""
    stamp = str(closest.get("timestamp") or "")
    original = str((data or {}).get("url") or "")
    snap = str(closest.get("url") or "")
    if not (stamp.isdigit() and snap):
        return "", ""
    target = snap.split(stamp, 1)[1].lstrip("/") if stamp in snap else original
    # ``id_`` = the archived bytes without the Wayback toolbar / rewriting.
    return f"https://web.archive.org/web/{stamp}id_/{target}", stamp


class _ReaderThrottle:
    """Keyless Jina Reader allows 20 requests/min per IP: keep >= 3.1 s apart."""

    _lock = __import__("threading").Lock()
    _last = 0.0
    gap_s = 3.1

    @classmethod
    def wait(cls) -> None:
        with cls._lock:
            delay = cls._last + cls.gap_s - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            cls._last = time.monotonic()


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
        allow_pdf: bool = False,
        max_pdf_bytes: int = MAX_PDF_BYTES,
        max_pdf_pages: int = MAX_PDF_PAGES,
    ) -> None:
        # An injected session (tests, custom transports) also carries the
        # fallback routes, so nothing escapes it; otherwise fallbacks use a
        # plain no-retry request (one attempt, short timeout).
        self._fallback_get: Any = session.get if session is not None else requests.get
        self.session = session or build_pooled_session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self.max_chars = int(max_chars)
        self.request_timeout = float(request_timeout)
        self.max_response_bytes = int(max_response_bytes)
        self.max_redirects = int(max_redirects)
        self.token = cancellation_token
        # PDF support is opt-in (ODAR Check): binary bodies get their own,
        # larger byte cap and a page cap so a hostile PDF cannot stall parsing.
        self.allow_pdf = bool(allow_pdf)
        self.max_pdf_bytes = int(max_pdf_bytes)
        self.max_pdf_pages = int(max_pdf_pages)

    # ------------------------------------------------------------------ #
    def _safe_fetch(self, url: str) -> Tuple[int, str, str, str]:
        status, final_url, content_type, body = self._safe_fetch_bytes(url)
        if content_type == PDF_CONTENT_TYPE:
            return status, final_url, content_type, _pdf_to_text(body, self.max_pdf_pages)
        return status, final_url, content_type, body.decode("utf-8", "replace")

    def _safe_fetch_bytes(self, url: str) -> Tuple[int, str, str, bytes]:
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
            is_pdf = self.allow_pdf and (
                content_type == PDF_CONTENT_TYPE
                or (
                    content_type in ("", "application/octet-stream")
                    and urlparse(current).path.lower().endswith(".pdf")
                )
            )
            if is_pdf:
                content_type = PDF_CONTENT_TYPE
            byte_cap = self.max_pdf_bytes if is_pdf else self.max_response_bytes
            declared_length = response.headers.get("Content-Length")
            if declared_length and declared_length.isdigit() and int(declared_length) > byte_cap:
                response.close()
                raise RuntimeError(f"declared body size {declared_length} exceeds limit")
            if content_type and not is_pdf and content_type not in ALLOWED_CONTENT_TYPES:
                response.close()
                raise RuntimeError(f"content-type '{content_type}' not allowed")
            chunks: List[bytes] = []
            total = 0
            try:
                for chunk in response.iter_content(chunk_size=65536):
                    total += len(chunk)
                    if total > byte_cap:
                        raise RuntimeError("response body exceeded byte cap")
                    chunks.append(chunk)
            finally:
                response.close()
            return response.status_code, current, content_type, b"".join(chunks)
        raise RuntimeError(f"too many redirects (> {self.max_redirects})")

    # ------------------------------------------------------------------ #
    def extract(self, url: str) -> ExtractedPage:
        """Return a capped text envelope for ``url`` (never raises).

        When the live page refuses scripted readers (401/403/406/429/451/503,
        a bot-challenge page, or a transport failure) the extractor falls back,
        in order, to the Jina Reader (``r.jina.ai``, free and keyless at 20
        requests/min) and the Internet Archive's closest snapshot.  The page
        keeps its ORIGINAL url for citation; ``engine`` records the route
        (``jina-reader`` / ``wayback:YYYYMMDD``) so the report can say how it was
        read.  Quarantined (injection / SSRF) pages never fall back.
        """
        page = self._extract_direct(url)
        if page.ok or page.quarantined or not _should_fall_back(page):
            return page
        for name, enabled, route in (
            ("reader", os.environ.get("ODAR_READER_FALLBACK", "1") != "0", self._via_reader),
            ("wayback", os.environ.get("ODAR_WAYBACK_FALLBACK", "1") != "0", self._via_wayback),
        ):
            if not enabled:
                continue
            try:
                alt = route(page.url)
            except UnsafeURLError as exc:
                return ExtractedPage(
                    url=page.url, ok=False, error=f"unsafe URL rejected: {exc}", quarantined=True
                )
            except Exception as exc:  # noqa: BLE001 - a dead fallback never masks the original error
                logger.info("%s fallback failed for %s: %s", name, page.url[:80], exc)
                continue
            if alt is not None and (alt.ok or alt.quarantined):
                return alt
        return page

    # ------------------------------------------------------------------ #
    def _via_reader(self, url: str) -> Optional[ExtractedPage]:
        validate_url(url, resolve_dns=True)  # never proxy a private / internal target
        _ReaderThrottle.wait()
        # One attempt, no retry adapter: a reader that cannot render the page in
        # ~15 s will not do better on a retry, and the Wayback route is next.
        response = self._fallback_get(
            JINA_READER + url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/plain", "X-Timeout": "12"},
            timeout=(5, 16),
            allow_redirects=False,
            stream=True,
        )
        try:
            status = response.status_code
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            body = response.raw.read(self.max_response_bytes + 1, decode_content=True) or b""
        finally:
            response.close()
        if status >= 400 or not body or len(body) > self.max_response_bytes:
            return None
        title, text = parse_reader_markdown(body.decode("utf-8", "replace"))
        if not text:
            return None
        return self._finish(url, text, title, "jina-reader", status, url, content_type or "text/plain")

    def _via_wayback(self, url: str) -> Optional[ExtractedPage]:
        validate_url(url, resolve_dns=True)
        response = self._fallback_get(
            WAYBACK_AVAILABLE + quote(url, safe=""), headers={"User-Agent": USER_AGENT}, timeout=(5, 12)
        )
        if response.status_code != 200:
            return None
        snap_url, stamp = parse_wayback_available(response.text[:20000])
        if not snap_url:
            return None
        status, _final, content_type, raw = self._safe_fetch(snap_url)
        if status >= 400:
            return None
        text = self._html_to_text(raw, url)
        title_match = _TITLE_RE.search(raw)
        title = strip_tags(title_match.group(1))[:200] if title_match else ""
        return self._finish(url, text, title, f"wayback:{stamp[:8]}", status, url, content_type)

    def _html_to_text(self, raw_html: str, url: str) -> str:
        if _HAS_TRAFILATURA:
            try:
                extracted = _trafilatura.extract(
                    raw_html, include_comments=False, include_tables=True, favor_precision=True
                )
                if extracted:
                    return extracted
            except Exception as exc:  # noqa: BLE001
                logger.warning("trafilatura failed for %s: %s", url, exc)
        return self._naive_extract(raw_html)

    def _finish(
        self,
        url: str,
        text: str,
        title: str,
        engine: str,
        status: int,
        final_url: str,
        content_type: str,
    ) -> ExtractedPage:
        """Shared tail: challenge check, cap, injection scan, envelope."""
        text = _WHITESPACE_RE.sub(" ", text or "").strip()
        if len(text) < 600 and _CHALLENGE_RE.search(text):
            return ExtractedPage(
                url=url, ok=False, error="bot challenge page", http_status=status, resolved_url=final_url
            )
        if len(text) > self.max_chars:
            text = text[: self.max_chars].rsplit(" ", 1)[0] + " [...capped]"
        scan = scan_for_injection(text)
        text = sanitize_external_text(text, max_chars=self.max_chars)
        if scan.detected:
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
            quarantined=scan.detected,
            injection_findings=scan.findings[:4],
        )

    # ------------------------------------------------------------------ #
    def _extract_direct(self, url: str) -> ExtractedPage:
        url = (url or "").strip()
        if not url:
            return ExtractedPage(url=url, ok=False, error="invalid or missing URL")
        try:
            validate_url(url, resolve_dns=False)  # cheap structural gate first
        except UnsafeURLError as exc:
            return ExtractedPage(url=url, ok=False, error=f"unsafe URL rejected: {exc}", quarantined=True)
        try:
            status, final_url, content_type, raw_html = self._safe_fetch(_readable_mirror(url))
        except UnsafeURLError as exc:
            # Redirect chain or DNS resolved somewhere forbidden.
            return ExtractedPage(url=url, ok=False, error=f"unsafe fetch refused: {exc}", quarantined=True)
        except Exception as exc:
            return ExtractedPage(url=url, ok=False, error=f"{type(exc).__name__}: {exc}")

        if status >= 400:
            return ExtractedPage(
                url=url, ok=False, error=f"HTTP {status}", http_status=status, resolved_url=final_url
            )

        title_match = _TITLE_RE.search(raw_html) if content_type != PDF_CONTENT_TYPE else None
        title = strip_tags(title_match.group(1))[:200] if title_match else ""

        text = ""
        engine = "none"
        if content_type == PDF_CONTENT_TYPE:
            text, engine = raw_html, "pypdf"
            if text.startswith("\x00pdf-error:"):
                return ExtractedPage(
                    url=url, ok=False, error=text[1:], http_status=status, resolved_url=final_url
                )
        elif _HAS_TRAFILATURA:
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
        if not text and content_type != PDF_CONTENT_TYPE:
            text = self._naive_extract(raw_html)
            engine = "naive-tag-strip"

        text = _WHITESPACE_RE.sub(" ", text).strip()
        if len(text) < 600 and _CHALLENGE_RE.search(text):
            # a cookie / JavaScript / captcha wall, not the page a person sees
            return ExtractedPage(
                url=url, ok=False, error="bot challenge page", http_status=status, resolved_url=final_url
            )
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
        cleaned = strip_tags(_INLINE_TAG_RE.sub("", cleaned))
        lines = [line.strip() for line in cleaned.splitlines()]
        return "\n".join(line for line in lines if len(line) > 25)
