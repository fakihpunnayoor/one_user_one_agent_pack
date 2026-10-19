"""Rule-based per-viewer decision engine for the One User, One Agent challenge.

This module deliberately treats catalogue/profile/event text as data, never as
instructions. It has no network/model-provider dependency and is deterministic,
so replay and tests can reproduce decisions.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Iterable

IST = timezone(timedelta(hours=5, minutes=30))


def parse_time(value: Any) -> datetime | None:
    """Parse ISO-8601 timestamps; naive timestamps are interpreted as UTC."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _event_time(event: dict) -> datetime | None:
    # received_at is trusted for ordering. occurred_at/sent_at can be wrong.
    return parse_time(event.get("received_at")) or parse_time(event.get("deliver_at"))


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _dedupe_events(events: Iterable[dict], user_id: str | None = None) -> list[dict]:
    """Deduplicate by event_id and keep the earliest received copy."""
    unique: dict[str, dict] = {}
    no_id: list[dict] = []
    for event in events:
        if user_id and event.get("user_id") != user_id:
            continue
        event_id = event.get("event_id")
        if not event_id:
            no_id.append(event)
            continue
        previous = unique.get(str(event_id))
        if previous is None:
            unique[str(event_id)] = event
        else:
            old_t, new_t = _event_time(previous), _event_time(event)
            if old_t is None or (new_t is not None and new_t < old_t):
                unique[str(event_id)] = event
    return list(unique.values()) + no_id


def _profile_for(user_id: str, profiles: Iterable[dict]) -> tuple[dict, list[dict]]:
    rows = [p for p in profiles if p.get("user_id") == user_id]
    # Prefer latest snapshot/update timestamp, and retain duplicate-row evidence.
    profile = max(rows, key=lambda p: (p.get("snapshot_at", ""), p.get("updated_at", "")), default={})
    return profile, rows


def resolve_facts(user_id, events, profiles, trigger):
    """Build a viewer snapshot, source provenance, and explicit conflicts."""
    trigger_time = _event_time(trigger)
    user_events = _dedupe_events(events, user_id)
    # Only facts received by the decision time are available to the agent.
    if trigger_time:
        user_events = [e for e in user_events if (_event_time(e) is None or _event_time(e) <= trigger_time)]
    user_events.sort(key=lambda e: (_event_time(e) or datetime.min.replace(tzinfo=timezone.utc), str(e.get("event_id", ""))))
    profile, profile_rows = _profile_for(user_id, profiles)

    app_dialect = trigger.get("app_dialect")
    preferred_dialect = profile.get("preferred_dialect")
    conflicts = []
    if len(profile_rows) > 1:
        conflicts.append({"field": "profile_rows", "row_count": len(profile_rows), "resolution": "latest_snapshot_then_updated_at"})
    if app_dialect and preferred_dialect and app_dialect != preferred_dialect:
        conflicts.append({"field": "dialect", "app_setting": app_dialect, "profile": preferred_dialect, "resolution": "app_setting"})

    # Last explicit opt-out wins; this applies even if the snapshot says opted in.
    notification_opt_out = False
    whatsapp_opt_out = False
    for event in user_events:
        name = event.get("event")
        props = event.get("properties") or {}
        channel = str(props.get("channel", "push")).strip().lower()
        if name in {"notification_opt_out", "notifications_opt_out"}:
            if channel in {"push", "notification", "notifications"}:
                notification_opt_out = True
            elif channel in {"whatsapp", "whats_app"}:
                whatsapp_opt_out = True
        elif name in {"notification_opt_in", "notifications_opt_in"}:
            if channel in {"push", "notification", "notifications"}:
                notification_opt_out = False
            elif channel in {"whatsapp", "whats_app"}:
                whatsapp_opt_out = False
        if name in {"whatsapp_opt_out", "whats_app_opt_out"}:
            whatsapp_opt_out = True
        elif name in {"whatsapp_opt_in", "whats_app_opt_in"}:
            whatsapp_opt_out = False
        # Some event schemas encode explicit changes as properties.
        if "notifications_opt_in" in props:
            notification_opt_out = not _truthy(props["notifications_opt_in"])
        if "whatsapp_opt_in" in props:
            whatsapp_opt_out = not _truthy(props["whatsapp_opt_in"])

    return {
        "user_id": user_id,
        "events": user_events,
        "profile": profile,
        "profile_row_count": len(profile_rows),
        "dialect": app_dialect or preferred_dialect,
        "dialect_source": "app_setting" if app_dialect else "profile" if preferred_dialect else "unknown",
        "language": trigger.get("app_language") or profile.get("preferred_language"),
        "conflicts": conflicts,
        "notifications_opt_in": _truthy(profile.get("notifications_opt_in")) and not notification_opt_out,
        "whatsapp_opt_in": _truthy(profile.get("whatsapp_opt_in")) and not whatsapp_opt_out,
        "notification_opt_out_seen": notification_opt_out,
        "whatsapp_opt_out_seen": whatsapp_opt_out,
        "trigger_time": trigger_time,
    }


def has_event(facts, event_name):
    return any(e.get("event") == event_name for e in facts.get("events", []))


def _events_named(facts: dict, name: str) -> list[dict]:
    return [e for e in facts.get("events", []) if e.get("event") == name]


def _properties(event: dict) -> dict:
    return event.get("properties") if isinstance(event.get("properties"), dict) else {}


def _within_days(timestamp: datetime | None, now: datetime | None, days: int) -> bool:
    if not timestamp or not now:
        return False
    age = (now - timestamp).total_seconds()
    return 0 <= age <= days * 86400


def _goal_index(goals: Any) -> dict[str, dict]:
    items = goals.get("goals", []) if isinstance(goals, dict) else goals
    return {g.get("goal_id"): g for g in items if isinstance(g, dict) and g.get("goal_id")}


def goal_eligibility(goal: dict, facts: dict, trigger_time: datetime | None) -> tuple[bool, str]:
    """Evaluate the machine-readable parts of the supplied goal configuration."""
    eligibility = goal.get("eligibility") or {}
    profile = facts.get("profile", {})
    events = facts.get("events", [])
    status = profile.get("subscription_status")
    goal_id = goal.get("goal_id")

    required_status = eligibility.get("subscription_status")
    if required_status and status not in required_status:
        return False, "subscription_status_not_eligible"
    no_prior = eligibility.get("requires_no_prior_event")
    if no_prior and any(e.get("event") == no_prior for e in events):
        return False, "prior_success_event_exists"
    signup = parse_time(profile.get("signup_at"))
    signup_days = eligibility.get("signed_up_within_days")
    if signup_days is not None and not _within_days(signup, trigger_time, int(signup_days)):
        return False, "signup_not_within_eligibility_window"

    if goal_id == "G07":
        plays = [e for e in events if e.get("event") == "play_started"]
        if not plays:
            return False, "no_prior_series_play"
        latest = max((_event_time(e) for e in plays if _event_time(e)), default=None)
        if not latest or not trigger_time or (trigger_time - latest).total_seconds() < 3 * 86400:
            return False, "last_play_not_at_least_3_days_ago"
        # Needs evidence of unfinished series; don't infer it from a play alone.
        if not any(e.get("event") in {"episode_started", "play_started"} and _properties(e).get("content_slug") for e in plays):
            return False, "unfinished_series_evidence_missing"
    elif goal_id == "G08":
        if status != "active":
            return False, "not_active_subscriber"
    elif goal_id == "G09":
        if status != "active":
            return False, "not_active_subscriber"
        recent_days = set()
        if trigger_time:
            for e in events:
                if e.get("event") == "play_started" and _event_time(e):
                    age = (trigger_time - _event_time(e)).total_seconds()
                    if 0 <= age <= 14 * 86400:
                        recent_days.add(_event_time(e).astimezone(IST).date())
        if len(recent_days) > 1:
            return False, "more_than_one_play_day_in_last_14_days"
    elif goal_id == "G11" and status != "active":
        return False, "not_active_subscriber"
    elif goal_id == "G12":
        if not trigger_time:
            return False, "trigger_time_missing"
        recent_opens = [e for e in events if e.get("event") == "app_open" and _event_time(e) and 0 <= (trigger_time - _event_time(e)).total_seconds() <= 14 * 86400]
        if recent_opens:
            return False, "app_open_within_previous_14_days"

    success = goal.get("success") or {}
    if not success.get("event"):
        return False, "success_signal_unmeasurable"
    # G03's control event corrects the success event from app_open to play_started.
    if goal_id == "G03":
        success = dict(success)
        success["event"] = "play_started"
    return True, "eligible"


def choose_goal(facts, goals, trigger_time):
    """Choose one measurable goal; fail closed if eligibility is unclear."""
    by_id = _goal_index(goals)
    # Goal ordering is intentionally explicit and stable, with new-user goals first.
    priority = ["G01", "G03", "G02", "G07", "G12", "G04", "G05", "G06", "G08", "G09", "G11"]
    for goal_id in priority:
        goal = by_id.get(goal_id)
        if not goal:
            continue
        eligible, _reason = goal_eligibility(goal, facts, trigger_time)
        if eligible:
            return goal
    return None


def evidence_for_goal(facts: dict, goal: dict | None) -> list[dict]:
    """Return concise evidence references with receive time and freshness."""
    if not goal:
        return []
    evidence = []
    relevant = set()
    eligibility = goal.get("eligibility") or {}
    if eligibility.get("requires_no_prior_event"):
        relevant.add(eligibility["requires_no_prior_event"])
    relevant.update({"app_open", "play_started", "episode_completed", "purchase_completed", "tv_app_open"})
    now = facts.get("trigger_time")
    for event in facts.get("events", []):
        if event.get("event") not in relevant:
            continue
        received = _event_time(event)
        age = max(0, int((now - received).total_seconds())) if now and received else None
        evidence.append({"event_id": event.get("event_id"), "event": event.get("event"), "received_at": iso(received), "age_seconds_at_decision": age, "source": "event_log"})
    profile = facts.get("profile", {})
    if profile:
        evidence.append({"source": "profiles_snapshot.csv", "user_id": facts.get("user_id"), "snapshot_at": profile.get("snapshot_at"), "updated_at": profile.get("updated_at"), "fields_used": [k for k in ("signup_at", "subscription_status", "preferred_dialect", "notifications_opt_in", "whatsapp_opt_in") if profile.get(k) not in (None, "")]})
    return evidence


def make_no_action_decision(trigger, reason, dialect=None, conflicts=None):
    received = _event_time(trigger)
    return {
        "decision_id": "D-" + str(trigger.get("event_id", "unknown")),
        "viewer_id": trigger.get("user_id"),
        "trigger": {"event_id": trigger.get("event_id"), "event": trigger.get("event"), "received_at": iso(received), "event_age_s": 0 if received else None},
        "goal": None,
        "action": "NO_ACTION",
        "no_action_reason": reason,
        "dialect": {"used": dialect, "source": "app_setting" if dialect else "unknown", "conflicts": conflicts or []},
        "confidence": 0.0,
        "confidence_reason": reason,
        "evidence": [],
        "assumptions": [],
        "checks": {"passed": [], "failed": []},
        "decided_in_ms": None,
        "status": "NO_ACTION",
    }


def decide(trigger: dict, events: list[dict], profiles: list[dict], goals: Any, catalogue: list[dict], arms: dict[str, str], withdrawn_title_ids: set[str] | None = None, control_events: list[dict] | None = None) -> dict:
    """Make one auditable decision. This function plans home-page actions only.

    Messaging remains deliberately disabled here until a separate publisher can
    enforce shared daily frequency and delivery confirmation state.
    """
    user_id = trigger.get("user_id")
    received = _event_time(trigger)
    facts = resolve_facts(user_id, events, profiles, trigger)
    dialect = facts.get("dialect")
    if arms.get(user_id) != "agent":
        return make_no_action_decision(trigger, "holdout_or_unknown_arm", dialect, facts["conflicts"])
    if not received:
        return make_no_action_decision(trigger, "trusted_received_at_missing", dialect, facts["conflicts"])
    goal = choose_goal(facts, goals, received)
    if not goal:
        return make_no_action_decision(trigger, "no_measurable_eligible_goal", dialect, facts["conflicts"])

    withdrawn = set(withdrawn_title_ids or set())
    for control in control_events or []:
        if control.get("type") == "title_withdrawn" and parse_time(control.get("at")) and parse_time(control.get("at")) <= received:
            withdrawn.add(control.get("title_id"))
    candidates = []
    for title in catalogue:
        if title.get("dialect") != dialect or title.get("title_id") in withdrawn:
            continue
        start, end = parse_time(title.get("available_from")), parse_time(title.get("available_until"))
        if start and received < start:
            continue
        if end and received > end:
            continue
        candidates.append(title)
    # Sort deterministically and prefer recent releases, then stable title ID.
    candidates.sort(key=lambda t: (t.get("release_date", ""), t.get("title_id", "")), reverse=True)
    selected = []
    seen_slugs = set()
    for title in candidates:
        slug = title.get("content_slug")
        if slug and slug in seen_slugs:
            continue
        selected.append(title)
        if slug:
            seen_slugs.add(slug)
        if len(selected) >= 11:
            break
    if not selected:
        return make_no_action_decision(trigger, "no_available_titles_for_current_dialect", dialect, facts["conflicts"])

    # One valid row needs 3-10 unique, available titles. Hero is not duplicated in row.
    row_titles = selected[:min(10, len(selected))]
    if len(row_titles) < 4:
        return make_no_action_decision(trigger, "fewer_than_three_valid_titles", dialect, facts["conflicts"])
    action = {
        "action_id": "A-" + str(trigger.get("event_id", "unknown")),
        "decision_id": "D-" + str(trigger.get("event_id", "unknown")),
        "user_id": user_id,
        "trigger_event_id": trigger.get("event_id"),
        "type": "home_page",
        "published_at": iso(received),
        "dialect": dialect,
        "hero_title_id": row_titles[0].get("title_id"),
        "rows": [{"row_type": "popular_in_dialect", "title_ids": [t.get("title_id") for t in row_titles[1:]]}],
    }
    # Ensure row still has the required minimum after removing hero.
    if len(action["rows"][0]["title_ids"]) < 3:
        return make_no_action_decision(trigger, "insufficient_titles_for_valid_home_page", dialect, facts["conflicts"])
    verification = verify_home_page(action, catalogue, arms, withdrawn, iso(received))
    if not verification["valid"]:
        decision = make_no_action_decision(trigger, "home_page_verification_failed", dialect, facts["conflicts"])
        decision["checks"]["failed"] = verification["failed"]
        return decision

    success = dict(goal.get("success") or {})
    if goal.get("goal_id") == "G03":
        success["event"] = "play_started"
        success["condition"] = "on a later IST calendar day within the window"
    decision = {
        "decision_id": action["decision_id"],
        "viewer_id": user_id,
        "trigger": {"event_id": trigger.get("event_id"), "event": trigger.get("event"), "received_at": iso(received), "event_age_s": 0},
        "goal": {"goal_id": goal.get("goal_id"), "name": goal.get("name"), "success_signal": success, "window_hours": success.get("window_hours"), "committed_at": iso(received)},
        "action": action,
        "no_action_reason": None,
        "dialect": {"used": dialect, "source": facts.get("dialect_source"), "conflicts": facts["conflicts"]},
        "confidence": 0.72 if facts.get("profile") else 0.55,
        "confidence_reason": "Eligible measurable goal and available catalogue titles; deterministic rule-based planner, not a calibrated probability.",
        "evidence": evidence_for_goal(facts, goal),
        "assumptions": ["Catalogue availability timestamps and trigger received_at are authoritative."],
        "checks": {"passed": ["agent_arm", "measurable_goal", "dialect_match", "availability_window", "content_slug_deduplication", "home_page_structure"], "failed": []},
        "decided_in_ms": None,
        "status": "PLANNED",
    }
    return decision


def verify_home_page(action, catalogue, arms, withdrawn_title_ids, publish_time):
    """Independently verify a home-page action; return all failed checks."""
    failures = []
    user_id = action.get("user_id")
    if arms.get(user_id) != "agent":
        failures.append("holdout_or_unknown_arm")
    dialect = action.get("dialect")
    publish_dt = parse_time(publish_time)
    by_id = {row.get("title_id"): row for row in catalogue}
    selected_ids = []
    hero_id = action.get("hero_title_id")
    if hero_id:
        selected_ids.append(hero_id)
    rows = action.get("rows", [])
    if len(rows) > 6:
        failures.append("too_many_rows")
    allowed_row_types = {"next_episode", "because_you_watched", "popular_in_dialect", "new_releases", "genre"}
    seen_slugs = set()
    if not hero_id:
        failures.append("missing_hero_title")
    for row in rows:
        row_type, title_ids = row.get("row_type"), row.get("title_ids", [])
        if row_type not in allowed_row_types:
            failures.append("invalid_row_type")
        if not 3 <= len(title_ids) <= 10:
            failures.append("invalid_row_size")
        selected_ids.extend(title_ids)
    if not rows:
        failures.append("missing_rows")
    for title_id in selected_ids:
        title = by_id.get(title_id)
        if not title:
            failures.append("unknown_title:" + str(title_id))
            continue
        if title_id in (withdrawn_title_ids or set()):
            failures.append("withdrawn_title:" + str(title_id))
        if title.get("dialect") != dialect:
            failures.append("wrong_dialect:" + str(title_id))
        start, end = parse_time(title.get("available_from")), parse_time(title.get("available_until"))
        if publish_dt and start and publish_dt < start:
            failures.append("not_yet_available:" + str(title_id))
        if publish_dt and end and publish_dt > end:
            failures.append("expired_title:" + str(title_id))
        slug = title.get("content_slug")
        if slug and slug in seen_slugs:
            failures.append("duplicate_content_slug:" + str(slug))
        elif slug:
            seen_slugs.add(slug)
    return {"valid": not failures, "failed": failures}
