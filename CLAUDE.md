# Architect-MCP — Project Engineering Guidelines

**Architect-MCP** is a portable Model Context Protocol (MCP) server and dynamic context bucket harness for university makerspaces.

## Core Directives & Constraints
1. **University Portability**: Never hardcode Harvey Mudd College (or any specific school) rules, machines, or inventory paths in Python source code. All institutional context must come from drop-in files under `tenant_packs/<university>/` (or a configured `ARCHITECT_TENANT_DIR`).
2. **Modular Context Buckets**: Organize context strictly across typed buckets (`safety`, `certifications`, `inventory`, `machines`, `workflows`). Never dump raw full-catalog dumps when bounded retrieval suffices, and never truncate global/pinned safety protocols.
3. **Deterministic Safety Gates**: Operations matching hazardous machinery (mills, lathes, table saws, laser cutters, welders, high voltage >50V) must deterministically flag `requires_steward_signoff = True`.
4. **Secret Hygiene**: Never commit API keys, tokens, or database credentials. Read all configuration via `os.getenv` (`architect_mcp/config.py`).
5. **Small Verified Slices**: Build and test one vertical slice at a time with comprehensive `pytest` coverage before pushing PRs.
