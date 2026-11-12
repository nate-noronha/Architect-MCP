"""Tests for Slice 2: Dynamic Context Harness, BucketManager, and Token Budget Packer."""

from architect_mcp.harness import BucketManager, ContextRouter, infer_subsystems
from architect_mcp.models import BucketKind, BuildPlanSpec, StepInput, Subsystem


def test_infer_subsystems_from_multidisciplinary_prompt():
    subs = infer_subsystems(
        "Build a 48V brushless motor dyno with a machined 6061 aluminum mounting plate and stitched leather tether"
    )
    assert Subsystem.MECHANICAL in subs
    assert Subsystem.ELECTRICAL in subs
    assert Subsystem.TEXTILES in subs


def test_assemble_context_harness_hmc_retrieves_relevant_buckets():
    manager = BucketManager.from_tenant("hmc-makerspace")
    router = ContextRouter(manager)

    payload = router.assemble(
        project_description="Machine a 6061 aluminum bracket on the mill and stitch a leather strap",
        student_certs=["general-certification"],
        token_budget=4000,
    )

    assert payload.university_slug == "hmc-makerspace"
    assert payload.total_tokens_used > 0

    safety_ids = {p.id for p in payload.safety_protocols}
    assert "hmc-general-shop-safety" in safety_ids
    assert "hmc-machine-shop-signoff" in safety_ids

    machine_ids = {m.id for m in payload.machines}
    assert "bridgeport-mill" in machine_ids
    assert "juki-leather-sewing" in machine_ids

    # Prerequisite chain: machine-shop-green requires general-certification
    cert_ids = {c.id for c in payload.certifications}
    assert {"general-certification", "machine-shop-green"} <= cert_ids
    assert "machine-shop-green" in payload.missing_certifications

    # Inventory matches aluminum stock cleanly without dumping unrelated items first
    inv_names = [i.name for i in payload.inventory]
    assert any("Aluminum" in name for name in inv_names)

    assert "## BUCKET: bucket://safety" in payload.rendered_context
    assert "## BUCKET: bucket://machines" in payload.rendered_context
    assert "## BUCKET: bucket://inventory" in payload.rendered_context


def test_token_budget_pins_safety_and_drops_excess_inventory():
    manager = BucketManager.from_tenant("hmc-makerspace")
    router = ContextRouter(manager)

    # Deliberately tight token budget to force lower-priority bucket trimming
    payload = router.assemble(
        project_description="Turn aluminum shaft on clausing lathe using calipers and multimeter",
        token_budget=550,
    )

    safety_ids = {p.id for p in payload.safety_protocols}
    assert "hmc-general-shop-safety" in safety_ids
    assert "hmc-machine-shop-signoff" in safety_ids

    inv_stats = next(s for s in payload.budget_stats if s.bucket == BucketKind.INVENTORY)
    assert inv_stats.items_included + inv_stats.items_dropped > 0
    assert inv_stats.items_dropped > 0


def test_context_harness_switches_seamlessly_to_mit_tenant():
    manager = BucketManager.from_tenant("mit-makerworks-demo")
    router = ContextRouter(manager)

    payload = router.assemble(
        project_description="Cut an aluminum chassis plate on the CNC mill or waterjet",
        student_certs=["tier-1-red-pass"],
        token_budget=4000,
    )

    assert payload.university_slug == "mit-makerworks-demo"
    assert "tier-2-waterjet-cert" in payload.missing_certifications
    assert any(m.id == "omax-protomax-waterjet" for m in payload.machines)
    assert any("MIT-ALU-5052" == i.sku for i in payload.inventory)
    assert any("maintenance" in w for w in payload.hazard_warnings)


def test_workflows_bucket_included_for_student_projects():
    manager = BucketManager.from_tenant("hmc-makerspace")
    manager.register_workflow(
        BuildPlanSpec(
            project_id=101,
            university_slug="hmc-makerspace",
            student_id="alice@hmc.edu",
            title="Solar Gimbal Frame",
            steps=[
                StepInput(
                    step_order=1,
                    subsystem=Subsystem.MECHANICAL,
                    title="Cut stock",
                    instructions="Deburr cut edges with file.",
                )
            ],
        )
    )
    router = ContextRouter(manager)
    payload = router.assemble(
        project_description="Continue gimbal aluminum assembly",
        student_id="alice@hmc.edu",
        token_budget=4000,
    )
    assert len(payload.workflows) == 1
    assert payload.workflows[0].title == "Solar Gimbal Frame"
    assert "Solar Gimbal Frame" in payload.rendered_context
