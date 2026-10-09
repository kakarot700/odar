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


TOWER_DOC = "The tower was completed in 1889 by a famous engineer [1].\n\n[1] https://facts.example/tower\n"
TOWER_QUOTE = "The tower was completed in 1889 for the world fair in Paris."


def _reply(verdict, quote):
    return lambda system, prompt: json.dumps({"verdict": verdict, "quote": quote, "reason": "test"})


def test_paraphrase_judge_reads_passages_and_grants_partial():
    calls = []

    def complete(system, prompt):
        calls.append(prompt)
        return _reply("PARTIAL", TOWER_QUOTE)(system, prompt)

    report = _checker(complete=complete).run(TOWER_DOC)
    result = report.claims[0]
    assert result.verdict == PARTIAL and result.citations[0].judged_by == "nli+llm"
    assert result.citations[0].quote == TOWER_QUOTE
    assert any("PASSAGES" in c and "[P1]" in c for c in calls)


def test_paraphrase_judge_supports_reworded_claim_with_verbatim_quote():
    report = _checker(complete=_reply("SUPPORTED", TOWER_QUOTE)).run(TOWER_DOC)
    assert report.claims[0].verdict == SUPPORTED
    assert "reworded" in report.claims[0].citations[0].note


def test_paraphrase_judge_rejects_invented_evidence():
    fake = "The tower was designed by a famous engineer named Gustave Eiffel in 1889."
    report = _checker(complete=_reply("SUPPORTED", fake)).run(TOWER_DOC)
    assert report.claims[0].verdict == WRONG_SOURCE


def test_paraphrase_judge_requires_numbers_in_quote():
    doc = "The tower was completed in 1889 by a famous engineer and cost 7 million francs [1].\n\n[1] https://facts.example/tower\n"
    report = _checker(complete=_reply("SUPPORTED", TOWER_QUOTE)).run(doc)
    assert report.claims[0].verdict in (PARTIAL, WRONG_SOURCE)


def test_llm_overrules_spurious_nli_contradiction():
    doc = "The tower was never completed in 1889 for the world fair in Paris [1].\n\n[1] https://facts.example/tower\n"
    report = _checker(complete=_reply("NOT_SUPPORTED", "")).run(doc)
    # The page itself carries no negation, so the model's "no" overrules NLI.
    assert report.claims[0].verdict == WRONG_SOURCE
    confirmed = _checker(complete=_reply("CONTRADICTED", TOWER_QUOTE)).run(doc)
    assert confirmed.claims[0].verdict == CONTRADICTED


def _tiny_pdf(text):
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


class _PdfResponse:
    status_code = 200

    def __init__(self, body, content_type="application/pdf"):
        self.body = body
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(body))}

    def iter_content(self, chunk_size=65536):
        yield self.body

    def close(self):
        pass


class _PdfSession:
    headers: dict = {}

    def __init__(self, body, content_type="application/pdf"):
        self.response = _PdfResponse(body, content_type)

    def get(self, *args, **kwargs):
        return self.response


def _offline_dns(monkeypatch):
    import odar.retrieval as retrieval
    from odar.url_safety import validate_url as real_validate

    monkeypatch.setattr(
        retrieval, "validate_url", lambda url, resolve_dns=True: real_validate(url, resolve_dns=False)
    )


def test_pdf_citation_is_read_when_enabled(monkeypatch):
    import pytest

    pytest.importorskip("pypdf")
    from odar.retrieval import PageExtractor

    _offline_dns(monkeypatch)
    body = _tiny_pdf("Human-caused warming reached about 1.07 degrees Celsius.")
    page = PageExtractor(session=_PdfSession(body), allow_pdf=True).extract("https://www.ipcc.ch/spm.pdf")
    assert page.ok and page.engine == "pypdf" and "1.07 degrees" in page.text


def test_pdf_refused_by_default_and_broken_pdf_reported(monkeypatch):
    from odar.retrieval import PageExtractor

    _offline_dns(monkeypatch)
    body = _tiny_pdf("hello world")
    page = PageExtractor(session=_PdfSession(body)).extract("https://www.ipcc.ch/spm.pdf")
    assert not page.ok and "not allowed" in (page.error or "")
    broken = PageExtractor(session=_PdfSession(b"%PDF-1.4 garbage"), allow_pdf=True).extract(
        "https://x.example/a.pdf"
    )
    assert not broken.ok and "pdf" in (broken.error or "").lower()


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


def test_explicit_negation_contradiction_survives_llm_hedge():
    class NegPage(FakeExtractor):
        def extract(self, url):
            page = super().extract(url)
            page.text = "The tower was not completed in 1889 for the world fair in Paris. " + FILLER * 3
            return page

    checker = _checker(complete=_reply("NOT_SUPPORTED", ""))
    checker.extractor = NegPage()
    report = checker.run(
        "The tower was completed in 1889 for the world fair in Paris [1].\n\n[1] https://facts.example/tower\n"
    )
    assert report.claims[0].verdict == CONTRADICTED


class FakeScholar:
    def __init__(self):
        from odar.scholar import Paper

        self.paper = Paper(
            title="Tower history",
            authors=["Ann Lee"],
            year=2001,
            venue="J Hist",
            doi="10.1/tower",
            kind="journal-article",
            abstract="The tower was completed in 1889 for the world fair in Paris and drew huge crowds.",
        )

    def suggest(self, text, rows=3):
        return [self.paper]

    def validate(self, raw, style="apa"):
        from odar.scholar import NOT_FOUND, ReferenceCheck

        return ReferenceCheck(raw=raw, status=NOT_FOUND, problems=["no such paper"])


def test_academic_mode_labels_sources_checks_references_and_suggests_papers():
    checker = _checker()
    checker.scholar = FakeScholar()
    doc = (
        "The tower was completed in 1889 for the world fair in Paris.\n\n"
        "The museum is open every day of the year [1].\n\n"
        "References\n[1] https://facts.example/tower\n"
        "[2] Doe, J. (2019). A history of imaginary towers. Fake Journal, 3, 1-9.\n"
    )
    report = checker.run(doc)
    uncited = report.claims[0]
    assert uncited.verdict == NO_CITATION
    assert uncited.replacement and uncited.replacement.url == "https://doi.org/10.1/tower"
    assert (
        uncited.replacement.source_type == "peer-reviewed"
        and "Lee, A. (2001)" in uncited.replacement.citation
    )
    assert report.claims[1].citations[0].source_type == "web"
    assert len(report.references) == 1 and report.references[0]["status"] == "NOT FOUND"
    assert report.to_dict()["references"]
