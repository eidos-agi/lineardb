# Greenmark Live Bring-Up Runbook

This runbook brings up LinearDB for Daniel's `daniel@eidosagi.com` Linear login.
That login may see many Linear teams. `GMW` is the required validation team for
Greenmark workflows, not the only team LinearDB should store.

Do not paste OAuth client secrets, access tokens, or refresh tokens into chat,
issues, commits, or docs.

## 1. OAuth App

Configure the LinearDB OAuth app with:

```text
Callback URL: http://localhost:8721/oauth/callback
Scope: read
Actor: user
```

Inject app credentials locally:

```bash
export LINEARDB_GREENMARK_OAUTH_CLIENT_ID=<client-id>
export LINEARDB_GREENMARK_OAUTH_CLIENT_SECRET=<client-secret>
```

On macOS, LinearDB can also read the app credentials from Keychain when the
environment variables are absent. The expected Keychain items are:

```text
service: lineardb.greenmark.oauth.client_id
account: LINEARDB_GREENMARK_OAUTH_CLIENT_ID

service: lineardb.greenmark.oauth.client_secret
account: LINEARDB_GREENMARK_OAUTH_CLIENT_SECRET
```

Prove only presence:

```bash
test -n "$LINEARDB_GREENMARK_OAUTH_CLIENT_ID"
test -n "$LINEARDB_GREENMARK_OAUTH_CLIENT_SECRET"
```

## 2. Connect

Preview the authorization URL:

```bash
bin/lineardb --account greenmark connect --dry-run
```

Connect with Daniel's Eidos login:

```bash
bin/lineardb --account greenmark connect
```

Expected result:

- `viewer.email` is `daniel@eidosagi.com`.
- `has_required_team` is true for `GMW`.
- `~/.lineardb/credentials.sqlite` exists.
- `oauth_token_teams` contains every visible team returned during connect.

Inspect non-secret token metadata:

```bash
sqlite3 ~/.lineardb/credentials.sqlite \
  "select account, viewer_email, organization_name, team_key from oauth_tokens;"
```

Inspect visible token teams:

```bash
sqlite3 ~/.lineardb/credentials.sqlite \
  "select account, team_key, team_name, validated_required from oauth_token_teams order by team_key;"
```

## 3. Fast Sync

Run the required guard:

```bash
bin/lineardb --account greenmark auth-check --team-key GMW
```

Run a fast current-state sync:

```bash
bin/lineardb --account greenmark sync \
  --sqlite outputs/greenmark-linear.sqlite \
  --skip-related
```

Confirm many-team account relationships:

```bash
sqlite3 outputs/greenmark-linear.sqlite \
  "select at.profile, t.key, t.name from account_teams at join teams t on t.id = at.team_id order by t.key;"
```

Confirm issue distribution by team:

```bash
sqlite3 outputs/greenmark-linear.sqlite \
  "select team_key, count(*) from issues group by team_key order by team_key;"
```

Confirm team-project links:

```bash
sqlite3 outputs/greenmark-linear.sqlite \
  "select t.key, p.name from team_projects tp join teams t on t.id = tp.team_id join projects p on p.id = tp.project_id order by t.key, p.name;"
```

## 4. Resumable Related-Data Sync

Start with a bounded Greenmark related-data batch:

```bash
bin/lineardb --account greenmark sync-related \
  --sqlite outputs/greenmark-linear.sqlite \
  --team-key GMW \
  --limit 25 \
  --progress
```

Resume the next batch with the same command. Completed issues are skipped by
default.

Run all remaining GMW related data:

```bash
bin/lineardb --account greenmark sync-related \
  --sqlite outputs/greenmark-linear.sqlite \
  --team-key GMW \
  --progress
```

Retry failed issues:

```bash
bin/lineardb --account greenmark sync-related \
  --sqlite outputs/greenmark-linear.sqlite \
  --team-key GMW \
  --retry-failed \
  --progress
```

Check related tables:

```bash
sqlite3 outputs/greenmark-linear.sqlite "
select 'comments', count(*) from comments
union all select 'attachments', count(*) from attachments
union all select 'issue_history', count(*) from issue_history
union all select 'issue_state_spans', count(*) from issue_state_spans
union all select 'related_sync_status', count(*) from related_sync_status
union all select 'sync_runs', count(*) from sync_runs
union all select 'issue_snapshots', count(*) from issue_snapshots;"
```

Check progress:

```bash
sqlite3 outputs/greenmark-linear.sqlite \
  "select status, count(*) from related_sync_status group by status order by status;"
```

## 5. Analytics

Run Greenmark-filtered analytics:

```bash
bin/lineardb analytics \
  --sqlite outputs/greenmark-linear.sqlite \
  --team-key GMW
```

Cross-check GMW count:

```bash
sqlite3 outputs/greenmark-linear.sqlite \
  "select count(*) from issues where team_key = 'GMW';"
```

Cross-check all-team count:

```bash
sqlite3 outputs/greenmark-linear.sqlite \
  "select count(*) from issues;"
```

## 6. Hardening Follow-Up

After the first successful full sync:

- Add a `lineardb relationships` command so relationship inspection does not
  require manual SQL.
- Add an account-level analytics mode that intentionally avoids `--team-key`.
- Add regression fixtures with more than two teams under one account profile.
- Decide when to add a second OAuth login/profile to the same mirror file.
