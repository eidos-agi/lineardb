# LinearDB Plan

LinearDB is a local Linear data-model mirror with analytics features. It is not
a replacement Linear UI and not a write-back task management product.

## Product Boundary

LinearDB owns:

- Account-scoped credential profiles.
- OAuth/client-credentials and personal-key fallback resolution.
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

The first planned slice is a single explicit account profile, usually
`greenmark`, mirrored into one SQLite database.

Accepted account hierarchy:

- `account_profile`: local credential/profile name such as `greenmark`.
- `organization`: Linear workspace identity returned by `viewer.organization`.
- `team`: Linear team such as `GMW`.
- `issue`: Linear task within a team.
- related issue records: comments, attachments, history, state spans.

Teams are not modeled as user accounts. A local account profile can see one
Linear organization and multiple teams, depending on the credential.

## Done Criteria

V1 is plan-ready when all of these are true:

- `bin/lineardb --account greenmark auth-check --team-key GMW` fails closed
  without falling back to ambient credentials when no Greenmark credential is
  present.
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
- Token-safe Linear GraphQL client with retry for transient failures.
- Account mirror collection across visible teams.
- Viewer/organization metadata captured in `account_profiles`.
- SQLite current-state tables and time-series `issue_snapshots`.
- Related issue tables for comments, attachments, history, and state spans.
- Atomic SQLite writes that preserve the prior database on write failure.
- Local analytics over the SQLite mirror.
- Unit tests and repo-local verification gate.

Blocked for live Greenmark proof:

- This shell does not currently contain `LINEARDB_GREENMARK_LINEAR_API_KEY` or
  `LINEARDB_GREENMARK_OAUTH_CLIENT_ID/LINEARDB_GREENMARK_OAUTH_CLIENT_SECRET`.

## Next Planned Work

1. Add a valid Greenmark OAuth/client-credentials profile through local secret
   injection, not chat.
2. Run `auth-check` and confirm `has_required_team` is true for `GMW`.
3. Run a fast `sync --skip-related` proof.
4. Run a full related-data sync.
5. Point LinearPlus compatibility commands at LinearDB instead of maintaining
   duplicated sync/auth code.
6. Add multi-account database support if one SQLite database needs to hold more
   than one account profile at the same time.
