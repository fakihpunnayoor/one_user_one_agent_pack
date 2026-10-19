# Architecture

## Components
- `src/agent.py`: deterministic decision policy. It resolves viewer facts from the event log and profile snapshot, applies goal eligibility, chooses an available catalogue set, creates an auditable decision, and verifies a home-page action independently.
- `src/replay.py`: offline replay harness. It orders triggers by `deliver_at`, makes per-user evidence available only when received, applies assignment-arm restrictions, models listed dependency windows and injected provider faults, maintains one open goal per user, prevents replacing a live home page within its 120-minute TTL, and writes decisions/actions/latency.
- `simulator/simulate_outcomes.py`: supplied evaluator simulator. Its policy-problem report is used as a safety check.
- `src/report.py`: writes a replay summary and intention-to-treat summary for the earlier randomized pilot.

## Data flow
Historical events and replay live events are indexed by viewer. At each trigger delivery time, the harness selects only events whose server `received_at` is not later than the trigger. Device `occurred_at` and `sent_at` are not trusted for ordering. The current trigger's app dialect/language is preferred over the warehouse profile snapshot. Duplicate event IDs are deduplicated by the agent's evidence builder.

## Decision contract
Every trigger produces a decision record. It contains the trigger identity/time, selected measurable goal or a no-action reason, action or `NO_ACTION`, dialect source/conflicts, evidence references and ages, assumptions, checks, confidence explanation, processing time, and status. Home-page actions are passed through a separate verifier before being written to the action stream.

## State and idempotency
Replay deduplicates trigger event IDs, keeps an in-memory active-goal record per viewer, and respects home-page TTL. This state is durable for the lifetime of one replay process and is emitted through the decision/action logs; it is not a production database. A production implementation should persist state transactionally in a durable store with idempotency keys and lease/lock semantics.

## Failure behavior
The harness fails closed during injected model-provider and data-store failure windows and rejects all supplied malformed/unsupported/timeout model-fault cases. The implementation is a deterministic mock/rule-based planner and does not make external model calls. Measured latency is local processing time, not a guarantee about production network/provider latency.
