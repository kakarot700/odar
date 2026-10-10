# ODAR benchmark log

Full results of every benchmark round, oldest first. See [README.md](README.md) for the summary and how to rerun.

## Round 1: ODAR vs GPT Researcher — side-by-side benchmark (2026-10-07)

Spend: **$0**. Both tools ran on the same free Token Harbor model `mimo-v2.5:free` (ODAR via Anthropic-compatible base `https://tokenharbor.ai`, GPT Researcher via OpenAI-compatible base `https://tokenharbor.ai/v1`) and the same free search backend (DuckDuckGo via the `ddgs`/DDGS library). GPT Researcher v0.16.1 (pip), default `research_report`, FAST/SMART/STRATEGIC LLM all set to the free model, `RETRIEVER=duckduckgo`, `CONTEXT_FILTER=keyword` (no embeddings call). ODAR = branch `chore/model-sonnet-5-5`, `--model llm --max-iterations 4 --max-search 8 --max-fetch 8 --max-model-calls 30 --timeout 540`.

Questions:
- **Q1 factual / current events:** main outcomes and agreements of COP30 (Belém, Nov 2025)
- **Q2 technical:** how PostgreSQL MVCC gives transaction isolation and why it causes bloat that VACUUM cleans
- **Q3 contested:** does raising the minimum wage reduce employment, and where do economists disagree

## Results

| Run | Completed? | Wall time | Model calls | Sources fetched (ok / tried) | Cited claims | Citation spot-check | Dead / hallucinated links in report | Answers the question? |
|---|---|---|---|---|---|---|---|---|
| ODAR Q1 | Yes, but abstained (0 findings) | 280 s | 18 | 4 / 8 | 0 | n/a (nothing cited) | 0 / 0 | No: "No certified findings" |
| GPTR Q1 | Yes | 204 s | 3 | 9 / 9 | 51 in-text, 5 refs | 7/8 supported, 1 partial | 0 / 0 | Yes: thorough, balanced, correct |
| ODAR Q2 | Try 1 FAILED (Token Harbor 400 mid-run, 142 s); try 2 yes | 218 s | 18 | 6 / 8 | 4 | 4/4 supported (verbatim PG docs) | 0 / 0 | Partly: isolation only, nothing on bloat or VACUUM |
| GPTR Q2 | Yes | 244 s | 4 | 11 / 11 | 57 in-text, 8 refs | 8/8 supported by cited page | 0 / 0 | Yes, but leans on low-quality blogs; one actionable error |
| ODAR Q3 | Yes | 208 s | 14 | 1 / 8 | 2 | 2/2 supported | 0 / 0 | No: both "findings" are trivial (one is a bibliography string) |
| GPTR Q3 | Yes | 274 s | 5 | 15 / 15 | 54 in-text, 7 refs | 9/9 supported | 0 / 0 | Yes, but one-sided on a contested question |

Totals: ODAR completed 3/3 questions (4 runs, 1 failure retried), but produced substantive answers for 0/3 and a partial for 1. GPT Researcher completed 3/3 on first try and answered all 3. Neither cited a dead link or invented a source; all 20 GPTR reference URLs returned HTTP 200. Full spot-check notes, scripts and all six reports: this `bench/` folder.

## What happened, per run

- **ODAR Q1:** extracted 6 claims as raw Carbon Brief sentences; the NLI verifier (`cross-encoder/nli-deberta-v3-small`) scored both "supports" and "refutes" high for plainly factual sentences (e.g. 0.98 vs 0.86, 0.99 vs 1.00), so every claim became CONFLICTING and the run abstained. 4 fetches failed (un.org, enb.iisd.org, iisd.org, a UNFCCC PDF). Audit PASS_WITH_FLAGS (3 unexamined contradictions).
- **ODAR Q2:** first try died on a `400 invalid_request_error` from the free model route mid-run (backend failure, no fallback). Retry certified 4 verbatim sentences from PG docs 13.2 (all correct), flagged "SQL standard defines four isolation levels" as CONFLICTING (it isn't), never reached bloat/VACUUM, and labelled the official docs "no primary-research source". Also tried to fetch a guessed URL that 404s (`docs/current/routine-vacuum.html`) — not cited, but the model invents URLs.
- **ODAR Q3:** 7 of 8 fetches blocked (CBO, IGM/Kent Clark Center ×3, Dube PDF, Wikipedia — 403 from this sandbox). Only the NBER abstract page loaded, so the "findings" are its first sentence and its citation string. Audit PASS.
- **GPTR Q1:** best report of the six. Correct dates, figures, and a careful note on the conflicting fossil-fuel-roadmap reports. Only flaw: a WRI quote attributed to an article where it's actually the title of a linked WRI statement.
- **GPTR Q2:** every cited sentence is on its page, but 3 of 8 refs are one blog's auto-generated "deep dive" series, and it repeats dubious advice (e.g. `parallel_leader_participation` as a vacuum control; `VACUUM FULL` shown as routine "aggressive" vacuuming without warning it takes an exclusive lock).
- **GPTR Q3:** accurate quotes and numbers (Neumark & Shirley 78.9%/53.9%/46.1% exact), but concludes firmly "raising the minimum wage does reduce employment". It fetched three Economic Policy Institute pages arguing little/no job loss and cited none; omits the Belman & Wolfson meta-analysis's own near-zero conclusion while citing it.

## Where ODAR wins
- **Never asserts what it can't verify.** Every ODAR certified claim checked out verbatim; no overreach, no one-sided verdicts, contradictions are surfaced explicitly. GPTR's errors (blog-sourced advice, a firm side on Q3) are exactly what ODAR's design refuses to produce.
- **Machine-checkable audit trail:** per-claim entailment scores, contradiction records, 9-check final audit, budgets and counters. GPTR gives prose plus a reference list.
- **Hard budgets and deadlines** (model calls, fetches, wall clock) were respected on every run.

## Where ODAR loses
- **Usefulness:** 0 of 3 substantive answers vs 3 of 3. On these questions a user would pay for GPTR's output, not ODAR's.
- **Verifier is the bottleneck:** the small NLI model produces spurious "refutes", turning clear facts into CONFLICTING and forcing abstention. Biggest single fix.
- **Claim extraction:** claims are raw source sentences (including bibliography strings and "Table 13.1 describes…"), not answers to the question. No synthesis step that composes certified facts into a readable report.
- **Fetching:** ODAR's model guesses URLs (404s) and goes for sites that block bots (CBO, IGM, Wikipedia, IISD); GPTR scrapes the search results it actually got and got 35/35. Report shows source IDs/titles but **no URLs**, so a reader can't click through.
- **Robustness:** one backend 400 killed a run outright (default `--llm-fallback fail`); GPTR had no failures.
- **Efficiency:** 14–18 model calls per run vs 3–5 for GPTR, at similar wall time (≈210–280 s each).

## Fairness caveats
- One free model, not what either tool is tuned for (GPTR defaults to gpt-5.4/mini; ODAR to Claude). Rankings could change on stronger models, and the NLI verifier is independent of the LLM.
- Search parity: both used DDGS, but ODAR's DDG-HTML and Wikipedia fallback tiers were switched off by a benchmark harness (`bench/odar_ddgs_only.py`) because this sandbox's proxy blocks them (502/403) and they added ~88 s per search. An unpatched ODAR run of Q1 hit its 540 s deadline with 0 evidence (CANCELLED) — recorded in `bench/attempts/`. In production ODAR would run with its fallbacks working.
- The sandbox blocks some sites (Wikipedia, CBO, IGM) for both tools; it hurt ODAR more because its model chose those URLs.
- Pairs ran concurrently against the same free endpoint and DuckDuckGo, so rate limits affected both; single run per question, small sample (n=3).
- GPTR model calls were counted by intercepting HTTP requests to tokenharbor.ai; ODAR's from its own governance counters. GPTR's self-reported "cost" ($0.15/$…) is a list-price estimate, not an actual charge; Token Harbor charged nothing.
- Citation accuracy = manual spot-check (fetch source, find the claim), ~5–9 per report; GPTR's "cited claims" = in-text citation count, ODAR's = certified claims.

## Suggested ODAR fixes (from this run)
1. Replace or recalibrate the NLI verifier (larger NLI model, or LLM-as-judge with the NLI as a tie-breaker); treat "support high AND refute high" as noise, not conflict.
2. Add a synthesis step: certified facts → readable answer with inline citations, plus show source URLs in the report.
3. Only fetch URLs that came from search results (or validate guessed URLs with a HEAD first); prefer the next result when a domain 403s.
4. Make one transient backend error retryable instead of failing the run.
5. Rerun this benchmark after 1–2, and once on claude-sonnet-5-5 when a key is available.

---

## Round 2: rerun after fixes (2026-10-07, round 2)

Same 3 questions, same free model `mimo-v2.5:free`, same harness, $0 charged. ODAR = branch `feat/verifier-synthesis-fetch-retry` (on top of the model-swap commit). GPT Researcher v0.16.1 rerun with identical config. Reports: `bench/v2/`.

| Run | Before (round 1) | After (round 2) | Wall time | Model calls | Certified / conflicting | Answers the question? |
|---|---|---|---|---|---|---|
| ODAR Q1 COP30 | abstained, 0 findings | **COMPLETE, audit PASS** | 228 s | 21 | 7 / 1 | Yes, short: fossil-fuel plan, tripled adaptation finance ($1.3T/yr by 2035), Belém Health Action Plan, trade forum; 3 sources |
| ODAR Q2 Postgres | 4 isolation-only claims (after 1 crash) | **COMPLETE, PASS_WITH_FLAGS** | 270 s | 26 | 8 / 0 | Yes: XID visibility → dead tuples stay until VACUUM → long transactions pin xmin; 4 sources incl. PG docs |
| ODAR Q3 min wage | 2 trivial claims | **COMPLETE, audit PASS** | 278 s | 22 | 8 / 0 | Yes, balanced: disagreement + Kaplan + CBO 1.3M + Seattle phase-ins; says what it can't conclude |
| GPTR Q1 | useful | useful | 175 s | 3 | n/a | Yes (long report, 9 sources) |
| GPTR Q2 | useful | useful | 281 s | 3 | n/a | Yes (5 sources) |
| GPTR Q3 | useful, one-sided | useful, still leans "does reduce" | 333 s | 5 | n/a | Yes (10 sources) |

Score: ODAR substantive answers **0/3 → 3/3**; zero false SUPPORTS+REFUTES conflicts on plain facts (the COP30 failure). Every ODAR answer sentence carries a clickable link; all 12 cited URLs came from search results (10 return 200 to curl; carbonbrief.org and epionline.org bot-block curl but ODAR fetched them). ODAR answers are ~220–320 words vs GPTR's 1,500–3,000 — ODAR is now correct and readable, GPTR is still deeper.

Run notes (honest):
- Q2 "after" is the round-1-fix run (`bench/v2/odar_runB_round1/`). Its round-2 rerun hit the 540 s deadline (CANCELLED, 9 certified claims) while Token Harbor's free route was returning repeated 5xx; retries recovered each call but ate the wall clock. Not a logic regression.
- Round-1 rerun found a new bug, now fixed in `0003`: a sentence split at "Feb." produced a subject-less claim ("8 estimates a $15 minimum wage…"), and the synthesiser filled the gap with "Moody's Analytics" (the source says CBO). Fix: abbreviation-aware sentence splitting + synthesis sentences are dropped if any proper name or number isn't in the facts they cite.
- First rerun with legacy `duckduckgo_search` 8.x returned NO_EVIDENCE on all three: that package now routes to Bing only and returns empty in this sandbox, and the new search-only fetch policy correctly refused guessed URLs. Switched ODAR to the maintained `ddgs` package (multi-backend). The round-1 table's "same DDGS backend" was therefore not fully true for ODAR; GPTR used `ddgs` both times.
- Retries observed: 2–6 per ODAR run (Token Harbor 5xx), all recovered; no run failed on a transient error (round 1: one hard crash).
- Weak spots still visible: ODAR repeats a dated framing from a 2019 source ("$7.25, not raised in a decade") and occasionally writes a filler sentence about what a source covers. Efficiency is still 21–26 model calls vs GPTR's 3–5.
- Cost: GPTR's JSON `costs_reported` (e.g. 0.11, 0.17) is LiteLLM's list-price estimate for the model name, not a charge. Both tools ran on a `:free` Token Harbor route; actual spend $0.

Validation on the branch: 264 passed / 2 skipped (default), 13/13 integration + benchmark-fix tests, Ruff clean, Mypy clean on 16 modules. Patches `0002`+`0003` apply cleanly on `827cb1d` and reproduce the branch tree exactly.

## Round 3 — deep mode (2026-10-07, free Token Harbor models only, $0)

ODAR `--mode deep --subquestions 4 --reflection-rounds 1 --max-search 14 --max-fetch 16 --max-model-calls 20 --max-verifications 80`. Routes: planner/writer deepseek-v4-flash:free → mimo-v2.5:free → mimo-v2.6-flash:free fallback. GPT Researcher numbers reuse round-1 reports (GPTR code unchanged, same mimo-v2.5:free model).

| Q | ODAR single (round 2) words / sources | ODAR deep words / sources | Deep time | LLM calls | NLI verifications | Cited sentences | Strict NLI entailed by cited page | GPTR words / sources | GPTR cited sentences | GPTR strict NLI entailed |
|---|---|---|---|---|---|---|---|---|---|---|
| Q1 COP30 | 248 / 3 | **838 / 8** | 218 s | 15 | 32 | 100% | 20% | 1,397 / 6 | 65% | 10% (10/20 cited pages unreachable) |
| Q2 Postgres | 310 / 4 | **701 / 9** | 305 s | 16 | 33 | 100% | 60% | 1,922 / 3 | 51% | 35% |
| Q3 min wage | 247 / 5 | 305 / 4 | 522 s | 13 | 25 | 100% | 54% | 1,792 / 4 | 65% | 0% (14/20 unreachable) |

Metric (bench/v3/cite_eval.py): body words; share of sentences carrying a citation; for up to 20 cited sentences, max entailment ≥0.5 of the sentence against its cited page(s) (nli-deberta-v3-small, best 24 spans). Strict: paraphrased multi-fact sentences score low for both systems, and it uses ODAR's own verifier family, so read it as relative.

Findings:
- Useful answers: 3/3 (deep) — Q1 and Q2 now structured, multi-section reports (summary + 4 sub-question sections) covering isolation AND bloat/VACUUM; Q3 thin.
- Every deep sentence carries a citation; a new sentence-level NLI gate drops writer sentences their cited facts don't entail (12 dropped on Q1, 22 on Q3).
- Q3 limit is retrieval, not reasoning: 12/16 fetches failed (403 from this sandbox on CBO/IGM/NBER/PDFs) and DDG timed out (search p50 6.7 s), so only 4 sources. A paid search/fetch API (plan item 5) is the fix.
- Router: mimo-v2.5:free returned empty text and was disabled mid-run on Q1 and Q2; fallback to deepseek-v4-flash:free kept both runs going. No run failed on a model error (round 1 had a crash on Q2).
- Still behind GPTR on length (≈0.4–0.6×); ahead on citation coverage and checked support.
- Sandbox rebooted once mid-benchmark, losing one pre-gate Q1 run; final numbers are all post-fix runs.

## Round 4: ODAR Ask vs Duck.ai free (2026-10-08)
Same 10 questions (bench/h2h/questions.txt), all scored by `odar check` (LLM judge, search off). Duck.ai: GPT-5.4 mini and Claude Haiku 4.5, web search on, no login. Duck source-name chips were mapped to [n] markers (bench/h2h/build_duck.py); answers with no chips got all refs appended at the end.
| Metric | ODAR | Duck GPT-5.4 mini | Duck Claude Haiku 4.5 |
|---|---|---|---|
| Avg trust score | 82 | 55 | 62 |
| Claims carrying a citation | 97% (91/94) | 35% (18/51) | 55% (81/147) |
| Cited claims fully supported | 62% | 39% | 54% |
| Supported or partly | 81% | 61% | 67% |
| Wrong source or contradicted | 8% | 22% | 31% |
| Answer time | 29-63 s | ~1-10 s | ~7-15 s |
Caveats: Duck cites per paragraph via chips, so "uncited" partly reflects its style; Duck timings eyeballed (no timer). Perplexity excluded (login wall), ChatGPT logged-out not scored.

## Round 5 (2026-10-08): ODAR after citation-accuracy fixes (same 10 questions, same checker)
Fixes (commits c374ca3 + d7fe168): quote-first prompt with exact numbers, snippet-only pages hidden from the model when ≥3 pages read in full, no markers on framing sentences, auto-repair pass (re-cite to best supporting source, drop unsupported sentences).

| Metric | ODAR before | ODAR after |
|---|---:|---:|
| Avg trust | 82 | 89 |
| Cited claims fully supported | 62% | 85% |
| Supported or partly | 81% | 91% |
| Wrong source / contradicted | 8% | 5% |
| Unverifiable | 11% | 4% |
| Answer time | 29–63s | 32–83s |
Per question: 96, 80, 100, 70, 93, 90, 67, 100, 100, 92. Repair pass changed 4 citations across 10 answers; most of the gain came from the prompt and readable-only sources. Weak spots left: Q4 repo rate (1 contradicted), Q7 temperature (2 partial). Search occasionally returned nothing (Q2, Q10 on first try), now retried once. Caveat: answers differ run to run; one run per question.

## Round 6 (2026-10-08, branch feat/ask-citation-accuracy @ HEAD)
Same 10 questions + ODAR checker. Trust: 86, 100, 100, 100, 95, 100, 91, 100, 100, 90 → avg 96.2 (round 5: 88.8). No planning leakage in any answer; 0 contradicted, 0 no-citation across all 10.
Fixes since round 5: warm verifier + 90s verify cap, stream only <answer> content, strip leaked planning + re-ask once when reply is only notes, keyword fallback search, one-fact-per-sentence prompt, no invented study descriptors. q1/q7/q10 still lose points on partial support of compound sentences. Free-model run-to-run variance is ±5–10 per question. 380 tests pass.

## Round 9 (2026-10-08) — Parallel Search + query planner + hedged writer race (branch feat/ask-citation-accuracy, 654464e)
Trust per question (1-10): 97, 92, 100, 100, 86, 94, 100, 100, 100, 100 → **avg 96.9** (round 6: 96.2). No contradicted claims, no dead links.
Answer time avg ~25 s (round 6 ~31 s); search+page check ~11-14 s. 388 tests pass.
Fixes on the way (round 7 avg ~77, round 8 avg 68.6): PubMed now serves a cookie wall to scripts -> read via NCBI E-utilities; cookie/captcha walls treated as unreadable; Parallel sources the reader can't open (401/403/404/429) drop out; cite page text not search excerpts; best writer first, OpenCode backup after 12 s; no listing dates in answers; clear and re-ask when the streamed answer has no citations.

## Round 10: Duck.ai strongest model (2026-10-09)
Duck.ai's strongest free model is **GPT-6 Luna** (default). Paid-locked: GPT-5.6 Terra, Claude Sonnet 4.6 (Plus); GPT-5.6 Sol, Claude Opus 4.8 (Pro). Web search on, no login, new chat per question. Same 10 questions, same `odar check` scorer (bench/h2h/build_duckstrong.py, agg.py; files duckstrong_q*.json, score_duckstrong_q*.json).

| Metric | ODAR (round 9) | Duck GPT-6 Luna | Duck GPT-5.4 mini | Duck Claude Haiku 4.5 |
|---|---|---|---|---|
| Avg trust | 96.9 | 58 | 55 | 62 |
| Claims carrying a citation | 100% | 40% (29/72) | 35% (18/51) | 55% (81/147) |
| Cited claims fully supported | 95%* | 34% | 39% | 54% |
| Wrong source or contradicted | 0% | 17% (5 wrong source, 0 contradicted) | 22% | 31% |
| Answer time | ~25 s | ~7-14 s | ~1-10 s | ~7-15 s |

Duck GPT-6 Luna per question: 30, 0, 100, 50, 88, 50, 100, 0, 62, 100. Short, well-written answers, but one chip per paragraph leaves most sentences uncited (Q2: one chip for 8 claims -> 0). Q8 cited a source (src 3) its panel never showed, so that claim counts uncited. Q4 says repo rate raised to 5.50% on Oct 7, 2026 (judged partly supported).
*ODAR supported % from the 8 round-9 score files still on disk (q2/q6 score files missing); other ODAR figures as recorded in round 9. Duck timings eyeballed (no timer).
