from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

LINEAR_OAUTH_AUTHORIZE_URL = "https://linear.app/oauth/authorize"
LINEAR_OAUTH_TOKEN_URL = "https://api.linear.app/oauth/token"
DEFAULT_ACCOUNT_ENV = "LINEARDB_ACCOUNT"
DEFAULT_OAUTH_SCOPE = "read"
DEFAULT_REDIRECT_URI = "http://localhost:8721/oauth/callback"
DEFAULT_EXPECTED_EMAIL_BY_ACCOUNT = {"greenmark": "daniel@eidosagi.com"}
DEFAULT_TEAM_KEY_BY_ACCOUNT = {"greenmark": "GMW"}
TOKEN_REFRESH_MARGIN_SECONDS = 300


class LinearDBError(RuntimeError):
    """Base error for token-safe LinearDB failures."""


class MissingCredentialError(LinearDBError):
    """Raised when no Linear OAuth credential is available."""


class OAuthStateError(LinearDBError):
    """Raised when an OAuth callback cannot be trusted."""


def account_env_key(account: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in account.upper()).strip("_")


def account_env_value(account: str | None, suffix: str) -> str | None:
    if not account:
        return None
    return os.environ.get(f"LINEARDB_{account_env_key(account)}_{suffix}")


def resolved_account(account: str | None = None) -> str:
    account_name = account or os.environ.get(DEFAULT_ACCOUNT_ENV)
    if not account_name:
        raise MissingCredentialError("Set --account or LINEARDB_ACCOUNT. LinearDB does not use ambient Linear tokens.")
    return account_name


def oauth_client_id(account: str | None) -> str:
    account_name = resolved_account(account)
    client_id = account_env_value(account_name, "OAUTH_CLIENT_ID")
    if not client_id:
        key = account_env_key(account_name)
        raise MissingCredentialError(f"Set LINEARDB_{key}_OAUTH_CLIENT_ID for LinearDB OAuth.")
    return client_id


def oauth_client_secret(account: str | None) -> str:
    account_name = resolved_account(account)
    client_secret = account_env_value(account_name, "OAUTH_CLIENT_SECRET")
    if not client_secret:
        key = account_env_key(account_name)
        raise MissingCredentialError(f"Set LINEARDB_{key}_OAUTH_CLIENT_SECRET for LinearDB OAuth.")
    return client_secret


def oauth_scope(account: str | None) -> str:
    return account_env_value(resolved_account(account), "OAUTH_SCOPE") or DEFAULT_OAUTH_SCOPE


def redirect_uri(account: str | None) -> str:
    return account_env_value(resolved_account(account), "OAUTH_REDIRECT_URI") or DEFAULT_REDIRECT_URI


def expected_email(account: str | None) -> str | None:
    account_name = resolved_account(account)
    return account_env_value(account_name, "EXPECTED_EMAIL") or DEFAULT_EXPECTED_EMAIL_BY_ACCOUNT.get(account_name)


def default_team_key(account: str | None) -> str | None:
    account_name = resolved_account(account)
    return account_env_value(account_name, "TEAM_KEY") or DEFAULT_TEAM_KEY_BY_ACCOUNT.get(account_name)


def token_store_path() -> Path:
    configured = os.environ.get("LINEARDB_TOKEN_DB")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".lineardb" / "credentials.sqlite"


def get_token(account: str | None = None, api_key_env: str | None = None) -> str:
    del api_key_env
    account_name = resolved_account(account)
    record = load_token_record(account_name)
    if not record:
        raise MissingCredentialError(
            f"Run `lineardb --account {account_name} connect` first. "
            "LinearDB is OAuth-only and will not use personal API keys."
        )

    now = int(time.time())
    expires_at = int(record.get("expires_at") or 0)
    if record.get("access_token") and expires_at - TOKEN_REFRESH_MARGIN_SECONDS > now:
        return bearer_header(record)

    refresh_token = record.get("refresh_token")
    if not refresh_token:
        raise MissingCredentialError(f"Reconnect account {account_name}; no Linear OAuth refresh token is stored.")

    response = refresh_access_token(
        account=account_name,
        refresh_token=refresh_token,
        client_id=oauth_client_id(account_name),
        client_secret=oauth_client_secret(account_name),
    )
    saved = save_token_response(account_name, response, merge_existing=record)
    return bearer_header(saved)


def authorization_url(
    account: str | None = None,
    state: str | None = None,
    prompt_consent: bool = True,
) -> tuple[str, str]:
    account_name = resolved_account(account)
    state_value = state or secrets.token_urlsafe(32)
    params = {
        "client_id": oauth_client_id(account_name),
        "redirect_uri": redirect_uri(account_name),
        "response_type": "code",
        "scope": oauth_scope(account_name),
        "state": state_value,
        "actor": "user",
    }
    if prompt_consent:
        params["prompt"] = "consent"
    return f"{LINEAR_OAUTH_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}", state_value


def exchange_authorization_code(
    account: str,
    code: str,
    endpoint: str = LINEAR_OAUTH_TOKEN_URL,
) -> dict[str, Any]:
    return token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(account),
            "client_id": oauth_client_id(account),
            "client_secret": oauth_client_secret(account),
        },
        endpoint=endpoint,
    )


def refresh_access_token(
    account: str,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    endpoint: str = LINEAR_OAUTH_TOKEN_URL,
) -> dict[str, Any]:
    del account
    return token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        endpoint=endpoint,
    )


def token_request(form: dict[str, str], endpoint: str = LINEAR_OAUTH_TOKEN_URL) -> dict[str, Any]:
    body = urllib.parse.urlencode(form).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "LinearDB/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise LinearDBError(f"Linear OAuth HTTP {exc.code}: {redact_secret(body_text)}") from exc
    except urllib.error.URLError as exc:
        raise LinearDBError(f"Linear OAuth network error: {exc.reason}") from exc

    if not payload.get("access_token"):
        raise LinearDBError("Linear OAuth response did not include an access_token.")
    return payload


def save_token_response(
    account: str,
    response: dict[str, Any],
    merge_existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = int(time.time())
    expires_in = int(response.get("expires_in") or 0)
    existing = merge_existing or {}
    record = {
        "account": account,
        "access_token": response.get("access_token") or existing.get("access_token"),
        "refresh_token": response.get("refresh_token") or existing.get("refresh_token"),
        "token_type": response.get("token_type") or existing.get("token_type") or "Bearer",
        "scope": normalize_scope(response.get("scope") or existing.get("scope") or oauth_scope(account)),
        "expires_at": now + expires_in if expires_in else existing.get("expires_at"),
        "updated_at": now,
    }
    if not record["access_token"]:
        raise LinearDBError("Linear OAuth token record is missing an access token.")
    write_token_record(account, record)
    return record


def normalize_scope(scope: Any) -> str:
    if isinstance(scope, list):
        return ",".join(str(item) for item in scope)
    return str(scope)


def bearer_header(record: dict[str, Any]) -> str:
    return f"{record.get('token_type') or 'Bearer'} {record['access_token']}"


def load_token_record(account: str) -> dict[str, Any] | None:
    path = token_store_path()
    if not path.exists():
        return None
    with sqlite3.connect(path) as connection:
        create_token_schema(connection)
        row = connection.execute(
            """
            select account, access_token, refresh_token, token_type, scope, expires_at, updated_at,
                   viewer_user_id, viewer_email, organization_id, organization_name, team_key
            from oauth_tokens
            where account = ?
            """,
            (account,),
        ).fetchone()
    if not row:
        return None
    keys = [
        "account",
        "access_token",
        "refresh_token",
        "token_type",
        "scope",
        "expires_at",
        "updated_at",
        "viewer_user_id",
        "viewer_email",
        "organization_id",
        "organization_name",
        "team_key",
    ]
    return dict(zip(keys, row, strict=True))


def write_token_record(account: str, record: dict[str, Any]) -> None:
    path = token_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        create_token_schema(connection)
        connection.execute(
            """
            insert into oauth_tokens(
              account, access_token, refresh_token, token_type, scope, expires_at, updated_at,
              viewer_user_id, viewer_email, organization_id, organization_name, team_key
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(account) do update set
              access_token = excluded.access_token,
              refresh_token = excluded.refresh_token,
              token_type = excluded.token_type,
              scope = excluded.scope,
              expires_at = excluded.expires_at,
              updated_at = excluded.updated_at,
              viewer_user_id = coalesce(excluded.viewer_user_id, oauth_tokens.viewer_user_id),
              viewer_email = coalesce(excluded.viewer_email, oauth_tokens.viewer_email),
              organization_id = coalesce(excluded.organization_id, oauth_tokens.organization_id),
              organization_name = coalesce(excluded.organization_name, oauth_tokens.organization_name),
              team_key = coalesce(excluded.team_key, oauth_tokens.team_key)
            """,
            (
                account,
                record.get("access_token"),
                record.get("refresh_token"),
                record.get("token_type") or "Bearer",
                record.get("scope") or DEFAULT_OAUTH_SCOPE,
                record.get("expires_at"),
                record.get("updated_at") or int(time.time()),
                record.get("viewer_user_id"),
                record.get("viewer_email"),
                record.get("organization_id"),
                record.get("organization_name"),
                record.get("team_key"),
            ),
        )
        connection.commit()
    os.chmod(path, 0o600)


def update_token_identity(
    account: str,
    viewer: dict[str, Any],
    team_key: str,
    teams: list[dict[str, Any]] | None = None,
) -> None:
    record = load_token_record(account)
    if not record:
        raise MissingCredentialError(f"No stored OAuth token exists for {account}.")
    organization = viewer.get("organization") or {}
    record.update(
        {
            "viewer_user_id": viewer.get("id"),
            "viewer_email": viewer.get("email"),
            "organization_id": organization.get("id"),
            "organization_name": organization.get("name"),
            "team_key": team_key,
        }
    )
    write_token_record(account, record)
    write_token_teams(account, teams or [], team_key)


def create_token_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        create table if not exists oauth_tokens (
          account text primary key,
          access_token text not null,
          refresh_token text,
          token_type text not null,
          scope text not null,
          expires_at integer,
          updated_at integer not null,
          viewer_user_id text,
          viewer_email text,
          organization_id text,
          organization_name text,
          team_key text
        );
        create table if not exists oauth_token_teams (
          account text not null,
          team_id text,
          team_key text not null,
          team_name text,
          validated_required integer not null default 0,
          updated_at integer not null,
          primary key (account, team_key),
          foreign key (account) references oauth_tokens(account) on delete cascade
        );
        """
    )


def write_token_teams(account: str, teams: list[dict[str, Any]], required_team_key: str) -> None:
    path = token_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("pragma foreign_keys = on")
        create_token_schema(connection)
        now = int(time.time())
        connection.execute("delete from oauth_token_teams where account = ?", (account,))
        for team in teams:
            key = team.get("key")
            if not key:
                continue
            connection.execute(
                """
                insert or replace into oauth_token_teams(
                  account, team_id, team_key, team_name, validated_required, updated_at
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    account,
                    team.get("id"),
                    key,
                    team.get("name"),
                    int(key == required_team_key),
                    now,
                ),
            )
        connection.commit()


def redact_secret(value: str, token: str | None = None) -> str:
    redacted = value
    candidates = [token]
    for key, secret in os.environ.items():
        if key.startswith("LINEARDB_") and key.endswith(("_OAUTH_CLIENT_SECRET", "_OAUTH_CLIENT_ID")):
            candidates.append(secret)
    for account in stored_accounts():
        record = load_token_record(account)
        if record:
            candidates.extend([record.get("access_token"), record.get("refresh_token")])
    for candidate in candidates:
        if candidate:
            redacted = redacted.replace(str(candidate), "[REDACTED_LINEAR_SECRET]")
    return redacted


def stored_accounts() -> list[str]:
    path = token_store_path()
    if not path.exists():
        return []
    with sqlite3.connect(path) as connection:
        create_token_schema(connection)
        rows = connection.execute("select account from oauth_tokens").fetchall()
    return [row[0] for row in rows]
