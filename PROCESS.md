# Engineering Process & Design Log — Architect-MCP

This document tracks architectural decisions, vertical slice milestones, and automated test results for **Architect-MCP**.

---

## 2026-11-09: Slice 1 — Modular Context Bucket Schemas & University Tenant Loader

- **Branch**: `feat/slice-1-buckets-and-loaders`
- **Deliverables Implemented**:
  - `architect_mcp/config.py`: Dynamic environment-driven configuration (`ARCHITECT_TENANT`, `ARCHITECT_TENANT_DIR`, `DATABASE_URL`, `ARCHITECT_TOKEN_BUDGET`) with zero hardcoded secrets.
  - `architect_mcp/models.py`: Typed Pydantic v2 schemas for all 5 modular context buckets (`safety`, `certifications`, `inventory`, `machines`, `workflows`) plus `CampusConfig` and `TenantPack`.
  - `architect_mcp/loaders/`:
    - `yaml_config_loader.py`: Validates `campus.yaml`, `certifications/*.yaml`, and `machines/*.yaml`.
    - `markdown_sop_loader.py`: Parses Markdown safety SOPs with YAML frontmatter (priority, PPE, forbidden materials, steward sign-off flags).
    - `csv_inventory_loader.py`: Resilient dual-schema CSV ingestor normalizing both `make.hmc.edu` exports (`Type,Name,Quantity,Locations,Required Certs,Required Roles` with `High`/`Low`/`3/8` status parsing) and standard stockroom CSVs (`sku,name,category,facility,location,quantity,unit`).
    - `tenant_loader.py`: Hydrates complete university tenant packs and validates cross-bucket integrity via `validate_tenant_pack()`.
  - Drop-in Tenant Packs in `tenant_packs/`:
    - `hmc-makerspace/` (Harvey Mudd College MAKE + Machine Shop)
    - `mit-makerworks-demo/` (Second campus profile demonstrating zero-code university portability)
- **Verification**:
  - Full Slice 1 test suite in `tests/test_tenant_loader.py` and `tests/test_tenant_portability.py` (`9 passed`).

---

## 2026-11-10: Slice 2 — Dynamic Context Harness, Token Budget Packer & Safety Gate Validator

- **Branch**: `feat/slice-2-context-harness` (stacked on `feat/slice-1-buckets-and-loaders`)
- **Deliverables Implemented**:
  - `architect_mcp/harness/bucket_manager.py`: Indexed registry over `bucket://safety`, `bucket://certifications`, `bucket://machines`, `bucket://inventory`, and `bucket://workflows`, with stopword-filtered keyword tokenization and prerequisite certification chain resolution.
  - `architect_mcp/harness/safety_validator.py`: Deterministic hazard scanner catching heavy machining steps (`lathe`, `mill`, `table saw`, `cnc`, `waterjet`), >50V electrical designs, down/maintenance equipment alerts, and tenant-defined forbidden materials (e.g. PVC/vinyl in laser cutters).
  - `architect_mcp/harness/router.py`: Multidisciplinary subsystem inference and scored multi-bucket context retrieval (`ContextRouter.assemble`).
  - `architect_mcp/harness/token_budget.py`: Deterministic safety-first token packer guaranteeing pinned global SOPs (`always_include=True`) and mandatory steward gates are never dropped while bounding stockroom retrieval to `token_budget`.
- **Verification**:
  - Ran `pytest -v` across all Slice 1 & Slice 2 suites (`18 passed` in `0.19s`: `tests/test_tenant_loader.py`, `tests/test_tenant_portability.py`, `tests/test_context_harness.py`, `tests/test_safety_gates.py`).

---

## 2026-11-10: Slice 3 — FastMCP Server (Tools, Resources, Prompts & CLI)

- **Branch**: `feat/slice-3-mcp-server` (stacked on `feat/slice-2-context-harness`)
- **Deliverables Implemented**:
  - `architect_mcp/server.py`: Complete `MCPServer` implementation supporting `stdio`, `sse`, and `streamable-http` transports.
    - **7 MCP Tools**:
      - `assemble_context_harness`: Dynamic multi-bucket context packer.
      - `search_makerspace_inventory`: Stockroom search with cabinet/bin path & role gating.
      - `check_machine_and_certification`: Operational readiness, cert chains, and safety gates.
      - `save_hardware_build_plan`: Registers build plans with deterministic safety locking.
      - `request_steward_safety_signoff`: Gated steward check-off request requiring verified PPE.
      - `complete_build_step`: Enforces safety gate block until steward sign-off.
      - `ingest_university_bucket`: Admin tool hot-reloading updated campus buckets.
    - **6 MCP Resources**:
      - `architect://tenant/info`
      - `architect://buckets/safety/all`
      - `architect://buckets/certifications/matrix`
      - `architect://buckets/machines/catalog`
      - `architect://buckets/inventory/summary`
      - `architect://projects/{project_id}`
    - **3 MCP Prompts**:
      - `/architect-build-plan`
      - `/shop-safety-precheck`
      - `/bom-stockroom-audit`
  - `pyproject.toml`: Added `mcp>=2.0.0` and console script `architect-mcp = architect_mcp.server:main`.
  - Client configuration templates in `examples/claude_desktop_config.json` and `examples/mcp_config.json`.
- **Verification**:
  - Full end-to-end test suite in `tests/test_mcp_tools.py` (`9 passed`).
  - Total test suite across all 3 slices: `27 passed` in `0.57s`.

---

## 2026-11-14: Slice 4 — Durable Workflow Persistence, Steward Queue & Campus Web App Bridge

- **Branch**: `feat/slice-4-persistent-workflows`
- **Deliverables Implemented**:
  - `architect_mcp/db.py`:
    - SQLModel persistence layer (`architect_projects`, `architect_steps`, `architect_bom_items`) supporting standalone SQLite (`DATABASE_URL`) and PostgreSQL.
    - `WorkflowRepository`: Durable CRUD for `save_hardware_build_plan`, `request_steward_safety_signoff`, `approve_steward_safety_signoff`, `complete_build_step`, and `list_pending_signoffs`.
    - `WebAppBridge`: Optional schema-aware sync (`ARCHITECT_WEBAPP_DATABASE_URL`) mirroring MCP build plans, BOM items, and Shop Steward sign-off approvals directly into a companion campus web portal database (`users`, `projects`, `project_steps`, `project_bom_items`).
  - `architect_mcp/harness/bucket_manager.py`: Integrated optional `WorkflowRepository` into `BucketManager` (`register_workflow`, `get_workflow`, `list_workflows`, `bucket_summary`).
  - `architect_mcp/server.py`:
    - Wired `WorkflowRepository` into `create_mcp_server`.
    - Added `approve_steward_safety_signoff` MCP tool.
    - Added `architect://stewards/queue` MCP resource for inspecting pending Shop Steward sign-offs.
  - `architect_mcp/config.py` & `.env.example`: Added `ARCHITECT_WEBAPP_DATABASE_URL` configuration.
- **Verification**:
  - Added `tests/test_persistent_workflows.py` (`3 passed`: server-restart persistence, companion web app DB bridge sync, and missing-schema no-op safety).
  - Full test suite across Slices 1–4: `30 passed` in `0.88s`.

---

## 2026-11-14: Slice 5 — Admin & Compliance CLI (`architect-admin`) & Full Compliance Suite

- **Branch**: `feat/slice-5-admin-cli` (stacked on `feat/slice-4-persistent-workflows`)
- **Deliverables Implemented**:
  - `architect_mcp/cli.py`:
    - `list-tenants`: Lists discovered university tenant packs and bucket counts.
    - `validate-pack`: Cross-bucket integrity validator (`--all`, `--json`) checking certs, machines, inventory, and pinned safety protocols.
    - `ingest-csv`: Parses and validates makerspace inventory CSVs (`--apply` installs into target tenant `inventory/` and re-validates).
    - `inspect-harness`: Runs `ContextRouter.assemble` on any build prompt and reports token budget usage, hazard warnings, and steward gate triggers.
  - `architect_mcp/server.py` & `pyproject.toml`: Registered `architect-admin` console script and dispatched admin subcommands cleanly through `architect-mcp <subcommand>` while preserving default MCP server mode.
  - `README.md`: Documented Admin & Compliance CLI commands.
- **Verification**:
  - Added `tests/test_admin_cli.py` (`6 passed`).
  - Full test suite across all 5 slices: `36 passed` in `0.82s`.

---

## 2026-11-14: Slice 6 — University Starter Pack, Onboarding CLI & Live MCP `stdio` E2E Verification

- **Branch**: `feat/slice-6-onboarding-and-e2e` (stacked on `feat/slice-5-admin-cli`)
- **Deliverables Implemented**:
  - `tenant_packs/university-template/`: Drop-in starter pack (`campus.yaml`, `safety/01_general_safety.md`, `certifications/certs.yaml`, `machines/machines.yaml`, `inventory/sample_inventory.csv`) for instant zero-code university onboarding.
  - `architect_mcp/cli.py`:
    - `init-pack`: Scaffolds a new university tenant pack from `university-template`, customizes `campus.yaml`, and validates cross-bucket integrity.
    - `print-config`: Generates ready-to-paste MCP client JSON configs (`claude-desktop`, `cursor`, `antigravity`) with the resolved Python executable and tenant environment variables.
- **Verification**:
  - Added `tests/test_stdio_protocol_e2e.py` (`4 passed`), including a live subprocess `stdio` JSON-RPC round-trip (`ClientSession` + `stdio_client`) verifying `initialize`, `list_tools`, `list_resources`, `list_prompts`, `assemble_context_harness`, `save_hardware_build_plan`, and `complete_build_step` safety gate blocking over real OS pipes.
  - Full test suite across Slices 1–6: `40 passed` in `1.45s`.

---

## 2026-11-14: Slice 7 — Remote HTTP/SSE Deployment Readiness, `/health` Endpoint & Docker/Railway Config

- **Branch**: `feat/slice-7-http-and-deploy` (stacked on `feat/slice-6-onboarding-and-e2e`)
- **Deliverables Implemented**:
  - `architect_mcp/server.py`:
    - Added `/health` GET custom route returning service status, active university slug, makerspace name, and bucket counts across both `streamable-http` and `sse` ASGI apps.
    - Added `$PORT` environment variable fallback for cloud deployment port binding.
  - `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `railway.toml`: Containerized remote MCP server deployment with persistent SQLite volume (`/data/architect_mcp.db`) and `/health` check.
- **Verification**:
  - Added `tests/test_http_transport.py` (`3 passed`: `/health` on Streamable HTTP, `/health` on SSE, and `/mcp` JSON-RPC `initialize` handshake).
  - Full test suite across Slices 1–7: `43 passed` in `1.56s`.

---

## 2026-11-14: Slice 8 — Safety Audit Telemetry (`architect_audit_logs`) & GitHub Actions CI

- **Branch**: `feat/slice-8-audit-and-ci` (stacked on `feat/slice-7-http-and-deploy`)
- **Deliverables Implemented**:
  - `architect_mcp/db.py`:
    - Added `AuditLogRow` (`architect_audit_logs`) table + `record_audit_event()` and `list_audit_events()` on `WorkflowRepository`.
    - Automatically logs `PLAN_SAVED`, `SIGNOFF_REQUESTED`, `SIGNOFF_APPROVED`, `SAFETY_GATE_BLOCKED`, and `STEP_COMPLETED` compliance events.
  - `architect_mcp/server.py`:
    - Emits `HARNESS_ASSEMBLED` telemetry on `assemble_context_harness` calls.
    - Added `architect://audit/recent` MCP resource exposing the latest 25 safety & tool audit events.
  - `.github/workflows/ci.yml`: Automated CI pipeline running `architect-mcp validate-pack --all` and `pytest -v` on every push and PR.
- **Verification**:
  - Added `tests/test_audit_telemetry.py` (`1 passed` end-to-end across all 6 audit event types).
  - Full test suite across Slices 1–8: `44 passed` in `1.86s`.
