from __future__ import annotations

from datetime import date
from typing import Any

from gym_tracker.garmin.protocol import GarminClient
from gym_tracker.storage.repository import ProjectRepository


def import_daily_recovery(
    repository: ProjectRepository,
    client: GarminClient,
    person: str,
    calendar_date: date,
) -> dict[str, Any]:
    snapshot = client.get_daily_recovery(calendar_date)
    if snapshot.person != person:
        raise ValueError("Garmin adapter returned recovery data for the wrong person")
    if snapshot.calendar_date != calendar_date:
        raise ValueError("Garmin adapter returned recovery data for the wrong date")

    raw_getter = getattr(client, "raw_recovery_payload", None)
    raw_payload = raw_getter(calendar_date) if callable(raw_getter) else {}
    repository.save_raw_recovery(person, calendar_date, raw_payload)
    updated = repository.save_recovery(snapshot)
    return {
        "date": calendar_date.isoformat(),
        "updated": updated,
        "available_sources": snapshot.available_sources,
        "unavailable_sources": snapshot.unavailable_sources,
    }
