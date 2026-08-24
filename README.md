# Gym Tracker

Gym Tracker is a production-minded, local-first CLI for managing two people's strength programs.
Human-readable YAML and Git history are canonical. Garmin Connect sends planned sessions to watches
and supplies completed set data; Codex can operate the same stable application API through MCP. The
runtime never calls an LLM.

The included starting programs provide four full-body sessions per week: Bogdan is biased toward
chest, back and shoulders; Roxana is biased toward glutes and legs. Loads are placeholders for a
conservative restart—review them before training and use judgment appropriate to your experience.
This is a training-management tool, not medical advice.

## Install

Requirements: macOS or another supported Python platform, Python 3.12+, and
[`uv`](https://docs.astral.sh/uv/).

```bash
uv sync --all-groups
uv run gym --help
uv run pytest
```

`uv.lock` should be committed. Ruff and mypy are configured in `pyproject.toml`:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

See [architecture](docs/architecture.md) for layer boundaries and
[Garmin research](docs/garmin-research.md) for verified capabilities and uncertainty. The complete
[agentic coaching workflow](docs/coaching.md) describes feedback, missed sessions, weekly snapshots,
and the conversational coach.

To run the agentic layer on another host and talk to it through WhatsApp, see the
[Hermes deployment guide](docs/hermes.md). Use a private Git remote because normalized history,
attendance, feedback, weekly plans, and Garmin workout identifiers are portable personal data.

## Plan and exercise format

Plans live in `plans/bogdan.yaml` and `plans/roxana.yaml`. Each workout contains internal exercise
ids, working sets, a rep range, target kilograms, rest and an optional future equipment-sharing key.
The introductory phase and weekly days are data, not hard-coded logic.

```yaml
- id: barbell_bench_press
  sets: 2
  rep_range: [8, 12]
  target_weight_kg: 40
  progression: double_progression
  rest_seconds: 120
  pairing_key: bench_1
```

Internal exercise definitions and increments live in `config/exercises.yaml`. Garmin mappings are
separate and exact. Never invent them. Search the maintained catalog first:

```bash
uv run gym garmin exercises search "bench"
uv run gym garmin exercises search "bench" --json
```

A missing mapping stops sync. An unknown incoming Garmin exercise stops normalization with a useful
error instead of recording the wrong movement.

## Garmin authentication

Run the interactive login once per logical account:

```bash
uv run gym garmin login bogdan
uv run gym garmin login roxana
```

Email/password are used only for login and never saved by this application. Garmin session/OAuth
tokens are persisted by the client under `~/.config/gym-tracker/accounts/<person>/`, outside the
repository, with a private directory mode. MFA is prompted when Garmin requires it. Do not copy this
directory into Git or logs.

This uses unofficial consumer endpoints. It may break when Garmin changes its web application, and
heavy automation may be rate-limited. There is no guarantee from Garmin.

## Import completed workouts

```bash
uv run gym import --person bogdan --since 7d
uv run gym import --person roxana --since 7d
uv run gym import --all --since 7d --json
```

One normalized YAML file is written per Garmin activity id under `data/imported/<person>/`; a repeat
import skips it. Raw diagnostic responses live in ignored `data/raw/` because they are noisy health
data. Commit normalized history when you want it represented in Git.

## Status and progression

```bash
uv run gym status bogdan
uv run gym status roxana --json
uv run gym progress propose bogdan
uv run gym progress propose bogdan --json
```

Double progression increases the load only after all prescribed sets reach the top of the range at
the current load. During the configured eight-session introduction, two qualifying sessions are
required. A configured maximum percentage caps accidental jumps. Missing reps maintain the load;
optional regression is off by default. RIR and notes can be added to normalized local set data but
are not required.

Proposal generation writes an ephemeral proposal without changing a plan. Review it, then apply:

```bash
uv run gym progress apply bogdan
git diff -- plans/bogdan.yaml
```

Apply refuses if the plan changed after proposal generation. Review flags are never applied.

## Diff, sync and scheduling

Read-only diff:

```bash
uv run gym garmin diff bogdan
```

Sync and scheduling default to dry-run. A real Garmin mutation requires `--execute`:

```bash
uv run gym garmin sync bogdan                 # preview
uv run gym garmin sync bogdan --execute       # external mutation
uv run gym garmin sync bogdan --week 2026-08-31
uv run gym garmin schedule bogdan --week 2026-08-31
uv run gym garmin schedule bogdan --week 2026-08-31 --execute
```

Sync state under `data/sync/` records each Garmin workout id and a content/mapping hash. An unchanged
run creates nothing. Changed workouts update in place; a compatibility fallback creates and verifies
a replacement before deleting the obsolete workout. State is persisted after every successful
mutation. Scheduling reads the calendar and skips the same workout/date pair.

Always inspect dry-run output immediately before `--execute`. Garmin deletions are limited to the
guarded compatibility fallback; this tool does not delete unrelated remote workouts.

Garmin workouts contain exact reps and kilograms. They do not evaluate double progression on the
watch. Base sync publishes the current recurring A/B/C/D prescriptions; after approving a dated
weekly plan, use `--week` to publish that week's exact numbers before scheduling it.

## Feedback-aware weekly coaching

The optional coaching layer reconciles planned sessions, imported Garmin activity, attendance, and
subjective feedback. Missing feedback is allowed, missed workouts are not counted as failed sets,
and the deterministic progression engine remains the safe baseline.

```bash
uv run gym coach check-in --person bogdan
uv run gym coach feedback bogdan --date 2026-08-24 --workout A \
  --energy 4 --difficulty 3 --notes "Bench felt easy"
uv run gym coach missed bogdan --date 2026-08-25 --workout B --reason "Travel"
uv run gym coach propose bogdan --week 2026-08-31 --json
# inspect, then explicitly apply locally:
uv run gym coach apply bogdan --week 2026-08-31
uv run gym garmin sync bogdan --week 2026-08-31
```

Application writes attendance and feedback under `data/`, and an approved dated snapshot under
`weeks/<monday>/`. One-off changes stay in the week; ongoing changes also update the base plan. The
repository-scoped `$gym-coach` skill provides the conversational layer and approval policy.

## MCP setup for Codex

The MCP server wraps `GymService`; it contains no planning logic. Start it over stdio with:

```bash
uv run gym-mcp
```

Configure Codex with the absolute repository working directory and command `uv run gym-mcp`. In
addition to plan, progression, and Garmin tools, the server exposes confirmed history import,
attendance/feedback, reconciliation/adherence, coaching context, pending check-ins, reviewable weekly
proposals, and dated plan inspection. See [agentic coaching workflow](docs/coaching.md) for the full
tool and approval flow.

Mutating MCP tools require `confirm=true`. Garmin tools additionally default to `dry_run=true`.
Import stays a CLI operation initially so credential/network diagnostics remain explicit.

## Suggested weekly workflow

```bash
uv run gym import --all --since 7d
uv run gym status bogdan
uv run gym status roxana
uv run gym progress propose bogdan
uv run gym progress propose roxana
# inspect proposals, then:
uv run gym progress apply bogdan
uv run gym progress apply roxana
git diff
uv run pytest
uv run gym garmin sync bogdan
uv run gym garmin sync roxana
# only after approval:
uv run gym garmin sync bogdan --execute
uv run gym garmin sync roxana --execute
```

## Troubleshooting

- “No Garmin session”: rerun `gym garmin login <person>`; tokens may have expired or been revoked.
- MFA/login failures: verify the account in Garmin Connect first and retry without rapid loops.
- Unknown exercise mapping: retain raw data, search the catalog, and update the explicit registry.
- Garmin HTTP 400 on an apparently valid exercise: catalog presence is not proof of live acceptance;
  inspect `data/raw/`, verify through Garmin's current UI, and do not guess a substitute.
- Stale proposal: rerun `gym progress propose` after any plan edit.
- A scheduled item appears late: Garmin calendar reads can lag writes; wait and diff again.
- Unexpected API shape: run with `--verbose`, sanitize any report, and consult
  `docs/garmin-research.md`. Logs intentionally avoid secrets and full health payloads.

Optional live smoke tests should use one disposable workout per account and proceed create → verify →
update → verify → schedule → verify. Normal pytest uses fakes only and needs no credentials.
