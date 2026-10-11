# Deep research mode (`--mode deep`)

The web app's Research mode and `run_research.py run --mode deep` use this pipeline. The model writes; it never certifies anything.

| Step | Model calls | What happens |
|---|---|---|
| Plan | 1 | The planner decides the question's **kind**. A `fact` question (one name, number or date) gets one sharpened sub-question with 3 queries. A `research` question gets N sub-questions from different angles with 2 queries each, broad first. The planner can also flag that peer-reviewed sources matter, which switches on the free scholarly APIs (`--no-auto-academic` turns that off). |
| Research | 0 | Researchers run in parallel, and each researcher runs its own queries in parallel too (at most 4 searches at once per run). They search, rank, and fetch search-result URLs only: 5 pages per sub-question by default (`--fetch-per-subquestion`), 6 for a fact question. A search or page already read in the run is served from a run-scoped cache. Blocked pages (401/403/406/429/451/503, bot challenges, timeouts) are retried through the Jina Reader and then the Internet Archive's closest snapshot. The report says when a source was read that way. |
| Mine | 0 | Verbatim declarative sentences, scored for relevance and informativeness. Figures and names count up; meta text, reference-list lines and dangling-pronoun openers count down. Footnote numbers stuck to the start of a sentence are removed. Near-duplicates are dropped. For a fact question, sentences that carry the kind of answer asked for (a year, an amount, people's names, an id like COP21) rank higher, and the sub-question gets up to 16 fact slots. A sentence whose figure contradicts its own percentage by a factor of ten ("usually 2 (or 20%)", "005 (0.5%)": a decimal that lost its "0." on the source page) is never mined as a fact and never used as evidence. |
| Verify | 0-1 per pass | The local NLI cross-encoder checks each fact against its sources. Every contradiction the NLI raises in a pass goes to **one** batched judge call. Claims whose conflicts the judge dismisses are re-verified from cache. Confirmed conflicts are kept and shown. |
| Reflect | 0-1 | Skipped when every sub-question already has enough certified facts, or when under half the time budget is left. |
| Write | 1 | **One** call writes the summary and every section from globally numbered facts. Each sentence must cite a fact, may not add names or numbers, and must be entailed by the facts it cites (NLI gate). For a fact question, if the summary's first sentence states none of the answer values in the certified fact that answers the question, that fact is placed first, verbatim and cited. |
| Repair | 0-1 | With the single-call writer, sentences that fail the NLI gate are not dropped straight away. **One** call asks the writer to rewrite all of them, each only from the facts it cited. A rewrite is kept only if it passes the same checks as a first draft (citations limited to the original sentence's facts, names and numbers grounded, NLI entailment); otherwise the sentence is dropped. |
| Fallback | 0 | If the writer's reply is unusable, the run falls back to per-section writers (`--writer sections` forces that mode). |

Typical total: 3-5 model calls per run, down from 13-16 before `feat/best-research`.

The NLI model is loaded once per process and shared by every auditor (deep runs, Check, the web server). It loads from the local Hugging Face cache without contacting the Hub, in the background while the planner call runs. Set `ODAR_NLI_ALLOW_HUB_CHECK=1` to restore the Hub check.

## Citation precision

Every citation in the answer must point at a source that carries the text it is attached to.

- **Which sources a fact cites.** The source whose span best matches the fact's wording comes first. A further source is cited only when its span carries the fact lexically: at least 60% of its content words and every name and number it states. A topically similar span that only the NLI model accepted is not cited. The Certified Findings list shows the same sources the answer cites.
- **Which facts a sentence cites.** A cited fact backs a sentence (or one of its claims) only when the NLI model entails it from that fact alone **and** the fact holds at least half of its content words. Citations to facts that back nothing in the sentence are removed.
- **Merged sentences are split.** When a sentence's claim units rest on different facts and each unit can stand as a sentence, it is split into one sentence per fact, each citing only its own fact. A split clause that repeats an earlier sentence is dropped. (If a unit cannot stand alone, such as a trailing "with ..." clause, the sentence keeps its in-place markers for the facts that back it.)
- **Dropped:** sentences about "the certified facts" themselves, and writer inferences that share little wording with any cited fact.
- **Source text clean-up.** A page's own reference markers (`[15]`, `[7,8]`) are removed from quoted facts and spans, so they are not shown as links to unrelated sources. Bibliography lines are not mined as facts and are not shown as conflicts.

## Trust sections in every report
- **Confidence and evidence**: overall High/Medium/Low, plus a per-section table (verified facts, independent sites, conflicts). A Low result carries an explicit "treat as tentative" line.
- **Freshness**: the span of years among dated sources, flagged when the question is time-sensitive and some sources are more than 2 years old.
- **Where sources disagree**: each unresolved conflict, with the claim and the contradicting passage as separate sentences, each citing its own source. Neither side is stated as fact.
- **Open questions**: sub-questions with no certified evidence.
