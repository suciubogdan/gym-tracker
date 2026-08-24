from __future__ import annotations

from copy import deepcopy

import pytest

from gym_tracker.domain.models import ExerciseRegistry
from gym_tracker.garmin.serializer import UnmappedExerciseError, serialize_strength_workout
from gym_tracker.storage.repository import ProjectRepository


def test_serializer_uses_verified_mapping_and_weight_kilograms(
    repository: ProjectRepository,
) -> None:
    workout = repository.load_plan("bogdan").workouts["A"]
    payload = serialize_strength_workout(workout, repository.load_registry())
    repeat = payload["workoutSegments"][0]["workoutSteps"][0]
    exercise = repeat["workoutSteps"][0]
    assert exercise["category"] == "BENCH_PRESS"
    assert exercise["exerciseName"] == "BARBELL_BENCH_PRESS"
    # Garmin's workout API interprets weightValue as kilograms. A value of
    # 40_000 renders as 40.000,0 kg in locales using a decimal comma.
    assert exercise["weightValue"] == 40
    assert exercise["endConditionValue"] == 8
    assert payload["description"].startswith("Equipment: barbell + bench — 40 kg")
    assert "cable machine + lat pulldown — 35 kg setting" in payload["description"]


def test_missing_mapping_fails_before_upload(repository: ProjectRepository) -> None:
    workout = repository.load_plan("bogdan").workouts["A"]
    raw = deepcopy(repository.load_registry().model_dump())
    raw["exercises"]["barbell_bench_press"]["garmin"] = None
    registry = ExerciseRegistry.model_validate(raw)
    with pytest.raises(UnmappedExerciseError, match="no verified Garmin mapping"):
        serialize_strength_workout(workout, registry)


def test_home_variants_serialize_under_thirty_minutes(
    repository: ProjectRepository,
) -> None:
    registry = repository.load_registry()
    for person in repository.people():
        variants = repository.load_plan(person).workout_variants["home"]
        for workout in variants.values():
            payload = serialize_strength_workout(workout, registry)
            assert payload["estimatedDurationInSecs"] < 30 * 60
            assert payload["description"].startswith("Equipment: ")
            assert len(payload["description"]) < 1024


def test_home_equipment_notes_include_exact_loads_and_venue_overrides(
    repository: ProjectRepository,
) -> None:
    workout = repository.load_plan("bogdan").workout_variants["home"]["A"]
    payload = serialize_strength_workout(workout, repository.load_registry())
    description = payload["description"]

    assert "dumbbells + floor — 10 kg each" in description
    assert "resistance band + pull-up bar — choose resistance" in description
    assert "kettlebell + floor — 16 kg & 24 kg" in description
    assert "dumbbells + floor or couch — 8 kg each" in description
