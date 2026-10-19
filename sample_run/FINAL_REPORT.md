# Replay and Pilot Report

## Replay
- Decisions: 2679
- Published actions: 700
- Goal outcomes: {'succeeded': 2, 'failed': 0, 'inconclusive': 698}
- Action types: {'home_page': 700}
- Latency: `{"decision_latency_ms": {"max": 6.499, "p50": 0.04, "p95": 3.034}, "decisions_over_60_seconds": 0, "no_action_decisions": 1979, "note": "Deterministic rule-based mock replay; measured local processing latency is not a production provider/network latency guarantee.", "processing_elapsed_ms": 3741.393, "published_actions": 700, "total_decisions": 2679}`

## Earlier randomized pilot: intention-to-treat

- **holdout**: 387/1979 successes (19.6%); 95% Wilson interval [0.1787, 0.2136]; served 0.
- **treatment**: 349/1912 successes (18.2%); 95% Wilson interval [0.1659, 0.2005]; served 217.

- Treatment minus holdout success-rate difference: **-1.3%** (approximate 95% CI -3.8% to +1.1%).

The earlier pilot is a descriptive estimate, not proof of causal effect. Keep every randomized user in the assigned arm, including treatment users who were not served. Comparing served users with everyone else is biased because service is a post-assignment selection. The confidence interval above uses a normal approximation.

Goal outcomes are replay judgments under the configured event history and rule implementation. They are not proof of real-world causal impact. The randomized pilot figures below are from the supplied earlier pilot CSV, not from this replay run.
