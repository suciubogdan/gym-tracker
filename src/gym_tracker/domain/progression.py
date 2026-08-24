from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from gym_tracker.domain.models import (
    CompletedSet,
    CompletedStrengthWorkout,
    ExerciseDefinition,
    ExercisePrescription,
    ProgressionAction,
    ProgressionChange,
    TrainingPlan,
)


@dataclass(frozen=True)
class ProgressionSettings:
    default_max_load_increase_percent: float = 10.0
    conservative_successful_sessions_required: int = 2
    failed_sessions_before_regression: int = 2
    enable_regression: bool = False


def round_to_increment(value: float, increment: float) -> float:
    """Round to the closest configured plate/stack increment deterministically."""
    value_d = Decimal(str(value))
    increment_d = Decimal(str(increment))
    units = (value_d / increment_d).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(units * increment_d)


def _relevant_sets(
    history: list[CompletedStrengthWorkout], exercise_id: str
) -> list[list[CompletedSet]]:
    sessions: list[list[CompletedSet]] = []
    for workout in sorted(history, key=lambda item: item.started_at, reverse=True):
        for exercise in workout.exercises:
            if exercise.exercise_id == exercise_id:
                sessions.append(exercise.sets)
    return sessions


def evaluate_exercise(
    workout_key: str,
    prescription: ExercisePrescription,
    definition: ExerciseDefinition,
    history: list[CompletedStrengthWorkout],
    *,
    introductory: bool,
    settings: ProgressionSettings,
) -> ProgressionChange:
    old = prescription.target_weight_kg
    if prescription.manual_override:
        return ProgressionChange(
            workout_key=workout_key,
            exercise_id=prescription.id,
            old_weight_kg=old,
            new_weight_kg=old,
            action=ProgressionAction.MANUAL,
            reason="manual override enabled",
        )

    sessions = _relevant_sets(history, prescription.id)
    if not sessions:
        return ProgressionChange(
            workout_key=workout_key,
            exercise_id=prescription.id,
            old_weight_kg=old,
            new_weight_kg=old,
            action=ProgressionAction.NO_DATA,
            reason="no completed sets imported",
        )

    minimum, maximum = prescription.rep_range

    def successful(sets: list[CompletedSet]) -> bool:
        working = sets[: prescription.sets]
        return len(working) == prescription.sets and all(
            item.reps is not None
            and item.reps >= maximum
            and item.weight_kg is not None
            and abs(item.weight_kg - old) < 0.011
            for item in working
        )

    required = settings.conservative_successful_sessions_required if introductory else 1
    if len(sessions) >= required and all(successful(item) for item in sessions[:required]):
        proposed = round_to_increment(old + definition.increment_kg, definition.increment_kg)
        increase_percent = 0.0 if old == 0 else ((proposed - old) / old) * 100
        if old == 0 or increase_percent > settings.default_max_load_increase_percent:
            return ProgressionChange(
                workout_key=workout_key,
                exercise_id=prescription.id,
                old_weight_kg=old,
                new_weight_kg=old,
                action=ProgressionAction.REVIEW,
                reason=(
                    f"proposed {proposed:g} kg exceeds "
                    f"{settings.default_max_load_increase_percent:g}% safety limit"
                ),
                requires_review=True,
            )
        phase_reason = f" across {required} sessions" if required > 1 else ""
        return ProgressionChange(
            workout_key=workout_key,
            exercise_id=prescription.id,
            old_weight_kg=old,
            new_weight_kg=proposed,
            action=ProgressionAction.INCREASE,
            reason=f"reached {maximum} reps on all {prescription.sets} sets{phase_reason}",
        )

    latest = sessions[0][: prescription.sets]
    failed = any(item.reps is not None and item.reps < minimum for item in latest)
    if failed and settings.enable_regression:
        count = settings.failed_sessions_before_regression
        enough_failures = len(sessions) >= count and all(
            any(item.reps is not None and item.reps < minimum for item in session)
            for session in sessions[:count]
        )
        if enough_failures:
            regressed = max(
                0.0,
                round_to_increment(old - definition.increment_kg, definition.increment_kg),
            )
            return ProgressionChange(
                workout_key=workout_key,
                exercise_id=prescription.id,
                old_weight_kg=old,
                new_weight_kg=regressed,
                action=ProgressionAction.REGRESS,
                reason=f"fell below {minimum} reps in {count} consecutive sessions",
            )

    reps = "/".join("?" if item.reps is None else str(item.reps) for item in latest)
    return ProgressionChange(
        workout_key=workout_key,
        exercise_id=prescription.id,
        old_weight_kg=old,
        new_weight_kg=old,
        action=ProgressionAction.MAINTAIN,
        reason=f"latest reps {reps}; continue within {minimum}-{maximum}",
    )


def propose_progression(
    plan: TrainingPlan,
    registry: dict[str, ExerciseDefinition],
    history: list[CompletedStrengthWorkout],
    settings: ProgressionSettings,
) -> list[ProgressionChange]:
    completed_sessions = len(history)
    introductory = (
        plan.phase.type == "introduction"
        and plan.phase.progression_aggressiveness == "conservative"
        and completed_sessions < plan.phase.sessions
    )
    changes: list[ProgressionChange] = []
    for workout_key, workout in plan.workouts.items():
        for prescription in workout.exercises:
            if prescription.id not in registry:
                raise ValueError(f"Unknown exercise {prescription.id!r} in workout {workout_key}")
            changes.append(
                evaluate_exercise(
                    workout_key,
                    prescription,
                    registry[prescription.id],
                    history,
                    introductory=introductory,
                    settings=settings,
                )
            )
    return changes


def apply_changes(plan: TrainingPlan, changes: list[ProgressionChange]) -> TrainingPlan:
    updated = plan.model_copy(deep=True)
    by_key: dict[str, dict[str, float]] = defaultdict(dict)
    for change in changes:
        if change.requires_review:
            continue
        by_key[change.workout_key][change.exercise_id] = change.new_weight_kg
    for workout_key, exercise_weights in by_key.items():
        for exercise in updated.workouts[workout_key].exercises:
            if exercise.id in exercise_weights:
                exercise.target_weight_kg = exercise_weights[exercise.id]
    return updated
