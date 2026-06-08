from __future__ import annotations

from typing import Any

from .analytics import counts, summarize_issues
from .graphql import LinearGraphQLClient
from .queries import ISSUE_ATTACHMENTS, ISSUE_COMMENTS, ISSUE_HISTORY, ISSUE_STATE_HISTORY, TEAM_ISSUES, TEAMS, VIEWER


def auth_check(client: LinearGraphQLClient, team_key: str = "GMW", team_page_size: int = 100) -> dict[str, Any]:
    viewer = client.execute(VIEWER).get("viewer")
    teams = teams_for_account(client, page_size=team_page_size)
    return {
        "viewer": viewer,
        "teams": teams,
        "team_keys": [team.get("key") for team in teams],
        "required_team_key": team_key,
        "has_required_team": any(team.get("key") == team_key for team in teams),
    }


def account_mirror_dump(
    client: LinearGraphQLClient,
    account: str | None = None,
    team_page_size: int = 100,
    issue_page_size: int = 100,
    sample_size: int = 20,
    include_related: bool = True,
    related_page_size: int = 100,
) -> dict[str, Any]:
    viewer = client.execute(VIEWER).get("viewer")
    teams = teams_for_account(client, page_size=team_page_size)
    issues: list[dict[str, Any]] = []
    for team in teams:
        _, team_issues = issues_for_team(client, team_key=team["key"], page_size=issue_page_size)
        for issue in team_issues:
            issue["team"] = team
        issues.extend(team_issues)

    analytics = summarize_issues({"key": "ALL", "name": "All accessible Linear teams"}, issues, sample_size=sample_size)
    analytics["teams"] = counts((issue.get("team") or {}).get("key") or "No team" for issue in issues)
    related = empty_related()
    if include_related:
        related = account_issue_related(client, issues, page_size=related_page_size)
    return {
        "query": {
            "account": account,
            "team_page_size": team_page_size,
            "issue_page_size": issue_page_size,
            "sample_size": sample_size,
            "include_related": include_related,
            "related_page_size": related_page_size,
        },
        "account": {
            "profile": account,
            "viewer": viewer,
            "organization": (viewer or {}).get("organization"),
        },
        "teams": teams,
        "issues": issues,
        "related": related,
        "analytics": analytics,
    }


def teams_for_account(client: LinearGraphQLClient, page_size: int = 100) -> list[dict[str, Any]]:
    after = None
    teams: list[dict[str, Any]] = []
    while True:
        data = client.execute(TEAMS, {"first": page_size, "after": after})
        connection = data.get("teams") or {}
        teams.extend(connection.get("nodes") or [])
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return teams
        after = page_info.get("endCursor")


def issues_for_team(
    client: LinearGraphQLClient,
    team_key: str,
    page_size: int = 100,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    after = None
    team: dict[str, Any] | None = None
    issues: list[dict[str, Any]] = []
    while True:
        data = client.execute(TEAM_ISSUES, {"teamKey": team_key, "first": page_size, "after": after})
        nodes = ((data.get("teams") or {}).get("nodes") or [])
        team = next((node for node in nodes if node.get("key") == team_key), nodes[0] if nodes else None)
        if not team:
            return None, issues
        issue_connection = team.get("issues") or {}
        issues.extend(issue_connection.get("nodes") or [])
        page_info = issue_connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return {key: value for key, value in team.items() if key != "issues"}, issues
        after = page_info.get("endCursor")


def account_issue_related(
    client: LinearGraphQLClient,
    issues: list[dict[str, Any]],
    page_size: int = 100,
) -> dict[str, list[dict[str, Any]]]:
    related = empty_related()
    for issue in issues:
        issue_id = issue.get("id") or issue.get("identifier")
        if not issue_id:
            continue
        related["comments"].extend(issue_related_nodes(client, issue, "comments", ISSUE_COMMENTS, page_size))
        related["attachments"].extend(issue_related_nodes(client, issue, "attachments", ISSUE_ATTACHMENTS, page_size))
        related["history"].extend(issue_related_nodes(client, issue, "history", ISSUE_HISTORY, page_size))
        related["state_spans"].extend(issue_related_nodes(client, issue, "stateHistory", ISSUE_STATE_HISTORY, page_size))
    return related


def issue_related_nodes(
    client: LinearGraphQLClient,
    issue: dict[str, Any],
    connection_name: str,
    query: str,
    page_size: int,
) -> list[dict[str, Any]]:
    after = None
    issue_id = issue.get("id") or issue.get("identifier")
    nodes: list[dict[str, Any]] = []
    while True:
        data = client.execute(query, {"id": issue_id, "first": page_size, "after": after})
        connection = ((data.get("issue") or {}).get(connection_name) or {})
        for node in connection.get("nodes") or []:
            node["issue_id"] = issue_id
            node["issue_identifier"] = issue.get("identifier")
            nodes.append(node)
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return nodes
        after = page_info.get("endCursor")


def empty_related() -> dict[str, list[dict[str, Any]]]:
    return {"comments": [], "attachments": [], "history": [], "state_spans": []}
