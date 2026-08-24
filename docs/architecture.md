# Architecture

## Design goals

Gym Tracker is a local-first command-line application. Git-tracked YAML is the source of truth;
Garmin Connect is an execution and data-capture system. No LLM is linked into the runtime and every
planning decision remains reproducible without Codex.

The dependency direction is deliberately one-way:

```text
CLI / MCP
    │
    ▼
application services
    ├── storage repositories ── YAML / JSON files
    ├── progression engine ──── pure Pydantic domain models
    ├── coaching service ────── reconciliation + validated weekly proposals
    └── GarminSyncService ───── GarminClient protocol
                                      ▲
                                      │
                              GarminConnectAdapter
                                      │
                              unofficial garminconnect
```

The domain package never imports `garminconnect`. The adapter translates both outgoing workouts and
incoming activities. Tests use `FakeGarminClient`, so no test requires an account or network access.

## Sources of truth

- `plans/<person>.yaml`: canonical gym workouts, location variants, targets, phase and schedule.
- `config/exercises.yaml`: internal exercise registry and separately verified Garmin mappings.
- `config/locations.yaml`: available equipment, venue constraints, and person-specific limitations.
- `config/progression.yaml`: deterministic progression and safety policy.
- `data/imported/<person>/<activity-id>.yaml`: normalized, inspectable completed training history.
- `data/imported/<person>/daily/<date>.yaml`: normalized Garmin recovery context and availability.
- `data/sync/<person>.yaml`: remote workout ids and last-synchronized content hashes.
- `data/proposals/<person>.yaml`: ephemeral proposal, ignored by Git until explicitly desired.
- `data/attendance/` and `data/feedback/`: structured user reports tied to planned sessions.
- `weeks/<monday>/<person>.yaml`: approved dated snapshot; base plans remain ongoing truth.
- `data/coaching/proposals/`: ephemeral feedback-aware proposals, ignored by Git.
- `data/raw/`: diagnostic source responses, always ignored because they are noisy health data.

Garmin ids do not enter plan files. Losing sync metadata causes a repair/create proposal, never a
blind name match or deletion.

## Core workflows

Import first lists activities for a bounded date range, filters strength activities, reads the
dedicated exercise-set document, requires every Garmin category/name pair to map internally, writes
a sanitized raw diagnostic record, and atomically writes one normalized file per activity id.
Existing activity files are skipped before a detail request, making imports idempotent. The validated
activity-list summary is passed into detail normalization because Garmin detail responses may omit
or malform start time, name, duration, heart rate, or activity type; detail values win when usable.
The agent-facing refresh also reads the current day's Training Status/load, Training Readiness, HRV,
sleep, resting heart rate, and Body Battery through the same account client. Only a bounded daily
snapshot is portable; endpoint payloads remain ignored raw diagnostics. Optional unsupported sources
are recorded as unavailable instead of blocking completed-activity import.

Progression reads the plan and history and produces a proposal tied to a hash of the input plan.
Apply refuses a stale proposal. The introductory phase requires repeated upper-bound success. Load
jumps exceeding the configured percentage become review items and are not applied.

Sync calculates a hash from each workout plus its Garmin exercise mappings. Every location and
A/B/C/D pair has a stable template key such as `gym:A` or `home:A`, so both variants remain visible
in Garmin. Legacy `A/B/C/D` state entries migrate to `gym:A/B/C/D` without changing their remote ids.
Sync creates missing workouts, updates changed workouts in place, and does nothing to unchanged
workouts. State is saved after every verified remote mutation so a partial run can safely resume.
The adapter has a fallback for old clients: create replacement, verify it appears, then delete the
obsolete template.

The Garmin workout description is derived, not hand-maintained. For every prescription, the domain
resolves a workout-level equipment override first and otherwise uses `config/exercises.yaml`. It
then emits the station plus the current target load (`kg each` for dumbbells and `kg setting` for
machines/cables). The generated equipment summary participates in the sync hash, so changing a load,
station, or equipment definition produces an update rather than stale notes.

Scheduling requires the synchronized id for the dated session's location, reads existing calendar
entries, and skips matching workout/date pairs. `schedule_session` resolves one exact date/key from
the effective dated plan; `schedule_week` is the explicit bulk path and requires a Monday. Neither
accepts a caller-selected location. When a weekly snapshot exists, diff/sync with `--week` serializes
its exact selected-location prescriptions and scheduling rejects stale remote hashes.

Coaching reconciliation joins the dated plan, explicit attendance, optional feedback, and imported
activities. Matching is exact by stored Garmin activity id or workout name within the planned/effective
date window; it never fuzzily assigns health history. The deterministic engine remains the baseline.
Agent-authored changes are typed, stale-checked, scope-checked, and safety-checked before they can be
saved. Application is a separate action and materializes a weekly snapshot. A location change
replaces only the selected dated A/B/C/D session with its configured variant; it does not rewrite
the recurring gym program. Distinct workout names keep home evidence out of gym load progression.
The agent-facing `refresh_coaching_data` application operation always runs the bounded Garmin import
and daily recovery normalization before returning recovery assessment, reconciliation, and pending
check-ins, so the conversational protocol has one ordered entry point and cannot accidentally
present pre-import evidence as current. Recovery assessment is pure and threshold-driven. It may
suppress an increase or request review, but it cannot create an increase or apply a reduction.

## Boundaries and failure policy

- Unknown internal ids fail validation.
- Missing or unknown Garmin mappings fail before upload/import rather than guessing.
- Malformed or drifted Garmin payloads fail in the adapter while raw information remains available.
- CLI Garmin sync/schedule and MCP equivalents default to preview/dry-run.
- Local proposal application and every external mutation use separate explicit commands.
- Missing subjective feedback never blocks planning; missed workouts are attendance, not failed sets.
