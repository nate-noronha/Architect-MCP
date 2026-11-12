# Architect-MCP: Portable Makerspace Context Harness & MCP Server

**Architect-MCP** is a plug-and-play **Model Context Protocol (MCP)** server and **Dynamic Context Bucket Harness** that allows any personal AI agent (Claude Desktop, Claude Code CLI, Google Antigravity, Cursor, Windsurf) to generate step-by-step hardware assembly and machining instructions grounded in a university makerspace's real safety protocols, machine certifications, and live component inventory.

- **Implementation Plan**: See [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md)
- **Process Journal**: See [PROCESS.md](./PROCESS.md)
- **Companion Harvey Mudd Native Web App (Wally)**: [`../E190-Final-Project`](../E190-Final-Project)

---

## Core Architecture

1. **Modular Context Buckets (`architect_mcp/harness/`)**:
   - `bucket://safety` — Machine-specific PPE, hazard rules, and mandatory Shop Steward sign-off triggers.
   - `bucket://certifications` — Student training tiers and equipment access permissions.
   - `bucket://inventory` — Live stockroom component quantities, bin/cabinet locations, and procurement status.
   - `bucket://machines` — Equipment envelopes, manuals, setup videos, and operational status.
   - `bucket://workflows` — Persistent step-by-step build plans and Shop Steward safety check-off locks.
2. **University-Agnostic Tenant Packs (`tenant_packs/<university>/`)**:
   - Any university can deploy **Architect-MCP** by dropping in their safety Markdown docs, certification YAML, and inventory CSV without modifying server code. Builtin packs:
     - `tenant_packs/hmc-makerspace/` (Harvey Mudd College MAKE & Machine Shop)
     - `tenant_packs/mit-makerworks-demo/` (MIT MakerWorks Demo)

---

## Quickstart & Installation

```bash
# Clone the repository
git clone https://github.com/nate-noronha/Architect-MCP.git
cd Architect-MCP

# Create & activate a virtualenv (or use active Python 3.11+ environment)
python3 -m venv .venv
source .venv/bin/activate

# Install Architect-MCP and its dependencies
pip install -e .

# Run test suite (all 27 tests run in <1 second)
pytest -v
```

---

## Running the MCP Server

### 1. Standard stdio Transport (For Claude Desktop, Cursor, Antigravity)
```bash
architect-mcp --transport stdio --tenant hmc-makerspace
```

### 2. Streamable HTTP / SSE Transport
```bash
architect-mcp --transport sse --host 127.0.0.1 --port 8765
```

---

## Connecting to Host AI Clients

### Claude Desktop
Add to your `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "architect": {
      "command": "architect-mcp",
      "args": ["--transport", "stdio"],
      "env": {
        "ARCHITECT_TENANT": "hmc-makerspace"
      }
    }
  }
}
```

### Claude Code CLI & Cursor (`.mcp.json`)
```json
{
  "mcpServers": {
    "architect": {
      "command": "architect-mcp",
      "args": ["--transport", "stdio"]
    }
  }
}
```

---

## Protocol Capabilities

### MCP Tools
- `assemble_context_harness(project_description, subsystems, student_id, student_certs, token_budget)`: Assembles priority-packed makerspace context.
- `search_makerspace_inventory(query, area, only_in_stock, limit)`: Live stockroom search with bin/cabinet path.
- `check_machine_and_certification(machine_or_process, student_id, student_certs)`: Equipment operational status, training prerequisite chain, and safety gate triggers.
- `save_hardware_build_plan(title, student_id, steps, bom_items, description)`: Registers a build plan with deterministic safety locks.
- `request_steward_safety_signoff(project_id, step_order, student_id, ppe_verified, setup_summary)`: Places hazardous step into the Steward queue.
- `complete_build_step(project_id, step_order, student_id, steward_id)`: Marks step complete only if safe or approved by a Steward.
- `ingest_university_bucket(university_slug, bucket_name, source_path)`: Hot-reloads updated campus buckets.

### MCP Resources
- `architect://tenant/info`: Campus metadata, areas, emergency contacts.
- `architect://buckets/safety/all`: Complete safety SOP registry and PPE rules.
- `architect://buckets/certifications/matrix`: Training badge tier matrix.
- `architect://buckets/machines/catalog`: Equipment specifications and operational status.
- `architect://buckets/inventory/summary`: Stockroom inventory health and SKU count.
- `architect://projects/{project_id}`: Active build plan and sign-off state.

### MCP Prompts
- `/architect-build-plan` (`project_idea`, `student_email`): Decomposes hardware builds into safe, inventory-backed steps.
- `/shop-safety-precheck` (`machine_name`, `material`): Pre-flight audit for machine/material hazards and certs.
- `/bom-stockroom-audit` (`bom_list`): Categorizes BOM into free campus stock vs. external orders.
