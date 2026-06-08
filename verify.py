from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    checks = [
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        [sys.executable, "scripts/security_scan.py"],
        [str(ROOT / "bin" / "lineardb"), "--account", "greenmark", "auth-check", "--dry-run", "--team-key", "GMW"],
        [str(ROOT / "bin" / "lineardb"), "--account", "greenmark", "sync", "--dry-run", "--sqlite", "/tmp/lineardb-verify.sqlite"],
    ]
    failures: list[dict[str, str]] = []
    for command in checks:
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            failures.append(
                {
                    "command": " ".join(command),
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            )
    payload = {
        "passed": not failures,
        "checks": len(checks),
        "failures": failures,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
