"""FastMCP / MCPServer implementation for Architect-MCP.

Exposes the 5 modular university makerspace context buckets over the Model Context Protocol (MCP).
Supports stdio and HTTP/SSE transports for Claude Desktop, Claude Code CLI, Cursor, and Antigravity.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # pragma: no cover - fallback for mcp < 2.0
    from mcp.server.fastmcp import FastMCP as MCPServer

from architect_mcp.config import get_settings
from architect_mcp.harness.bucket_manager import BucketManager
from architect_mcp.harness.router import ContextRouter
from architect_mcp.harness.safety_validator import assess_text_hazards, validate_build_steps
from architect_mcp.loaders import load_tenant_pack
from architect_mcp.models import (
    BOMInput,
    BuildPlanSpec,
    StepInput,
    StockStatus,
    Subsystem,
)


def create_mcp_server(
    tenant_slug: Optional[str] = None,
    tenant_packs_dir: Optional[Path] = None,
) -> MCPServer:
    """Instantiate and configure the Architect-MCP server with tools, resources, and prompts."""
    settings = get_settings()
    active_slug = tenant_slug or settings.tenant_slug
    packs_dir = tenant_packs_dir or settings.tenant_packs_dir

    manager = BucketManager.from_tenant(
        tenant_slug=active_slug, tenant_packs_dir=packs_dir
    )
    router = ContextRouter(manager)

    app = MCPServer(
        name="architect-mcp",
        version="0.1.0",
        description="Portable Model Context Protocol (MCP) server & modular context harness for university makerspaces",
    )

    # Store references for stateful inspections / testing
    app.manager = manager  # type: ignore[attr-defined]
    app.router = router  # type: ignore[attr-defined]

    # =========================================================================
    # MCP TOOLS
    # =========================================================================

    @app.tool()
    def assemble_context_harness(
        project_description: str,
        subsystems: Optional[list[str]] = None,
        student_id: Optional[str] = None,
        student_certs: Optional[list[str]] = None,
        token_budget: Optional[int] = None,
    ) -> dict[str, Any]:
        """Assemble a safety-first, token-budgeted makerspace context harness for a build project.

        Pulls from all 5 modular buckets (safety, certs, inventory, machines, workflows).
        Guarantees global PPE/safety rules are pinned while dynamically filtering machines
        and stockroom inventory to fit within the token budget.
        """
        budget = token_budget or settings.default_token_budget
        parsed_subs: list[Subsystem] = []
        if subsystems:
            for s in subsystems:
                try:
                    parsed_subs.append(Subsystem(s.lower().strip()))
                except ValueError:
                    continue

        payload = router.assemble(
            project_description=project_description,
            subsystems=parsed_subs or None,
            student_id=student_id,
            student_certs=student_certs,
            token_budget=budget,
        )

        return {
            "university_slug": payload.university_slug,
            "university_name": payload.university_name,
            "makerspace_name": payload.makerspace_name,
            "token_budget_allocated": payload.token_budget,
            "token_budget_used": payload.total_tokens_used,
            "safety_pinned_tokens": payload.safety_pinned_tokens,
            "steward_signoff_likely_required": payload.steward_signoff_likely_required,
            "hazard_warnings": payload.hazard_warnings,
            "missing_certifications": payload.missing_certifications,
            "inferred_subsystems": [s.value for s in payload.inferred_subsystems],
            "safety_protocols_count": len(payload.safety_protocols),
            "matched_machines_count": len(payload.machines),
            "matched_inventory_count": len(payload.inventory),
            "rendered_context": payload.rendered_context,
        }

    @app.tool()
    def search_makerspace_inventory(
        query: str,
        area: Optional[str] = None,
        only_in_stock: bool = False,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search live stockroom inventory by component name, SKU, category, or cabinet/bin location.

        Returns matching items with stock levels (High/Low/Count), cabinet path, and access roles.
        """
        items = manager.search_inventory(
            query=query, area=area, only_in_stock=only_in_stock, limit=limit
        )
        return [
            {
                "sku": it.sku,
                "name": it.name,
                "category": it.category,
                "area": it.area,
                "location": it.location_display,
                "locations": it.locations,
                "quantity": it.quantity_raw,
                "stock_status": it.stock_status.value,
                "unit": it.unit,
                "required_certs": it.required_certs,
                "required_roles": it.required_roles,
            }
            for it in items
        ]

    @app.tool()
    def check_machine_and_certification(
        machine_or_process: str,
        student_id: Optional[str] = None,
        student_certs: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Check equipment operational status, required certifications, and mandatory safety gates.

        Scans for machines matching the query, resolves prerequisite training chains,
        and flags whether a Shop Steward in-person sign-off is required.
        """
        matched = manager.find_machines(query=machine_or_process)
        hazard = assess_text_hazards(machine_or_process, manager.pack)

        if not matched:
            return {
                "found": False,
                "query": machine_or_process,
                "hazard_assessment": {
                    "requires_steward_signoff": hazard.requires_steward_signoff,
                    "warnings": hazard.warnings,
                },
                "message": f"No machine found matching '{machine_or_process}' at {manager.pack.campus.makerspace_name}.",
            }

        primary = matched[0]
        chain: list[dict[str, Any]] = []
        if primary.required_cert:
            for tier in manager.resolve_certification_chain(primary.required_cert):
                chain.append(
                    {
                        "id": tier.id,
                        "name": tier.name,
                        "level": tier.level,
                        "training_url": tier.training_url,
                        "requires_in_person_checkoff": tier.requires_in_person_checkoff,
                    }
                )

        held = {c.strip().lower() for c in (student_certs or []) if c.strip()}
        has_cert = True
        if primary.required_cert:
            req_lower = primary.required_cert.lower()
            tier_obj = manager.get_certification(primary.required_cert)
            tier_name = tier_obj.name.lower() if tier_obj else ""
            if req_lower not in held and tier_name not in held:
                has_cert = False

        signoff_required = (
            primary.requires_steward_signoff
            or hazard.requires_steward_signoff
        )

        return {
            "found": True,
            "machine": {
                "id": primary.id,
                "name": primary.name,
                "model_number": primary.model_number,
                "area": primary.area,
                "status": primary.status.value,
                "units_available": primary.units_available,
                "work_envelope": primary.work_envelope,
                "manual_url": primary.manual_url,
                "setup_video_url": primary.setup_video_url,
                "forbidden_materials": primary.forbidden_materials,
                "allowed_materials": primary.allowed_materials,
            },
            "required_cert": primary.required_cert,
            "certification_chain": chain,
            "student_eligible": has_cert,
            "requires_steward_signoff": signoff_required,
            "hazard_warnings": hazard.warnings,
        }

    @app.tool()
    def save_hardware_build_plan(
        title: str,
        student_id: str,
        steps: list[dict[str, Any]],
        bom_items: Optional[list[dict[str, Any]]] = None,
        description: str = "",
    ) -> dict[str, Any]:
        """Save a step-by-step hardware build plan with automatic safety gate verification.

        Validates all proposed steps against shop safety rules, deterministically locking any
        heavy machining, laser cutter, or high-voltage steps behind mandatory Steward sign-off.
        """
        step_inputs: list[StepInput] = []
        for s in steps:
            sub = Subsystem.ASSEMBLY
            if "subsystem" in s and s["subsystem"]:
                try:
                    sub = Subsystem(str(s["subsystem"]).lower().strip())
                except ValueError:
                    sub = Subsystem.ASSEMBLY

            step_inputs.append(
                StepInput(
                    step_order=int(s.get("step_order", len(step_inputs) + 1)),
                    subsystem=sub,
                    title=str(s.get("title", f"Step {len(step_inputs) + 1}")),
                    instructions=str(s.get("instructions", "")),
                    area=s.get("area"),
                    machines_used=s.get("machines_used", []) or [],
                    requires_steward_signoff=bool(s.get("requires_steward_signoff", False)),
                    safety_notes=s.get("safety_notes"),
                )
            )

        validated_steps = validate_build_steps(step_inputs, manager.pack)

        bom_inputs: list[BOMInput] = []
        if bom_items:
            for b in bom_items:
                part_name = str(b.get("part_name", ""))
                qty = int(b.get("quantity_needed", 1))
                matches = manager.search_inventory(query=part_name, only_in_stock=True, limit=1)
                in_stock = len(matches) > 0
                sku = matches[0].sku if in_stock else b.get("matched_sku")
                loc = matches[0].location_display if in_stock else b.get("location_hint")

                bom_inputs.append(
                    BOMInput(
                        part_name=part_name,
                        quantity_needed=qty,
                        matched_sku=sku,
                        in_stock_on_campus=in_stock,
                        location_hint=loc,
                        external_purchase_note=b.get("external_purchase_note"),
                    )
                )

        plan = BuildPlanSpec(
            university_slug=manager.university_slug,
            student_id=student_id,
            title=title,
            description=description,
            steps=validated_steps,
            bom_items=bom_inputs,
        )

        saved = manager.register_workflow(plan)
        signoff_steps = [s.step_order for s in saved.steps if s.requires_steward_signoff]

        return {
            "status": "SAVED",
            "project_id": saved.project_id,
            "title": saved.title,
            "student_id": saved.student_id,
            "total_steps": len(saved.steps),
            "safety_gated_steps": signoff_steps,
            "requires_steward_review": len(signoff_steps) > 0,
            "bom_items_count": len(saved.bom_items),
            "in_stock_bom_count": sum(1 for b in saved.bom_items if b.in_stock_on_campus),
            "message": (
                f"Build plan '{saved.title}' registered with {len(saved.steps)} steps. "
                f"{len(signoff_steps)} step(s) locked behind mandatory Shop Steward sign-off."
                if signoff_steps
                else f"Build plan '{saved.title}' registered with {len(saved.steps)} steps."
            ),
        }

    @app.tool()
    def request_steward_safety_signoff(
        project_id: int,
        step_order: int,
        student_id: str,
        ppe_verified: bool,
        setup_summary: str,
    ) -> dict[str, Any]:
        """Request Shop Steward in-person safety sign-off for a hazardous machining step.

        Requires PPE verification and workholding setup summary before submitting.
        """
        plan = manager._workflows.get(project_id)
        if not plan:
            return {
                "status": "ERROR",
                "message": f"Project ID {project_id} not found in active workflows.",
            }

        target_step: Optional[StepInput] = None
        for step in plan.steps:
            if step.step_order == step_order:
                target_step = step
                break

        if not target_step:
            return {
                "status": "ERROR",
                "message": f"Step order {step_order} not found in project {project_id}.",
            }

        if not ppe_verified:
            return {
                "status": "PPE_NOT_VERIFIED",
                "requires_action": "Verify safety glasses, tied-back hair, closed-toe shoes, and no loose jewelry before requesting signoff.",
            }

        target_step.safety_notes = (
            f"[Steward Review Requested by {student_id}]: {setup_summary} (PPE Verified: Yes)"
        )

        return {
            "status": "PENDING_STEWARD_REVIEW",
            "project_id": project_id,
            "step_order": step_order,
            "step_title": target_step.title,
            "machines": target_step.machines_used,
            "instructions": "Step queued for Shop Steward review. Please have on-duty Steward inspect your machine setup in person.",
        }

    @app.tool()
    def complete_build_step(
        project_id: int,
        step_order: int,
        student_id: str,
        steward_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Mark a hardware build step complete.

        CRITICAL SAFETY GATE: If the step requires Shop Steward sign-off and has not been approved,
        completion is strictly blocked until an authorized steward signs off.
        """
        plan = manager._workflows.get(project_id)
        if not plan:
            return {
                "status": "ERROR",
                "message": f"Project ID {project_id} not found in active workflows.",
            }

        target_step: Optional[StepInput] = None
        for step in plan.steps:
            if step.step_order == step_order:
                target_step = step
                break

        if not target_step:
            return {
                "status": "ERROR",
                "message": f"Step order {step_order} not found in project {project_id}.",
            }

        # Check safety gate
        if target_step.requires_steward_signoff:
            if not target_step.safety_approved_by and not steward_id:
                return {
                    "status": "BLOCKED_BY_SAFETY_GATE",
                    "project_id": project_id,
                    "step_order": step_order,
                    "step_title": target_step.title,
                    "error": (
                        f"SAFETY LOCK: Step {step_order} ('{target_step.title}') requires "
                        "in-person Shop Steward sign-off before it can be marked complete. "
                        "Do not power on or operate equipment until a steward inspects your setup."
                    ),
                }

            if steward_id:
                target_step.safety_approved_by = steward_id

        target_step.is_completed = True

        all_completed = all(s.is_completed for s in plan.steps)
        return {
            "status": "COMPLETED",
            "project_id": project_id,
            "step_order": step_order,
            "step_title": target_step.title,
            "all_steps_completed": all_completed,
            "approved_by": target_step.safety_approved_by,
            "message": f"Step {step_order} successfully marked complete.",
        }

    @app.tool()
    def ingest_university_bucket(
        university_slug: str,
        bucket_name: str,
        source_path: str,
    ) -> dict[str, Any]:
        """Hot-reload or ingest an updated university knowledge bucket (safety, inventory, certs, machines)."""
        valid_buckets = {"safety", "certifications", "inventory", "machines", "campus"}
        if bucket_name.lower() not in valid_buckets:
            return {
                "status": "ERROR",
                "error": f"Invalid bucket '{bucket_name}'. Must be one of: {sorted(valid_buckets)}",
            }

        p = Path(source_path).expanduser().resolve()
        if not p.exists():
            return {
                "status": "ERROR",
                "error": f"Source path '{source_path}' does not exist on disk.",
            }

        try:
            reloaded = load_tenant_pack(tenant_slug=university_slug, tenant_packs_dir=packs_dir)
            app.manager = BucketManager(reloaded)  # type: ignore[attr-defined]
            app.router = ContextRouter(app.manager)  # type: ignore[attr-defined]

            return {
                "status": "RELOADED",
                "university_slug": university_slug,
                "bucket_name": bucket_name,
                "counts": app.manager.bucket_summary(),  # type: ignore[attr-defined]
            }
        except Exception as exc:
            return {"status": "ERROR", "error": str(exc)}

    # =========================================================================
    # MCP RESOURCES
    # =========================================================================

    @app.resource("architect://tenant/info")
    def get_tenant_info() -> str:
        """Active university makerspace institutional metadata and emergency contacts."""
        info = {
            "university_slug": manager.pack.campus.university_slug,
            "university_name": manager.pack.campus.university_name,
            "makerspace_name": manager.pack.campus.makerspace_name,
            "location": manager.pack.campus.location,
            "website": manager.pack.campus.website,
            "contact_email": manager.pack.campus.contact_email,
            "default_token_budget": manager.pack.campus.default_token_budget,
            "areas": [a.model_dump() for a in manager.pack.campus.areas],
            "emergency_contacts": manager.pack.campus.emergency_contacts,
            "bucket_counts": manager.bucket_summary(),
        }
        return json.dumps(info, indent=2)

    @app.resource("architect://buckets/safety/all")
    def get_all_safety_protocols() -> str:
        """Complete safety protocol registry and PPE requirements for the active university."""
        protocols = [
            {
                "id": p.id,
                "title": p.title,
                "severity": p.severity.value,
                "priority": p.priority,
                "always_include": p.always_include,
                "requires_steward_signoff": p.requires_steward_signoff,
                "required_ppe": p.required_ppe,
                "forbidden_materials": p.forbidden_materials,
                "content": p.content,
            }
            for p in manager.list_safety_protocols()
        ]
        return json.dumps(protocols, indent=2)

    @app.resource("architect://buckets/certifications/matrix")
    def get_certifications_matrix() -> str:
        """Training badges, levels, and machine access matrix."""
        certs = [
            {
                "id": c.id,
                "name": c.name,
                "level": c.level,
                "granting_area": c.granting_area,
                "prerequisites": c.prerequisites,
                "unlocks_machines": c.unlocks_machines,
                "requires_in_person_checkoff": c.requires_in_person_checkoff,
                "training_url": c.training_url,
            }
            for c in manager.list_certifications()
        ]
        return json.dumps(certs, indent=2)

    @app.resource("architect://buckets/machines/catalog")
    def get_machines_catalog() -> str:
        """Active makerspace equipment catalog with status and envelopes."""
        machines = [
            {
                "id": m.id,
                "name": m.name,
                "model_number": m.model_number,
                "area": m.area,
                "status": m.status.value,
                "units_available": m.units_available,
                "required_cert": m.required_cert,
                "requires_steward_signoff": m.requires_steward_signoff,
                "work_envelope": m.work_envelope,
                "allowed_materials": m.allowed_materials,
                "forbidden_materials": m.forbidden_materials,
                "manual_url": m.manual_url,
            }
            for m in manager.pack.machines
        ]
        return json.dumps(machines, indent=2)

    @app.resource("architect://buckets/inventory/summary")
    def get_inventory_summary() -> str:
        """High-level stockroom summary: total SKUs, categories, and stock health."""
        items = manager.pack.inventory
        out_of_stock = sum(1 for i in items if i.stock_status == StockStatus.OUT_OF_STOCK)
        low_stock = sum(1 for i in items if i.stock_status == StockStatus.LOW)
        categories = sorted({i.category for i in items if i.category})

        summary = {
            "total_items": len(items),
            "out_of_stock_count": out_of_stock,
            "low_stock_count": low_stock,
            "categories": categories,
            "university_slug": manager.university_slug,
        }
        return json.dumps(summary, indent=2)

    @app.resource("architect://projects/{project_id}")
    def get_project_workflow(project_id: str) -> str:
        """Active build plan and Shop Steward safety gate status by project ID."""
        try:
            pid = int(project_id)
        except ValueError:
            return json.dumps({"error": f"Invalid project ID '{project_id}'"}, indent=2)

        plan = manager._workflows.get(pid)
        if not plan:
            return json.dumps({"error": f"Project {project_id} not found"}, indent=2)

        return json.dumps(plan.model_dump(mode="json"), indent=2)

    # =========================================================================
    # MCP PROMPTS
    # =========================================================================

    @app.prompt("architect-build-plan")
    def architect_build_plan_prompt(
        project_idea: str,
        student_email: Optional[str] = None,
    ) -> str:
        """Slash command prompt to decompose a hardware project into safe, stockroom-grounded steps."""
        campus_name = manager.pack.campus.makerspace_name
        email_clause = f" for student {student_email}" if student_email else ""
        return (
            f"You are Architect, the AI Hardware & Machining Assistant for {campus_name}.\n"
            f"The user wants to plan the following hardware build{email_clause}:\n"
            f"'''{project_idea}'''\n\n"
            "Instructions:\n"
            "1. Call `assemble_context_harness` to retrieve institutional safety rules, available machines, and campus stockroom inventory.\n"
            "2. Break down the project into logical steps across mechanical, fabrication, electrical, and firmware subsystems.\n"
            "3. If any step involves heavy machining (lathes, mills, table saws), laser cutting, or >50V high voltage, you MUST explicitly flag it as requiring Shop Steward sign-off.\n"
            "4. Search campus inventory for available fasteners and materials using `search_makerspace_inventory`.\n"
            "5. Save the final build plan using `save_hardware_build_plan` so it appears in the student's checklist."
        )

    @app.prompt("shop-safety-precheck")
    def shop_safety_precheck_prompt(
        machine_name: str,
        material: str,
    ) -> str:
        """Slash command prompt to run a pre-flight safety & certification audit for a machine."""
        campus_name = manager.pack.campus.makerspace_name
        return (
            f"Perform a strict pre-flight safety and certification audit for using the '{machine_name}' "
            f"with material '{material}' at {campus_name}.\n"
            "Instructions:\n"
            "1. Call `check_machine_and_certification` to verify machine operational readiness, work envelope, and required certs.\n"
            "2. Audit if the material is forbidden (e.g., PVC/chlorinated plastics in laser cutters).\n"
            "3. State mandatory PPE and whether an in-person Shop Steward sign-off is required before powering on the equipment."
        )

    @app.prompt("bom-stockroom-audit")
    def bom_stockroom_audit_prompt(
        bom_list: str,
    ) -> str:
        """Slash command prompt to audit a bill of materials against live campus stockroom inventory."""
        campus_name = manager.pack.campus.makerspace_name
        return (
            f"Audit the following Bill of Materials against {campus_name}'s live inventory:\n"
            f"'''{bom_list}'''\n\n"
            "Instructions:\n"
            "1. For each item in the list, use `search_makerspace_inventory` to find on-campus stock.\n"
            "2. Split the list into:\n"
            "   - 'In-Stock on Campus' (include SKU, status, and exact cabinet/bin path)\n"
            "   - 'Must Procure Externally' (suggest standard commercial suppliers like McMaster-Carr or DigiKey)."
        )

    return app


# Module-level default server instance
server = create_mcp_server()


def main() -> None:
    """CLI entrypoint for running the Architect-MCP server."""
    parser = argparse.ArgumentParser(
        description="Architect-MCP: Portable Makerspace Context Harness & MCP Server"
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for HTTP/SSE transport (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port for HTTP/SSE transport (default: 8765)",
    )
    parser.add_argument(
        "--tenant",
        default=None,
        help="University tenant slug to load (e.g. hmc-makerspace, mit-makerworks-demo)",
    )
    args = parser.parse_args()

    app = create_mcp_server(tenant_slug=args.tenant)

    if args.transport == "stdio":
        asyncio.run(app.run_stdio_async())
    elif args.transport == "sse":
        asyncio.run(app.run_sse_async(host=args.host, port=args.port))
    elif args.transport == "streamable-http":
        asyncio.run(app.run_streamable_http_async(host=args.host, port=args.port))


if __name__ == "__main__":
    main()
