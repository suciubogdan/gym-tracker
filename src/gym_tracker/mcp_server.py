from __future__ import annotations

from datetime import date
from typing import Any

from mcp.server import MCPServer

from gym_tracker.domain.models import (
    AttendanceStatus,
    CoachChange,
    ExerciseFeedback,
    OverallFeedback,
)
from gym_tracker.services import GymService
from gym_tracker.storage.repository import ProjectRepository, find_project_root

mcp = MCPServer(
    "gym-tracker",
    instructions=(
        "Local plans are canonical. Inspect proposals before applying them. "
        "Garmin synchronization defaults to dry-run and external mutations require confirmation."
    ),
)


def _service() -> GymService:
    return GymService(ProjectRepository(find_project_root()))


@mcp.tool()
def get_training_plan(person: str) -> dict[str, Any]:
    """Read the canonical local training plan."""
    return _service().get_training_plan(person)


@mcp.tool()
def get_recent_workouts(person: str, days: int = 7) -> list[dict[str, Any]]:
    """Read normalized local workout history; no Garmin call is made."""
    return _service().get_recent_workouts(person, days)


@mcp.tool()
def import_recent_workouts(person: str, days: int = 7, confirm: bool = False) -> dict[str, Any]:
    """Import normalized Garmin history locally only when confirm=true."""
    if not confirm:
        return {
            "imported": False,
            "reason": "Set confirm=true; this reads Garmin and writes normalized local history.",
        }
    return {"imported": True, "result": _service().import_workouts(person, days)}


@mcp.tool()
def get_training_status(person: str) -> dict[str, Any]:
    """Calculate deterministic progression status without mutation."""
    return _service().get_training_status(person)


@mcp.tool()
def propose_progression(person: str) -> dict[str, Any]:
    """Create a local, reviewable proposal; does not change the plan."""
    return _service().propose_progression(person).model_dump(mode="json")


@mcp.tool()
def apply_progression(person: str, confirm: bool = False) -> dict[str, Any]:
    """Apply a reviewed local proposal only when confirm=true."""
    if not confirm:
        return {"applied": False, "reason": "Set confirm=true after reviewing the proposal."}
    proposal = _service().apply_progression(person)
    return {"applied": True, "proposal": proposal.model_dump(mode="json")}


@mcp.tool()
def record_workout_feedback(
    person: str,
    scheduled_date: str,
    workout_key: str,
    status: str = "completed",
    garmin_activity_id: str | None = None,
    overall: dict[str, Any] | None = None,
    exercises: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Record local attendance and optional subjective feedback after a workout."""
    feedback = _service().record_workout_feedback(
        person=person,
        scheduled_date=date.fromisoformat(scheduled_date),
        workout_key=workout_key,
        status=AttendanceStatus(status),
        garmin_activity_id=garmin_activity_id,
        overall=OverallFeedback.model_validate(overall or {}),
        exercises=[ExerciseFeedback.model_validate(item) for item in exercises or []],
    )
    return feedback.model_dump(mode="json")


@mcp.tool()
def mark_workout_missed(
    person: str, scheduled_date: str, workout_key: str, reason: str | None = None
) -> dict[str, Any]:
    """Record a missed workout locally; it will not count as a progression failure."""
    value = _service().mark_workout_attendance(
        person=person,
        scheduled_date=date.fromisoformat(scheduled_date),
        workout_key=workout_key,
        status=AttendanceStatus.MISSED,
        reason=reason,
    )
    return value.model_dump(mode="json")


@mcp.tool()
def mark_workout_rescheduled(
    person: str,
    scheduled_date: str,
    workout_key: str,
    rescheduled_to: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Record that a planned workout moved to another date."""
    value = _service().mark_workout_attendance(
        person=person,
        scheduled_date=date.fromisoformat(scheduled_date),
        workout_key=workout_key,
        status=AttendanceStatus.RESCHEDULED,
        rescheduled_to=date.fromisoformat(rescheduled_to),
        reason=reason,
    )
    return value.model_dump(mode="json")


@mcp.tool()
def reconcile_planned_and_completed_workouts(person: str, week: str) -> dict[str, Any]:
    """Reconcile a Monday-starting week across plan, attendance, feedback, and Garmin data."""
    value = _service().reconcile_week(person, date.fromisoformat(week))
    return value.model_dump(mode="json")


@mcp.tool()
def get_week_adherence(person: str, week: str) -> dict[str, int]:
    """Count planned, completed, partial, missed, rescheduled, and unresolved sessions."""
    return _service().reconcile_week(person, date.fromisoformat(week)).adherence


@mcp.tool()
def get_coaching_context(person: str, target_week: str) -> dict[str, Any]:
    """Return the evidence bundle for feedback-aware coaching of a target week."""
    return _service().get_coaching_context(person, date.fromisoformat(target_week))


@mcp.tool()
def propose_next_week(person: str, target_week: str) -> dict[str, Any]:
    """Write a conservative deterministic proposal; never apply it automatically."""
    proposal = _service().propose_coaching_week(person, date.fromisoformat(target_week))
    return proposal.model_dump(mode="json")


@mcp.tool()
def save_coaching_proposal(
    person: str,
    target_week: str,
    summary: str,
    changes: list[dict[str, Any]],
    questions: list[str] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Validate and save an agent-authored proposal for user review; do not apply it."""
    proposal = _service().save_coaching_proposal(
        person=person,
        target_week=date.fromisoformat(target_week),
        summary=summary,
        changes=[CoachChange.model_validate(item) for item in changes],
        questions=questions,
        notes=notes,
    )
    return proposal.model_dump(mode="json")


@mcp.tool()
def get_week_proposal(person: str, target_week: str) -> dict[str, Any]:
    """Read the saved coaching proposal so it can be shown before approval."""
    value = _service().get_coaching_proposal(person, date.fromisoformat(target_week))
    return value.model_dump(mode="json")


@mcp.tool()
def apply_week_proposal(person: str, target_week: str, confirm: bool = False) -> dict[str, Any]:
    """Apply a reviewed proposal locally only when confirm=true."""
    if not confirm:
        return {"applied": False, "reason": "Set confirm=true after reviewing the proposal."}
    value = _service().apply_coaching_proposal(person, date.fromisoformat(target_week))
    return {"applied": True, "proposal": value.model_dump(mode="json")}


@mcp.tool()
def get_weekly_plan(person: str, week: str) -> dict[str, Any]:
    """Read the effective dated workout prescriptions for a Monday-starting week."""
    value = _service().get_weekly_plan(person, date.fromisoformat(week))
    return value.model_dump(mode="json")


@mcp.tool()
def get_pending_checkins(person: str, as_of: str) -> list[dict[str, str]]:
    """List recent planned sessions needing attendance or optional feedback follow-up."""
    return _service().get_pending_checkins(person, date.fromisoformat(as_of))


@mcp.tool()
def get_garmin_diff(person: str, week: str | None = None) -> list[dict[str, Any]]:
    """Read Garmin state and compare it to the canonical local plan."""
    return _service().get_garmin_diff(person, date.fromisoformat(week) if week else None)


@mcp.tool()
def sync_plan_to_garmin(
    person: str,
    week: str | None = None,
    dry_run: bool = True,
    confirm: bool = False,
) -> list[dict[str, Any]]:
    """Sync externally; dry-run is the safe default and a real sync needs confirm=true."""
    if not dry_run and not confirm:
        raise ValueError("A Garmin mutation requires confirm=true")
    return _service().sync_plan_to_garmin(
        person,
        dry_run=dry_run,
        week=date.fromisoformat(week) if week else None,
    )


@mcp.tool()
def schedule_week(
    person: str, week: str, dry_run: bool = True, confirm: bool = False
) -> list[dict[str, str]]:
    """Preview or externally schedule A/B/C/D for a Monday-starting ISO week."""
    if not dry_run and not confirm:
        raise ValueError("A Garmin mutation requires confirm=true")
    return _service().schedule_week(person, date.fromisoformat(week), dry_run=dry_run)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
