# Contributing

LinearDB is a small, standard-library Python package. Keep contributions
focused on the local Linear mirror, credential safety, and analytics over the
SQLite database.

## Local Setup

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python verify.py
```

## Rules

- Do not commit Linear API keys, OAuth client secrets, access tokens, refresh
  tokens, or SQLite dumps containing private task data.
- Do not add write-back Linear mutations without a separate approval and a new
  threat model.
- Preserve explicit-account fail-closed behavior. `--account greenmark` must
  not fall back to ambient `LINEAR_API_KEY` or `LINEARPLUS_LINEAR_API_KEY`.
- Keep the mirror read-only against Linear. Analytics should run from SQLite.
- Add or update tests for schema, auth, sync, and analytics behavior.

## Verification

Run:

```bash
python verify.py
```

The verification gate intentionally uses dry-run CLI checks and unit tests so
it can run without live Linear credentials.
