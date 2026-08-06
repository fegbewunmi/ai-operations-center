#!/usr/bin/env python3
"""
Seed script for AI Operations Center demo data.

Inserts realistic deployments and documents (runbooks, postmortems) into Cloud SQL.
Documents are embedded using text-embedding-004 via the Gemini API.

Usage:
    GEMINI_API_KEY=<key> DB_PASSWORD=<pass> python3 scripts/seed_data.py
"""
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

import asyncpg
import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DB_DSN = f"postgresql://ai_ops_user:{DB_PASSWORD}@127.0.0.1:5433/ai_ops"
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768

# Incident onset for the demo scenario: 2026-08-06 06:00 UTC
INCIDENT_ONSET = datetime(2026, 8, 6, 6, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Demo documents: runbooks and postmortems
# ---------------------------------------------------------------------------
DOCUMENTS = [
    {
        "title": "Payment Service - High Error Rate Runbook",
        "document_type": "runbook",
        "service_name": "payments",
        "content": """# Payment Service High Error Rate Runbook

## Symptoms
- Error rate on payments service exceeds 10%
- HTTP 500 or 503 responses from /v1/payments endpoint
- Alerts: HighErrorRate, PaymentGatewayErrors

## Immediate Triage (first 5 minutes)
1. Check recent deployments: `gcloud run revisions list --service payments`
2. Check dependency health: fraud-detection and payment-gateway-client services
3. Review error logs: filter for `severity=ERROR service=payments`
4. Check database connection pool utilisation in Cloud Monitoring

## Common Root Causes

### 1. Failed Deployment (most common)
- Look for a deployment within 30 minutes of incident onset
- Rollback command: `gcloud run services update-traffic payments --to-revisions=<prev>=100`
- Estimated resolution: 5 minutes

### 2. Payment Gateway Timeout
- External payment gateway (Stripe) may be degraded
- Check status.stripe.com
- Circuit breaker should activate after 5 consecutive failures
- Fallback: enable offline mode via feature flag `PAYMENTS_OFFLINE_MODE=true`

### 3. Database Connection Pool Exhaustion
- Symptom: errors containing "too many clients"
- Check: Cloud SQL metrics > Active connections
- Fix: restart payments service to reset connection pool
- Long-term: tune `DB_POOL_SIZE` environment variable (current: 10, max recommended: 20)

### 4. Fraud Detection Service Down
- payments calls fraud-detection synchronously before processing
- If fraud-detection is unresponsive, all payment attempts fail
- Check fraud-detection service health and recent deployments
- Temporary bypass: set `SKIP_FRAUD_CHECK=true` (requires L3 approval)

## Escalation
- L2: #platform-payments Slack channel
- L3: Page on-call engineer via PagerDuty rotation "payments-oncall"
- Incident commander: Notify #incidents channel
""",
    },
    {
        "title": "Payment Service v2.2.0 Outage - Postmortem",
        "document_type": "postmortem",
        "service_name": "payments",
        "content": """# Postmortem: Payment Service Outage - 2026-03-15

## Summary
Payment service experienced 87% error rate for 22 minutes following deployment of v2.2.0.
Root cause: a dependency version bump introduced an incompatible change to the Stripe SDK
that caused all payment attempts to fail with a serialization error.

## Impact
- Duration: 22 minutes (05:43 - 06:05 UTC)
- Error rate: 87% (from baseline of 0.3%)
- Affected transactions: ~4,200 failed payment attempts
- Revenue impact: ~$340,000 in failed transactions

## Timeline
- 05:40 UTC: v2.2.0 deployment initiated by automated CD pipeline
- 05:43 UTC: Deployment completes, error rate begins climbing
- 05:47 UTC: Alert fires (HighErrorRate threshold: 5%)
- 05:52 UTC: On-call engineer paged
- 05:55 UTC: Root cause identified (Stripe SDK serialization error in logs)
- 06:01 UTC: Rollback to v2.1.9 initiated
- 06:05 UTC: Error rate returns to baseline

## Root Cause
The v2.2.0 release upgraded `stripe-python` from 5.4.0 to 6.0.0.
The v6.0.0 SDK changed the PaymentIntent.create() return type, which our code
assumed was a dict but is now a StripeObject. This caused a TypeError on every
payment attempt.

## Contributing Factors
- No integration test covering the Stripe SDK version boundary
- CD pipeline deploys immediately to production without a canary phase
- Alert threshold (5%) too conservative - outage was 87% before alert fired

## Action Items
1. Add integration test suite for payment gateway SDK upgrades (P1, due 2026-04-01)
2. Implement canary deployment (10% traffic for 10 minutes before full rollout) (P1)
3. Lower alert threshold to 2% for payment service specifically (P2)
4. Add pre-deployment smoke test against staging Stripe environment (P2)

## Lessons Learned
- Third-party SDK major version bumps require manual testing, not just unit tests
- The 4-minute gap between deployment and alert is too long for a P0 service
- Rollback was fast (4 minutes) once root cause was identified - rollback procedure is solid
""",
    },
    {
        "title": "Database Connection Pool Exhaustion - Runbook",
        "document_type": "runbook",
        "service_name": "payments",
        "content": """# Database Connection Pool Exhaustion Runbook

## Symptoms
- Errors containing: "remaining connection slots are reserved", "too many clients"
- Requests timing out with 504 errors
- Cloud SQL metric "Active connections" at or near limit

## Detection
```
-- Run on Cloud SQL to check connections
SELECT count(*), state, wait_event_type, wait_event
FROM pg_stat_activity
GROUP BY state, wait_event_type, wait_event
ORDER BY count DESC;
```

## Immediate Actions
1. Identify which service is holding the most connections (see query above)
2. Restart the offending service to force connection pool reset
3. If payments service: `gcloud run services update payments --update-env-vars DB_POOL_SIZE=5`

## Root Causes and Fixes

### Connection Leak
- Symptom: connections in "idle" state accumulating over time
- Fix: ensure all DB sessions use async context managers (`async with db:`)
- Check code for bare `db.execute()` calls without proper session lifecycle

### Traffic Spike
- Symptom: active connections spike correlated with request rate
- Fix: reduce `DB_POOL_SIZE` per instance, scale horizontally instead
- Current limits: Cloud SQL max_connections=100, pool_size=10 per instance

### Long-running Transactions
- Symptom: connections in "idle in transaction" state
- Fix: add statement_timeout to DB connection: `SET statement_timeout = '30s'`
- Check for missing `await db.commit()` or `await db.rollback()` in error handlers

## Prevention
- Set `pool_pre_ping=True` in SQLAlchemy to detect stale connections
- Configure `pool_recycle=300` to recycle connections every 5 minutes
- Monitor "Active connections" metric with alert at 80% of max_connections
""",
    },
    {
        "title": "Payments Service - Architecture Overview",
        "document_type": "architecture_doc",
        "service_name": "payments",
        "content": """# Payments Service Architecture

## Overview
The payments service handles all payment processing for Orion Commerce.
It is a critical P0 service with a 99.95% uptime SLO.

## Dependencies (Synchronous - failure cascades)
- **fraud-detection**: Every payment is checked before processing. If fraud-detection
  is down, payments fail-closed (all payments rejected). SLA: p99 < 200ms.
- **payment-gateway-client**: Wraps the Stripe API. Circuit breaker: 5 failures in
  30 seconds triggers open state (10-second cooldown). If open, returns 503.
- **Cloud SQL (ai-ops-db)**: Primary datastore. Connection pool: 10 connections per
  instance. Read replica used for reporting queries.

## Dependencies (Asynchronous - failure is non-blocking)
- **notifications**: Sends payment confirmation emails/SMS. Failure here does not
  affect payment success. Uses Cloud Pub/Sub for decoupling.

## Callers (Services that call payments)
- **orders**: Calls `/v1/payments/charge` during order checkout. Highest traffic caller.
- **api-gateway**: Routes external payment API requests.

## Key Configuration
- `STRIPE_API_KEY`: Loaded from Secret Manager
- `DB_POOL_SIZE`: Default 10. Max recommended 20 (Cloud SQL limit: 100 total connections)
- `FRAUD_CHECK_TIMEOUT_MS`: Default 500ms. If exceeded, request fails.
- `CIRCUIT_BREAKER_THRESHOLD`: Default 5 failures before opening
- `MAX_RETRY_ATTEMPTS`: Default 3 (applies to Stripe API calls only)

## Deployment
- Runtime: Cloud Run (us-central1), min-instances: 2, max-instances: 20
- Container: python:3.11-slim, 512MB RAM, 1 vCPU
- Rollback: Traffic splitting via `gcloud run services update-traffic`

## SLOs
- Availability: 99.95% (22 minutes downtime budget per month)
- Latency p99: < 2000ms
- Error rate: < 0.5% (non-4xx errors)
""",
    },
    {
        "title": "Fraud Detection Service Degradation - Error Pattern",
        "document_type": "error_pattern",
        "service_name": "payments",
        "content": """# Error Pattern: Fraud Detection Dependency Failure

## Pattern Signature
- payments error rate spikes suddenly (>10%)
- All errors are 503 Service Unavailable
- Error message contains: "fraud check failed" or "fraud-detection timeout"
- fraud-detection service shows elevated latency or error rate simultaneously

## Why This Happens
payments calls fraud-detection synchronously with a 500ms timeout.
If fraud-detection is slow or down, every payment attempt times out or fails.
The circuit breaker opens after 5 consecutive failures (typically within 10-15 seconds),
at which point 100% of payment attempts fail until the breaker resets.

## Quick Verification
1. Check fraud-detection health: GET /health on fraud-detection service
2. Look for correlated alerts on fraud-detection
3. Check service dependency graph - fraud-detection is upstream blocker

## Resolution Steps
1. If fraud-detection is down: page the ML platform team (#ml-platform)
2. If fraud-detection is slow: check its dependencies (model serving, feature store)
3. Emergency bypass (L3 approval required):
   - Set `SKIP_FRAUD_CHECK=true` on payments service
   - This allows payments to proceed without fraud checking
   - Notify fraud team to run retrospective fraud analysis on bypass period
4. Once fraud-detection recovers, circuit breaker auto-resets after 10 seconds

## Historical Occurrences
- 2026-01-20: ML model redeployment caused 8-minute outage on fraud-detection
- 2025-11-03: Feature store latency spike caused fraud-detection p99 > 2s for 15 minutes
- 2025-08-14: Cloud Run cold start surge caused timeout cascade

## Prevention
- fraud-detection has a dedicated warm instance (min-instances: 3) to avoid cold starts
- Ongoing work to make fraud check asynchronous (post-payment, with reversal on fraud detection)
""",
    },
    # ── Orders service documents (latency regression family) ──────────────────
    {
        "title": "Orders Service - High Latency Runbook",
        "document_type": "runbook",
        "service_name": "orders",
        "content": """# Orders Service High Latency Runbook

## Symptoms
- Orders p99 latency exceeds 2000ms (baseline: 200ms)
- Downstream timeouts in API Gateway or upstream callers
- CPU normal, no exception errors — pure latency

## Immediate Triage (first 5 minutes)
1. Check Payments service latency — Orders calls Payments synchronously on every checkout
2. Check Cloud SQL query latency for the orders database
3. Check for recent deployments: `gcloud run revisions list --service orders`
4. Verify Inventory and Notifications queue depth (async — not a blocker)

## Common Root Causes

### 1. Slow Database Query (most common for gradual degradation)
- Check Cloud Monitoring: Cloud SQL > query latency by statement
- Look for queries without index using Cloud SQL insights
- Common culprit: table scan on large orders table after a schema change
- Fix: `EXPLAIN ANALYZE` the slow query, add index or revert migration
- Command: `gcloud sql operations list --instance ai-ops-db --filter="status=RUNNING"`

### 2. Payments Dependency Timeout
- Orders calls Payments synchronously; if Payments slows down, Orders times out
- Check Payments p99 latency in Cloud Monitoring
- If Payments is slow: investigate Payments service separately
- Orders circuit breaker: opens after 10 consecutive timeouts (30s cooldown)

### 3. N+1 Query Pattern After Deployment
- A code change introduced a loop calling DB once per order item
- Symptom: latency proportional to order size, not request count
- Fix: rollback the deployment that introduced the N+1 pattern
- Check: query count per request in Cloud SQL insights

## Escalation
- L2: #commerce-oncall Slack channel
- L3: Page on-call engineer via PagerDuty rotation "commerce-rotation"
""",
    },
    {
        "title": "Orders Service - Database Slow Query Postmortem",
        "document_type": "postmortem",
        "service_name": "orders",
        "content": """# Postmortem: Orders Latency Spike - 2026-05-22

## Summary
Orders service p99 latency climbed from 180ms to 3400ms over 40 minutes following
a schema migration that removed an index on the orders.customer_id column.
No error rate increase — only latency. Downstream services (API Gateway) began
timing out 25 minutes into the incident.

## Impact
- Duration: 67 minutes (11:00 - 12:07 UTC)
- Latency p99: 3400ms (from baseline 180ms)
- Error rate: 0% initially, then 8% as timeouts began cascading
- Affected users: ~12,000 requests timed out or saw >3s response times

## Timeline
- 10:55 UTC: Migration 0021 applied to Cloud SQL (removes unused index)
- 11:00 UTC: p99 latency begins climbing (gradual — no step change)
- 11:22 UTC: Latency alert fires (threshold: p99 > 1500ms for 5 minutes)
- 11:25 UTC: On-call engineer begins investigation
- 11:38 UTC: Root cause identified (slow table scan on orders.customer_id)
- 11:44 UTC: Hotfix migration 0022 applied (adds index back)
- 12:07 UTC: Latency returns to baseline

## Root Cause
Migration 0021 dropped an index on orders.customer_id marked as "unused"
by an automated analysis. In production the column is used by a join query
in the checkout flow — the automated analysis was run against a staging replica
with 1/100th the data volume, where the planner chose a different execution plan.

## What was misleading
- No exceptions in logs — pure latency degradation
- CPU normal throughout — not a resource exhaustion issue
- Alert was delayed 22 minutes (gradual degradation)
- No deployment near the incident window — it was a migration (separate CI job)

## Action Items
1. Treat Cloud SQL migrations as deployments in the deployment tracking system (P1)
2. Never automatically drop indexes — require manual approval (P1)
3. Add index-dependent query latency to pre-migration canary checks (P2)
4. Lower p99 alert threshold to 800ms for the orders service (P2)

## Lessons Learned
- Gradual latency degradation is harder to catch than step-change error rate spikes
- Schema migrations are deployments; they belong in deployment history
- "Unused" by the query planner in staging ≠ unused in production at scale
""",
    },
    {
        "title": "Orders Service - Architecture Overview",
        "document_type": "architecture_doc",
        "service_name": "orders",
        "content": """# Orders Service Architecture

## Overview
The orders service manages the full order lifecycle for Orion Commerce:
creation, payment processing coordination, inventory reservation, and notifications.
It is a Commerce Team P0 service with a 99.9% uptime SLO.

## Synchronous Dependencies (failure cascades to orders)
- **payments**: Called on every checkout. If Payments is slow or down, orders fail.
  Timeout: 5s. Circuit breaker: 10 failures in 60s. SLA: p99 < 500ms.
- **Cloud SQL (ai-ops-db)**: Primary orders datastore. Key tables:
  orders, order_items, customers. Read replica used for reporting.
  Connection pool: 15 connections per instance.

## Asynchronous Dependencies (non-blocking)
- **inventory**: Stock reservation via Cloud Pub/Sub. Orders succeed even if
  inventory is slow — eventual consistency model. Max retry: 3 times.
- **notifications**: Order confirmation via Cloud Pub/Sub. Failure does not
  affect order success. Dead-letter queue for failures.

## Critical Queries
- Order creation: INSERT into orders + order_items (uses transaction)
- Checkout flow: SELECT with JOIN on orders.customer_id + orders.status
- Order history: paginated SELECT with ORDER BY created_at DESC

## Indexes (critical — do not remove without load testing)
- orders.customer_id — used in checkout join queries
- orders.status — used in order history pagination
- orders.created_at — used in reporting and timeline queries
- order_items.order_id — used in item retrieval join

## Deployment
- Runtime: Cloud Run (us-central1), min-instances: 3, max-instances: 30
- Container: python:3.11-slim, 1GB RAM, 2 vCPU
- Rollback: Traffic splitting via `gcloud run services update-traffic`
- DB migrations: Alembic, applied separately from service deployments

## SLOs
- Availability: 99.9% (43 minutes downtime budget per month)
- Latency p99: < 500ms
- Error rate: < 0.5%
""",
    },

    # ── Inventory service documents (resource leak / exhaustion family) ────────
    {
        "title": "Inventory Service - Connection Leak Runbook",
        "document_type": "runbook",
        "service_name": "inventory",
        "content": """# Inventory Service - Resource Leak Runbook

## Symptoms
- Service is initially healthy; failures begin under sustained traffic hours later
- Cloud SQL "Active connections" metric growing over time (not correlated with request spikes)
- Eventual errors: "remaining connection slots are reserved" or 503s
- Logs may show misleading timeout errors before the underlying leak is apparent
- Gradual memory growth correlated with connection count

## Why It Is Hard to Detect
Resource leaks have time-delayed causality. The failure mode (connection exhaustion)
begins hours after the root cause (unclosed DB session in code). Short-window telemetry
often looks normal; the metric to watch is the TREND of connection count, not its value.

## Immediate Triage
1. Check Cloud Monitoring: Cloud SQL > Active connections (trend over last 2-4 hours)
2. Check if connection count is correlated with request rate or growing independently
3. Restart inventory service to force connection pool reset (L2 action):
   `gcloud run services update inventory --update-env-vars RESTART=1`
4. After restart: confirm connection count drops and service recovers

## Finding the Leak
```sql
-- Run on Cloud SQL to identify leaked connections
SELECT application_name, state, count(*)
FROM pg_stat_activity
WHERE datname = 'ai_ops'
GROUP BY application_name, state
ORDER BY count DESC;
```
- Idle connections that accumulate: likely missing `db.close()` or improper context manager usage
- "Idle in transaction" connections: missing `db.commit()` or `db.rollback()` in error handlers

## Common Root Causes in Inventory Service
### 1. Pub/Sub Consumer Missing Session Cleanup
- The Pub/Sub consumer creates a DB session on each message
- If processing raises an exception, the session context manager is bypassed
- Fix: ensure all message handlers use `async with AsyncSessionLocal() as db:`
- Never use bare `db = AsyncSessionLocal(); await db.execute(...)`

### 2. Background Reconciliation Job Leak
- Inventory runs hourly reconciliation as a background task
- If the task crashes mid-execution, it may leave sessions open
- Check: `ps aux` for orphaned reconciliation processes
- Fix: add explicit session cleanup to the reconciliation job error handler

## Long-Term Prevention
- Set `pool_pre_ping=True` in SQLAlchemy engine to detect stale connections
- Configure `pool_recycle=300` to close connections idle > 5 minutes
- Add Cloud Monitoring alert: "Active connections > 70% of max for 10 minutes"

## Escalation
- L2: #commerce-oncall (restart service)
- L3: Disable Pub/Sub consumer, enable maintenance mode (requires approval)
""",
    },
    {
        "title": "Inventory Service - Connection Exhaustion Postmortem",
        "document_type": "postmortem",
        "service_name": "inventory",
        "content": """# Postmortem: Inventory Service Failures - 2026-06-10

## Summary
Inventory service began failing 4 hours and 20 minutes after a code change
introduced a database session leak in the Pub/Sub message consumer.
The failure appeared as "too many clients" errors from Cloud SQL. A restart
resolved the immediate issue; a hotfix closed the session leak.

## Impact
- Gradual degradation over 4.5 hours, then hard failure
- 23% of stock reservation requests failed at peak
- No order loss (orders use async inventory — eventual consistency)
- Duration of hard failure: 18 minutes until service restarted

## Timeline
- 08:00 UTC: Deployment of v1.4.2 (new Pub/Sub consumer batch processing)
- 08:00–12:15 UTC: Connection count growing from 12 → 96 (max: 100)
- 12:15 UTC: First "too many clients" errors appear
- 12:22 UTC: Alert fires (error rate threshold)
- 12:29 UTC: On-call investigates — sees error messages about payload size (misleading)
- 12:35 UTC: Root cause identified (connection count trend in Cloud Monitoring)
- 12:38 UTC: Service restarted, connections drop to 8
- 13:10 UTC: Hotfix deployed closing DB session in batch consumer error handler

## What Was Misleading
- Initial error messages mentioned "payload too large" — a side effect, not the cause
- The failure looked like a sudden spike but was a 4-hour gradual build
- CPU and memory were normal until connections were exhausted
- No deployment visible in the 30-minute window before failure — it was 4 hours earlier

## Root Cause
The v1.4.2 batch consumer created a DB session outside the message-processing
context manager. When batch processing raised a validation error, the exception
handler ran but the DB session was never closed:

```python
# BEFORE (buggy)
db = AsyncSessionLocal()
for msg in batch:
    try:
        await process(db, msg)
    except ValidationError:
        logger.error("Skipping invalid message")
        # db session never closed here

# AFTER (fixed)
async with AsyncSessionLocal() as db:
    for msg in batch:
        try:
            await process(db, msg)
        except ValidationError:
            logger.error("Skipping invalid message")
            await db.rollback()  # explicit rollback, session closed by context manager
```

## Action Items
1. Add linting rule to block bare AsyncSessionLocal() without context manager (P1)
2. Add Cloud Monitoring alert: connections > 80% of max (P1)
3. Add connection count to pre-deployment health checks (P2)
4. Write unit test that verifies session is closed on exception in batch consumer (P1)

## Lessons Learned
- Resource leaks have time-delayed causality; look at metric TRENDS not current values
- Error messages at failure time often describe symptoms, not root cause
- The "suspicious deployment" heuristic needs a longer window (2+ hours) for leak patterns
""",
    },
    {
        "title": "Inventory Service - Architecture Overview",
        "document_type": "architecture_doc",
        "service_name": "inventory",
        "content": """# Inventory Service Architecture

## Overview
The inventory service tracks stock levels for Orion Commerce with eventual consistency.
It processes stock reservations asynchronously via Cloud Pub/Sub.
Commerce Team P1 service with a 99.5% uptime SLO.

## Data Flow
- Orders → Cloud Pub/Sub (stock reservation request) → Inventory (consumer)
- Inventory → Cloud Pub/Sub (stock confirmed/rejected) → Orders (consumer)

## Components
- **REST API**: Read-only stock queries from internal services
- **Pub/Sub Consumer**: Processes stock reservation messages from Orders
- **Reconciliation Job**: Hourly Cloud Scheduler job that reconciles DB with warehouse feed
- **Cloud SQL**: inventory_items, reservations, stock_movements tables

## Connection Pool Configuration
- pool_size: 10 per Cloud Run instance
- max_connections Cloud SQL: 100 (shared across all services)
- Inventory allocation: ~20 connections maximum (2 instances × 10 pool_size)
- pool_pre_ping: enabled
- pool_recycle: 300 seconds

## Known Risk: Pub/Sub Consumer Sessions
The Pub/Sub consumer creates one DB session per message batch. If exception handling
is not careful, sessions can leak. Always use:
```python
async with AsyncSessionLocal() as db:
    # process message
    await db.commit()
```

## Resource Limits
- Cloud Run min-instances: 2, max-instances: 10
- Memory: 512MB per instance
- CPU: 1 vCPU per instance

## SLOs
- Availability: 99.5%
- Stock reservation latency (end-to-end): < 5 seconds (async)
- Reconciliation freshness: < 70 minutes
""",
    },
]


# ---------------------------------------------------------------------------
# Deployments: one suspicious per service, others benign
# ---------------------------------------------------------------------------
def make_deployments(payments_id: str, orders_id: str, inventory_id: str) -> list[dict]:
    return [
        # ── Payments: suspicious deployment 15 min before onset ──────────────
        {
            "service_id": payments_id,
            "version_from": "v2.3.0",
            "version_to": "v2.3.1",
            "deployed_at": INCIDENT_ONSET - timedelta(minutes=15),
            "deployed_by": "ci-cd-pipeline@orion-commerce.iam.gserviceaccount.com",
            "status": "success",
            "config_changes": [
                "Updated DB_POOL_SIZE from 10 to 15",
                "Upgraded stripe-python from 7.1.0 to 7.2.0",
                "Added connection retry logic with exponential backoff",
            ],
            "rollback_available": True,
            "rollback_target_version": "v2.3.0",
            "git_commit_sha": "a3f8c2d1e4b5f6a7b8c9d0e1f2a3b4c5d6e7f8a9",
        },
        {
            "service_id": payments_id,
            "version_from": "v2.2.9",
            "version_to": "v2.3.0",
            "deployed_at": INCIDENT_ONSET - timedelta(days=3),
            "deployed_by": "alice@orion-commerce.com",
            "status": "success",
            "config_changes": ["Added metrics endpoint /metrics for Prometheus scraping"],
            "rollback_available": True,
            "rollback_target_version": "v2.2.9",
            "git_commit_sha": "b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0",
        },
        {
            "service_id": payments_id,
            "version_from": "v2.2.8",
            "version_to": "v2.2.9",
            "deployed_at": INCIDENT_ONSET - timedelta(days=10),
            "deployed_by": "bob@orion-commerce.com",
            "status": "success",
            "config_changes": ["Bumped pydantic from 2.8.0 to 2.9.0"],
            "rollback_available": False,
            "rollback_target_version": None,
            "git_commit_sha": "c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1",
        },

        # ── Orders: benign deployment history (NOT near incident onset) ───────
        # Used by INC-LR-001 (latency regression) — deployment is NOT the cause
        {
            "service_id": orders_id,
            "version_from": "v3.1.4",
            "version_to": "v3.1.5",
            "deployed_at": INCIDENT_ONSET - timedelta(days=2),
            "deployed_by": "carol@orion-commerce.com",
            "status": "success",
            "config_changes": ["Updated logging format for structured JSON"],
            "rollback_available": True,
            "rollback_target_version": "v3.1.4",
            "git_commit_sha": "d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2",
        },
        {
            "service_id": orders_id,
            "version_from": "v3.1.3",
            "version_to": "v3.1.4",
            "deployed_at": INCIDENT_ONSET - timedelta(days=8),
            "deployed_by": "dave@orion-commerce.com",
            "status": "success",
            "config_changes": ["Improved pagination for order history endpoint"],
            "rollback_available": True,
            "rollback_target_version": "v3.1.3",
            "git_commit_sha": "e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3",
        },

        # ── Inventory: deployment 4+ hours before onset (leak scenario) ───────
        # Used by INC-RL-001 (resource leak) — the leak was introduced here
        {
            "service_id": inventory_id,
            "version_from": "v1.4.1",
            "version_to": "v1.4.2",
            "deployed_at": INCIDENT_ONSET - timedelta(hours=4, minutes=20),
            "deployed_by": "ci-cd-pipeline@orion-commerce.iam.gserviceaccount.com",
            "status": "success",
            "config_changes": [
                "New Pub/Sub consumer: batch processing mode (processes 10 msgs at once)",
                "Reduced DB query timeout from 30s to 10s",
            ],
            "rollback_available": True,
            "rollback_target_version": "v1.4.1",
            "git_commit_sha": "f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4",
        },
        {
            "service_id": inventory_id,
            "version_from": "v1.4.0",
            "version_to": "v1.4.1",
            "deployed_at": INCIDENT_ONSET - timedelta(days=5),
            "deployed_by": "emma@orion-commerce.com",
            "status": "success",
            "config_changes": ["Added stock level caching (Redis TTL: 60s)"],
            "rollback_available": True,
            "rollback_target_version": "v1.4.0",
            "git_commit_sha": "a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5",
        },
    ]


# ---------------------------------------------------------------------------
# Embedding - direct REST call with outputDimensionality to match vector(768)
# ---------------------------------------------------------------------------
async def embed_text(api_key: str, text: str) -> list[float]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{EMBEDDING_MODEL}:embedContent"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            url,
            params={"key": api_key},
            json={
                "model": f"models/{EMBEDDING_MODEL}",
                "content": {"parts": [{"text": text}]},
                "outputDimensionality": EMBEDDING_DIMENSIONS,
            },
        )
        resp.raise_for_status()
        return resp.json()["embedding"]["values"]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main() -> None:
    if not DB_PASSWORD:
        print("ERROR: set DB_PASSWORD env var")
        sys.exit(1)
    if not GEMINI_API_KEY:
        print("ERROR: set GEMINI_API_KEY env var")
        sys.exit(1)

    print("Connecting to Cloud SQL via proxy...")
    conn = await asyncpg.connect(DB_DSN)

    print("Fetching service IDs...")
    service_ids: dict[str, str] = {}
    for svc_name in ("payments", "orders", "inventory"):
        row = await conn.fetchrow("SELECT service_id FROM services WHERE name = $1", svc_name)
        if not row:
            print(f"ERROR: '{svc_name}' service not found. Run migrations first.")
            sys.exit(1)
        service_ids[svc_name] = str(row["service_id"])
        print(f"  {svc_name}: {service_ids[svc_name]}")

    payments_id  = service_ids["payments"]
    orders_id    = service_ids["orders"]
    inventory_id = service_ids["inventory"]

    # --- Deployments ---
    print("\nInserting deployments...")
    for dep in make_deployments(payments_id, orders_id, inventory_id):
        await conn.execute("""
            INSERT INTO deployments
              (service_id, version_from, version_to, deployed_at, deployed_by,
               status, config_changes, rollback_available, rollback_target_version, git_commit_sha)
            VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10)
            ON CONFLICT DO NOTHING
        """,
            dep["service_id"],
            dep["version_from"],
            dep["version_to"],
            dep["deployed_at"],
            dep["deployed_by"],
            dep["status"],
            str(dep["config_changes"]).replace("'", '"'),
            dep["rollback_available"],
            dep["rollback_target_version"],
            dep["git_commit_sha"],
        )
        print(f"  {dep['version_to']} deployed at {dep['deployed_at'].strftime('%H:%M UTC')} - OK")

    # --- Documents with embeddings ---
    print("\nGenerating embeddings and inserting documents...")
    existing = await conn.fetchval("SELECT COUNT(*) FROM documents")
    if existing > 0:
        print(f"  {existing} documents already exist - skipping")
    else:
        for doc in DOCUMENTS:
            print(f"  Embedding: {doc['title'][:60]}...")
            embedding = await embed_text(GEMINI_API_KEY, doc["content"])
            embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"
            await conn.execute("""
                INSERT INTO documents (title, document_type, service_name, content, embedding)
                VALUES ($1, $2, $3, $4, $5::vector)
            """,
                doc["title"],
                doc["document_type"],
                doc["service_name"],
                doc["content"],
                embedding_str,
            )
            print(f"    -> inserted ({len(embedding)}d embedding)")

    await conn.close()
    print("\nDone! Seed data inserted successfully.")
    print(f"\nTest with service_name: 'payments' (not 'payment-service')")
    print(f"The suspicious deployment is 15 minutes before incident onset - the Planner should find it.")


if __name__ == "__main__":
    asyncio.run(main())
