from __future__ import annotations

from datetime import UTC, datetime

from gym_tracker.domain.models import ActivitySummary, CompletedStrengthWorkout
from gym_tracker.garmin.fake import FakeGarminClient
from gym_tracker.garmin.importer import import_recent
from gym_tracker.storage.repository import ProjectRepository


class CountingFake(FakeGarminClient):
    def __init__(self) -> None:
        super().__init__()
        self.detail_calls = 0
        self.received_summary: ActivitySummary | None = None

    def get_strength_activity(
        self, activity_id: str, summary: ActivitySummary | None = None
    ) -> CompletedStrengthWorkout:
        self.detail_calls += 1
        self.received_summary = summary
        return super().get_strength_activity(activity_id, summary)


def test_import_workflow_skips_existing_id_before_detail_request(
    repository: ProjectRepository,
) -> None:
    client = CountingFake()
    client.activities["77"] = CompletedStrengthWorkout(
        person="bogdan",
        garmin_activity_id="77",
        started_at=datetime.now(UTC),
        workout_name="A",
        exercises=[],
        imported_at=datetime.now(UTC),
    )
    first = import_recent(repository, client, "bogdan", 7)
    second = import_recent(repository, client, "bogdan", 7)
    assert first == {"imported": 1, "skipped": 0}
    assert second == {"imported": 0, "skipped": 1}
    assert client.detail_calls == 1
    assert client.received_summary is not None
    assert client.received_summary.activity_id == "77"
    assert (repository.root / "data" / "raw" / "bogdan" / "77.json").exists()


def test_import_rejects_zero_day_window(repository: ProjectRepository) -> None:
    try:
        import_recent(repository, FakeGarminClient(), "bogdan", 0)
    except ValueError as exc:
        assert "at least 1" in str(exc)
    else:
        raise AssertionError("zero-day import was accepted")
