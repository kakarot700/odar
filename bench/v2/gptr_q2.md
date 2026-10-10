# PostgreSQL MVCC and Table Bloat: How Multi-Version Concurrency Control Provides Isolation and Why It Requires VACUUM

## 1. Introduction

PostgreSQL's storage engine rests on a deliberate architectural trade-off: rather than locking rows in place and forcing readers to wait for writers, the system keeps multiple versions of each row and lets each transaction observe a consistent snapshot of the data as of its start time. This design delivers strong isolation characteristics — readers never block writers and writers never block readers — but it introduces an unavoidable side effect. Old row versions accumulate as *dead tuples*, and unless the space is reclaimed asynchronously, tables and indexes grow while every subsequent query pays a performance penalty ([pgviz, 2026](https://pgviz.com/guides/vacuum-bloat/)).

This report examines two related questions. First, how PostgreSQL's MVCC implementation actually delivers transaction isolation at the row-version level. Second, why that very mechanism is the root cause of table bloat, and why VACUUM — in its standard and full forms — is not an optional maintenance chore but a structurally necessary part of the engine ([Dhandala, 2026](https://oneuptime.com/blog/post/2026-01-21-postgresql-table-bloat/view); [Urhoba, n.d.](https://www.urhoba.net/en/post/mvcc-vacuum-and-table-bloat)).

## 2. How MVCC Provides Transaction Isolation

### 2.1 Snapshot-Based Visibility

Under MVCC (multi-version concurrency control), PostgreSQL never updates a row in place. Every `UPDATE` writes a completely new row version and marks the old one dead, while every `DELETE` merely marks the existing row version as dead without physically removing it ([pgviz, 2026](https://pgviz.com/guides/vacuum-bloat/)). Because both operations produce metadata changes rather than destructive writes, readers never block writers and vice versa — each transaction simply sees a consistent snapshot of the data as of its own start time ([pgviz, 2026](https://pgviz.com/guides/vacuum-bloat/)).

The visibility rule follows directly from this snapshot model: old row versions are retained for as long as *some* transaction might still need to see them. A dead version remains a valid candidate for visibility until no open transaction's snapshot can reference it. Only then does it become garbage, and only VACUUM reclaims it — never inline at the time of the `UPDATE` or `DELETE` itself ([pgviz, 2026](https://pgviz.com/guides/vacuum-bloat/)).

A concrete experimental illustration of this mechanism appears in the Urhoba bloat guide. The author left a `REPEATABLE READ` transaction open in one connection and updated 10,000 rows from a second connection. When VACUUM was subsequently executed, its verbose output reported:

```
tuples: 0 removed, 110000 remain, 10000 are dead but not yet removable
```

The 10,000 dead rows were physically present in the heap but could not be reclaimed because the open transaction's snapshot could still see them ([Urhoba, n.d.](https://www.urhoba.net/en/post/mvcc-vacuum-and-table-bloat)). This is isolation working exactly as designed — and simultaneously the clearest demonstration that isolation and space reclamation are fundamentally at odds within the same architecture.

### 2.2 The Locking Consequences of Snapshot Visibility

Because old versions are retained rather than destroyed, PostgreSQL avoids locking reads and writes against each other. The practical consequence for DML is that `SELECT`, `INSERT`, `UPDATE`, and `DELETE` all continue to operate during a standard VACUUM run, because VACUUM acquires only a `SHARE UPDATE EXCLUSIVE` lock. Only DDL operations and other VACUUM executions must wait ([Urhoba, n.d.](https://www.urhoba.net/en/post/mvcc-vacuum-and-table-bloat)). Standard VACUUM also cannot be executed inside a transaction block (`BEGIN ... COMMIT`), which distinguishes it from `VACUUM FULL` and from most other maintenance commands ([Urhoba, n.d.](https://www.urhoba.net/en/post/mvcc-vacuum-and-table-bloat)).

### 2.3 Identifying the Blocking Snapshot

The transactional state that governs what VACUUM can remove is exposed through the backend snapshot xmin. The guide recommends the following query to identify sessions holding back cleanup:

```sql
SELECT pid, usename, application_name, state,
       now() - xact_start AS islem_suresi,
       age(backend_xmin) AS xmin_yasi,
       left(query, 60) AS son_sorgu
FROM pg_stat_activity
WHERE backend_xmin IS NOT NULL
```

Sessions with a `backend_xmin` are, by definition, snapshots that pin otherwise-dead tuples in place, and the `age(backend_xmin)` column quantifies how far back that snapshot extends ([Urhoba, n.d.](https://www.urhoba.net/en/post/mvcc-vacuum-and-table-bloat)). The same guide describes this pattern as "the most overlooked cause" of bloat — a single long transaction can halt tuple cleanup across the entire database ([Urhoba, n.d.](https://www.urhoba.net/en/post/mvcc-vacuum-and-table-bloat)).

## 3. Why MVCC Causes Table Bloat

### 3.1 The Write Amplification Inherent in the Model

The bloat mechanism is a direct consequence of the visibility rule. Every `UPDATE` in PostgreSQL is implemented as a delete followed by an insert at the physical level: a new row version is written and the prior version is marked dead ([pgviz, 2026](https://pgviz.com/guides/vacuum-bloat/)). The guide quantifies the operational impact bluntly: an UPDATE-heavy workload writes roughly as much as an INSERT-heavy one, *plus* the additional index churn caused by updating every affected index entry ([pgviz, 2026](https://pgviz.com/guides/vacuum-bloat/)). For a workload where row count is stable — the Urhoba guide cites an e-commerce order table whose status cycles through `beklemede`, `hazirlaniyor`, `kargoda`, and `teslim_edildi` — the table size on disk can double or triple within weeks even though the logical row count never changes ([Urhoba, n.d.](https://www.urhoba.net/en/post/mvcc-vacuum-and-table-bloat)).

### 3.2 The Asynchronous Reclamation Requirement

PostgreSQL does not reclaim dead-version space inline. Reclamation is deferred to VACUUM, which runs asynchronously ([pgviz, 2026](https://pgviz.com/guides/vacuum-bloat/)). As long as the loop of `UPDATE`/`DELETE` → autovacuum → tuple removal keeps pace with workload churn, operators rarely notice bloat at all. When it falls behind, "tables and indexes silently grow — that's bloat — and every query pays for it" ([pgviz, 2026](https://pgviz.com/guides/vacuum-bloat/)).

### 3.3 Four Identified Causes of Bloat Accumulation

The OneUptime guide enumerates the primary conditions under which dead tuples accumulate faster than they are removed:

| Cause | Mechanism | Typical Symptom |
|---|---|---|
| MVCC leaves dead tuples after `UPDATE`/`DELETE` | Row versions are marked dead, never removed in place | Steady growth in `n_dead_tup` |
| Autovacuum not keeping up | Vacuum cadence lags behind churn rate | `last_autovacuum` significantly older than `last_dml` |
| Long-running transactions delaying tuple cleanup | Open snapshots mark dead rows as still visible | `dead but not yet removable` in VACUUM verbose output |
| Autovacuum disabled | No background reclamation at all | Unbounded heap growth |

([Dhandala, 2026](https://oneuptime.com/blog/post/2026-01-21-postgresql-table-bloat/view); [Urhoba, n.d.](https://www.urhoba.net/en/post/mvcc-vacuum-and-table-bloat)).

### 3.4 The Escalation Path of Uncontrolled Bloat

When bloat becomes severe enough, the consequences extend beyond degraded query performance. Two failure modes are documented specifically: PostgreSQL refusing to accept commands to avoid wraparound data loss, and — at the extreme — the database returning *No space left on device*. On replicas, cleanup can also generate conflicts during recovery ([pgviz, 2026](https://pgviz.com/guides/vacuum-bloat/)). Wraparound is the most operationally severe of these, since it indicates that the tuple visibility window itself is at risk.

## 4. Detecting Table Bloat

### 4.1 Quick Detection via `pg_stat_user_tables`

The standard first-line metric is the dead-tuple ratio from the statistics collector. The OneUptime guide uses the following query, flagging any table exceeding 10,000 dead tuples:

```sql
SELECT schemaname || '.' || relname AS table,
       pg_size_pretty(pg_relation_size(relid)) AS size,
       n_dead_tup,
       n_live_tup,
       ROUND(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 2) AS dead_pct
FROM pg_stat_user_tables
WHERE n_dead_tup > 10000
ORDER BY n_dead_tup DESC;
```

A complementary monitoring view tracks vacuum recency directly, pairing `n_dead_tup` with `last_vacuum` and `last_autovacuum` to distinguish between "bloat exists" and "bloat is accumulating because vacuum is stale" ([Dhandala, 2026](https://oneuptime.com/blog/post/2026-01-21-postgresql-table-bloat/view)).

### 4.2 Precise Bloat Measurement via `pgstattuple`

For heap-level precision, the `pgstattuple` extension provides a tuple-level breakdown of a given relation:

```sql
CREATE EXTENSION IF NOT EXISTS pgstattuple;
SELECT * FROM pgstattuple('users');
```

The four key metrics returned are `dead_tuple_count` (absolute number of dead tuples), `dead_tuple_percent` (share of the heap that is dead), `free_space` (unused but physically present space), and `free_percent` (that space as a percentage of relation size) ([Dhandala, 2026](https://oneuptime.com/blog/post/2026-01-21-postgresql-table-bloat/view)). The distinction between `dead_tuple_percent` and `free_percent` is analytically important: dead tuples are marked-but-retained space that autovacuum can release via index-only cleanup, whereas free space represents page-level gaps that generally require a rewrite to reclaim.

### 4.3 Query-Level Diagnosis

When a specific query appears to be suffering from bloat-related slowdowns, the guide recommends running `EXPLAIN (ANALYZE, BUFFERS)` and inspecting the output for nodes performing excess reading, since bloat inflates the number of pages the executor must scan ([pgviz, 2026](https://pgviz.com/guides/vacuum-bloat/)).

## 5. Cleaning Up Bloat: VACUUM and Alternatives

### 5.1 Comparison of Cleanup Mechanisms

| Mechanism | Lock Level | Shrinks File on Disk? | Downtime | Prerequisite |
|---|---|---|---|---|
| Standard `VACUUM` | `SHARE UPDATE EXCLUSIVE` | No — space returned to free list for reuse | None | None; cannot run in a transaction block |
| `VACUUM FULL` | Exclusive | Yes — rewrites entire table | Yes (table locked) | None |
| `pg_repack` | Minimal (online) | Yes — rebuilds via logical copy | Minimal | Primary key or `UNIQUE NOT NULL` index |
| `CLUSTER` | Exclusive (reorders data) | Yes (reclaims space) | Yes | Target index |

([Urhoba, n.d.](https://www.urhoba.net/en/post/mvcc-vacuum-and-table-bloat); [Dhandala, 2026](https://oneuptime.com/blog/post/2026-01-21-postgresql-table-bloat/view)).

The critical operational distinction is stated explicitly: "bloat you already have won't go away on its own — plain VACUUM stops the growth; reclaiming disk needs VACUUM FULL or an online rebuild" ([pgviz, 2026](https://pgviz.com/guides/vacuum-bloat/)). Standard VACUUM marks dead tuples as available for reuse and returns space to PostgreSQL's internal free space for future inserts, but the physical file size is unchanged ([Dhandala, 2026](https://oneuptime.com/blog/post/2026-01-21-postgresql-table-bloat/view)).

### 5.2 Choosing Between `VACUUM FULL` and `pg_repack`

`VACUUM FULL` rewrites the table under an exclusive lock, making it unsuitable for systems requiring continuous availability ([Dhandala, 2026](https://oneuptime.com/blog/post/2026-01-21-postgresql-table-bloat/view)). `pg_repack` provides the same physical rebuild with minimal locking by creating a new version of the table, copying data, and swapping it in; the target table must have a `PRIMARY KEY` or a `UNIQUE NOT NULL` index, and the extension must be enabled in the target database via `CREATE EXTENSION pg_repack` before running `pg_repack -d myapp -t users` ([Dhandala, 2026](https://oneuptime.com/blog/post/2026-01-21-postgresql-table-bloat/view)). `CLUSTER` reorders table data physically according to a specified index but requires locking the table ([Dhandala, 2026](https://oneuptime.com/blog/post/2026-01-21-postgresql-table-bloat/view)).

## 6. Prevention and Autovacuum Tuning

### 6.1 Global Autovacuum Parameters

The default autovacuum configuration is conservative, which is adequate for many workloads but often insufficient for high-churn environments. The recommended tuning points for a PostgreSQL 16/18-era deployment are:

| Parameter | Recommended Value | Role |
|---|---|---|
| `autovacuum` | `on` | Enables background vacuum workers |
| `autovacuum_vacuum_scale_factor` | `0.1` | Fraction of table that must be dead before vacuum triggers |
| `autovacuum_analyze_scale_factor` | `0.05` | Fraction dead before ANALYZE triggers |
| `autovacuum_vacuum_cost_delay` | `2ms` | Sleep between vacuum cost units (throttling) |
| `autovacuum_vacuum_cost_limit` | `1000` | Work done per cost-delay interval |

([Dhandala, 2026](https://oneuptime.com/blog/post/2026-01-21-postgresql-table-bloat/view)).

A scale factor of `0.1` means a table with 100,000 rows can accumulate roughly 10,000 dead tuples before autovacuum triggers — which for a very small table means the trigger threshold is effectively governed by the accompanying absolute threshold. This is precisely why the guide's detection query filters on `n_dead_tup > 10000`: on large tables the default scale factor may allow dead tuples to reach a count that measurably degrades scan performance before vacuum begins ([Dhandala, 2026](https://oneuptime.com/blog/post/2026-01-21-postgresql-table-bloat/view)).

### 6.2 Per-Table Overrides

The single most important tuning principle is to tune per table rather than globally, because "a handful of hot tables usually cause most of the pain" ([pgviz, 2026](https://pgviz.com/guides/vacuum-bloat/)). PostgreSQL supports this directly:

```sql
ALTER TABLE high_churn_table SET (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_vacuum_threshold = 1000
);
```

With a scale factor of `0.02` and a threshold of 1,000, autovacuum on a 500,000-row table triggers at roughly `max(1000, 0.02 × 500000) = 10,000` dead tuples, versus `0.1 × 500000 = 50,000` under global defaults — a fivefold increase in vacuum responsiveness at the cost of additional vacuum I/O on that relation ([Dhandala, 2026](https://oneuptime.com/blog/post/2026-01-21-postgresql-table-bloat/view); [pgviz, 2026](https://pgviz.com/guides/vacuum-bloat/)).

### 6.3 Operational Practices

The prevention guidance converges on four practices: monitor dead tuple counts regularly; tune autovacuum to the actual workload rather than defaults; use `pg_repack` for online maintenance where exclusive locks are unacceptable; and — most critically given the blocking analysis above — avoid long-running transactions that delay tuple cleanup, scheduling `VACUUM FULL` work in maintenance windows when necessary ([Dhandala, 2026](https://oneuptime.com/blog/post/2026-01-21-postgresql-table-bloat/view)).

## 7. Assessment and Conclusion

### 7.1 A Concrete Engineering Position

Based on the evidence reviewed, the position taken in this report is as follows: **MVCC in PostgreSQL is not a flaw that produces bloat as a side effect; bloat is the arithmetic consequence of the visibility guarantees that make non-blocking isolation possible, and it is an expected cost that must be budgeted into any production workload.** The snapshot model retains row versions until no transaction can see them ([pgviz, 2026](https://pgviz.com/guides/vacuum-bloat/)), and since the removal of those versions is asynchronous, the only variables under operator control are *how quickly* reclamation happens and *how much disk is wasted* in the interim — not whether dead versions exist at all.

Two practical conclusions follow directly. First, bloat prevention is predominantly a *transaction hygiene* problem before it is a configuration problem. The Urhoba experiment demonstrates this decisively: a single open `REPEATABLE READ` session prevented cleanup of 10,000 dead tuples that had already been logically superseded ([Urhoba, n.d.](https://www.urhoba.net/en/post/mvcc-vacuum-and-table-bloat)). No amount of autovacuum tuning can override an active snapshot that still sees a row version as live, which means long-running reports, idle-in-transaction sessions, and unscoped `REPEATABLE READ` transactions should be treated as first-order capacity risks, not background hygiene issues. Second, autovacuum defaults are designed for average workloads, and high-churn relations — the kind where row count is constant but logical state changes frequently — require explicit per-table overrides, because the default `autovacuum_vacuum_scale_factor` of `0.1` can permit dead-tuple counts that degrade scans before vacuum ever fires ([Dhandala, 2026](https://oneuptime.com/blog/post/2026-01-21-postgresql-table-bloat/view); [pgviz, 2026](https://pgviz.com/guides/vacuum-bloat/)).

Finally, operators should internalize the asymmetry between *stopping* bloat and *reversing* it: standard VACUUM halts growth and returns space for reuse, but only `VACUUM FULL`, `pg_repack`, or `CLUSTER` shrink the file itself ([pgviz, 2026](https://pgviz.com/guides/vacuum-bloat/)). Treating the absence of file-size reduction after a routine VACUUM as evidence of failure is a common misreading; the engine is working as designed, and the physical space is simply awaiting reuse by future inserts.

### 7.2 Summary

MVCC delivers isolation by keeping multiple row versions alive simultaneously and letting each transaction read a snapshot; that same retention policy means every `UPDATE` and `DELETE` leaves garbage behind, and the engine's asynchronous VACUUM loop is the only mechanism that removes it. When that loop is delayed — by long transactions, disabled autovacuum, or undersized cost budgets — dead tuples accumulate, heap and index sizes inflate, and every query's scan cost rises proportionally. Detection through `pg_stat_user_tables` and `pgstattuple`, remediation through tiered VACUUM options, and prevention through per-table autovacuum tuning together constitute a complete operational response to a problem that is structural, predictable, and entirely manageable.

---

## References

Dhandala, N. (2026, January 21). *How to handle table bloat in PostgreSQL*. OneUptime. [https://oneuptime.com/blog/post/2026-01-21-postgresql-table-bloat/view](https://oneuptime.com/blog/post/2026-01-21-postgresql-table-bloat/view)

pgviz. (2026, July 5). *PostgreSQL VACUUM, autovacuum and table bloat explained | pgviz*. [https://pgviz.com/guides/vacuum-bloat/](https://pgviz.com/guides/vacuum-bloat/)

Urhoba. (n.d.). *PostgreSQL MVCC, VACUUM and table bloat guide*. [https://www.urhoba.net/en/post/mvcc-vacuum-and-table-bloat](https://www.urhoba.net/en/post/mvcc-vacuum-and-table-bloat)