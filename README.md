# lead-desk-graphql

A **GraphQL API** for a sales-agent lead pipeline: agents work an open pool of leads,
claim them, and move them through the pipeline; managers see everything. Python,
graphql-core, SQLite.

**This is a demo I built to show how I design a GraphQL service and its schema.**
All data is synthetic, and a request header stands in for real authentication.
It is not client or production code.

## What it covers

| Concern | How it is handled |
|---|---|
| API contract | Schema-first (`app/schema.graphql`): typed enums, a Relay-style connection for lists, documented fields. |
| Authorization | One function builds the visibility filter for every query. An agent sees the open pool and their own leads; someone else's lead looks exactly like a missing one. |
| Pagination | Opaque cursors, `first` capped at 100, `hasNextPage` found by fetching one extra row. |
| N+1 queries | Leads and their owners come from one JOIN. A test counts queries: a 45-lead page costs 3, not 47. |
| Concurrency | `claimLead` is one conditional UPDATE, so two agents claiming the same lead can never both win. A test races two agents over 30 leads. |
| State rules | Status only moves forward (NEW, CONTACTED, QUOTED, SOLD) or to LOST; the update is guarded on the old status. |
| Errors | Every error carries a machine-readable code: `BAD_INPUT`, `NOT_FOUND`, `FORBIDDEN`, `CONFLICT`, `INVALID_TRANSITION`, `UNAUTHENTICATED`. |
| Indexing | Composite index on `(agent_id, status, id)` because every list query filters by owner and status and pages by id. |

## Run it

```bash
pip install -r requirements.txt
pytest -q                 # 13 tests
python -m app.server      # http://127.0.0.1:8000/graphql
```

```bash
curl -s 127.0.0.1:8000/graphql -H 'X-Agent-Id: 1' \
  -d '{"query":"{ pipeline { status count } leads(first: 3) { totalCount edges { node { fullName status } } } }"}'
```

## Limits

- The `X-Agent-Id` header is a stand-in; a real service needs sessions or tokens.
- SQLite on one shared connection behind a lock; a real deployment would use PostgreSQL with a pool.
- No query depth or cost limits, no subscriptions, no audit trail of status changes.

Python 3.12, graphql-core 3.2.
