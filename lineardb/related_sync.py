from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .mirror import issue_related_nodes
from .queries import ISSUE_ATTACHMENTS, ISSUE_COMMENTS, ISSUE_HISTORY, ISSUE_STATE_HISTORY
from .schema import (
    clear_issue_related,
    create_schema,
    mark_related_sync_done,
    mark_related_sync_failed,
    mark_related_sync_started,
    related_sync_summary,
    upsert_related,
)

RELATED_QUERIES = [
    ("comments", "comments", ISSUE_COMMENTS),
    ("attachments", "attachments", ISSUE_ATTACHMENTS),
    ("history", "history", ISSUE_HISTORY),
    ("state_spans", "stateHistory", ISSUE_STATE_HISTORY),
]


def sync_related_sqlite(
    client: Any,
    sqlite_path: str | Path,
    team_key: str | None = None,
    page_size: int = 100,
    limit: int | None = None,
    retry_failed: bool = False,
    force: bool = False,
    progress: bool = False,
) -> dict[str, Any]:
    path = Path(sqlite_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"SQLite mirror does not exist: {path}")

    processed = 0
    succeeded = 0
    failed = 0
    skipped_done = 0
    with sqlite3.connect(path) as connection:
        connection.execute("pragma foreign_keys = on")
        create_schema(connection)
        issues = load_issue_work(connection, team_key=team_key, retry_failed=retry_failed, force=force, limit=limit)
        total = len(issues)
        for index, issue in enumerate(issues, start=1):
            processed += 1
            issue_id = issue["id"]
            identifier = issue["identifier"]
            if progress:
                print_progress(index, total, identifier, "start")
            try:
                related = fetch_issue_related(client, issue, page_size=page_size)
                with connection:
                    clear_issue_related(connection, issue_id)
                    mark_related_sync_started(connection, issue_id, identifier, issue.get("team_key"))
                    upsert_related(connection, related)
                    mark_related_sync_done(connection, issue_id, identifier, issue.get("team_key"))
                succeeded += 1
                if progress:
                    counts = {key: len(value) for key, value in related.items()}
                    print_progress(index, total, identifier, "done", counts)
            except Exception as exc:
                with connection:
                    mark_related_sync_failed(connection, issue_id, identifier, issue.get("team_key"), str(exc))
                failed += 1
                if progress:
                    print_progress(index, total, identifier, "failed", {"error": str(exc)})
        skipped_done = count_done_skipped(connection, team_key=team_key, force=force, retry_failed=retry_failed)
        summary = related_sync_summary(connection)
        related_counts = table_counts(
            connection,
            ["comments", "attachments", "issue_history", "issue_state_spans", "related_sync_status"],
        )

    return {
        "sqlite": str(path),
        "team_key": team_key,
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
        "skipped_done": skipped_done,
        "status_counts": summary,
        "related_counts": related_counts,
    }


def load_issue_work(
    connection: sqlite3.Connection,
    team_key: str | None,
    retry_failed: bool,
    force: bool,
    limit: int | None,
) -> list[dict[str, Any]]:
    filters = []
    params: list[Any] = []
    if team_key:
        filters.append("i.team_key = ?")
        params.append(team_key)
    if not force:
        allowed = ["done"]
        if not retry_failed:
            allowed.append("failed")
        placeholders = ", ".join("?" for _ in allowed)
        filters.append(f"coalesce(r.status, 'pending') not in ({placeholders})")
        params.extend(allowed)
    where = f"where {' and '.join(filters)}" if filters else ""
    limit_clause = "limit ?" if limit is not None else ""
    if limit is not None:
        params.append(limit)
    rows = connection.execute(
        f"""
        select i.id, i.identifier, i.team_key, i.raw_json
        from issues i
        left join related_sync_status r on r.issue_id = i.id
        {where}
        order by i.team_key, i.identifier
        {limit_clause}
        """,
        params,
    ).fetchall()
    return [
        {"id": row[0], "identifier": row[1], "team_key": row[2], "raw_json": row[3]}
        for row in rows
    ]


def fetch_issue_related(client: Any, issue_row: dict[str, Any], page_size: int) -> dict[str, list[dict[str, Any]]]:
    issue = json.loads(issue_row["raw_json"])
    issue["id"] = issue_row["id"]
    issue["identifier"] = issue_row["identifier"]
    related: dict[str, list[dict[str, Any]]] = {
        "comments": [],
        "attachments": [],
        "history": [],
        "state_spans": [],
    }
    for output_key, connection_name, query in RELATED_QUERIES:
        related[output_key].extend(issue_related_nodes(client, issue, connection_name, query, page_size))
    return related


def count_done_skipped(
    connection: sqlite3.Connection,
    team_key: str | None,
    force: bool,
    retry_failed: bool,
) -> int:
    if force:
        return 0
    filters = ["r.status = 'done'"]
    params: list[Any] = []
    if team_key:
        filters.append("i.team_key = ?")
        params.append(team_key)
    if retry_failed:
        filters.append("r.status != 'failed'")
    where = " and ".join(filters)
    return int(
        connection.execute(
            f"""
            select count(*)
            from issues i
            join related_sync_status r on r.issue_id = i.id
            where {where}
            """,
            params,
        ).fetchone()[0]
    )


def table_counts(connection: sqlite3.Connection, tables: list[str]) -> dict[str, int]:
    return {table: int(connection.execute(f"select count(*) from {table}").fetchone()[0]) for table in tables}


def print_progress(index: int, total: int, identifier: str | None, status: str, extra: dict[str, Any] | None = None) -> None:
    payload = {
        "event": "related_sync_progress",
        "index": index,
        "total": total,
        "identifier": identifier,
        "status": status,
    }
    if extra:
        payload.update(extra)
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)
