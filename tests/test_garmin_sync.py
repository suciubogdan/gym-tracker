from __future__ import annotations

from datetime import date

import pytest

from gym_tracker.coaching.service import CoachingService
from gym_tracker.domain.models import (
    CoachChange,
    CoachChangeKind,
    CoachChangeScope,
    DiffAction,
    GarminWorkout,
    SyncEntry,
    SyncState,
)
from gym_tracker.garmin.fake import FakeGarminClient
from gym_tracker.garmin.sync import GarminSyncService
from gym_tracker.storage.repository import ProjectRepository


def test_sync_create_then_is_idempotent(repository: ProjectRepository) -> None:
    client = FakeGarminClient()
    service = GarminSyncService(repository, client)
    initial_diff = service.diff("bogdan")
    assert {item.action for item in initial_diff} == {DiffAction.CREATE}
    assert all(item.notes.startswith("Equipment: ") for item in initial_diff)
    service.sync("bogdan", dry_run=False)
    assert client.create_calls == 8
    assert {item.action for item in service.diff("bogdan")} == {DiffAction.UNCHANGED}
    service.sync("bogdan", dry_run=False)
    assert client.create_calls == 8


def test_changed_plan_updates_in_place(repository: ProjectRepository) -> None:
    client = FakeGarminClient()
    service = GarminSyncService(repository, client)
    service.sync("bogdan", dry_run=False)
    plan = repository.load_plan("bogdan")
    plan.workouts["A"].exercises[0].target_weight_kg += 2.5
    repository.save_plan(plan)
    diff = {item.template_key: item for item in service.diff("bogdan")}
    assert diff["gym:A"].action == DiffAction.UPDATE
    assert diff["home:A"].action == DiffAction.UNCHANGED
    service.sync("bogdan", dry_run=False)
    assert client.replace_calls == 1
    assert client.create_calls == 8


def test_missing_remote_workout_repairs_without_deleting_others(
    repository: ProjectRepository,
) -> None:
    client = FakeGarminClient()
    service = GarminSyncService(repository, client)
    service.sync("bogdan", dry_run=False)
    state = repository.load_sync_state("bogdan")
    missing_id = state.workouts["gym:A"].workout_id
    client.workouts.pop(missing_id)
    client.workouts["999"] = GarminWorkout(workout_id="999", name="Unrelated")
    service.sync("bogdan", dry_run=False)
    assert "999" in client.workouts
    assert client.delete_calls == 0
    assert client.create_calls == 9


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
    assert all(item["notes"].startswith("Equipment: ") for item in preview)
    service.schedule_week("roxana", week, dry_run=False)
    assert len(client.scheduled) == 4
    second = service.schedule_week("roxana", week, dry_run=False)
    assert {item["action"] for item in second} == {"unchanged"}
    assert len(client.scheduled) == 4


def test_single_session_schedule_is_exact_and_idempotent(
    repository: ProjectRepository,
) -> None:
    client = FakeGarminClient()
    service = GarminSyncService(repository, client)
    service.sync("bogdan", dry_run=False)
    session_date = date(2026, 8, 31)

    preview = service.schedule_session("bogdan", session_date, "A")
    assert preview["action"] == "schedule"
    assert preview["location"] == "gym"
    assert preview["notes"].startswith("Equipment: ")
    assert client.scheduled == {}

    applied = service.schedule_session("bogdan", session_date, "A", dry_run=False)
    assert applied["action"] == "schedule"
    assert len(client.scheduled) == 1
    second = service.schedule_session("bogdan", session_date, "A", dry_run=False)
    assert second["action"] == "unchanged"
    assert len(client.scheduled) == 1


def test_single_session_schedule_rejects_mismatched_date_or_workout(
    repository: ProjectRepository,
) -> None:
    client = FakeGarminClient()
    service = GarminSyncService(repository, client)

    with pytest.raises(ValueError, match="Workout A is planned for 2026-08-31"):
        service.schedule_session("bogdan", date(2026, 9, 1), "A")
    with pytest.raises(ValueError, match="Workout B is planned for 2026-09-01"):
        service.schedule_session("bogdan", date(2026, 8, 31), "B")


def test_two_accounts_keep_independent_sync_ids(repository: ProjectRepository) -> None:
    clients = {"bogdan": FakeGarminClient(), "roxana": FakeGarminClient()}
    for person, client in clients.items():
        GarminSyncService(repository, client).sync(person, dry_run=False)
    bogdan_state = repository.load_sync_state("bogdan")
    roxana_state = repository.load_sync_state("roxana")
    assert bogdan_state.person == "bogdan"
    assert roxana_state.person == "roxana"
    assert len(bogdan_state.workouts) == len(roxana_state.workouts) == 8


def test_schedule_requires_monday(repository: ProjectRepository) -> None:
    client = FakeGarminClient()
    repository.save_sync_state(
        SyncState(
            person="bogdan",
            workouts={
                f"gym:{key}": SyncEntry(workout_id=key, last_synced_hash="x") for key in "ABCD"
            },
        )
    )
    service = GarminSyncService(repository, client)
    try:
        service.schedule_week("bogdan", date(2026, 9, 1))
    except ValueError as exc:
        assert "Monday" in str(exc)
    else:
        raise AssertionError("non-Monday week was accepted")


def test_legacy_sync_keys_migrate_to_gym_without_losing_ids() -> None:
    state = SyncState.model_validate(
        {
            "person": "bogdan",
            "workouts": {
                "A": {"workout_id": "existing-a", "last_synced_hash": "hash-a"},
                "B": {"workout_id": "existing-b", "last_synced_hash": "hash-b"},
            },
        }
    )

    assert set(state.workouts) == {"gym:A", "gym:B"}
    assert state.workouts["gym:A"].workout_id == "existing-a"


def test_weekly_home_session_schedules_home_template_and_keeps_gym_template(
    repository: ProjectRepository,
) -> None:
    client = FakeGarminClient()
    service = GarminSyncService(repository, client)
    service.sync("bogdan", dry_run=False)
    state = repository.load_sync_state("bogdan")
    gym_a_id = state.workouts["gym:A"].workout_id
    home_a_id = state.workouts["home:A"].workout_id
    week = date(2026, 8, 31)
    coach = CoachingService(repository)
    coach.propose_session_location(
        person="bogdan",
        target_week=week,
        workout_key="A",
        location="home",
        rationale="Working from home",
    )
    coach.apply_proposal("bogdan", week)

    preview = service.schedule_week("bogdan", week)
    scheduled_a = next(item for item in preview if item["workout"] == "A")

    assert len(client.workouts) == 8
    assert gym_a_id in client.workouts
    assert scheduled_a["location"] == "home"
    assert scheduled_a["garmin_workout_id"] == home_a_id

    single = service.schedule_session("bogdan", week, "A")
    assert single["location"] == "home"
    assert single["garmin_workout_id"] == home_a_id


def test_weekly_home_adjustment_updates_only_home_template_before_scheduling(
    repository: ProjectRepository,
) -> None:
    client = FakeGarminClient()
    service = GarminSyncService(repository, client)
    service.sync("bogdan", dry_run=False)
    initial_state = repository.load_sync_state("bogdan")
    gym_a_id = initial_state.workouts["gym:A"].workout_id
    home_a_id = initial_state.workouts["home:A"].workout_id
    week = date(2026, 8, 31)
    coach = CoachingService(repository)
    coach.save_proposal(
        person="bogdan",
        target_week=week,
        summary="Train at home with a lighter floor press.",
        changes=[
            CoachChange(
                kind=CoachChangeKind.LOCATION,
                scope=CoachChangeScope.WEEK,
                workout_key="A",
                old_value="gym",
                new_value="home",
                rationale="Working from home",
            ),
            CoachChange(
                kind=CoachChangeKind.LOAD,
                scope=CoachChangeScope.WEEK,
                workout_key="A",
                exercise_id="dumbbell_floor_press",
                old_value=10,
                new_value=8,
                rationale="Conservative first session",
            ),
        ],
    )
    coach.apply_proposal("bogdan", week)

    with pytest.raises(RuntimeError, match=r"home:A.*target week's prescription"):
        service.schedule_week("bogdan", week)
    with pytest.raises(RuntimeError, match=r"home:A.*target week's prescription"):
        service.schedule_session("bogdan", week, "A")

    weekly_diff = {item.template_key: item for item in service.diff("bogdan", week)}
    assert weekly_diff["home:A"].action == DiffAction.UPDATE
    assert weekly_diff["gym:A"].action == DiffAction.UNCHANGED
    service.sync("bogdan", dry_run=False, week_start=week)
    final_state = repository.load_sync_state("bogdan")

    assert final_state.workouts["gym:A"].workout_id == gym_a_id
    assert final_state.workouts["home:A"].workout_id == home_a_id
    assert client.replace_calls == 1
    assert service.schedule_week("bogdan", week)[0]["location"] == "home"
    assert service.schedule_session("bogdan", week, "A")["location"] == "home"
