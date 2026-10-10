# Research Report: How does PostgreSQL's MVCC implementation provide transaction isolation, and why does it cause table bloat that VACUUM must clean up?

## Answer (written only from the certified findings below)
PostgreSQL implements transaction isolation using Multi-Version Concurrency Control (MVCC), in which the SQL standard's isolation levels are exposed as in Table 13.1 [[1]](https://www.postgresql.org/docs/current/transaction-iso.html). Although PostgreSQL lets you request any of the four standard levels, only three are implemented internally, so Read Uncommitted behaves as Read Committed [[1]](https://www.postgresql.org/docs/current/transaction-iso.html), and its Repeatable Read implementation even avoids phantom reads [[1]](https://www.postgresql.org/docs/current/transaction-iso.html). Visibility is decided by comparing transaction ID numbers: a row version whose insertion XID is greater than the current transaction's XID is treated as "in the future" and is hidden from that transaction [[2]](https://www.postgresql.org/docs/15/routine-vacuuming.html).

MVCC does not free space immediately: tuples that are deleted or obsoleted by an update stay in the table, physically present, until a VACUUM is done [[3]](https://www.postgresql.org/docs/14/sql-vacuum.html). VACUUM is what ultimately removes these dead tuples, using the visibility map to decide which table pages must be scanned [[2]](https://www.postgresql.org/docs/15/routine-vacuuming.html). However, VACUUM cannot reclaim a dead tuple if any active transaction might still need to see it [[4]](https://www.pginsights.dev/guides/postgres-dead-tuples-and-bloat). The frequent reason dead tuples pile up even when autovacuum runs correctly is a long-running transaction that holds the xmin horizon far in the past, and this effect can be dramatic — on a 100M-row table it can mean 20M dead tuples before vacuum runs [[4]](https://www.pginsights.dev/guides/postgres-dead-tuples-and-bloat)[[4]](https://www.pginsights.dev/guides/postgres-dead-tuples-and-bloat).

VACUUM generates a substantial amount of I/O traffic, which can degrade performance for other active sessions [[3]](https://www.postgresql.org/docs/14/sql-vacuum.html)[[2]](https://www.postgresql.org/docs/15/routine-vacuuming.html), it must ordinarily be run by the table owner or a superuser [[3]](https://www.postgresql.org/docs/14/sql-vacuum.html), and on partitioned tables a conflicting lock on the parent causes VACUUM to skip all partitions [[3]](https://www.postgresql.org/docs/14/sql-vacuum.html). The part of the question not fully answerable from the given facts is the precise per-tuple storage layout (heap tuple headers, per-column data, and tuple length overhead) that turns dead rows into wasted disk space; the facts confirm only that dead tuples remain physically present until VACUUM [[3]](https://www.postgresql.org/docs/14/sql-vacuum.html) and that they accumulate when the xmin horizon is held back [[4]](https://www.pginsights.dev/guides/postgres-dead-tuples-and-bloat).

## Certified Findings (semantic NLI verification)
- [clm_a5d43de2ce324967] The SQL standard and PostgreSQL-implemented transaction isolation levels are described in Table 13.1.
  - cited: [src_c628e01719bb4e02] [PostgreSQL: Documentation: 18: 13.2. Transaction Isolation](https://www.postgresql.org/docs/current/transaction-iso.html) (entailment p=0.98, evaluator: cross-encoder/nli-deberta-v3-small@enthr=0.75/spans=4-8)
- [clm_8086079eecac40a7] In PostgreSQL, you can request any of the four standard transaction isolation levels, but internally only three distinct isolation levels are implemented, i.e., PostgreSQL's Read Uncommitted mode behaves like Read Committed.
  - cited: [src_c628e01719bb4e02] [PostgreSQL: Documentation: 18: 13.2. Transaction Isolation](https://www.postgresql.org/docs/current/transaction-iso.html) (entailment p=0.97, evaluator: cross-encoder/nli-deberta-v3-small@enthr=0.75/spans=4-8)
- [clm_c5a03dd1428d4540] The table also shows that PostgreSQL's Repeatable Read implementation does not allow phantom reads.
  - cited: [src_c628e01719bb4e02] [PostgreSQL: Documentation: 18: 13.2. Transaction Isolation](https://www.postgresql.org/docs/current/transaction-iso.html) (entailment p=0.99, evaluator: cross-encoder/nli-deberta-v3-small@enthr=0.75/spans=4-8)
- [clm_1ac9d7bb34d245d6] PostgreSQL's MVCC transaction semantics depend on being able to compare transaction ID (XID) numbers: a row version with an insertion XID greater than the current transaction's XID is “in the future” and should not be visible to the current transaction.
  - cited: [src_090c5fef40044bcd] [PostgreSQL: Documentation: 15: 25.1. Routine Vacuuming](https://www.postgresql.org/docs/15/routine-vacuuming.html) (entailment p=0.99, evaluator: cross-encoder/nli-deberta-v3-small@enthr=0.75/spans=4-8)
- [clm_3fff5dcb7a42491c] VACUUM uses the visibility map to determine which pages of a table must be scanned.
  - cited: [src_090c5fef40044bcd] [PostgreSQL: Documentation: 15: 25.1. Routine Vacuuming](https://www.postgresql.org/docs/15/routine-vacuuming.html) (entailment p=0.99, evaluator: cross-encoder/nli-deberta-v3-small@enthr=0.75/spans=4-8)
  - cited: [src_090c5fef40044bcd] [PostgreSQL: Documentation: 15: 25.1. Routine Vacuuming](https://www.postgresql.org/docs/15/routine-vacuuming.html) (entailment p=0.88, evaluator: cross-encoder/nli-deberta-v3-small@enthr=0.75/spans=4-8)
- [clm_ee6745c7dbc24d22] VACUUM creates a substantial amount of I/O traffic, which can cause poor performance for other active sessions.
  - cited: [src_090c5fef40044bcd] [PostgreSQL: Documentation: 15: 25.1. Routine Vacuuming](https://www.postgresql.org/docs/15/routine-vacuuming.html) (entailment p=0.99, evaluator: cross-encoder/nli-deberta-v3-small@enthr=0.75/spans=4-8)
  - cited: [src_34abfc78075941eb] [PostgreSQL: Documentation: 14: VACUUM](https://www.postgresql.org/docs/14/sql-vacuum.html) (entailment p=1.00, evaluator: cross-encoder/nli-deberta-v3-small@enthr=0.75/spans=4-8)
- [clm_aa775546bbde4555] Also, while VACUUM ordinarily processes all partitions of specified partitioned tables, this option will cause VACUUM to skip all partitions if there is a conflicting lock on the partitioned table.
  - cited: [src_34abfc78075941eb] [PostgreSQL: Documentation: 14: VACUUM](https://www.postgresql.org/docs/14/sql-vacuum.html) (entailment p=0.99, evaluator: cross-encoder/nli-deberta-v3-small@enthr=0.75/spans=4-8)
- [clm_725c5dc3af7c4206] To vacuum a table, one must ordinarily be the table's owner or a superuser.
  - cited: [src_34abfc78075941eb] [PostgreSQL: Documentation: 14: VACUUM](https://www.postgresql.org/docs/14/sql-vacuum.html) (entailment p=0.97, evaluator: cross-encoder/nli-deberta-v3-small@enthr=0.75/spans=4-8)
- [clm_e34b1cfaf63a45e2] In normal PostgreSQL operation, tuples that are deleted or obsoleted by an update are not physically removed from their table; they remain present until a VACUUM is done.
  - cited: [src_34abfc78075941eb] [PostgreSQL: Documentation: 14: VACUUM](https://www.postgresql.org/docs/14/sql-vacuum.html) (entailment p=0.99, evaluator: cross-encoder/nli-deberta-v3-small@enthr=0.75/spans=4-8)
- [clm_87a6c7bf5a024f26] VACUUM can't reclaim a dead tuple if any active transaction might still need to see it.
  - cited: [src_80ed53289afd4215] [PostgreSQL Dead Tuples & Table Bloat: VACUUM, pg_repack, and Autovacuum Tuning | pginsights](https://www.pginsights.dev/guides/postgres-dead-tuples-and-bloat) (entailment p=0.99, evaluator: cross-encoder/nli-deberta-v3-small@enthr=0.75/spans=4-8)
- [clm_fd4ce7026fc5449b] The most common cause of dead tuples building up despite autovacuum running correctly is a long-running transaction holding the xmin horizon far back in the past.
  - cited: [src_80ed53289afd4215] [PostgreSQL Dead Tuples & Table Bloat: VACUUM, pg_repack, and Autovacuum Tuning | pginsights](https://www.pginsights.dev/guides/postgres-dead-tuples-and-bloat) (entailment p=0.97, evaluator: cross-encoder/nli-deberta-v3-small@enthr=0.75/spans=4-8)
- [clm_004915b047d04a34] On a 100M-row table, this means 20M dead tuples before vacuum runs.
  - cited: [src_80ed53289afd4215] [PostgreSQL Dead Tuples & Table Bloat: VACUUM, pg_repack, and Autovacuum Tuning | pginsights](https://www.pginsights.dev/guides/postgres-dead-tuples-and-bloat) (entailment p=0.96, evaluator: cross-encoder/nli-deberta-v3-small@enthr=0.75/spans=4-8)

## Uncertainty and Limitations
- No primary-research source was available; findings rest on secondary reporting and are limited accordingly.
- Uncertainty classification: UNCERTAIN

## Sources
1. [src_c628e01719bb4e02] [PostgreSQL: Documentation: 18: 13.2. Transaction Isolation](https://www.postgresql.org/docs/current/transaction-iso.html)
2. [src_090c5fef40044bcd] [PostgreSQL: Documentation: 15: 25.1. Routine Vacuuming](https://www.postgresql.org/docs/15/routine-vacuuming.html)
3. [src_34abfc78075941eb] [PostgreSQL: Documentation: 14: VACUUM](https://www.postgresql.org/docs/14/sql-vacuum.html)
4. [src_80ed53289afd4215] [PostgreSQL Dead Tuples & Table Bloat: VACUUM, pg_repack, and Autovacuum Tuning | pginsights](https://www.pginsights.dev/guides/postgres-dead-tuples-and-bloat)

---
status: COMPLETE | uncertainty: EVIDENCE_FOR | run: run_761796b8bc794bac
