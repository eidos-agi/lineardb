from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def current_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def write_mirror_sqlite(dump: dict[str, Any], db_path: str | os.PathLike[str]) -> None:
    target_path = os.fspath(db_path)
    temp_path = f"{target_path}.{current_run_id()}.tmp"
    try:
        write_mirror_sqlite_file(dump, temp_path)
        os.replace(temp_path, target_path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def write_mirror_sqlite_file(dump: dict[str, Any], db_path: str | os.PathLike[str]) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute("pragma foreign_keys = off")
        create_schema(connection)
        run_id = current_run_id()
        captured_at = current_timestamp()
        clear_current_tables(connection)
        insert_sync_run(connection, run_id, captured_at, dump)
        for team in dump.get("teams") or []:
            upsert_team(connection, team)
        for issue in dump.get("issues") or []:
            upsert_issue_related_records(connection, issue)
            upsert_issue(connection, issue)
            insert_issue_snapshot(connection, run_id, captured_at, issue)
        upsert_related(connection, dump.get("related") or {})
        connection.execute(
            "insert into metadata(key, value) values (?, ?)",
            ("analytics", json.dumps(dump.get("analytics") or {}, sort_keys=True)),
        )
        connection.execute("insert into metadata(key, value) values (?, ?)", ("latest_sync_run_id", run_id))
        connection.commit()


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        create table if not exists metadata (
          key text primary key,
          value text not null
        );
        create table if not exists sync_runs (
          id text primary key,
          account_profile text,
          started_at text not null,
          finished_at text not null,
          team_count integer not null,
          issue_count integer not null,
          raw_json text not null
        );
        create table if not exists account_profiles (
          profile text primary key,
          viewer_user_id text,
          viewer_name text,
          viewer_email text,
          organization_id text,
          organization_name text,
          organization_url_key text,
          raw_json text not null
        );
        create table if not exists teams (
          id text primary key,
          key text,
          name text,
          raw_json text not null
        );
        create table if not exists users (
          id text primary key,
          name text,
          raw_json text not null
        );
        create table if not exists projects (
          id text primary key,
          name text,
          url text,
          raw_json text not null
        );
        create table if not exists labels (
          id text primary key,
          name text,
          raw_json text not null
        );
        create table if not exists issues (
          id text primary key,
          identifier text,
          title text,
          url text,
          team_id text,
          team_key text,
          state_name text,
          state_type text,
          priority_label text,
          assignee_id text,
          assignee_name text,
          project_id text,
          project_name text,
          cycle_id text,
          cycle_name text,
          created_at text,
          updated_at text,
          completed_at text,
          canceled_at text,
          due_date text,
          raw_json text not null
        );
        create table if not exists issue_labels (
          issue_id text not null,
          label_id text not null,
          label_name text,
          primary key (issue_id, label_id)
        );
        create table if not exists issue_snapshots (
          run_id text not null,
          issue_id text not null,
          identifier text,
          team_key text,
          state_name text,
          state_type text,
          priority_label text,
          assignee_id text,
          project_id text,
          updated_at text,
          captured_at text not null,
          raw_json text not null,
          primary key (run_id, issue_id)
        );
        create table if not exists comments (
          id text primary key,
          issue_id text,
          issue_identifier text,
          body text,
          body_data text,
          url text,
          user_id text,
          user_name text,
          created_at text,
          updated_at text,
          archived_at text,
          raw_json text not null
        );
        create table if not exists attachments (
          id text primary key,
          issue_id text,
          issue_identifier text,
          title text,
          subtitle text,
          url text,
          source_type text,
          creator_id text,
          creator_name text,
          created_at text,
          updated_at text,
          archived_at text,
          raw_json text not null
        );
        create table if not exists issue_history (
          id text primary key,
          issue_id text,
          issue_identifier text,
          actor_id text,
          actor_name text,
          from_state_id text,
          from_state_name text,
          to_state_id text,
          to_state_name text,
          from_assignee_id text,
          to_assignee_id text,
          from_project_id text,
          to_project_id text,
          from_priority real,
          to_priority real,
          from_due_date text,
          to_due_date text,
          updated_description integer,
          created_at text,
          updated_at text,
          archived_at text,
          raw_json text not null
        );
        create table if not exists issue_state_spans (
          id text primary key,
          issue_id text,
          issue_identifier text,
          state_id text,
          state_name text,
          state_type text,
          started_at text,
          ended_at text,
          raw_json text not null
        );
        """
    )


def clear_current_tables(connection: sqlite3.Connection) -> None:
    for table in [
        "metadata",
        "issue_labels",
        "issues",
        "labels",
        "projects",
        "users",
        "teams",
        "account_profiles",
        "comments",
        "attachments",
        "issue_history",
        "issue_state_spans",
    ]:
        connection.execute(f"delete from {table}")


def insert_sync_run(connection: sqlite3.Connection, run_id: str, started_at: str, dump: dict[str, Any]) -> None:
    connection.execute(
        """
        insert or replace into sync_runs(id, account_profile, started_at, finished_at, team_count, issue_count, raw_json)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            (dump.get("account") or {}).get("profile"),
            started_at,
            current_timestamp(),
            len(dump.get("teams") or []),
            len(dump.get("issues") or []),
            json.dumps({"query": dump.get("query"), "analytics": dump.get("analytics")}, sort_keys=True),
        ),
    )
    upsert_account_profile(connection, dump.get("account") or {})


def upsert_account_profile(connection: sqlite3.Connection, account: dict[str, Any]) -> None:
    profile = account.get("profile") or "default"
    viewer = account.get("viewer") or {}
    organization = account.get("organization") or {}
    connection.execute(
        """
        insert or replace into account_profiles(
          profile, viewer_user_id, viewer_name, viewer_email,
          organization_id, organization_name, organization_url_key, raw_json
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            profile,
            viewer.get("id"),
            viewer.get("name"),
            viewer.get("email"),
            organization.get("id"),
            organization.get("name"),
            organization.get("urlKey"),
            json.dumps(account, sort_keys=True),
        ),
    )


def upsert_team(connection: sqlite3.Connection, team: dict[str, Any]) -> None:
    connection.execute(
        "insert or replace into teams(id, key, name, raw_json) values (?, ?, ?, ?)",
        (team.get("id"), team.get("key"), team.get("name"), json.dumps(team, sort_keys=True)),
    )


def upsert_issue_related_records(connection: sqlite3.Connection, issue: dict[str, Any]) -> None:
    team = issue.get("team") or {}
    if team.get("id"):
        upsert_team(connection, team)
    assignee = issue.get("assignee") or {}
    if assignee.get("id"):
        connection.execute(
            "insert or replace into users(id, name, raw_json) values (?, ?, ?)",
            (assignee.get("id"), assignee.get("name"), json.dumps(assignee, sort_keys=True)),
        )
    project = issue.get("project") or {}
    if project.get("id"):
        connection.execute(
            "insert or replace into projects(id, name, url, raw_json) values (?, ?, ?, ?)",
            (project.get("id"), project.get("name"), project.get("url"), json.dumps(project, sort_keys=True)),
        )
    for label in (issue.get("labels") or {}).get("nodes") or []:
        label_id = label.get("id") or label.get("name")
        if not label_id:
            continue
        connection.execute(
            "insert or replace into labels(id, name, raw_json) values (?, ?, ?)",
            (label_id, label.get("name"), json.dumps(label, sort_keys=True)),
        )
        connection.execute(
            "insert or replace into issue_labels(issue_id, label_id, label_name) values (?, ?, ?)",
            (issue.get("id") or issue.get("identifier"), label_id, label.get("name")),
        )


def upsert_issue(connection: sqlite3.Connection, issue: dict[str, Any]) -> None:
    team = issue.get("team") or {}
    state = issue.get("state") or {}
    assignee = issue.get("assignee") or {}
    project = issue.get("project") or {}
    cycle = issue.get("cycle") or {}
    connection.execute(
        """
        insert or replace into issues(
          id, identifier, title, url, team_id, team_key, state_name, state_type,
          priority_label, assignee_id, assignee_name, project_id, project_name,
          cycle_id, cycle_name, created_at, updated_at, completed_at, canceled_at,
          due_date, raw_json
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            issue.get("id") or issue.get("identifier"),
            issue.get("identifier"),
            issue.get("title"),
            issue.get("url"),
            team.get("id"),
            team.get("key"),
            state.get("name"),
            state.get("type"),
            issue.get("priorityLabel"),
            assignee.get("id"),
            assignee.get("name"),
            project.get("id"),
            project.get("name"),
            cycle.get("id"),
            cycle.get("name"),
            issue.get("createdAt"),
            issue.get("updatedAt"),
            issue.get("completedAt"),
            issue.get("canceledAt"),
            issue.get("dueDate"),
            json.dumps(issue, sort_keys=True),
        ),
    )


def insert_issue_snapshot(connection: sqlite3.Connection, run_id: str, captured_at: str, issue: dict[str, Any]) -> None:
    team = issue.get("team") or {}
    state = issue.get("state") or {}
    assignee = issue.get("assignee") or {}
    project = issue.get("project") or {}
    connection.execute(
        """
        insert or replace into issue_snapshots(
          run_id, issue_id, identifier, team_key, state_name, state_type,
          priority_label, assignee_id, project_id, updated_at, captured_at, raw_json
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            issue.get("id") or issue.get("identifier"),
            issue.get("identifier"),
            team.get("key"),
            state.get("name"),
            state.get("type"),
            issue.get("priorityLabel"),
            assignee.get("id"),
            project.get("id"),
            issue.get("updatedAt"),
            captured_at,
            json.dumps(issue, sort_keys=True),
        ),
    )


def upsert_related(connection: sqlite3.Connection, related: dict[str, list[dict[str, Any]]]) -> None:
    for comment in related.get("comments") or []:
        user = comment.get("user") or {}
        connection.execute(
            """
            insert or replace into comments(
              id, issue_id, issue_identifier, body, body_data, url, user_id, user_name,
              created_at, updated_at, archived_at, raw_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                comment.get("id"),
                comment.get("issue_id"),
                comment.get("issue_identifier"),
                comment.get("body"),
                comment.get("bodyData"),
                comment.get("url"),
                user.get("id"),
                user.get("name"),
                comment.get("createdAt"),
                comment.get("updatedAt"),
                comment.get("archivedAt"),
                json.dumps(comment, sort_keys=True),
            ),
        )
    for attachment in related.get("attachments") or []:
        creator = attachment.get("creator") or {}
        connection.execute(
            """
            insert or replace into attachments(
              id, issue_id, issue_identifier, title, subtitle, url, source_type,
              creator_id, creator_name, created_at, updated_at, archived_at, raw_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attachment.get("id"),
                attachment.get("issue_id"),
                attachment.get("issue_identifier"),
                attachment.get("title"),
                attachment.get("subtitle"),
                attachment.get("url"),
                attachment.get("sourceType"),
                creator.get("id"),
                creator.get("name"),
                attachment.get("createdAt"),
                attachment.get("updatedAt"),
                attachment.get("archivedAt"),
                json.dumps(attachment, sort_keys=True),
            ),
        )
    for event in related.get("history") or []:
        actor = event.get("actor") or {}
        from_state = event.get("fromState") or {}
        to_state = event.get("toState") or {}
        connection.execute(
            """
            insert or replace into issue_history(
              id, issue_id, issue_identifier, actor_id, actor_name,
              from_state_id, from_state_name, to_state_id, to_state_name,
              from_assignee_id, to_assignee_id, from_project_id, to_project_id,
              from_priority, to_priority, from_due_date, to_due_date,
              updated_description, created_at, updated_at, archived_at, raw_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.get("id"),
                event.get("issue_id"),
                event.get("issue_identifier"),
                actor.get("id") or event.get("actorId"),
                actor.get("name"),
                from_state.get("id") or event.get("fromStateId"),
                from_state.get("name"),
                to_state.get("id") or event.get("toStateId"),
                to_state.get("name"),
                event.get("fromAssigneeId"),
                event.get("toAssigneeId"),
                event.get("fromProjectId"),
                event.get("toProjectId"),
                event.get("fromPriority"),
                event.get("toPriority"),
                event.get("fromDueDate"),
                event.get("toDueDate"),
                int(bool(event.get("updatedDescription"))) if event.get("updatedDescription") is not None else None,
                event.get("createdAt"),
                event.get("updatedAt"),
                event.get("archivedAt"),
                json.dumps(event, sort_keys=True),
            ),
        )
    for span in related.get("state_spans") or []:
        state = span.get("state") or {}
        connection.execute(
            """
            insert or replace into issue_state_spans(
              id, issue_id, issue_identifier, state_id, state_name, state_type,
              started_at, ended_at, raw_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                span.get("id"),
                span.get("issue_id"),
                span.get("issue_identifier"),
                state.get("id") or span.get("stateId"),
                state.get("name"),
                state.get("type"),
                span.get("startedAt"),
                span.get("endedAt"),
                json.dumps(span, sort_keys=True),
            ),
        )
