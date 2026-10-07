"""Run executors for the web app.

* ``run_check`` verifies citations in pasted/uploaded text in-process and
  streams the checker's audit events as progress.
* ``run_research`` launches ``run_research.py run --mode deep`` in a child
  process (crash and timeout isolation), mirrors its job events as progress,
  then optionally runs ODAR Check over the finished report so every claim in
  it gets a per-claim audit with quotes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Dict, Optional

from odar.web.store import RunStore

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESEARCH_ARGS = (
    "--mode deep --subquestions 4 --reflection-rounds 1 --max-iterations 4 --max-search 14 "
    "--max-fetch 16 --max-model-calls 20 --max-verifications 80"
)
CHECK_STAGES = {
    "parse": "reading the answer",
    "atomize": "splitting claims",
    "fetch": "opening cited pages",
    "wayback": "checking the Wayback Machine",
    "verify": "matching claims to sources",
    "paraphrase_judge": "judging reworded claims",
    "search": "looking for replacement sources",
    "scholar": "searching academic papers",
    "reference": "validating references",
    "done": "finished",
}
PROGRESS_KEYS = ("url", "claim", "verdict", "state", "query", "hits", "trust_score", "claims", "error")

CheckFn = Callable[..., Any]
TranslateFn = Callable[[str, str], Any]  # (text, target) -> (text, translated?)


def _default_translate(text: str, target: str) -> Any:
    from odar.assist import make_complete, translate

    return translate(text, target, make_complete("writer"))


def _scholar(store: RunStore) -> Any:
    from odar.scholar import Scholar, cached_http_get

    return Scholar(get=cached_http_get(store))


def _progress_sink(store: RunStore, run_id: str, prefix: str = "") -> Callable[[Dict[str, Any]], None]:
    def sink(entry: Dict[str, Any]) -> None:
        kind = str(entry.get("event", ""))
        stage = CHECK_STAGES.get(kind)
        if stage is None:
            return
        store.update(run_id, stage=f"{prefix}{stage}")
        store.add_event(run_id, kind, {k: entry[k] for k in PROGRESS_KEYS if k in entry})

    return sink


def run_check(
    store: RunStore,
    run_id: str,
    text: str,
    *,
    use_llm: bool = True,
    search: bool = True,
    academic: bool = True,
    style: str = "apa",
    language: str = "auto",
    check_fn: Optional[CheckFn] = None,
    translate_fn: Optional[TranslateFn] = None,
) -> None:
    from odar.assist import detect_language

    if check_fn is None:
        from odar.check import check_text as check_fn
    store.update(run_id, status="RUNNING", stage="starting")
    store.add_event(run_id, "start", {"mode": "check", "chars": len(text)})
    source_lang = detect_language(text)
    translated = False
    if source_lang == "hi":  # NLI and most sources are English: check an English rendering
        store.update(run_id, stage="translating Hindi to English")
        text, translated = (translate_fn or _default_translate)(text, "en")
    kwargs: Dict[str, Any] = {"use_llm": use_llm, "search": search, "citation_style": style}
    if academic:
        kwargs["scholar"] = _scholar(store)
    report = check_fn(text, on_event=_progress_sink(store, run_id), **kwargs)
    result = report.to_dict()
    result["language"] = source_lang
    result["translated"] = translated
    store.update(run_id, status="COMPLETE", stage="finished", result=result)
    store.add_event(run_id, "end", {"trust_score": result.get("trust_score")})


def _research_env() -> Dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def run_research(
    store: RunStore,
    run_id: str,
    question: str,
    *,
    verify: bool = True,
    academic: bool = True,
    style: str = "apa",
    language: str = "auto",
    timeout_s: float = 540.0,
    check_fn: Optional[CheckFn] = None,
    translate_fn: Optional[TranslateFn] = None,
    command: Optional[list] = None,
    poll_s: float = 2.0,
) -> None:
    from odar.jobstore import JobStore

    from odar.assist import detect_language

    store.update(run_id, status="RUNNING", stage="planning research")
    store.add_event(run_id, "start", {"mode": "research"})
    translate_fn = translate_fn or _default_translate
    asked_in = detect_language(question)
    report_lang = asked_in if language == "auto" else language
    original_question = question
    if asked_in == "hi":  # search the (mostly English) web in English
        store.update(run_id, stage="translating the question")
        question, _ = translate_fn(question, "en")
    workdir = tempfile.mkdtemp(prefix=f"odar-web-{run_id}-")
    db = os.path.join(workdir, "jobs.db")
    model = "llm" if os.environ.get("ANTHROPIC_API_KEY") else "scripted"
    cmd = command or [
        sys.executable,
        os.path.join(ROOT, "run_research.py"),
        "run",
        question,
        *RESEARCH_ARGS.split(),
        "--timeout",
        str(int(timeout_s)),
        "--model",
        model,
        "--db",
        db,
        "--output-dir",
        workdir,
    ] + (["--academic"] if academic else [])
    proc = subprocess.Popen(
        cmd, cwd=ROOT, env=_research_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    started = time.monotonic()
    seen = 0
    while proc.poll() is None:
        if time.monotonic() - started > timeout_s + 90:
            proc.kill()
            break
        time.sleep(poll_s)
        seen = _mirror_job_events(store, run_id, db, seen, JobStore)
    output = proc.stdout.read() if proc.stdout else ""
    _mirror_job_events(store, run_id, db, seen, JobStore)
    payload = _read_payload(workdir)
    if payload is None:
        tail = output.strip().splitlines()[-3:]
        store.update(
            run_id, status="FAILED", stage="failed", error=" | ".join(tail)[:500] or "research run failed"
        )
        store.add_event(run_id, "end", {"status": "FAILED"})
        return
    if verify and payload.get("synthesis"):
        store.update(run_id, stage="checking the report's citations")
        if check_fn is None:
            from odar.check import check_text as check_fn
        try:
            report = check_fn(
                payload["synthesis"],
                use_llm=True,
                search=False,
                citation_style=style,
                on_event=_progress_sink(store, run_id, prefix="citation check: "),
            )
            payload["citation_check"] = report.to_dict()
        except Exception as exc:  # noqa: BLE001 - the report still stands without the check
            store.add_event(run_id, "check_error", {"error": str(exc)[:200]})
    if report_lang == "hi" and payload.get("synthesis"):
        store.update(run_id, stage="translating the report to Hindi")
        hindi, ok = translate_fn(payload["synthesis"], "hi")
        if ok:
            payload["synthesis_en"] = payload["synthesis"]
            payload["synthesis"] = hindi
        payload["language"] = "hi" if ok else "en"
    payload["question"] = original_question
    payload.pop("events", None)  # the job's raw event log is large; progress already mirrored it
    store.update(run_id, status="COMPLETE", stage="finished", result=payload)
    store.add_event(run_id, "end", {"status": payload.get("status")})


def _mirror_job_events(store: RunStore, run_id: str, db: str, seen: int, jobstore_cls: Any) -> int:
    if not os.path.exists(db):
        return seen
    try:
        jobs = jobstore_cls(db)
        try:
            listed = jobs.list_jobs(limit=1)
            if not listed:
                return seen
            events = jobs.events(listed[0]["job_id"], limit=1000)
        finally:
            jobs.close()
    except Exception:  # noqa: BLE001 - the child may hold a write lock mid-poll
        return seen
    for event in events[seen:]:
        kind = str(event.get("name") or event.get("kind") or "event")
        data = event.get("payload") or event.get("data") or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                data = {"detail": data[:200]}
        store.add_event(run_id, kind, data if isinstance(data, dict) else {"detail": str(data)[:200]})
        store.update(run_id, stage=_research_stage(kind))
    return len(events)


def _research_stage(kind: str) -> str:
    kind = kind.lower()
    for key, label in (
        ("run.start", "planning research"),
        ("plan", "planning sub-questions"),
        ("research_round", "searching and reading sources"),
        ("tool_session", "searching and reading sources"),
        ("decision", "deciding the next step"),
        ("reflection", "filling gaps"),
        ("writing", "writing the report"),
        ("search", "searching the web"),
        ("fetch", "reading sources"),
        ("verif", "verifying evidence"),
        ("reflect", "filling gaps"),
        ("write", "writing the report"),
        ("synth", "writing the report"),
        ("audit", "auditing citations"),
    ):
        if key in kind:
            return label
    return "researching"


def _read_payload(workdir: str) -> Optional[Dict[str, Any]]:
    for name in os.listdir(workdir):
        if name.endswith(".json"):
            try:
                with open(os.path.join(workdir, name), encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict) and "synthesis" in data:
                    return data
            except (OSError, ValueError):
                continue
    return None


def run_references(
    store: RunStore,
    run_id: str,
    text: str,
    *,
    style: str = "apa",
    references_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    **_: Any,
) -> None:
    """Validate a pasted reference list and build a corrected bibliography."""
    store.update(run_id, status="RUNNING", stage="validating references")
    store.add_event(run_id, "start", {"mode": "references", "style": style})
    if references_fn is None:
        from odar.scholar import check_references

        result = check_references(text, style=style, scholar=_scholar(store))
    else:
        result = references_fn(text, style=style)
    for ref in result.get("references", []):
        store.add_event(run_id, "reference", {"claim": ref["raw"][:120], "verdict": ref["status"]})
    store.update(run_id, status="COMPLETE", stage="finished", result=result)
    store.add_event(run_id, "end", {"counts": result.get("counts")})
