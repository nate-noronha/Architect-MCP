"""Slice 8 tests: Safety audit telemetry & `architect://audit/recent` MCP resource."""

import json
from pathlib import Path

import pytest

from architect_mcp.server import create_mcp_server


@pytest.mark.anyio
async def test_full_safety_audit_trail_recorded_and_exposed(tmp_path: Path):
    """Verify MCP tool calls emit structured compliance audit events in `architect_audit_logs`."""
    db_url = f"sqlite:///{tmp_path / 'audit_test.db'}"
    app = create_mcp_server(tenant_slug="hmc-makerspace", database_url=db_url)

    # 1. Assemble context harness
    await app.call_tool(
        "assemble_context_harness",
        {
            "project_description": "Turn aluminum hub on engine lathe",
            "student_id": "lena@hmc.edu",
            "token_budget": 3000,
        },
    )

    # 2. Save build plan with hazardous step
    save_res = await app.call_tool(
        "save_hardware_build_plan",
        {
            "title": "Lathe Hub",
            "student_id": "lena@hmc.edu",
            "steps": [
                {
                    "step_order": 1,
                    "subsystem": "mechanical",
                    "title": "Turn hub on lathe",
                    "instructions": "Face and bore 6061 aluminum",
                    "machines_used": ["lathe"],
                }
            ],
        },
    )
    pid = save_res.structured_content["project_id"]

    # 3. Request steward signoff
    await app.call_tool(
        "request_steward_safety_signoff",
        {
            "project_id": pid,
            "step_order": 1,
            "student_id": "lena@hmc.edu",
            "ppe_verified": True,
            "setup_summary": "3-jaw chuck, safety glasses on",
        },
    )

    # 4. Attempt unapproved completion -> SAFETY_GATE_BLOCKED
    blocked = await app.call_tool(
        "complete_build_step",
        {"project_id": pid, "step_order": 1, "student_id": "lena@hmc.edu"},
    )
    assert blocked.structured_content["status"] == "BLOCKED_BY_SAFETY_GATE"

    # 5. Steward approves & student completes
    await app.call_tool(
        "approve_steward_safety_signoff",
        {"project_id": pid, "step_order": 1, "steward_id": "steward_dan@hmc.edu"},
    )
    await app.call_tool(
        "complete_build_step",
        {"project_id": pid, "step_order": 1, "student_id": "lena@hmc.edu"},
    )

    # Inspect `architect://audit/recent`
    audit_raw = await app.read_resource("architect://audit/recent")
    events = json.loads(audit_raw[0].content)
    event_types = [e["event_type"] for e in events]

    assert "HARNESS_ASSEMBLED" in event_types
    assert "PLAN_SAVED" in event_types
    assert "SIGNOFF_REQUESTED" in event_types
    assert "SAFETY_GATE_BLOCKED" in event_types
    assert "SIGNOFF_APPROVED" in event_types
    assert "STEP_COMPLETED" in event_types

    blocked_evt = next(e for e in events if e["event_type"] == "SAFETY_GATE_BLOCKED")
    assert blocked_evt["steward_gate_triggered"] is True
    assert blocked_evt["actor_id"] == "lena@hmc.edu"
    assert blocked_evt["project_id"] == pid
