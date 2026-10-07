# ODAR web app

```bash
pip install -e ".[web,pdf]"
odar serve --port 8000            # http://127.0.0.1:8000
```

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

OpenAPI docs: `/api/docs`.

## Limits and privacy (env vars)

`ODAR_IP_RUNS_PER_HOUR` 10 · `ODAR_IP_RUNS_PER_DAY` 30 · `ODAR_KEY_DAILY_LIMIT` 50 ·
`ODAR_FOLLOWUPS_PER_HOUR` 30 · `ODAR_RETENTION_DAYS` 30 · `ODAR_TRUST_PROXY=1` behind a proxy ·
max 2 active runs per user. Uploads are never stored; checked text only when "Keep" is ticked;
share links send `X-Robots-Tag: noindex`; `DELETE /api/me` removes all runs and keys.
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

## Chrome extension

See `extension/README.md`: right-click selected text → Check citations with ODAR.
