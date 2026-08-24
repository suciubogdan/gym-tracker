from __future__ import annotations

from datetime import UTC, date, datetime

from gym_tracker.domain.models import (
    ActivitySummary,
    CompletedStrengthWorkout,
    DailyRecoverySnapshot,
    GarminWorkout,
    GarminWorkoutRef,
    PlannedWorkout,
    ScheduledWorkout,
)


class FakeGarminClient:
    """In-memory Garmin port for deterministic unit tests."""

    def __init__(self, person: str = "bogdan") -> None:
        self.person = person
        self.workouts: dict[str, GarminWorkout] = {}
        self.activities: dict[str, CompletedStrengthWorkout] = {}
        self.recovery: dict[date, DailyRecoverySnapshot] = {}
        self.scheduled: dict[tuple[str, date], ScheduledWorkout] = {}
        self.create_calls = 0
        self.replace_calls = 0
        self.delete_calls = 0

    def list_workouts(self) -> list[GarminWorkout]:
        return list(self.workouts.values())

    def create_workout(self, workout: PlannedWorkout) -> GarminWorkoutRef:
        self.create_calls += 1
        workout_id = str(1000 + self.create_calls)
        self.workouts[workout_id] = GarminWorkout(workout_id=workout_id, name=workout.name)
        return GarminWorkoutRef(workout_id=workout_id, name=workout.name)

    def replace_workout(self, garmin_workout_id: str, workout: PlannedWorkout) -> GarminWorkoutRef:
        self.replace_calls += 1
        if garmin_workout_id not in self.workouts:
            raise KeyError(garmin_workout_id)
        self.workouts[garmin_workout_id] = GarminWorkout(
            workout_id=garmin_workout_id, name=workout.name
        )
        return GarminWorkoutRef(workout_id=garmin_workout_id, name=workout.name)

    def delete_workout(self, garmin_workout_id: str) -> None:
        self.delete_calls += 1
        self.workouts.pop(garmin_workout_id, None)

    def schedule_workout(self, garmin_workout_id: str, scheduled_date: date) -> None:
        key = (garmin_workout_id, scheduled_date)
        self.scheduled.setdefault(
            key,
            ScheduledWorkout(
                schedule_id=str(len(self.scheduled) + 1),
                workout_id=garmin_workout_id,
                scheduled_date=scheduled_date,
            ),
        )

    def list_scheduled_workouts(self, year: int, month: int) -> list[ScheduledWorkout]:
        return [
            item
            for item in self.scheduled.values()
            if item.scheduled_date.year == year and item.scheduled_date.month == month
        ]

    def list_activities(self, start: date, end: date) -> list[ActivitySummary]:
        return [
            ActivitySummary(
                activity_id=item.garmin_activity_id,
                started_at=item.started_at,
                name=item.workout_name,
                activity_type="strength_training",
            )
            for item in self.activities.values()
            if start <= item.started_at.date() <= end
        ]

    def get_strength_activity(
        self, activity_id: str, summary: ActivitySummary | None = None
    ) -> CompletedStrengthWorkout:
        return self.activities[activity_id]

    def get_daily_recovery(self, calendar_date: date) -> DailyRecoverySnapshot:
        return self.recovery.get(
            calendar_date,
            DailyRecoverySnapshot(
                person=self.person,
                calendar_date=calendar_date,
                imported_at=datetime.now(UTC),
                unavailable_sources=[
                    "training_status",
                    "training_readiness",
                    "hrv",
                    "sleep",
                    "body_battery",
                ],
            ),
        )
