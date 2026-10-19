# Known Limitations

1. The agent is deterministic and rule-based; it does not call a real model provider. The injected mock model faults are rejected safely, but actual model latency, token budgets, retries, and provider behavior are not exercised.
2. `src/replay.py` models the dependency-unavailable/degraded windows with fail-closed no-action decisions. It does not simulate the specified 8-second p95 read latency or 20% data-store read timeouts in detail.
3. The replay state is in-memory and durable only during a single run. Production use requires transactional persistence, concurrent-worker coordination, and crash recovery.
4. This implementation only publishes home-page actions. Push, WhatsApp, and in-app-message generation/delivery are not implemented, so messaging consent/quiet-hours/frequency enforcement is not exercised by published messages. The supplied simulator reports no policy violations for the generated home-page actions.
5. Goal eligibility is intentionally conservative where the pack does not provide enough structured evidence. In particular, unfinished-series state for G07 is not inferred without reliable episode progress evidence; the G10 goal is unmeasurable because it has no success event.
6. The G03 success signal is corrected to `play_started` as specified by the control event. A full goal-outcome judge for every goal type and all correction timing edge cases remains limited.
7. Publication time is based on local measured Python processing time and is not a production latency guarantee. The simulator is synthetic and has a configured seed; evaluators may use another seed/replay day.
8. The earlier pilot report is observationally interpreted with randomized assignment as the denominator. Its simple confidence interval is approximate and is not a substitute for a pre-registered statistical analysis.

## Additional implementation boundaries in this final revision
- App-open triggers older than 300 seconds (occurrence time to delivery time) are rejected as stale. This is a configurable prototype threshold, not a production-calibrated freshness policy.
- Replay now emits `sample_run/goal_outcomes.jsonl` with `succeeded`, `failed`, and `inconclusive` judgments. The judge applies explicit conditions for G04/G05 and G07; other goal conditions remain simplified and should be expanded before production.
- A replay-local SQLite snapshot (`sample_run/viewer_state.sqlite3`) persists the latest per-viewer active-goal/homepage state for inspection. Each replay run rebuilds this database for reproducibility; this is not a production multi-process state service or restart-resume workflow.
- The randomized impact estimate in `sample_run/final_report.json` still uses the supplied historical `pilot/pilot_outcomes.csv`. It is not computed from synthetic simulator outcomes and must not be described as impact caused by this replay.
- Provider/network calls and the push, WhatsApp, and in-app delivery integrations are still mocked/not implemented. Local measured latency is not a production SLA guarantee.
