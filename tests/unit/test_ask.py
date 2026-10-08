"""Ask (fast cited answers), threads, focus modes, projects, images and Discover.

Every network and model call is faked: ddgs, the page fetcher, the LLM stream
and the citation judge.
"""

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from odar.check import PARTIAL, SUPPORTED, WRONG_SOURCE, CitationVerdict  # noqa: E402
from odar.schemas import ExtractedPage  # noqa: E402
from odar.web import ask  # noqa: E402
from odar.web.app import create_app  # noqa: E402
from odar.web.projects import bm25_search, chunk_text, tokens  # noqa: E402
from odar.web.store import RunStore  # noqa: E402


# ---------------------------------------------------------------------- fakes
class FakeDDGS:
    def __init__(self):
        self.calls = []

    def text(self, query, **kw):
        self.calls.append(("text", query, kw))
        if "site:reddit.com" in query:
            return [{"href": "https://www.reddit.com/r/space/x", "title": "Mars thread", "body": "people discuss"}]
        if "forum" in query:
            return [{"href": "https://forum.example.org/t/1", "title": "Forum post", "body": "forum talk"}]
        if "site:youtube.com" in query:
            return [{"href": "https://www.youtube.com/watch?v=abc", "title": "Mars video", "body": "desc"}]
        return [
            {"href": "https://nasa.gov/mars", "title": "Mars facts", "body": "Mars is the fourth planet."},
            {"href": "http://127.0.0.1/admin", "title": "internal", "body": "ssrf"},
            {"href": "https://en.wikipedia.org/wiki/Mars", "title": "Mars - Wikipedia", "body": "Red planet."},
            {"href": "https://nasa.gov/mars", "title": "dup", "body": "dup"},
        ]

    def news(self, query, **kw):
        self.calls.append(("news", query, kw))
        return [
            {
                "date": "Wed 2026-10-07 9:52 PM IST (UTC+05:30)",
                "title": f"Headline about {query}",
                "body": "news body",
                "url": "https://news.example.com/a",
                "image": "https://img.example.com/a.jpg",
                "source": "Example News",
            },
            {"date": "2026-10-07T10:00:00+00:00", "title": "bad", "url": "file:///etc/passwd", "source": "x"},
        ]

    def videos(self, query, **kw):
        raise RuntimeError("No results found.")

    def images(self, query, **kw):
        return [
            {"title": "ok", "image": "https://cdn.example.com/m.jpg", "thumbnail": "https://tse.example.com/t",
             "url": "https://britannica.com/mars", "width": "800", "height": "600"},
            {"title": "http thumb", "image": "http://cdn.example.com/x.jpg", "thumbnail": "http://t.example.com/t",
             "url": "https://example.com/p"},
            {"title": "internal", "image": "https://169.254.169.254/latest", "thumbnail": "https://169.254.169.254/x",
             "url": "https://example.com/q"},
            {"title": "js", "image": "javascript:alert(1)", "thumbnail": "javascript:alert(1)", "url": "https://e.com"},
        ]


class FakeExtractor:
    def __init__(self, pages):
        self.pages = pages
        self.seen = []

    def extract(self, url):
        self.seen.append(url)
        text = self.pages.get(url)
        if text is None:
            return ExtractedPage(url=url, ok=False, error="blocked")
        return ExtractedPage(url=url, ok=True, text=text, title="t", chars=len(text))


class FakeChecker:
    def __init__(self):
        self.calls = []

    def judge(self, claim, cit, link):
        self.calls.append((claim, cit.url, link.text[:40]))
        if "fourth planet" in claim:
            return CitationVerdict(url=cit.url, marker=cit.marker, verdict=SUPPORTED,
                                   quote="Mars is the fourth planet from the Sun.")
        if "red" in claim.lower():
            return CitationVerdict(url=cit.url, marker=cit.marker, verdict=PARTIAL, quote="Red planet.")
        return CitationVerdict(url=cit.url, marker=cit.marker, verdict=WRONG_SOURCE)


ANSWER = ["Mars is the fourth planet ", "from the Sun.[1] It looks red", " [2]. It has rings made of cheese [1, 2]."]


def fake_deps(answer=ANSWER, fail=False, seen=None):
    ddgs = FakeDDGS()
    checker = FakeChecker()

    def search(query, focus, n, **kw):
        if seen is not None:
            seen.append((query, focus))
        return ask.search_sources(query, focus, n, ddgs=ddgs, **kw)

    def fetch(sources):
        for s in sources:
            s.setdefault("text", s.get("snippet", ""))
            if s.get("url") == "https://nasa.gov/mars":
                s["text"] = "Mars is the fourth planet from the Sun. " * 3

    def stream(system, prompt, max_tokens):
        if seen is not None:
            seen.append(("prompt", system, prompt))
        if fail:
            raise RuntimeError("429 from every free route")
        yield from answer

    deps = ask.AskDeps(
        search=search,
        fetch=fetch,
        stream=stream,
        verify=lambda a, s: ask.verify_answer(a, s, checker=checker),
        images=lambda q: ask.image_search(q, ddgs=ddgs),
    )
    deps.extra = {"ddgs": ddgs, "checker": checker}
    return deps


def parse_sse(text):
    events = []
    for block in text.split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            events.append((name, data))
    return events


def make(tmp_path, monkeypatch=None, deps=None, discover_fn=None, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    store = RunStore(str(tmp_path / "a.db"))
    app = create_app(store, ask_deps=deps or fake_deps(), discover_fn=discover_fn, synchronous=True)
    return TestClient(app), store


# ---------------------------------------------------------------------- unit helpers
def test_search_focus_modes_filter_unsafe_and_label():
    d = FakeDDGS()
    web = ask.search_sources("mars", "all", 6, ddgs=d)
    assert [s["url"] for s in web] == ["https://nasa.gov/mars", "https://en.wikipedia.org/wiki/Mars"]
    assert web[0]["domain"] == "nasa.gov" and web[0]["kind"] == "web"
    news = ask.search_sources("mars", "news", 6, ddgs=d)
    assert len(news) == 1 and news[0]["kind"] == "news" and news[0]["publisher"] == "Example News"
    assert news[0]["date"] == "2026-10-07T21:52:00+05:30"  # freshness shown in the UI
    forum = ask.search_sources("mars", "reddit", 6, ddgs=d)
    assert {s["kind"] for s in forum} == {"forum"} and {s["domain"] for s in forum} == {"reddit.com", "forum.example.org"}
    vids = ask.search_sources("mars", "youtube", 6, ddgs=d)  # videos() raised: falls back to site:youtube.com
    assert vids[0]["kind"] == "video" and vids[0]["domain"] == "youtube.com"


def test_academic_focus_uses_scholar():
    class P:
        title, abstract, link, open_access_url, year, venue, peer_reviewed = (
            "Mars paper", "An abstract about Mars.", "https://doi.org/10.1/x", "", 2021, "Icarus", True)

    class S:
        def search(self, q, rows=5):
            return [P()]

    out = ask.search_sources("mars", "academic", 6, scholar=S())
    assert out[0]["kind"] == "paper" and out[0]["snippet"].startswith("An abstract") and out[0]["date"] == "2021"


def test_parse_date_variants():
    assert ask.parse_date("2026-10-07T10:00:00Z") == "2026-10-07T10:00:00+00:00"
    assert ask.parse_date("Wed 2026-10-07 9:05 AM IST (UTC+05:30)") == "2026-10-07T09:05:00+05:30"
    assert ask.parse_date("yesterday") == ""


def test_fetch_uses_extractor_and_keeps_snippet_on_failure():
    sources = [
        {"url": "https://a.org/x", "kind": "web", "snippet": "s1"},
        {"url": "https://b.org/2019/05/y", "kind": "web", "snippet": "s2"},
        {"url": "https://youtube.com/w", "kind": "video", "snippet": "vid"},
    ]
    ext = FakeExtractor({"https://a.org/x": "Full page text that is longer than the snippet."})
    ask.fetch_sources(sources, extractor=ext)
    assert sources[0]["text"].startswith("Full page") and sources[0]["fetched"]
    assert sources[1]["text"] == "s2" and sources[1]["year"] == 2019
    assert sources[2]["text"] == "vid" and "https://youtube.com/w" not in ext.seen  # videos: titles/descriptions only


def test_real_extractor_keeps_ssrf_guard():
    from odar.retrieval import PageExtractor

    page = PageExtractor().extract("http://169.254.169.254/latest/meta-data")
    assert not page.ok and page.quarantined


def test_markers_normalized_and_pairs_in_reading_order():
    text = ask.normalize_markers("A is B.[1] C is D [2]. E is F [1, 3].")
    assert text == "A is B [1]. C is D [2]. E is F [1][3]."
    pairs = ask.cited_pairs(text)
    assert [(p["occ"], p["n"]) for p in pairs] == [(0, 1), (1, 2), (2, 1), (3, 3)]
    assert pairs[2]["claim"] == "E is F ."


def test_verify_answer_maps_verdicts_and_bad_numbers():
    sources = [
        {"url": "https://nasa.gov/mars", "title": "Mars", "domain": "nasa.gov", "text": "Mars is the fourth planet."},
        {"url": "https://w.org", "title": "W", "domain": "w.org", "text": "Red planet."},
    ]
    answer = "Mars is the fourth planet from the Sun [1]. It looks red in the sky [2]. Moons are cheese [7]."
    res = ask.verify_answer(answer, sources, checker=FakeChecker())
    v = [c["verdict"] for c in res["citations"]]
    assert v == ["supported", "partial", "unsupported"]
    assert res["citations"][0]["quote"] == "Mars is the fourth planet from the Sun."
    assert res["citations"][0]["domain"] == "nasa.gov"
    assert "does not exist" in res["citations"][2]["note"]
    assert res["counts"] == {"supported": 1, "partial": 1, "unsupported": 1}


def test_prompt_has_sources_history_and_instructions():
    system, prompt = ask.build_prompt(
        "and its moons?",
        [{"title": "Mars", "domain": "nasa.gov", "text": "Mars has two moons, Phobos and Deimos."}],
        history=[{"q": "What is Mars?", "a": "A planet [1].", "sources": [{"title": "NASA", "domain": "nasa.gov"}]}],
        instructions="Answer like a teacher.",
    )
    assert "Answer like a teacher." in system and "[n]" not in system
    assert "[1] Mars (nasa.gov)" in prompt and "Q: What is Mars?" in prompt and "A: A planet ." in prompt
    assert ask.search_query("and its moons?", [{"q": "What is Mars?"}]) == "What is Mars? and its moons?"


def test_history_context_is_trimmed():
    hist = [{"q": f"q{i}", "a": "x" * 5000, "sources": []} for i in range(10)]
    ctx = ask.history_context(hist, limit=3000)
    assert len(ctx) <= 3000 and "q9" in ctx and "q0" not in ctx


def test_image_search_only_safe_https_links():
    imgs = ask.image_search("mars", ddgs=FakeDDGS())
    assert len(imgs) == 1 and imgs[0]["thumbnail"].startswith("https://") and imgs[0]["domain"] == "britannica.com"
    assert imgs[0]["width"] == 800


def test_discover_topic_items():
    items = ask.discover_topic("india", ddgs=FakeDDGS())
    assert len(items) == 1 and items[0]["source"] == "Example News" and items[0]["date"].startswith("2026-10-07")


def test_free_models_only(monkeypatch):
    monkeypatch.setenv("ODAR_MODEL_ROUTES", json.dumps({"writer": ["paid-model", "x:free"]}))
    assert ask.free_models("writer") == ["x:free"]


def test_bm25_and_chunking():
    text = "\n\n".join(f"Paragraph {i} talks about topic{i % 3} and more words." for i in range(60))
    chunks = chunk_text(text, size=300, overlap=50)
    assert len(chunks) > 5 and all(len(c) <= 400 for c in chunks)
    docs = [{"text": "Photosynthesis converts light into chemical energy."}, {"text": "Cats sleep a lot."}]
    hits = bm25_search("how does photosynthesis use light", docs)
    assert len(hits) == 1 and hits[0]["text"].startswith("Photosynthesis")
    assert "the" not in tokens("the cat")


# ---------------------------------------------------------------------- HTTP
def test_ask_stream_events_order_and_thread_saved(tmp_path):
    c, store = make(tmp_path)
    r = c.post("/api/ask/stream", json={"question": "What is Mars?", "focus": "all"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    names = [n for n, _ in events]
    assert names[0] == "thread" and names[1] == "sources"
    assert names.index("delta") < names.index("done") < names.index("verification") == len(names) - 1
    assert "images" in names
    srcs = dict(events)["sources"]["sources"]
    assert [s["n"] for s in srcs] == [1, 2] and "text" not in srcs[0]
    done = dict(events)["done"]
    assert done["answer"].startswith("Mars is the fourth planet from the Sun [1].")
    assert "[1][2]" in done["answer"] and done["timing"]["ttft_s"] is not None
    ver = dict(events)["verification"]["citations"]
    # the cheese sentence failed against both sources, so the repair pass removed it
    assert [x["verdict"] for x in ver] == ["supported", "partial"]
    assert ver[0]["quote"] and ver[0]["title"] == "Mars facts"
    final = dict(events)["verification"]
    assert "cheese" not in final["answer"] and final["answer"].startswith("Mars is the fourth planet")
    assert final["repairs"][0]["action"] == "removed"
    tid = dict(events)["thread"]["thread_id"]
    thread = c.get(f"/api/threads/{tid}").json()
    roles = [m["role"] for m in thread["messages"]]
    assert roles == ["user", "assistant"]
    assert thread["messages"][1]["data"]["verification"]["counts"]["supported"] == 1
    assert thread["messages"][1]["data"]["images"][0]["domain"] == "britannica.com"
    assert "cheese" not in thread["messages"][1]["content"]


def test_followup_keeps_context(tmp_path):
    seen = []
    c, _ = make(tmp_path, deps=fake_deps(seen=seen))
    first = parse_sse(c.post("/api/ask/stream", json={"question": "What is Mars?"}).text)
    tid = dict(first)["thread"]["thread_id"]
    seen.clear()
    second = parse_sse(c.post("/api/ask/stream", json={"question": "and its moons?", "thread_id": tid}).text)
    assert dict(second)["thread"]["thread_id"] == tid
    assert seen[0] == ("What is Mars? and its moons?", "all")  # search borrows the previous question
    prompt = [s for s in seen if s[0] == "prompt"][0][2]
    assert "Q: What is Mars?" in prompt and "Mars facts (nasa.gov)" in prompt
    assert len(c.get(f"/api/threads/{tid}").json()["messages"]) == 4


def test_ask_json_and_get_stream(tmp_path):
    c, _ = make(tmp_path)
    out = c.post("/api/ask", json={"question": "What is Mars?", "images": False}).json()
    assert out["answer"].startswith("Mars") and out["sources"][0]["url"] == "https://nasa.gov/mars"
    assert out["verification"]["citations"] and "images" not in out
    r = c.get("/api/ask/stream", params={"q": "What is Mars?", "focus": "news", "verify": "false"})
    names = [n for n, _ in parse_sse(r.text)]
    assert "verification" not in names and names[-1] == "done"
    srcs = dict(parse_sse(r.text))["sources"]["sources"]
    assert srcs[0]["kind"] == "news" and srcs[0]["date"]


def test_ask_validation_and_llm_failure(tmp_path):
    c, _ = make(tmp_path, deps=fake_deps(fail=True))
    assert c.post("/api/ask/stream", json={"question": "hi"}).status_code == 400
    assert c.post("/api/ask/stream", json={"question": "What is Mars?", "focus": "tiktok"}).status_code == 400
    assert c.post("/api/ask/stream", json={"question": "What is Mars?", "thread_id": "nope"}).status_code == 404
    events = parse_sse(c.post("/api/ask/stream", json={"question": "What is Mars?"}).text)
    names = [n for n, _ in events]
    assert "sources" in names and names[-1] == "error" and "done" not in names


def test_no_sources_answer_abstains(tmp_path):
    deps = fake_deps()
    deps.search = lambda q, f, n, **kw: []
    c, _ = make(tmp_path, deps=deps)
    ev = dict(parse_sse(c.post("/api/ask/stream", json={"question": "zzzz qqqq", "images": False}).text))
    assert ev["done"]["abstained"] and ev["done"]["answer"] == ask.NO_SOURCES


def test_ask_rate_limit_separate_from_runs(tmp_path, monkeypatch):
    c, _ = make(tmp_path, monkeypatch, ODAR_ASK_PER_HOUR=2, ODAR_IP_RUNS_PER_HOUR=1)
    for _ in range(2):
        assert c.post("/api/ask", json={"question": "What is Mars?"}).status_code == 200
    assert c.post("/api/ask", json={"question": "What is Mars?"}).status_code == 429
    lim = c.get("/api/limits").json()
    assert lim["ask_used_this_hour"] == 2 and lim["ask_hourly_limit"] == 2 and lim["used_this_hour"] == 0


def test_ask_with_api_key_and_thread_ownership(tmp_path):
    c, _ = make(tmp_path)
    key = c.post("/api/keys", json={"label": "bot"}).json()["key"]
    bot = TestClient(c.app)
    auth = {"Authorization": f"Bearer {key}"}
    out = bot.post("/api/ask", json={"question": "What is Mars?"}, headers=auth).json()
    tid = out["thread_id"]
    assert c.get("/api/threads").json()["threads"][0]["thread_id"] == tid  # key owner = browser owner
    stranger = TestClient(c.app)
    assert stranger.get(f"/api/threads/{tid}").status_code == 404
    assert stranger.post("/api/ask", json={"question": "more?", "thread_id": tid}).status_code == 404
    assert bot.get("/api/limits", headers=auth).json()["ask_used_this_hour"] == 1


def test_thread_rename_share_delete(tmp_path):
    c, _ = make(tmp_path)
    out = c.post("/api/ask", json={"question": "What is Mars?"}).json()
    tid, token = out["thread_id"], out["share_token"]
    assert c.patch(f"/api/threads/{tid}", json={"title": "Mars basics"}).json()["thread"]["title"] == "Mars basics"
    assert c.patch(f"/api/threads/{tid}", json={"title": "  "}).status_code == 400
    shared = TestClient(c.app).get(f"/api/shared-threads/{token}")
    assert shared.status_code == 200 and shared.json()["thread"]["title"] == "Mars basics"
    assert "owner" not in shared.json()["thread"] and "thread_id" not in shared.json()["thread"]
    assert c.get(f"/s/{token}").status_code == 200 and c.get(f"/c/{tid}").status_code == 200
    assert c.delete(f"/api/threads/{tid}").json()["deleted"]
    assert c.get("/api/threads").json()["threads"] == []
    assert c.get(f"/api/shared-threads/{token}").status_code == 404


def test_projects_files_and_scoped_ask(tmp_path):
    seen = []
    c, store = make(tmp_path, deps=fake_deps(seen=seen))
    pid = c.post("/api/projects", json={"name": "Biology", "instructions": "Explain simply."}).json()["project"]["project_id"]
    up = c.post(
        f"/api/projects/{pid}/files",
        files={"file": ("notes.md", b"Photosynthesis converts light energy into chemical energy in chloroplasts.\n\n"
                        b"Mitochondria make ATP.", "text/markdown")},
    )
    assert up.status_code == 201 and up.json()["file"]["chunks"] >= 1
    assert c.post(f"/api/projects/{pid}/files", files={"file": ("x.exe", b"MZ..", "application/x")}).status_code == 400
    ev = dict(parse_sse(c.post("/api/ask/stream", json={
        "question": "How does photosynthesis use light?", "project_id": pid, "sources": "files"}).text))
    srcs = ev["sources"]["sources"]
    assert srcs and all(s["kind"] == "file" for s in srcs)
    assert srcs[0]["title"] == "notes.md" and "chloroplasts" in srcs[0]["passage"]
    prompt = [s for s in seen if s[0] == "prompt"][0]
    assert "Explain simply." in prompt[1] and "[1] notes.md (My files)" in prompt[2]
    assert not [s for s in seen if s[0] != "prompt"]  # files only: no web search
    both = dict(parse_sse(c.post("/api/ask/stream", json={
        "question": "How does photosynthesis use light?", "project_id": pid, "sources": "both"}).text))
    kinds = [s["kind"] for s in both["sources"]["sources"]]
    assert "web" in kinds and "file" in kinds
    proj = c.get(f"/api/projects/{pid}").json()
    assert len(proj["threads"]) == 2 and proj["files"][0]["name"] == "notes.md"
    assert c.get("/api/threads", params={"project_id": pid}).json()["threads"][0]["project_id"] == pid
    assert TestClient(c.app).get(f"/api/projects/{pid}").status_code == 404
    assert c.patch(f"/api/projects/{pid}", json={"instructions": "Be brief."}).json()["project"]["instructions"] == "Be brief."
    fid = proj["files"][0]["file_id"]
    assert c.delete(f"/api/projects/{pid}/files/{fid}").json()["deleted"]
    assert c.delete(f"/api/projects/{pid}").json()["deleted"]
    assert c.get("/api/threads").json()["threads"] == []  # project threads go with it


def test_project_upload_size_cap(tmp_path):
    c, _ = make(tmp_path)
    pid = c.post("/api/projects", json={"name": "P"}).json()["project"]["project_id"]
    big = b"x" * (10_000_001)
    assert c.post(f"/api/projects/{pid}/files", files={"file": ("a.txt", big, "text/plain")}).status_code == 400


def test_images_endpoint(tmp_path):
    c, _ = make(tmp_path)
    imgs = c.get("/api/images", params={"q": "mars"}).json()["images"]
    assert len(imgs) == 1 and imgs[0]["url"] == "https://britannica.com/mars"
    assert c.get("/api/images", params={"q": " "}).status_code == 400


def test_discover_cached(tmp_path):
    calls = []

    def disc(topic):
        calls.append(topic)
        return [{"title": f"{topic} story", "url": "https://n.example.com/1", "source": "N", "date": "2026-10-07"}]

    c, _ = make(tmp_path, discover_fn=disc)
    first = c.get("/api/discover").json()
    assert [t["id"] for t in first["topics"]] == ["world", "tech", "science", "india", "business"]
    assert first["topics"][3]["items"][0]["title"] == "india story"
    c.get("/api/discover")
    c.get("/api/discover", params={"topic": "tech"})
    assert sorted(calls) == sorted(ask.DISCOVER_TOPICS)  # second call served from SQLite
    assert c.get("/api/discover", params={"topic": "sports"}).status_code == 400


def test_forget_me_removes_threads_and_projects(tmp_path):
    c, store = make(tmp_path)
    c.post("/api/ask", json={"question": "What is Mars?"})
    c.post("/api/projects", json={"name": "P"})
    c.delete("/api/me")
    owner_threads = store._conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
    assert owner_threads == 0 and store._conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0


def test_text_search_falls_back_across_backends():
    class Flaky:
        def __init__(self):
            self.backends = []

        def text(self, query, **kw):
            self.backends.append(kw.get("backend"))
            if kw.get("backend") == ask.TEXT_BACKENDS[0]:
                raise RuntimeError("No results found.")
            return [{"href": "https://example.org/a", "title": "A", "body": "b"}]

    d = Flaky()
    out = ask.search_sources("northern lights", "all", 6, ddgs=d)
    assert d.backends == list(ask.TEXT_BACKENDS[:2]) and out[0]["domain"] == "example.org"


# ---------------------------------------------------------------------- citation accuracy
class RepairChecker:
    """Source 2 holds the 1.8 kg figure; source 1 does not."""

    def judge(self, claim, cit, link):
        if "1.8 kg" in claim and "1.8 kg" in link.text:
            return CitationVerdict(url=cit.url, marker=cit.marker, verdict=SUPPORTED, quote="a 1.8 kg difference")
        if "calories" in claim and "calories" in link.text:
            return CitationVerdict(url=cit.url, marker=cit.marker, verdict=SUPPORTED, quote="calories")
        return CitationVerdict(url=cit.url, marker=cit.marker, verdict=WRONG_SOURCE)


def _srcs():
    return [
        {"url": "https://a.org/x", "title": "A", "kind": "web", "fetched": True,
         "text": "Fasting and cutting calories gave similar results overall."},
        {"url": "https://b.org/y", "title": "B", "kind": "web", "fetched": True,
         "text": "The trial found a 1.8 kg difference in weight after a year."},
        {"url": "https://c.org/z", "title": "C", "kind": "web", "fetched": True,
         "text": "Unrelated page about gardening tools."},
    ]


def test_repair_moves_marker_to_supporting_source():
    answer = "Both cut calories with similar results [1]. One trial found a 1.8 kg difference in weight [1]."
    res = ask.verify_answer(answer, _srcs(), checker=RepairChecker())
    assert res["answer"] == "Both cut calories with similar results [1]. One trial found a 1.8 kg difference in weight [2]."
    assert [c["verdict"] for c in res["citations"]] == ["supported", "supported"]
    assert res["repairs"] == [{"claim": "One trial found a 1.8 kg difference in weight .", "from": 1, "to": 2,
                               "action": "recited"}]


def test_repair_removes_unsupported_sentence_and_meta_markers():
    answer = ("Both cut calories with similar results [1]. The sources do not single out one key result [3]. "
              "Fasting cures baldness in all adults [3].")
    res = ask.verify_answer(answer, _srcs(), checker=RepairChecker())
    assert "baldness" not in res["answer"]
    assert "The sources do not single out one key result." in res["answer"]
    assert [c["n"] for c in res["citations"]] == [1]


def test_repair_off_keeps_answer():
    answer = "Fasting cures baldness in all adults [3]."
    res = ask.verify_answer(answer, _srcs(), checker=RepairChecker(), repair=False)
    assert "answer" not in res and res["citations"][0]["verdict"] == "unsupported"


def test_prompt_hides_snippet_only_pages_when_enough_are_readable():
    srcs = _srcs() + [{"url": "https://d.org", "title": "D", "kind": "web", "snippet": "blocked page"}]
    _, prompt = ask.build_prompt("fasting?", srcs)
    assert "[4] D" not in prompt and "[2] B" in prompt
    few = [srcs[0], srcs[3]]
    _, prompt = ask.build_prompt("fasting?", few)
    assert "[2] D (search snippet only)" in prompt
    system, _ = ask.build_prompt("q", srcs)
    assert "exactly" in system and "no marker" in system
