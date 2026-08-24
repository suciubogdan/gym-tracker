from __future__ import annotations

from datetime import date
from typing import Any

from mcp.server import MCPServer

from gym_tracker.services import GymService
from gym_tracker.storage.repository import ProjectRepository, find_project_root

mcp = MCPServer(
    "gym-tracker",
    instructions=(
        "Local plans are canonical. Inspect proposals before applying them. "
        "Garmin synchronization defaults to dry-run and external mutations require confirmation."
    ),
)


def _service() -> GymService:
    return GymService(ProjectRepository(find_project_root()))


@mcp.tool()
def get_training_plan(person: str) -> dict[str, Any]:
    """Read the canonical local training plan."""
    return _service().get_training_plan(person)


@mcp.tool()
def get_recent_workouts(person: str, days: int = 7) -> list[dict[str, Any]]:
    """Read normalized local workout history; no Garmin call is made."""
    return _service().get_recent_workouts(person, days)


@mcp.tool()
def get_training_status(person: str) -> dict[str, Any]:
    """Calculate deterministic progression status without mutation."""
    return _service().get_training_status(person)


@mcp.tool()
def propose_progression(person: str) -> dict[str, Any]:
    """Create a local, reviewable proposal; does not change the plan."""
    return _service().propose_progression(person).model_dump(mode="json")


@mcp.tool()
def apply_progression(person: str, confirm: bool = False) -> dict[str, Any]:
    """Apply a reviewed local proposal only when confirm=true."""
    if not confirm:
        return {"applied": False, "reason": "Set confirm=true after reviewing the proposal."}
    proposal = _service().apply_progression(person)
    return {"applied": True, "proposal": proposal.model_dump(mode="json")}


@mcp.tool()
def get_garmin_diff(person: str) -> list[dict[str, Any]]:
    """Read Garmin state and compare it to the canonical local plan."""
    return _service().get_garmin_diff(person)


@mcp.tool()
def sync_plan_to_garmin(
    person: str, dry_run: bool = True, confirm: bool = False
) -> list[dict[str, Any]]:
    """Sync externally; dry-run is the safe default and a real sync needs confirm=true."""
    if not dry_run and not confirm:
        raise ValueError("A Garmin mutation requires confirm=true")
    return _service().sync_plan_to_garmin(person, dry_run=dry_run)


@mcp.tool()
def schedule_week(
    person: str, week: str, dry_run: bool = True, confirm: bool = False
) -> list[dict[str, str]]:
    """Preview or externally schedule A/B/C/D for a Monday-starting ISO week."""
    if not dry_run and not confirm:
        raise ValueError("A Garmin mutation requires confirm=true")
    return _service().schedule_week(person, date.fromisoformat(week), dry_run=dry_run)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
