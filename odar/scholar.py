"""Academic sources: free scholarly APIs, reference validation, bibliographies.

Providers (all free; no paid key needed):

* Crossref   - DOI lookup + bibliographic search (most reliable, no key)
* PubMed     - NCBI E-utilities (biomedical)
* arXiv      - Atom API (preprints)
* OpenAlex   - broad index; set ``ODAR_OPENALEX_KEY`` (free key) or
  ``ODAR_CONTACT_EMAIL`` for the polite pool, otherwise it may rate-limit
* Semantic Scholar - best effort (shared anonymous pool often returns 429)

Every provider failure is soft: a reference that cannot be looked up is
reported as ``UNCHECKED``, never as fabricated.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

HttpGet = Callable[[str], Tuple[int, str]]
USER_AGENT = "ODAR/1.0 (citation checker; https://github.com/kakarot700/odar)"

VERIFIED = "VERIFIED"
MISMATCH = "MISMATCH"
NOT_FOUND = "NOT FOUND"
UNCHECKED = "UNCHECKED"

STYLES = ("apa", "mla", "chicago", "ieee")

_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)
_ARXIV_RE = re.compile(r"(?:arxiv(?:\.org/(?:abs|pdf)/|:\s*))(\d{4}\.\d{4,5}|[a-z\-]+/\d{7})(?:v\d+)?", re.I)
_PMID_RE = re.compile(r"(?:PMID:?\s*|pubmed\.ncbi\.nlm\.nih\.gov/)(\d{5,9})", re.I)
_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)[a-z]?\b")
_QUOTED_RE = re.compile(r"[\"“”]([^\"“”]{12,300})[\"“”]")


# ---------------------------------------------------------------------- #
@dataclass
class Paper:
    title: str
    authors: List[str] = field(default_factory=list)  # "Given Family" order
    year: Optional[int] = None
    venue: str = ""
    doi: str = ""
    url: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    publisher: str = ""
    kind: str = ""  # journal-article, preprint, proceedings, book, ...
    abstract: str = ""
    provider: str = ""
    open_access_url: str = ""

    @property
    def peer_reviewed(self) -> bool:
        return self.kind in ("journal-article", "proceedings-article", "proceedings") and not self.is_preprint

    @property
    def is_preprint(self) -> bool:
        return self.kind in ("preprint", "posted-content") or "arxiv" in (self.venue + self.url).lower()

    @property
    def link(self) -> str:
        return f"https://doi.org/{self.doi}" if self.doi else self.url

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["peer_reviewed"] = self.peer_reviewed
        data["is_preprint"] = self.is_preprint
        data["link"] = self.link
        return data


@dataclass
class ParsedReference:
    raw: str
    doi: str = ""
    arxiv_id: str = ""
    pmid: str = ""
    title: str = ""
    year: Optional[int] = None
    first_author: str = ""  # family name, lowercase
    url: str = ""


@dataclass
class ReferenceCheck:
    raw: str
    status: str
    matched: Optional[Dict[str, Any]] = None
    problems: List[str] = field(default_factory=list)
    title_similarity: float = 0.0
    formatted: str = ""
    suggestions: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------- #
# HTTP
# ---------------------------------------------------------------------- #
def default_http_get(url: str, timeout: float = 15.0) -> Tuple[int, str]:
    import requests

    for attempt in range(2):
        try:
            resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        except Exception as exc:  # noqa: BLE001
            logger.info("scholar GET failed %s: %s", url[:120], exc)
            return 0, ""
        if resp.status_code == 429 and attempt == 0:
            time.sleep(1.5)
            continue
        return resp.status_code, resp.text
    return 429, ""


def cached_http_get(store: Any, get: Optional[HttpGet] = None, ttl_s: float = 7 * 86400) -> HttpGet:
    """Wrap an HTTP getter with a persistent cache (any object with cache_get/cache_put).
    Only successful and definitive-404 answers are cached; rate limits and errors are not."""
    inner = get or default_http_get

    def fetch(url: str) -> Tuple[int, str]:
        hit = store.cache_get(url)
        if hit is not None:
            return hit
        status, body = inner(url)
        if status == 200 or (status == 404 and ("crossref" in url or "doi.org" in url)):
            store.cache_put(url, status, body, ttl_s)
        return status, body

    return fetch


def _json(get: HttpGet, url: str) -> Optional[Any]:
    status, body = get(url)
    if status != 200 or not body:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def _clean(text: Any) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(text or "")))
    return " ".join(text.split())


# ---------------------------------------------------------------------- #
# Providers
# ---------------------------------------------------------------------- #
CROSSREF_SELECT = "DOI,title,author,issued,container-title,type,volume,issue,page,publisher,URL,abstract"


def _crossref_paper(item: Dict[str, Any]) -> Paper:
    authors = []
    for a in item.get("author") or []:
        name = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", "")
        if name:
            authors.append(name)
    parts = ((item.get("issued") or {}).get("date-parts") or [[None]])[0]
    kind = item.get("type", "")
    return Paper(
        title=_clean((item.get("title") or [""])[0]),
        authors=authors,
        year=parts[0] if parts and isinstance(parts[0], int) else None,
        venue=_clean((item.get("container-title") or [""])[0]),
        doi=str(item.get("DOI", "")).lower(),
        url=item.get("URL", ""),
        volume=str(item.get("volume", "") or ""),
        issue=str(item.get("issue", "") or ""),
        pages=str(item.get("page", "") or ""),
        publisher=item.get("publisher", ""),
        kind="preprint" if kind == "posted-content" else kind,
        abstract=_clean(item.get("abstract", ""))[:1500],
        provider="crossref",
    )


def crossref_doi(doi: str, get: HttpGet) -> Optional[Paper]:
    data = _json(get, f"https://api.crossref.org/works/{quote(doi, safe='/()._;-')}")
    if not data or "message" not in data:
        return None
    return _crossref_paper(data["message"])


def crossref_search(query: str, get: HttpGet, rows: int = 5) -> List[Paper]:
    params = urlencode({"query.bibliographic": query[:300], "rows": rows, "select": CROSSREF_SELECT})
    data = _json(get, f"https://api.crossref.org/works?{params}")
    items = ((data or {}).get("message") or {}).get("items") or []
    return [_crossref_paper(i) for i in items if i.get("title")]


def _pubmed_summaries(ids: Sequence[str], get: HttpGet) -> List[Paper]:
    if not ids:
        return []
    data = _json(
        get,
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?"
        + urlencode({"db": "pubmed", "id": ",".join(ids), "retmode": "json"}),
    )
    result = (data or {}).get("result") or {}
    papers = []
    for uid in result.get("uids", []):
        rec = result.get(uid) or {}
        doi = next((a["value"] for a in rec.get("articleids", []) if a.get("idtype") == "doi"), "")
        year = _YEAR_RE.search(rec.get("pubdate", "") or "")
        authors = [a.get("name", "") for a in rec.get("authors", []) if a.get("authtype") == "Author"]
        papers.append(
            Paper(
                title=_clean(rec.get("title", "")).rstrip("."),
                authors=[_pubmed_name(a) for a in authors if a],
                year=int(year.group(1)) if year else None,
                venue=rec.get("fulljournalname") or rec.get("source", ""),
                doi=doi.lower(),
                url=f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
                volume=rec.get("volume", ""),
                issue=rec.get("issue", ""),
                pages=rec.get("pages", ""),
                kind="journal-article",
                provider="pubmed",
            )
        )
    return papers


def _pubmed_name(name: str) -> str:
    # "Paul P" -> "P Paul" (given-initials first, like the other providers)
    parts = name.split()
    suffix = ""
    if len(parts) >= 3 and parts[-1].rstrip(".").lower() in ("jr", "sr", "ii", "iii"):
        suffix = parts.pop()
    if len(parts) >= 2 and parts[-1].isupper() and len(parts[-1]) <= 3:
        return " ".join([".".join(parts[-1]) + "."] + parts[:-1] + ([suffix] if suffix else []))
    return name


def pubmed_search(query: str, get: HttpGet, rows: int = 5) -> List[Paper]:
    data = _json(
        get,
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
        + urlencode(
            {"db": "pubmed", "term": query[:300], "retmode": "json", "retmax": rows, "sort": "relevance"}
        ),
    )
    ids = ((data or {}).get("esearchresult") or {}).get("idlist") or []
    return _pubmed_summaries(ids, get)


def pubmed_id(pmid: str, get: HttpGet) -> Optional[Paper]:
    found = _pubmed_summaries([pmid], get)
    return found[0] if found else None


_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def _arxiv_feed(url: str, get: HttpGet) -> List[Paper]:
    status, body = get(url)
    if status != 200 or not body:
        return []
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return []
    papers = []
    for entry in root.findall(f"{_ATOM}entry"):
        abs_url = (entry.findtext(f"{_ATOM}id") or "").strip()
        if "/abs/" not in abs_url:
            continue
        year = _YEAR_RE.search(entry.findtext(f"{_ATOM}published") or "")
        doi = (entry.findtext(f"{_ARXIV_NS}doi") or "").strip().lower()
        papers.append(
            Paper(
                title=_clean(entry.findtext(f"{_ATOM}title")),
                authors=[_clean(a.findtext(f"{_ATOM}name")) for a in entry.findall(f"{_ATOM}author")],
                year=int(year.group(1)) if year else None,
                venue="arXiv",
                doi=doi,
                url=re.sub(r"v\d+$", "", abs_url.replace("http://", "https://")),
                kind="preprint" if not doi else "journal-article",
                abstract=_clean(entry.findtext(f"{_ATOM}summary"))[:1500],
                provider="arxiv",
                open_access_url=re.sub(r"v\d+$", "", abs_url.replace("http://", "https://")),
            )
        )
    return papers


def arxiv_search(query: str, get: HttpGet, rows: int = 5) -> List[Paper]:
    words = [w for w in re.findall(r"[A-Za-z0-9]+", query) if len(w) > 2][:8]
    if not words:
        return []
    q = " AND ".join(f"all:{w}" for w in words)
    return _arxiv_feed(
        "https://export.arxiv.org/api/query?" + urlencode({"search_query": q, "max_results": rows}), get
    )


def arxiv_id(identifier: str, get: HttpGet) -> Optional[Paper]:
    found = _arxiv_feed("https://export.arxiv.org/api/query?" + urlencode({"id_list": identifier}), get)
    return found[0] if found else None


def _openalex_params(extra: Dict[str, Any]) -> str:
    params = dict(extra)
    if os.environ.get("ODAR_OPENALEX_KEY"):
        params["api_key"] = os.environ["ODAR_OPENALEX_KEY"]
    if os.environ.get("ODAR_CONTACT_EMAIL"):
        params["mailto"] = os.environ["ODAR_CONTACT_EMAIL"]
    return urlencode(params)


def _openalex_paper(item: Dict[str, Any]) -> Paper:
    authors = [
        (a.get("author") or {}).get("display_name", "")
        for a in item.get("authorships") or []
        if a.get("author")
    ]
    loc = item.get("primary_location") or {}
    source = loc.get("source") or {}
    biblio = item.get("biblio") or {}
    pages = "-".join(x for x in (biblio.get("first_page"), biblio.get("last_page")) if x)
    kind = item.get("type_crossref") or item.get("type") or ""
    if kind == "article" and source.get("type") == "journal":
        kind = "journal-article"
    return Paper(
        title=_clean(item.get("display_name") or item.get("title")),
        authors=[a for a in authors if a],
        year=item.get("publication_year"),
        venue=source.get("display_name", "") or "",
        doi=(item.get("doi") or "").replace("https://doi.org/", "").lower(),
        url=loc.get("landing_page_url") or item.get("id", ""),
        volume=biblio.get("volume") or "",
        issue=biblio.get("issue") or "",
        pages=pages,
        kind="preprint" if kind in ("posted-content", "preprint") else kind,
        provider="openalex",
        open_access_url=((item.get("open_access") or {}).get("oa_url") or ""),
    )


def openalex_search(query: str, get: HttpGet, rows: int = 5) -> List[Paper]:
    data = _json(
        get, "https://api.openalex.org/works?" + _openalex_params({"search": query[:300], "per_page": rows})
    )
    return [_openalex_paper(i) for i in (data or {}).get("results") or [] if i.get("display_name")]


def semantic_scholar_search(query: str, get: HttpGet, rows: int = 5) -> List[Paper]:
    fields = "title,authors,year,venue,externalIds,url,publicationTypes,abstract,openAccessPdf"
    data = _json(
        get,
        "https://api.semanticscholar.org/graph/v1/paper/search?"
        + urlencode({"query": query[:300], "limit": rows, "fields": fields}),
    )
    papers = []
    for item in (data or {}).get("data") or []:
        ext = item.get("externalIds") or {}
        types = item.get("publicationTypes") or []
        papers.append(
            Paper(
                title=_clean(item.get("title")),
                authors=[a.get("name", "") for a in item.get("authors") or []],
                year=item.get("year"),
                venue=item.get("venue", "") or "",
                doi=(ext.get("DOI") or "").lower(),
                url=item.get("url", ""),
                kind="journal-article"
                if "JournalArticle" in types
                else ("preprint" if ext.get("ArXiv") else ""),
                abstract=_clean(item.get("abstract"))[:1500],
                provider="semanticscholar",
                open_access_url=((item.get("openAccessPdf") or {}).get("url") or ""),
            )
        )
    return papers


PROVIDERS: Dict[str, Callable[[str, HttpGet, int], List[Paper]]] = {
    "crossref": crossref_search,
    "pubmed": pubmed_search,
    "arxiv": arxiv_search,
    "openalex": openalex_search,
    "semanticscholar": semantic_scholar_search,
}


# ---------------------------------------------------------------------- #
# Search across providers
# ---------------------------------------------------------------------- #
def _norm_title(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (title or "").lower()))


def title_similarity(a: str, b: str) -> float:
    a, b = _norm_title(a), _norm_title(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def dedupe_papers(papers: Sequence[Paper]) -> List[Paper]:
    out: List[Paper] = []
    for paper in papers:
        dup = next(
            (
                p
                for p in out
                if (paper.doi and paper.doi == p.doi) or title_similarity(paper.title, p.title) >= 0.93
            ),
            None,
        )
        if dup is None:
            out.append(paper)
        else:  # merge the useful bits the first record lacked
            dup.abstract = dup.abstract or paper.abstract
            dup.open_access_url = dup.open_access_url or paper.open_access_url
            dup.doi = dup.doi or paper.doi
    return out


class Scholar:
    """Search and verify papers across free providers (injectable HTTP for tests)."""

    def __init__(
        self,
        get: Optional[HttpGet] = None,
        providers: Sequence[str] = ("crossref", "pubmed", "arxiv", "openalex", "semanticscholar"),
        cache: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.get = get or default_http_get
        self.providers = [p for p in providers if p in PROVIDERS]
        self.cache: Dict[str, Any] = cache if cache is not None else {}

    def _cached(self, key: str, fn: Callable[[], Any]) -> Any:
        if key not in self.cache:
            self.cache[key] = fn()
        return self.cache[key]

    def search(self, query: str, rows: int = 5, providers: Optional[Sequence[str]] = None) -> List[Paper]:
        found: List[Paper] = []
        for name in providers or self.providers:
            try:
                provider = PROVIDERS[name]
                found += self._cached(f"{name}:{rows}:{query}", partial(provider, query, self.get, rows))
            except Exception as exc:  # noqa: BLE001
                logger.info("provider %s failed: %s", name, exc)
        return dedupe_papers(found)

    def by_doi(self, doi: str) -> Optional[Paper]:
        doi = doi.lower().rstrip(".,;)")
        return self._cached(f"doi:{doi}", lambda: crossref_doi(doi, self.get))

    def by_arxiv(self, ident: str) -> Optional[Paper]:
        return self._cached(f"arxiv:{ident}", lambda: arxiv_id(ident, self.get))

    def by_pmid(self, pmid: str) -> Optional[Paper]:
        return self._cached(f"pmid:{pmid}", lambda: pubmed_id(pmid, self.get))

    # ------------------------------------------------------------------ #
    def validate(self, raw: str, style: str = "apa") -> ReferenceCheck:
        ref = parse_reference(raw)
        check = ReferenceCheck(raw=raw.strip(), status=UNCHECKED)
        paper: Optional[Paper] = None
        try:
            if ref.doi:
                paper = self.by_doi(ref.doi)
                if paper is None:
                    status, _ = self.get(f"https://doi.org/api/handles/{quote(ref.doi, safe='/')}")
                    if status == 404:
                        check.status = NOT_FOUND
                        check.problems.append(f"DOI {ref.doi} does not exist")
                    elif status == 200:  # registered outside Crossref (e.g. DataCite)
                        check.problems.append(
                            "DOI exists but has no Crossref record; matched by title instead"
                        )
            elif ref.arxiv_id:
                paper = self.by_arxiv(ref.arxiv_id)
                if paper is None:
                    check.status = NOT_FOUND
                    check.problems.append(f"arXiv {ref.arxiv_id} not found")
            elif ref.pmid:
                paper = self.by_pmid(ref.pmid)
                if paper is None:
                    check.status = NOT_FOUND
                    check.problems.append(f"PMID {ref.pmid} not found")
            if paper is None and check.status != NOT_FOUND:
                query = " ".join(
                    x for x in (ref.title or raw[:250], ref.first_author, str(ref.year or "")) if x
                )
                candidates = self.search(query, rows=4, providers=("crossref", "openalex", "pubmed", "arxiv"))
                if not candidates:
                    check.problems.append("scholarly indexes unreachable or returned nothing")
                    return check
                best = max(candidates, key=lambda p: _match_score(ref, p), default=None)
                if best is not None and _match_score(ref, best) >= 0.62:
                    paper = best
                elif candidates:
                    check.status = NOT_FOUND
                    check.problems.append(
                        "no paper with this title/author/year exists in the scholarly indexes"
                    )
                    check.suggestions = [p.to_dict() for p in candidates[:3]]
        except Exception as exc:  # noqa: BLE001
            check.problems.append(f"lookup failed: {type(exc).__name__}")
            return check
        if paper is None:
            return check
        check.matched = paper.to_dict()
        check.formatted = format_citation(paper, style)
        check.title_similarity = round(title_similarity(ref.title, paper.title), 2) if ref.title else 0.0
        problems = []
        if ref.title and check.title_similarity < 0.8:
            problems.append(f'title differs: real title is "{paper.title}"')
        if ref.year and paper.year and abs(ref.year - paper.year) > 1:
            problems.append(f"year differs: published {paper.year}, cited as {ref.year}")
        surnames = [_surname(a) for a in paper.authors]
        if ref.first_author and surnames and ref.first_author not in surnames[:3]:
            problems.append(f"author differs: first author is {paper.authors[0]}")
        check.problems += problems
        check.status = MISMATCH if problems else VERIFIED
        return check

    def suggest(self, claim_or_title: str, rows: int = 3) -> List[Paper]:
        """Real papers for a claim or a broken reference ('fix my citations')."""
        papers = self.search(claim_or_title, rows=4)
        papers.sort(key=lambda p: (-int(p.peer_reviewed), -(p.year or 0)))
        return papers[:rows]


def _surname(name: str) -> str:
    parts = [p for p in re.split(r"[\s,]+", name.strip()) if p]
    if not parts:
        return ""
    if "," in name:  # "Family, Given"
        return name.split(",")[0].strip().lower()
    return parts[-1].lower().strip(".")


def _match_score(ref: ParsedReference, paper: Paper) -> float:
    score = title_similarity(ref.title, paper.title) if ref.title else 0.0
    if not ref.title:  # no title parsed: lean on raw-text overlap
        raw = set(_norm_title(ref.raw).split())
        words = set(_norm_title(paper.title).split())
        score = len(raw & words) / max(1, len(words))
    if ref.year and paper.year:
        score += 0.08 if abs(ref.year - paper.year) <= 1 else -0.15
    if ref.first_author:
        score += 0.08 if ref.first_author in [_surname(a) for a in paper.authors[:3]] else -0.1
    return score


# ---------------------------------------------------------------------- #
# Reference parsing
# ---------------------------------------------------------------------- #
def parse_reference(raw: str) -> ParsedReference:
    text = " ".join(raw.split())
    text = re.sub(r"^\s*(\[\d+\]|\d+[.)])\s*", "", text)
    ref = ParsedReference(raw=text)
    m = _DOI_RE.search(text)
    if m:
        ref.doi = m.group(1).rstrip(".,;)").lower()
    m = _ARXIV_RE.search(text)
    if m:
        ref.arxiv_id = m.group(1)
    m = _PMID_RE.search(text)
    if m:
        ref.pmid = m.group(1)
    url = re.search(r"https?://\S+", text)
    ref.url = url.group(0).rstrip(".,;)") if url else ""
    years = _YEAR_RE.findall(text)
    ref.year = int(years[0]) if years else None
    quoted = _QUOTED_RE.search(text)
    if quoted:
        ref.title = quoted.group(1).strip(" .,")
    else:
        ref.title = _guess_title(text)
    first = re.match(r"\s*([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+)?)\s*,", text)
    if first:
        ref.first_author = first.group(1).split()[-1].lower()
    else:  # "J. Smith and ..." / "John Smith,"
        alt = re.match(r"\s*(?:[A-Z]\.\s*)+([A-Z][A-Za-z'\-]+)", text)
        if alt:
            ref.first_author = alt.group(1).lower()
    return ref


def _guess_title(text: str) -> str:
    # APA: Authors (2020). Title. Journal ...   /  Authors. Title. Journal, 2020.
    stripped = re.sub(r"https?://\S+|doi:\s*\S+", "", text, flags=re.I)
    m = re.search(r"\((19|20)\d\d[a-z]?\)\.\s*(.+?)\.\s", stripped + " ")
    if m:
        return m.group(2).strip()
    parts = [p.strip() for p in re.split(r"\.\s+", stripped) if p.strip()]
    if len(parts) >= 2:
        # the first long segment after the author block
        for part in parts[1:]:
            if len(part.split()) >= 3 and not _YEAR_RE.fullmatch(part):
                return part.strip(" .,")
    return ""


# ---------------------------------------------------------------------- #
# Citation styles
# ---------------------------------------------------------------------- #
def _split_name(name: str) -> Tuple[str, str]:
    name = name.strip()
    if "," in name:
        family, given = [x.strip() for x in name.split(",", 1)]
        return given, family
    parts = name.split()
    if len(parts) > 2 and parts[-1].rstrip(".").lower() in ("jr", "sr", "ii", "iii", "iv"):
        parts = parts[:-1]
    if len(parts) == 1:
        return "", parts[0]
    return " ".join(parts[:-1]), parts[-1]


def _initials(given: str) -> str:
    return " ".join(f"{p[0]}." for p in re.split(r"[\s.\-]+", given) if p)


def _apa_author(name: str) -> str:
    given, family = _split_name(name)
    return f"{family}, {_initials(given)}".strip(", ")


def _authors_apa(authors: List[str]) -> str:
    names = [_apa_author(a) for a in authors]
    if not names:
        return ""
    if len(names) > 20:
        names = names[:19] + ["...", names[-1]]
        return ", ".join(names)
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + ", & " + names[-1]


def _authors_mla(authors: List[str]) -> str:
    if not authors:
        return ""
    given, family = _split_name(authors[0])
    first = f"{family}, {given}".strip(", ")
    if len(authors) == 1:
        return first
    if len(authors) == 2:
        return f"{first}, and {authors[1]}"
    return f"{first}, et al"


def _authors_chicago(authors: List[str]) -> str:
    if not authors:
        return ""
    given, family = _split_name(authors[0])
    first = f"{family}, {given}".strip(", ")
    if len(authors) == 1:
        return first
    if len(authors) > 10:
        return ", ".join([first] + authors[1:7]) + ", et al"
    return ", ".join([first] + authors[1:-1]) + ", and " + authors[-1]


def _authors_ieee(authors: List[str]) -> str:
    names = []
    for a in authors[:6]:
        given, family = _split_name(a)
        names.append(f"{_initials(given)} {family}".strip())
    if len(authors) > 6:
        return f"{names[0]} et al."
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + ", and " + names[-1]


def format_citation(paper: Paper, style: str = "apa", number: int = 0) -> str:
    style = style.lower()
    year = str(paper.year) if paper.year else "n.d."
    title = paper.title.rstrip(".")
    link = paper.link
    venue = paper.venue
    if style == "apa":
        out = f"{_authors_apa(paper.authors)} ({year}). {title}."
        if venue:
            out += f" {venue}"
            if paper.volume:
                out += f", {paper.volume}"
                if paper.issue:
                    out += f"({paper.issue})"
            if paper.pages:
                out += f", {paper.pages}"
            out += "."
        return f"{out} {link}".strip() if link else out
    if style == "mla":
        authors = _authors_mla(paper.authors)
        out = f'{authors + ". " if authors else ""}"{title}." '
        if venue:
            out += venue
            if paper.volume:
                out += f", vol. {paper.volume}"
            if paper.issue:
                out += f", no. {paper.issue}"
            out += ", "
        out += year
        if paper.pages:
            out += f", pp. {paper.pages}"
        out += "."
        return f"{out} {link}." if link else out
    if style == "chicago":
        authors = _authors_chicago(paper.authors)
        out = f'{authors + ". " if authors else ""}"{title}."'
        if venue:
            out += f" {venue}"
            if paper.volume:
                out += f" {paper.volume}"
            if paper.issue:
                out += f", no. {paper.issue}"
        out += f" ({year})"
        if paper.pages:
            out += f": {paper.pages}"
        out += "."
        return f"{out} {link}." if link else out
    if style == "ieee":
        prefix = f"[{number}] " if number else ""
        out = f'{prefix}{_authors_ieee(paper.authors)}, "{title}," '
        if venue:
            out += f"{venue}, "
        if paper.volume:
            out += f"vol. {paper.volume}, "
        if paper.issue:
            out += f"no. {paper.issue}, "
        if paper.pages:
            out += f"pp. {paper.pages}, "
        out += f"{year}"
        if paper.doi:
            out += f", doi: {paper.doi}"
        elif paper.url:
            out += f". [Online]. Available: {paper.url}"
        return out + "."
    raise ValueError(f"unknown style {style!r}; use one of {', '.join(STYLES)}")


def bibliography(papers: Sequence[Paper], style: str = "apa") -> List[str]:
    if style.lower() == "ieee":  # numbered in citation order
        return [format_citation(p, "ieee", i) for i, p in enumerate(papers, 1)]
    return sorted((format_citation(p, style) for p in papers), key=lambda s: s.lower())


def split_references(text: str) -> List[str]:
    """One reference per line/numbered entry; skips headings and blank lines."""
    entries: List[str] = []
    current = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or re.fullmatch(r"#*\s*(references|bibliography|works cited|sources)\s*:?", line, re.I):
            if current:
                entries.append(current)
                current = ""
            continue
        starts_new = bool(re.match(r"^(\[\d+\]|\d+[.)]|[-*•])\s+", line)) or bool(
            re.match(r"^[A-Z][A-Za-z'\-]+,\s*(?:[A-Z]\.|[A-Z][a-z]+)", line)
        )
        if starts_new and current:
            entries.append(current)
            current = ""
        line = re.sub(r"^[-*•]\s+", "", line)
        current = f"{current} {line}".strip()
    if current:
        entries.append(current)
    return [e for e in entries if len(e) >= 12]


def check_references(
    text: str, style: str = "apa", scholar: Optional[Scholar] = None, max_refs: int = 40
) -> Dict[str, Any]:
    """Validate every reference in ``text`` and build a corrected bibliography."""
    scholar = scholar or Scholar()
    refs = split_references(text)[:max_refs]
    checks = [scholar.validate(r, style) for r in refs]
    for check in checks:
        if check.status == NOT_FOUND and not check.suggestions:
            parsed = parse_reference(check.raw)
            query = parsed.title or check.raw[:200]
            check.suggestions = [p.to_dict() for p in scholar.suggest(query)]
    good = dedupe_papers([Paper(**_paper_fields(c.matched)) for c in checks if c.matched])
    counts: Dict[str, int] = {}
    for c in checks:
        counts[c.status] = counts.get(c.status, 0) + 1
    return {
        "style": style,
        "counts": counts,
        "references": [c.to_dict() for c in checks],
        "bibliography": bibliography(good, style),
    }


def _paper_fields(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    keys = Paper.__dataclass_fields__.keys()
    return {k: v for k, v in (data or {}).items() if k in keys}
