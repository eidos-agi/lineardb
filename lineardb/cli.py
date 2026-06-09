from __future__ import annotations

import argparse
import json
import threading
import sys
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .analytics import sqlite_analytics
from .auth import (
    LinearDBError,
    MissingCredentialError,
    OAuthStateError,
    authorization_url,
    default_team_key,
    exchange_authorization_code,
    expected_email,
    get_token,
    redirect_uri,
    resolved_account,
    save_token_response,
    token_store_path,
    update_token_identity,
)
from .graphql import LinearGraphQLError, LinearGraphQLClient
from .mirror import account_mirror_dump, auth_check
from .schema import write_mirror_sqlite


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 2

    try:
        client = None if getattr(args, "dry_run", False) or args.command in {"analytics", "connect"} else build_client(args)
        result = args.handler(args, client)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except MissingCredentialError as exc:
        print(json.dumps({"ok": False, "blocked": "missing_credential", "message": str(exc)}, indent=2), file=sys.stderr)
        return 3
    except LinearGraphQLError as exc:
        print(json.dumps({"ok": False, "blocked": "linear_graphql_error", "errors": exc.errors}, indent=2), file=sys.stderr)
        return 4
    except LinearDBError as exc:
        print(json.dumps({"ok": False, "blocked": "lineardb_error", "message": str(exc)}, indent=2), file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lineardb", description="Local Linear data mirror and analytics.")
    parser.add_argument("--account", help="Explicit LinearDB account profile, such as greenmark.")
    parser.add_argument("--endpoint", default="https://api.linear.app/graphql", help="Linear GraphQL endpoint.")
    parser.add_argument(
        "--api-key-env",
        default=None,
        help=argparse.SUPPRESS,
    )
    subparsers = parser.add_subparsers(dest="command")

    connect = subparsers.add_parser("connect", help="Authorize this LinearDB account with Linear OAuth.")
    connect.add_argument("--dry-run", action="store_true", help="Print the authorize URL shape without starting OAuth.")
    connect.add_argument("--team-key", help="Required team key. Defaults to account policy, such as GMW.")
    connect.add_argument("--expected-email", help="Required viewer email. Defaults to account policy when set.")
    connect.add_argument("--no-open", action="store_true", help="Do not open the authorize URL in the default browser.")
    connect.add_argument("--timeout", type=int, default=300, help="Seconds to wait for the local OAuth callback.")
    connect.add_argument("--no-prompt-consent", action="store_true", help="Do not force Linear's consent prompt.")
    connect.set_defaults(handler=handle_connect)

    auth = subparsers.add_parser("auth-check", help="Verify identity and visible team keys.")
    auth.add_argument("--dry-run", action="store_true", help="Print the read-only operation without calling Linear.")
    auth.add_argument("--team-key", default="GMW")
    auth.add_argument("--team-page-size", type=int, default=100)
    auth.set_defaults(handler=handle_auth_check)

    sync = subparsers.add_parser("sync", help="Mirror all accessible Linear teams into SQLite.")
    sync.add_argument("--dry-run", action="store_true", help="Print the read-only operation without calling Linear.")
    sync.add_argument("--sqlite", help="SQLite output path.")
    sync.add_argument("--output-dir", help="Directory for timestamped SQLite output when --sqlite is omitted.")
    sync.add_argument("--team-page-size", type=int, default=100)
    sync.add_argument("--issue-page-size", type=int, default=100)
    sync.add_argument("--related-page-size", type=int, default=100)
    sync.add_argument("--sample-size", type=int, default=20)
    sync.add_argument("--skip-related", action="store_true")
    sync.set_defaults(handler=handle_sync)

    analytics = subparsers.add_parser("analytics", help="Analyze an existing LinearDB SQLite mirror.")
    analytics.add_argument("--sqlite", required=True, help="SQLite mirror path.")
    analytics.add_argument("--team-key", help="Optional team key filter such as GMW.")
    analytics.add_argument("--sample-size", type=int, default=20)
    analytics.set_defaults(handler=handle_analytics)

    return parser


def build_client(args: argparse.Namespace) -> LinearGraphQLClient:
    return LinearGraphQLClient(token=get_token(account=args.account, api_key_env=args.api_key_env), endpoint=args.endpoint)


def handle_connect(args: argparse.Namespace, client: LinearGraphQLClient | None) -> dict[str, Any]:
    del client
    account = resolved_account(args.account)
    team_key = args.team_key or default_team_key(account) or "GMW"
    email = args.expected_email or expected_email(account)
    url, state = authorization_url(account=account, prompt_consent=not args.no_prompt_consent)
    callback_uri = redirect_uri(account)
    if args.dry_run:
        return {
            "ok": True,
            "operation": "connect",
            "dry_run": True,
            "account": account,
            "authorize_url": url,
            "redirect_uri": callback_uri,
            "expected_email": email,
            "required_team_key": team_key,
            "read_only": True,
        }

    callback = wait_for_oauth_callback(callback_uri, state, url, open_browser=not args.no_open, timeout=args.timeout)
    response = exchange_authorization_code(account, callback["code"])
    record = save_token_response(account, response)
    connected_client = LinearGraphQLClient(token=f"{record.get('token_type') or 'Bearer'} {record['access_token']}", endpoint=args.endpoint)
    result = auth_check(connected_client, team_key=team_key)
    viewer = result.get("viewer") or {}
    if email and (viewer.get("email") or "").lower() != email.lower():
        raise LinearDBError(f"Connected Linear viewer email does not match expected email {email}.")
    if not result["has_required_team"]:
        raise LinearDBError(f"Connected Linear viewer cannot see required team {team_key}.")
    update_token_identity(account, viewer, team_key, teams=result.get("teams") or [])
    return {
        "ok": True,
        "operation": "connect",
        "account": account,
        "viewer": {"id": viewer.get("id"), "name": viewer.get("name"), "email": viewer.get("email")},
        "organization": viewer.get("organization"),
        "required_team_key": team_key,
        "has_required_team": True,
        "token_store": str(token_store_path()),
    }


def handle_auth_check(args: argparse.Namespace, client: LinearGraphQLClient | None) -> dict[str, Any]:
    if args.dry_run:
        return {
            "ok": True,
            "operation": "auth-check",
            "dry_run": True,
            "account": args.account,
            "team_key": args.team_key,
            "read_only": True,
        }
    if client is None:
        raise LinearDBError("Linear client is required for live auth check.")
    result = auth_check(client, team_key=args.team_key, team_page_size=args.team_page_size)
    return {"ok": result["has_required_team"], "operation": "auth-check", "account": args.account, **result}


def handle_sync(args: argparse.Namespace, client: LinearGraphQLClient | None) -> dict[str, Any]:
    sqlite_path = resolve_sqlite(args.sqlite, args.output_dir)
    if args.dry_run:
        return {
            "ok": True,
            "operation": "sync",
            "dry_run": True,
            "account": args.account,
            "sqlite": str(sqlite_path),
            "include_related": not args.skip_related,
            "read_only": True,
        }
    if client is None:
        raise LinearDBError("Linear client is required for live sync.")
    dump = account_mirror_dump(
        client,
        account=args.account,
        team_page_size=args.team_page_size,
        issue_page_size=args.issue_page_size,
        sample_size=args.sample_size,
        include_related=not args.skip_related,
        related_page_size=args.related_page_size,
    )
    write_mirror_sqlite(dump, sqlite_path)
    return {
        "ok": True,
        "operation": "sync",
        "account": args.account,
        "sqlite": str(sqlite_path),
        "team_count": len(dump["teams"]),
        "issue_count": dump["analytics"]["totals"]["issues"],
        "related_counts": {key: len(value) for key, value in dump.get("related", {}).items()},
        "analytics": dump["analytics"],
    }


def handle_analytics(args: argparse.Namespace, client: LinearGraphQLClient | None) -> dict[str, Any]:
    return {"ok": True, "operation": "analytics", **sqlite_analytics(args.sqlite, args.team_key, args.sample_size)}


def resolve_sqlite(sqlite_path: str | None, output_dir: str | None) -> Path:
    if sqlite_path:
        return Path(sqlite_path).expanduser().resolve()
    directory = Path(output_dir).expanduser().resolve() if output_dir else default_output_dir()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return directory / f"linear-mirror-{timestamp}.sqlite"


def default_output_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "outputs"


def wait_for_oauth_callback(
    callback_uri: str,
    state: str,
    authorize_url: str,
    open_browser: bool,
    timeout: int,
) -> dict[str, str]:
    parsed = urllib.parse.urlparse(callback_uri)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise LinearDBError("LinearDB local connect only supports http://localhost callback URLs.")
    if not parsed.port:
        raise LinearDBError("LinearDB local connect callback URL must include a port.")

    callback_path = parsed.path or "/"
    result: dict[str, str] = {}
    event = threading.Event()

    class OAuthCallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            request_url = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(request_url.query)
            if request_url.path != callback_path:
                self.send_response(404)
                self.end_headers()
                return
            if params.get("state", [""])[0] != state:
                result["error"] = "state_mismatch"
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"LinearDB OAuth state mismatch. You can close this tab.")
                event.set()
                return
            if params.get("error"):
                result["error"] = params.get("error", ["oauth_error"])[0]
                result["error_description"] = params.get("error_description", [""])[0]
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"LinearDB OAuth authorization failed. You can close this tab.")
                event.set()
                return
            code = params.get("code", [""])[0]
            if not code:
                result["error"] = "missing_code"
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"LinearDB OAuth callback did not include a code. You can close this tab.")
                event.set()
                return
            result["code"] = code
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"LinearDB connected. You can close this tab and return to Codex.")
            event.set()

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer((parsed.hostname, parsed.port), OAuthCallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        print(json.dumps({"operation": "connect", "authorize_url": authorize_url, "callback": callback_uri}, indent=2))
        if open_browser:
            webbrowser.open(authorize_url)
        if not event.wait(timeout):
            raise LinearDBError(f"Timed out waiting {timeout} seconds for Linear OAuth callback.")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    if result.get("error") == "state_mismatch":
        raise OAuthStateError("Linear OAuth callback state mismatch.")
    if result.get("error"):
        raise LinearDBError(f"Linear OAuth authorization failed: {result.get('error_description') or result['error']}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
