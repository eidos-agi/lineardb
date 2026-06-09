from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from lineardb.analytics import sqlite_analytics
from lineardb.auth import (
    MissingCredentialError,
    authorization_url,
    default_team_key,
    expected_email,
    get_token,
    oauth_client_id,
    oauth_client_secret,
    save_token_response,
)
from lineardb.cli import main
from lineardb.exec_brief import build_exec_brief
from lineardb.mirror import account_mirror_dump
from lineardb.related_sync import sync_related_sqlite
from lineardb.schema import write_mirror_sqlite


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def execute(self, query, variables=None):
        self.calls.append((query, variables or {}))
        return self.responses.pop(0)


class LinearDBTests(unittest.TestCase):
    def test_account_profile_does_not_use_api_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "credentials.sqlite"
            env = {
                "LINEARDB_TOKEN_DB": str(db_path),
                "LINEARDB_GREENMARK_LINEAR_API_KEY": "wrong-profile",
            }
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(MissingCredentialError):
                    get_token(account="greenmark")

    def test_account_profile_uses_stored_oauth_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "credentials.sqlite"
            with patch.dict(os.environ, {"LINEARDB_TOKEN_DB": str(db_path)}, clear=True):
                save_token_response(
                    "greenmark",
                    {"access_token": "access-token", "refresh_token": "refresh-token", "expires_in": 3600},
                )
                self.assertEqual(get_token(account="greenmark"), "Bearer access-token")

    def test_expired_token_refreshes_and_rotates_refresh_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "credentials.sqlite"
            env = {
                "LINEARDB_TOKEN_DB": str(db_path),
                "LINEARDB_GREENMARK_OAUTH_CLIENT_ID": "client-id",
                "LINEARDB_GREENMARK_OAUTH_CLIENT_SECRET": "client-secret",
            }
            with patch.dict(os.environ, env, clear=True):
                save_token_response(
                    "greenmark",
                    {"access_token": "old-access", "refresh_token": "old-refresh", "expires_in": -10},
                )
                with patch(
                    "lineardb.auth.refresh_access_token",
                    return_value={
                        "access_token": "new-access",
                        "refresh_token": "new-refresh",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                        "scope": "read",
                    },
                ):
                    self.assertEqual(get_token(account="greenmark"), "Bearer new-access")
                self.assertEqual(get_token(account="greenmark"), "Bearer new-access")

    def test_greenmark_defaults_are_daniel_eidosagi_and_gmw(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(expected_email("greenmark"), "daniel@eidosagi.com")
            self.assertEqual(default_team_key("greenmark"), "GMW")

    def test_authorization_url_uses_account_oauth_app_and_read_scope(self):
        env = {"LINEARDB_GREENMARK_OAUTH_CLIENT_ID": "client-id"}
        with patch.dict(os.environ, env, clear=True):
            url, state = authorization_url("greenmark", state="state-1")
        self.assertIn("client_id=client-id", url)
        self.assertIn("scope=read", url)
        self.assertIn("actor=user", url)
        self.assertIn("prompt=consent", url)
        self.assertEqual(state, "state-1")

    def test_oauth_app_credentials_can_resolve_from_keychain(self):
        def fake_run(command, text=True, capture_output=True, check=False):
            self.assertEqual(text, True)
            self.assertEqual(capture_output, True)
            self.assertEqual(check, False)
            service = command[command.index("-s") + 1]
            if service.endswith("client_id"):
                return type("Result", (), {"returncode": 0, "stdout": "client-id\n"})()
            if service.endswith("client_secret"):
                return type("Result", (), {"returncode": 0, "stdout": "client-secret\n"})()
            return type("Result", (), {"returncode": 1, "stdout": ""})()

        with patch.dict(os.environ, {}, clear=True):
            with patch("lineardb.auth.subprocess.run", side_effect=fake_run):
                self.assertEqual(oauth_client_id("greenmark"), "client-id")
                self.assertEqual(oauth_client_secret("greenmark"), "client-secret")

    def test_oauth_env_credentials_override_keychain(self):
        env = {
            "LINEARDB_GREENMARK_OAUTH_CLIENT_ID": "env-client-id",
            "LINEARDB_GREENMARK_OAUTH_CLIENT_SECRET": "env-client-secret",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("lineardb.auth.subprocess.run") as run:
                self.assertEqual(oauth_client_id("greenmark"), "env-client-id")
                self.assertEqual(oauth_client_secret("greenmark"), "env-client-secret")
        run.assert_not_called()

    def test_auth_check_dry_run_is_token_safe(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("sys.stdout") as stdout:
                code = main(["--account", "greenmark", "auth-check", "--dry-run", "--team-key", "GMW"])
        output = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertEqual(code, 0)
        self.assertIn('"operation": "auth-check"', output)
        self.assertNotIn("access-token", output)

    def test_connect_dry_run_reports_oauth_shape(self):
        env = {"LINEARDB_GREENMARK_OAUTH_CLIENT_ID": "client-id"}
        with patch.dict(os.environ, env, clear=True):
            with patch("sys.stdout") as stdout:
                code = main(["--account", "greenmark", "connect", "--dry-run"])
        output = "".join(call.args[0] for call in stdout.write.call_args_list)
        data = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(data["operation"], "connect")
        self.assertEqual(data["expected_email"], "daniel@eidosagi.com")
        self.assertEqual(data["required_team_key"], "GMW")
        self.assertIn("linear.app/oauth/authorize", data["authorize_url"])

    def test_connect_saves_token_after_email_and_team_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "credentials.sqlite"
            env = {
                "LINEARDB_TOKEN_DB": str(db_path),
                "LINEARDB_GREENMARK_OAUTH_CLIENT_ID": "client-id",
                "LINEARDB_GREENMARK_OAUTH_CLIENT_SECRET": "client-secret",
            }
            with patch.dict(os.environ, env, clear=True):
                with patch("lineardb.cli.wait_for_oauth_callback", return_value={"code": "code-1"}):
                    with patch(
                        "lineardb.cli.exchange_authorization_code",
                        return_value={
                            "access_token": "access-token",
                            "refresh_token": "refresh-token",
                            "expires_in": 3600,
                        },
                    ):
                        with patch(
                            "lineardb.cli.auth_check",
                            return_value={
                                "viewer": {
                                    "id": "viewer-1",
                                    "name": "Daniel",
                                    "email": "daniel@eidosagi.com",
                                    "organization": {"id": "org-1", "name": "Greenmark", "urlKey": "greenmark"},
                                },
                                "teams": [
                                    {"id": "team-gmw", "key": "GMW", "name": "Greenmark"},
                                    {"id": "team-aic", "key": "AIC", "name": "AIC"},
                                ],
                                "team_keys": ["GMW", "AIC"],
                                "required_team_key": "GMW",
                                "has_required_team": True,
                            },
                        ):
                            with patch("sys.stdout"):
                                code = main(["--account", "greenmark", "connect", "--no-open"])
                self.assertEqual(code, 0)
                self.assertEqual(get_token(account="greenmark"), "Bearer access-token")
                with sqlite3.connect(db_path) as connection:
                    token_teams = connection.execute(
                        """
                        select account, team_id, team_key, team_name, validated_required
                        from oauth_token_teams
                        order by team_key
                        """
                    ).fetchall()
                self.assertEqual(
                    token_teams,
                    [
                        ("greenmark", "team-aic", "AIC", "AIC", 0),
                        ("greenmark", "team-gmw", "GMW", "Greenmark", 1),
                    ],
                )

    def test_account_mirror_fetches_all_teams_and_issues_without_related(self):
        client = FakeClient(
            [
                {
                    "viewer": {
                        "id": "viewer-1",
                        "name": "Daniel",
                        "email": "daniel@greenmarkwaste.com",
                        "organization": {"id": "org-gmw", "name": "Greenmark", "urlKey": "greenmark"},
                    }
                },
                {
                    "teams": {
                        "nodes": [
                            {"id": "team-gmw", "key": "GMW", "name": "Greenmark"},
                            {"id": "team-aic", "key": "AIC", "name": "AIC"},
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                },
                {
                    "teams": {
                        "nodes": [
                            {
                                "id": "team-gmw",
                                "key": "GMW",
                                "name": "Greenmark",
                                "issues": {
                                    "nodes": [issue("issue-1", "GMW-1", "Todo", "unstarted")],
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                },
                            }
                        ]
                    }
                },
                {
                    "teams": {
                        "nodes": [
                            {
                                "id": "team-aic",
                                "key": "AIC",
                                "name": "AIC",
                                "issues": {
                                    "nodes": [issue("issue-2", "AIC-1", "Done", "completed")],
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                },
                            }
                        ]
                    }
                },
            ]
        )

        dump = account_mirror_dump(client, account="greenmark", include_related=False)

        self.assertEqual(dump["account"]["profile"], "greenmark")
        self.assertEqual(dump["account"]["organization"]["name"], "Greenmark")
        self.assertEqual([team["key"] for team in dump["teams"]], ["GMW", "AIC"])
        self.assertEqual([item["identifier"] for item in dump["issues"]], ["GMW-1", "AIC-1"])
        self.assertEqual(dump["analytics"]["teams"], {"AIC": 1, "GMW": 1})

    def test_sqlite_mirror_persists_current_state_and_snapshots(self):
        dump = sample_dump()
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "linear.sqlite"
            write_mirror_sqlite(dump, db_path)
            with sqlite3.connect(db_path) as connection:
                issue_count = connection.execute("select count(*) from issues").fetchone()[0]
                snapshot_count = connection.execute("select count(*) from issue_snapshots").fetchone()[0]
                comment_count = connection.execute("select count(*) from comments").fetchone()[0]
                account_profile = connection.execute("select profile from account_profiles").fetchone()[0]
                account_org = connection.execute(
                    """
                    select ao.profile, o.id, o.name
                    from account_organizations ao
                    join organizations o on o.id = ao.organization_id
                    """
                ).fetchone()
                account_teams = connection.execute(
                    """
                    select at.profile, t.id, t.key
                    from account_teams at
                    join teams t on t.id = at.team_id
                    order by t.key
                    """
                ).fetchall()
                team_project = connection.execute(
                    """
                    select t.key, p.id, p.name
                    from team_projects tp
                    join teams t on t.id = tp.team_id
                    join projects p on p.id = tp.project_id
                    """
                ).fetchone()

        self.assertEqual(issue_count, 3)
        self.assertEqual(snapshot_count, 3)
        self.assertEqual(comment_count, 1)
        self.assertEqual(account_profile, "greenmark")
        self.assertEqual(account_org, ("greenmark", "org-gmw", "Greenmark"))
        self.assertEqual(
            account_teams,
            [
                ("greenmark", "team-aic", "AIC"),
                ("greenmark", "team-gmw", "GMW"),
            ],
        )
        self.assertEqual(team_project, ("GMW", "project-1", "Cerebro"))

    def test_sqlite_analytics_reads_local_mirror(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "linear.sqlite"
            write_mirror_sqlite(sample_dump(), db_path)

            result = sqlite_analytics(db_path, team_key="GMW")

        self.assertEqual(result["totals"]["issues"], 2)
        self.assertEqual(result["totals"]["open"], 1)
        self.assertEqual(result["state_types"], {"completed": 1, "unstarted": 1})
        self.assertEqual(result["labels"], {"Paylocity": 1})
        self.assertEqual(len(result["snapshot_runs"]), 1)

    def test_analytics_cli_reads_existing_sqlite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "linear.sqlite"
            write_mirror_sqlite(sample_dump(), db_path)
            with patch("sys.stdout") as stdout:
                code = main(["analytics", "--sqlite", str(db_path), "--team-key", "GMW"])

        output = "".join(call.args[0] for call in stdout.write.call_args_list)
        data = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(data["operation"], "analytics")
        self.assertEqual(data["totals"]["issues"], 2)

    def test_write_mirror_keeps_existing_file_on_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "linear.sqlite"
            db_path.write_text("previous")
            with patch("lineardb.schema.write_mirror_sqlite_file", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    write_mirror_sqlite({}, db_path)
            self.assertEqual(db_path.read_text(), "previous")

    def test_sqlite_mirror_clears_related_rows_on_next_sync(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "linear.sqlite"
            write_mirror_sqlite(sample_dump(), db_path)
            dump = sample_dump()
            dump["related"] = {"comments": [], "attachments": [], "history": [], "state_spans": []}
            write_mirror_sqlite(dump, db_path)
            with sqlite3.connect(db_path) as connection:
                comment_count = connection.execute("select count(*) from comments").fetchone()[0]

        self.assertEqual(comment_count, 0)

    def test_sync_related_resumes_from_existing_sqlite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "linear.sqlite"
            write_mirror_sqlite(sample_dump(), db_path)
            client = FakeClient(
                [
                    related_response("comments", [{"id": "comment-1", "body": "One"}]),
                    related_response("attachments", [{"id": "attachment-1", "title": "File One"}]),
                    related_response("history", [{"id": "history-1", "actor": {"id": "actor-1", "name": "Daniel"}}]),
                    related_response("stateHistory", [{"id": "span-1", "state": {"id": "state-1", "name": "Todo", "type": "unstarted"}}]),
                    related_response("comments", [{"id": "comment-2", "body": "Two"}]),
                    related_response("attachments", [{"id": "attachment-2", "title": "File Two"}]),
                    related_response("history", [{"id": "history-2", "actor": {"id": "actor-2", "name": "Daniel"}}]),
                    related_response("stateHistory", [{"id": "span-2", "state": {"id": "state-2", "name": "Done", "type": "completed"}}]),
                ]
            )

            first = sync_related_sqlite(client, db_path, team_key="GMW", limit=1)
            second = sync_related_sqlite(client, db_path, team_key="GMW", limit=1)

            with sqlite3.connect(db_path) as connection:
                comments = connection.execute(
                    "select issue_identifier, body from comments order by issue_identifier"
                ).fetchall()
                statuses = connection.execute(
                    "select issue_identifier, status from related_sync_status where team_key = 'GMW' order by issue_identifier"
                ).fetchall()
                attachments = connection.execute("select count(*) from attachments").fetchone()[0]
                history = connection.execute("select count(*) from issue_history").fetchone()[0]
                spans = connection.execute("select count(*) from issue_state_spans").fetchone()[0]

        self.assertEqual(first["processed"], 1)
        self.assertEqual(first["succeeded"], 1)
        self.assertEqual(second["processed"], 1)
        self.assertEqual(second["succeeded"], 1)
        self.assertEqual(comments, [("GMW-1", "One"), ("GMW-2", "Two")])
        self.assertEqual(statuses, [("GMW-1", "done"), ("GMW-2", "done")])
        self.assertEqual(attachments, 2)
        self.assertEqual(history, 2)
        self.assertEqual(spans, 2)

    def test_sync_related_cli_dry_run_is_token_safe(self):
        with patch("sys.stdout") as stdout:
            code = main(["--account", "greenmark", "sync-related", "--dry-run", "--sqlite", "mirror.sqlite", "--team-key", "GMW"])

        output = "".join(call.args[0] for call in stdout.write.call_args_list)
        data = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(data["operation"], "sync-related")
        self.assertEqual(data["team_key"], "GMW")

    def test_exec_brief_ranks_executive_blockers_from_sqlite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "linear.sqlite"
            dump = sample_dump()
            dump["issues"][0]["labels"]["nodes"].append({"id": "label-2", "name": "Approval Gate"})
            write_mirror_sqlite(dump, db_path)

            report = build_exec_brief(
                db_path,
                team_key="GMW",
                limit=5,
                now=datetime(2026, 6, 20, tzinfo=timezone.utc),
            )

        self.assertEqual(report["metrics"]["issues"], 2)
        self.assertEqual(report["metrics"]["blocked_or_approval"], 1)
        self.assertEqual(report["decision_queue"][0]["identifier"], "GMW-1")
        self.assertIn("Approval Gate", report["decision_queue"][0]["reason"])

    def test_exec_brief_cli_writes_html_without_linear_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "linear.sqlite"
            output_path = Path(temp_dir) / "brief.html"
            write_mirror_sqlite(sample_dump(), db_path)
            with patch.dict(os.environ, {}, clear=True), patch("sys.stdout") as stdout:
                code = main(["exec-brief", "--sqlite", str(db_path), "--team-key", "GMW", "--output", str(output_path)])
            self.assertTrue(output_path.exists())
            self.assertIn("CEO/CFO Blocker Brief", output_path.read_text())

        output = "".join(call.args[0] for call in stdout.write.call_args_list)
        data = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(data["operation"], "exec-brief")


def issue(issue_id: str, identifier: str, state_name: str, state_type: str) -> dict:
    return {
        "id": issue_id,
        "identifier": identifier,
        "title": f"Task {identifier}",
        "url": f"https://linear.app/{identifier}",
        "priorityLabel": "High",
        "updatedAt": "2026-06-01T00:00:00Z",
        "state": {"id": f"state-{state_type}", "name": state_name, "type": state_type},
        "assignee": None,
        "project": {"id": "project-1", "name": "Cerebro"},
        "labels": {"nodes": [{"id": "label-1", "name": "Paylocity"}]} if identifier == "GMW-1" else {"nodes": []},
    }


def related_response(connection_name: str, nodes: list[dict]) -> dict:
    return {
        "issue": {
            connection_name: {
                "nodes": nodes,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }
    }


def sample_dump() -> dict:
    first = issue("issue-1", "GMW-1", "Todo", "unstarted")
    first["team"] = {"id": "team-gmw", "key": "GMW", "name": "Greenmark"}
    second = issue("issue-2", "GMW-2", "Done", "completed")
    second["team"] = {"id": "team-gmw", "key": "GMW", "name": "Greenmark"}
    third = issue("issue-3", "AIC-1", "Todo", "unstarted")
    third["team"] = {"id": "team-aic", "key": "AIC", "name": "AIC"}
    return {
        "query": {"include_related": True},
        "account": {
            "profile": "greenmark",
            "viewer": {
                "id": "viewer-1",
                "name": "Daniel",
                "email": "daniel@greenmarkwaste.com",
                "organization": {"id": "org-gmw", "name": "Greenmark", "urlKey": "greenmark"},
            },
            "organization": {"id": "org-gmw", "name": "Greenmark", "urlKey": "greenmark"},
        },
        "teams": [
            {"id": "team-gmw", "key": "GMW", "name": "Greenmark"},
            {"id": "team-aic", "key": "AIC", "name": "AIC"},
        ],
        "issues": [first, second, third],
        "related": {
            "comments": [
                {
                    "id": "comment-1",
                    "issue_id": "issue-1",
                    "issue_identifier": "GMW-1",
                    "body": "Status note",
                    "createdAt": "2026-06-01T01:00:00Z",
                    "updatedAt": "2026-06-01T01:00:00Z",
                    "user": {"id": "user-1", "name": "Daniel"},
                }
            ],
            "attachments": [],
            "history": [],
            "state_spans": [],
        },
        "analytics": {"totals": {"issues": 2}},
    }


if __name__ == "__main__":
    unittest.main()
