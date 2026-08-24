from __future__ import annotations

from datetime import date

from gym_tracker.domain.models import DiffAction, GarminWorkout, SyncEntry, SyncState
from gym_tracker.garmin.fake import FakeGarminClient
from gym_tracker.garmin.sync import GarminSyncService
from gym_tracker.storage.repository import ProjectRepository


def test_sync_create_then_is_idempotent(repository: ProjectRepository) -> None:
    client = FakeGarminClient()
    service = GarminSyncService(repository, client)
    assert {item.action for item in service.diff("bogdan")} == {DiffAction.CREATE}
    service.sync("bogdan", dry_run=False)
    assert client.create_calls == 4
    assert {item.action for item in service.diff("bogdan")} == {DiffAction.UNCHANGED}
    service.sync("bogdan", dry_run=False)
    assert client.create_calls == 4


def test_changed_plan_updates_in_place(repository: ProjectRepository) -> None:
    client = FakeGarminClient()
    service = GarminSyncService(repository, client)
    service.sync("bogdan", dry_run=False)
    plan = repository.load_plan("bogdan")
    plan.workouts["A"].exercises[0].target_weight_kg += 2.5
    repository.save_plan(plan)
    diff = {item.workout_key: item for item in service.diff("bogdan")}
    assert diff["A"].action == DiffAction.UPDATE
    service.sync("bogdan", dry_run=False)
    assert client.replace_calls == 1
    assert client.create_calls == 4


def test_missing_remote_workout_repairs_without_deleting_others(
    repository: ProjectRepository,
) -> None:
    client = FakeGarminClient()
    service = GarminSyncService(repository, client)
    service.sync("bogdan", dry_run=False)
    state = repository.load_sync_state("bogdan")
    missing_id = state.workouts["A"].workout_id
    client.workouts.pop(missing_id)
    client.workouts["999"] = GarminWorkout(workout_id="999", name="Unrelated")
    service.sync("bogdan", dry_run=False)
    assert "999" in client.workouts
    assert client.delete_calls == 0
    assert client.create_calls == 5


def test_schedule_is_idempotent_and_uses_configured_days(
    repository: ProjectRepository,
) -> None:
    client = FakeGarminClient()
    service = GarminSyncService(repository, client)
    service.sync("roxana", dry_run=False)
    week = date(2026, 8, 31)
    preview = service.schedule_week("roxana", week)
    assert [item["date"] for item in preview] == [
        "2026-08-31",
        "2026-09-02",
        "2026-09-04",
        "2026-09-06",
    ]
    service.schedule_week("roxana", week, dry_run=False)
    assert len(client.scheduled) == 4
    second = service.schedule_week("roxana", week, dry_run=False)
    assert {item["action"] for item in second} == {"unchanged"}
    assert len(client.scheduled) == 4


def test_two_accounts_keep_independent_sync_ids(repository: ProjectRepository) -> None:
    clients = {"bogdan": FakeGarminClient(), "roxana": FakeGarminClient()}
    for person, client in clients.items():
        GarminSyncService(repository, client).sync(person, dry_run=False)
    bogdan_state = repository.load_sync_state("bogdan")
    roxana_state = repository.load_sync_state("roxana")
    assert bogdan_state.person == "bogdan"
    assert roxana_state.person == "roxana"
    assert len(bogdan_state.workouts) == len(roxana_state.workouts) == 4


def test_schedule_requires_monday(repository: ProjectRepository) -> None:
    client = FakeGarminClient()
    repository.save_sync_state(
        SyncState(
            person="bogdan",
            workouts={key: SyncEntry(workout_id=key, last_synced_hash="x") for key in "ABCD"},
        )
    )
    service = GarminSyncService(repository, client)
    try:
        service.schedule_week("bogdan", date(2026, 9, 1))
    except ValueError as exc:
        assert "Monday" in str(exc)
    else:
        raise AssertionError("non-Monday week was accepted")
