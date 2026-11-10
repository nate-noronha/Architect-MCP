"""Comprehensive test suite for Architect-MCP Slice 3: MCP Tools, Resources & Prompts."""

import json
import pytest

from architect_mcp.server import create_mcp_server


@pytest.fixture
def mcp_app():
    """Fixture returning an initialized Architect-MCP server instance."""
    return create_mcp_server(tenant_slug="hmc-makerspace")


@pytest.fixture
def mit_mcp_app():
    """Fixture returning an Architect-MCP server instance loaded with MIT tenant pack."""
    return create_mcp_server(tenant_slug="mit-makerworks-demo")


@pytest.mark.anyio
async def test_server_metadata_and_tool_registration(mcp_app):
    """Verify all 7 tools, 6 resources, and 3 prompts are registered."""
    tools = await mcp_app.list_tools()
    tool_names = {t.name for t in tools}
    expected_tools = {
        "assemble_context_harness",
        "search_makerspace_inventory",
        "check_machine_and_certification",
        "save_hardware_build_plan",
        "request_steward_safety_signoff",
        "complete_build_step",
        "ingest_university_bucket",
    }
    assert expected_tools.issubset(tool_names)

    resources = await mcp_app.list_resources()
    resource_uris = {r.uri for r in resources}
    assert "architect://tenant/info" in resource_uris
    assert "architect://buckets/safety/all" in resource_uris
    assert "architect://buckets/certifications/matrix" in resource_uris
    assert "architect://buckets/machines/catalog" in resource_uris
    assert "architect://buckets/inventory/summary" in resource_uris

    prompts = await mcp_app.list_prompts()
    prompt_names = {p.name for p in prompts}
    assert "architect-build-plan" in prompt_names
    assert "shop-safety-precheck" in prompt_names
    assert "bom-stockroom-audit" in prompt_names


@pytest.mark.anyio
async def test_tool_assemble_context_harness(mcp_app):
    """Verify assemble_context_harness tool packs safety and inventory."""
    res = await mcp_app.call_tool(
        "assemble_context_harness",
        {
            "project_description": "Machined aluminum bracket for solar tracker with M3 bolts",
            "token_budget": 3500,
        },
    )
    assert res.is_error is False
    data = res.structured_content
    assert data["university_slug"] == "hmc-makerspace"
    assert data["safety_protocols_count"] >= 1
    assert data["token_budget_used"] > 0
    assert "MAKE" in data["rendered_context"] or "Harvey Mudd" in data["rendered_context"]


@pytest.mark.anyio
async def test_tool_search_makerspace_inventory(mcp_app):
    """Verify search_makerspace_inventory returns matching stock and locations."""
    res = await mcp_app.call_tool(
        "search_makerspace_inventory",
        {"query": "wire", "limit": 5},
    )
    assert res.is_error is False
    items = res.structured_content.get("result") if isinstance(res.structured_content, dict) else res.structured_content
    assert isinstance(items, list)
    assert len(items) > 0
    first = items[0]
    assert "sku" in first
    assert "location" in first
    assert "stock_status" in first


@pytest.mark.anyio
async def test_tool_check_machine_and_certification(mcp_app):
    """Verify check_machine_and_certification flags hazardous machines and prereq certs."""
    res = await mcp_app.call_tool(
        "check_machine_and_certification",
        {"machine_or_process": "lathe turning"},
    )
    assert res.is_error is False
    data = res.structured_content
    assert data["found"] is True
    assert data["requires_steward_signoff"] is True
    assert len(data["certification_chain"]) >= 1

    # Check non-existent machine
    res_none = await mcp_app.call_tool(
        "check_machine_and_certification",
        {"machine_or_process": "quantum teleporter flux capacitor"},
    )
    assert res_none.structured_content["found"] is False


@pytest.mark.anyio
async def test_tool_save_build_plan_and_deterministic_safety_locks(mcp_app):
    """Verify save_hardware_build_plan deterministically locks dangerous steps."""
    steps = [
        {
            "step_order": 1,
            "title": "Turn aluminum shaft to 12mm on engine lathe",
            "instructions": "Mount 6061 stock in 3-jaw chuck and take roughing cuts at 750 RPM",
            "machines_used": ["lathe"],
        },
        {
            "step_order": 2,
            "title": "Solder power connector",
            "instructions": "Solder 12V DC barrel jack with lead-free solder",
            "machines_used": ["soldering iron"],
        },
    ]
    bom = [
        {"part_name": "M3 socket head cap screw", "quantity_needed": 4},
    ]

    res = await mcp_app.call_tool(
        "save_hardware_build_plan",
        {
            "title": "Solar Gimbal Shaft",
            "student_id": "student_jordan@hmc.edu",
            "steps": steps,
            "bom_items": bom,
        },
    )
    assert res.is_error is False
    data = res.structured_content
    assert data["status"] == "SAVED"
    assert data["project_id"] is not None
    # Step 1 used lathe -> must be locked
    assert 1 in data["safety_gated_steps"]
    assert data["requires_steward_review"] is True


@pytest.mark.anyio
async def test_steward_gate_locking_and_completion_enforcement(mcp_app):
    """Verify complete_build_step blocks unapproved hazardous steps."""
    # 1. Save build plan with hazardous step
    save_res = await mcp_app.call_tool(
        "save_hardware_build_plan",
        {
            "title": "Milled Heatsink Plate",
            "student_id": "student_sam@hmc.edu",
            "steps": [
                {
                    "step_order": 1,
                    "title": "Fly cut aluminum face on Bridgeport mill",
                    "instructions": "Secure in vise, zero Z with edge finder, fly cut at 1200 RPM",
                    "machines_used": ["bridgeport"],
                }
            ],
        },
    )
    pid = save_res.structured_content["project_id"]

    # 2. Student attempts to mark complete without steward sign-off -> MUST BE BLOCKED
    blocked_res = await mcp_app.call_tool(
        "complete_build_step",
        {"project_id": pid, "step_order": 1, "student_id": "student_sam@hmc.edu"},
    )
    assert blocked_res.is_error is False
    assert blocked_res.structured_content["status"] == "BLOCKED_BY_SAFETY_GATE"
    assert "SAFETY LOCK" in blocked_res.structured_content["error"]

    # 3. Request sign-off with PPE false -> rejected
    ppe_fail = await mcp_app.call_tool(
        "request_steward_safety_signoff",
        {
            "project_id": pid,
            "step_order": 1,
            "student_id": "student_sam@hmc.edu",
            "ppe_verified": False,
            "setup_summary": "Part clamped in vise",
        },
    )
    assert ppe_fail.structured_content["status"] == "PPE_NOT_VERIFIED"

    # 4. Request sign-off with PPE verified -> queued
    ppe_ok = await mcp_app.call_tool(
        "request_steward_safety_signoff",
        {
            "project_id": pid,
            "step_order": 1,
            "student_id": "student_sam@hmc.edu",
            "ppe_verified": True,
            "setup_summary": "Part seated on parallels in Kurt vise, speed set to 1200 RPM",
        },
    )
    assert ppe_ok.structured_content["status"] == "PENDING_STEWARD_REVIEW"

    # 5. Steward signs off and completes
    complete_res = await mcp_app.call_tool(
        "complete_build_step",
        {
            "project_id": pid,
            "step_order": 1,
            "student_id": "student_sam@hmc.edu",
            "steward_id": "steward_alex@hmc.edu",
        },
    )
    assert complete_res.structured_content["status"] == "COMPLETED"
    assert complete_res.structured_content["all_steps_completed"] is True


@pytest.mark.anyio
async def test_mcp_resources_inspection(mcp_app):
    """Verify reading MCP resources returns valid JSON."""
    # Tenant info
    tenant_raw = await mcp_app.read_resource("architect://tenant/info")
    tenant_data = json.loads(tenant_raw[0].content)
    assert tenant_data["university_slug"] == "hmc-makerspace"
    assert "makerspace_name" in tenant_data
    assert len(tenant_data["areas"]) >= 1

    # Safety protocols
    safety_raw = await mcp_app.read_resource("architect://buckets/safety/all")
    safety_data = json.loads(safety_raw[0].content)
    assert isinstance(safety_data, list)
    assert any(p["always_include"] for p in safety_data)

    # Certifications matrix
    certs_raw = await mcp_app.read_resource("architect://buckets/certifications/matrix")
    certs_data = json.loads(certs_raw[0].content)
    assert isinstance(certs_data, list)
    assert len(certs_data) >= 1

    # Machines catalog
    machines_raw = await mcp_app.read_resource("architect://buckets/machines/catalog")
    machines_data = json.loads(machines_raw[0].content)
    assert isinstance(machines_data, list)
    assert any("mill" in m["name"].lower() or "lathe" in m["name"].lower() for m in machines_data)

    # Inventory summary
    inv_raw = await mcp_app.read_resource("architect://buckets/inventory/summary")
    inv_data = json.loads(inv_raw[0].content)
    assert inv_data["total_items"] > 0
    assert len(inv_data["categories"]) > 0


@pytest.mark.anyio
async def test_mcp_prompts_generation(mcp_app):
    """Verify prompt templates render actionable hardware instructions."""
    # Build plan prompt
    build_prompt = await mcp_app.get_prompt(
        "architect-build-plan",
        {"project_idea": "Quadrotor carbon fiber frame", "student_email": "test@hmc.edu"},
    )
    text = build_prompt.messages[0].content.text
    assert "assemble_context_harness" in text
    assert "save_hardware_build_plan" in text

    # Precheck prompt
    precheck = await mcp_app.get_prompt(
        "shop-safety-precheck",
        {"machine_name": "Universal Laser Cutter", "material": "Acrylic"},
    )
    precheck_text = precheck.messages[0].content.text
    assert "check_machine_and_certification" in precheck_text
    assert "forbidden" in precheck_text.lower()


@pytest.mark.anyio
async def test_tenant_portability_across_mcp_server(mit_mcp_app):
    """Verify MIT tenant pack loads cleanly and surfaces MIT-specific data."""
    tenant_raw = await mit_mcp_app.read_resource("architect://tenant/info")
    tenant_data = json.loads(tenant_raw[0].content)
    assert tenant_data["university_slug"] == "mit-makerworks-demo"
    assert "MIT" in tenant_data["university_name"]

    res = await mit_mcp_app.call_tool(
        "check_machine_and_certification",
        {"machine_or_process": "haas cnc mill"},
    )
    assert res.structured_content["found"] is True
