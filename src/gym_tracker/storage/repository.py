from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

from gym_tracker.domain.models import (
    CompletedStrengthWorkout,
    ExerciseRegistry,
    ProgressionProposal,
    SyncState,
    TrainingPlan,
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

    def save_raw(self, person: str, activity_id: str, payload: dict[str, Any]) -> None:
        path = self.root / "data" / "raw" / person / f"{activity_id}.json"
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
