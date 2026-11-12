"""Portability tests proving any university can deploy Architect-MCP via drop-in files."""

from pathlib import Path

from architect_mcp.loaders import load_tenant_pack, validate_tenant_pack
from architect_mcp.models import MachineStatus, StockStatus


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
