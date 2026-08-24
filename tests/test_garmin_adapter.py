from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from gym_tracker.domain.models import ActivitySummary
from gym_tracker.garmin.adapter import GarminConnectAdapter
from gym_tracker.garmin.serializer import UnmappedExerciseError
from gym_tracker.storage.repository import ProjectRepository


class ActivityApi:
    def __init__(self, name: str = "BARBELL_BENCH_PRESS") -> None:
        self.name = name

    def get_activity(self, activity_id: str) -> dict[str, object]:
        return {
            "activityId": activity_id,
            "activityName": "Strength A",
            "startTimeGMT": "2026-08-20 10:00:00",
            "duration": 3000,
            "averageHR": 112,
            "activityType": {"typeKey": "strength_training"},
        }

    def get_activity_exercise_sets(self, activity_id: str) -> dict[str, object]:
        return {
            "activityId": activity_id,
            "exerciseSets": [
                {
                    "setType": "ACTIVE",
                    "duration": 30.0,
                    "repetitionCount": 10,
                    "weight": 40_000.0,
                    "exercises": [{"category": "BENCH_PRESS", "name": self.name}],
                },
                {"setType": "REST", "duration": 90.0, "exercises": []},
                {
                    "setType": "ACTIVE",
                    "duration": 32.0,
                    "repetitionCount": 9,
                    "weight": 40_000.0,
                    "exercises": [{"category": "BENCH_PRESS", "name": self.name}],
                },
            ],
        }


class MissingTimestampActivityApi(ActivityApi):
    def get_activity(self, activity_id: str) -> dict[str, object]:
        value = super().get_activity(activity_id)
        for key in (
            "activityName",
            "startTimeGMT",
            "startTimeLocal",
            "duration",
            "averageHR",
            "activityType",
        ):
            value.pop(key, None)
        return value


class MalformedTimestampActivityApi(ActivityApi):
    def get_activity(self, activity_id: str) -> dict[str, object]:
        value = super().get_activity(activity_id)
        value["startTimeGMT"] = "not-a-timestamp"
        return value


class LegacyWorkoutApi:
    def __init__(self) -> None:
        self.workouts: dict[str, dict[str, object]] = {
            "10": {"workoutId": 10, "workoutName": "Old"}
        }
        self.deleted: list[str] = []

    def upload_workout(self, payload: dict[str, object]) -> dict[str, object]:
        self.workouts["11"] = {
            "workoutId": 11,
            "workoutName": payload["workoutName"],
        }
        return self.workouts["11"]

    def get_workouts(self, start: int, limit: int) -> list[dict[str, object]]:
        return list(self.workouts.values())[start : start + limit]

    def delete_workout(self, workout_id: str) -> None:
        self.deleted.append(workout_id)
        self.workouts.pop(workout_id)


class RecoveryApi:
    def get_training_status(self, cdate: str) -> dict[str, object]:
        return {
            "mostRecentVO2Max": {"generic": {"vo2MaxPreciseValue": 47.2}},
            "mostRecentTrainingStatus": {
                "latestTrainingStatusData": {
                    "device": {
                        "primaryTrainingDevice": True,
                        "trainingStatusFeedbackPhrase": "PRODUCTIVE",
                        "acuteTrainingLoadDTO": {
                            "dailyTrainingLoadAcute": 321,
                            "acwrStatus": "OPTIMAL",
                        },
                    }
                }
            },
            "mostRecentTrainingLoadBalance": {
                "metricsTrainingLoadBalanceDTOMap": {
                    "device": {"trainingBalanceFeedbackPhrase": "BALANCED"}
                }
            },
        }

    def get_morning_training_readiness(self, cdate: str) -> dict[str, object]:
        return {"score": 63, "level": "MODERATE", "recoveryTime": 420}

    def get_hrv_data(self, cdate: str) -> dict[str, object]:
        return {
            "hrvSummary": {
                "lastNightAvg": 52,
                "status": "BALANCED",
                "baseline": {"balancedLow": 45, "balancedUpper": 65},
            }
        }

    def get_sleep_data(self, cdate: str) -> dict[str, object]:
        return {
            "dailySleepDTO": {
                "sleepTimeSeconds": 27_000,
                "sleepScores": {"overall": {"value": 74}},
            },
            "restingHeartRate": 52,
            "bodyBatteryChange": 48,
            "sleepBodyBattery": [{"value": 30}, {"value": 76}],
        }

    def get_body_battery(self, cdate: str) -> list[dict[str, object]]:
        return [{"bodyBatteryValuesArray": [[1, 30], [2, 68]]}]


def test_completed_activity_normalizes_active_sets(repository: ProjectRepository) -> None:
    adapter = GarminConnectAdapter("bogdan", repository.load_registry(), ActivityApi())
    workout = adapter.get_strength_activity("42")
    assert workout.garmin_activity_id == "42"
    assert workout.average_heart_rate == 112
    assert len(workout.exercises) == 1
    assert [item.reps for item in workout.exercises[0].sets] == [10, 9]
    assert workout.exercises[0].sets[0].weight_kg == 40


def test_completed_activity_falls_back_to_validated_list_summary(
    repository: ProjectRepository,
) -> None:
    adapter = GarminConnectAdapter(
        "bogdan", repository.load_registry(), MissingTimestampActivityApi()
    )
    started_at = datetime(2026, 8, 24, 16, 13, 52, tzinfo=UTC)
    summary = ActivitySummary(
        activity_id="44",
        started_at=started_at,
        name="Bogdan Home Full Body A",
        activity_type="strength_training",
        duration_seconds=1234,
        average_heart_rate=101,
    )

    workout = adapter.get_strength_activity("44", summary)

    assert workout.started_at == started_at
    assert workout.workout_name == "Bogdan Home Full Body A"
    assert workout.duration_seconds == 1234
    assert workout.average_heart_rate == 101
    assert workout.source_summary["activity_type"] == "strength_training"
    assert adapter.raw_activity_payload("44")["activity_list_summary"]["activity_id"] == "44"


def test_completed_activity_still_rejects_missing_timestamp_without_summary(
    repository: ProjectRepository,
) -> None:
    adapter = GarminConnectAdapter(
        "bogdan", repository.load_registry(), MissingTimestampActivityApi()
    )

    with pytest.raises(ValueError, match="no usable start time"):
        adapter.get_strength_activity("45")


def test_completed_activity_uses_list_summary_when_detail_timestamp_is_malformed(
    repository: ProjectRepository,
) -> None:
    adapter = GarminConnectAdapter(
        "bogdan", repository.load_registry(), MalformedTimestampActivityApi()
    )
    started_at = datetime(2026, 8, 24, 16, 13, 52, tzinfo=UTC)
    summary = ActivitySummary(
        activity_id="46",
        started_at=started_at,
        name="Bogdan Home Full Body A",
        activity_type="strength_training",
    )

    assert adapter.get_strength_activity("46", summary).started_at == started_at


def test_completed_activity_rejects_unknown_mapping(repository: ProjectRepository) -> None:
    adapter = GarminConnectAdapter(
        "bogdan", repository.load_registry(), ActivityApi("NOT_A_REAL_EXERCISE")
    )
    try:
        adapter.get_strength_activity("43")
    except UnmappedExerciseError as exc:
        assert "not mapped internally" in str(exc)
    else:
        raise AssertionError("unknown mapping did not fail")


def test_daily_recovery_normalizes_only_coaching_fields(
    repository: ProjectRepository,
) -> None:
    adapter = GarminConnectAdapter("bogdan", repository.load_registry(), RecoveryApi())
    day = date(2026, 8, 24)

    snapshot = adapter.get_daily_recovery(day)

    assert snapshot.training_status == "PRODUCTIVE"
    assert snapshot.acute_training_load == 321
    assert snapshot.training_load_status == "OPTIMAL"
    assert snapshot.training_load_balance == "BALANCED"
    assert snapshot.vo2_max == 47.2
    assert snapshot.readiness_score == 63
    assert snapshot.recovery_time_minutes == 420
    assert snapshot.hrv_status == "BALANCED"
    assert snapshot.overnight_hrv_ms == 52
    assert snapshot.sleep_score == 74
    assert snapshot.sleep_seconds == 27_000
    assert snapshot.body_battery_at_wake == 76
    assert snapshot.body_battery_current == 68
    assert snapshot.available_sources == [
        "training_status",
        "training_readiness",
        "hrv",
        "sleep",
        "body_battery",
    ]
    assert set(adapter.raw_recovery_payload(day)["sources"]) == set(snapshot.available_sources)


def test_legacy_replacement_creates_verifies_then_deletes(
    repository: ProjectRepository,
) -> None:
    api = LegacyWorkoutApi()
    adapter = GarminConnectAdapter("bogdan", repository.load_registry(), api)
    result = adapter.replace_workout("10", repository.load_plan("bogdan").workouts["A"])
    assert result.workout_id == "11"
    assert api.deleted == ["10"]
    assert "11" in api.workouts
    assert "10" not in api.workouts
