from __future__ import annotations

from collections import OrderedDict

from gym_tracker.domain.models import (
    Equipment,
    ExercisePrescription,
    ExerciseRegistry,
    PlannedWorkout,
)


def resolved_equipment(
    prescription: ExercisePrescription, registry: ExerciseRegistry
) -> Equipment | None:
    return prescription.equipment or registry.require(prescription.id).equipment


def _words(value: str) -> str:
    return value.replace("_", " ").replace("pull up", "pull-up")


def _equipment_label(equipment: Equipment) -> str:
    equipment_type = _words(equipment.type)
    station = _words(equipment.station) if equipment.station else None
    if equipment.type == "bodyweight":
        return station or "bodyweight"
    if equipment.type == "machine" and station:
        return f"{station} machine"
    if station:
        return f"{equipment_type} + {station}"
    return equipment_type


def _format_weight(value: float) -> str:
    return f"{value:g}"


def _load_detail(prescription: ExercisePrescription, equipment: Equipment) -> str | None:
    if prescription.target_weight_kg > 0:
        value = _format_weight(prescription.target_weight_kg)
        if equipment.type == "dumbbells":
            return f"{value} kg each"
        if equipment.type in {"cable_machine", "machine"}:
            return f"{value} kg setting"
        return f"{value} kg"
    if equipment.type == "resistance_band":
        return "choose resistance"
    return None


def equipment_summary(workout: PlannedWorkout, registry: ExerciseRegistry) -> str:
    """Build deterministic Garmin notes from prescribed equipment and exact target loads."""
    grouped: OrderedDict[str, list[str]] = OrderedDict()
    for prescription in workout.exercises:
        equipment = resolved_equipment(prescription, registry)
        if equipment is None:
            continue
        label = _equipment_label(equipment)
        detail = _load_detail(prescription, equipment)
        details = grouped.setdefault(label, [])
        if detail is not None and detail not in details:
            details.append(detail)
    requirements = [
        label if not details else f"{label} — {' & '.join(details)}"
        for label, details in grouped.items()
    ]
    return "Equipment: " + "; ".join(requirements)
