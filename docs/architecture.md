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
Existing activity files are skipped before a detail request, making imports idempotent.

Progression reads the plan and history and produces a proposal tied to a hash of the input plan.
Apply refuses a stale proposal. The introductory phase requires repeated upper-bound success. Load
jumps exceeding the configured percentage become review items and are not applied.

Sync calculates a hash from each workout plus its Garmin exercise mappings. It creates missing
workouts, updates changed workouts in place, and does nothing to unchanged workouts. State is saved
after every verified remote mutation so a partial run can safely resume. The adapter has a fallback
for old clients: create replacement, verify it appears, then delete the obsolete template.

Scheduling requires a synchronized id, validates that `--week` is a Monday, reads existing calendar
entries, and skips matching workout/date pairs. When a weekly snapshot exists, diff/sync with
`--week` serializes its exact prescriptions and scheduling rejects stale remote hashes.

Coaching reconciliation joins the dated plan, explicit attendance, optional feedback, and imported
activities. Matching is exact by stored Garmin activity id or workout name within the planned/effective
date window; it never fuzzily assigns health history. The deterministic engine remains the baseline.
Agent-authored changes are typed, stale-checked, scope-checked, and safety-checked before they can be
saved. Application is a separate action and materializes a weekly snapshot. A location change
replaces only the selected dated A/B/C/D session with its configured variant; it does not rewrite
the recurring gym program. Distinct workout names keep home evidence out of gym load progression.

## Boundaries and failure policy

- Unknown internal ids fail validation.
- Missing or unknown Garmin mappings fail before upload/import rather than guessing.
- Malformed or drifted Garmin payloads fail in the adapter while raw information remains available.
- CLI Garmin sync/schedule and MCP equivalents default to preview/dry-run.
- Local proposal application and every external mutation use separate explicit commands.
- Missing subjective feedback never blocks planning; missed workouts are attendance, not failed sets.
