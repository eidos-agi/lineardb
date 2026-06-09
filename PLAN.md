# LinearDB Plan

LinearDB is a local Linear data-model mirror with analytics features. It is not
a replacement Linear UI and not a write-back task management product.

## Product Boundary

LinearDB owns:

- Account-scoped credential profiles.
- OAuth installed-user credential profiles with refresh-token rotation.
- Workspace identity and visible team validation.
- Read-only Linear GraphQL collection.
- Local SQLite mirror schema.
- Time-series snapshots for repeated syncs.
- Related task data: comments, attachments, history, and state spans.
- Local analytics over SQLite.

LinearPlus owns:

- Initiative workflows.
- Greenmark-specific workflow naming and bootstrap commands.
- Plugin compatibility while LinearPlus is migrated to consume LinearDB.

## V1 Scope

The first planned slice is a single explicit account profile, `greenmark`,
connected through Daniel's `daniel@eidosagi.com` Linear login and mirrored into
one SQLite database.

Accepted account hierarchy:

- `account_profile`: local credential/profile name such as `greenmark`.
- `viewer`: Linear user identity returned by OAuth, expected to be
  `daniel@eidosagi.com` for `greenmark`.
- `organization`: Linear workspace identity returned by `viewer.organization`.
- `team`: any Linear team visible to `daniel@eidosagi.com`; `GMW` is required
  for Greenmark validation but is not the only expected team.
- `issue`: Linear task within a team.
- related issue records: comments, attachments, history, state spans.

Teams are not modeled as user accounts. The core use case is that Daniel's
single `daniel@eidosagi.com` login can see many teams, and LinearDB must retain
all of those account-team relationships while still proving that `GMW` is
available for Greenmark-specific workflows.

## Done Criteria

V1 is plan-ready when all of these are true:

- `bin/lineardb --account greenmark auth-check --team-key GMW` fails closed
  without falling back to ambient credentials or personal API keys when no
  Greenmark OAuth installation is present.
- `bin/lineardb --account greenmark connect` performs a local OAuth callback,
  validates viewer email `daniel@eidosagi.com`, validates team `GMW`, stores
  the token record locally, and records all visible teams for that token.
- `bin/lineardb --account greenmark sync --dry-run` reports the intended local
  SQLite path without calling Linear.
- `bin/lineardb sync` mirrors teams, issues, snapshots, comments, attachments,
  history, and state spans when a valid credential is present.
- `bin/lineardb analytics --sqlite <db> --team-key GMW` reads only SQLite and
  returns task analytics.
- `python verify.py` passes.

## Current Status

Implemented:

- Account-scoped credential resolution with fail-closed explicit accounts.
- Local OAuth callback flow for installed user accounts.
- Stored OAuth access/refresh tokens with refresh-token rotation.
- Default `greenmark` validation for `daniel@eidosagi.com` and required team
  `GMW`, while preserving every visible team for that login.
- Token-safe Linear GraphQL client with retry for transient failures.
- Account mirror collection across visible teams.
- Viewer/organization metadata captured in `account_profiles`.
- Explicit relationship tables for `account_organizations`, `account_teams`,
  `team_projects`, and credential-store `oauth_token_teams`.
- SQLite current-state tables and time-series `issue_snapshots`.
- Related issue tables for comments, attachments, history, and state spans.
- Atomic SQLite writes that preserve the prior database on write failure.
- Local analytics over the SQLite mirror.
- Unit tests and repo-local verification gate.

Blocked for live Greenmark proof:

- This shell does not currently contain
  `LINEARDB_GREENMARK_OAUTH_CLIENT_ID/LINEARDB_GREENMARK_OAUTH_CLIENT_SECRET`,
  and the `greenmark` OAuth profile has not been connected locally.

## Live Bring-Up Plan

### Stage 1: OAuth App And Secret Injection

Goal: make the local `greenmark` profile capable of starting the OAuth install
without exposing secrets in chat or repo files.

- Create or reuse the LinearDB OAuth app.
- Configure callback URL `http://localhost:8721/oauth/callback`.
- Keep scope read-only: `read`.
- Inject `LINEARDB_GREENMARK_OAUTH_CLIENT_ID` and
  `LINEARDB_GREENMARK_OAUTH_CLIENT_SECRET` through a local shell, Keychain, or
  Daniel-owned secret manager.
- Prove presence without printing values.
- Run `bin/lineardb --account greenmark connect --dry-run`.

Exit criteria:

- Dry-run emits an authorize URL.
- Dry-run shows `expected_email: daniel@eidosagi.com`.
- Dry-run shows `required_team_key: GMW`.

### Stage 2: OAuth Install And Many-Team Validation

Goal: connect Daniel's `daniel@eidosagi.com` Linear login and prove that the
single OAuth token can see many teams while requiring `GMW`.

- Run `bin/lineardb --account greenmark connect`.
- Approve with the `daniel@eidosagi.com` Linear login.
- Query `~/.lineardb/credentials.sqlite` for `oauth_tokens` metadata.
- Query `oauth_token_teams` and confirm it contains all visible team keys, not
  only `GMW`.
- Confirm exactly one row is marked `validated_required = 1` for `GMW`.

Exit criteria:

- Stored token profile is `greenmark`.
- Stored viewer email is `daniel@eidosagi.com`.
- `oauth_token_teams` contains `GMW` plus the other teams visible to Daniel.
- No token values are printed or written to repo files.

### Stage 3: Fast Mirror Proof

Goal: prove the mirror can write a local SQLite database across all visible
teams without the slower related-data crawl.

- Run `bin/lineardb --account greenmark auth-check --team-key GMW`.
- Run:

  ```bash
  bin/lineardb --account greenmark sync \
    --sqlite outputs/greenmark-linear.sqlite \
    --skip-related
  ```

- Query `account_profiles`, `account_organizations`, `account_teams`, `teams`,
  `issues`, `projects`, and `team_projects`.
- Confirm `account_teams` contains many teams for `greenmark`.
- Confirm `issues` are distributed across team keys.

Exit criteria:

- `auth-check` returns `has_required_team: true`.
- SQLite file exists under `outputs/`.
- `account_teams` has more than one team when Daniel's login exposes more than
  one team.
- `GMW` issues are present and filterable.

### Stage 4: Resumable Related-Data Sync

Goal: collect the full task context needed for analytics and time-series work.

- Start with a bounded related-data batch:

  ```bash
  bin/lineardb --account greenmark sync-related \
    --sqlite outputs/greenmark-linear.sqlite \
    --team-key GMW \
    --limit 25 \
    --progress
  ```

- Resume by re-running the same command. Completed issues are skipped by
  default.
- Run without `--limit` when the bounded batches are healthy.
- Use `--retry-failed` to revisit failed issues.
- Validate row counts for `comments`, `attachments`, `issue_history`, and
  `issue_state_spans`.
- Confirm `related_sync_status` records `done`, `failed`, and in-progress
  state per issue.

Exit criteria:

- Related tables are populated when Linear returns related records.
- Interrupted related syncs can resume without re-fetching completed issues.
- Failed issues are visible and can be retried.

### Stage 5: Analytics Proof

Goal: prove local-only analytics over the mirror, with Greenmark filtered views
and many-team account-level views.

- Run:

  ```bash
  bin/lineardb analytics \
    --sqlite outputs/greenmark-linear.sqlite \
    --team-key GMW
  ```

- Add or run an account-level analytics command/report that does not filter to
  `GMW`.
- Compare counts from `analytics.teams` to SQL counts from `issues`.

Exit criteria:

- GMW analytics return task totals, states, assignees, projects, labels, stale
  samples, and snapshot history.
- Account-level analytics show all visible teams for Daniel's login.
- SQL counts and analytics counts agree.

### Stage 6: Product Hardening

Goal: turn the proof into a durable local product surface.

- Add a `relationships` or `inspect` CLI command that prints non-secret account,
  organization, team, project, and token-team relationships.
- Add account-level related sync scheduling after the GMW path is proven.
- Add regression tests for many-team sync from one account profile.
- Add docs for common SQL joins over `account_teams`, `team_projects`, and
  `issue_snapshots`.
- Decide whether the single SQLite mirror should support multiple account
  profiles in one file before adding a second login.

Exit criteria:

- A user can inspect relationships without opening SQLite manually.
- The many-team account model is tested and documented.
- Adding a second OAuth login is a schema extension, not a redesign.
