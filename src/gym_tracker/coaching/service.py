from __future__ import annotations

from calendar import day_name
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from typing import Any

from gym_tracker.domain.models import (
    AttendanceRecord,
    AttendanceStatus,
    CoachChange,
    CoachChangeKind,
    CoachChangeScope,
    CoachChangeSource,
    CoachingProposal,
    ExerciseFeedback,
    ExercisePrescription,
    OverallFeedback,
    PerceivedDifficulty,
    PlannedWorkout,
    ProgressionAction,
    ReconciledSession,
    TechniqueQuality,
    TrainingPlan,
    WeeklyPlan,
    WeeklySessionPlan,
    WeekReconciliation,
    WorkoutFeedback,
)
from gym_tracker.domain.progression import ProgressionSettings, propose_progression
from gym_tracker.storage.repository import ProjectRepository, model_hash

WEEKDAY_NUMBERS = {name.lower(): index for index, name in enumerate(day_name)}


def require_monday(week_start: date) -> None:
    if week_start.weekday() != 0:
        raise ValueError("week must be a Monday")


class CoachingService:
    def __init__(self, repository: ProjectRepository) -> None:
        self.repository = repository

    def build_weekly_plan(self, person: str, week_start: date) -> WeeklyPlan:
        require_monday(week_start)
        plan = self.repository.load_plan(person)
        sessions = [
            WeeklySessionPlan(
                scheduled_date=week_start + timedelta(days=WEEKDAY_NUMBERS[weekday.lower()]),
                workout_key=workout_key,
                workout=plan.workouts[workout_key].model_copy(deep=True),
                location="gym",
            )
            for weekday, workout_key in plan.weekly_schedule.items()
        ]
        sessions.sort(key=lambda item: item.scheduled_date)
        return WeeklyPlan(
            person=person,
            week_start=week_start,
            created_at=datetime.now(UTC),
            source_plan_hash=model_hash(plan),
            sessions=sessions,
        )

    def _weekly_plan(self, person: str, week_start: date) -> WeeklyPlan:
        return self.repository.load_weekly_plan(person, week_start) or self.build_weekly_plan(
            person, week_start
        )

    def validate_session(
        self, person: str, workout_key: str, scheduled_date: date | None = None
    ) -> TrainingPlan:
        week_start = None
        if scheduled_date is not None:
            week_start = scheduled_date - timedelta(days=scheduled_date.weekday())
        plan = self.repository.effective_plan(person, week_start)
        if workout_key not in plan.workouts:
            raise ValueError(f"Unknown workout {workout_key!r} for {person}")
        return plan

    def record_feedback(
        self,
        *,
        person: str,
        scheduled_date: date,
        workout_key: str,
        status: AttendanceStatus,
        garmin_activity_id: str | None = None,
        rescheduled_to: date | None = None,
        reason: str | None = None,
        overall: OverallFeedback | None = None,
        exercises: list[ExerciseFeedback] | None = None,
    ) -> WorkoutFeedback:
        if status not in {AttendanceStatus.COMPLETED, AttendanceStatus.PARTIAL}:
            raise ValueError("feedback status must be completed or partial")
        plan = self.validate_session(person, workout_key, scheduled_date)
        registry = self.repository.load_registry()
        planned_ids = {item.id for item in plan.workouts[workout_key].exercises}
        for item in exercises or []:
            if item.exercise_id not in planned_ids:
                raise ValueError(f"{item.exercise_id!r} is not prescribed in workout {workout_key}")
            if item.substitute_exercise_id:
                registry.require(item.substitute_exercise_id)
        now = datetime.now(UTC)
        existing = self.repository.load_attendance(person, scheduled_date, workout_key)
        if existing and existing.status == AttendanceStatus.RESCHEDULED:
            rescheduled_to = rescheduled_to or existing.rescheduled_to
            reason = reason or existing.reason
        attendance = AttendanceRecord(
            person=person,
            scheduled_date=scheduled_date,
            workout_key=workout_key,
            status=status,
            recorded_at=now,
            garmin_activity_id=garmin_activity_id,
            rescheduled_to=rescheduled_to,
            reason=reason,
        )
        feedback = WorkoutFeedback(
            person=person,
            scheduled_date=scheduled_date,
            workout_key=workout_key,
            recorded_at=now,
            garmin_activity_id=garmin_activity_id,
            overall=overall or OverallFeedback(),
            exercises=exercises or [],
        )
        self.repository.save_attendance(attendance)
        self.repository.save_feedback(feedback)
        return feedback

    def mark_attendance(
        self,
        *,
        person: str,
        scheduled_date: date,
        workout_key: str,
        status: AttendanceStatus,
        reason: str | None = None,
        rescheduled_to: date | None = None,
    ) -> AttendanceRecord:
        self.validate_session(person, workout_key)
        record = AttendanceRecord(
            person=person,
            scheduled_date=scheduled_date,
            workout_key=workout_key,
            status=status,
            recorded_at=datetime.now(UTC),
            rescheduled_to=rescheduled_to,
            reason=reason,
        )
        self.repository.save_attendance(record)
        return record

    def reconcile_week(
        self, person: str, week_start: date, *, as_of: date | None = None
    ) -> WeekReconciliation:
        require_monday(week_start)
        reconciliation_date = as_of or date.today()
        weekly = self._weekly_plan(person, week_start)
        week_end = week_start + timedelta(days=6)
        all_activities = self.repository.history(person)
        activities = [
            item for item in all_activities if week_start <= item.started_at.date() <= week_end
        ]
        used_activity_ids: set[str] = set()
        reconciled: list[ReconciledSession] = []
        for session in weekly.sessions:
            attendance = self.repository.load_attendance(
                person, session.scheduled_date, session.workout_key
            )
            effective_date = (
                attendance.rescheduled_to
                if attendance and attendance.rescheduled_to
                else session.scheduled_date
            )
            activity = None
            if attendance and attendance.garmin_activity_id:
                activity = next(
                    (
                        item
                        for item in all_activities
                        if item.garmin_activity_id == attendance.garmin_activity_id
                    ),
                    None,
                )
            if activity is None and (
                attendance is None or attendance.status != AttendanceStatus.MISSED
            ):
                candidate_pool = (
                    all_activities if attendance and attendance.rescheduled_to else activities
                )
                candidates = [
                    item
                    for item in candidate_pool
                    if item.garmin_activity_id not in used_activity_ids
                    and item.workout_name == session.workout.name
                    and abs((item.started_at.date() - effective_date).days) <= 1
                ]
                candidates.sort(
                    key=lambda item: abs((item.started_at.date() - effective_date).days)
                )
                activity = candidates[0] if candidates else None
            if activity:
                used_activity_ids.add(activity.garmin_activity_id)

            feedback = self.repository.load_feedback(
                person, session.scheduled_date, session.workout_key
            )
            if activity:
                if attendance and attendance.status == AttendanceStatus.PARTIAL:
                    status = AttendanceStatus.PARTIAL
                elif feedback and any(
                    item.status.value == "skipped" for item in feedback.exercises
                ):
                    status = AttendanceStatus.PARTIAL
                else:
                    status = AttendanceStatus.COMPLETED
            elif attendance:
                status = attendance.status
            elif session.scheduled_date > reconciliation_date:
                status = AttendanceStatus.PLANNED
            else:
                status = AttendanceStatus.UNRESOLVED
            feedback_missing = (
                status
                in {
                    AttendanceStatus.COMPLETED,
                    AttendanceStatus.PARTIAL,
                }
                and feedback is None
            )
            reconciled.append(
                ReconciledSession(
                    scheduled_date=session.scheduled_date,
                    effective_date=effective_date,
                    workout_key=session.workout_key,
                    workout_name=session.workout.name,
                    location=session.location,
                    status=status,
                    garmin_activity_id=(activity.garmin_activity_id if activity else None),
                    feedback_recorded=feedback is not None,
                    feedback_missing=feedback_missing,
                    reason=attendance.reason if attendance else None,
                )
            )
        return WeekReconciliation(
            person=person,
            week_start=week_start,
            generated_at=datetime.now(UTC),
            sessions=reconciled,
            unscheduled_activity_ids=[
                item.garmin_activity_id
                for item in activities
                if item.garmin_activity_id not in used_activity_ids
            ],
        )

    def get_context(self, person: str, target_week: date) -> dict[str, Any]:
        require_monday(target_week)
        review_week = target_week - timedelta(days=7)
        plan = self.repository.load_plan(person)
        reconciliation = self.reconcile_week(person, review_week)
        recent_feedback = [
            item
            for item in self.repository.feedback(person)
            if review_week <= item.scheduled_date <= review_week + timedelta(days=6)
        ]
        settings = ProgressionSettings(**self.repository.load_progression_settings())
        progression = propose_progression(
            plan,
            self.repository.load_registry().exercises,
            self.repository.history(person),
            settings,
        )
        return {
            "person": person,
            "target_week": target_week.isoformat(),
            "active_plan": plan.model_dump(mode="json"),
            "review_week": reconciliation.model_dump(mode="json"),
            "adherence": reconciliation.adherence,
            "feedback": [item.model_dump(mode="json") for item in recent_feedback],
            "deterministic_progression": [item.model_dump(mode="json") for item in progression],
            "fallback_policy": {
                "completed_without_feedback": "use objective data and otherwise continue",
                "no_activity_or_feedback": "leave unresolved and continue unchanged",
                "missed": "do not count as failed progression",
                "partial": "completed exercises count; skipped exercises are not failed sets",
            },
        }

    @staticmethod
    def _feedback_hazards(
        feedback: list[WorkoutFeedback], workout_key: str, exercise_id: str
    ) -> list[str]:
        hazards: list[str] = []
        for record in feedback:
            if record.workout_key != workout_key:
                continue
            if record.overall.pain_or_discomfort:
                hazards.append("session reported pain or discomfort")
            for item in record.exercises:
                if item.exercise_id != exercise_id:
                    continue
                if item.difficulty == PerceivedDifficulty.TOO_HARD:
                    hazards.append("exercise was reported too hard")
                if item.technique in {TechniqueQuality.UNCERTAIN, TechniqueQuality.BROKE_DOWN}:
                    hazards.append(f"technique was reported {item.technique.value}")
        return hazards

    def propose_week(self, person: str, target_week: date) -> CoachingProposal:
        require_monday(target_week)
        review_start = target_week - timedelta(days=7)
        reconciliation = self.reconcile_week(person, review_start)
        plan = self.repository.load_plan(person)
        feedback = [
            item
            for item in self.repository.feedback(person)
            if review_start <= item.scheduled_date <= review_start + timedelta(days=6)
        ]
        settings = ProgressionSettings(**self.repository.load_progression_settings())
        progression = propose_progression(
            plan,
            self.repository.load_registry().exercises,
            self.repository.history(person),
            settings,
        )
        changes: list[CoachChange] = []
        questions: list[str] = []
        notes: list[str] = []
        for item in progression:
            hazards = self._feedback_hazards(feedback, item.workout_key, item.exercise_id)
            if hazards and item.action == ProgressionAction.INCREASE:
                notes.append(
                    f"Suppressed {item.workout_key}/{item.exercise_id} increase: "
                    + "; ".join(hazards)
                )
                continue
            if item.action in {ProgressionAction.INCREASE, ProgressionAction.REGRESS}:
                changes.append(
                    CoachChange(
                        kind=CoachChangeKind.LOAD,
                        scope=CoachChangeScope.ONGOING,
                        workout_key=item.workout_key,
                        exercise_id=item.exercise_id,
                        old_value=item.old_weight_kg,
                        new_value=item.new_weight_kg,
                        rationale=item.reason,
                        evidence=["deterministic double progression"],
                        source=CoachChangeSource.DETERMINISTIC,
                        requires_review=item.requires_review,
                    )
                )
            elif item.action == ProgressionAction.REVIEW:
                notes.append(
                    f"Manual review required for {item.workout_key}/{item.exercise_id}: "
                    f"{item.reason}"
                )
        for session in reconciliation.sessions:
            if session.feedback_missing:
                questions.append(
                    f"Optional: how did {session.workout_key} on "
                    f"{session.effective_date.isoformat()} feel?"
                )
            elif session.status == AttendanceStatus.UNRESOLVED:
                questions.append(
                    f"Was {session.workout_key} on {session.scheduled_date.isoformat()} "
                    "completed, missed, or rescheduled?"
                )
            elif session.status == AttendanceStatus.RESCHEDULED:
                questions.append(
                    f"{session.workout_key} was moved to {session.effective_date.isoformat()}; "
                    "confirm it after completion or report another change."
                )
            elif session.status == AttendanceStatus.MISSED:
                notes.append(
                    f"{session.workout_key} was missed; it does not count as failed progression."
                )
        proposal = CoachingProposal(
            person=person,
            target_week=target_week,
            created_at=datetime.now(UTC),
            base_plan_hash=model_hash(plan),
            review_week=reconciliation,
            summary=(
                f"Prepared {len(changes)} deterministic change(s); missing feedback leaves "
                "the remaining plan unchanged."
            ),
            changes=changes,
            questions=questions,
            notes=notes,
        )
        self.repository.save_coaching_proposal(proposal)
        return proposal

    def save_proposal(
        self,
        *,
        person: str,
        target_week: date,
        summary: str,
        changes: list[CoachChange],
        questions: list[str] | None = None,
        notes: list[str] | None = None,
    ) -> CoachingProposal:
        require_monday(target_week)
        plan = self.repository.load_plan(person)
        reconciliation = self.reconcile_week(person, target_week - timedelta(days=7))
        validated = self._validate_changes(plan, self._weekly_plan(person, target_week), changes)
        proposal = CoachingProposal(
            person=person,
            target_week=target_week,
            created_at=datetime.now(UTC),
            base_plan_hash=model_hash(plan),
            review_week=reconciliation,
            summary=summary,
            changes=validated,
            questions=questions or [],
            notes=notes or [],
        )
        self.repository.save_coaching_proposal(proposal)
        return proposal

    def propose_session_location(
        self,
        *,
        person: str,
        target_week: date,
        workout_key: str,
        location: str,
        rationale: str,
    ) -> CoachingProposal:
        require_monday(target_week)
        plan = self.repository.load_plan(person)
        weekly = self._weekly_plan(person, target_week)
        try:
            session = next(item for item in weekly.sessions if item.workout_key == workout_key)
        except StopIteration as exc:
            raise ValueError(f"Unknown workout {workout_key!r}") from exc
        normalized_location = location.strip().lower()
        retained: list[CoachChange] = []
        questions: list[str] = []
        notes: list[str] = []
        try:
            existing = self.repository.load_coaching_proposal(person, target_week)
        except FileNotFoundError:
            existing = None
        if (
            existing is not None
            and existing.applied_at is None
            and existing.base_plan_hash == model_hash(plan)
        ):
            retained = [
                item
                for item in existing.changes
                if not (item.kind == CoachChangeKind.LOCATION and item.workout_key == workout_key)
            ]
            questions = existing.questions
            notes = existing.notes
        change = CoachChange(
            kind=CoachChangeKind.LOCATION,
            scope=CoachChangeScope.WEEK,
            workout_key=workout_key,
            old_value=session.location,
            new_value=normalized_location,
            rationale=rationale,
            evidence=["user reported that the gym is unavailable"],
            source=CoachChangeSource.USER,
        )
        return self.save_proposal(
            person=person,
            target_week=target_week,
            summary=(
                f"Proposed {normalized_location} variant for {workout_key}; "
                "the recurring gym plan remains unchanged."
            ),
            changes=[*retained, change],
            questions=questions,
            notes=notes,
        )

    @staticmethod
    def _prescription(workout: PlannedWorkout, exercise_id: str) -> ExercisePrescription:
        try:
            return next(item for item in workout.exercises if item.id == exercise_id)
        except StopIteration as exc:
            raise ValueError(f"Exercise {exercise_id!r} is not in {workout.name!r}") from exc

    def _validate_changes(
        self, plan: TrainingPlan, weekly: WeeklyPlan, changes: list[CoachChange]
    ) -> list[CoachChange]:
        registry = self.repository.load_registry()
        locations = self.repository.load_locations()
        settings = ProgressionSettings(**self.repository.load_progression_settings())
        normalized_changes: dict[int, CoachChange] = {}
        validation_weekly = deepcopy(weekly)
        weekly_by_key = {item.workout_key: item for item in validation_weekly.sessions}
        for index, change in enumerate(changes):
            if change.workout_key not in weekly_by_key or change.workout_key not in plan.workouts:
                raise ValueError(f"Unknown workout {change.workout_key!r}")
            if change.kind == CoachChangeKind.LOCATION:
                session = weekly_by_key[change.workout_key]
                current = session.location
                if current != str(change.old_value):
                    raise ValueError(
                        f"Stale location value for {change.workout_key}: expected {current}"
                    )
                proposed_location = str(change.new_value).strip().lower()
                if proposed_location == "gym":
                    proposed_workout = plan.workouts[change.workout_key]
                else:
                    locations.require(proposed_location)
                    variants = plan.workout_variants.get(proposed_location)
                    if variants is None or change.workout_key not in variants:
                        raise ValueError(
                            f"No {proposed_location!r} variant for workout {change.workout_key}"
                        )
                    proposed_workout = variants[change.workout_key]
                session.location = proposed_location
                session.workout = proposed_workout.model_copy(deep=True)
                normalized_changes[index] = change.model_copy(
                    update={"new_value": proposed_location}
                )
                continue
            if change.kind == CoachChangeKind.SCHEDULE:
                current = weekly_by_key[change.workout_key].scheduled_date.isoformat()
                proposed_date = date.fromisoformat(str(change.new_value))
                if current != str(change.old_value):
                    raise ValueError(
                        f"Stale schedule value for {change.workout_key}: expected {current}"
                    )
                if not weekly.week_start <= proposed_date <= weekly.week_start + timedelta(days=6):
                    raise ValueError("schedule change must stay inside the target week")
                normalized_changes[index] = change.model_copy(
                    update={"new_value": proposed_date.isoformat()}
                )
                continue

        for index, change in enumerate(changes):
            if index in normalized_changes:
                continue
            assert change.exercise_id is not None
            workout = (
                plan.workouts[change.workout_key]
                if change.scope == CoachChangeScope.ONGOING
                else weekly_by_key[change.workout_key].workout
            )
            prescription = self._prescription(workout, change.exercise_id)
            if change.kind == CoachChangeKind.LOAD:
                current_value: Any = prescription.target_weight_kg
                new_value = float(change.new_value)
                if new_value < 0:
                    raise ValueError("load cannot be negative")
                requires_review = change.requires_review
                if current_value > 0 and new_value > current_value:
                    increase = ((new_value - current_value) / current_value) * 100
                    if increase > settings.default_max_load_increase_percent:
                        requires_review = True
                normalized = change.model_copy(
                    update={"new_value": new_value, "requires_review": requires_review}
                )
            elif change.kind == CoachChangeKind.SETS:
                current_value = prescription.sets
                new_value = int(change.new_value)
                if not 1 <= new_value <= 10:
                    raise ValueError("sets must be between 1 and 10")
                normalized = change.model_copy(update={"new_value": new_value})
            elif change.kind == CoachChangeKind.REP_RANGE:
                current_value = list(prescription.rep_range)
                candidate_values = [int(value) for value in change.new_value]
                if len(candidate_values) != 2:
                    raise ValueError("rep_range must contain exactly two values")
                candidate = (candidate_values[0], candidate_values[1])
                ExercisePrescription(**(prescription.model_dump() | {"rep_range": candidate}))
                normalized = change.model_copy(update={"new_value": list(candidate)})
            elif change.kind == CoachChangeKind.EXERCISE:
                current_value = prescription.id
                replacement = ExercisePrescription.model_validate(change.new_value)
                replacement_definition = registry.require(replacement.id)
                if replacement_definition.garmin is None:
                    raise ValueError(
                        f"Replacement {replacement.id!r} has no verified Garmin mapping"
                    )
                normalized = change.model_copy(
                    update={"new_value": replacement.model_dump(mode="json")}
                )
            else:
                raise ValueError(f"Unsupported coaching change {change.kind}")
            if current_value != change.old_value:
                raise ValueError(
                    f"Stale {change.kind.value} value for {change.workout_key}/"
                    f"{change.exercise_id}: expected {current_value!r}"
                )
            normalized_changes[index] = normalized
        return [normalized_changes[index] for index in range(len(changes))]

    def _apply_change_to_workout(self, workout: PlannedWorkout, change: CoachChange) -> None:
        assert change.exercise_id is not None
        prescription = self._prescription(workout, change.exercise_id)
        if change.kind == CoachChangeKind.LOAD:
            prescription.target_weight_kg = float(change.new_value)
        elif change.kind == CoachChangeKind.SETS:
            prescription.sets = int(change.new_value)
        elif change.kind == CoachChangeKind.REP_RANGE:
            values = [int(value) for value in change.new_value]
            if len(values) != 2:
                raise ValueError("rep_range must contain exactly two values")
            prescription.rep_range = (values[0], values[1])
        elif change.kind == CoachChangeKind.EXERCISE:
            replacement = ExercisePrescription.model_validate(change.new_value)
            index = workout.exercises.index(prescription)
            workout.exercises[index] = replacement

    def apply_proposal(self, person: str, target_week: date) -> CoachingProposal:
        proposal = self.repository.load_coaching_proposal(person, target_week)
        if proposal.applied_at is not None:
            raise RuntimeError("Coaching proposal has already been applied")
        plan = self.repository.load_plan(person)
        if proposal.base_plan_hash != model_hash(plan):
            raise RuntimeError("Plan changed after coaching proposal; generate a fresh proposal")
        weekly = deepcopy(self._weekly_plan(person, target_week))
        changes = self._validate_changes(plan, weekly, proposal.changes)
        plan_changed = False
        weekly_by_key = {item.workout_key: item for item in weekly.sessions}
        ordered_changes = sorted(
            changes,
            key=lambda item: 0 if item.kind == CoachChangeKind.LOCATION else 1,
        )
        for change in ordered_changes:
            if change.requires_review:
                continue
            if change.kind == CoachChangeKind.LOCATION:
                session = weekly_by_key[change.workout_key]
                location = str(change.new_value)
                session.location = location
                session.workout = (
                    plan.workouts[change.workout_key].model_copy(deep=True)
                    if location == "gym"
                    else plan.workout_variants[location][change.workout_key].model_copy(deep=True)
                )
                continue
            if change.kind == CoachChangeKind.SCHEDULE:
                weekly_by_key[change.workout_key].scheduled_date = date.fromisoformat(
                    str(change.new_value)
                )
                continue
            if change.scope == CoachChangeScope.WEEK:
                self._apply_change_to_workout(weekly_by_key[change.workout_key].workout, change)
            else:
                self._apply_change_to_workout(plan.workouts[change.workout_key], change)
                plan_changed = True
                if weekly_by_key[change.workout_key].location == "gym":
                    self._apply_change_to_workout(weekly_by_key[change.workout_key].workout, change)
        # Revalidate uniqueness and in-week dates after schedule mutations.
        weekly = WeeklyPlan.model_validate(weekly.model_dump())
        if plan_changed:
            self.repository.save_plan(plan)
        weekly.source_plan_hash = model_hash(plan)
        self.repository.save_weekly_plan(weekly)
        applied = proposal.model_copy(update={"changes": changes, "applied_at": datetime.now(UTC)})
        self.repository.save_coaching_proposal(applied)
        return applied

    def pending_checkins(self, person: str, as_of: date) -> list[dict[str, str]]:
        start = as_of - timedelta(days=6)
        monday = start - timedelta(days=start.weekday())
        weeks = {monday, monday + timedelta(days=7)}
        prompts: list[dict[str, str]] = []
        for week in sorted(weeks):
            for session in self.reconcile_week(person, week, as_of=as_of).sessions:
                if not start <= session.scheduled_date <= as_of:
                    continue
                if session.status == AttendanceStatus.UNRESOLVED:
                    prompt = "Was this workout completed, missed, or rescheduled?"
                elif session.feedback_missing:
                    prompt = "Workout imported. How did it feel, and was anything skipped?"
                elif (
                    session.status == AttendanceStatus.RESCHEDULED
                    and session.effective_date <= as_of
                ):
                    prompt = "Was the rescheduled workout completed, missed, or moved again?"
                else:
                    continue
                prompts.append(
                    {
                        "person": person,
                        "date": session.scheduled_date.isoformat(),
                        "workout": session.workout_key,
                        "prompt": prompt,
                    }
                )
        return prompts
