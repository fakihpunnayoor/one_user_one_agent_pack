import unittest
from src.agent import _dedupe_events, resolve_facts, goal_eligibility, verify_home_page, parse_time

class AgentTests(unittest.TestCase):
    def test_deduplicates_event_id(self):
        rows = [
            {"event_id":"e1","user_id":"u","received_at":"2026-01-01T00:00:02Z"},
            {"event_id":"e1","user_id":"u","received_at":"2026-01-01T00:00:01Z"},
            {"event_id":"e2","user_id":"u","received_at":"2026-01-01T00:00:03Z"},
        ]
        result = _dedupe_events(rows, "u")
        self.assertEqual(len(result), 2)
        self.assertEqual(next(x for x in result if x['event_id']=='e1')['received_at'], '2026-01-01T00:00:01Z')

    def test_app_dialect_wins_and_conflict_is_logged(self):
        trigger = {"user_id":"u","received_at":"2026-10-01T10:00:00Z","app_dialect":"d2"}
        profile = {"user_id":"u","preferred_dialect":"d1","notifications_opt_in":"true","whatsapp_opt_in":"true"}
        facts = resolve_facts("u", [], [profile], trigger)
        self.assertEqual(facts['dialect'], 'd2')
        self.assertEqual(facts['dialect_source'], 'app_setting')
        self.assertTrue(any(c['field']=='dialect' for c in facts['conflicts']))

    def test_whatsapp_opt_out_does_not_opt_out_push(self):
        trigger = {"user_id":"u","received_at":"2026-10-01T10:00:00Z","app_dialect":"d2"}
        profile = {"user_id":"u","notifications_opt_in":"true","whatsapp_opt_in":"true"}
        event = {"event_id":"e","user_id":"u","event":"notification_opt_out","received_at":"2026-10-01T09:00:00Z","properties":{"channel":"whatsapp"}}
        facts = resolve_facts("u", [event], [profile], trigger)
        self.assertTrue(facts['notifications_opt_in'])
        self.assertFalse(facts['whatsapp_opt_in'])

    def test_g10_is_unmeasurable(self):
        goal = {"goal_id":"G10","success":{"event":None}}
        ok, reason = goal_eligibility(goal, {"profile":{},"events":[]}, parse_time('2026-10-01T10:00:00Z'))
        self.assertFalse(ok)
        self.assertEqual(reason, 'success_signal_unmeasurable')

    def test_homepage_verifier_rejects_duplicate_slug_and_holdout(self):
        catalogue = [
            {"title_id":"T1","content_slug":"same","dialect":"d1","available_from":"2026-01-01T00:00:00Z"},
            {"title_id":"T2","content_slug":"same","dialect":"d1","available_from":"2026-01-01T00:00:00Z"},
            {"title_id":"T3","content_slug":"s3","dialect":"d1","available_from":"2026-01-01T00:00:00Z"},
            {"title_id":"T4","content_slug":"s4","dialect":"d1","available_from":"2026-01-01T00:00:00Z"},
        ]
        action = {"user_id":"u","dialect":"d1","hero_title_id":"T1","rows":[{"row_type":"popular_in_dialect","title_ids":["T2","T3","T4"]}]}
        result = verify_home_page(action, catalogue, {"u":"holdout"}, set(), '2026-10-01T10:00:00Z')
        self.assertFalse(result['valid'])
        self.assertIn('holdout_or_unknown_arm', result['failed'])
        self.assertTrue(any(x.startswith('duplicate_content_slug') for x in result['failed']))

if __name__ == '__main__': unittest.main()
