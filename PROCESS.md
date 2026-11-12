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
