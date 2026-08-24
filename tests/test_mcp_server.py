from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import pytest

import gym_tracker.mcp_server as mcp_server
from gym_tracker.domain.models import CompletedStrengthWorkout
from gym_tracker.garmin.fake import FakeGarminClient
from gym_tracker.services import GymService
from gym_tracker.storage.repository import ProjectRepository


def test_coaching_tools_are_registered() -> None:
    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {
        "get_training_plan",
        "get_recovery_context",
        "import_recent_workouts",
        "refresh_coaching_data",
        "get_training_locations",
        "record_workout_feedback",
        "mark_workout_missed",
        "mark_workout_rescheduled",
        "reconcile_planned_and_completed_workouts",
        "get_coaching_context",
        "propose_next_week",
        "save_coaching_proposal",
        "get_week_proposal",
        "propose_session_location",
        "apply_week_proposal",
        "get_weekly_plan",
        "get_pending_checkins",
        "get_garmin_diff",
        "sync_plan_to_garmin",
        "schedule_session",
        "schedule_week",
    } <= names


def test_mcp_mutations_require_confirmation_before_service_access() -> None:
    assert mcp_server.import_recent_workouts("bogdan")["imported"] is False
    assert (
        mcp_server.refresh_coaching_data("bogdan", "2026-08-24", "2026-08-24")["refreshed"] is False
    )
    assert mcp_server.apply_week_proposal("bogdan", "2026-08-31")["applied"] is False
    with pytest.raises(ValueError, match="requires confirm=true"):
        mcp_server.sync_plan_to_garmin("bogdan", dry_run=False)
    with pytest.raises(ValueError, match="requires confirm=true"):
        mcp_server.schedule_session("bogdan", "2026-08-31", "A", dry_run=False)
    with pytest.raises(ValueError, match="requires confirm=true"):
        mcp_server.schedule_week("bogdan", "2026-08-31", dry_run=False)


def test_refresh_coaching_data_imports_before_returning_evidence(
    repository: ProjectRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeGarminClient()
    client.activities["activity-a"] = CompletedStrengthWorkout(
        person="bogdan",
        garmin_activity_id="activity-a",
        started_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
        workout_name="Bogdan Full Body A",
        exercises=[],
        imported_at=datetime(2026, 8, 24, 13, tzinfo=UTC),
    )
    service = GymService(repository, client_factory=lambda person: client)
    monkeypatch.setattr(mcp_server, "_service", lambda: service)

    result = mcp_server.refresh_coaching_data("bogdan", "2026-08-24", "2026-08-24", confirm=True)

    assert result["refreshed"] is True
    assert result["result"]["import"] == {"imported": 1, "skipped": 0}
    assert result["result"]["import_window_days"] == 7
    assert result["result"]["recovery_import"]["updated"] is True
    assert result["result"]["recovery_assessment"]["assessment"]["state"] == "unknown"
    assert result["result"]["reconciliation"]["week_start"] == "2026-08-24"
    assert result["result"]["reconciliation"]["sessions"][0]["status"] == "completed"
    assert result["result"]["reconciliation"]["sessions"][0]["garmin_activity_id"] == "activity-a"
    assert isinstance(result["result"]["pending_checkins"], list)
    assert mcp_server.get_recovery_context("bogdan", "2026-08-24")["snapshots"]


def test_mcp_plan_views_expose_equipment_notes_without_garmin(
    repository: ProjectRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = GymService(repository)
    monkeypatch.setattr(mcp_server, "_service", lambda: service)

    plan = mcp_server.get_training_plan("bogdan")
    weekly = mcp_server.get_weekly_plan("bogdan", "2026-08-31")

    assert plan["workouts"]["A"]["equipment_notes"].startswith("Equipment: ")
    assert plan["workout_variants"]["home"]["A"]["equipment_notes"].startswith("Equipment: ")
    assert all(
        session["equipment_notes"].startswith("Equipment: ") for session in weekly["sessions"]
    )


def test_mcp_garmin_views_include_template_location_and_equipment_notes(
    repository: ProjectRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeGarminClient()
    service = GymService(repository, client_factory=lambda person: client)
    monkeypatch.setattr(mcp_server, "_service", lambda: service)

    diff = mcp_server.get_garmin_diff("bogdan")
    assert len(diff) == 8
    assert {item["template_key"] for item in diff} >= {"gym:A", "home:A"}
    assert all(item["notes"].startswith("Equipment: ") for item in diff)

    service.sync_plan_to_garmin("bogdan", dry_run=False)
    session = mcp_server.schedule_session("bogdan", "2026-08-31", "A")
    schedule = mcp_server.schedule_week("bogdan", date(2026, 8, 31).isoformat())
    assert session["workout"] == "A"
    assert session["location"] == "gym"
    assert session["notes"].startswith("Equipment: ")
    assert all(item["location"] == "gym" for item in schedule)
    assert all(item["notes"].startswith("Equipment: ") for item in schedule)
