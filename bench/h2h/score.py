"""Score one system's answers with the same ODAR checker. usage: score.py SYS Q1 Q2"""
import json, sys, time, os
from odar.check import check_text, CheckLimits
sysn, a, b = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
for q in range(a, b + 1):
    fn = f"{sysn}_q{q}.json"
    if not os.path.exists(fn) or os.path.exists(f"score_{sysn}_q{q}.json"):
        continue
    d = json.load(open(fn))
    srcs = d.get("sources") or []
    urls = [s["url"] if isinstance(s, dict) else s for s in srcs]
    text = d["answer"] + "\n\nReferences\n" + "\n".join(f"[{i}] {u}" for i, u in enumerate(urls, 1))
    t = time.time()
    try:
        rep = check_text(text, use_llm=True, search=False, limits=CheckLimits(deadline_s=150))
        out = rep.to_dict()
    except Exception as e:
        out = {"error": repr(e)}
    out["score_s"] = round(time.time() - t, 1)
    json.dump(out, open(f"score_{sysn}_q{q}.json", "w"), indent=1)
    print(sysn, q, out.get("trust_score"), out.get("counts") or {k: v for k, v in out.items() if k in ("summary", "error")}, flush=True)
