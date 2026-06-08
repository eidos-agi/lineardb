# LinearDB Implementation Notes

LinearDB is now a standalone small Python package in this directory. LinearPlus
still contains its own LinearDB-shaped sync path from the first pass, but the
new package is the intended product-category home for the mirror/analytics
contract.

## Source Of Truth

- Product/category stub: `/Users/dshanklinbv/repos-eidos-agi/lineardb`
- Current standalone implementation: `/Users/dshanklinbv/repos-eidos-agi/lineardb`
- Earlier integrated implementation: `/Users/dshanklinbv/repos-eidos-agi/linearplus`
- Installed plugin cache:
  `/Users/dshanklinbv/.codex/plugins/cache/eidos-agi/linearplus/0.1.0`

## Package Layout

- `lineardb/auth.py`: account-scoped API key/OAuth credential resolution.
- `lineardb/graphql.py`: token-safe Linear GraphQL client with transient retry.
- `lineardb/queries.py`: read-only Linear GraphQL queries for mirror sync.
- `lineardb/mirror.py`: account-wide data collection across visible teams.
- `lineardb/schema.py`: SQLite schema and atomic mirror writes.
- `lineardb/analytics.py`: local analytics over issues and snapshots.
- `lineardb/cli.py`: `auth-check`, `sync`, and `analytics` commands.
- `tests/test_lineardb.py`: unit coverage for credential boundaries, sync
  shape, SQLite persistence, analytics, and atomic write behavior.
- `PLAN.md`: product boundary, V1 acceptance criteria, and next planned work.
- `verify.py`: local proof gate.

## Command Contract

```bash
bin/lineardb --account greenmark auth-check --team-key GMW
bin/lineardb --account greenmark sync --sqlite outputs/greenmark-linear.sqlite
bin/lineardb analytics --sqlite outputs/greenmark-linear.sqlite --team-key GMW
```

`auth-check` and `sync` call Linear unless `--dry-run` is set. `analytics`
reads only the local SQLite mirror.

## Verification

```bash
python verify.py
```

The gate runs tests and dry-run CLI checks only. It does not require or use live
Linear credentials.

## LinearPlus Integration

LinearPlus now includes:

- OAuth client-credentials exchange.
- Single-account profile resolution via `--account greenmark` and
  `LINEARDB_GREENMARK_*` env vars.
- Explicit account profiles fail closed and do not use ambient Linear
  credentials.
- Personal API key fallback.
- `auth-check --team-key GMW`.
- SQLite sync with atomic writes.
- Current-state tables, sync runs, issue snapshots, comments, attachments,
  issue history, and state spans.

The next integration step is to make LinearPlus import or shell out to
LinearDB for auth, sync, and mirror analytics instead of carrying parallel
connectivity code.

## Why This Boundary Exists

The wrong-profile incident proved that Linear connectivity cannot depend on a
browser login or personal API key whose workspace can silently drift.

LinearDB owns credential truth, workspace/team validation, local mirror shape,
and analytics over the mirror. LinearPlus should consume that connectivity
instead of deciding which Linear tenant is correct.
