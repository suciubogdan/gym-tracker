from __future__ import annotations

from typing import Any

from gym_tracker.domain.models import ExerciseRegistry, PlannedWorkout

GARMIN_SERIALIZER_VERSION = "strength-weight-value-kg-v2"


class UnmappedExerciseError(ValueError):
    pass


def serialize_strength_workout(
    workout: PlannedWorkout, registry: ExerciseRegistry
) -> dict[str, Any]:
    """Build a Garmin payload through garminconnect's maintained typed models."""
    from garminconnect.workout import (  # type: ignore[import-untyped]
        StrengthWorkout,
        WorkoutSegment,
        create_strength_set,
    )

    steps = []
    order = 1
    estimated_seconds = 0
    for item in workout.exercises:
        definition = registry.require(item.id)
        if definition.garmin is None:
            raise UnmappedExerciseError(
                f"{item.id!r} has no verified Garmin mapping; add one after catalog inspection"
            )
        mapping = definition.garmin
        steps.append(
            create_strength_set(
                mapping.category,
                step_order=order,
                sets=item.sets,
                reps=item.rep_range[0],
                rest_seconds=item.rest_seconds,
                exercise_name=mapping.exercise,
                weight_kg=item.target_weight_kg,
            )
        )
        order += 3
        estimated_seconds += item.sets * (40 + item.rest_seconds)
    typed = StrengthWorkout(
        workoutName=workout.name,
        estimatedDurationInSecs=estimated_seconds,
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType={"sportTypeId": 5, "sportTypeKey": "strength_training"},
                workoutSteps=steps,
            )
        ],
    )
    payload = dict(typed.to_dict())

    # garminconnect 0.3.11 multiplies this field by the kilogram unit factor
    # (1,000), but Garmin's workout API/UI interprets weightValue itself as kg.
    # Preserve the library's verified workout shape and unit metadata while
    # correcting the value using our canonical kilogram targets.
    serialized_blocks = payload["workoutSegments"][0]["workoutSteps"]
    for item, block in zip(workout.exercises, serialized_blocks, strict=True):
        block["workoutSteps"][0]["weightValue"] = float(item.target_weight_kg)

    return payload
