# Agentic coaching workflow

The coach is an optional agent layer over a deterministic local application. It gathers the user's
subjective report, reads normalized Garmin evidence, and prepares a proposal. The application still
works without an agent and never calls an LLM API.

## What is stored

- `data/attendance/<person>/<date>-<workout>.yaml`: completed, partial, missed, or rescheduled.
- `data/feedback/<person>/<date>-<workout>.yaml`: optional overall and per-exercise feedback.
- `data/imported/<person>/<activity-id>.yaml`: objective Garmin sets and reps.
- `data/imported/<person>/daily/<date>.yaml`: normalized daily Garmin recovery evidence.
- `weeks/<monday>/<person>.yaml`: the approved, dated prescription for one week.
- `data/coaching/proposals/<person>/<monday>.yaml`: ephemeral proposal awaiting review.

The recurring program in `plans/<person>.yaml` remains canonical for ongoing programming. A weekly
snapshot can override loads, sets, reps, exercises, or dates for one week without changing that base.
An `ongoing` proposal change updates both the base and the target week; a `week` change updates only
the dated snapshot. Home A/B/C/D variants also live in the plan, while `config/locations.yaml`
records the equipment and constraints they were designed around.

## Fallback behavior

Evidence is deliberately allowed to be incomplete:

- Imported activity, no feedback: completed sets drive deterministic progression; otherwise hold.
- Future dated session: keep it planned; do not ask for attendance early.
- No activity and no attendance report: mark unresolved and continue the plan unchanged.
- Missed: record attendance; do not count it as failure and do not regress because of it.
- Partial: completed exercises can count; skipped exercises are not treated as failed sets.
- Pain, “too hard,” or technique breakdown: suppress an automatic increase and flag the issue.
- Recovery `normal`: context only; it never creates an increase.
- Recovery `caution`: suppress increases and otherwise hold the current prescription.
- Recovery `review`: suppress increases and ask how the person feels before proposing a week-only
  reduction.
- Missing readiness or another recovery source: treat the snapshot as partial and continue.

Feedback is optional. The coach may ask a short follow-up, but lack of an answer never blocks the
next week from being prepared.

## CLI workflow

Import recent objective data, then see what needs a check-in:

```bash
uv run gym import --person bogdan --since 7d
uv run gym coach check-in --person bogdan --as-of 2026-08-30
uv run gym coach reconcile bogdan --week 2026-08-24 --json
```

Record a simple report, a missed session, or a move:

```bash
uv run gym coach feedback bogdan --date 2026-08-24 --workout A \
  --energy 4 --difficulty 3 --recovery 4 --notes "Everything moved well"

uv run gym coach feedback bogdan --date 2026-08-25 --workout B --status partial \
  --exercise '{"exercise_id":"barbell_back_squat","status":"skipped","notes":"knee irritated"}' \
  --pain --pain-notes "Knee discomfort"

uv run gym coach missed bogdan --date 2026-08-27 --workout C --reason "Travel"
uv run gym coach reschedule bogdan --date 2026-08-29 --workout D \
  --to 2026-08-30 --reason "Schedule conflict"
```

Prepare, inspect, and explicitly apply the next week:

```bash
uv run gym coach context bogdan --week 2026-08-31 --json
uv run gym coach propose bogdan --week 2026-08-31 --json
uv run gym coach proposal bogdan --week 2026-08-31 --json
# after review:
uv run gym coach apply bogdan --week 2026-08-31 --json
uv run gym coach plan bogdan --week 2026-08-31 --json
```

If one gym session must happen at home, create a week-only location proposal. It does not alter the
recurring gym workout:

```bash
uv run gym coach locations --json
uv run gym coach location bogdan --week 2026-08-31 --workout A \
  --location home --reason "Working from home" --json
uv run gym coach proposal bogdan --week 2026-08-31 --json
# after review:
uv run gym coach apply bogdan --week 2026-08-31 --json
```

The included home variants use five exercises, two working sets, and short rests to fit under 30
minutes. Dumbbell and kettlebell loads are conservative starting prescriptions; band resistance and
bodyweight movements remain manual. Roxana's variants avoid jumping, lunges, step-ups, and deep knee
flexion for now, and every knee-dominant repetition must remain in a comfortable range.

`coach propose` supplies the safe deterministic baseline. Through MCP, an agent can replace it with a
validated coaching proposal containing `CoachChange` objects:

```json
{
  "kind": "load",
  "scope": "week",
  "workout_key": "A",
  "exercise_id": "barbell_bench_press",
  "old_value": 40.0,
  "new_value": 37.5,
  "rationale": "One-week recovery adjustment",
  "evidence": ["User reported poor recovery and unstable technique"],
  "source": "coach",
  "requires_review": false
}
```

Kinds are `load`, `sets`, `rep_range`, `exercise`, `schedule`, and `location`. Schedule and location
changes must be week-scoped; schedule changes stay within the target Monday–Sunday. Exercise
replacements contain a full
prescription and require an exact configured Garmin mapping. Old values are mandatory stale-write
guards. Excessive load jumps are converted to review flags and skipped during apply.

## Garmin handoff

Garmin templates contain exact numbers, not a future progression algorithm. Without `--week`, sync
publishes separate gym and home A/B/C/D templates using the recurring targets. Both sets remain
visible in Garmin. After an approved weekly plan, sync any exact selected-location adjustments and
then schedule it:

Each template's notes contain its current equipment manifest: required stations and implements plus
target loads. Dumbbell loads are labeled per hand, machine/cable loads as settings, and variable band
resistance as “choose resistance.” Workout-level equipment overrides handle venue-specific choices,
such as using a kettlebell for a home goblet squat.

```bash
uv run gym garmin diff bogdan --week 2026-08-31
uv run gym garmin sync bogdan --week 2026-08-31              # dry-run
uv run gym garmin sync bogdan --week 2026-08-31 --execute    # explicit external write
uv run gym garmin schedule bogdan --week 2026-08-31          # dry-run
uv run gym garmin schedule bogdan --week 2026-08-31 --execute
```

When only one session is approved, preview and schedule only that exact date/key. The location is
derived from the effective weekly plan, so a dated Home A selects `home:A` automatically:

```bash
uv run gym garmin schedule-session bogdan --date 2026-08-31 --workout A
uv run gym garmin schedule-session bogdan --date 2026-08-31 --workout A --execute
```

The command rejects a mismatched date/key, an unsynchronized selected-location prescription, and a
duplicate calendar entry. `schedule_week` remains available only for an explicitly approved bulk
operation.

Scheduling verifies that every remote template hash matches the target week's prescription. This
prevents an adjusted week from accidentally using stale weights. Template identities are stable
location/key pairs (`gym:A` through `gym:D`, `home:A` through `home:D`); weekly scheduling selects
the correct id without overwriting the other location's workout.

## Conversational and recurring check-ins

The repository-scoped `$gym-coach` skill teaches Codex the evidence and approval flow. A useful
conversation can be as short as “Workout A felt good; bench was easy, everything else on target” or
“I missed C and want to do it Friday.” The agent records only stated facts and shows changes before
applying them.

Every coaching interaction starts with `refresh_coaching_data(confirm=true, days=7)`. It performs a
bounded Garmin activity read plus today's recovery read, persists new normalized workouts and the
daily recovery snapshot, and only then returns recovery assessment, reconciliation, and pending
check-ins. This repository treats that import as standing-authorized local synchronization: the user
does not need to ask for it. It does not create, update, schedule, or delete anything in Garmin. If
activity refresh fails, the agent must identify its local evidence as stale and must not derive
progression changes from it. Missing optional recovery endpoints are reported as partial data.

For recurring interaction, schedule a Codex task that runs `refresh_coaching_data` after expected
training days, and a weekly task that refreshes before calling `get_coaching_context` and
`propose_next_week`. The bounded history import has standing authorization, but proposal apply,
outbound Garmin sync, and Garmin schedule remain approval-gated actions.

## MCP tools

The MCP surface includes reading plans, locations, and history; the mandatory compound
`refresh_coaching_data` activity/recovery import and reconciliation call; reading deterministic
recovery context; importing recent Garmin workouts directly;
recording feedback/attendance; reconciliation/adherence; coaching context;
deterministic and custom proposal creation; proposal inspection/application; weekly plan inspection;
pending check-ins; and week-aware Garmin diff/sync/schedule. Training-plan workouts and dated weekly
sessions include generated `equipment_notes` without a Garmin call. Garmin previews also include
`template_key`, `location`, and `notes`. MCP exposes both exact `schedule_session` and bulk
`schedule_week`; each external write requires `dry_run=false` and `confirm=true`. Local apply also
requires `confirm=true`.
