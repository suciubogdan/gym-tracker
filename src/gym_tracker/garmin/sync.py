from __future__ import annotations

from datetime import date, timedelta

from gym_tracker.domain.equipment import equipment_summary
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
            value={
                "exercise_mappings": mapping_subset,
                "equipment_summary": equipment_summary(workout, registry),
            },
        )
        return model_hash(payload) + ":" + model_hash(hash_context)

    def _desired_templates(
        self, person: str, week_start: date | None = None
    ) -> dict[str, PlannedWorkout]:
        plan = self.repository.load_plan(person)
        templates = {
            f"gym:{workout_key}": workout.model_copy(deep=True)
            for workout_key, workout in plan.workouts.items()
        }
        for location, variants in plan.workout_variants.items():
            templates.update(
                {
                    f"{location}:{workout_key}": workout.model_copy(deep=True)
                    for workout_key, workout in variants.items()
                }
            )
        if week_start is None:
            return templates
        weekly = self.repository.load_weekly_plan(person, week_start)
        if weekly is None:
            return templates
        for session in weekly.sessions:
            template_key = f"{session.location}:{session.workout_key}"
            if template_key not in templates:
                raise ValueError(f"No Garmin template configured for {template_key}")
            templates[template_key] = session.workout.model_copy(deep=True)
        return templates

    def diff(self, person: str, week_start: date | None = None) -> list[GarminDiffItem]:
        templates = self._desired_templates(person, week_start)
        state = self.repository.load_sync_state(person)
        remote_ids = {item.workout_id for item in self.client.list_workouts()}
        registry = self.repository.load_registry()
        results: list[GarminDiffItem] = []
        for template_key, workout in templates.items():
            location, workout_key = template_key.split(":", 1)
            local_hash = self._workout_hash(workout)
            entry = state.workouts.get(template_key)
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
                    template_key=template_key,
                    location=location,
                    workout_key=workout_key,
                    workout_name=workout.name,
                    action=action,
                    reason=reason,
                    notes=equipment_summary(workout, registry),
                    local_hash=local_hash,
                    garmin_workout_id=remote_id,
                )
            )
        return results

    def sync(
        self,
        person: str,
        *,
        dry_run: bool = True,
        week_start: date | None = None,
    ) -> list[GarminDiffItem]:
        differences = self.diff(person, week_start)
        if dry_run:
            return differences
        templates = self._desired_templates(person, week_start)
        state = self.repository.load_sync_state(person)
        for item in differences:
            workout = templates[item.template_key]
            if item.action in {DiffAction.CREATE, DiffAction.REPAIR}:
                reference = self.client.create_workout(workout)
            elif item.action == DiffAction.UPDATE:
                if item.garmin_workout_id is None:
                    raise RuntimeError("Update diff is missing a Garmin workout id")
                reference = self.client.replace_workout(item.garmin_workout_id, workout)
            else:
                continue
            state.workouts[item.template_key] = SyncEntry(
                workout_id=reference.workout_id, last_synced_hash=item.local_hash
            )
            # Persist after each verified remote mutation to survive partial failures.
            self.repository.save_sync_state(state)
        return self.diff(person, week_start)

    def schedule_week(
        self, person: str, week: date, *, dry_run: bool = True
    ) -> list[dict[str, str]]:
        if week.weekday() != 0:
            raise ValueError("--week must be a Monday")
        plan = self.repository.load_plan(person)
        templates = self._desired_templates(person, week)
        state = self.repository.load_sync_state(person)
        desired: list[tuple[date, str, str, str]] = []
        weekly = self.repository.load_weekly_plan(person, week)
        if weekly:
            schedule_items = [
                (item.scheduled_date, item.workout_key, item.location) for item in weekly.sessions
            ]
        else:
            schedule_items = []
            for configured_day, workout_key in plan.weekly_schedule.items():
                if configured_day.lower() not in WEEKDAYS:
                    raise ValueError(f"Unknown weekday {configured_day!r}")
                schedule_items.append(
                    (week + timedelta(days=WEEKDAYS[configured_day.lower()]), workout_key, "gym")
                )
        for scheduled_date, workout_key, location in schedule_items:
            template_key = f"{location}:{workout_key}"
            entry = state.workouts.get(template_key)
            if entry is None:
                raise RuntimeError(
                    f"Workout {template_key} has not been synced; "
                    f"run `gym garmin sync {person} --week {week.isoformat()} --execute` first"
                )
            expected_hash = self._workout_hash(templates[template_key])
            if entry.last_synced_hash != expected_hash:
                raise RuntimeError(
                    f"Workout {template_key} does not match the target week's prescription; "
                    f"run `gym garmin sync {person} --week {week.isoformat()} --execute` first"
                )
            desired.append((scheduled_date, workout_key, location, entry.workout_id))

        months = {(day.year, day.month) for day, _, _, _ in desired}
        existing = []
        for year, month in months:
            existing.extend(self.client.list_scheduled_workouts(year, month))
        existing_keys = {(item.workout_id, item.scheduled_date) for item in existing}
        registry = self.repository.load_registry()
        result: list[dict[str, str]] = []
        for scheduled_date, workout_key, location, workout_id in desired:
            already = (workout_id, scheduled_date) in existing_keys
            result.append(
                {
                    "date": scheduled_date.isoformat(),
                    "workout": workout_key,
                    "location": location,
                    "notes": equipment_summary(templates[f"{location}:{workout_key}"], registry),
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
