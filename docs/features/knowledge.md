# Knowledge documents and grounded retrieval

`ard.features.knowledge.install(app)` installs these routes once. The installed application loads `ard/static/knowledge-tools.js` in the agents tab through the common ARD host. The panel has actual file import, slice settings, version/history selection, source/text/chunk downloads, offset context preview, edit-as-version, archive/restore confirmation, index build and citation-answer controls. All dynamic content renders as text; project/identity transitions clear selected documents, files, edit fields and answers. Delayed responses also check the current context and selected document.

## Document routes

All URLs below start with `/api/knowledge`. Identity authentication and project access apply to every route. `POST` import, versions, archive and index/build require developer or admin. Read operations and answering permit project readers; searches never silently write an index.

| Method and path | Request | Result |
| --- | --- | --- |
| `GET /projects/{pid}/documents` | Query: `include_archived=false`, `include_history=false`, `q=""` (name substring), `offset=0`, `limit=100` (1–250) | `{documents,total,offset,limit}`; each record includes `root_id`, `version`, `head_id`, `is_latest`, `archived`, `status_revision` |
| `POST /projects/{pid}/import` | Multipart: required `file`; optional `name`, `strategy=paragraph`, `chunk_size=600`, `overlap=60`, `delimiter="\n\n"` | 201 immutable document record with original `source_sha256`, extracted/chunk payload `sha256`, text checksum, format, filename and chunk count |
| `GET /documents/{id}` | No body | `{document,text,source,segments,offset_unit}` |
| `GET /documents/{id}/chunks` | Query: `offset=0`, `limit=20` (1–100) | `{document_id,source,chunks,total,offset,limit,offset_unit}`; each chunk includes index, start/end, exact text and source locations |
| `GET /documents/{id}/chunks/{index}` | Query: `context=120` (0–1000) | Exact selected chunk, source locations and `preview:{start,end,before,selected,after}` |
| `GET /documents/{id}/export` | Query: `format=source|text|chunks`, default `chunks` | Attachment: original bytes, UTF-8 extracted text, or JSON containing document/source/text/chunks/segments |
| `POST /documents/{id}/versions` | JSON shown below | 201 new immutable version; old bytes and metadata remain unchanged |
| `POST /documents/{id}/archive` | JSON shown below | `{document_id,archived,status_revision,source_retained,derived_version_ids,reference_policy}` |

Version request:

```json
{
  "name": "合成工艺说明 · 修订",
  "text": "压力超过阈值时需要复核。",
  "expected_revision": 1,
  "strategy": "paragraph",
  "chunk_size": 600,
  "overlap": 60,
  "delimiter": "\n\n"
}
```

The document must be the current, unarchived head. A second writer editing a superseded version gets HTTP 409. New versions retain `parent_id`/`root_id`, increment `version`, preserve the edited text as a new TXT source, and record the parent source digest in provenance. They do not rewrite an imported PDF/DOCX/XLSX file.

Archive request:

```json
{"archived": true, "confirmed": true, "expected_revision": 0}
```

`expected_revision` here is **status_revision**, initially zero, not the immutable document revision. To restore, send `archived:false` and the current returned status revision. Stale revisions get 409. `confirmed:false` gets 400. A separate `document_status` record tracks changes and the audit chain records them. Archive retains source files, extracted payloads, child-version references and historical indexes. It excludes the current version from retrieval; an older version does not silently become current when the latest version is archived. Archived/history versions remain inspectable and downloadable by authorized project readers.

## Actual extraction and bounds

| Input | Extraction and source locations |
| --- | --- |
| TXT, MD | Exact decoded text; UTF-8 (optional BOM) or BOM-tagged UTF-16. Markdown markup is retained. |
| DOCX | Standard OOXML text, tabs and breaks in document paragraphs/tables, plus headers, footers and notes; source XML part and paragraph number. |
| PDF | Actual pypdf text extraction per page; page numbers retained. Encrypted PDFs and documents without extractable text are rejected. No OCR. |
| HTML, HTM | Text with decoded entities and block boundaries; block numbers retained. Script/style/head/template/iframe/object content is excluded. Nothing is executed or fetched; CSS/layout visibility is not evaluated. |
| XLSX | Actual worksheet cell values, sheet names and cell coordinates. Formula expressions are retained as text; formulas/macros are not evaluated. |

Imports accept 1 byte–10 MiB, at most 2,000,000 extracted characters and 5,000 chunks per document. Chunk strategies are `fixed`, `paragraph`, `heading` (Markdown), `sentence`, `delimiter`; chunk size is 50–4,000 and overlap is 0–1,000, strictly below chunk size. Locations are Unicode code-point offsets into **extracted text**, zero based, end exclusive; they are not binary file byte offsets. Separators inserted during structured extraction remain in the exported text, so every chunk satisfies `text[start:end] == chunk.text`.

DOCX/XLSX ZIP checks reject traversal/absolute/backslash/drive paths, symbolic links, duplicate members, encryption, DTD/entity declarations, more than 1,000 members, more than 32 MiB expanded content, and expansion ratios above 200:1. Some large repetitive but legitimate OOXML files may require splitting because of these bounds.

DOCX/PDF/XLSX parse in a disposable Python process, with a 15-second wall timeout and bounded input/output. POSIX systems additionally enforce 768 MiB address space and 10 CPU seconds. PDF extraction rejects more than 300 pages, bounds supported stream decoders to 8 MiB, and checks 32 MiB cumulative page content. XLSX is opened read-only, has no external-link loading, and allows at most 100 sheets/100,000 declared or encountered cells. Unsupported/corrupt/bounded failures return a clear 400 without creating a document. Resource bounds supplement format checks; this is a local parser, not a universal sandbox for arbitrary executables.

Runtime dependencies: `pypdf==6.10.0`, `openpyxl==3.1.5`. DOCX/HTML logic uses Python standard libraries.

## Persistent semantic index

| Method and path | Request | Result |
| --- | --- | --- |
| `GET /projects/{pid}/index` | No body | `{state,identity?,chunk_count,corpus_sha256,index_id?,built_at?,reason?}`; states: `unavailable`, `not_built`, `stale`, `ready` |
| `POST /projects/{pid}/index/build` | No body; writer only | `{state,index_id,identity,chunk_count,embedded_chunks,reused_chunks,corpus_sha256,built_at}` |

The configured connector supplies `embedding_identity() -> {provider,model,endpoint_sha256}`. Endpoints and credentials are not returned. Each cached `knowledge_embedding` binds its finite numeric vector blob to the project, provider/model/endpoint identity and exact chunk text SHA-256. The `knowledge_index` manifest binds each current chunk to a document, original-source SHA and cached vector. No untrusted serialization formats are loaded.

An unchanged build does not embed text again. A new version reuses exact unchanged chunks, and only changed/new text is sent to the configured model. Duplicate text within a project shares a vector; reported embedded/reused counts are unique text chunks, while `chunk_count` counts source positions. Builds send at most 32 texts per connector call. New batch scheduling has a two-minute budget; an in-flight call is additionally governed by the configured connector timeout. Corpus/config checks and quota-checked metadata writes are atomic; concurrent document/config changes reject publication of a stale index.

Cache vectors and manifest snapshots survive restart. Old manifests and vectors remain quota-accounted for restoration/audit; there is no automatic deletion. `knowledge_embedding.sha256`, `knowledge_index.sha256` and `knowledge_index_snapshot.sha256` point to actual blobs. `identity_sha256`, `corpus_sha256` and vector `text_sha256` are checksums, not blob references. Document `source_sha256` and `sha256` are actual blobs; document `text_sha256` may be a checksum only.

The existing `POST /api/projects/{pid}/search` keeps its `{query,top_k,threshold,mode}` payload. It now selects the newest version in each document lineage and excludes archived heads. Keyword search is immediate (10,000 total active chunks maximum). Semantic/hybrid search permits at most 500 active chunks and requires a ready index. It only embeds the **query** on each search. A missing/stale/config-mismatched index returns 400 with a rebuild instruction; it never updates the index during a read. An empty corpus returns no matches without calling an embedding model. Search checks for corpus changes before returning. Semantic responses include the embedding identity.

## Grounded answering

`POST /api/knowledge/projects/{pid}/answer` accepts:

```json
{"query":"压力超过阈值后怎么办？","mode":"keyword","top_k":5,"threshold":0.05}
```

The endpoint retrieves evidence first. No match returns `answer:null`, `answer_type:"no_evidence"`, `generated:false`, `model_called:false`, empty citations and an explicit limitation; it never calls chat. Otherwise the configured chat adapter receives only the question and matched evidence, marked as data. The prompt requires `[S1]` references and acknowledgement of unsupported information.

Successful output has `answer_type:"grounded_generation"`, `answer`, `model`, `generated:true`, `model_called:true`, `citations` and `limitations`, plus retrieval matches/timing. Each citation carries `citation_id`, `used`, document/version, original text, source SHA, chunk index, offsets, source locations and score. The response explicitly says that matching citation identifiers does not verify sentence-level entailment or complete coverage. Generated text with absent/invented citation identifiers is suppressed as `answer_type:"unsupported_generation"`, `answer:null`; the user still receives the evidence and reason. Evidence archived/superseded during generation causes 409 rather than publication of stale citations.

## Verification scope

`python -m pytest tests/test_knowledge_features.py tests/test_workflows.py -q` exercises real parser bytes (including generated PDF/XLSX/DOCX), exact source exports and offsets, malicious/oversized ZIP/PDF/text bounds, lineage/archive rules, cross-project and reader/write permissions, quotas/rollback, persistent index reuse/restart/config/archive changes and no-hit/citation behavior. Chat/embedding stubs are explicitly **synthetic protocol/unit mocks**, not evidence that a remote model was contacted or that retrieval quality was validated. No external service, GPU, OCR or browser-rendering acceptance is claimed. The environment's browser URL policy blocks local views, so browser/device rendering remains unverified.

`node --test tests/test_knowledge_ui.mjs` checks reset behavior and delayed detail/answer rejection against a minimal DOM model. It is a UI unit test, not a substitute for actual browser/device validation.
