from __future__ import annotations

import pytest
from pydantic import ValidationError

from gym_tracker.domain.models import ExercisePrescription, TrainingPlan
from gym_tracker.storage.repository import ProjectRepository, dump_yaml, load_yaml


def test_both_plans_load_and_have_four_full_body_sessions(
    repository: ProjectRepository,
) -> None:
    for person in ("bogdan", "roxana"):
        plan = repository.load_plan(person)
        assert plan.person == person
        assert set(plan.workouts) == {"A", "B", "C", "D"}
        assert set(plan.workout_variants["home"]) == {"A", "B", "C", "D"}
        assert len(plan.weekly_schedule) == 4
        assert all(len(workout.exercises) == 6 for workout in plan.workouts.values())
        assert all(
            len(workout.exercises) == 5 for workout in plan.workout_variants["home"].values()
        )


def test_plan_yaml_round_trip(repository: ProjectRepository) -> None:
    plan = repository.load_plan("bogdan")
    target = repository.root / "round-trip.yaml"
    dump_yaml(target, plan.model_dump(mode="json"))
    reloaded = TrainingPlan.model_validate(load_yaml(target))
    assert reloaded == plan


def test_invalid_rep_range_is_rejected() -> None:
    with pytest.raises(ValidationError, match="rep_range"):
        ExercisePrescription(
            id="test",
            sets=2,
            rep_range=(12, 8),
            target_weight_kg=10,
            rest_seconds=60,
        )


def test_registry_has_unique_reverse_mappings(repository: ProjectRepository) -> None:
    registry = repository.load_registry()
    mapped_count = sum(item.garmin is not None for item in registry.exercises.values())
    assert len(registry.reverse_garmin()) == mapped_count


def test_home_variants_use_known_mapped_exercises(repository: ProjectRepository) -> None:
    registry = repository.load_registry()
    location = repository.load_locations().require("home")
    assert "30 minutes" in " ".join(location.constraints)
    for person in repository.people():
        for workout in repository.load_plan(person).workout_variants["home"].values():
            for prescription in workout.exercises:
                assert registry.require(prescription.id).garmin is not None
