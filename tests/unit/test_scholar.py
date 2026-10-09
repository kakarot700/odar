"""Academic sources: parsing, validation, styles, quality labels (offline fakes)."""

import json
from urllib.parse import parse_qs, urlparse

from odar.scholar import (
    MISMATCH,
    NOT_FOUND,
    UNCHECKED,
    VERIFIED,
    Paper,
    Scholar,
    bibliography,
    check_references,
    format_citation,
    parse_reference,
    split_references,
)
from odar.source_quality import quality_label

LECUN = {
    "DOI": "10.1038/nature14539",
    "title": ["Deep learning"],
    "author": [
        {"given": "Yann", "family": "LeCun"},
        {"given": "Yoshua", "family": "Bengio"},
        {"given": "Geoffrey", "family": "Hinton"},
    ],
    "issued": {"date-parts": [[2015, 5, 27]]},
    "container-title": ["Nature"],
    "type": "journal-article",
    "volume": "521",
    "issue": "7553",
    "page": "436-444",
}
ATTENTION_FEED = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/1706.03762v7</id><published>2017-06-12T00:00:00Z</published>
<title>Attention Is All You Need</title><summary>The dominant sequence transduction models are based on
recurrent networks. We propose the Transformer, based solely on attention mechanisms.</summary>
<author><name>Ashish Vaswani</name></author><author><name>Noam Shazeer</name></author></entry></feed>"""


def fake_get(url):
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    if parsed.netloc == "api.crossref.org" and parsed.path == "/works/10.1038/nature14539":
        return 200, json.dumps({"message": LECUN})
    if parsed.netloc == "api.crossref.org" and parsed.path.startswith("/works/"):
        return 404, "Resource not found."
    if parsed.netloc == "doi.org":
        return 404, "{}"
    if parsed.netloc == "api.crossref.org":
        query = q.get("query.bibliographic", [""])[0].lower()
        items = [LECUN] if "deep learning" in query else []
        if "quantum" in query:
            items = [dict(LECUN, title=["Quantum circuits for malaria models"], DOI="10.1/q")]
        return 200, json.dumps({"message": {"items": items}})
    if parsed.netloc == "export.arxiv.org":
        if "1706.03762" in url or "attention" in url.lower():
            return 200, ATTENTION_FEED
        return 200, '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    if "eutils" in parsed.netloc:
        return 200, json.dumps({"esearchresult": {"idlist": []}})
    return 429, ""


def scholar():
    return Scholar(get=fake_get)


def test_parse_reference_variants():
    apa = parse_reference(
        "LeCun, Y., Bengio, Y., & Hinton, G. (2015). Deep learning. Nature, 521, 436-444. "
        "https://doi.org/10.1038/nature14539"
    )
    assert apa.doi == "10.1038/nature14539" and apa.year == 2015 and apa.first_author == "lecun"
    assert apa.title == "Deep learning"
    mla = parse_reference('Vaswani, Ashish, et al. "Attention Is All You Need." arXiv:1706.03762, 2017.')
    assert mla.arxiv_id == "1706.03762" and mla.title == "Attention Is All You Need."[:-1]
    ieee = parse_reference('[3] J. Smith, "A study of things that matter," Proc. X, 2020. PMID: 12345678')
    assert ieee.pmid == "12345678" and ieee.first_author == "smith"


def test_validate_verified_mismatch_and_fabricated():
    s = scholar()
    ok = s.validate("LeCun, Y. (2015). Deep learning. Nature. doi:10.1038/nature14539")
    assert ok.status == VERIFIED and ok.matched["venue"] == "Nature"
    wrong_year = s.validate("LeCun, Y. (2019). Deep learning. Nature. https://doi.org/10.1038/nature14539")
    assert wrong_year.status == MISMATCH and any("2015" in p for p in wrong_year.problems)
    fake = s.validate(
        "Smith, J. (2021). Quantum gradient descent cures malaria. https://doi.org/10.9999/fake.1"
    )
    assert fake.status == NOT_FOUND and "does not exist" in fake.problems[0]
    arxiv = s.validate("Vaswani, A. et al. (2017). Attention is all you need. arXiv:1706.03762")
    assert (
        arxiv.status == VERIFIED
        and arxiv.matched["is_preprint"] is True
        or arxiv.matched["kind"] == "preprint"
    )


def test_unreachable_indexes_are_unchecked_not_fabricated():
    s = Scholar(get=lambda url: (0, ""))
    result = s.validate("Doe, J. (2020). Some study on things. Journal of Stuff.")
    assert result.status == UNCHECKED


def test_styles():
    p = Paper(
        title="Deep learning",
        authors=["Yann LeCun", "Yoshua Bengio", "Geoffrey Hinton"],
        year=2015,
        venue="Nature",
        doi="10.1038/nature14539",
        volume="521",
        issue="7553",
        pages="436-444",
        kind="journal-article",
    )
    assert (
        format_citation(p, "apa")
        == "LeCun, Y., Bengio, Y., & Hinton, G. (2015). Deep learning. Nature, 521(7553), 436-444. "
        "https://doi.org/10.1038/nature14539"
    )
    assert format_citation(p, "mla").startswith(
        'LeCun, Yann, et al. "Deep learning." Nature, vol. 521, no. 7553, 2015'
    )
    assert format_citation(p, "chicago").startswith(
        'LeCun, Yann, Yoshua Bengio, and Geoffrey Hinton. "Deep learning." Nature 521, no. 7553 (2015): 436-444.'
    )
    assert format_citation(p, "ieee", 1) == (
        '[1] Y. LeCun, Y. Bengio, and G. Hinton, "Deep learning," Nature, vol. 521, no. 7553, '
        "pp. 436-444, 2015, doi: 10.1038/nature14539."
    )
    assert bibliography([p, Paper(title="A", authors=["Ann Zed"], year=2020)], "apa")[0].startswith("LeCun")


def test_split_and_check_references_builds_bibliography():
    text = (
        "References\n"
        "1. LeCun, Y., Bengio, Y., & Hinton, G. (2015). Deep learning. Nature. https://doi.org/10.1038/nature14539\n"
        "2. Smith, J. (2021). Quantum gradient descent cures malaria. J Imag Med.\n"
        "   continued on a second line.\n"
    )
    assert len(split_references(text)) == 2
    result = check_references(text, "ieee", scholar=scholar())
    assert result["counts"][VERIFIED] == 1 and result["counts"][NOT_FOUND] == 1
    assert result["bibliography"][0].startswith("[1] Y. LeCun")
    fabricated = result["references"][1]
    assert fabricated["suggestions"], "fix-my-citations should offer real papers"


def test_quality_labels():
    cases = {
        "https://doi.org/10.1038/nature14539": "peer-reviewed",
        "https://pubmed.ncbi.nlm.nih.gov/123/": "peer-reviewed",
        "https://arxiv.org/abs/1706.03762": "preprint",
        "https://www.cdc.gov/flu": "government",
        "https://mohfw.gov.in/x": "government",
        "https://www.who.int/news": "organization",
        "https://cs.stanford.edu/page": "academic",
        "https://en.wikipedia.org/wiki/Malaria": "encyclopedia",
        "https://www.reuters.com/world/x": "news",
        "https://someone.medium.com/post": "blog",
        "https://www.reddit.com/r/x": "forum",
        "https://example.com/page": "web",
    }
    for url, label in cases.items():
        assert quality_label(url) == label, url
