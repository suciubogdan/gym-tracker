from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from gym_tracker.coaching.recovery import RecoverySettings, assess_recovery
from gym_tracker.domain.models import DailyRecoverySnapshot, RecoveryState
from gym_tracker.storage.repository import ProjectRepository


def _settings(repository: ProjectRepository) -> RecoverySettings:
    return RecoverySettings(**repository.load_recovery_settings())


def _snapshot(calendar_date: date, **values: object) -> DailyRecoverySnapshot:
    return DailyRecoverySnapshot(
        person="bogdan",
        calendar_date=calendar_date,
        imported_at=datetime(2026, 8, 24, 8, tzinfo=UTC),
        **values,
    )


def test_recovery_snapshot_round_trips_and_avoids_timestamp_only_rewrite(
    repository: ProjectRepository,
) -> None:
    day = date(2026, 8, 24)
    first = _snapshot(day, sleep_score=72, hrv_status="BALANCED")
    second = first.model_copy(update={"imported_at": first.imported_at + timedelta(hours=1)})

    assert repository.save_recovery(first) is True
    assert repository.save_recovery(second) is False
    assert repository.recovery("bogdan") == [first]


def test_good_recovery_never_creates_or_authorizes_an_increase(
    repository: ProjectRepository,
) -> None:
    day = date(2026, 8, 24)
    result = assess_recovery(
        "bogdan",
        [
            _snapshot(
                day,
                readiness_score=85,
                sleep_score=82,
                body_battery_at_wake=78,
                hrv_status="BALANCED",
                training_status="PRODUCTIVE",
            )
        ],
        _settings(repository),
        as_of=day,
    )

    assert result.state == RecoveryState.NORMAL
    assert result.suppress_increases is False
    assert "does not add load" in result.recommendation


def test_two_degraded_signals_suppress_increases_without_reducing_load(
    repository: ProjectRepository,
) -> None:
    day = date(2026, 8, 24)
    result = assess_recovery(
        "bogdan",
        [_snapshot(day, sleep_score=55, body_battery_at_wake=30, hrv_status="BALANCED")],
        _settings(repository),
        as_of=day,
    )

    assert result.state == RecoveryState.CAUTION
    assert result.suppress_increases is True
    assert result.signals == ["Sleep score 55/100", "Body Battery at wake 30/100"]
    assert "Keep current prescriptions" in result.recommendation


def test_persistent_degradation_requires_review(repository: ProjectRepository) -> None:
    day = date(2026, 8, 24)
    result = assess_recovery(
        "bogdan",
        [
            _snapshot(
                day - timedelta(days=1),
                sleep_score=55,
                body_battery_at_wake=30,
            ),
            _snapshot(day, sleep_score=54, body_battery_at_wake=29),
        ],
        _settings(repository),
        as_of=day,
    )

    assert result.state == RecoveryState.REVIEW
    assert any("consecutive days" in item for item in result.signals)


def test_nonconsecutive_snapshots_do_not_count_as_persistent(
    repository: ProjectRepository,
) -> None:
    day = date(2026, 8, 24)
    result = assess_recovery(
        "bogdan",
        [
            _snapshot(day - timedelta(days=2), sleep_score=55, body_battery_at_wake=30),
            _snapshot(day, sleep_score=54, body_battery_at_wake=29),
        ],
        _settings(repository),
        as_of=day,
    )

    assert result.state == RecoveryState.CAUTION
    assert not any("consecutive days" in item for item in result.signals)


def test_training_status_alone_is_context_only(repository: ProjectRepository) -> None:
    day = date(2026, 8, 24)
    result = assess_recovery(
        "bogdan",
        [_snapshot(day, training_status="STRAINED", training_load_status="HIGH")],
        _settings(repository),
        as_of=day,
    )

    assert result.state == RecoveryState.UNKNOWN
    assert result.suppress_increases is False
    assert all("context only" in item for item in result.signals)


def test_stale_or_missing_recovery_is_neutral(repository: ProjectRepository) -> None:
    day = date(2026, 8, 24)
    result = assess_recovery(
        "bogdan",
        [_snapshot(day - timedelta(days=2), sleep_score=20)],
        _settings(repository),
        as_of=day,
    )

    assert result.state == RecoveryState.UNKNOWN
    assert result.suppress_increases is False
