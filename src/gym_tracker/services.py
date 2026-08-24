from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from gym_tracker.coaching.service import CoachingService
from gym_tracker.domain.equipment import equipment_summary
from gym_tracker.domain.models import (
    AttendanceRecord,
    AttendanceStatus,
    CoachChange,
    CoachingProposal,
    ExerciseFeedback,
    ExerciseRegistry,
    OverallFeedback,
    PlannedWorkout,
    ProgressionProposal,
    WeeklyPlan,
    WeekReconciliation,
    WorkoutFeedback,
)
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

    @staticmethod
    def _workout_view(workout: PlannedWorkout, registry: ExerciseRegistry) -> dict[str, Any]:
        value = workout.model_dump(mode="json")
        value["equipment_notes"] = equipment_summary(workout, registry)
        return value

    def get_training_plan(self, person: str) -> dict[str, Any]:
        plan = self.repository.load_plan(person)
        registry = self.repository.load_registry()
        value = plan.model_dump(mode="json")
        value["workouts"] = {
            key: self._workout_view(workout, registry) for key, workout in plan.workouts.items()
        }
        value["workout_variants"] = {
            location: {
                key: self._workout_view(workout, registry) for key, workout in variants.items()
            }
            for location, variants in plan.workout_variants.items()
        }
        return value

    def get_training_locations(self) -> dict[str, Any]:
        return self.repository.load_locations().model_dump(mode="json")

    def get_recent_workouts(self, person: str, days: int = 7) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in self.repository.history(person, days)]

    def _coach(self) -> CoachingService:
        return CoachingService(self.repository)

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

    def record_workout_feedback(
        self,
        *,
        person: str,
        scheduled_date: date,
        workout_key: str,
        status: AttendanceStatus,
        garmin_activity_id: str | None = None,
        rescheduled_to: date | None = None,
        reason: str | None = None,
        overall: OverallFeedback | None = None,
        exercises: list[ExerciseFeedback] | None = None,
    ) -> WorkoutFeedback:
        return self._coach().record_feedback(
            person=person,
            scheduled_date=scheduled_date,
            workout_key=workout_key,
            status=status,
            garmin_activity_id=garmin_activity_id,
            rescheduled_to=rescheduled_to,
            reason=reason,
            overall=overall,
            exercises=exercises,
        )

    def mark_workout_attendance(
        self,
        *,
        person: str,
        scheduled_date: date,
        workout_key: str,
        status: AttendanceStatus,
        reason: str | None = None,
        rescheduled_to: date | None = None,
    ) -> AttendanceRecord:
        return self._coach().mark_attendance(
            person=person,
            scheduled_date=scheduled_date,
            workout_key=workout_key,
            status=status,
            reason=reason,
            rescheduled_to=rescheduled_to,
        )

    def reconcile_week(self, person: str, week: date) -> WeekReconciliation:
        return self._coach().reconcile_week(person, week)

    def get_coaching_context(self, person: str, target_week: date) -> dict[str, Any]:
        return self._coach().get_context(person, target_week)

    def propose_coaching_week(self, person: str, target_week: date) -> CoachingProposal:
        return self._coach().propose_week(person, target_week)

    def save_coaching_proposal(
        self,
        *,
        person: str,
        target_week: date,
        summary: str,
        changes: list[CoachChange],
        questions: list[str] | None = None,
        notes: list[str] | None = None,
    ) -> CoachingProposal:
        return self._coach().save_proposal(
            person=person,
            target_week=target_week,
            summary=summary,
            changes=changes,
            questions=questions,
            notes=notes,
        )

    def get_coaching_proposal(self, person: str, target_week: date) -> CoachingProposal:
        return self.repository.load_coaching_proposal(person, target_week)

    def propose_session_location(
        self,
        *,
        person: str,
        target_week: date,
        workout_key: str,
        location: str,
        rationale: str,
    ) -> CoachingProposal:
        return self._coach().propose_session_location(
            person=person,
            target_week=target_week,
            workout_key=workout_key,
            location=location,
            rationale=rationale,
        )

    def apply_coaching_proposal(self, person: str, target_week: date) -> CoachingProposal:
        return self._coach().apply_proposal(person, target_week)

    def get_weekly_plan(self, person: str, week: date) -> WeeklyPlan:
        return self.repository.load_weekly_plan(person, week) or self._coach().build_weekly_plan(
            person, week
        )

    def get_weekly_plan_view(self, person: str, week: date) -> dict[str, Any]:
        weekly = self.get_weekly_plan(person, week)
        registry = self.repository.load_registry()
        value = weekly.model_dump(mode="json")
        for payload, session in zip(value["sessions"], weekly.sessions, strict=True):
            payload["equipment_notes"] = equipment_summary(session.workout, registry)
        return value

    def get_pending_checkins(self, person: str, as_of: date) -> list[dict[str, str]]:
        return self._coach().pending_checkins(person, as_of)

    def get_garmin_diff(self, person: str, week: date | None = None) -> list[dict[str, Any]]:
        service = GarminSyncService(self.repository, self.client_factory(person))
        return [item.model_dump(mode="json") for item in service.diff(person, week)]

    def sync_plan_to_garmin(
        self, person: str, *, dry_run: bool = True, week: date | None = None
    ) -> list[dict[str, Any]]:
        service = GarminSyncService(self.repository, self.client_factory(person))
        return [
            item.model_dump(mode="json")
            for item in service.sync(person, dry_run=dry_run, week_start=week)
        ]

    def schedule_week(
        self, person: str, week: date, *, dry_run: bool = True
    ) -> list[dict[str, str]]:
        service = GarminSyncService(self.repository, self.client_factory(person))
        return service.schedule_week(person, week, dry_run=dry_run)

    def schedule_session(
        self,
        person: str,
        scheduled_date: date,
        workout_key: str,
        *,
        dry_run: bool = True,
    ) -> dict[str, str]:
        service = GarminSyncService(self.repository, self.client_factory(person))
        return service.schedule_session(
            person,
            scheduled_date,
            workout_key,
            dry_run=dry_run,
        )
