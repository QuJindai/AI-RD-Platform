# Real HTTP functional acceptance

`scripts/functional_smoke.py` uses only Python standard libraries and `httpx`. It does not import application internals, patch functions, inject telemetry, start an application, or create a test client. It works from outside the repository against a running installed-wheel service.

```bash
python /path/to/scripts/functional_smoke.py \
  --base-url http://127.0.0.1:8765 \
  --output /path/to/evidence/functional-http.json
```

The service must have the feature routers installed. Each invocation creates a uniquely named **synthetic** project and leaves its persistent assets/history available for inspection. No existing project is edited. The script stops the prediction deployment it creates; it does not stop or delete other users' deployments, jobs or containers. The final explicitly requested backup covers the whole platform and therefore requires no active jobs anywhere in that service. Use a dedicated acceptance instance for repeatable full coverage.

## Identities

For complete coverage, configure two different synthetic users in the running service:

- `ARD_TOKEN`: global (`projects:["*"]`) administrator used for creating data, submitting labels, settings and whole-platform backup.
- `ARD_REVIEW_TOKEN`: a different user with global reviewer/admin rights, used for annotation review and training-data approval.

These environment variables belong to the script process and contain the matching credentials for the server's configured identities. Credentials never enter the report. They are not generated or installed by this script.

Without `ARD_REVIEW_TOKEN`, local administration can run the other feature chains. The script actually submits labels and verifies that self-review returns 403, then reports independent review/export as `NOT_RUN` coverage. It does not impersonate another user or mark unperformed review as successful. Nonlocal identity configurations also need a separate reviewer for training-data approval; insufficient permissions fail with the actual HTTP status.

## Checked feature chains

| Area | Actual checks |
| --- | --- |
| Data | Synthetic record creation, immutable deduplication, exported data/digest, metadata version and catalogue search, exact category partition, distribution comparison, archive/restore with source bytes retained. |
| Annotation | Eligible identity list, selected source rows, persisted submission, self-review denial, then independent review and labelled immutable dataset export when the reviewer credential is supplied. |
| Knowledge | TXT multipart import, original bytes/digest, Unicode offsets and context, edit as new version, current-version source citations, no-hit generation suppression, archive/restore. No chat model is needed for the no-hit check. |
| Models | Approved training data, actual CPU training and held-out metrics, platform JSON package export/import, reusable baseline execution/history, selected-dataset reevaluation and row results, same-row model comparison, downloadable HTML report, actual deployment predictions/statistics, stopping the created deployment. |
| Workflow | New-node definition validation, JSON package roundtrip, variable/condition/iteration execution, immutable dataset output, and false-branch text template with unused nodes skipped. |
| Skills | The eight supported operations and their input schemas; creation/invocation of a real data-profile skill and its persistent run history. |
| Operations | Settings/revision change, manual collection of real host/project telemetry, a deliberately permissive test rule (`active_jobs >= 0`) evaluated on that actual sample, alert acknowledge/resolve history, telemetry and project audit CSV, audit hash chain. |
| Packages | Actual synthetic dependency ZIP upload, individual member digests, exact download bytes and dependency-discovery NDJSON. The listed dependency is not installed. |
| Backup | Whole-platform ZIP download/digest, manifest format/membership, every member length/SHA, required cross-module source/artifact objects, SQLite integrity/record count, current project and persisted workflow dataset, and manifest audit result. |

Each job wait has a 30-second deadline. HTTP requests use bounded timeouts and no redirects/proxy inheritance. A transport, permission, assertion or application error ends the run and saves a `FAIL` report containing the completed evidence and the error. No model, Docker or third-party MCP response is mocked.

## Evidence output and exit codes

Each completed check prints `PASS <name>` to stderr. Stdout and `--output` receive a JSON report with actual check names, observed values/asset IDs, HTTP method/path/status/timing/response SHA-256, produced artifact IDs, downloaded report/CSV/backup digests, unrun coverage and errors. It retains no authorization headers or full backup bytes. The downloaded backup is checked in memory and is not a restore/restart test.

| Exit code | Report status | Meaning |
| --- | --- | --- |
| `0` | `PASS` | All requested local feature chains passed, including independent annotation review/export. |
| `2` | `PARTIAL` | Implemented checks passed, but independent annotation review/export lacked a separate reviewer credential. |
| `1` | `FAIL` | A real request, permission check, job or assertion failed; earlier checks are preserved. |

External chat/embedding availability, semantic quality, live Docker/third-party MCP, database source setup, OCR, browser rendering, GPU execution and target hardware are not acceptance claims from this script. Database sources and external protocol behavior have separate module tests. A `PASS` is evidence for the enumerated CPU-capable HTTP chain, not for those unexercised systems.
