"""Tier 3/4: API keys, rate limits, follow-ups, privacy, Hindi, Google Docs, freshness, cache."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from odar import assist, freshness  # noqa: E402
from odar.scholar import cached_http_get  # noqa: E402
from odar.web import inputs, runners  # noqa: E402
from odar.web.app import create_app  # noqa: E402
from odar.web.store import RunStore  # noqa: E402

RESULT = {
    "trust_score": 50,
    "grade": "mixed",
    "counts": {},
    "claims": [{"claim": "Malaria deaths were 610,000 in 2024.", "verdict": "SUPPORTED", "citations": []}],
}


def done_check(store, run_id, text, **options):
    store.update(run_id, status="COMPLETE", stage="finished", result=RESULT)


def make(tmp_path, monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    store = RunStore(str(tmp_path / "t.db"))
    app = create_app(
        store,
        check_runner=done_check,
        research_runner=done_check,
        references_runner=done_check,
        followup_fn=lambda mode, result, q, lang: {"answer": f"[{lang}] 610,000 [1]", "abstained": False},
        synchronous=True,
    )
    return TestClient(app), store


def test_api_keys_create_use_limit_revoke(tmp_path, monkeypatch):
    c, store = make(tmp_path, monkeypatch, ODAR_KEY_DAILY_LIMIT=2)
    key = c.post("/api/keys", json={"label": "ext"}).json()["key"]
    assert key.startswith("odar_")
    bot = TestClient(c.app)
    auth = {"Authorization": f"Bearer {key}"}
    rid = bot.post("/api/runs", data={"mode": "check", "text": "a claim [1]"}, headers=auth).json()["run_id"]
    # key runs land in the key owner's browser history
    assert c.get("/api/runs").json()["runs"][0]["run_id"] == rid
    assert bot.post("/api/runs", data={"mode": "check", "text": "a claim"}, headers=auth).status_code == 201
    assert bot.post("/api/runs", data={"mode": "check", "text": "a claim"}, headers=auth).status_code == 429
    assert bot.get("/api/keys", headers=auth).status_code == 403  # keys can't manage keys
    assert bot.get("/api/limits", headers=auth).json()["used_today"] == 2
    prefix = c.get("/api/keys").json()["keys"][0]["prefix"]
    assert c.delete(f"/api/keys/{prefix}").json()["revoked"]
    assert bot.get("/api/runs", headers=auth).status_code == 401
    assert bot.get("/api/runs", headers={"Authorization": "Bearer odar_nope"}).status_code == 401


def test_ip_rate_limit(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch, ODAR_IP_RUNS_PER_HOUR=2)
    codes = [
        TestClient(c.app).post("/api/runs", data={"mode": "check", "text": "a claim"}).status_code
        for _ in range(3)
    ]
    assert codes == [201, 201, 429]


def test_followups_and_forget_me(tmp_path, monkeypatch):
    c, store = make(tmp_path, monkeypatch)
    rid = c.post("/api/runs", data={"mode": "check", "text": "a claim [1]"}).json()["run_id"]
    ans = c.post(f"/api/runs/{rid}/ask", json={"question": "कितनी मौतें हुईं?"}).json()
    assert ans["answer"].startswith("[hi]")
    assert c.post(f"/api/runs/{rid}/ask", json={"question": "x"}).status_code == 400
    assert TestClient(c.app).post(f"/api/runs/{rid}/ask", json={"question": "who?"}).status_code == 404
    assert c.get(f"/api/runs/{rid}/followups").json()["followups"][0]["question"].startswith("कितनी")
    share = c.get(f"/api/runs/{rid}").json()["run"]["share_token"]
    assert c.get(f"/api/share/{share}").headers["x-robots-tag"].startswith("noindex")
    assert c.delete("/api/me").json()["deleted_runs"] == 1
    assert store.get(rid) is None


def test_retention_purge(tmp_path):
    store = RunStore(str(tmp_path / "r.db"))
    run = store.create("check", "t", "x")
    store._conn.execute("UPDATE runs SET created = created - 40 * 86400 WHERE run_id = ?", (run["run_id"],))
    store._conn.commit()
    assert store.purge_older_than(30) == 1 and store.get(run["run_id"]) is None


def test_hindi_check_translates_then_checks(tmp_path):
    store = RunStore(str(tmp_path / "h.db"))
    run = store.create("check", "t", "x")
    seen = {}

    class Rep:
        def to_dict(self):
            return {"trust_score": 100, "claims": []}

    def fake_check(text, **kw):
        seen["text"] = text
        return Rep()

    runners.run_check(
        store,
        run["run_id"],
        "मलेरिया से 2024 में 6 लाख मौतें हुईं [1]\n[1] https://who.int/x",
        academic=False,
        check_fn=fake_check,
        translate_fn=lambda text, target: ("Malaria killed 600,000 in 2024 [1]\n[1] https://who.int/x", True),
    )
    result = store.get(run["run_id"])["result"]
    assert seen["text"].startswith("Malaria") and result["language"] == "hi" and result["translated"]


def test_translate_keeps_markers_and_urls():
    text = "Deaths fell [1]. See https://who.int/report for details."
    ok = assist.translate(text, "hi", lambda s, p, m: p.replace("Deaths fell", "मौतें घटीं"))
    assert ok[1] and "https://who.int/report" in ok[0] and "[1]" in ok[0]
    lost = assist.translate(text, "hi", lambda s, p, m: "मौतें घटीं।")
    assert lost == (text, False)
    assert assist.detect_language("यह हिंदी है") == "hi" and assist.detect_language("plain English") == "en"


def test_followup_grounding_and_abstention():
    result = {"synthesis": "Malaria deaths were 610,000 in 2024 [1].", "status": "COMPLETE"}
    grounded = assist.answer_followup(
        "research",
        result,
        "How many?",
        lambda s, p, m: "About 610,000 died in 2024 [1]. Cases hit 999 million.",
    )
    assert grounded["answer"] == "About 610,000 died in 2024 [1]." and grounded["dropped"] == 1
    no = assist.answer_followup("research", result, "Who won?", lambda s, p, m: assist.ABSTAIN)
    assert no["abstained"] and no["answer"] == assist.ABSTAIN
    assert assist.answer_followup("research", result, "q?", None)["abstained"]


def test_google_doc_export_keeps_links():
    markup = (
        "<html><head><title>Essay</title><style>p{}</style></head><body><p>Deaths fell "
        '<a href="https://www.google.com/url?q=https://who.int/r&amp;sa=D">WHO</a> in 2024.</p></body></html>'
    )
    text, title = inputs.text_from_google_doc(
        "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit",
        get=lambda u: (200, markup),
    )
    assert title == "Essay" and "WHO (https://who.int/r)" in text
    with pytest.raises(inputs.InputError):
        inputs.text_from_google_doc(
            "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit",
            get=lambda u: (403, ""),
        )


def test_freshness():
    assert freshness.source_year("https://news.example/2019/05/story", "") == 2019
    assert freshness.source_year("https://x.org/a", "Last updated: 12 March 2023. Text") == 2023
    assert freshness.source_year("https://x.org/a", "no dates here") is None
    old = freshness.this_year() - 5
    assert "check for newer data" in freshness.freshness_warning("Inflation is currently 5%.", old)
    assert "claim is about" in freshness.freshness_warning(f"In {old + 2} sales rose.", old)
    assert freshness.freshness_warning("Water boils at 100 C.", old) == ""


def test_cached_http_get(tmp_path):
    store = RunStore(str(tmp_path / "c.db"))
    calls = []

    def inner(url):
        calls.append(url)
        return (200, "ok") if "good" in url else (429, "")

    get = cached_http_get(store, inner)
    assert get("https://api.crossref.org/good") == (200, "ok")
    assert get("https://api.crossref.org/good") == (200, "ok")
    get("https://api.crossref.org/limited")
    get("https://api.crossref.org/limited")
    assert (
        calls.count("https://api.crossref.org/good") == 1
        and calls.count("https://api.crossref.org/limited") == 2
    )
