# LinearDB

LinearDB is the Eidos product category for turning Linear into a durable local
database and operational intelligence layer.

It is not a Linear UI clone. It is a data-model mirror with analytics features:
Linear data is copied onto this machine, stored as queryable SQLite tables, and
then analyzed locally.

- OAuth app authentication for installed Linear user accounts.
- Workspace and team visibility validation.
- Local SQLite/DuckDB-ready sync.
- Current-state issue tables.
- Time-series issue snapshots.
- Comments, attachments, issue history, and state spans.
- Token-safe credential handling and retry behavior.
- Local analytics over the mirror without additional Linear API calls.

## Current Implementation

This directory now contains the standalone LinearDB package and CLI:

```text
/Users/dshanklinbv/repos-eidos-agi/lineardb
```

The product plan and acceptance criteria live in `PLAN.md`.

## Install

Until the first release is tagged, install from source:

```bash
pipx install git+https://github.com/eidos-agi/lineardb.git
```

LinearPlus can consume LinearDB for connectivity and local sync, then layer
LinearPlus-specific workflows on top:

- initiative lookup/create/attach
- Greenmark AI Search Visibility bootstrap
- Greenmark task analytics
- Greenmark/account dump CLI routing

## CLI Shape

Connect the first Greenmark profile for Daniel's Eidos login:

```bash
export LINEARDB_GREENMARK_OAUTH_CLIENT_ID=<client-id>
export LINEARDB_GREENMARK_OAUTH_CLIENT_SECRET=<client-secret>

bin/lineardb --account greenmark connect
```

The default `greenmark` account policy expects the connected Linear viewer to
be `daniel@eidosagi.com`. That login may see many Linear teams; `GMW` is the
required validation team for Greenmark workflows, not the only team LinearDB
should store. Tokens are stored in a local SQLite credential store at
`~/.lineardb/credentials.sqlite` unless `LINEARDB_TOKEN_DB` points somewhere
else.

Dry-run the Greenmark account guard without calling Linear:

```bash
bin/lineardb --account greenmark auth-check --dry-run --team-key GMW
```

Verify live credentials before any Greenmark sync:

```bash
bin/lineardb --account greenmark auth-check --team-key GMW
```

Mirror all visible Linear teams into a local SQLite database:

```bash
bin/lineardb --account greenmark sync \
  --sqlite outputs/greenmark-linear.sqlite
```

Use `--skip-related` only for a fast current-state refresh. The full mirror
includes comments, attachments, issue history, and state spans.

Analyze an existing mirror without calling Linear:

```bash
bin/lineardb analytics \
  --sqlite outputs/greenmark-linear.sqlite \
  --team-key GMW
```

## SQLite Tables

Current-state tables:

- `account_profiles`
- `organizations`
- `account_organizations`
- `account_teams`
- `teams`
- `users`
- `projects`
- `team_projects`
- `labels`
- `issue_labels`
- `issues`

Time-series and related-data tables:

- `sync_runs`
- `issue_snapshots`
- `comments`
- `attachments`
- `issue_history`
- `issue_state_spans`

Every object table keeps `raw_json` so the mirror can preserve Linear fields
before every field has a first-class column.

Relationship tables are intentionally explicit:

- `account_organizations` ties a local account profile to the Linear
  organization/workspace returned by OAuth.
- `account_teams` ties that account profile to every visible team returned by
  the token.
- `team_projects` ties teams to projects discovered through issues.
- The credential store also keeps `oauth_token_teams`, tying an OAuth token
  account to every team visible during `connect` and marking the required team
  such as `GMW`.

## Analytics

`lineardb analytics` reads only the local SQLite mirror. It reports:

- totals for issues, open, completed, canceled, unassigned, and no-project work
- counts by team, state type, state, priority, assignee, project, and label
- stale open issue samples
- snapshot run history
- state trends from `issue_snapshots`

## Verification

Run the local gate:

```bash
python verify.py
```

This runs unit tests plus dry-run auth/sync checks. It does not call Linear.

## Required Guard

Before any Greenmark run, the active credential must pass:

```bash
cd /Users/dshanklinbv/repos-eidos-agi/lineardb
bin/lineardb --account greenmark auth-check --team-key GMW
```

If `has_required_team` is not `true`, stop. The credential is not Greenmark
ready.

## Preferred Auth Shape

LinearDB is OAuth-only. For the first version, create one LinearDB OAuth app and
connect the `greenmark` profile with Daniel's `daniel@eidosagi.com` Linear
login. The OAuth app must include this local callback URL:

```text
http://localhost:8721/oauth/callback
```

Set the app credentials in the shell or local secret manager before connecting:

```bash
LINEARDB_GREENMARK_OAUTH_CLIENT_ID
LINEARDB_GREENMARK_OAUTH_CLIENT_SECRET
LINEARDB_GREENMARK_OAUTH_SCOPE=read
```

Explicit account profiles fail closed. `--account greenmark` uses only
the stored OAuth installation and account-scoped `LINEARDB_GREENMARK_*` OAuth
app settings. It must not fall back to ambient Linear credentials or personal
API keys.
