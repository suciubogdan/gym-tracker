from __future__ import annotations

import asyncio

from gym_tracker.mcp_server import (
    apply_week_proposal,
    import_recent_workouts,
    mcp,
)


def test_coaching_tools_are_registered() -> None:
    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {
        "import_recent_workouts",
        "record_workout_feedback",
        "mark_workout_missed",
        "mark_workout_rescheduled",
        "reconcile_planned_and_completed_workouts",
        "get_coaching_context",
        "propose_next_week",
        "save_coaching_proposal",
        "get_week_proposal",
        "apply_week_proposal",
        "get_weekly_plan",
        "get_pending_checkins",
    } <= names


def test_mcp_mutations_require_confirmation_before_service_access() -> None:
    assert import_recent_workouts("bogdan")["imported"] is False
    assert apply_week_proposal("bogdan", "2026-08-31")["applied"] is False
