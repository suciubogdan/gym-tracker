from __future__ import annotations

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


def test_completed_activity_normalizes_active_sets(repository: ProjectRepository) -> None:
    adapter = GarminConnectAdapter("bogdan", repository.load_registry(), ActivityApi())
    workout = adapter.get_strength_activity("42")
    assert workout.garmin_activity_id == "42"
    assert workout.average_heart_rate == 112
    assert len(workout.exercises) == 1
    assert [item.reps for item in workout.exercises[0].sets] == [10, 9]
    assert workout.exercises[0].sets[0].weight_kg == 40


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
