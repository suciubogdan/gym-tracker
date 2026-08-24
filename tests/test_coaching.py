from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from gym_tracker.coaching.service import CoachingService
from gym_tracker.domain.models import (
    AttendanceStatus,
    CoachChange,
    CoachChangeKind,
    CoachChangeScope,
    CompletedExercise,
    CompletedSet,
    CompletedStrengthWorkout,
    OverallFeedback,
)
from gym_tracker.garmin.fake import FakeGarminClient
from gym_tracker.garmin.sync import GarminSyncService
from gym_tracker.storage.repository import ProjectRepository


def _bench_session(activity_id: str, started_at: datetime) -> CompletedStrengthWorkout:
    return CompletedStrengthWorkout(
        person="bogdan",
        garmin_activity_id=activity_id,
        started_at=started_at,
        workout_name="Bogdan Full Body A",
        exercises=[
            CompletedExercise(
                exercise_id="barbell_bench_press",
                sets=[
                    CompletedSet(
                        exercise_id="barbell_bench_press",
                        set_number=set_number,
                        reps=12,
                        weight_kg=40,
                    )
                    for set_number in (1, 2)
                ],
            )
        ],
        imported_at=started_at,
    )


def test_reconciliation_combines_garmin_attendance_and_missing_feedback(
    repository: ProjectRepository,
) -> None:
    week = date(2026, 8, 24)
    repository.save_completed(_bench_session("activity-a", datetime(2026, 8, 24, tzinfo=UTC)))
    coach = CoachingService(repository)
    coach.mark_attendance(
        person="bogdan",
        scheduled_date=date(2026, 8, 25),
        workout_key="B",
        status=AttendanceStatus.MISSED,
        reason="travel",
    )

    result = coach.reconcile_week("bogdan", week, as_of=week)
    by_key = {item.workout_key: item for item in result.sessions}

    assert by_key["A"].status == AttendanceStatus.COMPLETED
    assert by_key["A"].feedback_missing is True
    assert by_key["B"].status == AttendanceStatus.MISSED
    assert by_key["B"].reason == "travel"
    assert by_key["C"].status == AttendanceStatus.PLANNED
    assert result.adherence["completed"] == 1
    assert result.adherence["missed"] == 1


def test_missing_feedback_does_not_block_deterministic_progression(
    repository: ProjectRepository,
) -> None:
    target_week = date(2026, 8, 31)
    repository.save_completed(_bench_session("one", datetime(2026, 8, 20, tzinfo=UTC)))
    repository.save_completed(_bench_session("two", datetime(2026, 8, 24, tzinfo=UTC)))

    proposal = CoachingService(repository).propose_week("bogdan", target_week)
    bench = next(
        item
        for item in proposal.changes
        if item.workout_key == "A" and item.exercise_id == "barbell_bench_press"
    )

    assert bench.old_value == 40
    assert bench.new_value == 42.5
    assert "missing feedback" in proposal.summary


def test_explicit_missed_attendance_is_not_overridden_by_fuzzy_activity_match(
    repository: ProjectRepository,
) -> None:
    week = date(2026, 8, 24)
    repository.save_completed(_bench_session("activity-a", datetime(2026, 8, 24, tzinfo=UTC)))
    coach = CoachingService(repository)
    coach.mark_attendance(
        person="bogdan",
        scheduled_date=week,
        workout_key="A",
        status=AttendanceStatus.MISSED,
    )

    result = coach.reconcile_week("bogdan", week, as_of=week)
    session = next(item for item in result.sessions if item.workout_key == "A")

    assert session.status == AttendanceStatus.MISSED
    assert result.unscheduled_activity_ids == ["activity-a"]


def test_feedback_after_reschedule_preserves_effective_date(
    repository: ProjectRepository,
) -> None:
    week = date(2026, 8, 24)
    coach = CoachingService(repository)
    coach.mark_attendance(
        person="bogdan",
        scheduled_date=week,
        workout_key="A",
        status=AttendanceStatus.RESCHEDULED,
        rescheduled_to=date(2026, 8, 26),
        reason="work conflict",
    )
    coach.record_feedback(
        person="bogdan",
        scheduled_date=week,
        workout_key="A",
        status=AttendanceStatus.COMPLETED,
        overall=OverallFeedback(difficulty=3),
    )

    result = coach.reconcile_week("bogdan", week, as_of=date(2026, 8, 26))
    session = next(item for item in result.sessions if item.workout_key == "A")

    assert session.status == AttendanceStatus.COMPLETED
    assert session.effective_date == date(2026, 8, 26)
    assert session.reason == "work conflict"


def test_pain_feedback_suppresses_an_otherwise_valid_increase(
    repository: ProjectRepository,
) -> None:
    target_week = date(2026, 8, 31)
    repository.save_completed(_bench_session("one", datetime(2026, 8, 20, tzinfo=UTC)))
    repository.save_completed(_bench_session("two", datetime(2026, 8, 24, tzinfo=UTC)))
    coach = CoachingService(repository)
    coach.record_feedback(
        person="bogdan",
        scheduled_date=date(2026, 8, 24),
        workout_key="A",
        status=AttendanceStatus.COMPLETED,
        overall=OverallFeedback(pain_or_discomfort=True, pain_notes="shoulder"),
    )

    proposal = coach.propose_week("bogdan", target_week)

    assert not any(
        item.workout_key == "A" and item.exercise_id == "barbell_bench_press"
        for item in proposal.changes
    )
    assert any("reported pain" in note for note in proposal.notes)


def test_week_scoped_change_updates_snapshot_but_not_base_plan(
    repository: ProjectRepository,
) -> None:
    target_week = date(2026, 8, 31)
    coach = CoachingService(repository)
    coach.save_proposal(
        person="bogdan",
        target_week=target_week,
        summary="Use a lighter bench for this week only.",
        changes=[
            CoachChange(
                kind=CoachChangeKind.LOAD,
                scope=CoachChangeScope.WEEK,
                workout_key="A",
                exercise_id="barbell_bench_press",
                old_value=40,
                new_value=37.5,
                rationale="Temporary recovery adjustment",
                evidence=["user reported poor recovery"],
            )
        ],
    )

    coach.apply_proposal("bogdan", target_week)
    weekly = repository.load_weekly_plan("bogdan", target_week)

    assert weekly is not None
    assert weekly.sessions[0].workout.exercises[0].target_weight_kg == 37.5
    assert repository.load_plan("bogdan").workouts["A"].exercises[0].target_weight_kg == 40


def test_weekly_garmin_sync_is_required_before_scheduling_adjusted_plan(
    repository: ProjectRepository,
) -> None:
    target_week = date(2026, 8, 31)
    coach = CoachingService(repository)
    coach.save_proposal(
        person="bogdan",
        target_week=target_week,
        summary="One-week recovery adjustment.",
        changes=[
            CoachChange(
                kind=CoachChangeKind.LOAD,
                scope=CoachChangeScope.WEEK,
                workout_key="A",
                exercise_id="barbell_bench_press",
                old_value=40,
                new_value=37.5,
                rationale="Recovery",
            )
        ],
    )
    coach.apply_proposal("bogdan", target_week)
    client = FakeGarminClient()
    garmin = GarminSyncService(repository, client)
    garmin.sync("bogdan", dry_run=False)

    with pytest.raises(RuntimeError, match="target week's prescription"):
        garmin.schedule_week("bogdan", target_week)

    garmin.sync("bogdan", dry_run=False, week_start=target_week)
    preview = garmin.schedule_week("bogdan", target_week)
    assert [item["date"] for item in preview] == [
        "2026-08-31",
        "2026-09-01",
        "2026-09-03",
        "2026-09-05",
    ]


def test_schedule_changes_are_revalidated_for_duplicate_dates(
    repository: ProjectRepository,
) -> None:
    target_week = date(2026, 8, 31)
    coach = CoachingService(repository)
    coach.save_proposal(
        person="bogdan",
        target_week=target_week,
        summary="Bad schedule should fail atomically.",
        changes=[
            CoachChange(
                kind=CoachChangeKind.SCHEDULE,
                scope=CoachChangeScope.WEEK,
                workout_key="A",
                old_value="2026-08-31",
                new_value="2026-09-01",
                rationale="Conflict test",
            )
        ],
    )

    with pytest.raises(ValueError, match="duplicate dates"):
        coach.apply_proposal("bogdan", target_week)
    assert repository.load_weekly_plan("bogdan", target_week) is None


def test_excessive_agent_load_change_is_flagged_and_not_applied(
    repository: ProjectRepository,
) -> None:
    target_week = date(2026, 8, 31)
    coach = CoachingService(repository)
    proposal = coach.save_proposal(
        person="bogdan",
        target_week=target_week,
        summary="Unsafe jump should require human review.",
        changes=[
            CoachChange(
                kind=CoachChangeKind.LOAD,
                scope=CoachChangeScope.ONGOING,
                workout_key="A",
                exercise_id="barbell_bench_press",
                old_value=40,
                new_value=50,
                rationale="Deliberately excessive test change",
            )
        ],
    )

    assert proposal.changes[0].requires_review is True
    coach.apply_proposal("bogdan", target_week)
    weekly = repository.load_weekly_plan("bogdan", target_week)
    assert weekly is not None
    assert weekly.sessions[0].workout.exercises[0].target_weight_kg == 40
    assert repository.load_plan("bogdan").workouts["A"].exercises[0].target_weight_kg == 40
