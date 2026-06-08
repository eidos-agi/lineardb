from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .auth import LinearDBError, redact_secret

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"


class LinearGraphQLError(LinearDBError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        messages = "; ".join(str(error.get("message", error)) for error in errors)
        super().__init__(messages)


@dataclass(frozen=True)
class LinearGraphQLClient:
    token: str
    endpoint: str = LINEAR_GRAPHQL_URL
    max_retries: int = 3
    retry_sleep_seconds: float = 1.0

    def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={
                "Accept": "application/json",
                "Authorization": self.token,
                "Content-Type": "application/json",
                "User-Agent": "LinearDB/0.1",
            },
            method="POST",
        )
        body: dict[str, Any] = {}
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    body = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                body_text = exc.read().decode("utf-8", errors="replace")
                if exc.code in {429, 502, 503, 504} and attempt < self.max_retries:
                    time.sleep(self.retry_sleep_seconds * (attempt + 1))
                    continue
                raise LinearDBError(f"Linear HTTP {exc.code}: {redact_secret(body_text, self.token)}") from exc
            except urllib.error.URLError as exc:
                if attempt < self.max_retries:
                    time.sleep(self.retry_sleep_seconds * (attempt + 1))
                    continue
                raise LinearDBError(f"Linear network error: {exc.reason}") from exc

        if body.get("errors"):
            raise LinearGraphQLError(body["errors"])
        return body.get("data") or {}
