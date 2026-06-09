from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BLOCKER_LABELS = {
    "approval gate",
    "blocked",
    "evidence-needed",
    "human needed",
    "proof required",
    "read-only-until-approved",
    "waiting-for-others",
}

EXEC_LABELS = {
    "compliance",
    "finance-control",
    "financial-provenance",
    "payable",
    "paylocity",
    "security-review",
    "subscriptions",
}

COMMENT_SIGNALS = (
    "approval",
    "blocked",
    "blocker",
    "decision",
    "need",
    "needed",
    "waiting",
)


def generate_exec_brief(
    db_path: str | Path,
    output_path: str | Path | None = None,
    team_key: str | None = "GMW",
    limit: int = 10,
    now: datetime | None = None,
) -> dict[str, Any]:
    path = Path(db_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"SQLite mirror does not exist: {path}")
    generated_at = now or datetime.now(timezone.utc)
    report = build_exec_brief(path, team_key=team_key, limit=limit, now=generated_at)
    output = Path(output_path).expanduser().resolve() if output_path else default_output_path(path, team_key)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_html(report), encoding="utf-8")
    return {
        "sqlite": str(path),
        "output": str(output),
        "team_key": team_key,
        "generated_at": generated_at.isoformat(),
        "metrics": report["metrics"],
        "decision_count": len(report["decision_queue"]),
        "project_count": len(report["project_risk"]),
    }


def build_exec_brief(db_path: Path, team_key: str | None, limit: int, now: datetime) -> dict[str, Any]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        issues = load_issues(connection, team_key)
        label_map = load_labels(connection, [issue["id"] for issue in issues])
        comment_map = load_latest_comments(connection, [issue["id"] for issue in issues])
        span_map = load_state_span_days(connection, [issue["id"] for issue in issues], now)

    scored = [
        score_issue(issue, label_map.get(issue["id"], []), comment_map.get(issue["id"]), span_map.get(issue["id"]), now)
        for issue in issues
    ]
    decision_queue = sorted(
        [issue for issue in scored if issue["state_type"] not in {"completed", "canceled"}],
        key=lambda issue: (-issue["score"], -issue["days_stale"], issue["identifier"] or ""),
    )[:limit]
    return {
        "title": "CEO/CFO Blocker Brief",
        "team_key": team_key,
        "generated_at": now.isoformat(),
        "metrics": metrics(scored),
        "decision_queue": decision_queue,
        "project_risk": project_risk(scored),
        "aging": aging_buckets(scored),
    }


def load_issues(connection: sqlite3.Connection, team_key: str | None) -> list[sqlite3.Row]:
    where = "where team_key = ?" if team_key else ""
    params: tuple[str, ...] = (team_key,) if team_key else ()
    return connection.execute(
        f"""
        select id, identifier, title, url, team_key, state_name, state_type,
               priority_label, assignee_name, project_name, updated_at, due_date
        from issues
        {where}
        order by identifier asc
        """,
        params,
    ).fetchall()


def load_labels(connection: sqlite3.Connection, issue_ids: list[str]) -> dict[str, list[str]]:
    if not issue_ids:
        return {}
    placeholders = ",".join("?" for _ in issue_ids)
    rows = connection.execute(
        f"""
        select issue_id, label_name
        from issue_labels
        where issue_id in ({placeholders})
        order by label_name asc
        """,
        issue_ids,
    ).fetchall()
    labels: dict[str, list[str]] = {}
    for row in rows:
        labels.setdefault(row["issue_id"], []).append(row["label_name"] or "")
    return labels


def load_latest_comments(connection: sqlite3.Connection, issue_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not issue_ids:
        return {}
    placeholders = ",".join("?" for _ in issue_ids)
    rows = connection.execute(
        f"""
        select c.issue_id, c.body, c.user_name, c.updated_at
        from comments c
        join (
          select issue_id, max(coalesce(updated_at, created_at, '')) as latest_at
          from comments
          where issue_id in ({placeholders})
          group by issue_id
        ) latest on latest.issue_id = c.issue_id
                and latest.latest_at = coalesce(c.updated_at, c.created_at, '')
        """,
        issue_ids,
    ).fetchall()
    return {row["issue_id"]: dict(row) for row in rows}


def load_state_span_days(connection: sqlite3.Connection, issue_ids: list[str], now: datetime) -> dict[str, int]:
    if not issue_ids:
        return {}
    placeholders = ",".join("?" for _ in issue_ids)
    rows = connection.execute(
        f"""
        select issue_id, max(started_at) as started_at
        from issue_state_spans
        where issue_id in ({placeholders}) and ended_at is null
        group by issue_id
        """,
        issue_ids,
    ).fetchall()
    spans: dict[str, int] = {}
    for row in rows:
        started = parse_datetime(row["started_at"])
        if started:
            spans[row["issue_id"]] = max((now - started).days, 0)
    return spans


def score_issue(
    issue: sqlite3.Row,
    labels: list[str],
    latest_comment: dict[str, Any] | None,
    state_span_days: int | None,
    now: datetime,
) -> dict[str, Any]:
    label_set = {label.lower() for label in labels}
    updated_at = parse_datetime(issue["updated_at"])
    days_stale = max((now - updated_at).days, 0) if updated_at else 999
    is_open = issue["state_type"] not in {"completed", "canceled"}
    blocker_labels = sorted(label for label in labels if label.lower() in BLOCKER_LABELS)
    exec_labels = sorted(label for label in labels if label.lower() in EXEC_LABELS)
    latest_body = (latest_comment or {}).get("body") or ""
    latest_lower = latest_body.lower()
    comment_signal = any(signal in latest_lower for signal in COMMENT_SIGNALS)
    score = 0
    reasons: list[str] = []
    if issue["priority_label"] in {"Urgent", "High"}:
        score += 30
        reasons.append(f"{issue['priority_label']} priority")
    if blocker_labels:
        score += 35
        reasons.append(", ".join(blocker_labels))
    if exec_labels:
        score += 25
        reasons.append(", ".join(exec_labels))
    if days_stale >= 30:
        score += 20
        reasons.append("stale 30+ days")
    elif days_stale >= 14:
        score += 12
        reasons.append("stale 14+ days")
    if not issue["assignee_name"] and is_open:
        score += 10
        reasons.append("unassigned")
    if comment_signal:
        score += 12
        reasons.append("comment asks for attention")
    if state_span_days and state_span_days >= 14:
        score += 10
        reasons.append(f"{state_span_days} days in state")
    if not issue["project_name"] and is_open:
        score += 5
        reasons.append("no project")
    return {
        "id": issue["id"],
        "identifier": issue["identifier"],
        "title": issue["title"],
        "url": issue["url"],
        "team_key": issue["team_key"],
        "state": issue["state_name"],
        "state_type": issue["state_type"],
        "priority": issue["priority_label"] or "No priority",
        "assignee": issue["assignee_name"] or "Unassigned",
        "project": issue["project_name"] or "No project",
        "updated_at": issue["updated_at"],
        "due_date": issue["due_date"],
        "days_stale": days_stale,
        "state_span_days": state_span_days,
        "labels": labels,
        "blocker_labels": blocker_labels,
        "exec_labels": exec_labels,
        "latest_comment": summarize_comment(latest_comment),
        "score": score if is_open else 0,
        "reason": "; ".join(reasons) if reasons else "Monitor",
        "is_open": is_open,
        "is_blocked": bool(blocker_labels) or "blocked" in label_set,
        "is_exec_risk": bool(exec_labels),
    }


def summarize_comment(comment: dict[str, Any] | None) -> dict[str, Any] | None:
    if not comment:
        return None
    body = " ".join((comment.get("body") or "").split())
    if len(body) > 180:
        body = f"{body[:177]}..."
    return {"body": body, "user": comment.get("user_name"), "updated_at": comment.get("updated_at")}


def metrics(issues: list[dict[str, Any]]) -> dict[str, int]:
    open_issues = [issue for issue in issues if issue["is_open"]]
    return {
        "issues": len(issues),
        "open": len(open_issues),
        "open_high_priority": sum(1 for issue in open_issues if issue["priority"] in {"Urgent", "High"}),
        "blocked_or_approval": sum(1 for issue in open_issues if issue["is_blocked"]),
        "stale_14_days": sum(1 for issue in open_issues if issue["days_stale"] >= 14),
        "unassigned": sum(1 for issue in open_issues if issue["assignee"] == "Unassigned"),
        "finance_compliance_security": sum(1 for issue in open_issues if issue["is_exec_risk"]),
    }


def project_risk(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projects: dict[str, dict[str, Any]] = {}
    for issue in issues:
        if not issue["is_open"]:
            continue
        project = projects.setdefault(
            issue["project"],
            {"project": issue["project"], "open": 0, "high": 0, "blocked": 0, "stale": 0, "unassigned": 0, "risk_score": 0},
        )
        project["open"] += 1
        if issue["priority"] in {"Urgent", "High"}:
            project["high"] += 1
            project["risk_score"] += 3
        if issue["is_blocked"]:
            project["blocked"] += 1
            project["risk_score"] += 4
        if issue["days_stale"] >= 14:
            project["stale"] += 1
            project["risk_score"] += 2
        if issue["assignee"] == "Unassigned":
            project["unassigned"] += 1
            project["risk_score"] += 1
        if issue["is_exec_risk"]:
            project["risk_score"] += 3
    return sorted(projects.values(), key=lambda project: (-project["risk_score"], -project["open"], project["project"]))[:12]


def aging_buckets(issues: list[dict[str, Any]]) -> dict[str, int]:
    buckets = {"0-7": 0, "8-14": 0, "15-30": 0, "30+": 0}
    for issue in issues:
        if not issue["is_open"]:
            continue
        if issue["days_stale"] <= 7:
            buckets["0-7"] += 1
        elif issue["days_stale"] <= 14:
            buckets["8-14"] += 1
        elif issue["days_stale"] <= 30:
            buckets["15-30"] += 1
        else:
            buckets["30+"] += 1
    return buckets


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def default_output_path(db_path: Path, team_key: str | None) -> Path:
    stem = f"{(team_key or 'all').lower()}-exec-brief"
    return db_path.parent / f"{stem}.html"


def render_html(report: dict[str, Any]) -> str:
    metrics_html = "\n".join(
        f"<div class=\"metric\"><span>{escape(label)}</span><strong>{value}</strong></div>"
        for label, value in [
            ("Open", report["metrics"]["open"]),
            ("High Priority", report["metrics"]["open_high_priority"]),
            ("Blocked / Approval", report["metrics"]["blocked_or_approval"]),
            ("Stale 14+ Days", report["metrics"]["stale_14_days"]),
            ("Unassigned", report["metrics"]["unassigned"]),
            ("Finance / Compliance / Security", report["metrics"]["finance_compliance_security"]),
        ]
    )
    decision_rows = "\n".join(render_decision_row(issue) for issue in report["decision_queue"])
    project_rows = "\n".join(render_project_row(project) for project in report["project_risk"])
    aging_html = "\n".join(
        f"<div class=\"age\"><span>{escape(bucket)}</span><strong>{count}</strong></div>"
        for bucket, count in report["aging"].items()
    )
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>{escape(report['title'])}</title>
  <style>
    :root {{
      --bg: #f6f7f4;
      --text: #171a15;
      --muted: #687063;
      --line: #d7dccf;
      --panel: #ffffff;
      --green: #166a4d;
      --amber: #9a5d00;
      --red: #9e2f2f;
      --blue: #285f8f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif;
      font-size: 14px;
      line-height: 1.45;
    }}
    main {{ max-width: 1440px; margin: 0 auto; padding: 28px; }}
    header {{ display: flex; justify-content: space-between; align-items: end; gap: 24px; margin-bottom: 18px; }}
    h1 {{ margin: 0; font-size: 30px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 10px; font-size: 17px; letter-spacing: 0; }}
    .sub {{ color: var(--muted); margin-top: 4px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 360px; gap: 18px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); gap: 10px; margin-bottom: 18px; }}
    .metric, .age {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      min-height: 76px;
    }}
    .metric span, .age span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric strong, .age strong {{ display: block; margin-top: 8px; font-size: 28px; }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 18px;
    }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 9px 8px; vertical-align: top; text-align: left; }}
    th {{ color: var(--muted); font-size: 12px; font-weight: 700; }}
    tr:last-child td {{ border-bottom: 0; }}
    a {{ color: var(--blue); text-decoration: none; font-weight: 700; }}
    .issue-title {{ display: block; color: var(--text); font-weight: 700; }}
    .meta {{ color: var(--muted); font-size: 12px; margin-top: 2px; }}
    .pill {{ display: inline-block; border-radius: 999px; padding: 2px 7px; font-size: 12px; font-weight: 700; margin: 0 4px 4px 0; }}
    .high {{ background: #ffe1df; color: var(--red); }}
    .risk {{ background: #fff0cf; color: var(--amber); }}
    .ok {{ background: #ddf1e7; color: var(--green); }}
    .reason {{ color: #30352d; }}
    .comment {{ color: var(--muted); font-size: 12px; margin-top: 5px; }}
    .aging {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }}
    .score {{ font-size: 22px; font-weight: 800; }}
    @media (max-width: 980px) {{
      main {{ padding: 16px; }}
      header {{ display: block; }}
      .grid {{ grid-template-columns: 1fr; }}
      .metrics {{ grid-template-columns: repeat(2, 1fr); }}
      table {{ table-layout: auto; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>{escape(report['title'])}</h1>
        <div class=\"sub\">Team {escape(report.get('team_key') or 'ALL')} · Generated {escape(report['generated_at'])}</div>
      </div>
    </header>
    <div class=\"metrics\">{metrics_html}</div>
    <div class=\"grid\">
      <div>
        <section>
          <h2>Decision Queue</h2>
          <table>
            <thead><tr><th style=\"width: 36%\">Issue</th><th>Reason</th><th style=\"width: 14%\">Owner</th><th style=\"width: 12%\">Age</th><th style=\"width: 9%\">Score</th></tr></thead>
            <tbody>{decision_rows}</tbody>
          </table>
        </section>
        <section>
          <h2>Project Risk</h2>
          <table>
            <thead><tr><th>Project</th><th>Open</th><th>High</th><th>Blocked</th><th>Stale</th><th>Unassigned</th><th>Risk</th></tr></thead>
            <tbody>{project_rows}</tbody>
          </table>
        </section>
      </div>
      <aside>
        <section>
          <h2>Blocker Aging</h2>
          <div class=\"aging\">{aging_html}</div>
        </section>
      </aside>
    </div>
  </main>
</body>
</html>
"""


def render_decision_row(issue: dict[str, Any]) -> str:
    labels = "".join(f"<span class=\"pill risk\">{escape(label)}</span>" for label in issue["blocker_labels"] + issue["exec_labels"])
    priority_class = "high" if issue["priority"] in {"Urgent", "High"} else "ok"
    comment = ""
    if issue["latest_comment"]:
        comment = f"<div class=\"comment\">Latest: {escape(issue['latest_comment']['body'])}</div>"
    return f"""
    <tr>
      <td><a href=\"{escape(issue['url'] or '#')}\">{escape(issue['identifier'] or '')}</a><span class=\"issue-title\">{escape(issue['title'] or '')}</span><div class=\"meta\">{escape(issue['project'])} · {escape(issue['state'] or '')}</div></td>
      <td><span class=\"pill {priority_class}\">{escape(issue['priority'])}</span>{labels}<div class=\"reason\">{escape(issue['reason'])}</div>{comment}</td>
      <td>{escape(issue['assignee'])}</td>
      <td>{issue['days_stale']}d stale{state_age(issue)}</td>
      <td><span class=\"score\">{issue['score']}</span></td>
    </tr>"""


def state_age(issue: dict[str, Any]) -> str:
    if issue.get("state_span_days") is None:
        return ""
    return f"<div class=\"meta\">{issue['state_span_days']}d in state</div>"


def render_project_row(project: dict[str, Any]) -> str:
    return f"""
    <tr>
      <td>{escape(project['project'])}</td>
      <td>{project['open']}</td>
      <td>{project['high']}</td>
      <td>{project['blocked']}</td>
      <td>{project['stale']}</td>
      <td>{project['unassigned']}</td>
      <td><strong>{project['risk_score']}</strong></td>
    </tr>"""


def escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)
