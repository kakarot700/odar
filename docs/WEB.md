# ODAR web app

```bash
pip install -e ".[web,pdf]"
odar serve --port 8000            # http://127.0.0.1:8000
```

The app is a texting-style assistant over a **live sky**, phone first (one narrow ~520 px column on
desktop). Its look follows the Hark Pro design language, with ODAR's own name and "O" mark:

* **Sky**: a full-screen canvas gradient that follows the local time of day (night, dawn, day, golden
  hour, dusk) with a few soft clouds drifting slowly (`js/sky.js`). It is drawn at quarter resolution
  (~20 fps), pauses while the tab is hidden, and is a still picture under `prefers-reduced-motion`.
  Text tone follows the sky's brightness (light text at dusk and night); Profile → Text colour can force
  Light or Dark. `?sky=dawn|day|golden|dusk|night` pins the sky for the session (`?sky=live` unpins), which
  is handy for screenshots and demos.
* **Frosted glass** everywhere: bubbles, top-bar controls, composer, cards, sheets and menus use
  translucent fills, `backdrop-filter` blur, a hairline light border and a soft shadow. The chat fades
  out under the top bar (the view is a masked fixed scroller). Font: Inter (variable, Latin subset,
  SIL OFL, bundled in `static/fonts/`), system stack fallback.
* **Top bar**: round glass Home button on the left; a centred segmented pill **Chat | Projects** (the active
  segment is a solid white pill); a glass **Search** pill (an icon on phones) and your round avatar on the
  right.
* **Chat** is one continuous thread (no "new chat" list), with no avatars, names or timestamps. ODAR's
  answers arrive as **short bubbles** (one per paragraph; a heading rides with its text; tables get their
  own wide bubble); your messages are pills on the right. While ODAR works, a **live work card** (a dark
  rounded card) shows the sources being read as tiles (favicons and sites) and the step list, with
  floating round glass buttons (expand for source titles and steps; stop), and under it a translucent
  **status pill** whose one short phrase cross-fades as the work moves ("Searching PubMed, arXiv and
  Crossref", "Reading 6 sources", "Writing the answer", "Checking quotes"). When done it folds into a
  small pill ("Read 6 sources · checked 9 citations · 41s") that opens the steps.
* **Composer**: a glass pill, "Ask ODAR", with **+** and a **mic** on the right; send appears once there
  is text, and turns into stop while an answer streams. The **+** menu holds the mode (Ask / Deep research
  / Check / References), the focus (All web, Academic, News, Discussions, YouTube; in projects: files /
  web / both), attach a file, and add to a project. Anything not default shows as a chip above the
  composer (tap it to go back to Ask or All web; research options sit there too). The mic uses the Web
  Speech API (Hindi when the app is in Hindi) and is hidden where the browser has none.
* Each answer carries: quiet inline `[n]` citation chips coloured by verdict (tap one for a sheet with
  the verified quote, URL and supported / partly supported / not supported / not checked badge); a
  **trust** badge and a "N supported · M partial" line that opens every check; **link cards**, up to
  three fanned, slightly rotated cards (image when the page's site has one in the image results,
  otherwise a favicon tile, then title and site; tap opens the page) plus "All N sources" for the full
  list; a fanned **photo** stack; and **follow-up chips** (full report, simpler, academic, latest news,
  Hindi).
* **Deep research, Check and References** runs started from the composer appear in the thread as the same
  live work card (pages being read as tiles, steps, status phrase from the run's newest event; expand
  opens the full run view, the other button minimises the card; runs cannot be stopped from the UI), then
  a done pill, a glass **summary card** (report collapsed with "Read the full report", trust badge,
  citation-check details) and a white **receipt card** (sources read, claims checked, trust score,
  evidence) with a black **Download PDF** pill and Markdown, Word, Share link and "Ask about it".
* **Home** (house button): **Action Cards**, frosted pills whose verb is an embedded white button, built
  from your real data: "Read your report on …", "Follow your deep research on …" (while one runs),
  "Verify citations in …" (opens the checks of your latest answer), "Continue research on …", "Try
  today's Discover: …", "Download your latest report as PDF", "Check an AI answer for fake citations".
  They sit in three rows that scroll sideways. Then glass **Panels**: Trust (latest answer's supported /
  partly / not supported tiles and a sparkline of trust scores across answers), Usage (questions and deep
  runs left with thin bars), Discover (topic tabs, the top story as the big line, a list with
  thumbnails), Activity (questions and runs per day, last 7 days), Reports (thumbnails list, download the
  latest PDF) and Projects. Each panel has a small icon and a grey "Title · Subtitle" header, one big
  insight sentence, a pale full-width button at the bottom, and **Reply**, which drops a prompt about it
  into the composer. On screens 1180 px and wider, Home opens as a **left column next to the chat** (the
  house button toggles it); on phones it is its own page with the composer (what you type there is asked
  in Chat). Pull down to refresh.
* **Projects** tab: create, rename, delete; each project opens its own thread (the latest one continues;
  "New thread" starts fresh) with a **Files** sheet for uploads and custom instructions.
* **Search** (or Ctrl/Cmd-K): searches thread titles, the messages of your 25 most recent threads and
  report titles in the browser.
* **Profile** sheet, in order: plan and usage, profile name (stored in this browser only), API keys,
  language (English / हिंदी: app labels, and deep reports default to Hindi), text colour (Auto follows the
  sky / Light / Dark), privacy and run history, about, sign out (a placeholder: there are no accounts yet;
  data is tied to this browser).
* Respects `prefers-reduced-motion` and safe-area insets. Plain HTML/CSS and ES modules in
  `odar/web/static/` (`app.js` plus `js/*.js`: `core`, `sky`, `chat`, `work`, `runs`, `home`, `projects`,
  `search`, `profile`, `md`), no build step and no CDN.

Older links keep working: `/c/<thread>`, `/p/<project>`, `/runs/<id>`, `/r/<token>` (shared report),
`/s/<token>` (shared thread), `/history`, `/settings` (opens the profile sheet), `/new?mode=research`.

### Ask

* **Focus** (one per question): All web · Academic (Crossref, PubMed, arXiv, OpenAlex, Semantic Scholar
  abstracts) · News (ddgs news, dates shown) · Discussions (`site:reddit.com` plus forums, labelled
  "forum") · YouTube (video titles and descriptions only; pages are not fetched).
* Pipeline: ddgs search (~6 results, backend fallback yahoo → auto → bing → duckduckgo) → SSRF-guarded
  fetch of the top pages (`PageExtractor`, 15 s phase cap) → one streamed answer on the free routes
  (only `:free` models are ever used here) → verification of each cited sentence (≤14 checks, 50 s cap).
* **Threads**: Research / Check / References cards in a thread are not used as chat context. Follow-ups keep context (last 3 Q&A turns, trimmed to 3,000 chars, plus their source
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
| `POST /api/runs` (form: mode, text/url/file, style, language, academic, verify, thread_id) | start a run; `thread_id` (an owned thread, or `new`) also shows it in that chat thread as a request bubble plus a run card, and the response carries `thread_id` |
| `GET /api/runs/{id}?after=N` | status, stage, new progress events, result |
| `POST /api/runs/{id}/ask` `{"question": ...}` | follow-up grounded in the report |
| `GET /api/runs/{id}/export.{md,docx,pdf}` | download |
| `GET /api/share/{token}` | public read-only report |
| `GET /api/limits` | remaining quota |
| `POST /api/ask/stream` `{"question", "focus", "thread_id", "project_id", "sources", "images", "verify"}` | SSE: `thread`, `sources`, `images`, `delta`…, `done`, `verification` (or `error`) |
| `GET /api/ask/stream?q=...&focus=...` | same, for `EventSource` |
| `POST /api/ask` | same pipeline, one JSON object |
| `GET /api/threads?project_id=` · `GET/PATCH/DELETE /api/threads/{id}` | threads (owner only); `GET` returns the latest `limit` messages (default 200, max 500), oldest first |
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
