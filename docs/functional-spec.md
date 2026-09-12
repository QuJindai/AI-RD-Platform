# Functional completion specification

The user approved the existing five-module GUI and requested functional development. Extend the installed application with actual persistent operations; keep the standalone GUI mockup explicitly synthetic. Preserve existing data and the 77 Python / 11 JavaScript regressions.

## Architecture and invariants

- Python 3.12, FastAPI, Store/Service/Jobs and same-origin vanilla JavaScript remain the platform foundation.
- Feature routers live in ard/features/. Each exposes install(app), which includes its router exactly once. Root integrates installers before static routes.
- Dependencies come from ard.features.common: Svc, User and Input. app.state.security resolves the same identity as the original API.
- Every lookup uses Service.project/record/list and every mutation uses allow_write or a stronger admin/reviewer rule. Multi-record writes use Store.transaction; quota checks and revision checks are atomic.
- All asset versions are immutable. Metadata changes create new versions or distinct metadata records. Removal is explicitly confirmed, reversible archival with referential checks; no automatic blob erasure.
- User content is not executed on the host. Optional Docker, database and remote model adapters report unavailable honestly. Protocol fixtures are not real third-party service evidence.
- Secrets are server configuration or stored outside public records with restricted file permissions. Never return secrets, log credentials or accept model-generated endpoint addresses.
- Plain language Chinese forms expose every newly delivered capability. Project/identity reset clears all feature results and inputs; delayed responses cannot populate another context.
- Public source and tests use only synthetic records and generic descriptions; do not publish the original requirements document.

## Independent modules

1. Data: searchable/versioned metadata, distribution comparison, category/manual split, confirmed archive, spreadsheet/semistructured imports, media integrity/preview, independently assigned row/text labels and review/export.
2. Knowledge: TXT/MD/DOCX/PDF/HTML/XLSX import, source-preserving chunk preview/export, edit as new version/archive, persistent semantic index with model identity, grounded answer endpoint with citations and explicit no-hit behavior.
3. Models: import/export validated platform JSON model packages, reusable immutable experiment baselines, evaluation on a selected dataset and downloadable HTML report, model comparison, OpenAI-compatible and Ollama protocol support, actual deployment statistics.
4. Workflow and skills: visual node/edge editor with JSON roundtrip, validated condition/variable/template/bounded iteration and dataset-output nodes, pause/resume/retry and logs, import/export/versioning; eight useful builtin skill operations with versioned skill definitions, run history and a bounded agent runtime.
5. Operations: project settings/quota management, audit filtering/export, bounded telemetry history and export, threshold alerts with acknowledge/resolve history, verified backup artifact.
6. Integrations: immutable dependency/tool package registry and manifests, Docker configuration/export and availability-checked lifecycle for managed containers; configured HTTP MCP initialize/list/call with bounded transport and explicit invocation.
7. Root integration: configured read-only data sources and dataset snapshots; common UI host, router installation, dependency locks, complete tests, package/recovery checks and public repository publication.

## Feature UI host contract

Every feature JavaScript file is a deferred classic script loaded after app.js and features.js. It may call:

- ARD.panel(tab, id, title, markup): create a card in the existing tab and return its element. markup is trusted static author-written HTML only.
- ARD.register(id, {refresh(context), reset()}): lifecycle hooks; refresh is invoked after core project refresh, reset on identity/project transition.
- ARD.context(): current identity/project context; ARD.current(context): current-context boolean.
- ARD.request(path, options={}, context): same-origin request, credentials and stale-response protection.
- ARD.run(button, task): busy guard, capture project context, invoke task(context), show user-visible errors; ignore stale responses.
- ARD.refresh(context): refresh core project lists and all feature controllers.
- ARD.el(tag, className, text), ARD.option(value,label), ARD.message(text,type), ARD.saveBlob(blob,filename): safe rendering/download helpers.
- ARD.data(): read-only access to current project's datasets, models, workflows, documents, jobs, approvals and selected IDs. Do not mutate this object.
- ARD.readonly controls must not suggest actual live metrics from sample data. All dynamic text uses textContent.

Each module owns its source, feature JS, tests and docs/features/<module>.md with the exact routes and payloads. Shared API, app.js, index.html, style.css, requirement locks and top-level docs are root-owned.

## Acceptance

Unit and API tests prove actual content transforms, permissions, immutable/revision rules, failure/limit behavior and persistence. Existing core regressions stay green. One integrated real HTTP run covers the new CPU-capable chain and produces raw evidence. Wheel installation is tested from outside the repository. Public commit contents must match the tested tree and CI must pass.

Browser URL policy previously blocked both loopback and local files. Do not bypass that policy; retain actual browser/device rendering as unverified. No GPU scheduler, enterprise vGPU isolation, universal neural-model conversion or target-hardware acceptance may be marked implemented through a placeholder or a CPU-only test.
