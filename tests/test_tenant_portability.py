"""Portability tests proving any university can deploy Architect-MCP via drop-in files."""

from pathlib import Path

import pytest

from architect_mcp.loaders import load_tenant_pack, validate_tenant_pack
from architect_mcp.models import MachineStatus, StockStatus
from architect_mcp.server import create_mcp_server


def test_hot_swap_between_hmc_and_mit_tenants():
    hmc = load_tenant_pack("hmc-makerspace")
    mit = load_tenant_pack("mit-makerworks-demo")

    assert hmc.campus.university_name != mit.campus.university_name
    assert validate_tenant_pack(hmc).is_valid is True
    assert validate_tenant_pack(mit).is_valid is True

    # Verify different CSV schemas both normalized to identical InventoryItem contracts
    mit_inv = {item.sku: item for item in mit.inventory}
    assert mit_inv["MIT-ALU-5052"].stock_status == StockStatus.HIGH
    assert mit_inv["MIT-ALU-5052"].unit == "sheets"
    assert mit_inv["MIT-VESC-75"].stock_status == StockStatus.LOW

    # Verify machine operational states preserved across tenants
    mit_machines = {m.id: m for m in mit.machines}
    assert mit_machines["haas-mini-mill"].status == MachineStatus.MAINTENANCE
    assert mit_machines["omax-protomax-waterjet"].requires_steward_signoff is True


def test_zero_code_onboarding_for_brand_new_university_pack(tmp_path: Path):
    uni_dir = tmp_path / "caltech-fablab"
    for sub in ("safety", "certifications", "machines", "inventory"):
        (uni_dir / sub).mkdir(parents=True)

    (uni_dir / "campus.yaml").write_text(
        """university_slug: caltech-fablab
university_name: Caltech
makerspace_name: Jim Hall Prototyping Lab
areas:
  - slug: robotics-bay
    name: Robotics Bay
    facility_code: MAKERSPACE
""",
        encoding="utf-8",
    )
    (uni_dir / "safety" / "core.md").write_text(
        """---
always_include: true
severity: warning
required_ppe: [ANSI Z87.1 Glasses]
---
# Core Lab Rules
Eye protection mandatory at all stations.
""",
        encoding="utf-8",
    )
    (uni_dir / "certifications" / "certs.yaml").write_text(
        """certifications:
  - id: bay-orientation
    name: Robotics Bay Orientation
    level: 1
    granting_area: robotics-bay
""",
        encoding="utf-8",
    )
    (uni_dir / "machines" / "machines.yaml").write_text(
        """machines:
  - id: bambu-x1c
    name: Bambu Lab X1-Carbon
    area: robotics-bay
    required_cert: bay-orientation
""",
        encoding="utf-8",
    )
    (uni_dir / "inventory" / "stock.csv").write_text(
        """sku,name,category,facility,location,quantity,unit
CT-CF-PLA,Carbon Fiber PLA Spool,material,robotics-bay,Dry Cabinet A,6,spools
""",
        encoding="utf-8",
    )

    pack = load_tenant_pack("caltech-fablab", tenant_packs_dir=tmp_path)
    report = validate_tenant_pack(pack)

    assert report.is_valid is True
    assert pack.campus.university_name == "Caltech"
    assert len(pack.machines) == 1
    assert pack.inventory[0].sku == "CT-CF-PLA"
    assert pack.inventory[0].stock_status == StockStatus.LIMITED_COUNT



@pytest.mark.anyio
async def test_ucb_jacobs_hall_live_tenant_pack_and_mcp_server(tmp_path: Path):
    """Verify UC Berkeley Jacobs Hall pack loads cleanly and enforces Room 122B safety locks."""
    pack = load_tenant_pack("ucb-jacobs-hall")
    report = validate_tenant_pack(pack)
    assert report.is_valid is True
    assert "Berkeley" in pack.campus.university_name
    assert len(pack.inventory) >= 200

    app = create_mcp_server(
        tenant_slug="ucb-jacobs-hall",
        database_url=f"sqlite:///{tmp_path / 'ucb_test.db'}",
    )

    # Search real Jacobs Material Store items (FabLight stainless plate & plywood)
    inv_res = await app.call_tool(
        "search_makerspace_inventory",
        {"query": "FabLight Stainless Steel", "only_in_stock": True, "limit": 5},
    )
    items = (
        inv_res.structured_content.get("result")
        if isinstance(inv_res.structured_content, dict)
        else inv_res.structured_content
    )
    assert isinstance(items, list)
    assert len(items) > 0
    assert any("Stainless Steel" in item["name"] for item in items)

    # Save a build plan touching FabLight/OMAX/ShopBot -> must lock behind Steward sign-off
    save_res = await app.call_tool(
        "save_hardware_build_plan",
        {
            "title": "CalSat Structural Bracket",
            "student_id": "oski@berkeley.edu",
            "steps": [
                {
                    "step_order": 1,
                    "subsystem": "mechanical",
                    "title": "Cut aluminum plate on OMAX 2626 waterjet",
                    "instructions": "Clamp 6061 plate onto OMAX brick bed and run CAM path",
                    "machines_used": ["omax-2626-waterjet"],
                },
                {
                    "step_order": 2,
                    "subsystem": "fabrication",
                    "title": "Cut plywood housing on Universal Laser",
                    "instructions": "Laser cut 1/4 inch plywood panels in Room 110",
                    "machines_used": ["uls-ils1275-laser"],
                },
            ],
        },
    )
    saved = save_res.structured_content
    assert saved["status"] == "SAVED"
    assert 1 in saved["safety_gated_steps"]

    blocked = await app.call_tool(
        "complete_build_step",
        {"project_id": saved["project_id"], "step_order": 1, "student_id": "oski@berkeley.edu"},
    )
    assert blocked.structured_content["status"] == "BLOCKED_BY_SAFETY_GATE"
