# ODAR Check: citation report

**Trust score: 8/100** (unreliable citations)

PARTIALLY SUPPORTED: 1 · WRONG SOURCE: 3 · DEAD LINK: 2 · NO CITATION: 2

| # | Claim | Verdict | Source |
|---|---|---|---|
| 1 | For backend services, Rust and Go differ primarily in memory management, concurrency, and performance. | NO CITATION | none |
| 2 | Rust guarantees memory safety at compile time through ownership and borrowing, eliminating garbage collection… | PARTIALLY SUPPORTED | doc.rust-lang.org/book/ch04-00-understanding-ownership.html |
| 3 | Go uses a runtime GC, which simplifies development but adds latency and memory overhead. | WRONG SOURCE | go.dev/doc/faq |
| 4 | Concurrency-wise, Go offers lightweight goroutines and channels natively, making it straightforward to build … | WRONG SOURCE | go.dev/doc/effective_go |
| 5 | Rust supports concurrency via OS threads and async/await (e.g., Tokio), giving finer control but requiring mo… | WRONG SOURCE | rust-lang.github.io/async-book/01_getting_started/01_chapte… |
| 6 | Rust generally outperforms Go in CPU-bound and low-latency workloads due to zero-cost abstractions and no run… | DEAD LINK | www.rust-lang.org/performance |
| 7 | Go emphasizes fast compilation and operational simplicity. | DEAD LINK | www.rust-lang.org/performance |
| 8 | The ecosystems also differ: Go’s standard library excels at HTTP and microservices, while Rust’s crate ecosys… | NO CITATION | none |

## Details

### 1. NO CITATION
> For backend services, Rust and Go differ primarily in memory management, concurrency, and performance.


### 2. PARTIALLY SUPPORTED
> Rust guarantees memory safety at compile time through ownership and borrowing, eliminating garbage collection (GC) pauses.

- [1] https://doc.rust-lang.org/book/ch04-00-understanding-ownership.html: **PARTIALLY SUPPORTED** (link: live; entailment 0.00)
  - quote: "It enables Rust to make memory safety guarantees without needing a garbage collector, so it’s important to understand how ownership works."
  - note: partly supported: Supports memory safety without GC via ownership, but no compile-time detail.

### 3. WRONG SOURCE
> Go uses a runtime GC, which simplifies development but adds latency and memory overhead.

- [2] https://go.dev/doc/faq#garbage_collection: **WRONG SOURCE** (link: live; entailment 0.00)
  - note: the page is real but does not say this
- suggested source: none found that supports this claim

### 4. WRONG SOURCE
> Concurrency-wise, Go offers lightweight goroutines and channels natively, making it straightforward to build high-concurrency network services.

- [3] https://go.dev/doc/effective_go#concurrency: **WRONG SOURCE** (link: live; entailment 0.00)
  - note: the page is real but does not say this
- suggested source: none found that supports this claim

### 5. WRONG SOURCE
> Rust supports concurrency via OS threads and async/await (e.g., Tokio), giving finer control but requiring more design effort.

- [4] https://rust-lang.github.io/async-book/01_getting_started/01_chapter.html: **WRONG SOURCE** (link: live; entailment 0.00)
  - note: the page is real but does not say this
- suggested source: none found that supports this claim

### 6. DEAD LINK
> Rust generally outperforms Go in CPU-bound and low-latency workloads due to zero-cost abstractions and no runtime.

- [5] https://www.rust-lang.org/performance: **DEAD LINK** (link: not_found; entailment 0.00)
  - note: page not found (404); archive check unavailable
- note: atomic part of a compound sentence

### 7. DEAD LINK
> Go emphasizes fast compilation and operational simplicity.

- [5] https://www.rust-lang.org/performance: **DEAD LINK** (link: not_found; entailment 0.00)
  - note: page not found (404); archive check unavailable
- note: atomic part of a compound sentence

### 8. NO CITATION
> The ecosystems also differ: Go’s standard library excels at HTTP and microservices, while Rust’s crate ecosystem is rich but demands a steeper learning curve.


---
check chk_9c39a0211eb5 · evaluator cross-encoder/nli-deberta-v3-small@cross-encoder/nli-deberta-v3-small/thr=0.75 · 94 s · fetches 10, wayback_lookups 2, searches 3, llm_calls 3, nli_checks 12, scorer_load_ms 12577
