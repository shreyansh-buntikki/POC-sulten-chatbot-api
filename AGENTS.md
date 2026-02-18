# AI Agent Rules

## Core Architecture Principles
- Enforce clear separation of concerns:
  **API layer → business logic → validation/helpers → data access**
- API routes/controllers must be:
  - Thin
  - Async where applicable
  - Free of business logic and direct database access
- Business logic belongs in services/use-cases
- Data access layers must not contain business rules

## API Design
- All endpoints must return a consistent response envelope:
  - success
  - message
  - data
- Prefer explicit, typed response models
- Raise domain-specific exceptions (e.g. validation, not-found, permission)
- Centralize exception handling; never swallow errors silently

## Validation
- Use a schema-first approach (e.g. Pydantic, Zod, etc.)
- Full type hints required
- No raw dictionaries crossing validation boundaries
- Validation must occur at system edges (API, job input, external data)

## Logging & Error Handling
- Log lifecycle events at `info`
- Log recoverable issues at `warning`
- Log failures at `error` with stack traces
- Never catch exceptions without re-raising or converting them to domain errors
- Debug logs should be verbose but removable without logic changes

## Data Access & Performance
- Avoid N+1 queries at all costs
- Prefer:
  1. Batch fetch
  2. In-memory processing
  3. Bulk write operations
- Use in-memory maps for O(1) lookups
- Sort results deterministically
- Paginate large datasets
- Stream large files instead of loading into memory

## Background & Long-Running Work
- Offload heavy computation, I/O, or large datasets to background jobs
- Jobs must:
  - Be trackable via an ID
  - Expose clear status transitions (e.g. queued → running → finished/failed)
- APIs should return immediately with a job reference
- Clients poll or subscribe for job results

## Code Quality Standards
- Target modern language versions (e.g. Python 3.11+)
- Follow standard style guides (PEP 8, etc.)
- No bare `except`
- No mixed return types
- Keep cognitive complexity low (<15)
- Prefer multiple small functions over monoliths
- No nested functions; extract helpers
- Imports ordered: standard → third-party → internal

## Scope & Delivery Discipline
- Touch only files required for the task
- Do not introduce new dependencies unless explicitly requested
- No surprise refactors
- Tests and documentation only when requested
- Partial, correct solutions beat over-engineered ones

## Debugging Rules
- Temporary debugging output is allowed
- Remove all debug artifacts before final submission
- Update this file when new global rules emerge
