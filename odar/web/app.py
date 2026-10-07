"""ODAR web app: Research (cited report) and Check (verify an AI answer).

Run locally::

    pip install -e .[web,pdf]
    odar serve --port 8000          # or: uvicorn odar.web.app:app

Each browser gets an anonymous id cookie; its history lists only its own runs.
A run's share link (``/r/<token>``) is a read-only view anyone can open.

Programmatic use: create a key in the app (or ``POST /api/keys``) and send
``Authorization: Bearer odar_...``. Limits (env-configurable):

* ``ODAR_IP_RUNS_PER_HOUR`` (10) / ``ODAR_IP_RUNS_PER_DAY`` (30) for browsers
* per-key daily limit (``ODAR_KEY_DAILY_LIMIT``, 50)
* ``ODAR_FOLLOWUPS_PER_HOUR`` (30) follow-up questions
* ``ODAR_RETENTION_DAYS`` (30): runs older than this are deleted automatically
"""

from __future__ import annotations

import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles

from odar.web import exporters, inputs
from odar.web.store import ACTIVE, RunStore

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
COOKIE = "odar_uid"
MAX_ACTIVE_PER_USER = 2
EXPORT_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}

Runner = Callable[..., None]
MODES = ("research", "check", "references")
LANG_CHOICES = ("auto", "en", "hi")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class AskBody(BaseModel):
    question: str
    language: str = "auto"


class KeyBody(BaseModel):
    label: str = ""


def _public(run: Dict[str, Any], *, owner_view: bool) -> Dict[str, Any]:
    data = {
        k: run.get(k)
        for k in ("mode", "title", "status", "stage", "error", "result", "created", "updated", "share_token")
    }
    data["input_text"] = run.get("input_text", "")
    data["input_meta"] = run.get("input_meta", {})
    if owner_view:
        data["run_id"] = run["run_id"]
    return data


def create_app(
    store: Optional[RunStore] = None,
    *,
    check_runner: Optional[Runner] = None,
    research_runner: Optional[Runner] = None,
    references_runner: Optional[Runner] = None,
    url_reader: Optional[Callable[[str], Any]] = None,
    followup_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    max_workers: int = 2,
    synchronous: bool = False,
) -> FastAPI:
    """Build the app. Runners and the URL reader are injectable for tests;
    ``synchronous=True`` runs jobs inline (tests) instead of on the worker pool."""
    from odar.web import runners

    store = store or RunStore(os.environ.get("ODAR_WEB_DB", "odar_web.db"))
    store.requeue_stale()
    retention_days = float(os.environ.get("ODAR_RETENTION_DAYS", "30") or 0)
    store.purge_older_than(retention_days)
    ip_hourly = _env_int("ODAR_IP_RUNS_PER_HOUR", 10)
    ip_daily = _env_int("ODAR_IP_RUNS_PER_DAY", 30)
    key_daily = _env_int("ODAR_KEY_DAILY_LIMIT", 50)
    followups_hourly = _env_int("ODAR_FOLLOWUPS_PER_HOUR", 30)
    trust_proxy = os.environ.get("ODAR_TRUST_PROXY", "") == "1"
    check_runner = check_runner or runners.run_check
    research_runner = research_runner or runners.run_research
    references_runner = references_runner or runners.run_references
    url_reader = url_reader or inputs.text_from_url
    pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="odar-run")
    lock = threading.Lock()

    app = FastAPI(title="ODAR", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.store = store
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    # The Chrome extension calls the API with a key from its own origin (no cookies).
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"chrome-extension://[a-p]{32}",
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )

    def owner_of(request: Request) -> str:
        return getattr(request.state, "uid", "") or request.cookies.get(COOKIE, "")

    def client_ip(request: Request) -> str:
        if trust_proxy and request.headers.get("x-forwarded-for"):
            return request.headers["x-forwarded-for"].split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    @app.middleware("http")
    async def identity(request: Request, call_next: Any) -> Response:
        request.state.key = None
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer odar_"):
            key = store.key_owner(auth.split(None, 1)[1].strip())
            if key is None:
                return JSONResponse({"detail": "invalid or revoked API key"}, status_code=401)
            request.state.key = key
            request.state.uid = key["owner"]
            return await call_next(request)
        existing = request.cookies.get(COOKIE, "")
        request.state.uid = existing if 16 <= len(existing) <= 64 else secrets.token_urlsafe(16)
        response = await call_next(request)
        if request.url.path.startswith(("/r/", "/api/share/")):
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        if request.state.uid != existing and not getattr(request.state, "forget", False):
            response.set_cookie(COOKIE, request.state.uid, max_age=31536000, httponly=True, samesite="lax")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    def _execute(run_id: str, mode: str, text: str, options: Dict[str, Any]) -> None:
        try:
            if mode == "check":
                check_runner(store, run_id, text, **options)
            elif mode == "references":
                references_runner(store, run_id, text, **options)
            else:
                research_runner(store, run_id, text, **options)
        except Exception as exc:  # noqa: BLE001 - a failed run must be visible, not silent
            store.update(
                run_id, status="FAILED", stage="failed", error=f"{type(exc).__name__}: {str(exc)[:300]}"
            )
            store.add_event(run_id, "end", {"status": "FAILED"})

    # ------------------------------------------------------------------ #
    @app.get("/", include_in_schema=False)
    @app.get("/history", include_in_schema=False)
    @app.get("/settings", include_in_schema=False)
    @app.get("/runs/{run_id}", include_in_schema=False)
    @app.get("/r/{token}", include_in_schema=False)
    def index(run_id: str = "", token: str = "") -> FileResponse:
        return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        return {"ok": True}

    @app.post("/api/runs")
    async def create_run(
        request: Request,
        mode: str = Form(...),
        text: str = Form(""),
        url: str = Form(""),
        file: Optional[UploadFile] = File(None),
        verify: bool = Form(True),
        use_llm: bool = Form(True),
        keep_input: bool = Form(False),
        academic: bool = Form(True),
        style: str = Form("apa"),
        language: str = Form("auto"),
    ) -> JSONResponse:
        if language not in LANG_CHOICES:
            raise HTTPException(400, "language must be auto, en or hi")
        if mode not in MODES:
            raise HTTPException(400, "mode must be 'research', 'check' or 'references'")
        style = style.lower()
        if style not in ("apa", "mla", "chicago", "ieee"):
            raise HTTPException(400, "style must be apa, mla, chicago or ieee")
        owner = owner_of(request)
        with lock:
            active = [r for r in store.history(owner, limit=20) if r["status"] in ACTIVE]
            if len(active) >= MAX_ACTIVE_PER_USER:
                raise HTTPException(429, "you already have runs in progress; wait for one to finish")
            key = request.state.key
            if key is not None:
                if not store.hit(f"key:{key['prefix']}", 86400, int(key["daily_limit"])):
                    raise HTTPException(429, "this API key reached its daily run limit")
            else:
                ip = client_ip(request)
                if not store.hit(f"ip:{ip}", 3600, ip_hourly):
                    raise HTTPException(429, "too many runs from your network this hour; try again later")
                if not store.hit(f"ipd:{ip}", 86400, ip_daily):
                    raise HTTPException(429, "daily run limit reached for your network")
        store.purge_older_than(retention_days)
        source = "text"
        title = ""
        try:
            if file is not None and file.filename:
                data = await file.read(inputs.MAX_UPLOAD_BYTES + 1)
                body, source = inputs.text_from_upload(file.filename, data)
                title = file.filename
            elif url.strip():
                body, title = url_reader(url.strip())
                source = "url"
            else:
                body = inputs._cap(text)
        except inputs.InputError as exc:
            raise HTTPException(400, str(exc)) from exc
        if mode == "research":
            body = " ".join(body.split())[:600]
            if len(body) < 8:
                raise HTTPException(400, "ask a fuller research question")
            title = title or body[:120]
            options: Dict[str, Any] = {
                "verify": verify,
                "academic": academic,
                "style": style,
                "language": language,
            }
        elif mode == "references":
            title = title or f"References ({style.upper()})"
            options = {"style": style}
        else:
            title = title or (body.split("\n", 1)[0][:100] or "Pasted answer")
            options = {"use_llm": use_llm, "academic": academic, "style": style, "language": language}
        meta = {"source": source, "chars": len(body)}
        if source == "url":
            meta["url"] = url.strip()[:500]
        run = store.create(mode, title, body, meta, owner=owner, keep_input=keep_input or mode == "research")
        if synchronous:
            _execute(run["run_id"], mode, body, options)
        else:
            pool.submit(_execute, run["run_id"], mode, body, options)
        return JSONResponse({"run_id": run["run_id"], "share_token": run["share_token"]}, status_code=201)

    @app.get("/api/runs")
    def history(request: Request, limit: int = 50) -> Dict[str, Any]:
        owner = owner_of(request)
        return {"runs": store.history(owner, limit=min(limit, 200)) if owner else []}

    def _owned(request: Request, run_id: str) -> Dict[str, Any]:
        run = store.get(run_id)
        if run is None or (run["owner"] and run["owner"] != owner_of(request)):
            raise HTTPException(404, "run not found")
        return run

    def _shared(token: str) -> Dict[str, Any]:
        run = store.by_token(token)
        if run is None:
            raise HTTPException(404, "report not found")
        return run

    @app.get("/api/runs/{run_id}")
    def get_run(request: Request, run_id: str, after: int = 0) -> Dict[str, Any]:
        run = _owned(request, run_id)
        return {"run": _public(run, owner_view=True), "events": store.events(run_id, after=after)}

    @app.delete("/api/runs/{run_id}")
    def delete_run(request: Request, run_id: str) -> Dict[str, Any]:
        _owned(request, run_id)
        return {"deleted": store.delete(run_id)}

    @app.post("/api/runs/{run_id}/ask")
    def ask(request: Request, run_id: str, body: AskBody) -> Dict[str, Any]:
        """Follow-up question answered only from this report (abstains otherwise)."""
        run = _owned(request, run_id)
        if run["status"] != "COMPLETE" or not run.get("result"):
            raise HTTPException(409, "the report is not finished yet")
        question = body.question.strip()
        if not 3 <= len(question) <= 500:
            raise HTTPException(400, "ask a question of 3 to 500 characters")
        if body.language not in LANG_CHOICES:
            raise HTTPException(400, "language must be auto, en or hi")
        if not store.hit(f"ask:{owner_of(request)}", 3600, followups_hourly):
            raise HTTPException(429, "too many follow-up questions this hour")
        from odar.assist import answer_followup, detect_language, make_complete

        lang = detect_language(question) if body.language == "auto" else body.language
        lang = lang if lang in ("en", "hi") else "en"
        if followup_fn is not None:
            answer = followup_fn(run["mode"], run["result"], question, lang)
        else:
            answer = answer_followup(run["mode"], run["result"], question, make_complete("writer"), lang)
        store.add_event(run_id, "followup", {"question": question, **answer})
        return answer

    @app.get("/api/runs/{run_id}/followups")
    def followups(request: Request, run_id: str) -> Dict[str, Any]:
        _owned(request, run_id)
        items = [e["data"] for e in store.events(run_id, limit=2000) if e["kind"] == "followup"]
        return {"followups": items}

    # ------------------------------------------------------------------ #
    # API keys + privacy
    # ------------------------------------------------------------------ #
    def _browser_only(request: Request) -> str:
        if request.state.key is not None:
            raise HTTPException(403, "manage keys from the web app, not with a key")
        return owner_of(request)

    @app.post("/api/keys")
    def create_key(request: Request, body: KeyBody) -> Dict[str, Any]:
        owner = _browser_only(request)
        if len(store.list_keys(owner)) >= 5:
            raise HTTPException(400, "at most 5 active keys; revoke one first")
        key = store.create_key(owner, body.label, key_daily)
        return {
            "key": key,
            "prefix": key[:12],
            "daily_limit": key_daily,
            "note": "shown once; store it safely",
        }

    @app.get("/api/keys")
    def list_keys(request: Request) -> Dict[str, Any]:
        return {"keys": store.list_keys(_browser_only(request))}

    @app.delete("/api/keys/{prefix}")
    def revoke_key(request: Request, prefix: str) -> Dict[str, Any]:
        return {"revoked": store.revoke_key(_browser_only(request), prefix)}

    @app.delete("/api/me")
    def forget_me(request: Request, response: Response) -> Dict[str, Any]:
        owner = _browser_only(request)
        deleted = store.delete_owner(owner)
        request.state.forget = True
        response.delete_cookie(COOKIE)
        return {"deleted_runs": deleted}

    @app.get("/api/limits")
    def limits(request: Request) -> Dict[str, Any]:
        key = request.state.key
        if key is not None:
            return {
                "daily_limit": key["daily_limit"],
                "used_today": store.used(f"key:{key['prefix']}", 86400),
            }
        ip = client_ip(request)
        return {
            "hourly_limit": ip_hourly,
            "used_this_hour": store.used(f"ip:{ip}", 3600),
            "daily_limit": ip_daily,
            "used_today": store.used(f"ipd:{ip}", 86400),
            "retention_days": retention_days,
        }

    @app.get("/api/share/{token}")
    def get_shared(token: str, after: int = 0) -> Dict[str, Any]:
        run = _shared(token)
        return {"run": _public(run, owner_view=False), "events": store.events(run["run_id"], after=after)}

    def _export(run: Dict[str, Any], fmt: str) -> Response:
        if fmt not in EXPORT_TYPES:
            raise HTTPException(400, "format must be md, docx or pdf")
        if run["status"] != "COMPLETE" or not run.get("result"):
            raise HTTPException(409, "the report is not finished yet")
        markdown = exporters.run_markdown(run)
        if fmt == "md":
            body: bytes = markdown.encode("utf-8")
        elif fmt == "docx":
            body = exporters.to_docx(markdown)
        else:
            body = exporters.to_pdf(markdown)
        name = f"odar-{run['mode']}-{run['share_token'][:8]}.{fmt}"
        return Response(
            body,
            media_type=EXPORT_TYPES[fmt],
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/api/runs/{run_id}/export.{fmt}")
    def export_run(request: Request, run_id: str, fmt: str) -> Response:
        return _export(_owned(request, run_id), fmt)

    @app.get("/api/share/{token}/export.{fmt}")
    def export_shared(token: str, fmt: str) -> Response:
        return _export(_shared(token), fmt)

    return app


def __getattr__(name: str) -> Any:  # lazy ``uvicorn odar.web.app:app``
    if name == "app":
        return create_app()
    raise AttributeError(name)
