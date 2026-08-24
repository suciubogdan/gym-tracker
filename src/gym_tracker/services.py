from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from gym_tracker.domain.models import ProgressionProposal
from gym_tracker.domain.progression import ProgressionSettings, apply_changes, propose_progression
from gym_tracker.garmin.adapter import GarminConnectAdapter
from gym_tracker.garmin.importer import import_recent
from gym_tracker.garmin.protocol import GarminClient
from gym_tracker.garmin.sync import GarminSyncService
from gym_tracker.storage.repository import ProjectRepository, model_hash

ClientFactory = Callable[[str], GarminClient]


class GymService:
    """Application API shared by the CLI and MCP server."""

    def __init__(
        self, repository: ProjectRepository, client_factory: ClientFactory | None = None
    ) -> None:
        self.repository = repository
        self.client_factory = client_factory or self._default_client

    def _default_client(self, person: str) -> GarminClient:
        return GarminConnectAdapter.from_persisted_tokens(person, self.repository.load_registry())

    def get_training_plan(self, person: str) -> dict[str, Any]:
        return self.repository.load_plan(person).model_dump(mode="json")

    def get_recent_workouts(self, person: str, days: int = 7) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in self.repository.history(person, days)]

    def get_training_status(self, person: str) -> dict[str, Any]:
        plan = self.repository.load_plan(person)
        history = self.repository.history(person)
        settings = ProgressionSettings(**self.repository.load_progression_settings())
        changes = propose_progression(
            plan, self.repository.load_registry().exercises, history, settings
        )
        return {
            "person": person,
            "phase": plan.phase.model_dump(mode="json"),
            "completed_sessions": len(history),
            "recommendations": [item.model_dump(mode="json") for item in changes],
        }

    def propose_progression(self, person: str) -> ProgressionProposal:
        plan = self.repository.load_plan(person)
        settings = ProgressionSettings(**self.repository.load_progression_settings())
        proposal = ProgressionProposal(
            person=person,
            created_at=datetime.now(UTC),
            plan_hash=model_hash(plan),
            changes=propose_progression(
                plan,
                self.repository.load_registry().exercises,
                self.repository.history(person),
                settings,
            ),
        )
        self.repository.save_proposal(proposal)
        return proposal

    def apply_progression(self, person: str) -> ProgressionProposal:
        proposal = self.repository.load_proposal(person)
        plan = self.repository.load_plan(person)
        if proposal.plan_hash != model_hash(plan):
            raise RuntimeError(
                "Plan changed after proposal; generate a fresh proposal before applying"
            )
        updated = apply_changes(plan, proposal.changes)
        self.repository.save_plan(updated)
        return proposal

    def import_workouts(self, person: str, days: int = 7) -> dict[str, int]:
        return import_recent(self.repository, self.client_factory(person), person, days)

    def get_garmin_diff(self, person: str) -> list[dict[str, Any]]:
        service = GarminSyncService(self.repository, self.client_factory(person))
        return [item.model_dump(mode="json") for item in service.diff(person)]

    def sync_plan_to_garmin(self, person: str, *, dry_run: bool = True) -> list[dict[str, Any]]:
        service = GarminSyncService(self.repository, self.client_factory(person))
        return [item.model_dump(mode="json") for item in service.sync(person, dry_run=dry_run)]

    def schedule_week(
        self, person: str, week: date, *, dry_run: bool = True
    ) -> list[dict[str, str]]:
        service = GarminSyncService(self.repository, self.client_factory(person))
        return service.schedule_week(person, week, dry_run=dry_run)
