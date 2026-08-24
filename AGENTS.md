# Codex operating guide

## Repository contract

This is a local-first strength planner. `plans/*.yaml`, `config/exercises.yaml`, normalized
`data/imported/`, and explicit progression policy are the source of truth. Garmin is an execution and
capture target, never the canonical plan. The application must work without AI and must not call an
LLM API.

Portable personal state is intentionally Git-trackable in `data/imported/`, `data/attendance/`,
`data/feedback/`, `data/sync/`, and `weeks/`. Treat the repository as private health-adjacent data.
Never commit `data/raw/`, transient proposals, Garmin credentials/tokens, live Hermes
configuration/state, or WhatsApp sessions. Sanitized configuration examples are allowed. When
syncing data, stage only the named portable paths and stop on conflicts.

Architecture flows from CLI/MCP → `GymService` → pure domain/storage or the `GarminClient` protocol.
Keep Garmin-library imports in `gym_tracker/garmin/`. Keep business rules out of the CLI, MCP server,
and adapter. Read `docs/architecture.md` and `docs/garmin-research.md` before integration changes.

## Important commands

```bash
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run gym status bogdan --json
uv run gym progress propose bogdan --json
uv run gym garmin diff bogdan --json
uv run gym garmin sync bogdan --dry-run --json
```

## Plan invariants

- People are `bogdan` and `roxana`, each with A/B/C/D and four configured weekdays.
- Both programs stay full-body. Preserve Bogdan's upper-body/chest/back/shoulder bias and Roxana's
  glute/leg bias unless the user explicitly changes goals.
- Default working philosophy is approximately two sets, mostly 8–12, about six exercises and 45–60
  minutes. These are data conventions, not code constants.
- Do not change exercise selection merely because a load should progress.
- Equipment and `pairing_key` fields preserve future couple-training optimization.
- Validate round-trip serialization after every schema change.

## Progression invariants

Progression is deterministic double progression. During the introductory session count, require the
configured repeated success. Missing RIR never blocks the engine. Manual overrides win. Loads above
the maximum percentage change become review flags and are not applied. `progress propose` may write
only a proposal; `progress apply` is the separate local mutation and must reject stale plan hashes.

Always show the user a proposal before applying it. A request to analyze or propose is not approval
to apply.

## Coaching rules

Reconcile dated plans, explicit attendance, feedback, and exact Garmin activity identity before
coaching. Never invent subjective feedback. Missing feedback must not block the next week: use
objective sets when present and otherwise continue unchanged. Missed workouts are attendance, not
failed progression; partial sessions count only completed exercises.

Coach changes are typed and must state week/ongoing scope, old/new values, rationale, and evidence.
Pain, excessive difficulty, or technique breakdown suppresses automatic increases and prompts
review, not diagnosis. Preserve deterministic progression as the baseline, manual overrides, load
jump review flags, and exact exercise mappings. Always show a weekly proposal before local apply.

## Garmin rules

- Never invent a Garmin category or exercise value. Search the installed catalog and verify the
  exact pair before editing `config/exercises.yaml`.
- Unknown outgoing or incoming mappings fail loudly. Do not fuzzy-match health history.
- Credentials/tokens belong only under `~/.config/gym-tracker/`; never log or commit them.
- Raw Garmin health payloads belong in ignored `data/raw/`. Keep logs structural and sanitized.
- Sync identity is the stored remote id plus local content/mapping hash, not workout name.
- Prefer in-place PUT update. Replacement order is create → verify → update state → delete obsolete;
  if verification fails, retain the old workout.
- Scheduling must use the plan's `weekly_schedule`, not hard-coded weekdays.
- Use a fresh `garmin diff` / dry-run before every external mutation.

## Operation classes

Read-only: plan/history/status, proposal inspection, Garmin exercise search, Garmin diff, dry-run sync
and dry-run schedule.

Local mutations: importing normalized history, writing a proposal, applying a reviewed proposal,
recording attendance/feedback, materializing a weekly plan, and updating sync metadata after a
verified operation. Explain which files change and inspect Git diff.

External mutations: Garmin login, real sync/create/update/delete, and real scheduling. Never infer
approval from a request to review, analyze, diff or prepare. CLI must require `--execute`; MCP must
require `dry_run=false` and `confirm=true`.

## Testing expectations

Use `FakeGarminClient` for almost all tests. Live credentials are never a test prerequisite. Cover
progression boundaries, introduction behavior, failed/missing sets, rounding, serialization,
activity-id idempotency, strict exercise mapping, sync create/update/no-op/repair, replacement safety,
schedule idempotency, and independent accounts. Run pytest, Ruff and mypy before handoff.
