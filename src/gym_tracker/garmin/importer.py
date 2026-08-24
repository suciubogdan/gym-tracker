from __future__ import annotations

from datetime import date, timedelta

from gym_tracker.garmin.protocol import GarminClient
from gym_tracker.storage.repository import ProjectRepository


def import_recent(
    repository: ProjectRepository, client: GarminClient, person: str, days: int
) -> dict[str, int]:
    if days < 1:
        raise ValueError("days must be at least 1")
    end = date.today()
    start = end - timedelta(days=days - 1)
    imported = 0
    skipped = 0
    for summary in client.list_activities(start, end):
        existing = repository.root / "data" / "imported" / person / f"{summary.activity_id}.yaml"
        if existing.exists():
            skipped += 1
            continue
        try:
            completed = client.get_strength_activity(summary.activity_id, summary=summary)
        except Exception:
            raw_getter = getattr(client, "raw_activity_payload", None)
            if callable(raw_getter):
                repository.save_raw(person, summary.activity_id, raw_getter(summary.activity_id))
            raise
        if completed.person != person:
            raise ValueError("Garmin adapter returned an activity for the wrong person")
        # The normalized representation is tracked; the raw health payload is local-only.
        raw_getter = getattr(client, "raw_activity_payload", None)
        raw_payload = raw_getter(summary.activity_id) if callable(raw_getter) else {}
        repository.save_raw(person, summary.activity_id, raw_payload)
        imported += int(repository.save_completed(completed))
    return {"imported": imported, "skipped": skipped}
