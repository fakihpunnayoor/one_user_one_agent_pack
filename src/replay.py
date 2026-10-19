"""Deterministic replay harness for the One User, One Agent challenge."""
from __future__ import annotations
import csv, json, time, sqlite3
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from src.agent import decide, parse_time, iso

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "sample_run"

def read_jsonl(path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)

def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def dedupe_triggers(triggers):
    """Keep the first delivered trigger for each event_id, in delivery order."""
    seen = set()
    unique = []
    for trigger in sorted(triggers, key=lambda e: e.get("deliver_at", "")):
        event_id = trigger.get("event_id")
        if event_id and event_id in seen:
            continue
        if event_id:
            seen.add(event_id)
        unique.append(trigger)
    return unique

def event_age_seconds(trigger, delivery):
    """Age of an event when delivered, measured from occurrence to delivery."""
    occurred = parse_time(trigger.get("occurred_at") or trigger.get("sent_at") or trigger.get("received_at"))
    delivered = parse_time(delivery)
    if not occurred or not delivered:
        return None
    return max(0, int((delivered - occurred).total_seconds()))

def dependency_failure_reason(delivery, controls, trigger, faults):
    """Return a fail-closed reason for an injected dependency/model failure, if any."""
    delivery = delivery or ""
    for control in controls:
        start, end = control.get("at", ""), control.get("until", "")
        if start <= delivery < end:
            if control.get("type") == "dependency_unavailable" and control.get("dependency") == "model_provider":
                return "model_provider_unavailable_fail_closed"
            if control.get("type") == "dependency_degraded" and control.get("dependency") == "user_data_store":
                return "user_data_store_degraded_fail_closed"
    fault = faults.get(trigger.get("event_id"))
    if fault and fault.get("fault") in {"unsupported_claim", "malformed_output", "timeout"}:
        return "mock_provider_fault_rejected:" + fault.get("fault", "unknown")
    return None

def load_catalogue():
    rows = read_csv(ROOT / "data" / "catalogue.csv")
    return rows

def main():
    wall_start = time.perf_counter()
    triggers = list(read_jsonl(ROOT / "replay" / "triggers.jsonl"))
    live = list(read_jsonl(ROOT / "replay" / "live_events.jsonl"))
    controls = sorted(list(read_jsonl(ROOT / "replay" / "control_events.jsonl")), key=lambda x: x.get("at", ""))
    faults = {x.get("trigger_event_id"): x for x in read_jsonl(ROOT / "replay" / "model_faults.jsonl")}
    arms = {r["user_id"]: r["arm"] for r in read_csv(ROOT / "replay" / "arms.csv")}
    profiles = read_csv(ROOT / "data" / "profiles_snapshot.csv")
    goals = json.loads((ROOT / "config" / "goals.json").read_text(encoding="utf-8"))
    catalogue = load_catalogue()
    # Load event history by user so each decision only sees evidence available by its delivery time.
    event_by_user = defaultdict(list)
    for e in read_jsonl(ROOT / "data" / "events.jsonl"):
        event_by_user[e.get("user_id")].append(e)
    for e in live:
        event_by_user[e.get("user_id")].append(e)
    for user in event_by_user:
        event_by_user[user].sort(key=lambda e: (e.get("received_at", ""), e.get("event_id", "")))
    # Deduplicate trigger delivery by event_id, retaining first delivered copy.
    unique = dedupe_triggers(triggers)
    decisions, actions, goal_outcomes = [], [], []
    active_goal = {}  # one open goal per viewer
    # Rebuild a deterministic durable-state snapshot for each replay run.
    state_db = OUT / "viewer_state.sqlite3"
    if state_db.exists(): state_db.unlink()
    state_conn = sqlite3.connect(state_db)
    state_conn.execute("CREATE TABLE viewer_state (user_id TEXT PRIMARY KEY, active_goal_json TEXT, last_homepage TEXT, updated_at TEXT)")
    last_homepage = {}
    action_counter = 0
    for index, original in enumerate(unique, start=1):
        started = time.perf_counter()
        trigger = dict(original)
        delivery = trigger.get("deliver_at") or trigger.get("received_at")
        # The replay clock is delivery time; retain source timestamp for audit.
        trigger["source_received_at"] = trigger.get("received_at")
        trigger["received_at"] = delivery
        now = parse_time(delivery)
        user_id = trigger.get("user_id")
        available_events = [e for e in event_by_user.get(user_id, []) if not e.get("received_at") or e.get("received_at") <= (delivery or "")]
        active_controls = [c for c in controls if c.get("at", "") <= (delivery or "")]
        reason = None
        age_s = event_age_seconds(trigger, delivery)
        # App-open triggers older than five minutes are too stale to represent a live session.
        if trigger.get("event") == "app_open" and age_s is not None and age_s > 300:
            reason = "stale_app_open_trigger"
        failure_reason = dependency_failure_reason(delivery, active_controls, trigger, faults)
        if reason is not None:
            decision = None
        elif arms.get(user_id) != "agent":
            decision = None
            reason = "holdout_arm_no_action"
        elif failure_reason:
            decision = None
            reason = failure_reason
        else:
            # Do not re-open a new goal while an existing measurable goal is open.
            opened = active_goal.get(user_id)
            if opened:
                # Apply the analytics correction to any still-open G03 goal, including older goals.
                if opened.get("goal_id") == "G03":
                    correction = next((c for c in active_controls if c.get("type") == "goal_rule_corrected" and c.get("goal_id") == "G03"), None)
                    if correction:
                        opened["success_event"] = (correction.get("new_success") or {}).get("event", "play_started")
                        opened["rule_corrected_at"] = correction.get("at")
                goal_event = opened.get("success_event")
                goal_start = opened.get("started_at")
                window_h = opened.get("window_hours", 0)
                matching = [e for e in available_events if e.get("event") == goal_event and e.get("received_at", "") >= goal_start]
                if opened.get("goal_id") in {"G04", "G05"}:
                    matching = [e for e in matching if (e.get("properties") or {}).get("is_trial") in (False, "false", 0, "0")]
                if opened.get("goal_id") == "G07":
                    matching = [e for e in matching if (e.get("properties") or {}).get("content_slug") == opened.get("content_slug")]
                succeeded = bool(matching)
                elapsed = (now - parse_time(goal_start)).total_seconds() / 3600 if now and parse_time(goal_start) else 0
                if succeeded or elapsed > window_h:
                    outcome = "succeeded" if succeeded else "failed"
                    goal_outcomes.append({"user_id": user_id, "goal_id": opened.get("goal_id"), "started_at": goal_start,
                                          "judged_at": delivery, "outcome": outcome,
                                          "evidence_event_id": matching[0].get("event_id") if succeeded else None,
                                          "reason": "success_condition_met" if succeeded else "deadline_elapsed_without_success"})
                    active_goal.pop(user_id, None)
                else:
                    decision = None; reason = "viewer_already_has_open_goal"
            if reason is None:
                # Prevent repeatedly replacing a live home page within its 120-minute TTL.
                last = parse_time(last_homepage.get(user_id))
                if now and last and (now-last).total_seconds() < 120*60:
                    decision = None; reason = "personalised_home_page_still_live"
                else:
                    decision = decide(trigger, available_events, profiles, goals, catalogue, arms,
                                      withdrawn_title_ids={c.get("title_id") for c in active_controls if c.get("type")=="title_withdrawn"},
                                      control_events=active_controls)
                    # Decision engine emits action timestamps at the decision time; publish at measured completion.
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        decision_id = f"D-{index:05d}"
        if decision is None:
            decision = {
                "decision_id": decision_id, "viewer_id": user_id,
                "trigger": {"event_id": trigger.get("event_id"), "event": trigger.get("event"), "received_at": delivery,
                            "source_received_at": trigger.get("source_received_at"), "event_age_s": age_s},
                "goal": None, "action": "NO_ACTION", "no_action_reason": reason or "safe_no_action",
                "dialect": {"used": trigger.get("app_dialect"), "source": "app_setting", "conflicts": []},
                "confidence": 0.0, "confidence_reason": reason or "safe_no_action", "evidence": [],
                "assumptions": [], "checks": {"passed": [], "failed": []}, "status": "NO_ACTION"}
        else:
            decision["decision_id"] = decision_id
            decision["trigger"]["received_at"] = delivery
            decision["trigger"]["source_received_at"] = trigger.get("source_received_at")
            decision["trigger"]["event_age_s"] = age_s
            decision["decided_in_ms"] = elapsed_ms
            act = decision.get("action")
            if isinstance(act, dict):
                action_counter += 1
                act["action_id"] = f"A-{action_counter:05d}"
                act["decision_id"] = decision_id
                act["published_at"] = iso(now + timedelta(milliseconds=elapsed_ms)) if now else delivery
                actions.append(act)
                decision["status"] = "PUBLISHED"
                last_homepage[user_id] = act["published_at"]
                goal = decision.get("goal") or {}
                if goal.get("goal_id"):
                    active_goal[user_id] = {"goal_id": goal["goal_id"], "success_event": (goal.get("success_signal") or {}).get("event"),
                                            "started_at": delivery, "window_hours": goal.get("window_hours", 0)}
            else:
                decision["action"] = "NO_ACTION"
                decision["status"] = "NO_ACTION"
                decision["no_action_reason"] = decision.get("no_action_reason") or "planner_declined_action"
        decision["decided_in_ms"] = elapsed_ms
        # The hard publication SLO is 60 seconds. A >60s decision is rejected, never published.
        if elapsed_ms > 60000 and isinstance(decision.get("action"), dict):
            bad = decision.pop("action")
            actions = [a for a in actions if a.get("action_id") != bad.get("action_id")]
            decision["action"] = "NO_ACTION"; decision["status"] = "NO_ACTION"
            decision["no_action_reason"] = "publication_slo_exceeded"
        decisions.append(decision)
        state_conn.execute("INSERT INTO viewer_state(user_id, active_goal_json, last_homepage, updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET active_goal_json=excluded.active_goal_json, last_homepage=excluded.last_homepage, updated_at=excluded.updated_at",
                           (str(user_id), json.dumps(active_goal.get(user_id)) if active_goal.get(user_id) else None, last_homepage.get(user_id), delivery))
    # Remaining open goals have not yet reached a terminal judgment in the replay window.
    for user_id, opened in active_goal.items():
        goal_outcomes.append({"user_id": user_id, "goal_id": opened.get("goal_id"), "started_at": opened.get("started_at"),
                              "judged_at": None, "outcome": "inconclusive", "evidence_event_id": None,
                              "reason": "replay_window_ended_before_success_or_deadline"})
    state_conn.commit(); state_conn.close()
    OUT.mkdir(parents=True, exist_ok=True)
    write_jsonl(OUT / "decisions.jsonl", decisions)
    write_jsonl(OUT / "actions.jsonl", actions)
    write_jsonl(OUT / "goal_outcomes.jsonl", goal_outcomes)
    latencies = [d.get("decided_in_ms", 0) or 0 for d in decisions]
    latencies_sorted = sorted(latencies)
    def pct(p): return latencies_sorted[min(len(latencies_sorted)-1, int((len(latencies_sorted)-1)*p))] if latencies_sorted else 0
    latency = {"total_decisions": len(decisions), "published_actions": len(actions),
               "no_action_decisions": sum(d.get("action") == "NO_ACTION" for d in decisions),
               "processing_elapsed_ms": round((time.perf_counter()-wall_start)*1000, 3),
               "decision_latency_ms": {"p50": pct(.50), "p95": pct(.95), "max": max(latencies, default=0)},
               "decisions_over_60_seconds": sum(v > 60000 for v in latencies),
               "note": "Deterministic rule-based mock replay; measured local processing latency is not a production provider/network latency guarantee."}
    (OUT / "latency_report.json").write_text(json.dumps(latency, indent=2), encoding="utf-8")
    print(f"Triggers processed: {len(unique)}; decisions: {len(decisions)}; actions: {len(actions)}")
    print(f"Output folder: {OUT}")

if __name__ == "__main__": main()
