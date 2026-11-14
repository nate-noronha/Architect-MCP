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
