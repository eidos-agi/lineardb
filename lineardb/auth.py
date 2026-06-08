from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

LINEAR_OAUTH_TOKEN_URL = "https://api.linear.app/oauth/token"
DEFAULT_ACCOUNT_ENV = "LINEARDB_ACCOUNT"
DEFAULT_API_KEY_ENV = "LINEAR_API_KEY"
FALLBACK_API_KEY_ENV = "LINEARPLUS_LINEAR_API_KEY"
DEFAULT_OAUTH_CLIENT_ID_ENV = "LINEAR_OAUTH_CLIENT_ID"
DEFAULT_OAUTH_CLIENT_SECRET_ENV = "LINEAR_OAUTH_CLIENT_SECRET"
FALLBACK_OAUTH_CLIENT_ID_ENV = "LINEARPLUS_OAUTH_CLIENT_ID"
FALLBACK_OAUTH_CLIENT_SECRET_ENV = "LINEARPLUS_OAUTH_CLIENT_SECRET"
DEFAULT_OAUTH_SCOPE_ENV = "LINEARPLUS_OAUTH_SCOPE"
DEFAULT_OAUTH_SCOPE = "read"


class LinearDBError(RuntimeError):
    """Base error for token-safe LinearDB failures."""


class MissingCredentialError(LinearDBError):
    """Raised when no Linear credential is available."""


def account_env_key(account: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in account.upper()).strip("_")


def account_env_value(account: str | None, suffix: str) -> str | None:
    if not account:
        return None
    return os.environ.get(f"LINEARDB_{account_env_key(account)}_{suffix}")


def get_token(account: str | None = None, api_key_env: str = DEFAULT_API_KEY_ENV) -> str:
    account_name = account or os.environ.get(DEFAULT_ACCOUNT_ENV)

    token = account_env_value(account_name, "LINEAR_API_KEY")
    if not account_name:
        token = token or os.environ.get(api_key_env) or os.environ.get(FALLBACK_API_KEY_ENV)
    if token:
        return token

    client_id = account_env_value(account_name, "OAUTH_CLIENT_ID")
    client_secret = account_env_value(account_name, "OAUTH_CLIENT_SECRET")
    scope = account_env_value(account_name, "OAUTH_SCOPE")
    if not account_name:
        client_id = client_id or os.environ.get(DEFAULT_OAUTH_CLIENT_ID_ENV) or os.environ.get(FALLBACK_OAUTH_CLIENT_ID_ENV)
        client_secret = (
            client_secret
            or os.environ.get(DEFAULT_OAUTH_CLIENT_SECRET_ENV)
            or os.environ.get(FALLBACK_OAUTH_CLIENT_SECRET_ENV)
        )

    if client_id and client_secret:
        return oauth_client_credentials_token(client_id, client_secret, scope=scope)

    if account_name:
        key = account_env_key(account_name)
        raise MissingCredentialError(
            f"Set LINEARDB_{key}_LINEAR_API_KEY or "
            f"LINEARDB_{key}_OAUTH_CLIENT_ID/LINEARDB_{key}_OAUTH_CLIENT_SECRET. "
            "LinearDB will not use ambient credentials for an explicit account."
        )

    raise MissingCredentialError(
        f"Set LINEARDB_<ACCOUNT>_LINEAR_API_KEY, {api_key_env}, or {FALLBACK_API_KEY_ENV}; "
        f"or set LINEARDB_<ACCOUNT>_OAUTH_CLIENT_ID/SECRET or "
        f"{FALLBACK_OAUTH_CLIENT_ID_ENV}/{FALLBACK_OAUTH_CLIENT_SECRET_ENV}."
    )


def oauth_client_credentials_token(
    client_id: str,
    client_secret: str,
    scope: str | None = None,
    endpoint: str = LINEAR_OAUTH_TOKEN_URL,
) -> str:
    resolved_scope = scope or os.environ.get(DEFAULT_OAUTH_SCOPE_ENV) or DEFAULT_OAUTH_SCOPE
    form = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "scope": resolved_scope,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=form,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "LinearDB/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise LinearDBError(f"Linear OAuth HTTP {exc.code}: {redact_secret(body_text)}") from exc
    except urllib.error.URLError as exc:
        raise LinearDBError(f"Linear OAuth network error: {exc.reason}") from exc

    access_token = body.get("access_token")
    if not access_token:
        raise LinearDBError("Linear OAuth response did not include an access_token.")
    return f"{body.get('token_type') or 'Bearer'} {access_token}"


def redact_secret(value: str, token: str | None = None) -> str:
    redacted = value
    candidates = [
        token,
        os.environ.get(DEFAULT_API_KEY_ENV),
        os.environ.get(FALLBACK_API_KEY_ENV),
        os.environ.get(DEFAULT_OAUTH_CLIENT_SECRET_ENV),
        os.environ.get(FALLBACK_OAUTH_CLIENT_SECRET_ENV),
    ]
    for key, secret in os.environ.items():
        if key.startswith("LINEARDB_") and key.endswith(("_LINEAR_API_KEY", "_OAUTH_CLIENT_SECRET")):
            candidates.append(secret)
    for candidate in candidates:
        if candidate:
            redacted = redacted.replace(candidate, "[REDACTED_LINEAR_SECRET]")
    return redacted
