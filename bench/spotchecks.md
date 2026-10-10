# Citation spot-checks (manual: fetched source, searched for the claim)
## GPTR q1 (COP30) — 7 checked
1. 195 countries approved 29 documents; forest fund (Agência Brasil/ANBA) — SUPPORTED
2. ~80 nations backed fossil-fuel roadmap (ANBA) — SUPPORTED
3. Tripling adaptation finance by 2035, "later than some hoped" (Carbon Brief) — SUPPORTED
4. RSO text failed to endorse IPCC as "best available science" (Carbon Brief) — SUPPORTED
5. Three trade dialogues in Bonn 2026/27/28 (Carbon Brief) — SUPPORTED
6. Mountains dialogue (C2ES PDF p.17) — SUPPORTED
7. "insufficiently incisive" (UNGCN Italia) — SUPPORTED
8. WRI "underdelivers on fossil fuels" attributed to Waskow et al. article — PARTIAL (phrase is the title of a separate WRI statement linked from that page)
All 5 reference URLs return HTTP 200. No fabricated sources.

## GPTR q2 (Postgres MVCC) — 8 checked
1. "reading never blocks writing..." (postgresql.org mvcc-intro) — SUPPORTED
2. "UPDATE-heavy workload writes roughly as much as INSERT-heavy, plus index churn" (pgviz) — SUPPORTED
3. SSI overhead 5–15% in micro-benchmarks (martinuke0 05-27) — SUPPORTED by cited page (page itself gives no measurement; just hand-waves to docs)
4. 500M rows / 10k TPS autovacuum lags (martinuke0 05-27) — SUPPORTED
5. Storage growth 12%/yr vs 35%; parallel_leader_participation keeps bloat <5% (martinuke0 05-30) — SUPPORTED by page, but technically dubious (parallel_leader_participation is a query-executor setting, not a vacuum control); reads like an unverified/AI-written case study
6. idle_in_transaction_session_timeout '5min' (rajpoot) — SUPPORTED
7. n_dead_tup not an exact bloat measure (techbuzzonline) — SUPPORTED
8. Autovacuum no exclusive lock, doesn't shrink file (dev.to) — SUPPORTED
All 8 reference URLs return HTTP 200. Citation faithfulness high; source quality low: 3 of 8 refs are one blog's auto-generated "deep dive" series, only 1 official doc cited (VACUUM docs page fetched but not cited). Report's own SQL example labels VACUUM FULL as part of routine "aggressive" vacuuming — an error a reader could act on.

## ODAR q1 (COP30) — nothing to check
COMPLETE but abstained: 0 certified claims. Extracted 6 claims as raw source sentences (mostly Carbon Brief), and the NLI evaluator (deberta-v3-small) scored *both* support and refutation high (e.g. 0.98 vs 0.86, 0.99 vs 1.00) for plainly factual sentences → all CONFLICTING. 4/8 fetches failed (un.org, enb.iisd.org, iisd.org, unfccc.int PDF). Audit PASS_WITH_FLAGS (3 unexamined contradictions). No hallucinated or dead citations (there are none).

## GPTR q3 (minimum wage) — 9 checked
1. Neumark & Shirley 78.9% / 53.9% / 46.1% negative elasticities (NBER PDF) — SUPPORTED (exact)
2. Upjohn: MW reduces job growth over several years, strongest for young workers (upjohn.org) — SUPPORTED
3. Upjohn: hours fall, capital investment rises (upjohn.org) — SUPPORTED
4. Belman & Wolfson meta-analysis >200 studies "strongest evidence yet" (upjohn.org) — SUPPORTED, but report omits that meta-analysis's own conclusion (small/near-zero employment effects)
5. Increases up to ~59% of local median wage have small effects (ryanoconnellfinance.com) — SUPPORTED
6. CBO 2021: 1.4M jobs, 17M raises, 900K out of poverty (ryanoconnellfinance.com) — SUPPORTED by cited page, but second-hand (finance blog, not CBO)
7. Time-series: 10% MW → 1–3% teen employment drop (americanactionforum.org) — SUPPORTED
8. 166 economists, ~3/4 oppose $15 (epionline.org) — SUPPORTED (survey commissioned by Employment Policies Institute, industry-aligned; report does flag it)
9. Neumark & Wascher "clearly incorrect" quote (NBER w12663) — SUPPORTED
All 8 reference URLs return HTTP 200. Faithfulness high. Balance issue: report takes a firm side ("raising the minimum wage does reduce employment") on a contested question; it fetched three Economic Policy Institute pages arguing little/no job loss and cited none of them; the opposing view is represented mainly by a 2022 advocacy page and a Deaton/Diamond quote.
