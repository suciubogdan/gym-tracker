# Garmin Connect research

Research date: 2026-08-24. This integration is based on observed consumer-web behavior, not a
supported personal API contract. Reverify after upgrading the client.

## Library choice

Use [`python-garminconnect`](https://github.com/cyberjunky/python-garminconnect), published on PyPI as
`garminconnect`. Version 0.3.11 is the initial tested floor and requires Python 3.12 or newer. It is
actively maintained, exposes the needed workout/activity operations, has retry/error translation,
and ships typed strength-workout models plus a 1,527-entry exercise catalog across 47 categories.

The official Garmin Connect Developer Program APIs are partner APIs and are not equivalent to
personal access to the consumer account. The application therefore treats every endpoint below as
unsupported and isolates it behind `GarminClient`.

## Verified library capabilities

| Requirement | Current library surface | Strategy |
| --- | --- | --- |
| Authentication | `Garmin.login(tokenstore)` with credential fallback and MFA callback | One token directory per logical person under `~/.config/gym-tracker/accounts/` |
| Token persistence | OAuth/session tokens load, refresh and dump through the token store | Never place tokens, passwords or email in the repository |
| Multiple accounts | Independent `Garmin` instance and token store | Adapter is constructed with `person` |
| List/read workouts | `get_workouts`, `get_workout_by_id` | Remote ids are authoritative only through sync state |
| Create strength workout | `StrengthWorkout`, `WorkoutSegment`, `create_strength_set`, `upload_workout` | Serialize only verified category/exercise pairs |
| Update workout | `update_workout(id, full_payload)` uses PUT and retains the id | Preferred sync path; schedules remain attached |
| Delete workout | `delete_workout(id)` | Used only after a fallback replacement is verified |
| Schedule/unschedule | `schedule_workout(id, YYYY-MM-DD)`, `get_scheduled_workouts`, `unschedule_workout` | Read before scheduling for idempotency |
| Activity range | `get_activities_by_date` | Client-side filtering accepts only strength types |
| Strength sets | `get_activity_exercise_sets(id)` | Normalize active sets; ignore rest sets |
| Activity details | `get_activity`, `get_activity_details` | Summary supplies name/time/duration/heart rate |

The relevant maintained source is visible in the library's
[`Garmin` implementation](https://github.com/cyberjunky/python-garminconnect/blob/master/garminconnect/__init__.py),
[`workout` models](https://github.com/cyberjunky/python-garminconnect/blob/master/garminconnect/workout.py),
and [`exercise` catalog](https://github.com/cyberjunky/python-garminconnect/blob/master/garminconnect/exercises.py).

## Observed payloads

Outgoing strength blocks are repeat groups containing a rep-ended interval and a timed rest.
`sportTypeId=5` / `strength_training` is supplied by the maintained model. Live verification showed
that Garmin's workout API interprets `weightValue` as kilograms even though `garminconnect` 0.3.11's
helper multiplies it by the kilogram unit factor. The adapter corrects the serialized value back to
the canonical kilogram target while retaining Garmin's unit metadata. Garmin represents only one
rep target, so this application sends the bottom of the local rep range; the full range remains
canonical YAML.

The completed strength endpoint returns an envelope resembling:

```json
{
  "activityId": 123,
  "exerciseSets": [{
    "setType": "ACTIVE",
    "duration": 26.9,
    "repetitionCount": 10,
    "weight": 40000.0,
    "exercises": [{"category": "BENCH_PRESS", "name": "BARBELL_BENCH_PRESS"}]
  }]
}
```

`REST` entries are not working sets. Completed-set `weight` is observed in grams and normalized to
kilograms. `exerciseSets` may be a list or a single object. The original activity id is the local
idempotency key.

## Exercise mapping strategy

Internal exercise ids never equal Garmin enums implicitly. `config/exercises.yaml` owns the explicit
mapping. Initial values were checked against the 0.3.11 bundled catalog. New mappings must come from
`gym garmin exercises search`, not memory or a derived spelling.

The bundled catalog is the safest practical inspection mechanism but is not a contract: independent
live research has observed that a catalog value can still be rejected by Garmin's service. A 400
`Invalid Sub-Category Passed` therefore means the mapping must be reverified, not coerced. Unknown
incoming mappings fail loudly and preserve diagnostic data instead of being silently assigned to a
similar internal exercise.

## Known uncertainty and risk

- Consumer endpoints can change without versioning or notice and may rate-limit or reject automation.
- Response shapes differ across account, locale, device firmware and activity provenance.
- Calendar reads can lag accepted REST writes. This implementation is idempotent on observed state,
  but an immediate repeat during Garmin propagation may still need care.
- Exercise auto-detection can produce category-only, `UNKNOWN`, or multiple probability candidates.
  The first explicit category/name pair is accepted only if it exactly matches the registry.
- Reps and weights can be absent. RIR/RPE is not generally captured and remains optional local data.
- Target weight behavior and workout availability vary by watch model.
- Live update-in-place is supported by the chosen client but remains unofficial. A guarded
  create/verify/delete fallback exists for client versions without update.
- No live account tests run in CI. Optional manual smoke testing is documented in the README.

## Update policy

Pin the client below the next minor line. Before changing the pin: review upstream workout and auth
changes, run unit tests, inspect one dry-run diff per account, serialize a sample strength workout,
and use a nonessential live workout for create/update/schedule verification.
