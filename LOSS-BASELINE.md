# Loss Baseline - 2026-06-08

| Composite | Value |
|-----------|-------|
| Loss | 0.24 (lower is better) |
| Mission | 0.76 (higher is better, max 1.0) |

## Missions

| Mission | Score | Evidence |
|---------|-------|----------|
| Mirror Linear data locally | 0.85 | `lineardb sync` writes teams, issues, snapshots, comments, attachments, history, and state spans to SQLite. |
| Preserve credential boundaries | 0.90 | Explicit accounts fail closed and tests cover ambient credential refusal. |
| Analyze from local data | 0.80 | `lineardb analytics` reads SQLite without Linear API calls. |
| Ship as an Eidos tool | 0.65 | FOSS, CI, forge provenance, and eidosagi.com tool page exist; public remote/release still pending. |

## Losses

| Loss | Value | Evidence |
|------|-------|----------|
| Wrong-workspace credential risk | 0.10 | `--account greenmark` refuses ambient credentials. |
| Live Greenmark proof gap | 0.45 | No `LINEARDB_GREENMARK_*` credential is present in this shell yet. |
| Packaging/release gap | 0.35 | Source install is documented; PyPI release is not done. |
| Schema contract gap | 0.20 | SQLite schema is implemented in code; JSON/schema contracts are not yet externalized. |

Run `python verify.py` before and after changes. If loss rises without a
mission improvement, the change is wrong.
