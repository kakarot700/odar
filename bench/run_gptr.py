import asyncio, json, os, sys, time, httpx
calls = {"llm": 0, "other_th": 0, "fetch_hosts": []}
_orig_async = httpx.AsyncClient.send
_orig_sync = httpx.Client.send
def _count(req):
    u = str(req.url)
    if "tokenharbor.ai" in u:
        if "chat/completions" in u: calls["llm"] += 1
        else: calls["other_th"] += 1
async def a_send(self, req, *a, **k):
    _count(req); return await _orig_async(self, req, *a, **k)
def s_send(self, req, *a, **k):
    _count(req); return _orig_sync(self, req, *a, **k)
httpx.AsyncClient.send = a_send; httpx.Client.send = s_send
from gpt_researcher import GPTResearcher
async def main(qid):
    q = json.load(open(os.path.join(os.path.dirname(__file__), "questions.json")))[qid]
    t = time.time(); status = "ok"; report = ""; r = None
    try:
        r = GPTResearcher(query=q, report_type="research_report")
        await r.conduct_research()
        report = await r.write_report()
    except Exception as e:
        status = f"error: {type(e).__name__}: {e}"[:500]
    out = {"qid": qid, "status": status, "secs": round(time.time() - t, 1), "llm_calls": calls["llm"],
           "other_tokenharbor_calls": calls["other_th"],
           "visited_urls": sorted(r.visited_urls) if r else [], "source_urls": r.get_source_urls() if r else [],
           "costs_reported": r.get_costs() if r else None}
    open(f"{{os.path.dirname(__file__)}}/gptr_{qid}.md", "w").write(report or "")
    json.dump(out, open(f"{{os.path.dirname(__file__)}}/gptr_{qid}.json", "w"), indent=1)
    print(json.dumps({k: v for k, v in out.items() if k not in ("visited_urls",)})[:800])
asyncio.run(main(sys.argv[1]))
