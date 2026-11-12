"""Unit tests for Slice 1: modular context bucket schemas and university tenant loaders."""

from pathlib import Path

import pytest

from architect_mcp.config import get_settings
from architect_mcp.loaders import (
    TenantLoadError,
    list_available_tenants,
    load_tenant_pack,
    parse_inventory_csv,
    parse_safety_markdown,
    validate_tenant_pack,
)
from architect_mcp.models import MachineStatus, SafetySeverity, StockStatus


def test_list_available_tenants_discovers_builtin_packs():
    tenants = list_available_tenants()
    assert "hmc-makerspace" in tenants
    assert "mit-makerworks-demo" in tenants


def test_load_hmc_tenant_pack_and_validate():
    pack = load_tenant_pack("hmc-makerspace")

    assert pack.university_slug == "hmc-makerspace"
    assert pack.campus.university_name == "Harvey Mudd College"
    assert len(pack.campus.areas) == 5

    # Safety bucket checks
    assert len(pack.safety_protocols) >= 4
    pinned = [p for p in pack.safety_protocols if p.always_include]
    assert len(pinned) == 1
    assert pinned[0].id == "hmc-general-shop-safety"

    signoff_sops = [p for p in pack.safety_protocols if p.requires_steward_signoff]
    assert len(signoff_sops) >= 2

    # Certifications & Machines bucket checks
    cert_ids = {c.id for c in pack.certifications}
    assert {"general-certification", "laser-cutting-cert", "machine-shop-green"} <= cert_ids

    machines_by_id = {m.id: m for m in pack.machines}
    assert machines_by_id["clausing-lathe"].requires_steward_signoff is True
    assert machines_by_id["brother-embroidery"].status == MachineStatus.RESERVED
    assert machines_by_id["juki-leather-sewing"].status == MachineStatus.OPERATIONAL

    # Inventory bucket checks (make.hmc.edu CSV normalization)
    inv_by_name = {i.name: i for i in pack.inventory}
    assert inv_by_name["Spare Multimeter Probes"].stock_status == StockStatus.LOW
    assert inv_by_name["Heat Shrinks"].stock_status == StockStatus.HIGH
    assert inv_by_name["30' Extension Cord"].stock_status == StockStatus.OUT_OF_STOCK
    assert inv_by_name["Calipers"].quantity_numeric == 3
    assert inv_by_name["Calipers"].quantity_total == 8
    assert inv_by_name["Sandpaper"].location_display == "Main Area & Cage -> Cabinet 11 -> Top"

    report = validate_tenant_pack(pack)
    assert report.is_valid is True
    assert report.errors == []
    assert report.bucket_counts["safety"] >= 4
    assert report.bucket_counts["inventory"] >= 20


def test_parse_safety_markdown_frontmatter_and_defaults():
    sop = parse_safety_markdown(
        """---
title: Welding Bay Protocol
severity: steward_signoff_required
required_ppe: Auto-darkening helmet, Leather apron
requires_steward_signoff: true
---
# Welding Safety
Verify fume extractor is running before striking an arc.
""",
        source_name="welding_bay.md",
    )
    assert sop.id == "welding-bay"
    assert sop.title == "Welding Bay Protocol"
    assert sop.severity == SafetySeverity.STEWARD_SIGNOFF_REQUIRED
    assert sop.required_ppe == ["Auto-darkening helmet", "Leather apron"]
    assert sop.requires_steward_signoff is True
    assert "fume extractor" in sop.content


def test_parse_inventory_csv_collects_bad_rows_without_failing_valid_rows():
    csv_payload = """Type,Name,Quantity,Locations,Required Certs,Required Roles
Tool,Digital Calipers,3/8,Main Area & Cage > 1c,,User
Tool,Broken Row,not-a-number,Somewhere,,User
Material,,High,Bin 1,,User
Material,Cast Acrylic 3mm,High,Rapid Bay > Rack A,laser-cutting-cert,User
"""
    items, errors = parse_inventory_csv(csv_payload, source_name="sample.csv")
    assert len(items) == 2
    assert len(errors) == 2
    assert items[0].name == "Digital Calipers"
    assert items[1].required_certs == ["laser-cutting-cert"]


def test_validate_tenant_pack_flags_unknown_certification_reference(tmp_path: Path):
    pack_dir = tmp_path / "broken-uni"
    (pack_dir / "safety").mkdir(parents=True)
    (pack_dir / "machines").mkdir(parents=True)

    (pack_dir / "campus.yaml").write_text(
        "university_slug: broken-uni\nuniversity_name: Test U\nmakerspace_name: Test Lab\n",
        encoding="utf-8",
    )
    (pack_dir / "safety" / "general.md").write_text(
        "---\nalways_include: true\n---\n# General Rules\nWear safety glasses.\n",
        encoding="utf-8",
    )
    (pack_dir / "machines" / "machines.yaml").write_text(
        "machines:\n  - id: mill-1\n    name: Knee Mill\n    area: metal-lab\n    required_cert: missing-cert-id\n",
        encoding="utf-8",
    )

    pack = load_tenant_pack("broken-uni", tenant_packs_dir=tmp_path)
    report = validate_tenant_pack(pack)
    assert report.is_valid is False
    assert any("missing-cert-id" in err for err in report.errors)


def test_missing_tenant_raises_clear_error(tmp_path: Path):
    with pytest.raises(TenantLoadError, match="does not exist"):
        load_tenant_pack("nonexistent-university", tenant_packs_dir=tmp_path)


def test_config_reads_tenant_from_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("ARCHITECT_TENANT", "mit-makerworks-demo")
    monkeypatch.setenv("ARCHITECT_TENANT_DIR", str(tmp_path))
    monkeypatch.setenv("ARCHITECT_TOKEN_BUDGET", "6000")

    settings = get_settings()
    assert settings.tenant_slug == "mit-makerworks-demo"
    assert settings.tenant_packs_dir == tmp_path.resolve()
    assert settings.default_token_budget == 6000
