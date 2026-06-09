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

## Next Planned Work

1. Add the LinearDB OAuth app client id/secret through local secret injection,
   not chat.
2. Run `connect` and approve with `daniel@eidosagi.com`.
3. Run `auth-check` and confirm `has_required_team` is true for `GMW`.
4. Run a fast `sync --skip-related` proof.
5. Run a full related-data sync.
6. Add multi-account database support if one SQLite database needs to hold more
   than one account profile at the same time.
