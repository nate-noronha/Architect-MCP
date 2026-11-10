"""Tests for Slice 2: Deterministic Safety Gate Validator & Forbidden Material Scanner."""

from architect_mcp.harness import assess_text_hazards, validate_build_steps
from architect_mcp.loaders import load_tenant_pack
from architect_mcp.models import StepInput, Subsystem


def test_high_voltage_threshold_triggers_steward_signoff():
    pack = load_tenant_pack("hmc-makerspace")

    safe_24v = assess_text_hazards("Wire 24V brushed DC motor driver bench test", pack)
    assert safe_24v.requires_steward_signoff is False

    hv_60v = assess_text_hazards("Wire 60V DC brushless inverter pack with XT60", pack)
    assert hv_60v.requires_steward_signoff is True
    assert any("60v" in w.lower() for w in hv_60v.warnings)


def test_forbidden_laser_material_detected_deterministically():
    pack = load_tenant_pack("hmc-makerspace")

    pvc_check = assess_text_hazards(
        "Laser cut 3mm PVC baffle plates on the Epilog laser cutter", pack
    )
    assert len(pvc_check.forbidden_material_detected) > 0
    assert any("pvc" in hit.lower() for hit in pvc_check.forbidden_material_detected)

    acrylic_check = assess_text_hazards(
        "Laser cut 3mm cast acrylic baffle plates on the Epilog laser cutter", pack
    )
    assert acrylic_check.forbidden_material_detected == []


def test_validate_build_steps_enforces_steward_gate_on_machining():
    pack = load_tenant_pack("hmc-makerspace")
    steps = [
        StepInput(
            step_order=1,
            subsystem=Subsystem.ASSEMBLY,
            title="Inspect printed enclosure",
            instructions="Check M3 bolt clearance with dial calipers.",
            requires_steward_signoff=False,
        ),
        StepInput(
            step_order=2,
            subsystem=Subsystem.MECHANICAL,
            title="Turn hub on Clausing Lathe",
            instructions="Chuck 6061-T6 bar stock and turn bore on lathe.",
            requires_steward_signoff=False,
        ),
    ]

    validated = validate_build_steps(steps, pack)
    assert validated[0].requires_steward_signoff is False
    assert validated[1].requires_steward_signoff is True
    assert "clausing-lathe" in validated[1].machines_used
    assert validated[1].safety_notes is not None
    assert "PPE:" in validated[1].safety_notes


def test_maintenance_machine_flagged_across_mit_tenant():
    mit_pack = load_tenant_pack("mit-makerworks-demo")
    res = assess_text_hazards("Mill pocket on the Haas Mini Mill CNC", mit_pack)
    assert res.requires_steward_signoff is True
    assert "haas-mini-mill" in res.unavailable_machines
