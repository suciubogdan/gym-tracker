from __future__ import annotations

import getpass
import json
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer

from gym_tracker.domain.models import (
    AttendanceStatus,
    ExerciseFeedback,
    OverallFeedback,
)
from gym_tracker.garmin.adapter import login_account, search_exercise_catalog
from gym_tracker.logging import configure_logging
from gym_tracker.services import GymService
from gym_tracker.storage.repository import ProjectRepository, find_project_root

app = typer.Typer(no_args_is_help=True, help="Local-first strength training manager.")
garmin_app = typer.Typer(no_args_is_help=True, help="Explicit Garmin Connect operations.")
garmin_exercises_app = typer.Typer(no_args_is_help=True, help="Inspect Garmin's exercise catalog.")
progress_app = typer.Typer(no_args_is_help=True, help="Deterministic progression proposals.")
coach_app = typer.Typer(no_args_is_help=True, help="Feedback-aware weekly coaching workflow.")
app.add_typer(garmin_app, name="garmin")
garmin_app.add_typer(garmin_exercises_app, name="exercises")
app.add_typer(progress_app, name="progress")
app.add_typer(coach_app, name="coach")

_root: Path | None = None


class FeedbackStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"


def _service() -> GymService:
    return GymService(ProjectRepository(_root or find_project_root()))


def _emit(value: Any, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(value, indent=2, default=str))
        return
    if isinstance(value, list):
        for item in value:
            typer.echo(_format_item(item))
    elif isinstance(value, dict):
        typer.echo(_format_item(value))
    else:
        typer.echo(str(value))


def _format_item(item: dict[str, Any]) -> str:
    if "action" in item and "exercise_id" in item:
        return (
            f"{item['workout_key']} · {item['exercise_id']}: "
            f"{item['old_weight_kg']:g} → {item['new_weight_kg']:g} kg "
            f"[{str(item['action']).upper()}]\n  {item['reason']}"
        )
    if "action" in item and "workout_key" in item:
        return (
            f"{item['workout_key']} · {item['workout_name']}: "
            f"{str(item['action']).upper()} — {item['reason']}"
        )
    return json.dumps(item, indent=2, default=str)


def _parse_since(value: str) -> int:
    normalized = value.strip().lower()
    if not normalized.endswith("d") or not normalized[:-1].isdigit():
        raise typer.BadParameter("Use a duration such as 7d or 14d")
    days = int(normalized[:-1])
    if days < 1:
        raise typer.BadParameter("Duration must be at least 1d")
    return days


def _parse_date(value: str, option: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"{option} must be an ISO date such as 2026-08-31") from exc


def _parse_week(value: str) -> date:
    week = _parse_date(value, "--week")
    if week.weekday() != 0:
        raise typer.BadParameter("--week must be a Monday")
    return week


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    root: Annotated[Path | None, typer.Option(hidden=True)] = None,
) -> None:
    """Manage canonical local plans; Garmin writes require explicit --execute."""
    global _root
    _root = root
    configure_logging(verbose)


@app.command()
def status(
    person: str,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show deterministic recommendations without changing a plan."""
    value = _service().get_training_status(person)
    if json_output:
        _emit(value, True)
    else:
        typer.echo(f"{person.title()} · {value['completed_sessions']} imported sessions")
        for item in value["recommendations"]:
            typer.echo(_format_item(item))


@app.command("import")
def import_command(
    person: Annotated[str | None, typer.Option("--person")] = None,
    all_people: Annotated[bool, typer.Option("--all")] = False,
    since: Annotated[str, typer.Option("--since")] = "7d",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Import completed Garmin strength sessions idempotently."""
    service = _service()
    if person is None and not all_people:
        raise typer.BadParameter("Specify --person NAME or --all")
    if person is not None and all_people:
        raise typer.BadParameter("Use either --person or --all, not both")
    people = service.repository.people() if all_people else [str(person)]
    results = {name: service.import_workouts(name, _parse_since(since)) for name in people}
    _emit(results, json_output)


@progress_app.command("propose")
def progress_propose(
    person: str, json_output: Annotated[bool, typer.Option("--json")] = False
) -> None:
    """Write a reviewable proposal, never the canonical plan."""
    proposal = _service().propose_progression(person)
    _emit([item.model_dump(mode="json") for item in proposal.changes], json_output)


@progress_app.command("apply")
def progress_apply(
    person: str, json_output: Annotated[bool, typer.Option("--json")] = False
) -> None:
    """Apply the latest non-stale proposal to local YAML only."""
    proposal = _service().apply_progression(person)
    result = {"person": person, "applied": True, "changes": len(proposal.changes)}
    _emit(result, json_output)


@coach_app.command("feedback")
def coach_feedback(
    person: str,
    scheduled_date: Annotated[str, typer.Option("--date")],
    workout: Annotated[str, typer.Option("--workout")],
    status: Annotated[FeedbackStatus, typer.Option()] = FeedbackStatus.COMPLETED,
    activity_id: Annotated[str | None, typer.Option("--activity-id")] = None,
    energy: Annotated[int | None, typer.Option(min=1, max=5)] = None,
    difficulty: Annotated[int | None, typer.Option(min=1, max=5)] = None,
    enjoyment: Annotated[int | None, typer.Option(min=1, max=5)] = None,
    recovery: Annotated[int | None, typer.Option(min=1, max=5)] = None,
    pain: Annotated[bool, typer.Option("--pain/--no-pain")] = False,
    pain_notes: Annotated[str | None, typer.Option()] = None,
    notes: Annotated[str | None, typer.Option()] = None,
    exercise: Annotated[
        list[str] | None,
        typer.Option(
            "--exercise",
            help="Repeat with JSON exercise feedback, for example "
            '\'{"exercise_id":"goblet_squat","difficulty":"too_hard"}\'.',
        ),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Record structured subjective feedback and attendance locally."""
    try:
        exercises = [ExerciseFeedback.model_validate_json(item) for item in exercise or []]
    except ValueError as exc:
        raise typer.BadParameter(f"Invalid --exercise JSON: {exc}") from exc
    feedback = _service().record_workout_feedback(
        person=person,
        scheduled_date=_parse_date(scheduled_date, "--date"),
        workout_key=workout,
        status=AttendanceStatus(status.value),
        garmin_activity_id=activity_id,
        overall=OverallFeedback(
            energy=energy,
            difficulty=difficulty,
            enjoyment=enjoyment,
            recovery=recovery,
            pain_or_discomfort=pain,
            pain_notes=pain_notes,
            notes=notes,
        ),
        exercises=exercises,
    )
    _emit(feedback.model_dump(mode="json"), json_output)


@coach_app.command("missed")
def coach_missed(
    person: str,
    scheduled_date: Annotated[str, typer.Option("--date")],
    workout: Annotated[str, typer.Option("--workout")],
    reason: Annotated[str | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Mark a planned workout missed; it is not treated as a failed session."""
    record = _service().mark_workout_attendance(
        person=person,
        scheduled_date=_parse_date(scheduled_date, "--date"),
        workout_key=workout,
        status=AttendanceStatus.MISSED,
        reason=reason,
    )
    _emit(record.model_dump(mode="json"), json_output)


@coach_app.command("reschedule")
def coach_reschedule(
    person: str,
    scheduled_date: Annotated[str, typer.Option("--date")],
    workout: Annotated[str, typer.Option("--workout")],
    to: Annotated[str, typer.Option("--to")],
    reason: Annotated[str | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Record that a planned workout moved to another date."""
    record = _service().mark_workout_attendance(
        person=person,
        scheduled_date=_parse_date(scheduled_date, "--date"),
        workout_key=workout,
        status=AttendanceStatus.RESCHEDULED,
        rescheduled_to=_parse_date(to, "--to"),
        reason=reason,
    )
    _emit(record.model_dump(mode="json"), json_output)


@coach_app.command("reconcile")
def coach_reconcile(
    person: str,
    week: Annotated[str, typer.Option("--week")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Reconcile planned sessions, attendance, feedback, and imported Garmin history."""
    value = _service().reconcile_week(person, _parse_week(week))
    _emit(value.model_dump(mode="json"), json_output)


@coach_app.command("context")
def coach_context(
    person: str,
    week: Annotated[str, typer.Option("--week")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Read the evidence bundle an agent should use to coach the target week."""
    _emit(_service().get_coaching_context(person, _parse_week(week)), json_output)


@coach_app.command("propose")
def coach_propose(
    person: str,
    week: Annotated[str, typer.Option("--week")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create a safe deterministic proposal for a target week without applying it."""
    proposal = _service().propose_coaching_week(person, _parse_week(week))
    _emit(proposal.model_dump(mode="json"), json_output)


@coach_app.command("proposal")
def coach_proposal(
    person: str,
    week: Annotated[str, typer.Option("--week")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the saved proposal for a target week."""
    proposal = _service().get_coaching_proposal(person, _parse_week(week))
    _emit(proposal.model_dump(mode="json"), json_output)


@coach_app.command("apply")
def coach_apply(
    person: str,
    week: Annotated[str, typer.Option("--week")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Apply a previously reviewed proposal to local base/weekly YAML."""
    proposal = _service().apply_coaching_proposal(person, _parse_week(week))
    _emit(proposal.model_dump(mode="json"), json_output)


@coach_app.command("plan")
def coach_plan(
    person: str,
    week: Annotated[str, typer.Option("--week")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show the effective dated prescription for one week."""
    plan = _service().get_weekly_plan(person, _parse_week(week))
    _emit(plan.model_dump(mode="json"), json_output)


@coach_app.command("check-in")
def coach_check_in(
    person: Annotated[str | None, typer.Option("--person")] = None,
    all_people: Annotated[bool, typer.Option("--all")] = False,
    as_of: Annotated[str | None, typer.Option("--as-of")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List recent sessions that need attendance or subjective feedback."""
    service = _service()
    if person is None and not all_people:
        raise typer.BadParameter("Specify --person NAME or --all")
    if person is not None and all_people:
        raise typer.BadParameter("Use either --person or --all, not both")
    people = service.repository.people() if all_people else [str(person)]
    check_date = date.today() if as_of is None else _parse_date(as_of, "--as-of")
    values = [item for name in people for item in service.get_pending_checkins(name, check_date)]
    _emit(values, json_output)


@garmin_app.command("login")
def garmin_login(person: str) -> None:
    """Authenticate interactively and store private tokens outside Git."""
    repository = _service().repository
    if person not in repository.people():
        raise typer.BadParameter(f"Unknown person {person!r}")
    email = typer.prompt("Garmin email")
    password = getpass.getpass("Garmin password: ")
    login_account(person, email, password, lambda: typer.prompt("Garmin MFA code"))
    typer.echo(f"Authenticated Garmin account for {person}; credentials were not stored.")


@garmin_exercises_app.command("search")
def garmin_exercise_search(
    term: str, json_output: Annotated[bool, typer.Option("--json")] = False
) -> None:
    """Search the catalog bundled by garminconnect; do not guess enum values."""
    _emit(search_exercise_catalog(term), json_output)


@garmin_app.command("diff")
def garmin_diff(
    person: str,
    week: Annotated[str | None, typer.Option("--week")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Compare canonical local workouts to synchronized Garmin ids/hashes."""
    _emit(
        _service().get_garmin_diff(person, _parse_week(week) if week else None),
        json_output,
    )


@garmin_app.command("sync")
def garmin_sync(
    person: str,
    week: Annotated[str | None, typer.Option("--week")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run/--execute")] = True,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Synchronize workouts. Defaults to a non-mutating dry run."""
    _emit(
        _service().sync_plan_to_garmin(
            person, dry_run=dry_run, week=_parse_week(week) if week else None
        ),
        json_output,
    )


@garmin_app.command("schedule")
def garmin_schedule(
    person: str,
    week: Annotated[str, typer.Option("--week")],
    dry_run: Annotated[bool, typer.Option("--dry-run/--execute")] = True,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Schedule the configured week. Defaults to a non-mutating preview."""
    _emit(_service().schedule_week(person, _parse_week(week), dry_run=dry_run), json_output)


if __name__ == "__main__":
    app()
