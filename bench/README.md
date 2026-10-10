# ODAR benchmarks

Every benchmark run against ODAR so far: the questions, the raw answers from each system, the scores, and the scripts that produced them. The full round-by-round log is in [RESULTS.md](RESULTS.md); the citation checker's own evaluation is in [check/RESULTS.md](check/RESULTS.md).

All runs used free models only (Token Harbor `:free` routes), so total spend was $0.

## Headline results

**Ask mode vs Duck.ai** (10 questions, scored claim by claim by `odar check`):

| Metric | ODAR Ask (round 9, 654464e) | Duck.ai GPT-6 Luna | Duck.ai GPT-5.4 mini | Duck.ai Claude Haiku 4.5 |
|---|---|---|---|---|
| Avg trust score | 96.9 | 58 | 55 | 62 |
| Claims carrying a citation | 100% | 40% | 35% | 55% |
| Wrong source or contradicted | 0% | 17% | 22% | 31% |
| Answer time | ~25 s | ~7-14 s | ~1-10 s | ~7-15 s |

**ODAR Ask over time** (same 10 questions, same scorer): avg trust 82 (round 4) → 89 (round 5) → 96.2 (round 6) → 96.9 (round 9).

**Deep research vs GPT Researcher** (3 questions, same free model and search): ODAR deep cites 100% of sentences against GPT Researcher's 51-65%, with higher strict NLI support on every question. GPT Researcher still writes 1.7-6x longer reports.

**ODAR Check** (citation verifier): caught 11/11 planted fake citations with no false alarms, and every one of 15 dead or hallucinated links in real model answers.

## Caveats

- One run per question; free-model answers vary by about ±5-10 trust points between runs.
- `odar check` is ODAR's own verifier, so it scores ODAR's answers too. The planted-citation test in `check/` is the independent sanity check on it.
- Duck.ai timings were eyeballed, and Duck cites per paragraph through chips, which counts against it on "claims carrying a citation".
- Runs happened in a sandbox whose proxy blocked some sites (403/502), which hurt retrieval for both sides in rounds 1-3.

## Layout

| Path | What it holds |
|---|---|
| `questions.json`, `odar_q*`, `gptr_q*`, `spotchecks.md`, `attempts/` | Round 1: ODAR vs GPT Researcher, plus failed attempts kept for the record |
| `v2/` | Round 2: same questions after the verifier and synthesis fixes |
| `v3/` | Round 3: deep mode, with `cite_eval.py` (NLI citation metric) and `analyze.py` |
| `check/` | ODAR Check evaluation: planted fakes (`p*.md` + `p*.truth.json`) and real answers (`a*.md`) |
| `h2h/` | Rounds 4-10: ODAR Ask vs Duck.ai. `questions.txt`, raw answers (`odar*_q*.json`, `duck*_q*.json`), scores (`score_*.json`), Duck notes in `duck/` |

## Rerunning

Scripts need an Anthropic-compatible key and base URL. Put them in a `.env` (or point `ODAR_BENCH_ENV` at one) and set `PYTHON` to your venv's interpreter if it is not `python`.

```bash
# Ask mode, questions 1-10, results prefixed odar10_
bench/h2h/run_all.sh 1 10 odar10
(cd bench/h2h && python agg.py odar10 duckstrong)   # summary per system

# Deep mode vs GPT Researcher, one question per call
bench/run_odar.sh q1
GPTR_PYTHON=/path/to/gptr-venv/bin/python bench/run_gptr.sh q1
```

Delete stale `score_*.json` for a prefix before rescoring it.
