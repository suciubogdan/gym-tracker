from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import structlog

from gym_tracker.domain.models import (
    ActivitySummary,
    CompletedExercise,
    CompletedSet,
    CompletedStrengthWorkout,
    DailyRecoverySnapshot,
    ExerciseRegistry,
    GarminWorkout,
    GarminWorkoutRef,
    PlannedWorkout,
    ScheduledWorkout,
)
from gym_tracker.garmin.serializer import UnmappedExerciseError, serialize_strength_workout

logger = structlog.get_logger(__name__)


def account_token_dir(person: str) -> Path:
    return Path.home() / ".config" / "gym-tracker" / "accounts" / person


def _prepare_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def login_account(person: str, email: str, password: str, mfa_prompt: Any) -> None:
    """Authenticate once and persist only Garmin-issued tokens outside the repository."""
    from garminconnect import Garmin  # type: ignore[import-untyped]

    token_dir = account_token_dir(person)
    _prepare_private_directory(token_dir)
    client = Garmin(email, password, prompt_mfa=mfa_prompt)
    client.login(str(token_dir))
    logger.info("garmin_login_complete", person=person, token_dir=str(token_dir))


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Garmin response has no usable start time: {value!r}")
    normalized = value.replace(" ", "T")
    parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _activity_type(raw: dict[str, Any]) -> str | None:
    value = raw.get("activityType")
    if isinstance(value, dict):
        key = value.get("typeKey") or value.get("key")
        return str(key) if key else None
    return str(value) if value else None


def _walk_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_objects(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_objects(nested)


def _nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _text(value: Any) -> str | None:
    return str(value) if value is not None and str(value).strip() else None


def _primary_device_entry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    candidates = [item for item in value.values() if isinstance(item, dict)]
    if not candidates:
        return {}
    return next(
        (item for item in candidates if item.get("primaryTrainingDevice") is True),
        candidates[0],
    )


def _last_series_value(value: Any, *, item_key: str | None = None) -> int | None:
    if not isinstance(value, list):
        return None
    for item in reversed(value):
        candidate = item.get(item_key) if item_key and isinstance(item, dict) else item
        if isinstance(candidate, list) and len(candidate) >= 2:
            candidate = candidate[1]
        parsed = _integer(candidate)
        if parsed is not None:
            return parsed
    return None


class GarminConnectAdapter:
    """Thin adapter over unofficial, unsupported Garmin consumer endpoints."""

    def __init__(self, person: str, registry: ExerciseRegistry, api: Any) -> None:
        self.person = person
        self.registry = registry
        self.api = api
        self._raw_activities: dict[str, dict[str, Any]] = {}
        self._raw_recovery: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_persisted_tokens(cls, person: str, registry: ExerciseRegistry) -> GarminConnectAdapter:
        from garminconnect import Garmin

        token_dir = account_token_dir(person)
        if not token_dir.exists():
            raise RuntimeError(f"No Garmin session for {person}; run `gym garmin login {person}`")
        api = Garmin()
        api.login(str(token_dir))
        return cls(person, registry, api)

    def list_workouts(self) -> list[GarminWorkout]:
        logger.info("garmin_workouts_list", person=self.person)
        raw_workouts: list[dict[str, Any]] = []
        for start in range(0, 1000, 100):
            page = self.api.get_workouts(start=start, limit=100)
            raw_workouts.extend(page)
            if len(page) < 100:
                break
        return [
            GarminWorkout(
                workout_id=str(item["workoutId"]),
                name=str(item.get("workoutName") or "Unnamed workout"),
                raw=item,
            )
            for item in raw_workouts
            if item.get("workoutId") is not None
        ]

    def create_workout(self, workout: PlannedWorkout) -> GarminWorkoutRef:
        payload = serialize_strength_workout(workout, self.registry)
        logger.info("garmin_workout_create", person=self.person, name=workout.name)
        result = self.api.upload_workout(payload)
        return GarminWorkoutRef(workout_id=str(result["workoutId"]), name=workout.name)

    def replace_workout(self, garmin_workout_id: str, workout: PlannedWorkout) -> GarminWorkoutRef:
        payload = serialize_strength_workout(workout, self.registry)
        logger.info("garmin_workout_update", person=self.person, workout_id=garmin_workout_id)
        if hasattr(self.api, "update_workout"):
            result = self.api.update_workout(garmin_workout_id, payload)
            result_id = result.get("workoutId", garmin_workout_id)
            return GarminWorkoutRef(workout_id=str(result_id), name=workout.name)

        # Compatibility fallback for older clients: create, verify, then delete old.
        replacement = self.create_workout(workout)
        if replacement.workout_id not in {item.workout_id for item in self.list_workouts()}:
            raise RuntimeError("Garmin replacement could not be verified; old workout retained")
        self.delete_workout(garmin_workout_id)
        return replacement

    def delete_workout(self, garmin_workout_id: str) -> None:
        logger.warning(
            "garmin_workout_delete_obsolete",
            person=self.person,
            workout_id=garmin_workout_id,
        )
        self.api.delete_workout(garmin_workout_id)

    def schedule_workout(self, garmin_workout_id: str, scheduled_date: date) -> None:
        logger.info(
            "garmin_workout_schedule",
            person=self.person,
            workout_id=garmin_workout_id,
            date=scheduled_date.isoformat(),
        )
        self.api.schedule_workout(garmin_workout_id, scheduled_date.isoformat())

    def list_scheduled_workouts(self, year: int, month: int) -> list[ScheduledWorkout]:
        raw = self.api.get_scheduled_workouts(year, month)
        found: dict[str, ScheduledWorkout] = {}
        for item in _walk_objects(raw):
            schedule_id = item.get("workoutScheduleId") or item.get("scheduleId")
            workout_id = item.get("workoutId")
            date_value = item.get("date") or item.get("calendarDate") or item.get("startDate")
            if schedule_id is None or workout_id is None or not isinstance(date_value, str):
                continue
            try:
                scheduled_date = date.fromisoformat(date_value[:10])
            except ValueError:
                continue
            found[str(schedule_id)] = ScheduledWorkout(
                schedule_id=str(schedule_id),
                workout_id=str(workout_id),
                scheduled_date=scheduled_date,
            )
        return list(found.values())

    def list_activities(self, start: date, end: date) -> list[ActivitySummary]:
        logger.info("garmin_activities_list", person=self.person, start=str(start), end=str(end))
        raw_items = self.api.get_activities_by_date(start.isoformat(), end.isoformat())
        summaries: list[ActivitySummary] = []
        for item in raw_items:
            activity_type = _activity_type(item)
            if activity_type not in {"strength_training", "strength"}:
                continue
            summaries.append(
                ActivitySummary(
                    activity_id=str(item["activityId"]),
                    started_at=_parse_datetime(
                        item.get("startTimeGMT") or item.get("startTimeLocal")
                    ),
                    name=item.get("activityName"),
                    activity_type=activity_type,
                    duration_seconds=item.get("duration"),
                    average_heart_rate=item.get("averageHR"),
                )
            )
        return summaries

    def get_strength_activity(
        self, activity_id: str, summary: ActivitySummary | None = None
    ) -> CompletedStrengthWorkout:
        detail = self.api.get_activity(activity_id)
        sets_payload = self.api.get_activity_exercise_sets(activity_id)
        self._raw_activities[activity_id] = {
            "activity_list_summary": summary.model_dump(mode="json") if summary else None,
            "activity": detail,
            "exercise_sets": sets_payload,
        }
        reverse = self.registry.reverse_garmin()
        grouped: OrderedDict[str, list[CompletedSet]] = OrderedDict()
        active_sets = sets_payload.get("exerciseSets", [])
        if isinstance(active_sets, dict):
            active_sets = [active_sets]
        for raw_set in active_sets:
            if raw_set.get("setType") == "REST":
                continue
            exercises = raw_set.get("exercises") or []
            if not exercises:
                raise UnmappedExerciseError(
                    f"Garmin activity {activity_id} has an active set without an exercise mapping"
                )
            exercise = exercises[0]
            category = str(exercise.get("category") or "")
            name = str(exercise.get("name") or "")
            exercise_id = reverse.get((category, name))
            if exercise_id is None:
                raise UnmappedExerciseError(
                    f"Garmin exercise {category}/{name or '<none>'} is not mapped internally; "
                    f"inspect with `gym garmin exercises search` and update config/exercises.yaml"
                )
            grouped.setdefault(exercise_id, [])
            weight = raw_set.get("weight")
            grouped[exercise_id].append(
                CompletedSet(
                    exercise_id=exercise_id,
                    set_number=len(grouped[exercise_id]) + 1,
                    reps=raw_set.get("repetitionCount"),
                    weight_kg=None if weight is None else float(weight) / 1000.0,
                    duration_seconds=raw_set.get("duration"),
                )
            )
        detail_started = detail.get("startTimeGMT") or detail.get("startTimeLocal")
        if detail_started is not None:
            try:
                started_at = _parse_datetime(detail_started)
            except ValueError:
                if summary is None:
                    raise
                started_at = summary.started_at
        elif summary is not None:
            started_at = summary.started_at
        else:
            started_at = _parse_datetime(None)
        return CompletedStrengthWorkout(
            person=self.person,
            garmin_activity_id=activity_id,
            started_at=started_at,
            workout_name=detail.get("activityName") or (summary.name if summary else None),
            duration_seconds=(
                detail.get("duration")
                if detail.get("duration") is not None
                else (summary.duration_seconds if summary else None)
            ),
            average_heart_rate=(
                detail.get("averageHR")
                if detail.get("averageHR") is not None
                else (summary.average_heart_rate if summary else None)
            ),
            exercises=[
                CompletedExercise(exercise_id=key, sets=value) for key, value in grouped.items()
            ],
            imported_at=datetime.now(UTC),
            source_summary={
                "activity_type": _activity_type(detail)
                or (summary.activity_type if summary else None),
                "set_count": sum(len(value) for value in grouped.values()),
            },
        )

    def raw_activity_payload(self, activity_id: str) -> dict[str, Any]:
        """Return the last fetched raw response for ignored diagnostic storage."""
        return self._raw_activities.get(activity_id, {})

    def get_daily_recovery(self, calendar_date: date) -> DailyRecoverySnapshot:
        date_value = calendar_date.isoformat()
        payloads: dict[str, Any] = {}
        unavailable: list[str] = []
        calls: dict[str, Callable[[], Any]] = {
            "training_status": lambda: self.api.get_training_status(date_value),
            "training_readiness": lambda: self.api.get_morning_training_readiness(date_value),
            "hrv": lambda: self.api.get_hrv_data(date_value),
            "sleep": lambda: self.api.get_sleep_data(date_value),
            "body_battery": lambda: self.api.get_body_battery(date_value),
        }
        for source, call in calls.items():
            try:
                payload = call()
            except Exception as exc:  # Garmin endpoints differ by device and firmware.
                logger.warning(
                    "garmin_recovery_source_unavailable",
                    person=self.person,
                    source=source,
                    error_type=type(exc).__name__,
                )
                payload = None
                unavailable.append(f"{source}:{type(exc).__name__}")
            else:
                if not payload:
                    unavailable.append(source)
            payloads[source] = payload

        training_status = payloads["training_status"]
        status_entry = _primary_device_entry(
            _nested(training_status, "mostRecentTrainingStatus", "latestTrainingStatusData")
        )
        load = _nested(status_entry, "acuteTrainingLoadDTO") or {}
        balance_entry = _primary_device_entry(
            _nested(
                training_status,
                "mostRecentTrainingLoadBalance",
                "metricsTrainingLoadBalanceDTOMap",
            )
        )
        vo2 = _nested(training_status, "mostRecentVO2Max", "generic") or {}

        readiness = payloads["training_readiness"] or {}
        if isinstance(readiness, list):
            readiness = readiness[0] if readiness else {}

        hrv = payloads["hrv"] or {}
        hrv_summary = _nested(hrv, "hrvSummary") or {}
        hrv_baseline = _nested(hrv_summary, "baseline") or {}

        sleep = payloads["sleep"] or {}
        sleep_daily = _nested(sleep, "dailySleepDTO") or {}
        sleep_scores = _nested(sleep_daily, "sleepScores") or {}

        body_battery = payloads["body_battery"] or []
        battery_entry = (
            body_battery[0]
            if isinstance(body_battery, list) and body_battery and isinstance(body_battery[0], dict)
            else {}
        )
        available = [source for source, payload in payloads.items() if payload]
        self._raw_recovery[date_value] = {
            "calendar_date": date_value,
            "sources": payloads,
            "unavailable_sources": unavailable,
        }

        return DailyRecoverySnapshot(
            person=self.person,
            calendar_date=calendar_date,
            imported_at=datetime.now(UTC),
            training_status=_text(status_entry.get("trainingStatusFeedbackPhrase")),
            acute_training_load=_number(load.get("dailyTrainingLoadAcute")),
            training_load_status=_text(load.get("acwrStatus")),
            training_load_balance=_text(balance_entry.get("trainingBalanceFeedbackPhrase")),
            vo2_max=_number(vo2.get("vo2MaxPreciseValue") or vo2.get("vo2MaxValue")),
            readiness_score=_integer(readiness.get("score")),
            readiness_level=_text(readiness.get("level")),
            recovery_time_minutes=_integer(readiness.get("recoveryTime")),
            hrv_status=_text(hrv_summary.get("status") or sleep.get("hrvStatus")),
            overnight_hrv_ms=_number(
                hrv_summary.get("lastNightAvg") or sleep.get("avgOvernightHrv")
            ),
            hrv_baseline_low_ms=_number(hrv_baseline.get("balancedLow")),
            hrv_baseline_high_ms=_number(hrv_baseline.get("balancedUpper")),
            sleep_score=_integer(_nested(sleep_scores, "overall", "value")),
            sleep_seconds=_integer(sleep_daily.get("sleepTimeSeconds")),
            resting_heart_rate=_integer(sleep.get("restingHeartRate")),
            body_battery_at_wake=_last_series_value(
                sleep.get("sleepBodyBattery"), item_key="value"
            ),
            body_battery_current=_last_series_value(battery_entry.get("bodyBatteryValuesArray")),
            body_battery_change=_integer(sleep.get("bodyBatteryChange")),
            available_sources=available,
            unavailable_sources=unavailable,
        )

    def raw_recovery_payload(self, calendar_date: date) -> dict[str, Any]:
        """Return the last fetched raw recovery response for ignored diagnostics."""
        return self._raw_recovery.get(calendar_date.isoformat(), {})


def search_exercise_catalog(term: str) -> list[dict[str, str]]:
    from garminconnect import exercises

    results = exercises.find(term)
    return [dict(item) for item in results]
