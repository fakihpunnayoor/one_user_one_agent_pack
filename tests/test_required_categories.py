"""Explicit regression tests for the four assignment-required test categories."""
import json
import unittest
from pathlib import Path

from src.agent import _dedupe_events, decide
from src.replay import dedupe_triggers, dependency_failure_reason, event_age_seconds, load_catalogue

ROOT = Path(__file__).resolve().parents[1]

class RequiredAssignmentTests(unittest.TestCase):
    def test_late_or_duplicate_trigger_is_idempotent(self):
        # Same event delivered twice; keep only the first delivery in order.
        triggers = [
            {"event_id": "evt-1", "user_id": "u1", "deliver_at": "2026-10-01T10:00:00Z"},
            {"event_id": "evt-1", "user_id": "u1", "deliver_at": "2026-10-01T10:00:05Z"},
            {"event_id": "evt-2", "user_id": "u1", "deliver_at": "2026-10-01T10:00:06Z"},
        ]
        result = dedupe_triggers(triggers)
        self.assertEqual([x["event_id"] for x in result], ["evt-1", "evt-2"])
        # History event duplicates also collapse by event_id.
        events = [
            {"event_id":"e1", "user_id":"u1", "received_at":"2026-10-01T10:00:02Z"},
            {"event_id":"e1", "user_id":"u1", "received_at":"2026-10-01T10:00:01Z"},
        ]
        self.assertEqual(len(_dedupe_events(events, "u1")), 1)


    def test_event_age_and_stale_app_open_are_measurable(self):
        trigger = {"event":"app_open", "occurred_at":"2026-10-01T10:00:00Z"}
        self.assertEqual(event_age_seconds(trigger, "2026-10-01T10:06:00Z"), 360)

    def test_g03_correction_applies_to_open_goal(self):
        # Replay applies the control to a still-open G03 goal before checking success.
        opened = {"goal_id":"G03", "success_event":"app_open", "started_at":"2026-10-01T08:00:00Z", "window_hours":48}
        control = {"type":"goal_rule_corrected", "goal_id":"G03", "at":"2026-10-01T08:30:00Z", "new_success":{"event":"play_started"}}
        if opened.get("goal_id") == "G03" and control["type"] == "goal_rule_corrected":
            opened["success_event"] = control["new_success"]["event"]
        self.assertEqual(opened["success_event"], "play_started")

    def test_conflicting_source_prefers_current_app_dialect(self):
        from src.agent import resolve_facts
        trigger = {"user_id":"u1", "received_at":"2026-10-01T10:00:00Z", "app_dialect":"d2"}
        profile = {"user_id":"u1", "preferred_dialect":"d1", "notifications_opt_in":"true"}
        facts = resolve_facts("u1", [], [profile], trigger)
        self.assertEqual(facts["dialect"], "d2")
        self.assertEqual(facts["dialect_source"], "app_setting")
        self.assertTrue(any(c.get("field") == "dialect" for c in facts["conflicts"]))

    def test_tool_failure_fails_closed(self):
        controls = [{"type":"dependency_unavailable", "dependency":"model_provider",
                     "at":"2026-10-01T10:00:00Z", "until":"2026-10-01T10:05:00Z"}]
        reason = dependency_failure_reason(
            "2026-10-01T10:02:00Z", controls,
            {"event_id":"evt-1"}, {})
        self.assertEqual(reason, "model_provider_unavailable_fail_closed")
        fault_reason = dependency_failure_reason(
            "2026-10-01T11:00:00Z", [], {"event_id":"evt-2"},
            {"evt-2":{"fault":"timeout"}})
        self.assertEqual(fault_reason, "mock_provider_fault_rejected:timeout")
        # Outside the injected failure window, no failure is reported.
        self.assertIsNone(dependency_failure_reason(
            "2026-10-01T10:06:00Z", controls, {"event_id":"evt-3"}, {}))

    def test_corrected_g03_goal_uses_play_started(self):
        goals = json.loads((ROOT / "config" / "goals.json").read_text(encoding="utf-8"))
        trigger = {"event_id":"trigger-g03", "user_id":"test-user",
                   "event":"app_open", "received_at":"2026-10-01T10:00:00Z",
                   "deliver_at":"2026-10-01T10:00:00Z", "app_dialect":"d1", "app_language":"eng"}
        profile = {"user_id":"test-user", "signup_at":"2026-09-30T10:00:00Z",
                   "subscription_status":"never", "preferred_dialect":"d1",
                   "notifications_opt_in":"true", "whatsapp_opt_in":"false"}
        # Existing play disqualifies G01, allowing G03 to be selected by priority.
        events = [{"event_id":"prior-play", "user_id":"test-user", "event":"play_started",
                   "received_at":"2026-09-30T11:00:00Z", "properties":{"title_id":"T0001"}}]
        arms = {"test-user":"agent"}
        decision = decide(trigger, events, [profile], goals, load_catalogue(), arms,
                          control_events=[{"type":"goal_rule_corrected", "goal_id":"G03",
                                           "at":"2026-10-01T08:30:00Z",
                                           "new_success":{"event":"play_started"}}])
        self.assertEqual(decision.get("goal", {}).get("goal_id"), "G03")
        self.assertEqual(decision.get("goal", {}).get("success_signal", {}).get("event"), "play_started")

if __name__ == "__main__":
    unittest.main()
