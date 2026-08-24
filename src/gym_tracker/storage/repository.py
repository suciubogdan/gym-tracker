from __future__ import annotations

import hashlib
import json
import os
from calendar import day_name
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

from gym_tracker.domain.models import (
    AttendanceRecord,
    CoachingProposal,
    CompletedStrengthWorkout,
    DailyRecoverySnapshot,
    ExerciseRegistry,
    LocationRegistry,
    ProgressionProposal,
    SyncState,
    TrainingPlan,
    WeeklyPlan,
    WorkoutFeedback,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


def find_project_root(start: Path | None = None) -> Path:
    configured = os.getenv("GYM_TRACKER_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        pyproject = candidate / "pyproject.toml"
        if pyproject.exists() and "gym-tracker" in pyproject.read_text(encoding="utf-8"):
            return candidate
    raise FileNotFoundError("Could not find gym-tracker project root; set GYM_TRACKER_ROOT")


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def dump_yaml(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(value, handle, sort_keys=False, allow_unicode=True)
    temporary.replace(path)


def model_hash(model: BaseModel) -> str:
    payload = json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class ProjectRepository:
    def __init__(self, root: Path) -> None:
        self.root = root

    def people(self) -> list[str]:
        return sorted(path.stem for path in (self.root / "plans").glob("*.yaml"))

    def load_plan(self, person: str) -> TrainingPlan:
        path = self.root / "plans" / f"{person}.yaml"
        if not path.exists():
            raise ValueError(f"Unknown person {person!r}; expected one of {self.people()}")
        return TrainingPlan.model_validate(load_yaml(path))

    def save_plan(self, plan: TrainingPlan) -> None:
        dump_yaml(self.root / "plans" / f"{plan.person}.yaml", plan.model_dump(mode="json"))

    def load_registry(self) -> ExerciseRegistry:
        return ExerciseRegistry.model_validate(load_yaml(self.root / "config" / "exercises.yaml"))

    def load_progression_settings(self) -> dict[str, Any]:
        return dict(load_yaml(self.root / "config" / "progression.yaml"))

    def load_recovery_settings(self) -> dict[str, Any]:
        return dict(load_yaml(self.root / "config" / "recovery.yaml"))

    def load_locations(self) -> LocationRegistry:
        return LocationRegistry.model_validate(load_yaml(self.root / "config" / "locations.yaml"))

    def history(self, person: str, days: int | None = None) -> list[CompletedStrengthWorkout]:
        items = [
            CompletedStrengthWorkout.model_validate(load_yaml(path))
            for path in (self.root / "data" / "imported" / person).glob("*.yaml")
        ]
        items.sort(key=lambda item: item.started_at, reverse=True)
        if days is None:
            return items
        cutoff = datetime.now(UTC).timestamp() - days * 86400
        return [item for item in items if item.started_at.timestamp() >= cutoff]

    def save_completed(self, workout: CompletedStrengthWorkout) -> bool:
        path = (
            self.root / "data" / "imported" / workout.person / f"{workout.garmin_activity_id}.yaml"
        )
        if path.exists():
            return False
        dump_yaml(path, workout.model_dump(mode="json"))
        return True

    def recovery_path(self, person: str, calendar_date: date) -> Path:
        return (
            self.root / "data" / "imported" / person / "daily" / f"{calendar_date.isoformat()}.yaml"
        )

    def save_recovery(self, snapshot: DailyRecoverySnapshot) -> bool:
        path = self.recovery_path(snapshot.person, snapshot.calendar_date)
        if path.exists():
            existing = DailyRecoverySnapshot.model_validate(load_yaml(path))
            comparable = {"imported_at"}
            if existing.model_dump(exclude=comparable) == snapshot.model_dump(exclude=comparable):
                return False
        dump_yaml(path, snapshot.model_dump(mode="json"))
        return True

    def recovery(
        self, person: str, *, start: date | None = None, end: date | None = None
    ) -> list[DailyRecoverySnapshot]:
        items = [
            DailyRecoverySnapshot.model_validate(load_yaml(path))
            for path in (self.root / "data" / "imported" / person / "daily").glob("*.yaml")
        ]
        if start is not None:
            items = [item for item in items if item.calendar_date >= start]
        if end is not None:
            items = [item for item in items if item.calendar_date <= end]
        return sorted(items, key=lambda item: item.calendar_date)

    def save_raw(self, person: str, activity_id: str, payload: dict[str, Any]) -> None:
        path = self.root / "data" / "raw" / person / f"{activity_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        temporary.replace(path)

    def save_raw_recovery(self, person: str, calendar_date: date, payload: dict[str, Any]) -> None:
        path = self.root / "data" / "raw" / person / "daily" / f"{calendar_date.isoformat()}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        temporary.replace(path)

    def load_sync_state(self, person: str) -> SyncState:
        path = self.root / "data" / "sync" / f"{person}.yaml"
        if not path.exists():
            return SyncState(person=person)
        return SyncState.model_validate(load_yaml(path))

    def save_sync_state(self, state: SyncState) -> None:
        dump_yaml(
            self.root / "data" / "sync" / f"{state.person}.yaml",
            state.model_dump(mode="json"),
        )

    def save_proposal(self, proposal: ProgressionProposal) -> None:
        dump_yaml(
            self.root / "data" / "proposals" / f"{proposal.person}.yaml",
            proposal.model_dump(mode="json"),
        )

    def load_proposal(self, person: str) -> ProgressionProposal:
        path = self.root / "data" / "proposals" / f"{person}.yaml"
        if not path.exists():
            raise FileNotFoundError(
                f"No proposal for {person}; run `gym progress propose {person}`"
            )
        return ProgressionProposal.model_validate(load_yaml(path))

    @staticmethod
    def _session_filename(scheduled_date: date, workout_key: str) -> str:
        return f"{scheduled_date.isoformat()}-{workout_key}.yaml"

    def save_attendance(self, record: AttendanceRecord) -> None:
        path = (
            self.root
            / "data"
            / "attendance"
            / record.person
            / self._session_filename(record.scheduled_date, record.workout_key)
        )
        dump_yaml(path, record.model_dump(mode="json"))

    def load_attendance(
        self, person: str, scheduled_date: date, workout_key: str
    ) -> AttendanceRecord | None:
        path = (
            self.root
            / "data"
            / "attendance"
            / person
            / self._session_filename(scheduled_date, workout_key)
        )
        if not path.exists():
            return None
        return AttendanceRecord.model_validate(load_yaml(path))

    def attendance(self, person: str) -> list[AttendanceRecord]:
        records = [
            AttendanceRecord.model_validate(load_yaml(path))
            for path in (self.root / "data" / "attendance" / person).glob("*.yaml")
        ]
        return sorted(records, key=lambda item: (item.scheduled_date, item.workout_key))

    def save_feedback(self, feedback: WorkoutFeedback) -> None:
        path = (
            self.root
            / "data"
            / "feedback"
            / feedback.person
            / self._session_filename(feedback.scheduled_date, feedback.workout_key)
        )
        dump_yaml(path, feedback.model_dump(mode="json"))

    def load_feedback(
        self, person: str, scheduled_date: date, workout_key: str
    ) -> WorkoutFeedback | None:
        path = (
            self.root
            / "data"
            / "feedback"
            / person
            / self._session_filename(scheduled_date, workout_key)
        )
        if not path.exists():
            return None
        return WorkoutFeedback.model_validate(load_yaml(path))

    def feedback(self, person: str) -> list[WorkoutFeedback]:
        records = [
            WorkoutFeedback.model_validate(load_yaml(path))
            for path in (self.root / "data" / "feedback" / person).glob("*.yaml")
        ]
        return sorted(records, key=lambda item: (item.scheduled_date, item.workout_key))

    def weekly_plan_path(self, person: str, week_start: date) -> Path:
        return self.root / "weeks" / week_start.isoformat() / f"{person}.yaml"

    def save_weekly_plan(self, plan: WeeklyPlan) -> None:
        dump_yaml(
            self.weekly_plan_path(plan.person, plan.week_start),
            plan.model_dump(mode="json"),
        )

    def load_weekly_plan(self, person: str, week_start: date) -> WeeklyPlan | None:
        path = self.weekly_plan_path(person, week_start)
        if not path.exists():
            return None
        return WeeklyPlan.model_validate(load_yaml(path))

    def effective_plan(self, person: str, week_start: date | None = None) -> TrainingPlan:
        base = self.load_plan(person)
        if week_start is None:
            return base
        weekly = self.load_weekly_plan(person, week_start)
        if weekly is None:
            return base
        schedule = {
            day_name[item.scheduled_date.weekday()].lower(): item.workout_key
            for item in weekly.sessions
        }
        return base.model_copy(
            update={
                "schedule": {"workouts_per_week": len(weekly.sessions)},
                "weekly_schedule": schedule,
                "workouts": {
                    item.workout_key: item.workout.model_copy(deep=True) for item in weekly.sessions
                },
            }
        )

    def coaching_proposal_path(self, person: str, target_week: date) -> Path:
        return (
            self.root
            / "data"
            / "coaching"
            / "proposals"
            / person
            / f"{target_week.isoformat()}.yaml"
        )

    def save_coaching_proposal(self, proposal: CoachingProposal) -> None:
        dump_yaml(
            self.coaching_proposal_path(proposal.person, proposal.target_week),
            proposal.model_dump(mode="json"),
        )

    def load_coaching_proposal(self, person: str, target_week: date) -> CoachingProposal:
        path = self.coaching_proposal_path(person, target_week)
        if not path.exists():
            raise FileNotFoundError(
                f"No coaching proposal for {person} and {target_week}; "
                f"run `gym coach propose {person} --week {target_week}`"
            )
        return CoachingProposal.model_validate(load_yaml(path))
