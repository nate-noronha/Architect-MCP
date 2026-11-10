# Architect-MCP: Portable Makerspace Context Harness & MCP Server

**Architect-MCP** is a plug-and-play **Model Context Protocol (MCP)** server and **Dynamic Context Bucket Harness** that allows any personal AI agent (Claude Desktop, Claude Code CLI, Google Antigravity, Cursor, Windsurf) to generate step-by-step hardware assembly and machining instructions grounded in a university makerspace's real safety protocols, machine certifications, and live component inventory.

- **Implementation Plan**: See [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md)
- **Companion Harvey Mudd Native Web App**: [`../E190-Final-Project`](../E190-Final-Project)

## Core Architecture

1. **Modular Context Buckets (`architect_mcp/harness/`)**:
   - `bucket://safety` — Machine-specific PPE, hazard rules, and mandatory Shop Steward sign-off triggers.
   - `bucket://certifications` — Student training tiers and equipment access permissions.
   - `bucket://inventory` — Live stockroom component quantities, bin/cabinet locations, and procurement status.
   - `bucket://machines` — Equipment envelopes, manuals, setup videos, and operational status.
   - `bucket://workflows` — Persistent step-by-step build plans and Shop Steward safety check-off locks.
2. **University-Agnostic Tenant Packs (`tenant_packs/<university>/`)**:
   - Any university can deploy **Architect-MCP** by dropping in their safety Markdown docs, certification YAML, and inventory CSV without modifying server code.

