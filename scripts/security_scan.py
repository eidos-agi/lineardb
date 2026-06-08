from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "outputs"}
SECRET_PATTERNS = {
    "linear_api_key": re.compile(r"lin_api_[A-Za-z0-9_-]{12,}"),
    "oauth_secret_assignment": re.compile(r"(OAUTH_CLIENT_SECRET|CLIENT_SECRET)\s*=\s*['\"][^'\"]+['\"]"),
    "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def main() -> int:
    findings: list[dict[str, str]] = []
    for path in source_files():
        text = path.read_text(errors="ignore")
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append({"file": str(path.relative_to(ROOT)), "pattern": name})

    gitignore = (ROOT / ".gitignore").read_text()
    required_ignores = [".env", "*.pem", "*.key", "credentials*", "*.sqlite"]
    missing_ignores = [item for item in required_ignores if item not in gitignore]

    payload = {
        "passed": not findings and not missing_ignores,
        "findings": findings,
        "missing_ignores": missing_ignores,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["passed"] else 1


def source_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if path.is_dir() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in {".py", ".md", ".toml", ".yaml", ".yml", ".json"} or path.name in {
            "lineardb",
            ".gitignore",
        }:
            files.append(path)
    return files


if __name__ == "__main__":
    raise SystemExit(main())
