"""Source classification, quality ranking, deduplication and independence.

Pipeline stages (explicitly separated):

1. discovery        - search tiers return candidate hits
2. relevance filter - bigram/token gate (odar.retrieval)
3. source class     - publisher/type classification (this module)
4. quality scoring  - class weights + topical relevance
5. deduplication    - content-hash + near-duplicate similarity
6. independence     - publisher-domain clustering

Wikipedia and generic web pages are *never* silently treated as primary
research: classes carry explicit weights and the downstream policy reads
them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence
from urllib.parse import urlparse

from odar.evidence import content_hash, span_similarity
from odar.loop_breaker import STOPWORDS, tokenize

# Source classes with explicit quality weights.  Wikipedia and generic web
# pages are *never* silently treated as primary research: the downstream
# policy reads these weights.
CLASS_WEIGHTS: Dict[str, float] = {
    "primary_research": 1.0,
    "systematic_review": 1.0,
    "meta_analysis": 1.0,
    "government": 0.85,
    "university": 0.75,
    "professional_organization": 0.7,
    "reputable_secondary": 0.6,
    "encyclopedia": 0.45,
    "news": 0.4,
    "general_web": 0.3,
    "blog": 0.2,
    "forum": 0.15,
    "unknown": 0.2,
}

_PUBMED_DOMAINS = {"pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "europepmc.org"}
_PREPRINT_OR_STUDY_HINTS = ("study", "trial", "randomized", "cohort", "rct")
_GOVERNMENT_TLDS = (".gov", ".gov.uk", ".gov.au", ".gov.in", ".gc.ca", ".europa.eu")
_UNIVERSITY_TLDS = (".edu", ".ac.uk", ".ac.in", ".edu.au", ".ac.jp", ".edu.cn")
_PROFESSIONAL_DOMAINS = {
    "who.int",
    "nih.gov",
    "cdc.gov",
    "mayoclinic.org",
    "nhs.uk",
    "cochranelibrary.com",
    "cochrane.org",
    "bmj.com",
    "thelancet.com",
    "nejm.org",
    "jamanetwork.com",
    "nature.com",
    "sciencedirect.com",
    "springer.com",
    "wiley.com",
    "apa.org",
    "heart.org",
    "diabetes.org",
}
_REPUTABLE_SECONDARY_DOMAINS = {
    "en.wikipedia.org",  # classified separately as encyclopedia below
    "reuters.com",
    "apnews.com",
    "nature.com",
}
_NEWS_DOMAINS = {
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "bbc.co.uk",
    "nytimes.com",
    "theguardian.com",
    "washingtonpost.com",
    "economist.com",
    "cnn.com",
    "ndtv.com",
    "thehindu.com",
}
_FORUM_HOSTS = {"reddit.com", "news.ycombinator.com", "quora.com", "stackoverflow.com"}
_BLOG_HINTS = ("blog.", "medium.com", "substack.com", "wordpress.com", "blogspot.")


def domain_of(url: str) -> str:
    netloc = urlparse(url or "").netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def classify_source(url: str, title: str = "", snippet: str = "") -> str:
    """Assign a source class from URL structure and on-page signals."""
    domain = domain_of(url)
    host = urlparse(url or "").netloc.lower()
    text = f"{title} {snippet}".lower()

    if any(domain == d or domain.endswith("." + d) for d in _PUBMED_DOMAINS):
        return "primary_research"
    if "cochrane" in domain or "systematic review" in text or "meta-analysis" in text:
        return "meta_analysis" if "meta-analysis" in text or "cochrane" in domain else "systematic_review"
    if any(domain.endswith(tld) for tld in _GOVERNMENT_TLDS):
        return "government"
    if any(domain.endswith(tld) for tld in _UNIVERSITY_TLDS):
        return "university"
    if any(domain == d or domain.endswith("." + d) for d in _PROFESSIONAL_DOMAINS):
        return "professional_organization"
    if host == "en.wikipedia.org" or domain.endswith(".wikipedia.org"):
        return "encyclopedia"
    if any(domain == d or domain.endswith("." + d) for d in _FORUM_HOSTS):
        return "forum"
    if any(hint in domain for hint in _BLOG_HINTS):
        return "blog"
    if any(domain == d or domain.endswith("." + d) for d in _NEWS_DOMAINS):
        return "news"
    if any(hint in text for hint in _PREPRINT_OR_STUDY_HINTS) and (
        "jama" in domain or "bmj" in domain or "lancet" in domain or "nejm" in domain
    ):
        return "primary_research"
    if domain:
        return "general_web"
    return "unknown"


def quality_weight(source_class: str) -> float:
    return CLASS_WEIGHTS.get(source_class, CLASS_WEIGHTS["unknown"])


@dataclass
class RankedSource:
    url: str
    title: str
    snippet: str
    source_class: str
    weight: float
    relevance: int
    independent: bool = True
    duplicate_of: Optional[str] = None
    content_digest: str = ""


def relevance_of(query: str, title: str, snippet: str) -> int:
    """Bigram + token relevance score (same gate metric as retrieval)."""
    q_tokens = [t for t in tokenize(query) if t not in STOPWORDS and len(t) > 2]
    q_set = set(q_tokens)
    q_bigrams = {(q_tokens[i], q_tokens[i + 1]) for i in range(len(q_tokens) - 1)}
    text_tokens = tokenize(f"{title} {snippet}")
    overlap = len(set(text_tokens) & q_set)
    bigrams = sum(1 for i in range(len(text_tokens) - 1) if (text_tokens[i], text_tokens[i + 1]) in q_bigrams)
    return 2 * bigrams + overlap


def rank_sources(query: str, hits: Sequence, near_dup_threshold: float = 0.85) -> List[RankedSource]:
    """Classify, score, deduplicate and independence-mark candidate hits.

    ``hits`` are objects with ``url/title/snippet`` (SearchHit-compatible).
    Near-duplicate pages (content similarity on title+snippet, or identical
    digest) are marked ``duplicate_of`` and excluded from independence.
    """
    ranked: List[RankedSource] = []
    for hit in hits:
        source_class = classify_source(hit.url, hit.title, hit.snippet)
        ranked.append(
            RankedSource(
                url=hit.url,
                title=hit.title or "",
                snippet=hit.snippet or "",
                source_class=source_class,
                weight=quality_weight(source_class),
                relevance=relevance_of(query, hit.title or "", hit.snippet or ""),
                content_digest=content_hash(f"{hit.title or ''}|{hit.snippet or ''}")[:16],
            )
        )

    # Deduplication + independence (publisher-domain clustering).
    for i, candidate in enumerate(ranked):
        for prior in ranked[:i]:
            if prior.duplicate_of:
                continue
            if (
                candidate.content_digest == prior.content_digest
                or span_similarity(f"{candidate.title} {candidate.snippet}", f"{prior.title} {prior.snippet}")
                >= near_dup_threshold
            ):
                candidate.duplicate_of = prior.url
                candidate.independent = False
                break
        if (
            candidate.independent
            and domain_of(candidate.url)
            and any(
                other.independent
                and other.url != candidate.url
                and domain_of(other.url) == domain_of(candidate.url)
                for other in ranked[:i]
            )
        ):
            # Same publisher already counted once: keep the page but note it
            # does not add an independent source.
            candidate.independent = False

    ranked.sort(key=lambda r: (r.duplicate_of is not None, -r.weight, -r.relevance))
    return ranked


# ---------------------------------------------------------------------- #
# User-facing quality labels (web app, ODAR Check)
# ---------------------------------------------------------------------- #
QUALITY_LABELS = (
    "peer-reviewed",
    "preprint",
    "government",
    "academic",
    "organization",
    "encyclopedia",
    "news",
    "blog",
    "forum",
    "web",
)
_PREPRINT_HOSTS = (
    "arxiv.org",
    "biorxiv.org",
    "medrxiv.org",
    "ssrn.com",
    "researchsquare.com",
    "preprints.org",
    "osf.io",
    "chemrxiv.org",
    "psyarxiv.com",
)
_JOURNAL_HOSTS = (
    "doi.org",
    "nature.com",
    "sciencedirect.com",
    "springer.com",
    "link.springer.com",
    "wiley.com",
    "bmj.com",
    "thelancet.com",
    "nejm.org",
    "jamanetwork.com",
    "plos.org",
    "frontiersin.org",
    "mdpi.com",
    "tandfonline.com",
    "sagepub.com",
    "academic.oup.com",
    "cell.com",
    "science.org",
    "pnas.org",
    "ieeexplore.ieee.org",
    "dl.acm.org",
    "aclanthology.org",
    "pubs.acs.org",
    "journals.plos.org",
    "cochranelibrary.com",
    "annualreviews.org",
    "ncbi.nlm.nih.gov",
    "pubmed.ncbi.nlm.nih.gov",
    "europepmc.org",
    "jstor.org",
    "proceedings.neurips.cc",
    "openreview.net",
    "cambridge.org",
)
_GOV_EXTRA = (".gov", ".gov.in", ".nic.in", ".gov.uk", ".gov.au", ".gc.ca", ".europa.eu", ".mil", ".int")
_ORG_EXTRA = ("who.int", "un.org", "worldbank.org", "imf.org", "oecd.org", "ipcc.ch", "unicef.org")
_NEWS_EXTRA = (
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "npr.org",
    "aljazeera.com",
    "indianexpress.com",
    "hindustantimes.com",
    "timesofindia.indiatimes.com",
    "livemint.com",
    "scroll.in",
    "theverge.com",
    "wired.com",
    "techcrunch.com",
    "arstechnica.com",
    "forbes.com",
    "cnbc.com",
    "axios.com",
    "politico.com",
    "time.com",
    "theatlantic.com",
    "vox.com",
    "nbcnews.com",
    "abcnews.go.com",
    "cbsnews.com",
    "usatoday.com",
    "latimes.com",
)
_FORUM_EXTRA = (
    "stackexchange.com",
    "quora.com",
    "reddit.com",
    "news.ycombinator.com",
    "stackoverflow.com",
    "discourse",
    "forum",
    "community.",
)
_BLOG_EXTRA = (
    "medium.com",
    "substack.com",
    "wordpress.com",
    "blogspot.",
    "dev.to",
    "hashnode",
    "ghost.io",
    "tumblr.com",
    "blog.",
    "/blog",
)


def _host_match(domain: str, hosts: Sequence[str]) -> bool:
    return any(domain == h or domain.endswith("." + h) for h in hosts)


def quality_label(url: str, title: str = "") -> str:
    """One plain-language label for how much weight a source deserves."""
    domain = domain_of(url)
    lower_url = (url or "").lower()
    if not domain:
        return "web"
    if _host_match(domain, _PREPRINT_HOSTS):
        return "preprint"
    if _host_match(domain, _JOURNAL_HOSTS):
        return "peer-reviewed"
    if _host_match(domain, _ORG_EXTRA):
        return "organization"
    if any(domain.endswith(t) for t in _GOV_EXTRA):
        return "government"
    if any(domain.endswith(t) for t in _UNIVERSITY_TLDS) or domain.endswith(".edu"):
        return "academic"
    if domain.endswith("wikipedia.org") or domain in ("britannica.com", "www.britannica.com"):
        return "encyclopedia"
    if _host_match(domain, _FORUM_EXTRA) or any(h in domain for h in ("forum", "community.")):
        return "forum"
    if any(h in domain for h in _BLOG_EXTRA[:-2]) or domain.startswith("blog.") or "/blog" in lower_url:
        return "blog"
    if _host_match(domain, tuple(_NEWS_DOMAINS)) or _host_match(domain, _NEWS_EXTRA):
        return "news"
    cls = classify_source(url, title)
    return {
        "primary_research": "peer-reviewed",
        "systematic_review": "peer-reviewed",
        "meta_analysis": "peer-reviewed",
        "government": "government",
        "university": "academic",
        "professional_organization": "organization",
        "encyclopedia": "encyclopedia",
        "news": "news",
        "blog": "blog",
        "forum": "forum",
    }.get(cls, "web")
