# Project operations

`ard.features.operations.install(app)` installs this router once. The operations tab loads
`operation-tools.js` after `features.js`. All project reads resolve `Service.project`;
every alert/sample/rule reference is checked against the path project. Input objects
reject unknown fields and non-finite numbers.

## Project settings

| Method and route | Permission | Result |
| --- | --- | --- |
| `GET /api/projects/{pid}/settings` | Project reader | `{project, usage, telemetry}` |
| `PATCH /api/projects/{pid}/settings` | Project administrator | Updated `{project, usage, telemetry}` |

Settings update payload:

```json
{
  "expected_revision": 1,
  "name": "工程验证项目",
  "description": "使用合成记录验证功能",
  "quota_bytes": 104857600,
  "max_jobs": 4,
  "telemetry_retention_count": 120,
  "telemetry_retention_hours": 168
}
```

Name is 1–100 characters and must contain non-whitespace; description is at most 2000.
Quota is 1024–10737418240 bytes; jobs are 1–20. Retention is 10–2000 records and
1–8760 hours, defaulting to 120 records / 168 hours. Description defaults to empty.
`expected_revision`, name, quota and max jobs are required.

The revision, quota floors, update, telemetry pruning and audit events share one SQLite
transaction. A stale revision or a quota below current usage returns 409 without
changing the name or other settings. Stored usage sums `size_bytes` over project
records, including immutable/archived versions; it is the same logical accounting as
asset ingestion, rather than physical disk deduplication. Every nonterminal job counts
toward the floor, including paused jobs and jobs waiting for approval. The response
contains `usage.stored_bytes`, `usage.active_jobs`, and the current project revision.

## Audit search and CSV

| Method and route | Query | Result |
| --- | --- | --- |
| `GET /api/projects/{pid}/audit` | `actor`, `action`, `since`, `until`, `limit=200`, `offset=0` | `{items,total,limit,offset}` |
| `GET /api/projects/{pid}/audit/export` | Same filters; `limit=10000`, `offset=0` | UTF-8 BOM CSV download |

All project readers may read/export. Actor/action filters are exact matches, at most
200 characters. Times must be ISO 8601 timestamps with a timezone, for example
`2026-09-12T08:00:00Z`; bounds are inclusive and converted to UTC. Reversed or
timezone-free bounds return 422. The list limit is 1–1000, export limit is 1–10000,
and offset is 0–10000000. Results are newest sequence first. Project creation and
project updates whose original audit scope is global are included only when their
entity ID equals the authorized project.

CSV columns: `seq,at,actor,action,entity_id,project_id,detail,previous_hash,hash`.
`X-Total-Count` and `X-Exported-Count` distinguish matching rows from exported rows.
Cells beginning with formula indicators (`=`, `+`, `-`, `@`), including after leading
whitespace, and cells beginning with tab/CR/LF receive an apostrophe. JSON audit data
is unchanged. This is a local hash chain; external audit anchoring is not configured.

## Explicit telemetry

| Method and route | Input/query | Result |
| --- | --- | --- |
| `POST /api/projects/{pid}/telemetry/sample` | `{"evaluate_alerts":true}`; defaults to true | 201 `{sample,evaluation,pruned,policy}` |
| `GET /api/projects/{pid}/telemetry` | Optional `since`, `until`; `limit=2000`, 1–2000 | `{items,total,policy}` |
| `GET /api/projects/{pid}/telemetry/export` | Optional `since`, `until` | Retained samples as UTF-8 BOM CSV |

Sampling requires developer/admin write permission. Reads/exports require project
access. Sampling is **manual only**; there is no background timer. Concurrent requests
are serialized at insertion and the minimum per-project interval is one second (429
when violated). CPU uses two Linux `/proc/stat` observations about 0.1 seconds apart;
memory uses Linux `MemTotal`/`MemAvailable`; disk uses `shutil.disk_usage` for the data
directory's filesystem. No psutil or new package dependency is required. Linux CPU
and memory counters are unavailable on unsupported hosts: values are null and
`unavailable` explains why. Disk counters remain available where the OS supports them.

The `sample.metrics` object contains `cpu_percent`, `memory_percent`, `disk_percent`,
`cpu_count`, `project_storage_percent`, `active_jobs`, `stored_bytes`, and available
`memory_total_bytes`, `memory_available_bytes`, `disk_total_bytes`, `disk_free_bytes`.
Sample metadata records source, scope, CPU window, timestamp and actor. CPU/memory
describe the service host as visible to the OS, not resources isolated per project.
No GPU collection, scheduler, external metrics service or continuous monitoring is
implied. The project storage percentage uses current persisted record usage.

Retained history lives in SQLite and survives restart. Both age and count limits apply.
Expired samples are hidden on reads; persistent pruning happens on a new sample or
settings update, not on a background timer. Audit events remain in the append-only
chain after telemetry records expire. Alert records retain their observed value and
sample timestamp even when the original telemetry record expires. Audit and alert
history themselves are not subject to telemetry retention.

CSV columns: `id,at,creator,cpu_percent,memory_percent,disk_percent,`
`project_storage_percent,active_jobs,stored_bytes,memory_total_bytes,`
`memory_available_bytes,disk_total_bytes,disk_free_bytes,source,unavailable`.

## Threshold rules and alert history

| Method and route | Permission | Input/result |
| --- | --- | --- |
| `GET /api/projects/{pid}/alert-rules` | Reader | Rule records |
| `POST /api/projects/{pid}/alert-rules` | Admin | Rule input below → 201 rule |
| `PATCH /api/projects/{pid}/alert-rules/{rule_id}` | Admin | Rule input + `expected_revision` → rule |
| `POST /api/projects/{pid}/alerts/evaluate` | Developer/admin | `{"sample_id":"..."}` → `{sample_id,evaluation}` |
| `GET /api/projects/{pid}/alerts` | Reader | Optional `status`, `limit=200`, `offset=0` → `{items,total,limit,offset}` |
| `POST /api/projects/{pid}/alerts/{alert_id}/actions` | Developer/admin | Action input below → updated alert |

```json
{
  "name": "CPU 使用率阈值",
  "metric": "cpu_percent",
  "operator": "gte",
  "threshold": 80,
  "enabled": true
}
```

Metrics: `cpu_percent`, `memory_percent`, `disk_percent`,
`project_storage_percent`, `active_jobs`. Operators: `gt`, `gte` (default), `lt`, `lte`.
Thresholds must be finite, 0–100 for percentages and 0–1000000 for activity counts.
Names contain non-whitespace and are at most 100 characters. At most 50 rules per
project are retained; rules can be edited or disabled. Rule changes close existing
alerts with a `rule_changed` history event. A stale update returns 409 atomically.

Evaluation uses an explicitly referenced retained sample from the same project.
Missing metrics are skipped. Each rule revision has persistent evaluation state;
duplicate or older samples return `already_evaluated_or_older`. A first threshold
breach opens an alert; further breached samples do not duplicate it. A subsequent
normal sample resolves open/acknowledged alerts with a `recovered` event. Manual
resolution while still breached waits for a normal sample before rearming. No network
notification or email is sent. After a rule revision, the next explicit evaluation can
evaluate that changed rule again.

Action payload:

```json
{
  "expected_revision": 1,
  "action": "acknowledge",
  "comment": "已开始检查"
}
```

Actions are `acknowledge` (OPEN → ACKNOWLEDGED) or `resolve` (OPEN/ACKNOWLEDGED →
RESOLVED). Comments default to empty, maximum 1000 characters. Terminal or duplicate
actions and stale revisions return 409. Every alert has `events` containing timestamp,
actor, action and comment; rule snapshot, observed value and sampled time remain in
the alert record. List status is `OPEN`, `ACKNOWLEDGED` or `RESOLVED`; limit is 1–1000,
offset is 0–10000000. The GUI displays the newest 200 matching alerts.

## Verified global backup and offline restore

`POST /api/operations/backup` accepts only:

```json
{"confirm":"BACKUP_ALL_PROJECTS"}
```

The caller must be an administrator with `*` project scope. This is a full database
snapshot of all projects, never a scoped-project export. The endpoint returns
`ai-rd-backup.zip`; response headers include `X-Backup-SHA256` for the entire archive
and `X-Backup-Objects`. The temporary download is removed after the response.

The server waits for in-process Store transactions using the Store lock, rejects
active jobs (including queued, paused and approval-waiting jobs), and uses SQLite's
backup API to create a transaction-consistent database file. The exported file uses
DELETE journal mode and needs no WAL/SHM sidecars. The snapshot is inspected again
for active jobs. Referenced objects are immutable and SHA-256 checked before and
after copying. A `backup:requested` audit event records an attempted snapshot, even
when subsequent content validation fails.

Archive members are only `manifest.json`, `platform.sqlite3`, and
`objects/<64-lowercase-hex-digest>`. Top-level content fields `sha256`, `source_sha256`,
`preview_sha256`, `manifest_sha256`, `artifact_sha256`, `chunks_sha256`, `vector_sha256`,
`vectors_sha256`, `dataset_sha256` and `index_sha256` require present content objects.
Other digest-like values are included when they identify an existing object;
nonstored endpoint/config/text/identity fingerprints are not treated as missing
objects. Unreferenced orphan blobs are omitted. No runtime/private directory,
identity configuration, environment file, credential file or connector secret is
included. User asset content and public metadata are preserved without redaction.

Manifest version 1 records the format `ai-rd-platform-backup`, timestamp, record/object
counts, audit chain count/head, private-configuration exclusion, and a SHA-256/size
entry for every data member. Limits: at most 10000 objects; database at most 64 MiB;
each object at most 100 MiB; manifest at most 4 MiB; expanded total at most 512 MiB.
Backup creation reserves 4 MiB for the manifest, so database plus objects must fit
within 508 MiB. Larger deployments require an independently designed backup process.
Generated ZIP members are stored without compression.

Restore into a **new or empty directory** whose parent already exists:

```bash
python scripts/restore_backup.py ai-rd-backup.zip /path/to/new-empty-runtime
```

The script also works with an installed package available to the selected interpreter.
It validates in a private staging directory, then renames into the destination. Existing
nonempty directories and any symlink in the destination path are rejected. Restore
never overwrites live data and never starts the restored service. ZIP traversal,
absolute/backslash paths, duplicate entries, extra files, links/special files,
encryption, unsupported compression and amplification above 100:1 are rejected.
The ZIP file itself is limited to 516 MiB, in addition to the expanded/member limits.
Every size/hash, database schema/integrity, content reference, project ownership and
audit-chain hash is checked. A failed restore leaves the requested destination
unchanged. SHA-256 and the local chain detect corruption; there is no digital
signature or externally anchored proof of the archive's producer.

Successful output is JSON with destination, records, objects, audit result, archive
SHA-256 and private-configuration exclusion. Reconfigure identities and external
connectors before using the restored deployment. Private container ownership state
is excluded, so restored metadata does not authorize control of existing containers.

## Verification

`python -m pytest tests/test_operation_features.py tests/test_platform.py -q` covers
settings permissions, concurrent revisions, quota floors, CSV/time filters, real Linux
host sampling, persistent/count/age retention, explicit alert evaluation and history,
cross-project references, full backup/restore of actual dataset rows and trained model
predictions, document retrieval, audit integrity, and corrupt/traversing/bomb/link
archives. `node --check ard/static/operation-tools.js` checks script syntax.

The GUI uses checked ARD contexts, escaped text rendering, busy actions, and explicit
reset of inputs/results across project or identity changes. Same-context audit and
alert query sequence guards prevent older responses from replacing newer searches.
Browser/device rendering has not been verified because the session browser URL
policy blocks localhost and local files; no workaround is used.
