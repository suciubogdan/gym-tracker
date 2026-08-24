from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from gym_tracker.domain.models import (
    CompletedExercise,
    CompletedSet,
    CompletedStrengthWorkout,
    ProgressionAction,
)
from gym_tracker.services import GymService
from gym_tracker.storage.repository import ProjectRepository


def _successful_session(activity_id: str, days_ago: int) -> CompletedStrengthWorkout:
    exercise_id = "barbell_bench_press"
    return CompletedStrengthWorkout(
        person="bogdan",
        garmin_activity_id=activity_id,
        started_at=datetime.now(UTC) - timedelta(days=days_ago),
        workout_name="Bogdan Full Body A",
        exercises=[
            CompletedExercise(
                exercise_id=exercise_id,
                sets=[
                    CompletedSet(
                        exercise_id=exercise_id,
                        set_number=index,
                        reps=12,
                        weight_kg=40,
                    )
                    for index in (1, 2)
                ],
            )
        ],
        imported_at=datetime.now(UTC),
    )


def test_proposal_then_apply_changes_local_plan(repository: ProjectRepository) -> None:
    repository.save_completed(_successful_session("1", 3))
    repository.save_completed(_successful_session("2", 0))
    service = GymService(repository)
    proposal = service.propose_progression("bogdan")
    bench_a = next(
        item
        for item in proposal.changes
        if item.workout_key == "A" and item.exercise_id == "barbell_bench_press"
    )
    assert bench_a.action == ProgressionAction.INCREASE
    assert repository.load_plan("bogdan").workouts["A"].exercises[0].target_weight_kg == 40
    service.apply_progression("bogdan")
    assert repository.load_plan("bogdan").workouts["A"].exercises[0].target_weight_kg == 42.5


def test_apply_rejects_stale_proposal(repository: ProjectRepository) -> None:
    service = GymService(repository)
    service.propose_progression("roxana")
    plan = repository.load_plan("roxana")
    plan.priorities.append("manual_change")
    repository.save_plan(plan)
    with pytest.raises(RuntimeError, match="Plan changed"):
        service.apply_progression("roxana")


def test_plan_views_include_derived_equipment_notes(repository: ProjectRepository) -> None:
    service = GymService(repository)

    plan = service.get_training_plan("bogdan")
    weekly = service.get_weekly_plan_view("bogdan", date(2026, 8, 31))

    assert plan["workouts"]["A"]["equipment_notes"].startswith("Equipment: barbell + bench — 40 kg")
    assert (
        "kettlebell + floor — 16 kg & 24 kg"
        in plan["workout_variants"]["home"]["A"]["equipment_notes"]
    )
    assert weekly["sessions"][0]["location"] == "gym"
    assert weekly["sessions"][0]["equipment_notes"] == plan["workouts"]["A"]["equipment_notes"]
