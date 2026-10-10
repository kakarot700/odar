# ODAR Check: citation report

**Trust score: 33/100** (unreliable citations)

SUPPORTED: 1 · UNVERIFIABLE: 2 · DEAD LINK: 2

| # | Claim | Verdict | Source |
|---|---|---|---|
| 1 | PostgreSQL implements three distinct isolation levels internally, and Read Uncommitted behaves like Read Comm… | SUPPORTED | www.postgresql.org/docs/current/transaction-iso.html |
| 2 | The Eiffel Tower was completed in 1889. | UNVERIFIABLE | en.wikipedia.org/wiki/Eiffel_Tower |
| 3 | The Eiffel Tower is made entirely of aluminium. | UNVERIFIABLE | en.wikipedia.org/wiki/Eiffel_Tower |
| 4 | Python 3.12 removed the distutils package. | DEAD LINK | docs.python.org/3/whatsnew/3.12-removed-distutils-guide.html |
| 5 | The Great Wall of China is visible from the Moon with the naked eye. | DEAD LINK | www.nasa-moonfacts-archive.org/great-wall-visible |

## Details

### 1. SUPPORTED
> PostgreSQL implements three distinct isolation levels internally, and Read Uncommitted behaves like Read Committed.

- [1] https://www.postgresql.org/docs/current/transaction-iso.html: **SUPPORTED** (link: live; entailment 1.00)
  - quote: "In PostgreSQL, you can request any of the four standard transaction isolation levels, but internally only three distinct isolation levels are implemented, i.e., PostgreSQL's Read Uncommitted mode behaves like Read Committed."

### 2. UNVERIFIABLE
> The Eiffel Tower was completed in 1889.

- [2] https://en.wikipedia.org/wiki/Eiffel_Tower: **UNVERIFIABLE** (link: blocked; entailment 0.00)
  - note: page exists but could not be read (HTTP 403); no archive copy
- suggested source: https://geographypin.com/the-original-purpose-of-the-eiffel-tower/ (entailment 0.99)
  - quote: "How the Eiffel Tower Fulfilled Its Purpose During the 1889 Fair"

### 3. UNVERIFIABLE
> The Eiffel Tower is made entirely of aluminium.

- [2] https://en.wikipedia.org/wiki/Eiffel_Tower: **UNVERIFIABLE** (link: blocked; entailment 0.00)
  - note: page exists but could not be read (HTTP 403); no archive copy
- suggested source: none found that supports this claim

### 4. DEAD LINK
> Python 3.12 removed the distutils package.

- [3] https://docs.python.org/3/whatsnew/3.12-removed-distutils-guide.html: **DEAD LINK** (link: not_found; entailment 0.00)
  - note: page not found (404) and never archived: likely fabricated URL

### 5. DEAD LINK
> The Great Wall of China is visible from the Moon with the naked eye.

- [4] https://www.nasa-moonfacts-archive.org/great-wall-visible: **DEAD LINK** (link: no_domain; entailment 0.00)
  - note: domain does not resolve and was never archived: likely fabricated URL

---
check chk_b5dff84b8bee · evaluator cross-encoder/nli-deberta-v3-small@cross-encoder/nli-deberta-v3-small/thr=0.75 · 209 s · fetches 11, wayback_lookups 6, searches 2, llm_calls 0, nli_checks 5
