from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gym_tracker.domain.models import (
    CompletedExercise,
    CompletedSet,
    CompletedStrengthWorkout,
    ExerciseDefinition,
    ExercisePrescription,
    ProgressionAction,
)
from gym_tracker.domain.progression import (
    ProgressionSettings,
    evaluate_exercise,
    round_to_increment,
)


def prescription(weight: float = 40) -> ExercisePrescription:
    return ExercisePrescription(
        id="barbell_bench_press",
        sets=2,
        rep_range=(8, 12),
        target_weight_kg=weight,
        rest_seconds=120,
    )


def definition(increment: float = 2.5) -> ExerciseDefinition:
    return ExerciseDefinition(
        display_name="Bench",
        muscle_groups=["chest"],
        increment_kg=increment,
    )


def session(
    reps: tuple[int, int], weight: float = 40, days_ago: int = 0
) -> CompletedStrengthWorkout:
    exercise_id = "barbell_bench_press"
    return CompletedStrengthWorkout(
        person="bogdan",
        garmin_activity_id=str(100 + days_ago),
        started_at=datetime.now(UTC) - timedelta(days=days_ago),
        workout_name="A",
        exercises=[
            CompletedExercise(
                exercise_id=exercise_id,
                sets=[
                    CompletedSet(
                        exercise_id=exercise_id,
                        set_number=index + 1,
                        reps=value,
                        weight_kg=weight,
                    )
                    for index, value in enumerate(reps)
                ],
            )
        ],
        imported_at=datetime.now(UTC),
    )


def evaluate(
    history: list[CompletedStrengthWorkout],
    *,
    introductory: bool = False,
    settings: ProgressionSettings | None = None,
    weight: float = 40,
) -> object:
    return evaluate_exercise(
        "A",
        prescription(weight),
        definition(),
        history,
        introductory=introductory,
        settings=settings or ProgressionSettings(),
    )


def test_double_progression_increases_after_top_reps() -> None:
    result = evaluate([session((12, 12))])
    assert result.action == ProgressionAction.INCREASE  # type: ignore[attr-defined]
    assert result.new_weight_kg == 42.5  # type: ignore[attr-defined]


def test_introductory_phase_requires_two_successes() -> None:
    first = evaluate([session((12, 12))], introductory=True)
    second = evaluate([session((12, 12)), session((12, 12), days_ago=3)], introductory=True)
    assert first.action == ProgressionAction.MAINTAIN  # type: ignore[attr-defined]
    assert second.action == ProgressionAction.INCREASE  # type: ignore[attr-defined]


def test_failed_set_maintains_by_default() -> None:
    result = evaluate([session((8, 7))])
    assert result.action == ProgressionAction.MAINTAIN  # type: ignore[attr-defined]
    assert result.new_weight_kg == 40  # type: ignore[attr-defined]


def test_optional_regression_requires_consecutive_failures() -> None:
    settings = ProgressionSettings(enable_regression=True)
    result = evaluate([session((7, 7)), session((6, 7), days_ago=3)], settings=settings)
    assert result.action == ProgressionAction.REGRESS  # type: ignore[attr-defined]
    assert result.new_weight_kg == 37.5  # type: ignore[attr-defined]


def test_large_percentage_increase_requires_review() -> None:
    result = evaluate([session((12, 12), weight=10)], weight=10)
    assert result.action == ProgressionAction.REVIEW  # type: ignore[attr-defined]
    assert result.requires_review is True  # type: ignore[attr-defined]
    assert result.new_weight_kg == 10  # type: ignore[attr-defined]


def test_increment_rounding_is_half_up() -> None:
    assert round_to_increment(61.25, 2.5) == 62.5
    assert round_to_increment(60.9, 1) == 61
