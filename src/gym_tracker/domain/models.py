from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GarminExerciseMapping(DomainModel):
    category: str = Field(pattern=r"^[A-Z0-9_]+$")
    exercise: str = Field(pattern=r"^[A-Z0-9_]+$")


class Equipment(DomainModel):
    type: str
    station: str | None = None


class ExerciseDefinition(DomainModel):
    display_name: str
    muscle_groups: list[str]
    increment_kg: float = Field(gt=0)
    equipment: Equipment | None = None
    garmin: GarminExerciseMapping | None = None


class ExerciseRegistry(DomainModel):
    exercises: dict[str, ExerciseDefinition]

    def require(self, exercise_id: str) -> ExerciseDefinition:
        try:
            return self.exercises[exercise_id]
        except KeyError as exc:
            raise ValueError(f"Unknown internal exercise id: {exercise_id}") from exc

    def reverse_garmin(self) -> dict[tuple[str, str], str]:
        result: dict[tuple[str, str], str] = {}
        for exercise_id, definition in self.exercises.items():
            if definition.garmin:
                result[(definition.garmin.category, definition.garmin.exercise)] = exercise_id
        return result


class ExercisePrescription(DomainModel):
    id: str
    sets: int = Field(ge=1, le=10)
    rep_range: tuple[int, int]
    target_weight_kg: float = Field(ge=0)
    progression: str = "double_progression"
    rest_seconds: int = Field(ge=0, le=900)
    pairing_key: str | None = None
    manual_override: bool = False

    @field_validator("rep_range")
    @classmethod
    def valid_rep_range(cls, value: tuple[int, int]) -> tuple[int, int]:
        if value[0] < 1 or value[1] < value[0]:
            raise ValueError("rep_range must be [minimum, maximum] with minimum >= 1")
        return value


class PlannedWorkout(DomainModel):
    name: str
    emphasis: list[str] = Field(default_factory=list)
    exercises: list[ExercisePrescription]

    @model_validator(mode="after")
    def unique_exercises(self) -> PlannedWorkout:
        ids = [item.id for item in self.exercises]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Workout {self.name!r} contains duplicate exercises")
        return self


class IntroductoryPhase(DomainModel):
    type: Literal["introduction", "normal"] = "introduction"
    sessions: int = Field(default=8, ge=0)
    progression_aggressiveness: Literal["conservative", "normal"] = "conservative"


class TrainingPlan(DomainModel):
    person: str
    priorities: list[str]
    schedule: dict[str, int]
    weekly_schedule: dict[str, str]
    phase: IntroductoryPhase
    workouts: dict[str, PlannedWorkout]

    @model_validator(mode="after")
    def valid_schedule(self) -> TrainingPlan:
        unknown = set(self.weekly_schedule.values()) - set(self.workouts)
        if unknown:
            raise ValueError(f"weekly_schedule references unknown workouts: {sorted(unknown)}")
        expected = self.schedule.get("workouts_per_week")
        if expected is not None and expected != len(self.weekly_schedule):
            raise ValueError("workouts_per_week must match weekly_schedule entries")
        return self


class CompletedSet(DomainModel):
    exercise_id: str
    set_number: int = Field(ge=1)
    reps: int | None = Field(default=None, ge=0)
    weight_kg: float | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    rir: float | None = Field(default=None, ge=0, le=10)
    notes: str | None = None


class CompletedExercise(DomainModel):
    exercise_id: str
    sets: list[CompletedSet]


class CompletedStrengthWorkout(DomainModel):
    person: str
    garmin_activity_id: str
    started_at: datetime
    workout_name: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    average_heart_rate: int | None = Field(default=None, ge=0)
    exercises: list[CompletedExercise]
    imported_at: datetime
    source: Literal["garmin", "manual"] = "garmin"
    source_summary: dict[str, Any] = Field(default_factory=dict)


class ActivitySummary(DomainModel):
    activity_id: str
    started_at: datetime
    name: str | None = None
    activity_type: str | None = None
    duration_seconds: float | None = None
    average_heart_rate: int | None = None


class GarminWorkout(DomainModel):
    workout_id: str
    name: str
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)


class GarminWorkoutRef(DomainModel):
    workout_id: str
    name: str


class ScheduledWorkout(DomainModel):
    schedule_id: str
    workout_id: str
    scheduled_date: date


class SyncEntry(DomainModel):
    workout_id: str
    last_synced_hash: str


class SyncState(DomainModel):
    person: str
    workouts: dict[str, SyncEntry] = Field(default_factory=dict)


class DiffAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    UNCHANGED = "unchanged"
    REPAIR = "repair"


class GarminDiffItem(DomainModel):
    workout_key: str
    workout_name: str
    action: DiffAction
    reason: str
    local_hash: str
    garmin_workout_id: str | None = None


class ProgressionAction(StrEnum):
    INCREASE = "increase"
    MAINTAIN = "maintain"
    REGRESS = "regress"
    REVIEW = "review"
    NO_DATA = "no_data"
    MANUAL = "manual"


class ProgressionChange(DomainModel):
    workout_key: str
    exercise_id: str
    old_weight_kg: float
    new_weight_kg: float
    action: ProgressionAction
    reason: str
    requires_review: bool = False


class ProgressionProposal(DomainModel):
    person: str
    created_at: datetime
    plan_hash: str
    changes: list[ProgressionChange]
