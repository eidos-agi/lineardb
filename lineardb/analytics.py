from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


def counts(values: Iterable[str]) -> dict[str, int]:
    counter = Counter(values)
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def issue_state_type(issue: dict[str, Any]) -> str:
    return (issue.get("state") or {}).get("type") or ""


def issue_summary(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "identifier": issue.get("identifier"),
        "title": issue.get("title"),
        "url": issue.get("url"),
        "state": (issue.get("state") or {}).get("name"),
        "state_type": issue_state_type(issue) or None,
        "priority": issue.get("priorityLabel"),
        "assignee": (issue.get("assignee") or {}).get("name"),
        "project": (issue.get("project") or {}).get("name"),
        "updatedAt": issue.get("updatedAt"),
        "dueDate": issue.get("dueDate"),
    }


def summarize_issues(team: dict[str, Any], issues: list[dict[str, Any]], sample_size: int = 20) -> dict[str, Any]:
    state_types = counts(issue_state_type(issue) or "No state type" for issue in issues)
    assignees = counts((issue.get("assignee") or {}).get("name") or "Unassigned" for issue in issues)
    projects = counts((issue.get("project") or {}).get("name") or "No project" for issue in issues)
    return {
        "team": team,
        "totals": {
            "issues": len(issues),
            "open": sum(1 for issue in issues if issue_state_type(issue) not in {"completed", "canceled"}),
            "completed": state_types.get("completed", 0),
            "canceled": state_types.get("canceled", 0),
            "unassigned": assignees.get("Unassigned", 0),
            "without_project": projects.get("No project", 0),
        },
        "state_types": state_types,
        "states": counts((issue.get("state") or {}).get("name") or "No state" for issue in issues),
        "priorities": counts(issue.get("priorityLabel") or "No priority" for issue in issues),
        "assignees": assignees,
        "projects": projects,
        "labels": counts(
            label.get("name") or "Unnamed label"
            for issue in issues
            for label in ((issue.get("labels") or {}).get("nodes") or [])
        ),
        "sample_issues": [issue_summary(issue) for issue in issues[:sample_size]],
        "stale_open_issues": [issue_summary(issue) for issue in stale_open_issues(issues, limit=sample_size)],
    }


def stale_open_issues(issues: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    open_issues = [issue for issue in issues if issue_state_type(issue) not in {"completed", "canceled"}]
    return sorted(open_issues, key=lambda issue: issue.get("updatedAt") or "")[:limit]


def sqlite_analytics(db_path: str | Path, team_key: str | None = None, sample_size: int = 20) -> dict[str, Any]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        where, params = team_filter(team_key)
        totals = scalar_row(
            connection,
            f"""
            select
              count(*) as issues,
              sum(case when coalesce(state_type, '') not in ('completed', 'canceled') then 1 else 0 end) as open,
              sum(case when state_type = 'completed' then 1 else 0 end) as completed,
              sum(case when state_type = 'canceled' then 1 else 0 end) as canceled,
              sum(case when assignee_id is null then 1 else 0 end) as unassigned,
              sum(case when project_id is null then 1 else 0 end) as without_project
            from issues
            {where}
            """,
            params,
        )
        return {
            "sqlite": str(Path(db_path).expanduser()),
            "team_key": team_key,
            "totals": {key: totals[key] or 0 for key in totals.keys()},
            "teams": grouped_counts(connection, "team_key", "issues", where, params),
            "state_types": grouped_counts(connection, "state_type", "issues", where, params),
            "states": grouped_counts(connection, "state_name", "issues", where, params),
            "priorities": grouped_counts(connection, "priority_label", "issues", where, params),
            "assignees": grouped_counts(connection, "assignee_name", "issues", where, params, fallback="Unassigned"),
            "projects": grouped_counts(connection, "project_name", "issues", where, params, fallback="No project"),
            "labels": label_counts(connection, team_key),
            "snapshot_runs": snapshot_runs(connection, team_key),
            "state_trends": state_trends(connection, team_key),
            "stale_open_issues": stale_open_samples(connection, where, params, sample_size),
        }


def team_filter(team_key: str | None) -> tuple[str, tuple[str, ...]]:
    if not team_key:
        return "", ()
    return "where team_key = ?", (team_key,)


def scalar_row(connection: sqlite3.Connection, query: str, params: tuple[str, ...]) -> sqlite3.Row:
    row = connection.execute(query, params).fetchone()
    if row is None:
        raise RuntimeError("analytics query returned no row")
    return row


def grouped_counts(
    connection: sqlite3.Connection,
    column: str,
    table: str,
    where: str,
    params: tuple[str, ...],
    fallback: str = "None",
) -> dict[str, int]:
    rows = connection.execute(
        f"""
        select coalesce({column}, ?) as name, count(*) as count
        from {table}
        {where}
        group by coalesce({column}, ?)
        order by count desc, name asc
        """,
        (fallback, *params, fallback),
    ).fetchall()
    return {row["name"]: row["count"] for row in rows}


def label_counts(connection: sqlite3.Connection, team_key: str | None) -> dict[str, int]:
    where = ""
    params: tuple[str, ...] = ()
    if team_key:
        where = "where issues.team_key = ?"
        params = (team_key,)
    rows = connection.execute(
        f"""
        select coalesce(issue_labels.label_name, 'Unnamed label') as name, count(*) as count
        from issue_labels
        join issues on issues.id = issue_labels.issue_id
        {where}
        group by coalesce(issue_labels.label_name, 'Unnamed label')
        order by count desc, name asc
        """,
        params,
    ).fetchall()
    return {row["name"]: row["count"] for row in rows}


def snapshot_runs(connection: sqlite3.Connection, team_key: str | None) -> list[dict[str, Any]]:
    where = ""
    params: tuple[str, ...] = ()
    if team_key:
        where = "where issue_snapshots.team_key = ?"
        params = (team_key,)
    rows = connection.execute(
        f"""
        select run_id, min(captured_at) as captured_at, count(*) as issue_count
        from issue_snapshots
        {where}
        group by run_id
        order by captured_at asc
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def state_trends(connection: sqlite3.Connection, team_key: str | None) -> list[dict[str, Any]]:
    where = ""
    params: tuple[str, ...] = ()
    if team_key:
        where = "where team_key = ?"
        params = (team_key,)
    rows = connection.execute(
        f"""
        select run_id, captured_at, coalesce(state_type, 'No state type') as state_type, count(*) as issue_count
        from issue_snapshots
        {where}
        group by run_id, captured_at, coalesce(state_type, 'No state type')
        order by captured_at asc, state_type asc
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def stale_open_samples(
    connection: sqlite3.Connection,
    where: str,
    params: tuple[str, ...],
    sample_size: int,
) -> list[dict[str, Any]]:
    clause = "where coalesce(state_type, '') not in ('completed', 'canceled')"
    if where:
        clause = f"{where} and coalesce(state_type, '') not in ('completed', 'canceled')"
    rows = connection.execute(
        f"""
        select identifier, title, url, state_name as state, state_type, priority_label as priority,
               assignee_name as assignee, project_name as project, updated_at as updatedAt, due_date as dueDate
        from issues
        {clause}
        order by coalesce(updated_at, '') asc
        limit ?
        """,
        (*params, sample_size),
    ).fetchall()
    return [dict(row) for row in rows]
