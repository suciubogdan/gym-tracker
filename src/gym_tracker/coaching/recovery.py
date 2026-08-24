from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gym_tracker.domain.models import (
    DailyRecoverySnapshot,
    RecoveryAssessment,
    RecoveryState,
)


class RecoverySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    readiness_caution_below: int = Field(ge=0, le=100)
    readiness_review_below: int = Field(ge=0, le=100)
    sleep_caution_below: int = Field(ge=0, le=100)
    sleep_review_below: int = Field(ge=0, le=100)
    body_battery_caution_below: int = Field(ge=0, le=100)
    body_battery_review_below: int = Field(ge=0, le=100)
    hrv_degraded_statuses: list[str]
    degraded_signals_for_caution: int = Field(ge=1)
    degraded_signals_for_review: int = Field(ge=1)
    severe_signals_for_review: int = Field(ge=1)
    persistent_days_for_review: int = Field(ge=2)
    max_snapshot_age_days: int = Field(ge=0)

    @model_validator(mode="after")
    def ordered_thresholds(self) -> RecoverySettings:
        pairs = (
            (self.readiness_review_below, self.readiness_caution_below, "readiness"),
            (self.sleep_review_below, self.sleep_caution_below, "sleep"),
            (
                self.body_battery_review_below,
                self.body_battery_caution_below,
                "body battery",
            ),
        )
        for review, caution, name in pairs:
            if review > caution:
                raise ValueError(f"{name} review threshold must not exceed caution threshold")
        if self.degraded_signals_for_review < self.degraded_signals_for_caution:
            raise ValueError("review signal count must not be below caution signal count")
        return self


def _signal_counts(
    snapshot: DailyRecoverySnapshot, settings: RecoverySettings
) -> tuple[int, int, list[str], int]:
    degraded = 0
    severe = 0
    available = 0
    signals: list[str] = []

    if snapshot.readiness_score is not None:
        available += 1
        if snapshot.readiness_score < settings.readiness_caution_below:
            degraded += 1
            signals.append(f"Training readiness {snapshot.readiness_score}/100")
        if snapshot.readiness_score < settings.readiness_review_below:
            severe += 1

    if snapshot.sleep_score is not None:
        available += 1
        if snapshot.sleep_score < settings.sleep_caution_below:
            degraded += 1
            signals.append(f"Sleep score {snapshot.sleep_score}/100")
        if snapshot.sleep_score < settings.sleep_review_below:
            severe += 1

    if snapshot.body_battery_at_wake is not None:
        available += 1
        if snapshot.body_battery_at_wake < settings.body_battery_caution_below:
            degraded += 1
            signals.append(f"Body Battery at wake {snapshot.body_battery_at_wake}/100")
        if snapshot.body_battery_at_wake < settings.body_battery_review_below:
            severe += 1

    if snapshot.hrv_status is not None:
        available += 1
        if snapshot.hrv_status.casefold() in {
            item.casefold() for item in settings.hrv_degraded_statuses
        }:
            degraded += 1
            signals.append(f"HRV status {snapshot.hrv_status}")

    return degraded, severe, signals, available


def assess_recovery(
    person: str,
    snapshots: list[DailyRecoverySnapshot],
    settings: RecoverySettings,
    *,
    as_of: date,
) -> RecoveryAssessment:
    recent = sorted(
        [item for item in snapshots if item.calendar_date <= as_of],
        key=lambda item: item.calendar_date,
    )
    if not recent or as_of - recent[-1].calendar_date > timedelta(
        days=settings.max_snapshot_age_days
    ):
        return RecoveryAssessment(
            person=person,
            as_of=as_of,
            state=RecoveryState.UNKNOWN,
            data_quality="none",
            snapshot_dates=[item.calendar_date for item in recent],
            recommendation="No current Garmin recovery snapshot; continue unchanged.",
        )

    current = recent[-1]
    degraded, severe, signals, available = _signal_counts(current, settings)
    primary_signal_count = 4
    data_quality: Literal["complete", "partial", "none"] = (
        "complete" if available == primary_signal_count else "partial"
    )
    if available == 0:
        data_quality = "none"

    daily_degraded = [_signal_counts(item, settings)[0] for item in recent]
    persistent = False
    if len(daily_degraded) >= settings.persistent_days_for_review:
        snapshot_tail = recent[-settings.persistent_days_for_review :]
        tail = daily_degraded[-settings.persistent_days_for_review :]
        consecutive = all(
            current.calendar_date - previous.calendar_date == timedelta(days=1)
            for previous, current in pairwise(snapshot_tail)
        )
        persistent = consecutive and all(
            value >= settings.degraded_signals_for_caution for value in tail
        )
        if persistent:
            signals.append(
                f"Recovery was degraded for {settings.persistent_days_for_review} consecutive days"
            )

    if current.training_status:
        signals.append(f"Training status {current.training_status} (context only)")
    if current.training_load_status:
        signals.append(f"Acute load status {current.training_load_status} (context only)")

    if available == 0:
        state = RecoveryState.UNKNOWN
    elif (
        severe >= settings.severe_signals_for_review
        or degraded >= settings.degraded_signals_for_review
        or persistent
    ):
        state = RecoveryState.REVIEW
    elif severe >= 1 or degraded >= settings.degraded_signals_for_caution:
        state = RecoveryState.CAUTION
    else:
        state = RecoveryState.NORMAL

    recommendations = {
        RecoveryState.NORMAL: "Use completed performance and feedback; recovery does not add load.",
        RecoveryState.CAUTION: "Keep current prescriptions and suppress load increases.",
        RecoveryState.REVIEW: (
            "Suppress increases and ask how the person feels before proposing a week-only "
            "reduction."
        ),
        RecoveryState.UNKNOWN: "Recovery data is unavailable; continue unchanged.",
    }
    return RecoveryAssessment(
        person=person,
        as_of=as_of,
        state=state,
        data_quality=data_quality,
        snapshot_dates=[item.calendar_date for item in recent],
        signals=signals,
        suppress_increases=state in {RecoveryState.CAUTION, RecoveryState.REVIEW},
        recommendation=recommendations[state],
    )
