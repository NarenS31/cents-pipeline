# Distributed crawling (design, not implemented)

Status: only `LocalQueue` exists (`src/cents/queue/base.py`). This document describes how Redis + Celery workers
would slot in behind the same `TaskQueue` interface.

## Invariant
Politeness is enforced **per host**, so every request to a given host must pass through one limiter.
Therefore work is partitioned **by host**, never by district: `partition_by_host()` already groups districts that
share a website host into one batch.

## Shape
```
cents audit --queue redis://...  ->  partition_by_host(districts, N)
        |                                   |
        v                                   v
   Redis (broker)  <-- one Celery task per batch --> workers
        |                                   |
        |            each worker: AsyncHostRateLimiter + PoliteFetcher (same code as local)
        v
   results backend: validated CurriculumAuditResult rows (public) / FOIAContact rows (private store)
```
- **Routing:** one Celery queue per batch id, or a consistent hash of host to worker, so a host is never split.
- **Idempotency:** task key = `(run_id, district_id)`; retries are safe because results are overwritten, not appended.
- **Rate limit across restarts:** a worker owns its hosts for the whole run. If shared limiting is ever needed, replace the
  in-memory bucket with a Redis token bucket keyed by host (Lua script), keeping the 1 request/second ceiling.
- **Privacy:** contacts go to a separate private store (mode/ACL equivalent to `--contacts-out`); only public rows are exported.
- **Failure:** a worker crash re-queues its batch; robots.txt is re-fetched (cache is per process).

Not built because a full state is ~100 districts, which the single-process async crawler finishes in minutes.
