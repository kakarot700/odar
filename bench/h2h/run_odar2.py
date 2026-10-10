import tempfile
import json, sys, time, subprocess, os, requests
start, end, port = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
qs = open("questions.txt").read().strip().splitlines()
env = dict(os.environ, ODAR_WEB_DB=f"{tempfile.gettempdir()}/h2h{port}.db", ODAR_ASK_PER_HOUR="100")
srv = subprocess.Popen([sys.executable,"-m","odar.cli","serve","--port",port],
    cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")), env=env, stdout=open(f"{tempfile.gettempdir()}/h2h{port}.log","w"), stderr=subprocess.STDOUT)
s = requests.Session(); s.trust_env = False
try:
    for _ in range(60):
        try:
            if s.get(f"http://127.0.0.1:{port}/api/health", timeout=2).ok: break
        except Exception: pass
        time.sleep(1)
    for i in range(start, end + 1):
        t = time.time()
        try:
            r = s.post(f"http://127.0.0.1:{port}/api/ask", json={"question": qs[i-1], "images": False}, timeout=280)
            d = r.json()
        except Exception as e:
            d = {"error": repr(e)}
        d["q"] = i; d["seconds"] = round(time.time() - t, 1)
        json.dump(d, open(f"{os.environ.get('PREFIX','odar')}_q{i}.json", "w"), indent=1)
        print(i, d["seconds"], str(d)[:150], flush=True)
finally:
    srv.terminate()
