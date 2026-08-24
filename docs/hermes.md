# Running the coach with Hermes and WhatsApp

Hermes can run the existing `gym-mcp` server as a local stdio subprocess and discovers the
repository's `.agents/skills/gym-coach` skill. The clone contains plans and portable personal state;
the Hermes host supplies private Garmin and WhatsApp credentials.

## Data boundary

Use a private Git remote. These paths are intentionally portable and may be committed:

- `plans/` and `config/`: ongoing program and deterministic policy.
- `data/imported/`: normalized completed Garmin sets.
- `data/attendance/` and `data/feedback/`: personal reports.
- `data/sync/`: account-specific Garmin workout IDs and content hashes; these are identifiers, not
  login credentials.
- `weeks/`: approved dated prescriptions.

Do not commit `data/raw/`, `data/proposals/`, `data/coaching/proposals/`, `~/.config/gym-tracker/`,
`~/.hermes/`, `.env`, Garmin credentials/tokens, or WhatsApp sessions. The ignored proposals are
review buffers; only an applied weekly plan needs to travel between machines.

## Install a clone

```bash
git clone <private-repository-url> workout-plans
cd workout-plans
uv sync --all-groups
uv run pytest
```

Garmin OAuth/session tokens deliberately do not travel with Git. Authenticate each person once on
the Hermes host:

```bash
uv run gym garmin login bogdan
uv run gym garmin login roxana
```

This writes tokens below `~/.config/gym-tracker/accounts/`, outside the clone.

## Connect Hermes to gym-tracker

Merge [the example configuration](../.hermes/config.example.yaml) into
`~/.hermes/config.yaml`. Replace the repository path and the `uv` path (`command -v uv`) with
absolute paths. The gateway uses `terminal.cwd`, so pointing it into this checkout also enables
project instructions and skill discovery.

Then trust this repository and verify the MCP server:

```bash
cd /absolute/path/to/workout-plans
hermes skills trust
hermes mcp test gym_tracker
```

Hermes prefixes the tools as `mcp_gym_tracker_*`. The configuration exposes the coaching workflow
but omits the older direct progression-apply tools. Mutations are still guarded: imports and local
proposal application require `confirm=true`; Garmin writes require both `dry_run=false` and
`confirm=true`.

## Connect WhatsApp

Run the guided setup and select WhatsApp:

```bash
hermes gateway setup
hermes gateway install
hermes gateway start
```

Set `WHATSAPP_ALLOWED_USERS` in `~/.hermes/.env` to your full phone number with country code, without
`+` or spaces. Keep `whatsapp.unauthorized_dm_behavior: "ignore"` from the example configuration.
Protect `~/.hermes/platforms/whatsapp/session/` like a password.

Hermes' quick personal-account bridge emulates WhatsApp Web and carries a small account-restriction
risk. Prefer a dedicated number and conversational traffic. For a supported production route, use
Hermes' WhatsApp Business Cloud API adapter instead; it requires Meta Business setup and a public
HTTPS webhook.

## Conversation and Git synchronization

From WhatsApp, messages such as these should activate the project skill:

- “I finished Bogdan A. Bench felt easy; everything else was on target.”
- “I missed C because of travel.”
- “I am working from home; switch Bogdan B this week to the home version.”
- “Review this week and propose next week.”
- “Show me the Garmin dry-run for next week.”

For a home request, the agent reads the configured location inventory and proposes the matching
week-only A/B/C/D variant. It must show a proposal before applying it and must separately preview
Garmin before any external write. Give Hermes an explicit standing instruction if you want portable personal-data
changes committed and pushed after each successful interaction. Its allowed Git scope is:

```text
plans/ data/imported/ data/attendance/ data/feedback/ data/sync/ weeks/
```

Keep code/config/skill changes in a separate reviewed commit. On a push conflict, Hermes should stop
and ask rather than force-push or discard another clone's data.

## Updating the deployment

Before pulling on a second machine, ensure its personal-data changes are committed and pushed. Then:

```bash
git pull --ff-only
uv sync --all-groups
uv run pytest
hermes mcp test gym_tracker
```

After changes to `~/.hermes/config.yaml`, use `/reload-mcp` in an active Hermes conversation or
restart the gateway.
