# One User, One Agent — Submission

## Requirements
- Python 3.10+ recommended (standard-library implementation; no third-party packages required).
- Run commands from the project root, the directory containing `src/`, `data/`, `config/`, `replay/`, and `simulator/`.

## Run

```powershell
python --version
python -m py_compile src/agent.py src/replay.py src/report.py
python -m unittest discover -s tests -v
python -m src.replay
python simulator/simulate_outcomes.py --pack . --actions sample_run/actions.jsonl --out sample_run/sim
python -m src.report
```

## Outputs
- `sample_run/decisions.jsonl`: one decision per unique trigger, including explicit no-action reasons.
- `sample_run/actions.jsonl`: verified home-page actions for the randomized agent arm only.
- `sample_run/latency_report.json`: processing latency summary.
- `sample_run/sim/simulator_report.json`: supplied simulator output and policy-problem count.
- `sample_run/final_report.json` and `sample_run/FINAL_REPORT.md`: replay summary and earlier randomized pilot ITT summary.

## Design summary
The agent uses a deterministic policy and no external model/network service. It trusts server `received_at`/replay `deliver_at`, logs evidence and conflicts, checks goal measurability, verifies home-page content, suppresses holdout actions, deduplicates triggers, and fails closed during supplied dependency/model fault windows.

## Important scope note
This is a reproducible assignment implementation, not a production deployment. It currently publishes home-page actions only; push/WhatsApp/in-app messaging delivery is not implemented. See `KNOWN_LIMITATIONS.md` and `ARCHITECTURE.md`.
