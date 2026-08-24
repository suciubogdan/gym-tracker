from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class _ScheduleItem:
    scheduled_date: date
    workout_key: str
    location: str


@dataclass(frozen=True)
class _ScheduleTarget(_ScheduleItem):
    workout_id: str
    notes: str


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

    def _schedule_items(self, person: str, week: date) -> list[_ScheduleItem]:
        plan = self.repository.load_plan(person)
        weekly = self.repository.load_weekly_plan(person, week)
        if weekly:
            return [
                _ScheduleItem(item.scheduled_date, item.workout_key, item.location)
                for item in weekly.sessions
            ]
        items: list[_ScheduleItem] = []
        for configured_day, workout_key in plan.weekly_schedule.items():
            if configured_day.lower() not in WEEKDAYS:
                raise ValueError(f"Unknown weekday {configured_day!r}")
            items.append(
                _ScheduleItem(
                    week + timedelta(days=WEEKDAYS[configured_day.lower()]),
                    workout_key,
                    "gym",
                )
            )
        return items

    def _verified_schedule_targets(
        self, person: str, week: date, items: list[_ScheduleItem]
    ) -> list[_ScheduleTarget]:
        templates = self._desired_templates(person, week)
        state = self.repository.load_sync_state(person)
        registry = self.repository.load_registry()
        targets: list[_ScheduleTarget] = []
        for item in items:
            template_key = f"{item.location}:{item.workout_key}"
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
            targets.append(
                _ScheduleTarget(
                    scheduled_date=item.scheduled_date,
                    workout_key=item.workout_key,
                    location=item.location,
                    workout_id=entry.workout_id,
                    notes=equipment_summary(templates[template_key], registry),
                )
            )
        return targets

    def _schedule_targets(
        self, targets: list[_ScheduleTarget], *, dry_run: bool
    ) -> list[dict[str, str]]:
        months = {(item.scheduled_date.year, item.scheduled_date.month) for item in targets}
        existing = []
        for year, month in months:
            existing.extend(self.client.list_scheduled_workouts(year, month))
        existing_keys = {(item.workout_id, item.scheduled_date) for item in existing}
        result: list[dict[str, str]] = []
        for target in targets:
            already = (target.workout_id, target.scheduled_date) in existing_keys
            result.append(
                {
                    "date": target.scheduled_date.isoformat(),
                    "workout": target.workout_key,
                    "location": target.location,
                    "notes": target.notes,
                    "garmin_workout_id": target.workout_id,
                    "action": "unchanged" if already else "schedule",
                }
            )
            if not dry_run and not already:
                self.client.schedule_workout(target.workout_id, target.scheduled_date)
        return result

    def schedule_week(
        self, person: str, week: date, *, dry_run: bool = True
    ) -> list[dict[str, str]]:
        if week.weekday() != 0:
            raise ValueError("--week must be a Monday")
        items = self._schedule_items(person, week)
        targets = self._verified_schedule_targets(person, week, items)
        return self._schedule_targets(targets, dry_run=dry_run)

    def schedule_session(
        self,
        person: str,
        scheduled_date: date,
        workout_key: str,
        *,
        dry_run: bool = True,
    ) -> dict[str, str]:
        week = scheduled_date - timedelta(days=scheduled_date.weekday())
        items = self._schedule_items(person, week)
        matching = [
            item
            for item in items
            if item.scheduled_date == scheduled_date and item.workout_key == workout_key
        ]
        if not matching:
            same_workout = [item for item in items if item.workout_key == workout_key]
            if same_workout:
                planned_date = same_workout[0].scheduled_date.isoformat()
                raise ValueError(
                    f"Workout {workout_key} is planned for {planned_date}, "
                    f"not {scheduled_date.isoformat()}"
                )
            same_date = [item for item in items if item.scheduled_date == scheduled_date]
            if same_date:
                raise ValueError(
                    f"{same_date[0].workout_key} is planned for {scheduled_date.isoformat()}, "
                    f"not {workout_key}"
                )
            raise ValueError(
                f"Workout {workout_key} is not planned in the week of {week.isoformat()}"
            )
        targets = self._verified_schedule_targets(person, week, matching)
        return self._schedule_targets(targets, dry_run=dry_run)[0]


from pydantic import BaseModel  # noqa: E402


class _HashWrapper(BaseModel):
    serializer_version: str
    value: dict[str, object]
