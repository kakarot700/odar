"""Web app: store, input parsers, exporters, and the HTTP API with fake runners."""

import io
import json
import zipfile

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from odar.web import exporters, inputs  # noqa: E402
from odar.web.app import create_app  # noqa: E402
from odar.web.store import RunStore  # noqa: E402

CHECK_RESULT = {
    "check_id": "chk",
    "trust_score": 50,
    "grade": "mixed",
    "counts": {"SUPPORTED": 1, "DEAD LINK": 1},
    "claims": [
        {
            "claim_id": "c1",
            "claim": "Malaria caused 610,000 deaths in 2024.",
            "verdict": "SUPPORTED",
            "citations": [
                {
                    "url": "https://who.int/malaria",
                    "marker": "[1]",
                    "verdict": "SUPPORTED",
                    "link_state": "live",
                    "entailment": 0.97,
                    "contradiction": 0.01,
                    "quote": "There were an estimated 610 000 malaria deaths in 2024.",
                    "note": "",
                    "judged_by": "nli",
                }
            ],
            "quote": "",
            "quote_url": "",
            "replacement": None,
            "replacement_searched": False,
            "note": "",
        },
        {
            "claim_id": "c2",
            "claim": "A vaccine eliminated malaria in Kenya — मलेरिया.",
            "verdict": "DEAD LINK",
            "citations": [],
            "quote": "",
            "quote_url": "",
            "replacement": {"url": "https://example.org/r", "title": "R", "quote": "q", "entailment": 0.9},
            "replacement_searched": True,
            "note": "",
        },
    ],
    "links": [],
    "audit_trail": [],
}


class FakeReport:
    def to_dict(self):
        return CHECK_RESULT


def fake_check(store, run_id, text, **options):
    store.update(run_id, status="RUNNING", stage="opening cited pages")
    store.add_event(run_id, "fetch", {"url": "https://who.int/malaria"})
    store.update(run_id, status="COMPLETE", stage="finished", result=CHECK_RESULT)


def fake_research(store, run_id, question, **options):
    store.add_event(run_id, "telemetry.plan", {"subquestions": "3"})
    result = {
        "status": "COMPLETE",
        "uncertainty": "LOW",
        "synthesis": "# Answer\n\nMalaria deaths fell [1].\n\n## Sources\n- [1] https://who.int/malaria",
        "citation_check": CHECK_RESULT if options.get("verify") else None,
    }
    store.update(run_id, status="COMPLETE", stage="finished", result=result)


def boom(store, run_id, text, **options):
    raise RuntimeError("model route down")


REFS_RESULT = {
    "style": "apa",
    "counts": {"VERIFIED": 1, "NOT FOUND": 1},
    "references": [
        {
            "raw": "LeCun (2015) Deep learning",
            "status": "VERIFIED",
            "matched": {"title": "Deep learning", "link": "https://doi.org/10.1038/nature14539"},
            "problems": [],
            "title_similarity": 1.0,
            "formatted": "LeCun, Y. (2015). Deep learning. Nature.",
            "suggestions": [],
        },
        {
            "raw": "Smith (2021) Fake",
            "status": "NOT FOUND",
            "matched": None,
            "problems": ["DOI does not exist"],
            "title_similarity": 0.0,
            "formatted": "",
            "suggestions": [{"title": "Real paper", "year": 2020, "link": "https://doi.org/10.1/r"}],
        },
    ],
    "bibliography": ["LeCun, Y. (2015). Deep learning. Nature."],
}


def fake_refs(store, run_id, text, **options):
    store.update(
        run_id, status="COMPLETE", stage="finished", result=dict(REFS_RESULT, style=options["style"])
    )


@pytest.fixture()
def client(tmp_path):
    store = RunStore(str(tmp_path / "web.db"))
    app = create_app(
        store,
        check_runner=fake_check,
        research_runner=fake_research,
        references_runner=fake_refs,
        url_reader=lambda url: ("Article text with a link https://who.int/malaria", "Article"),
        synchronous=True,
    )
    return TestClient(app)


def _docx(paragraphs, link=None):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    rels = ""
    if link:
        body += '<w:p><w:hyperlink r:id="rId9"><w:r><w:t>source</w:t></w:r></w:hyperlink></w:p>'
        rels = (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId9" Type="hyperlink" Target="{link}" TargetMode="External"/></Relationships>'
        )
    doc = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", doc)
        if rels:
            z.writestr("word/_rels/document.xml.rels", rels)
    return buf.getvalue()


# ---------------------------------------------------------------- inputs
def test_docx_text_keeps_hyperlink_targets():
    text = inputs.text_from_docx(_docx(["First claim.", "Second claim."], link="https://who.int/x"))
    assert "First claim." in text and "https://who.int/x" in text


def test_upload_rejects_unknown_and_oversized():
    with pytest.raises(inputs.InputError):
        inputs.text_from_upload("a.exe", b"MZ....")
    with pytest.raises(inputs.InputError):
        inputs.text_from_upload("a.txt", b"x" * (inputs.MAX_UPLOAD_BYTES + 1))
    with pytest.raises(inputs.InputError):
        inputs.text_from_upload("empty.txt", b"   ")


# ---------------------------------------------------------------- exporters
def test_exports_md_docx_pdf_including_hindi():
    md = exporters.check_markdown(CHECK_RESULT, "My answer")
    assert "Trust score: 50/100" in md and '"There were an estimated 610 000' in md
    assert "suggested source: https://example.org/r" in md
    docx = exporters.to_docx(md)
    names = zipfile.ZipFile(io.BytesIO(docx)).namelist()
    assert "word/document.xml" in names and "[Content_Types].xml" in names
    assert "मलेरिया" in zipfile.ZipFile(io.BytesIO(docx)).read("word/document.xml").decode()
    pytest.importorskip("fpdf")
    pdf = exporters.to_pdf(md)
    assert pdf[:5] == b"%PDF-" and len(pdf) > 800


def test_research_markdown_appends_citation_check():
    md = exporters.research_markdown(
        {
            "synthesis": "Answer body",
            "status": "COMPLETE",
            "uncertainty": "LOW",
            "citation_check": CHECK_RESULT,
        },
        "Question",
    )
    assert md.startswith("# Question") and "## Citation check" in md


# ---------------------------------------------------------------- API
def test_check_run_lifecycle_share_export_delete(client):
    r = client.post("/api/runs", data={"mode": "check", "text": "Claim [1].\n\n[1] https://who.int/malaria"})
    assert r.status_code == 201
    run_id, token = r.json()["run_id"], r.json()["share_token"]
    data = client.get(f"/api/runs/{run_id}").json()
    assert data["run"]["status"] == "COMPLETE" and data["run"]["result"]["trust_score"] == 50
    assert any(e["kind"] == "fetch" for e in data["events"])
    assert client.get("/api/runs").json()["runs"][0]["run_id"] == run_id
    # share link: read-only, no run_id leaked, exports work
    shared = TestClient(client.app).get(f"/api/share/{token}").json()
    assert "run_id" not in shared["run"] and shared["run"]["result"]["grade"] == "mixed"
    for fmt in ("md", "docx", "pdf"):
        exp = client.get(f"/api/share/{token}/export.{fmt}")
        assert exp.status_code == 200 and exp.headers["content-disposition"].endswith(f'.{fmt}"')
    assert client.get(f"/api/runs/{run_id}/export.exe").status_code == 400
    assert client.delete(f"/api/runs/{run_id}").json()["deleted"] is True
    assert client.get(f"/api/share/{token}").status_code == 404


def test_other_browsers_cannot_read_or_delete_by_run_id(client):
    run_id = client.post("/api/runs", data={"mode": "check", "text": "x claim here"}).json()["run_id"]
    stranger = TestClient(client.app)
    assert stranger.get(f"/api/runs/{run_id}").status_code == 404
    assert stranger.delete(f"/api/runs/{run_id}").status_code == 404
    assert stranger.get("/api/runs").json()["runs"] == []


def test_research_run_with_verify_and_url_and_upload(client):
    rid = client.post(
        "/api/runs", data={"mode": "research", "text": "Is malaria declining worldwide?"}
    ).json()["run_id"]
    run = client.get(f"/api/runs/{rid}").json()["run"]
    assert run["result"]["citation_check"]["trust_score"] == 50
    rid = client.post("/api/runs", data={"mode": "check", "url": "https://news.example/a"}).json()["run_id"]
    run = client.get(f"/api/runs/{rid}").json()["run"]
    assert run["input_meta"]["source"] == "url" and run["title"] == "Article"
    files = {"file": ("essay.docx", _docx(["An essay claim."]), "application/octet-stream")}
    rid = client.post("/api/runs", data={"mode": "check", "keep_input": "false"}, files=files).json()[
        "run_id"
    ]
    run = client.get(f"/api/runs/{rid}").json()["run"]
    assert run["input_meta"]["source"] == "docx" and run["input_text"] == ""


def test_bad_inputs_and_failed_runner(tmp_path):
    store = RunStore(str(tmp_path / "w.db"))
    c = TestClient(create_app(store, check_runner=boom, research_runner=fake_research, synchronous=True))
    assert c.post("/api/runs", data={"mode": "write-my-essay", "text": "x"}).status_code == 400
    assert c.post("/api/runs", data={"mode": "check", "text": "   "}).status_code == 400
    assert c.post("/api/runs", data={"mode": "research", "text": "hi"}).status_code == 400
    rid = c.post("/api/runs", data={"mode": "check", "text": "a real claim"}).json()["run_id"]
    run = c.get(f"/api/runs/{rid}").json()["run"]
    assert run["status"] == "FAILED" and "model route down" in run["error"]
    assert c.get(f"/api/runs/{rid}/export.md").status_code == 409


def test_active_run_limit(tmp_path):
    store = RunStore(str(tmp_path / "w.db"))
    c = TestClient(
        create_app(store, check_runner=lambda *a, **k: None, research_runner=fake_research, synchronous=True)
    )
    for _ in range(2):
        assert c.post("/api/runs", data={"mode": "check", "text": "a claim"}).status_code == 201
    assert c.post("/api/runs", data={"mode": "check", "text": "a claim"}).status_code == 429


def test_index_and_share_page_serve_frontend(client):
    for path in ("/", "/history", "/r/abc", "/runs/abc"):
        page = client.get(path)
        assert page.status_code == 200 and "ODAR" in page.text
    assert client.get("/static/app.js").status_code == 200


def test_store_marks_interrupted_runs_failed(tmp_path):
    path = str(tmp_path / "s.db")
    store = RunStore(path)
    run = store.create("check", "t", "text")
    store.update(run["run_id"], status="RUNNING")
    store.close()
    again = RunStore(path)
    assert again.requeue_stale() == 1
    assert again.get(run["run_id"])["status"] == "FAILED"
    assert json.dumps(again.history(""))


def test_references_mode_and_export(client):
    bad = client.post(
        "/api/runs", data={"mode": "references", "text": "x reference here", "style": "harvardish"}
    )
    assert bad.status_code == 400
    r = client.post(
        "/api/runs", data={"mode": "references", "text": "LeCun (2015) Deep learning", "style": "mla"}
    )
    rid = r.json()["run_id"]
    run = client.get(f"/api/runs/{rid}").json()["run"]
    assert run["title"] == "References (MLA)" and run["result"]["counts"]["NOT FOUND"] == 1
    md = client.get(f"/api/runs/{rid}/export.md").text
    assert "real alternative: Real paper (2020)" in md and "## Bibliography" in md
    assert client.get(f"/api/runs/{rid}/export.docx").status_code == 200
