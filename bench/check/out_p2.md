# ODAR Check: citation report

**Trust score: 38/100** (unreliable citations)

SUPPORTED: 3 · WRONG SOURCE: 3 · DEAD LINK: 2

| # | Claim | Verdict | Source |
|---|---|---|---|
| 1 | In Python, functools.cache is a simple lightweight unbounded function cache, sometimes called memoize. | SUPPORTED | docs.python.org/3/library/functools.html |
| 2 | The functools.cache decorator evicts the least recently used values once it reaches 128 entries. | WRONG SOURCE | docs.python.org/3/library/functools.html |
| 3 | The cache is threadsafe so that the wrapped function can be used in multiple threads. | SUPPORTED | docs.python.org/3/library/functools.html |
| 4 | Go is an open-source programming language that focuses on simplicity, reliability, and efficiency. | SUPPORTED | go.dev/doc/effective_go |
| 5 | Go was designed by Microsoft as a replacement for C#. | WRONG SOURCE | go.dev/doc/effective_go |
| 6 | Rust guarantees memory safety without a garbage collector. | DEAD LINK | doc.rust-lang.org/book/ch99-07-memory-safety-guarantees.html |
| 7 | JSON support in Python is provided by the json module in the standard library. | DEAD LINK | www.python-json-handbook.dev/stdlib/json-module |
| 8 | Python dictionaries preserve insertion order since Python 3.7. | WRONG SOURCE | www.who.int/news-room/fact-sheets/detail/malaria |

## Details

### 1. SUPPORTED
> In Python, functools.cache is a simple lightweight unbounded function cache, sometimes called memoize.

- inline https://docs.python.org/3/library/functools.html: **SUPPORTED** (link: live; entailment 0.03)
  - quote: "Simple lightweight unbounded function cache. Sometimes called “memoize”."
  - note: reworded match: P2 exactly describes functools.cache as claimed.

### 2. WRONG SOURCE
> The functools.cache decorator evicts the least recently used values once it reaches 128 entries.

- inline https://docs.python.org/3/library/functools.html: **WRONG SOURCE** (link: live; entailment 0.05)
  - note: the page is real but does not say this
- suggested source: none found that supports this claim

### 3. SUPPORTED
> The cache is threadsafe so that the wrapped function can be used in multiple threads.

- inline https://docs.python.org/3/library/functools.html: **SUPPORTED** (link: live; entailment 0.99)
  - quote: "The cache is threadsafe so that the wrapped function can be used in multiple threads."

### 4. SUPPORTED
> Go is an open-source programming language that focuses on simplicity, reliability, and efficiency.

- inline https://go.dev/doc/effective_go: **SUPPORTED** (link: live; entailment 0.99)
  - quote: "Go is an open-source programming language that focuses on simplicity, reliability, and efficiency, specifically designed to make it easy to build software at scale."

### 5. WRONG SOURCE
> Go was designed by Microsoft as a replacement for C#.

- inline https://go.dev/doc/effective_go: **WRONG SOURCE** (link: live; entailment 0.00)
  - note: the page is real but does not say this
- suggested source: none found that supports this claim

### 6. DEAD LINK
> Rust guarantees memory safety without a garbage collector.

- inline https://doc.rust-lang.org/book/ch99-07-memory-safety-guarantees.html: **DEAD LINK** (link: not_found; entailment 0.00)
  - note: page not found (404); archive check unavailable

### 7. DEAD LINK
> JSON support in Python is provided by the json module in the standard library.

- inline https://www.python-json-handbook.dev/stdlib/json-module: **DEAD LINK** (link: no_domain; entailment 0.00)
  - note: domain does not resolve (archive check unavailable)

### 8. WRONG SOURCE
> Python dictionaries preserve insertion order since Python 3.7.

- inline https://www.who.int/news-room/fact-sheets/detail/malaria: **WRONG SOURCE** (link: live; entailment 0.00)
  - note: the page is real but does not say this
- suggested source: none found that supports this claim

---
check chk_94b2249800ab · evaluator cross-encoder/nli-deberta-v3-small@cross-encoder/nli-deberta-v3-small/thr=0.75 · 90 s · fetches 9, wayback_lookups 4, searches 3, llm_calls 4, nli_checks 9, scorer_load_ms 12470
