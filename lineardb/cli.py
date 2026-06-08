from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analytics import sqlite_analytics
from .auth import LinearDBError, MissingCredentialError, get_token
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
        client = None if getattr(args, "dry_run", False) or args.command == "analytics" else build_client(args)
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
    parser.add_argument("--api-key-env", default="LINEAR_API_KEY", help="Ambient API key env var for no-account mode.")
    subparsers = parser.add_subparsers(dest="command")

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


if __name__ == "__main__":
    raise SystemExit(main())
