# Data catalogue and independent annotation

Implementation: `ard/features/data.py`, `ard/engines/data.py`, and `ard/static/data-tools.js`.
`install(app)` is idempotent. The router resolves identity through `app.state.security` and uses the shared service dependencies. The deferred UI script requires `app.js` and `features.js`; its actual Chinese forms appear in the data tab. No browser rendering validation is claimed.

## Authorization and persistence

Every route checks the selected project or source record. Catalogue, comparisons, source rows, media and final labels are readable by identities authorized for that project. Mutations require developer/admin access, except label review, which requires reviewer/admin access. Tokens and user names are resolved by existing security configuration; clients cannot submit a different author name.

Dataset versions are immutable `dataset` records with content-addressed JSON. Metadata edits create a new record with the original content digest and the immediate source in `parents`. Each logical version consumes its full content size against the project quota, matching the existing service. Multi-record writes and quota/revision checks run inside one Store transaction. Archival does not delete a record or any object. Original historical rows and downloads remain available to authorized users.

Blob fields for backups are top-level `sha256` (datasets, media, annotation submissions and accepted reviews) and `preview_sha256` (image previews). Annotation tasks and archive events do not contain separate blobs.

## Catalogue, metadata, partitions and archival

| Method and route | Query or JSON body | Result |
| --- | --- | --- |
| `GET /api/projects/{pid}/data-catalog` | Optional `q` (case-insensitive substring of name, description, tags, ID, parents, `vN` depth or version label), exact `tag`, `lineage_id`, `include_archived=false` | Dataset list enriched with `lineage:{depth,root_ids,parents,children}` and `archive:{archived,revision,reason}`. A lineage includes versions sharing a root with the selected version. IDs disambiguate branches at the same depth. |
| `POST /api/assets/{id}/metadata-version` | `{ "name":"Reviewed data", "tags":["reviewed"], "description":"...", "version_label":"release-1", "expected_revision":1 }` | 201 new immutable dataset; name and expected revision are required, other fields default to empty. Tags: at most 30 unique nonblank strings, each at most 80 characters. |
| `POST /api/assets/{id}/partition` | Exactly one of `{ "column":"category" }` or `{ "groups":[{"name":"A","row_indexes":[0,2]},{"name":"B","row_indexes":[1,3]}] }` | 201 list of nonempty new dataset versions. Category mode supports 2–30 distinct values, including missing values; manual mode supports 2–30 groups, unique names, strict integer row indexes and exhaustive exactly-once assignment. Original row order is retained within each category; manual order follows the supplied indexes. |
| `POST /api/projects/{pid}/data-distributions` | `{ "left_id":"...", "right_id":"...", "columns":["category","value"] }` | Exact count, missing count, distinct count and top 20 values for each version/column; omitted count; finite numeric count/min/max/mean/population standard deviation; exact discrete total variation, 0–1. At most 20 common columns. Numeric summaries exclude booleans and numeric-looking strings. |
| `GET /api/assets/{id}/references` | None | `{asset_id,archive,references}`. |
| `POST /api/assets/{id}/archive` | `{ "archived":true, "confirm":"FULL_DATASET_ID", "expected_revision":0, "reason":"..." }` | 201 immutable `dataset_archive_event`; restore uses `archived:false` and the latest archive revision. `confirm` must equal the path's full ID. Archive revision starts at 0 and increments for each event. |

Archival checks all same-project `job`, `model`, `workflow`, `model_baseline`, `model_evaluation`, `model_comparison`, `experiment` and `experiment_baseline` references, pending approvals, and annotation tasks other than exported/rejected tasks. Historic jobs/models/workflow versions also block archival, preserving reproducibility. The 409 response identifies blocking records. Parent/child dataset links are retained as history and do not themselves block archival. Metadata changes, splits, new annotations and labelled exports reject archived sources. Shared service integration uses `require_active(service, already_authorized_dataset)`; `is_archived(service, dataset)` reads the same state.

`expected_revision` conflicts, repeated archive/restore actions, repeated submissions/reviews/exports and archived source writes return 409. Invalid table/label content returns 400; invalid typed payloads return 422; project/role denial returns 403. No archive operation releases quota because objects remain stored.

## Table imports

The existing `POST /api/projects/{pid}/import` multipart field `file` now uses the following real parsers and returns the usual immutable dataset. The expanded-import GUI provides the complete file selector.

| Format | Actual interpretation and boundary |
| --- | --- |
| CSV | UTF-8 with optional BOM; unique nonblank header fields; no extra cells. |
| JSON | A JSON array of objects; finite numbers only. |
| JSONL / NDJSON | One JSON object per nonblank line. |
| XLSX | `openpyxl`, first visible worksheet; first row contains unique string headers. Dates/times become ISO strings. Formula cells are rejected; upload evaluated values. No macro execution, external workbook following or formula evaluation. `.xls`/`.xlsm` are unsupported. |
| YAML / YML | `PyYAML` safe loader; top-level array of objects. Duplicate/nonstring keys, aliases, custom tags and nonfinite numbers are rejected. Maximum 20 nesting levels and 500,000 parser nodes. |
| XML | UTF-8; root children are rows, direct leaf children are fields, row attributes become `@name` fields. Unique fields only; nested fields and field attributes are unsupported. DTD and entity declarations are rejected. |
| HTML / HTM | First complete table; first row supplies headers; markup reduces to text and scripts/styles are ignored. Nested tables and cell spans other than 1 are rejected. The uploaded HTML is never rendered or executed. |
| Parquet | `pyarrow.parquet`; normalized JSON-compatible rows. Dates/times become ISO strings. Bytes, decimal/custom scalars and unsupported scalar structures are rejected. Row/column and declared expansion bounds are inspected before bounded batch reads. |
| ZIP | Safe supported **text** table members (CSV/JSON/JSONL/NDJSON/YAML/XML/HTML) are combined; other members are ignored. XLSX and Parquet must be uploaded directly, so nested binary decompression is not accepted through ZIP. Nothing is extracted to a user-selected path. |

Limits: 20 MiB input per file or parsed ZIP member, 50,000 rows, 200 distinct top-level columns. All imported nested JSON-compatible values are limited to 20 levels. XLSX/ZIP: at most 200 members and 100 MiB declared expanded bytes, no traversal/absolute/Windows drive paths, symlinks, special files or encryption, and a maximum 200:1 compression ratio for members over 1 MiB. XLSX XML is scanned for DTD/entities before parsing. Parquet declared expansion is limited to 100 MiB and 200:1 above 1 MiB; Thrift string metadata is limited to 20 MiB and containers to 100,000 items. These are bounded CPU parsers, not a general isolated document-conversion service.

Dependencies currently installed and pinned by root integration: Pillow, openpyxl, PyYAML, pyarrow. A missing parser returns an explicit unavailable-dependency error; no substitute/fake format is emitted.

## Media import and preview

| Method and route | Payload | Result |
| --- | --- | --- |
| `POST /api/projects/{pid}/media-import` | multipart `file`, optional `name` (max 150 characters) | 201 `{media,dataset}`. A `media` record stores the original verified file; an immutable one-row dataset stores the media ID and metadata, allowing catalogue search, lineage and row classification. |
| `GET /api/projects/{pid}/media` | None | Authorized project media records. |
| `GET /api/media/{id}` | None | Metadata, original digest, size and image/video inspection scope. |
| `GET /api/media/{id}/content` | None | Original file with detected MIME, a generated safe inline filename, `nosniff` and private/no-store headers. |
| `GET /api/media/{id}/preview` | None | Server-generated PNG thumbnail for an image; 409 for video. |

Images: JPEG/PNG/BMP/TIFF/GIF/WebP are decoded using Pillow, at most 20 million pixels per frame, 100 frames and 64 million cumulative decoded pixels. All frames are loaded; preview uses the orientation-corrected first frame, maximum 1024×1024, with EXIF metadata omitted from the preview. Original downloads retain original bytes and metadata. Valid media type comes from decoded content. Image integrity is recorded as `all_frames_decoded`.

Videos: MP4/AVI/MKV signature checks plus real system `ffprobe` container/stream probing. `ffprobe` is invoked with a fixed argument array, no shell, only a generated temporary file, a format and protocol whitelist, 5 MB probe size, 2-second analysis budget, 32 MiB maximum individual allocation and a 10-second process timeout. Video dimensions are limited to 32 million pixels and duration to 7 days; upload size is 20 MiB. Temporary files are removed. Missing ffprobe returns 503. No URL input, external streaming request or arbitrary command is accepted. Inspection records `container_and_stream_probe_only`: this does not promise complete frame decoding, transcoding, codec repair or a generated poster. Browser playback uses the authenticated original content and depends on codec/container support; originals can be downloaded even when AVI/MKV are not playable by the browser.

## Annotation workflow

1. A developer/admin creates a task with real configured annotator user names. Each name must have developer/admin access to the project. Local mode exposes only `local`; to complete independent review, configure separate annotation and review identities. One annotator is allowed, but a pairwise disagreement rate requires at least two.
2. Each assigned account labels every selected source row and submits once. The server binds the submission to the authenticated user and stores a write-once label blob. Submitted labels cannot be replaced. Before all accounts submit, every account can see only its own labels; even reviewers receive no other labels. Source row data and progress counts remain available to project readers.
3. When all accounts submit, the task becomes `SUBMITTED`. A reviewer/admin who is neither an actual submitter nor, in token mode, the task creator can decide once. Exact canonical label equality is compared for every row and unordered annotator pair; span order is normalized. No κ score or semantic similarity is claimed.
4. Acceptance defaults to shared labels only when every row agrees. If there is a disagreement, the reviewer must supply validated final labels for every selected row. Rejection seals the decision and ends the task; create a new task to repeat work. Accepted labels are immutable through the API.
5. A developer/admin exports an accepted task exactly once into a new immutable dataset containing selected source rows, a configurable label column (default `_label`) and `_source_row`. The original dataset is the parent. Existing column collisions are rejected. Exported datasets use the normal authenticated row and JSON-download endpoints.

| Method and route | Query / JSON body | Result |
| --- | --- | --- |
| `GET /api/projects/{pid}/annotation-annotators` | None | Available user names and writable roles, never tokens. |
| `POST /api/projects/{pid}/annotation-tasks` | `{ "dataset_id":"...", "name":"Independent labels", "task_type":"text_classification", "text_column":"text", "labels":["positive","negative"], "annotators":["alice","bob"], "row_indexes":[0,1] }` | 201 task, `OPEN`, revision 1. Types: `row_classification`, `text_classification`, `span`, `qa`. Omit row_indexes for all rows. QA requires a nonblank `question`. Labels optional only for QA. |
| `GET /api/projects/{pid}/annotation-tasks` | None | Project task metadata and statuses. |
| `GET /api/annotation-tasks/{id}` | None | Task, submission count, `own_submission`, `can_review`; after complete submission, `agreement`; eligible reviewers also receive `submissions:[{annotator,items}]`. Final review details are included after a decision. |
| `GET /api/annotation-tasks/{id}/rows` | `offset=0`, `limit=20` (max 100) | `{total,rows:[{row_index,row}]}`. Offset addresses the task's selected-row list; row_index is the original dataset's zero-based row index. |
| `POST /api/annotation-tasks/{id}/submit` | `{ "expected_revision":1, "items":[{"row_index":0,"value":"positive"},{"row_index":1,"value":"negative"}] }` | 201 task view; submission is bound to current account. All selected rows must appear exactly once. Reload task revision after another account submits. |
| `POST /api/annotation-tasks/{id}/review` | `{ "expected_revision":3, "decision":"accept", "comment":"Reviewed", "items":[...] }` | 201 task view; `ACCEPTED` or `REJECTED`. `items` may be omitted for fully agreeing acceptance; reject forbids items. |
| `POST /api/annotation-tasks/{id}/export` | `{ "expected_revision":4, "name":"Reviewed labelled data", "label_column":"label" }` | 201 `{dataset,task_id,review_id}`; task becomes `EXPORTED`. Name/label column are optional. |

Label value schemas:

| Type | Exact submitted/reviewed `value` | Exported label |
| --- | --- | --- |
| Row/text classification | One string from task.labels | Same string |
| Span | `[{"start":0,"end":2,"label":"entity"}]`; empty array allowed | Same validated spans, each enriched with exact source `text` |
| Extractive QA | `{"start":1,"end":3}` or `{"unanswerable":true}` | Offsets/unanswerable plus the task's `question` and exact extracted `answer` |

Span/QA offsets are zero-based Unicode code points with an exclusive end, not UTF-8 bytes or JavaScript UTF-16 units. The GUI converts selected text offsets accordingly. Span ranges must be nonempty, within source text, sorted canonically and nonoverlapping; at most 200 spans per row. QA uses one common question and one contiguous answer or an explicit no-answer value. Tasks select at most 5,000 unique rows, 10 annotators and 100 unique labels, with labels at most 100 characters. Image bounding boxes, video frame-level geometry, nested/overlapping entities, generative QA, annotator reassignment and resubmission are outside this feature's scope.

## GUI and verification

The real UI contains search/lineage/archive controls, immutable metadata forms, category and editable manual group splits, comparison tables, all supported table/media upload selectors, authenticated media preview/download, task assignment, paged row/text label editors, Unicode text selection, per-annotator review candidates, disagreement status and sealed export/download. All dynamic values use `textContent` or form values. Identity/project reset clears file inputs, text, selection, drafts and rendered results and stops video playback and revokes object URLs. Write controls require developer/admin access; label editing/submission and review buttons also follow assignment, role and task state. Every awaited UI response is checked against the captured context; row/task/media operations also verify the selected object or request sequence before writing.

Domain test command:

```bash
/workspace/scratch/1bd60b330871/ai-rd-venv/bin/python -m pytest tests/test_data.py tests/test_data_features.py -q
node --check ard/static/data-tools.js
```

Recorded domain run (2026-09-12): **68 passed** in 3.02 seconds, with two upstream Starlette/httpx deprecation warnings. JavaScript syntax and scoped diff whitespace checks passed.

Tests use only synthetic records and generated files. They cover actual table values, real ffprobe on locally generated MP4, AVI and MKV files, decoded image previews, independent blinded submissions, classification/span/QA exports, source immutability and persisted content, reference denial, revision conflicts, unauthorized/cross-project access, malformed parser payloads and bounded failure paths. Local browser URLs remain disallowed by the browser tool policy; browser/device rendering is unverified.
