from __future__ import annotations

from datetime import date, timedelta

from gym_tracker.domain.models import (
    DiffAction,
    GarminDiffItem,
    PlannedWorkout,
    SyncEntry,
)
from gym_tracker.garmin.protocol import GarminClient
from gym_tracker.garmin.serializer import GARMIN_SERIALIZER_VERSION
from gym_tracker.storage.repository import ProjectRepository, model_hash

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


class GarminSyncService:
    def __init__(self, repository: ProjectRepository, client: GarminClient) -> None:
        self.repository = repository
        self.client = client

    def _workout_hash(self, workout: PlannedWorkout) -> str:
        registry = self.repository.load_registry()
        mapping_subset: dict[str, object] = {}
        for item in workout.exercises:
            mapping = registry.require(item.id).garmin
            mapping_subset[item.id] = mapping.model_dump() if mapping else None
        payload = workout.model_copy(update={})
        hash_context = _HashWrapper(
            serializer_version=GARMIN_SERIALIZER_VERSION,
            value=mapping_subset,
        )
        return model_hash(payload) + ":" + model_hash(hash_context)

    def diff(self, person: str) -> list[GarminDiffItem]:
        plan = self.repository.load_plan(person)
        state = self.repository.load_sync_state(person)
        remote_ids = {item.workout_id for item in self.client.list_workouts()}
        results: list[GarminDiffItem] = []
        for key, workout in plan.workouts.items():
            local_hash = self._workout_hash(workout)
            entry = state.workouts.get(key)
            if entry is None:
                action, reason, remote_id = DiffAction.CREATE, "no Garmin mapping", None
            elif entry.workout_id not in remote_ids:
                action, reason, remote_id = (
                    DiffAction.REPAIR,
                    "mapped Garmin workout no longer exists",
                    entry.workout_id,
                )
            elif entry.last_synced_hash != local_hash:
                action, reason, remote_id = (
                    DiffAction.UPDATE,
                    "local workout changed since last sync",
                    entry.workout_id,
                )
            else:
                action, reason, remote_id = (
                    DiffAction.UNCHANGED,
                    "hash and remote id match",
                    entry.workout_id,
                )
            results.append(
                GarminDiffItem(
                    workout_key=key,
                    workout_name=workout.name,
                    action=action,
                    reason=reason,
                    local_hash=local_hash,
                    garmin_workout_id=remote_id,
                )
            )
        return results

    def sync(self, person: str, *, dry_run: bool = True) -> list[GarminDiffItem]:
        differences = self.diff(person)
        if dry_run:
            return differences
        plan = self.repository.load_plan(person)
        state = self.repository.load_sync_state(person)
        for item in differences:
            workout = plan.workouts[item.workout_key]
            if item.action in {DiffAction.CREATE, DiffAction.REPAIR}:
                reference = self.client.create_workout(workout)
            elif item.action == DiffAction.UPDATE:
                if item.garmin_workout_id is None:
                    raise RuntimeError("Update diff is missing a Garmin workout id")
                reference = self.client.replace_workout(item.garmin_workout_id, workout)
            else:
                continue
            state.workouts[item.workout_key] = SyncEntry(
                workout_id=reference.workout_id, last_synced_hash=item.local_hash
            )
            # Persist after each verified remote mutation to survive partial failures.
            self.repository.save_sync_state(state)
        return self.diff(person)

    def schedule_week(
        self, person: str, week: date, *, dry_run: bool = True
    ) -> list[dict[str, str]]:
        if week.weekday() != 0:
            raise ValueError("--week must be a Monday")
        plan = self.repository.load_plan(person)
        state = self.repository.load_sync_state(person)
        desired: list[tuple[date, str, str]] = []
        for day_name, workout_key in plan.weekly_schedule.items():
            if day_name.lower() not in WEEKDAYS:
                raise ValueError(f"Unknown weekday {day_name!r}")
            entry = state.workouts.get(workout_key)
            if entry is None:
                raise RuntimeError(
                    f"Workout {workout_key} has not been synced; "
                    f"run `gym garmin sync {person}` first"
                )
            desired.append(
                (
                    week + timedelta(days=WEEKDAYS[day_name.lower()]),
                    workout_key,
                    entry.workout_id,
                )
            )

        months = {(day.year, day.month) for day, _, _ in desired}
        existing = []
        for year, month in months:
            existing.extend(self.client.list_scheduled_workouts(year, month))
        existing_keys = {(item.workout_id, item.scheduled_date) for item in existing}
        result: list[dict[str, str]] = []
        for scheduled_date, workout_key, workout_id in desired:
            already = (workout_id, scheduled_date) in existing_keys
            result.append(
                {
                    "date": scheduled_date.isoformat(),
                    "workout": workout_key,
                    "garmin_workout_id": workout_id,
                    "action": "unchanged" if already else "schedule",
                }
            )
            if not dry_run and not already:
                self.client.schedule_workout(workout_id, scheduled_date)
        return result


from pydantic import BaseModel  # noqa: E402


class _HashWrapper(BaseModel):
    serializer_version: str
    value: dict[str, object]
