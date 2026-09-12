# Functional completion implementation plan

**Goal:** Complete persistent feature chains behind the approved five-module interface.

**Architecture:** Independent feature routers and feature JavaScript controllers reuse Service/Store/Jobs and a root-owned UI host. Integrate and review after each domain is implemented, then verify the installed package.

**Tech Stack:** Python 3.12, FastAPI, SQLite, scikit-learn, vanilla JavaScript; pinned parsing/connector dependencies.

**Spec:** docs/functional-spec.md

## Global constraints

Apply every invariant and exact common interface from the specification. Root owns shared files; domain workers do not edit each other's modules or spawn agents. Browser policy blocks are not to be bypassed. Public artifacts contain synthetic examples only.

## Task 1: Data and annotation

Files: ard/features/data.py, ard/engines/data.py, ard/static/data-tools.js, tests/test_data_features.py, docs/features/data.md.

- [x] Implement and test the data contracts in the specification, including actual import/transform results, independent annotation/review, immutable metadata versions, reference-aware archive and cross-project denial.
- [x] Connect the feature to the data tab using ARD.panel/register/run; selected assets and labels must reset at the project boundary.
- [x] Run the domain tests plus original data tests and record the exact results.

## Task 2: Documents and retrieval

Files: ard/features/knowledge.py, ard/knowledge.py, ard/static/knowledge-tools.js, tests/test_knowledge_features.py, docs/features/knowledge.md.

- [x] Parse supported files into source text; inspect chunks, version/archive documents and build persistent model-bound indexes.
- [x] Ground answer generation in selected evidence; a no-hit query returns no answer without calling a model.
- [x] Test source offsets, parser failure limits, index invalidation, permissions and no-hit behavior; connect actual controls.

## Task 3: Model lifecycle

Files: ard/features/models.py, ard/connectors.py, ard/static/model-tools.js, tests/test_model_features.py, docs/features/models.md.

- [x] Validate model imports without untrusted deserialization; implement versioned baselines, actual reevaluation, comparison and HTML reports.
- [x] Add configured OpenAI-compatible protocol support while preserving Ollama chat/embedding signatures and explicit unavailable errors.
- [x] Test model shape/type validation, metrics, report escaping, timeout/protocol faults and identity isolation; connect actual controls.

## Task 4: Workflows, skills and bounded agents

Files: ard/features/workflows.py, ard/features/skills.py, ard/workflows.py, ard/jobs.py, ard/static/workflow-tools.js, ard/static/skill-tools.js, tests/test_workflow_features.py, docs/features/workflows.md.

- [x] Add useful executable nodes, durable pause/resume/retry, export/import and skill version/run history; preserve approval atomicity.
- [x] Build a keyboard-usable visual editor with node/edge controls, drag ordering and JSON roundtrip.
- [x] Test branching/iteration limits, cancelled and resumed execution, no arbitrary code, publication permissions and invalid references.

## Task 5: Project operations

Files: ard/features/operations.py, ard/static/operation-tools.js, tests/test_operation_features.py, scripts/restore_backup.py, docs/features/operations.md.

- [x] Implement atomic project settings, filtered audit export, telemetry/alerts, action history and a verified backup.
- [x] Test read/write roles, stale revisions, storage usage floors, retention bounds, alert lifecycle and restored content integrity.
- [x] Connect settings, audit, monitoring, alerts and backup controls in the operations tab.

## Task 6: External integrations

Files: ard/features/integrations.py, ard/static/integration-tools.js, tests/test_integration_features.py, docs/features/integrations.md.

- [x] Implement safe immutable tool/dependency packages and manifests, configured HTTP MCP handshake/list/call, and availability-checked Docker managed-instance lifecycle.
- [x] Test archive traversal/bombs, secret masking, transport errors, invocation bounds and managed-container ownership.
- [x] Connect actual package, MCP and runtime controls; label missing Docker explicitly.

## Task 7: Integration and delivery

Files: ard/features/common.py, ard/features/__init__.py, ard/features/sources.py, ard/static/features.js, ard/static/source-tools.js, ard/api.py, ard/static/app.js, ard/static/index.html, ard/static/style.css, tests/test_source_features.py, scripts/functional_smoke.py, requirements.lock and delivery docs.

- [x] Provide the exact ARD host interfaces and install all feature routers; preserve original IDs and handlers.
- [x] Add configured read-only database extraction and immutable snapshots; reject arbitrary file paths and write statements.
- [x] Run all Python and UI tests, complete real HTTP acceptance, wheel install and recovery verification.
- [x] Review the integrated change, resolve concrete issues, update actual coverage/evidence, prepare the public main update and enforce CI gates for the exact commit. The remote workflow records publication validation.
