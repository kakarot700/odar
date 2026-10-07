# ODAR web app

```bash
pip install -e ".[web,pdf]"
odar serve --port 8000            # http://127.0.0.1:8000
```

The landing view is **Ask**, a chat: type a question, get a short answer with inline `[n]` citations,
then a Sources card, an Images strip, and a **Verified** card where every cited sentence has been checked
against its source (local NLI plus a verbatim-quote LLM judge) and labelled supported / partial /
unsupported. Hover or tap a `[n]` marker to see the source title, domain, verified quote and verdict.
The house button opens **Home**: Discover headlines, Projects, recent threads, and the Research / Check /
References tools (also in the composer's mode menu). Mobile-first, one column on phones, thread sidebar on
desktop, dark mode follows the system setting.

### Ask

* **Focus** (one per question): All web · Academic (Crossref, PubMed, arXiv, OpenAlex, Semantic Scholar
  abstracts) · News (ddgs news, dates shown) · Discussions (`site:reddit.com` plus forums, labelled
  "forum") · YouTube (video titles and descriptions only; pages are not fetched).
* Pipeline: ddgs search (~6 results, backend fallback yahoo → auto → bing → duckduckgo) → SSRF-guarded
  fetch of the top pages (`PageExtractor`, 15 s phase cap) → one streamed answer on the free routes
  (only `:free` models are ever used here) → verification of each cited sentence (≤14 checks, 50 s cap).
* **Threads**: follow-ups keep context (last 3 Q&A turns, trimmed to 3,000 chars, plus their source
  titles; short follow-ups borrow the previous question's terms for search). Rename, delete, share
  (`/s/<token>`, read-only, `noindex`).
* **Projects**: name + custom instructions + files (PDF/DOCX/TXT/MD, 10 MB each, max 20 per project, via
  `inputs.py`). Files are chunked (~900 chars) and searched locally with BM25 (no embeddings, no network).
  Questions inside a project use **My files**, **Web**, or **Both**; file citations show the file name and
  passage. Unlike Check uploads, project file *text* is stored (in SQLite) until you delete the file or
  project.
* **Images**: ddgs images, shown as a strip with source links. The server never fetches image bytes; only
  https thumbnails that pass the SSRF URL policy are returned, and the browser loads them directly with
  `referrerpolicy="no-referrer"`.
* **Discover**: World, Tech & AI, Science, India, Business headlines from ddgs news, cached in SQLite
  for 3 hours (`ODAR_DISCOVER_TTL_S`). No LLM summaries are made up front; tapping a story starts an
  Ask thread (News focus) about it.

Modes: **Research** (deep cited report, optional per-claim citation check), **Check** (verify an AI
answer's citations, with quotes, source-quality labels, freshness and confidence), **References**
(validate a bibliography against Crossref / PubMed / arXiv / OpenAlex, flag fake or mismatched
references, suggest real papers, format APA / MLA / Chicago / IEEE).

Inputs: paste, PDF/DOCX/TXT/MD upload (10 MB), article URL, Google Doc shared as "anyone with the link".
Outputs: share link, Markdown / DOCX / PDF export, follow-up questions answered only from the report.
Hindi: Hindi input is checked via an English translation; reports can be written in Hindi.

Not offered on purpose: plagiarism detection, writing essays or assignments.

## API

Create a key in the app (**API & privacy**) and send `Authorization: Bearer odar_...`.

| Endpoint | Purpose |
|---|---|
| `POST /api/runs` (form: mode, text/url/file, style, language, academic, verify) | start a run |
| `GET /api/runs/{id}?after=N` | status, stage, new progress events, result |
| `POST /api/runs/{id}/ask` `{"question": ...}` | follow-up grounded in the report |
| `GET /api/runs/{id}/export.{md,docx,pdf}` | download |
| `GET /api/share/{token}` | public read-only report |
| `GET /api/limits` | remaining quota |
| `POST /api/ask/stream` `{"question", "focus", "thread_id", "project_id", "sources", "images", "verify"}` | SSE: `thread`, `sources`, `images`, `delta`…, `done`, `verification` (or `error`) |
| `GET /api/ask/stream?q=...&focus=...` | same, for `EventSource` |
| `POST /api/ask` | same pipeline, one JSON object |
| `GET /api/threads?project_id=` · `GET/PATCH/DELETE /api/threads/{id}` | threads (owner only) |
| `GET /api/shared-threads/{token}` | public read-only thread |
| `POST/GET /api/projects` · `GET/PATCH/DELETE /api/projects/{id}` | projects |
| `POST /api/projects/{id}/files` (multipart `file`) · `DELETE .../files/{file_id}` | project files |
| `GET /api/images?q=` | image results (thumbnail, image, source page) |
| `GET /api/discover?topic=all\|world\|tech\|science\|india\|business` | cached headlines |

`focus`: `all`, `academic`, `news`, `reddit`, `youtube`. `sources` (projects only): `web`, `files`, `both`.
The `verification` event lists one entry per marker occurrence in reading order:
`{occ, n, claim, verdict: supported|partial|unsupported|unchecked, check_verdict, quote, note, title, domain, url}`.

OpenAPI docs: `/api/docs`.

## Limits and privacy (env vars)

`ODAR_IP_RUNS_PER_HOUR` 10 · `ODAR_IP_RUNS_PER_DAY` 30 · `ODAR_KEY_DAILY_LIMIT` 50 ·
`ODAR_FOLLOWUPS_PER_HOUR` 30 · `ODAR_ASK_PER_HOUR` 60 (Ask has its own, lighter budget per network or key;
images use twice that) · `ODAR_RETENTION_DAYS` 30 · `ODAR_TRUST_PROXY=1` behind a proxy ·
max 2 active runs per user. Uploads are never stored; checked text only when "Keep" is ticked;
share links send `X-Robots-Tag: noindex`; threads older than the retention period are deleted;
`DELETE /api/me` removes all runs, threads, projects (with their files) and keys.
Scholarly API answers are cached for 7 days in SQLite. `ODAR_OPENALEX_KEY` (free) and
`ODAR_CONTACT_EMAIL` improve OpenAlex/Crossref access.

## Models

Free `:free` routes with automatic fallback (`odar/router.py`): deepseek-v4-flash, mimo-v2.5,
mimo-v2.6-flash. Override per role with `ODAR_MODEL_ROUTES` JSON. Without a model key, Check still
runs fully local (NLI only).

## Free hosting

Vercel can't run the backend: the local NLI model needs PyTorch (too large and slow for
serverless). Use the Dockerfile on **Hugging Face Spaces** (free CPU, 16 GB RAM) with
`CMD odar serve --host 0.0.0.0 --port 7860`. Render's free tier (512 MB) is too small for NLI.
A live check takes about 2 to 7 minutes on free models; references take under a minute.
An Ask answer takes about 10 to 25 s on free models (sandbox smoke test 2026-10-07: search 0.7 s, answer
done at 10.3 s, verification of 10 cited sentences 44 s more). Token Harbor delivered the answer as one
burst, so "time to first token" is close to the full answer time; the UI shows a typing indicator.

## Chrome extension

See `extension/README.md`: right-click selected text → Check citations with ODAR.
