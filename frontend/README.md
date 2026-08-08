# Frontend — Dev 1

Compliance / risk dashboards and the Human Review Queue UI. Vite + React 19 + TypeScript
(strict), TanStack Query for fetching, React Router for routing. No component library —
plain CSS in `src/styles.css`.

## Run

The backend has to be up first; the dev server proxies `/api/*` to `localhost:8000`.

```bash
# terminal 1 — backend, with data
cd backend
docker compose up -d db
alembic upgrade head
python -m app.pipeline --persist                                   # DOC-001
python -m app.pipeline --chunks ../fixtures/mock/chunks.doc-003.json --persist
uvicorn app.api:app --reload

# terminal 2 — frontend
cd frontend
npm install
npm run dev          # http://localhost:5173
```

```bash
npm run test         # vitest
npm run typecheck    # tsc --noEmit
npm run build        # typecheck + production build
```

## The one thing this UI exists to do

Two escalation sources feed one queue: ambiguous KG edges from Clause NER, and
exhausted-budget / low-confidence redlines from the Redline Generator. Both write the
same record, so `TraceTimeline` renders both with no branching on `source` — only the
column heading changes ("Strategy tried" vs "Query issued").

The queue list surfaces `rounds_attempted` before a reviewer opens anything, because
*"nobody has looked at this yet"* and *"the agent already tried three strategies and
failed"* are completely different situations and a queue that can't tell them apart is
just a pile of work.

## Resolving writes to two places

`POST /review-queue/{id}/resolve` updates the escalation **and** the candidate KG edge —
status plus `resolved_by: "human"`. That second write is the point: `GET /kg/edges`
defaults to `confirmed`, so until the edge itself flips, a reviewer's decision is
invisible to the Risk Engine. `resolved_by` keeps human-confirmed facts permanently
distinguishable from AI-generated ones (ARCHITECTURE.md §4.2).

## Contract

`src/api/types.ts` mirrors `backend/app/schemas.py` by hand. The status enum is exactly
`pending_review` · `confirmed` · `rejected` — a `pending` typo here errors nowhere and
silently shows fewer items. Change both files in the same PR.

## Where the data comes from

Every panel is a real read; nothing is stubbed. `dashboard_stubs.py` no longer exists.

| Client function | Backing module |
| --- | --- |
| `listReviewQueue`, `getEscalation`, `resolveEscalation` | `app/review_queue.py` |
| `listFacts`, `listEdges` | `app/api.py` -> `app/kg/store.py` |
| `listRiskFlags` | `app/risk/routes.py` |
| `listRedlines`, `getRedline` | `app/redline/routes.py` |
| `listDocuments`, `getDocumentStatus`, `processDocument` | `app/documents.py` |
| `uploadDocument` | `ingestion/api.py` |
| `search` | `app/search/routes.py` |
| `getSummary` | `app/summary/routes.py` |
| `compareDocuments`, `getRiskPreview` | `app/compare/routes.py` |
| `listNegotiationLadders` | `app/negotiation/routes.py` |
| `listRegulationsForRisk` | `app/regulations/routes.py` |

Resolving an escalation is attributed to the authenticated caller, not to a field in the
request body. `ResolveRequest` therefore has no `reviewer_id`.
