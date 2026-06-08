# Security

LinearDB handles Linear credentials and local mirrors of task data. Treat both
as sensitive.

## Supported Versions

Only the current `main` branch is supported before the first tagged release.

## Reporting

Report security issues privately to Eidos AGI maintainers. Do not open public
issues that include credentials, tokens, private task data, customer names, or
database dumps.

## Credential Rules

- Never paste API keys, OAuth client secrets, access tokens, or refresh tokens
  into chat, docs, tests, or issue comments.
- Use environment variables or a local secret store controlled by the operator.
- Use account-scoped variables such as `LINEARDB_GREENMARK_OAUTH_CLIENT_ID` and
  `LINEARDB_GREENMARK_OAUTH_CLIENT_SECRET` for recurring sync.
- Explicit account profiles must fail closed and must not fall back to ambient
  credentials.

## Data Rules

- SQLite mirror files are ignored by git.
- Do not publish mirrors unless the data owner has explicitly approved that
  exact disclosure.
- Keep Linear API access read-only unless a future release adds a reviewed,
  human-gated write surface.
