from __future__ import annotations

from datetime import UTC, datetime

from gym_tracker.domain.models import CompletedStrengthWorkout
from gym_tracker.storage.repository import ProjectRepository


def test_activity_import_storage_is_idempotent(repository: ProjectRepository) -> None:
    workout = CompletedStrengthWorkout(
        person="bogdan",
        garmin_activity_id="12345",
        started_at=datetime.now(UTC),
        exercises=[],
        imported_at=datetime.now(UTC),
    )
    assert repository.save_completed(workout) is True
    assert repository.save_completed(workout) is False
    assert [item.garmin_activity_id for item in repository.history("bogdan")] == ["12345"]


def test_two_people_use_separate_history_directories(repository: ProjectRepository) -> None:
    for person in ("bogdan", "roxana"):
        repository.save_completed(
            CompletedStrengthWorkout(
                person=person,
                garmin_activity_id="same-upstream-shape",
                started_at=datetime.now(UTC),
                exercises=[],
                imported_at=datetime.now(UTC),
            )
        )
    assert len(repository.history("bogdan")) == 1
    assert len(repository.history("roxana")) == 1
