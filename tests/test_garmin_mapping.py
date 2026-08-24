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


def test_missing_mapping_fails_before_upload(repository: ProjectRepository) -> None:
    workout = repository.load_plan("bogdan").workouts["A"]
    raw = deepcopy(repository.load_registry().model_dump())
    raw["exercises"]["barbell_bench_press"]["garmin"] = None
    registry = ExerciseRegistry.model_validate(raw)
    with pytest.raises(UnmappedExerciseError, match="no verified Garmin mapping"):
        serialize_strength_workout(workout, registry)
