# Changelog

## 0.1.0 - Unreleased

- Added standalone `lineardb` package and CLI.
- Added OAuth installed-user `connect` flow with a local callback server.
- Added local SQLite credential storage with access-token refresh support.
- Added explicit relationship tables for OAuth-token teams, account
  organizations, account teams, and team projects.
- Added resumable `sync-related` for comments, attachments, issue history, and
  state spans with per-issue progress in SQLite.
- Added `exec-brief` HTML output for a one-screen CEO/CFO blocker and project
  risk review from the local SQLite mirror.
- Added account-scoped OAuth resolution with fail-closed explicit accounts.
- Made `greenmark` default to viewer `daniel@eidosagi.com` and team `GMW`.
- Added read-only Linear GraphQL mirror sync into SQLite.
- Added current-state tables, issue snapshots, comments, attachments, issue
  history, and state spans.
- Added account profile metadata for viewer and Linear organization identity.
- Added local SQLite analytics command.
- Added FOSS, shipping, and forge provenance artifacts.
