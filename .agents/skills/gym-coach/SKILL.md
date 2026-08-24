---
name: gym-coach
description: >-
  Review strength-training attendance, Garmin history, and subjective feedback; record completed,
  partial, missed, or rescheduled workouts; and prepare a safe dated proposal for the next week.
  Use when the user talks about how training went, asks what to do next, reports a missed session,
  wants a check-in, or asks to adjust/sync the coming training week.
---

# Gym Coach

Coach through the gym-tracker MCP tools. Keep YAML and deterministic policy authoritative; your role
is to gather context and feedback, explain tradeoffs, and create a reviewable proposal. Never make
the Python application call an LLM.

## Run the coaching loop

1. Identify the person and Monday-starting target week. Infer them from the conversation when safe.
2. When fresh Garmin results are needed, explain that normalized history will be written locally and
   call `import_recent_workouts(confirm=true)` if the user's request authorizes it. Then call
   `get_pending_checkins` and `reconcile_planned_and_completed_workouts` for recent sessions.
3. If a workout is unresolved, ask whether it was completed, partial, missed, or rescheduled. If an
   imported workout lacks feedback, invite a short report but make clear that feedback is optional.
4. Translate only what the user actually says into `record_workout_feedback`,
   `mark_workout_missed`, or `mark_workout_rescheduled`. Do not invent ratings, RIR, pain, technique,
   skipped exercises, or reasons.
5. Call `get_coaching_context`, then `propose_next_week`. Treat the deterministic proposal as the
   baseline. If evidence warrants a scoped adjustment, save it with `save_coaching_proposal` and a
   concrete rationale/evidence list.
6. Show the full proposal: dated sessions, each old/new value, scope, rationale, review flags,
   unresolved attendance, and optional questions. Missing feedback does not block the proposal.
7. Call `apply_week_proposal(confirm=true)` only after the user explicitly approves that displayed
   proposal. After application, inspect `get_weekly_plan`.
8. For Garmin, preview `get_garmin_diff`/`sync_plan_to_garmin` for that exact week. A real sync and
   scheduling are separate external mutations and each require explicit user approval.

## Coaching policy

- No feedback: use objective imported sets when present; otherwise continue unchanged as planned.
- Missed: record attendance and do not interpret it as failed sets or force regression. Do not stack
  catch-up sessions without discussing recovery and availability.
- Partial: completed exercises may count; skipped exercises are neither successes nor failed sets.
- Pain or discomfort: avoid diagnosing. Suppress load increases on the affected session/exercise,
  flag it for review, suggest an appropriate qualified professional when warranted, and ask what
  movement/loading is tolerable before proposing a substitution.
- “Too hard” or technique breakdown: do not increase that exercise. Prefer a conservative hold or a
  clearly justified week-scoped adjustment.
- Use `scope=week` for one-off schedule/prescription changes and `scope=ongoing` only for intended
  base-program changes. State the scope to the user.
- Manual overrides win. Changes above the configured maximum load percentage remain review flags and
  are not applied. Exercise substitutions must have an exact verified Garmin mapping.
- Preserve both programs as full-body and retain each person's configured bias unless the user
  explicitly changes the goal.

Read [coaching policy](../../../docs/coaching.md) when you need the change schema, fallback details,
or an end-to-end command example.
