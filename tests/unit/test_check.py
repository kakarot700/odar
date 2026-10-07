"""ODAR Check: citation verification fixtures, one per verdict."""

import json
import re

from odar.check import (
    CONTRADICTED,
    DEAD_LINK,
    NO_CITATION,
    PARTIAL,
    SUPPORTED,
    UNVERIFIABLE,
    WRONG_SOURCE,
    CheckLimits,
    CitationChecker,
    Claim,
    atomize,
    check_text,
    parse_claims,
)
from odar.citation_auditor import CitationAuditor
from odar.schemas import ExtractedPage, SearchHit

NEG = {"never", "not", "no", "cannot"}


def _toks(text):
    return {t for t in re.findall(r"[a-z]+", text.lower()) if len(t) > 2}


class FakeScorer:
    """Entails when the hypothesis' words (digits ignored) are in the premise;
    contradicts (symmetrically) when the two overlap heavily but exactly one
    side carries a negation word."""

    def predict(self, pairs):
        out = []
        for premise, hypothesis in pairs:
            p, h = _toks(premise), _toks(hypothesis)
            core_h, core_p = h - NEG, p - NEG
            overlap = len(core_h & core_p) / max(1, len(core_h))
            negated = bool(h & NEG) != bool(p & NEG)
            if overlap >= 0.8 and negated:
                out.append([4.0, -2.0, 0.0])
            elif overlap >= 0.999 and not negated:
                out.append([-2.0, 4.0, 0.0])
            elif overlap >= 0.6:
                out.append([-1.0, 1.0, 0.5])  # borderline band
            else:
                out.append([-2.0, -2.0, 4.0])
        return out


FILLER = (
    "The museum is open every day of the year. Tickets can be bought online in advance. "
    "Guided tours run hourly from the main entrance hall. "
)
PAGES = {
    "https://facts.example/tower": "The tower was completed in 1889 for the world fair in Paris. "
    + "The tower is about 300 metres tall including antennas. "
    + FILLER,
    "https://facts.example/boiling": "Water boils at 100 degrees at sea level. "
    + "Salt does not change the boiling point of water in any noticeable way. "
    + FILLER,
    "https://facts.example/unrelated": "Bananas are rich in potassium and grow in tropical climates. "
    + FILLER,
    "https://real.example/replacement": "The moon orbits the earth roughly every month. " + FILLER,
}


class FakeExtractor:
    def __init__(self):
        self.calls = []

    def extract(self, url):
        self.calls.append(url)
        if url in PAGES:
            return ExtractedPage(
                url=url,
                ok=True,
                text=PAGES[url],
                chars=len(PAGES[url]),
                http_status=200,
                resolved_url=url,
                title="Page",
            )
        if "ghost-domain" in url:
            return ExtractedPage(
                url=url,
                ok=False,
                error="unsafe fetch refused: DNS resolution failed for 'x'",
                quarantined=True,
            )
        if "blocked.example" in url:
            return ExtractedPage(url=url, ok=False, error="HTTP 403", http_status=403)
        if "web.archive.org" in url and "gone.example" in url:
            text = "The library was founded in 1901 by the city council. " + FILLER * 2
            return ExtractedPage(
                url=url, ok=True, text=text, chars=len(text), http_status=200, resolved_url=url
            )
        return ExtractedPage(url=url, ok=False, error="HTTP 404", http_status=404)


class FakeWayback:
    def closest(self, url):
        if "gone.example" in url:
            return {
                "url": f"http://web.archive.org/web/2020/{url}",
                "raw_url": f"https://web.archive.org/web/2020id_/{url}",
                "timestamp": "2020",
            }
        return None


class FakeSearch:
    def text(self, query, max_results=6):
        return [
            SearchHit(url="https://real.example/replacement", title="Moon facts", snippet="", engine="fake")
        ]


def _checker(**kw):
    return CitationChecker(
        extractor=FakeExtractor(),
        auditor=CitationAuditor(scorer=FakeScorer()),
        searcher=kw.pop("searcher", FakeSearch()),
        wayback=FakeWayback(),
        complete=kw.pop("complete", None),
        limits=CheckLimits(**kw),
    )


TEXT = """The tower was completed in 1889 for the world fair in Paris [1].
The tower is about 330 metres tall including antennas [1].
Salt does change the boiling point of water in a noticeable way [2].
Bananas are an excellent source of vitamin B12 for vegetarians [3].
The moon orbits the earth roughly every month [4].
The library was founded in 1901 by the city council [5].
The canal was opened in 1914 after a decade of construction [6].
The moon orbits the earth roughly every month, according to many astronomers worldwide.

[1] https://facts.example/tower
[2] https://facts.example/boiling
[3] https://facts.example/unrelated
[4] https://ghost-domain.example/moon/facts
[5] https://gone.example/history/library
[6] https://blocked.example/canal/history
"""


def _by_claim(report):
    return {r.claim.split(" (")[0]: r for r in report.claims}


def test_every_verdict_fixture():
    report = _checker().run(TEXT)
    verdicts = [r.verdict for r in report.claims]
    assert verdicts == [
        SUPPORTED,
        PARTIAL,  # figure 330 is not on the page
        CONTRADICTED,
        WRONG_SOURCE,
        DEAD_LINK,  # domain does not resolve
        SUPPORTED,  # live page 404 but verified on the Wayback copy
        UNVERIFIABLE,  # 403 and never archived
        NO_CITATION,
    ]
    supported = report.claims[0]
    assert "completed in 1889" in supported.quote and supported.quote_url == "https://facts.example/tower"
    contradicted = report.claims[2].citations[0]
    assert "does not change" in contradicted.quote
    assert "fabricated" in report.claims[4].citations[0].note
    assert "Wayback" in report.claims[5].citations[0].note


def test_trust_score_counts_and_exclusions():
    report = _checker().run(TEXT)
    # scored: SUPPORTED x2 (1.0), PARTIAL (0.5), CONTRADICTED, WRONG SOURCE, DEAD LINK -> 2.5/6
    assert report.trust_score == round(100 * 2.5 / 6)
    assert report.counts[NO_CITATION] == 1 and report.counts[UNVERIFIABLE] == 1
    assert report.grade == "unreliable citations"


def test_replacement_found_for_dead_link_and_uncited_claim():
    report = _checker().run(TEXT)
    dead = report.claims[4]
    assert dead.replacement and dead.replacement.url == "https://real.example/replacement"
    assert "moon orbits" in dead.replacement.quote
    wrong = report.claims[3]
    assert wrong.replacement_searched and wrong.replacement is None  # search finds nothing about bananas


def test_replacement_search_budget():
    report = _checker(max_replacement_searches=1).run(TEXT)
    assert sum(r.replacement_searched for r in report.claims) == 1
    assert report.usage["searches"] == 1


def test_parser_formats():
    text = (
        "Claim one is stated here clearly [1][2]. Claim two cites a range of sources here [3-4].\n"
        "Inline ([Wiki](https://w.example/a/b)) shows the inline style is parsed fine.\n"
        "A bare URL style claim is also handled here https://bare.example/x/y.\n"
        "A numbered marker with no reference entry appears here [9].\n\n"
        "## Sources\n1. First https://one.example/a\n[2]: https://two.example/b\n"
        "[3] Three - https://three.example/c\n[4] [Four](https://four.example/d)\n"
    )
    claims = parse_claims(text)
    assert [c.url for c in claims[0].citations] == ["https://one.example/a", "https://two.example/b"]
    assert [c.url for c in claims[1].citations] == ["https://three.example/c", "https://four.example/d"]
    assert claims[2].citations[0].url == "https://w.example/a/b" and "Wiki" not in claims[2].text
    assert claims[3].citations[0].marker == "bare"
    assert claims[4].citations[0].url is None
    assert all("http" not in c.text and "[" not in c.text for c in claims)


def test_unmatched_marker_is_no_citation():
    report = _checker().run("The tower was completed in 1889 for the world fair [7].\n")
    assert report.claims[0].verdict == NO_CITATION
    assert "no matching reference" in report.claims[0].citations[0].note


def test_ssrf_unsafe_urls_never_fetched():
    checker = _checker(searcher=None)
    report = checker.run(
        "The admin console reveals the cloud credentials [1].\n"
        "The internal service lists the user records [2].\n\n"
        "[1] http://169.254.169.254/latest/meta-data/\n[2] http://localhost:8080/users\n"
    )
    assert all(r.verdict == DEAD_LINK for r in report.claims)
    assert checker.extractor.calls == []  # refused before any network I/O
    assert all(link.state == "unsafe" for link in report.links)


def test_soft_404_redirect_to_homepage_is_dead():
    class Redirecting(FakeExtractor):
        def extract(self, url):
            if "news.example" in url:
                text = "Welcome to our homepage. " + FILLER
                return ExtractedPage(
                    url=url,
                    ok=True,
                    text=text,
                    chars=len(text),
                    http_status=200,
                    resolved_url="https://news.example/",
                )
            return super().extract(url)

    checker = _checker()
    checker.extractor = Redirecting()
    report = checker.run(
        "The report found record sales in the third quarter [1].\n\n[1] https://news.example/2024/05/report\n"
    )
    assert report.claims[0].verdict == DEAD_LINK


def test_llm_adjudicates_borderline_but_never_grants_supported():
    calls = []

    def complete(system, prompt):
        calls.append(prompt)
        return '{"verdict": "PARTIAL", "reason": "same topic"}'

    checker = _checker(complete=complete)
    # 'tower completed 1889 world fair Paris famous engineer' -> overlap 6/8 (borderline)
    report = checker.run(
        "The tower was completed in 1889 by a famous engineer [1].\n\n[1] https://facts.example/tower\n"
    )
    result = report.claims[0]
    assert result.verdict == PARTIAL and result.citations[0].judged_by == "nli+llm"
    assert any("QUOTE" in c for c in calls)


def test_injection_page_is_quarantined_and_flagged():
    class Injected(FakeExtractor):
        def extract(self, url):
            page = super().extract(url)
            if url.endswith("/tower"):
                page.quarantined = True
            return page

    checker = _checker()
    checker.extractor = Injected()
    report = checker.run(
        "The tower was completed in 1889 for the world fair in Paris [1].\n\n[1] https://facts.example/tower\n"
    )
    assert "quarantined" in report.claims[0].citations[0].note


def test_atomize_keeps_only_faithful_parts():
    claim = Claim(
        claim_id="c1",
        text="The tower was completed in 1889 for the world fair; the tower is about 300 metres tall including antennas.",
        sentence="",
    )

    def complete(system, prompt):
        return json.dumps(
            {
                "claims": [
                    "The tower was completed in 1889 for the world fair.",
                    "The tower is about 300 metres tall including antennas.",
                    "The tower was designed by aliens from another galaxy.",
                ]
            }
        )

    parts = atomize([claim], complete)
    assert [p.claim_id for p in parts] == ["c1.1", "c1.2"]
    assert all("aliens" not in p.text for p in parts)


def test_reports_render_markdown_and_json():
    report = _checker().run(TEXT)
    md = report.to_markdown()
    assert "Trust score" in md and "CONTRADICTED" in md and "suggested source" in md
    data = json.loads(report.to_json())
    assert data["claims"][0]["verdict"] == SUPPORTED
    assert data["audit_trail"][0]["event"] == "parse"
    assert all("text" not in link for link in data["links"])  # page bodies stay out of the report


def test_check_text_entry_point_runs_fully_local():
    report = check_text(
        TEXT,
        use_llm=False,
        search=False,
        extractor=FakeExtractor(),
        auditor=CitationAuditor(scorer=FakeScorer()),
        wayback=FakeWayback(),
    )
    assert report.usage["llm_calls"] == 0 and report.usage["searches"] == 0
    assert report.claims[0].verdict == SUPPORTED


def test_cli_check_writes_reports(tmp_path, monkeypatch, capsys):
    import odar.check as check_mod
    from odar import cli

    real = check_mod.check_text

    def fake_check_text(text, **kw):
        kw.update(
            extractor=FakeExtractor(),
            auditor=CitationAuditor(scorer=FakeScorer()),
            wayback=FakeWayback(),
            searcher=FakeSearch(),
        )
        return real(text, **kw)

    monkeypatch.setattr(check_mod, "check_text", fake_check_text)
    src = tmp_path / "answer.md"
    src.write_text(TEXT)
    assert cli.main(["check", str(src), "--no-llm", "--json", "--output-dir", str(tmp_path / "out")]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["claims"][0]["verdict"] == SUPPORTED
    assert len(list((tmp_path / "out").glob("chk_*.md"))) == 1


def test_heading_only_support_is_not_full_support():
    class Heading(FakeExtractor):
        def extract(self, url):
            if url.endswith("/heading"):
                text = "Tower opened 1889. " + FILLER * 2
                return ExtractedPage(
                    url=url, ok=True, text=text, chars=len(text), http_status=200, resolved_url=url
                )
            return super().extract(url)

    class Ent(FakeScorer):
        def predict(self, pairs):
            return [
                [-2.0, 4.0, 0.0] if p.strip() == "Tower opened 1889." else [-2.0, -2.0, 4.0]
                for p, _h in pairs
            ]

    checker = _checker(searcher=None)
    checker.extractor = Heading()
    checker.auditor = CitationAuditor(scorer=Ent())
    report = checker.run(
        "The tower opened to visitors in 1889 for the fair [1].\n\n[1] https://x.example/a/heading\n"
    )
    assert report.claims[0].verdict == PARTIAL


def test_archive_outage_never_claims_fabricated():
    class Down:
        def closest(self, url):
            raise RuntimeError("wayback CDX unavailable")

    checker = _checker(searcher=None)
    checker.wayback = Down()
    report = checker.run(
        "The canal was opened in 1914 after a decade of work [1].\n\n[1] https://x.example/a/missing\n"
    )
    cit = report.claims[0].citations[0]
    assert report.claims[0].verdict == DEAD_LINK
    assert "fabricated" not in cit.note and "archive check unavailable" in cit.note
