# ODAR Check evaluation — 2026-10-07 (final run after fixes)

Models: free Token Harbor routes only (deepseek-v4-flash:free for answer generation + atomization/adjudication). Spend: $0.
NLI: local cross-encoder (CPU). Runtime 60–126 s per answer.

## Planted fake citations (p1 malaria, p2 programming) — ground truth in p*.truth.json
| Set | Claims | Exact verdict | Fakes flagged | Real claims wrongly flagged |
|---|---|---|---|---|
| p1 | 8 | 8/8 | 6/6 | 0/2 |
| p2 | 8 | 7/8 (one SUPPORTED shown as PARTIALLY SUPPORTED) | 5/5 | 0/3 |
| Total | 16 | 15/16 (94%) | 11/11 recall, 11/11 precision | 0 |

## Five free-model answers (a1–a5), hand-labelled after the run
| Answer | Claims | Retrieval-unavailable | Correct | Wrong | Unsure |
|---|---|---|---|---|---|
| a1 fasting (JAMA/NEJM 403) | 12 | 6 | 6 | 0 | 0 |
| a2 2008 crisis | 7 | 0 | 5 | 0 | 2 (FCIC page WRONG SOURCE) |
| a3 mRNA | 12 | 0 | 12 | 0 | 0 |
| a4 Rust vs Go | 8 | 0 | 4 | 4 | 0 |
| a5 climate (IPCC PDF) | 8 | 4 | 4 | 0 | 0 |
| Total | 47 | 10 | 31/37 judged (84%) | 4 | 2 |

Findings:
- 15 hallucinated/dead links in real model answers, all caught (incl. Fed/St. Louis Fed soft-404s returning HTTP 200).
- Every NO CITATION verdict correct (16).
- Weak spot: content judgments on live pages. a4: 3 true paraphrased claims marked WRONG SOURCE (NLI misses paraphrase), 1 false CONTRADICTED (Go FAQ GC passage). False negatives err toward caution but are noisy.
- Retrieval: paywalled journals return 403 in sandbox; PDFs (IPCC SPM) unsupported -> reported UNVERIFIABLE, not guessed.

# Round 2 — PDF reading + reworded-claim judge (2026-10-07, patch 0006)

Changes: opt-in PDF extraction (pypdf, 25 MB / 80-page caps, SSRF path unchanged); LLM "paraphrase judge" reads top passages and must quote the page verbatim (numbers must appear in the quote); LLM second opinion on NLI contradictions, except explicit on-topic negations, which stand.

| Set | Claims | Unreadable | Correct | Wrong | Unsure |
|---|---|---|---|---|---|
| Planted p1+p2 | 16 | 0 | 16/16 (11/11 fakes flagged, 0 false flags) | 0 | 0 |
| a1 fasting | 12 | 6 (JAMA/NEJM 403) | 6 | 0 | 0 |
| a2 2008 crisis | 9 | 0 | 7 | 0 | 2 |
| a3 mRNA | 14 | 0 | 14 | 0 | 0 |
| a4 Rust vs Go | 8 | 0 | 5 | 2 | 1 |
| a5 climate (IPCC PDF now read) | 8 | 0 | 7 | 0 | 1 |
| Real answers total | 51 | 6 (was 10) | 39/45 (87%); 39/41 decided (95%) | 2 (was 4) | 4 |

Remaining misses: a4 goroutines (Effective Go) and Rust async-book claims still WRONG SOURCE (passages too lexically distant to reach the judge). False Go-GC contradiction fixed. Free models only, $0.
