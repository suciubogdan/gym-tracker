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
    instructions: list[str] = Field(default_factory=list)
    equipment: Equipment | None = None

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
    workout_variants: dict[str, dict[str, PlannedWorkout]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_schedule(self) -> TrainingPlan:
        unknown = set(self.weekly_schedule.values()) - set(self.workouts)
        if unknown:
            raise ValueError(f"weekly_schedule references unknown workouts: {sorted(unknown)}")
        expected = self.schedule.get("workouts_per_week")
        if expected is not None and expected != len(self.weekly_schedule):
            raise ValueError("workouts_per_week must match weekly_schedule entries")
        base_keys = set(self.workouts)
        for location, variants in self.workout_variants.items():
            if set(variants) != base_keys:
                raise ValueError(
                    f"{location!r} workout variants must define exactly {sorted(base_keys)}"
                )
        return self


class TrainingLocation(DomainModel):
    display_name: str
    equipment: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    person_constraints: dict[str, list[str]] = Field(default_factory=dict)


class LocationRegistry(DomainModel):
    locations: dict[str, TrainingLocation]

    def require(self, location: str) -> TrainingLocation:
        try:
            return self.locations[location]
        except KeyError as exc:
            raise ValueError(f"Unknown training location: {location}") from exc


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

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_workout_keys(cls, value: Any) -> Any:
        if not isinstance(value, dict) or not isinstance(value.get("workouts"), dict):
            return value
        raw = dict(value)
        workouts = raw["workouts"]
        migrated = {key: item for key, item in workouts.items() if ":" in key}
        for key, item in workouts.items():
            if ":" not in key:
                migrated.setdefault(f"gym:{key}", item)
        raw["workouts"] = migrated
        return raw


class DiffAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    UNCHANGED = "unchanged"
    REPAIR = "repair"


class GarminDiffItem(DomainModel):
    template_key: str
    location: str
    workout_key: str
    workout_name: str
    action: DiffAction
    reason: str
    notes: str
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


class AttendanceStatus(StrEnum):
    PLANNED = "planned"
    COMPLETED = "completed"
    PARTIAL = "partial"
    MISSED = "missed"
    RESCHEDULED = "rescheduled"
    UNRESOLVED = "unresolved"


class ExerciseCompletionStatus(StrEnum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    SUBSTITUTED = "substituted"


class PerceivedDifficulty(StrEnum):
    TOO_EASY = "too_easy"
    ON_TARGET = "on_target"
    TOO_HARD = "too_hard"
    UNKNOWN = "unknown"


class TechniqueQuality(StrEnum):
    STABLE = "stable"
    UNCERTAIN = "uncertain"
    BROKE_DOWN = "broke_down"
    UNKNOWN = "unknown"


class AttendanceRecord(DomainModel):
    person: str
    scheduled_date: date
    workout_key: str
    status: AttendanceStatus
    recorded_at: datetime
    garmin_activity_id: str | None = None
    rescheduled_to: date | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def reschedule_has_date(self) -> AttendanceRecord:
        if self.status == AttendanceStatus.RESCHEDULED and self.rescheduled_to is None:
            raise ValueError("rescheduled attendance requires rescheduled_to")
        return self


class ExerciseFeedback(DomainModel):
    exercise_id: str
    status: ExerciseCompletionStatus = ExerciseCompletionStatus.COMPLETED
    difficulty: PerceivedDifficulty = PerceivedDifficulty.UNKNOWN
    technique: TechniqueQuality = TechniqueQuality.UNKNOWN
    rir: float | None = Field(default=None, ge=0, le=10)
    substitute_exercise_id: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def substitution_has_exercise(self) -> ExerciseFeedback:
        if (
            self.status == ExerciseCompletionStatus.SUBSTITUTED
            and self.substitute_exercise_id is None
        ):
            raise ValueError("substituted feedback requires substitute_exercise_id")
        if (
            self.status != ExerciseCompletionStatus.SUBSTITUTED
            and self.substitute_exercise_id is not None
        ):
            raise ValueError("substitute_exercise_id is only valid for substituted feedback")
        return self


class OverallFeedback(DomainModel):
    energy: int | None = Field(default=None, ge=1, le=5)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    enjoyment: int | None = Field(default=None, ge=1, le=5)
    recovery: int | None = Field(default=None, ge=1, le=5)
    pain_or_discomfort: bool = False
    pain_notes: str | None = None
    notes: str | None = None


class WorkoutFeedback(DomainModel):
    person: str
    scheduled_date: date
    workout_key: str
    recorded_at: datetime
    garmin_activity_id: str | None = None
    overall: OverallFeedback = Field(default_factory=OverallFeedback)
    exercises: list[ExerciseFeedback] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_exercise_feedback(self) -> WorkoutFeedback:
        ids = [item.exercise_id for item in self.exercises]
        if len(ids) != len(set(ids)):
            raise ValueError("feedback contains duplicate exercise ids")
        return self


class ReconciledSession(DomainModel):
    scheduled_date: date
    effective_date: date
    workout_key: str
    workout_name: str
    location: str = "gym"
    status: AttendanceStatus
    garmin_activity_id: str | None = None
    feedback_recorded: bool = False
    feedback_missing: bool = False
    reason: str | None = None


class WeekReconciliation(DomainModel):
    person: str
    week_start: date
    generated_at: datetime
    sessions: list[ReconciledSession]
    unscheduled_activity_ids: list[str] = Field(default_factory=list)

    @property
    def adherence(self) -> dict[str, int]:
        result = {item.value: 0 for item in AttendanceStatus}
        for session in self.sessions:
            result[session.status.value] += 1
        return result


class WeeklySessionPlan(DomainModel):
    scheduled_date: date
    workout_key: str
    workout: PlannedWorkout
    location: str = "gym"


class WeeklyPlan(DomainModel):
    person: str
    week_start: date
    created_at: datetime
    source_plan_hash: str
    sessions: list[WeeklySessionPlan]

    @model_validator(mode="after")
    def valid_week(self) -> WeeklyPlan:
        if self.week_start.weekday() != 0:
            raise ValueError("week_start must be a Monday")
        keys = [item.workout_key for item in self.sessions]
        dates = [item.scheduled_date for item in self.sessions]
        if len(keys) != len(set(keys)):
            raise ValueError("weekly plan contains duplicate workout keys")
        if len(dates) != len(set(dates)):
            raise ValueError("weekly plan contains duplicate dates")
        week_end = self.week_start.toordinal() + 6
        if any(
            not self.week_start.toordinal() <= item.scheduled_date.toordinal() <= week_end
            for item in self.sessions
        ):
            raise ValueError("weekly session dates must be inside the target week")
        return self


class CoachChangeKind(StrEnum):
    LOAD = "load"
    SETS = "sets"
    REP_RANGE = "rep_range"
    EXERCISE = "exercise"
    SCHEDULE = "schedule"
    LOCATION = "location"


class CoachChangeScope(StrEnum):
    WEEK = "week"
    ONGOING = "ongoing"


class CoachChangeSource(StrEnum):
    DETERMINISTIC = "deterministic"
    COACH = "coach"
    USER = "user"


class CoachChange(DomainModel):
    kind: CoachChangeKind
    scope: CoachChangeScope = CoachChangeScope.ONGOING
    workout_key: str
    exercise_id: str | None = None
    old_value: Any
    new_value: Any
    rationale: str
    evidence: list[str] = Field(default_factory=list)
    source: CoachChangeSource = CoachChangeSource.COACH
    requires_review: bool = False

    @model_validator(mode="after")
    def valid_target(self) -> CoachChange:
        if self.kind in {CoachChangeKind.SCHEDULE, CoachChangeKind.LOCATION}:
            if self.exercise_id is not None:
                raise ValueError(f"{self.kind.value} changes cannot target an exercise")
            if self.scope != CoachChangeScope.WEEK:
                raise ValueError(f"{self.kind.value} changes must be week-scoped")
        elif self.exercise_id is None:
            raise ValueError(f"{self.kind.value} changes require exercise_id")
        return self


class CoachingProposal(DomainModel):
    person: str
    target_week: date
    created_at: datetime
    base_plan_hash: str
    review_week: WeekReconciliation
    summary: str
    changes: list[CoachChange]
    questions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    applied_at: datetime | None = None

    @model_validator(mode="after")
    def consistent_and_unique(self) -> CoachingProposal:
        if self.review_week.person != self.person:
            raise ValueError("review week person must match proposal person")
        targets = [(item.kind, item.workout_key, item.exercise_id) for item in self.changes]
        if len(targets) != len(set(targets)):
            raise ValueError("coaching proposal contains duplicate change targets")
        return self
