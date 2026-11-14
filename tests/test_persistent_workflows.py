"""Slice 4 tests: Durable SQLite/PostgreSQL workflow persistence & Campus Web App Bridge."""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from architect_mcp.db import WebAppBridge, WorkflowRepository
from architect_mcp.server import create_mcp_server


def _init_companion_webapp_schema(db_path: Path) -> str:
    """Create a minimal companion campus web portal database schema for bridge testing."""
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(120) NOT NULL,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    role VARCHAR(40) NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id INTEGER NOT NULL REFERENCES users(id),
                    title VARCHAR(200) NOT NULL,
                    description TEXT NOT NULL,
                    status VARCHAR(40) NOT NULL,
                    cad_reference_url VARCHAR(500),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE project_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL REFERENCES projects(id),
                    step_order INTEGER NOT NULL,
                    subsystem VARCHAR(40) NOT NULL,
                    title VARCHAR(200) NOT NULL,
                    instructions TEXT NOT NULL,
                    facility VARCHAR(64),
                    safety_level VARCHAR(64) NOT NULL,
                    requires_steward_signoff BOOLEAN NOT NULL,
                    safety_notes TEXT,
                    is_completed BOOLEAN NOT NULL,
                    safety_approved_by INTEGER REFERENCES users(id),
                    safety_approved_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE project_bom_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL REFERENCES projects(id),
                    part_name VARCHAR(200) NOT NULL,
                    quantity_needed INTEGER NOT NULL,
                    source_status VARCHAR(64) NOT NULL,
                    inventory_item_id INTEGER,
                    external_purchase_note TEXT
                )
                """
            )
        )
    return db_url


@pytest.mark.anyio
async def test_build_plan_and_safety_gates_persist_across_server_restarts(tmp_path: Path):
    """Verify saved build plans, signoff requests, and gate locks survive server restarts."""
    db_url = f"sqlite:///{tmp_path / 'architect_persist.db'}"

    # Instance 1: Create plan and request steward sign-off
    server1 = create_mcp_server(tenant_slug="hmc-makerspace", database_url=db_url)
    save_res = await server1.call_tool(
        "save_hardware_build_plan",
        {
            "title": "CNC Motor Mount",
            "student_id": "maya@hmc.edu",
            "steps": [
                {
                    "step_order": 1,
                    "subsystem": "mechanical",
                    "title": "Machine mounting plate on Bridgeport mill",
                    "instructions": "Clamp 6061 plate in Kurt vise and mill slots",
                    "machines_used": ["bridgeport"],
                },
                {
                    "step_order": 2,
                    "subsystem": "firmware",
                    "title": "Flash firmware onto microcontroller",
                    "instructions": "Upload Arduino sketch over USB-C cable",
                    "machines_used": [],
                },
            ],
            "bom_items": [
                {"part_name": "Wire", "quantity_needed": 2},
                {"part_name": "Custom Titanium Shaft", "quantity_needed": 1},
            ],
        },
    )
    pid = save_res.structured_content["project_id"]
    assert pid is not None
    assert 1 in save_res.structured_content["safety_gated_steps"]

    req_res = await server1.call_tool(
        "request_steward_safety_signoff",
        {
            "project_id": pid,
            "step_order": 1,
            "student_id": "maya@hmc.edu",
            "ppe_verified": True,
            "setup_summary": "Vise indicated to 0.001 in, safety glasses on",
        },
    )
    assert req_res.structured_content["status"] == "PENDING_STEWARD_REVIEW"

    # Instance 2: Fresh server instance pointing at the same SQLite DB
    server2 = create_mcp_server(tenant_slug="hmc-makerspace", database_url=db_url)

    # Project resource & steward queue resource must reflect persisted state
    proj_raw = await server2.read_resource(f"architect://projects/{pid}")
    proj_data = json.loads(proj_raw[0].content)
    assert proj_data["title"] == "CNC Motor Mount"
    assert len(proj_data["steps"]) == 2
    assert len(proj_data["bom_items"]) == 2
    assert "Vise indicated" in proj_data["steps"][0]["safety_notes"]

    queue_raw = await server2.read_resource("architect://stewards/queue")
    queue_data = json.loads(queue_raw[0].content)
    assert len(queue_data) == 1
    assert queue_data[0]["project_id"] == pid
    assert queue_data[0]["signoff_status"] == "PENDING_STEWARD_REVIEW"

    # Safety lock must still block unapproved completion on the restarted server
    blocked = await server2.call_tool(
        "complete_build_step",
        {"project_id": pid, "step_order": 1, "student_id": "maya@hmc.edu"},
    )
    assert blocked.structured_content["status"] == "BLOCKED_BY_SAFETY_GATE"

    # Steward approves step 1
    approved = await server2.call_tool(
        "approve_steward_safety_signoff",
        {
            "project_id": pid,
            "step_order": 1,
            "steward_id": "steward_lee@hmc.edu",
            "notes": "Workholding verified solid.",
        },
    )
    assert approved.structured_content["status"] == "APPROVED"

    # Now student can complete step 1 without passing steward_id again
    done1 = await server2.call_tool(
        "complete_build_step",
        {"project_id": pid, "step_order": 1, "student_id": "maya@hmc.edu"},
    )
    assert done1.structured_content["status"] == "COMPLETED"
    assert done1.structured_content["approved_by"] == "steward_lee@hmc.edu"
    assert done1.structured_content["all_steps_completed"] is False

    # Complete step 2 -> project complete
    done2 = await server2.call_tool(
        "complete_build_step",
        {"project_id": pid, "step_order": 2, "student_id": "maya@hmc.edu"},
    )
    assert done2.structured_content["all_steps_completed"] is True


@pytest.mark.anyio
async def test_companion_webapp_bridge_syncs_projects_steps_and_steward_approvals(
    tmp_path: Path,
):
    """Verify Architect-MCP mirrors build plans & steward approvals into companion web DB."""
    arch_db = f"sqlite:///{tmp_path / 'architect.db'}"
    webapp_db = _init_companion_webapp_schema(tmp_path / "wally_webapp.db")

    server = create_mcp_server(
        tenant_slug="hmc-makerspace",
        database_url=arch_db,
        webapp_database_url=webapp_db,
    )

    save_res = await server.call_tool(
        "save_hardware_build_plan",
        {
            "title": "Autonomous Rover Chassis",
            "student_id": "alex@hmc.edu",
            "steps": [
                {
                    "step_order": 1,
                    "subsystem": "mechanical",
                    "title": "Turn axle on engine lathe",
                    "instructions": "Face and turn 10mm steel shaft",
                    "machines_used": ["lathe"],
                }
            ],
            "bom_items": [
                {"part_name": "Heat Shrinks", "quantity_needed": 5},
                {"part_name": "M4 Titanium Bolt", "quantity_needed": 8},
            ],
        },
    )
    pid = save_res.structured_content["project_id"]

    await server.call_tool(
        "approve_steward_safety_signoff",
        {
            "project_id": pid,
            "step_order": 1,
            "steward_id": "steward_sam@hmc.edu",
            "notes": "Chuck key removed, RPM verified.",
        },
    )

    # Inspect companion web database directly
    web_engine = create_engine(webapp_db, connect_args={"check_same_thread": False})
    with web_engine.connect() as conn:
        proj_row = conn.execute(text("SELECT id, title, status FROM projects")).fetchone()
        assert proj_row is not None
        assert proj_row[1] == "Autonomous Rover Chassis"

        step_row = conn.execute(
            text(
                "SELECT title, safety_level, requires_steward_signoff, safety_approved_by "
                "FROM project_steps WHERE project_id = :pid"
            ),
            {"pid": proj_row[0]},
        ).fetchone()
        assert step_row is not None
        assert step_row[0] == "Turn axle on engine lathe"
        assert step_row[1] == "steward_sign_off_required"
        assert bool(step_row[2]) is True
        assert step_row[3] is not None

        bom_rows = conn.execute(
            text(
                "SELECT part_name, source_status FROM project_bom_items WHERE project_id = :pid"
            ),
            {"pid": proj_row[0]},
        ).fetchall()
        status_by_part = {r[0]: r[1] for r in bom_rows}
        assert status_by_part["Heat Shrinks"] == "in_stock_on_campus"
        assert status_by_part["M4 Titanium Bolt"] == "external_purchase_needed"


def test_webapp_bridge_noop_when_schema_missing(tmp_path: Path):
    """Verify WebAppBridge safely no-ops if pointed at an uninitialized database."""
    empty_db = f"sqlite:///{tmp_path / 'empty.db'}"
    bridge = WebAppBridge(empty_db)
    assert bridge.has_compatible_schema() is False

    repo = WorkflowRepository(
        database_url=f"sqlite:///{tmp_path / 'arch.db'}",
        webapp_database_url=empty_db,
    )
    assert repo.count_plans() == 0
