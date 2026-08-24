from __future__ import annotations

from datetime import date
from typing import Protocol

from gym_tracker.domain.models import (
    ActivitySummary,
    CompletedStrengthWorkout,
    DailyRecoverySnapshot,
    GarminWorkout,
    GarminWorkoutRef,
    PlannedWorkout,
    ScheduledWorkout,
)


class GarminClient(Protocol):
    """Port implemented by the unofficial API adapter and test fakes."""

    def list_workouts(self) -> list[GarminWorkout]: ...

    def create_workout(self, workout: PlannedWorkout) -> GarminWorkoutRef: ...

    def replace_workout(
        self, garmin_workout_id: str, workout: PlannedWorkout
    ) -> GarminWorkoutRef: ...

    def delete_workout(self, garmin_workout_id: str) -> None: ...

    def schedule_workout(self, garmin_workout_id: str, scheduled_date: date) -> None: ...

    def list_scheduled_workouts(self, year: int, month: int) -> list[ScheduledWorkout]: ...

    def list_activities(self, start: date, end: date) -> list[ActivitySummary]: ...

    def get_strength_activity(
        self, activity_id: str, summary: ActivitySummary | None = None
    ) -> CompletedStrengthWorkout: ...

    def get_daily_recovery(self, calendar_date: date) -> DailyRecoverySnapshot: ...
