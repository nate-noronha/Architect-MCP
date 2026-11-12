"""Deterministic token estimation and safety-first context window budget packer."""

import math
from typing import Callable, TypeVar

from pydantic import BaseModel, Field

from architect_mcp.models import (
    BucketKind,
    BuildPlanSpec,
    CertificationTier,
    InventoryItem,
    MachineSpec,
    SafetyProtocol,
    StockStatus,
    Subsystem,
)

T = TypeVar("T")

_SUBSYSTEM_TAG_WEIGHTS: dict[str, str] = {
    StockStatus.HIGH.value: "IN STOCK (High)",
    StockStatus.MEDIUM.value: "IN STOCK (Medium)",
    StockStatus.LOW.value: "LOW STOCK",
    StockStatus.OUT_OF_STOCK.value: "OUT OF STOCK",
}


def estimate_tokens(text: str) -> int:
    """Fast deterministic token estimator (~4 chars/token) with minimum non-empty cost."""
    cleaned = (text or "").strip()
    if not cleaned:
        return 0
    return max(1, math.ceil(len(cleaned) / 4))


class BucketPackingStats(BaseModel):
    """Per-bucket accounting showing kept items, dropped counts, and token usage."""

    bucket: BucketKind
    items_included: int = 0
    items_dropped: int = 0
    tokens_used: int = 0
    pinned: bool = False


class AssembledContextPayload(BaseModel):
    """Structured, citation-tagged context output served by the Dynamic Context Harness."""

    university_slug: str
    university_name: str
    makerspace_name: str
    project_description: str
    inferred_subsystems: list[Subsystem] = Field(default_factory=list)
    token_budget: int
    total_tokens_used: int
    safety_pinned_tokens: int
    budget_stats: list[BucketPackingStats] = Field(default_factory=list)

    # Modular bucket slices
    safety_protocols: list[SafetyProtocol] = Field(default_factory=list)
    certifications: list[CertificationTier] = Field(default_factory=list)
    missing_certifications: list[str] = Field(default_factory=list)
    machines: list[MachineSpec] = Field(default_factory=list)
    inventory: list[InventoryItem] = Field(default_factory=list)
    workflows: list[BuildPlanSpec] = Field(default_factory=list)
    hazard_warnings: list[str] = Field(default_factory=list)
    steward_signoff_likely_required: bool = False

    # Ready-to-inject formatted prompt block
    rendered_context: str = ""


def format_safety_item(proto: SafetyProtocol) -> str:
    """Render one safety protocol into compact, LLM-readable Markdown."""
    gate_tag = " [STEWARD SIGN-OFF REQUIRED]" if proto.requires_steward_signoff else ""
    pin_tag = " [PINNED]" if proto.always_include else ""
    ppe_line = f"Required PPE: {', '.join(proto.required_ppe)}" if proto.required_ppe else ""
    forbidden_line = (
        f"FORBIDDEN MATERIALS: {', '.join(proto.forbidden_materials)}"
        if proto.forbidden_materials
        else ""
    )
    parts = [
        f"### [{proto.id}] {proto.title} ({proto.severity.value}){pin_tag}{gate_tag}",
        ppe_line,
        forbidden_line,
        proto.content.strip(),
        f"_Citation: bucket://safety/{proto.source_file or proto.id}_",
    ]
    return "\n".join(line for line in parts if line)


def format_certification_item(cert: CertificationTier) -> str:
    prereqs = ", ".join(cert.prerequisites) if cert.prerequisites else "None"
    machines = ", ".join(cert.unlocks_machines) if cert.unlocks_machines else "General area"
    return (
        f"- **[{cert.id}] {cert.name}** (Level {cert.level}): {cert.description} "
        f"| Prereqs: {prereqs} | Unlocks: {machines} "
        f"(_bucket://certifications/{cert.id}_)"
    )


def format_machine_item(machine: MachineSpec) -> str:
    gate = " | STEWARD SIGN-OFF MANDATORY" if machine.requires_steward_signoff else ""
    cert = machine.required_cert or "None"
    forbidden = (
        f" | FORBIDDEN: {', '.join(machine.forbidden_materials)}"
        if machine.forbidden_materials
        else ""
    )
    return (
        f"- **[{machine.id}] {machine.name}** ({machine.status.value}, {machine.units_available}/{machine.units_total} avail) "
        f"| Area: `{machine.area}` | Cert: `{cert}`{gate} "
        f"| Envelope: {machine.work_envelope or 'N/A'}{forbidden} "
        f"(_bucket://machines/{machine.id}_)"
    )


def format_inventory_item(item: InventoryItem) -> str:
    stock_label = _SUBSYSTEM_TAG_WEIGHTS.get(
        item.stock_status.value, f"{item.stock_status.value} ({item.quantity_raw})"
    )
    return (
        f"- **[{item.sku}] {item.name}** — {stock_label} (`{item.quantity_raw}` {item.unit}) "
        f"| Location: `{item.location_display}` (_bucket://inventory/{item.sku}_)"
    )


def format_workflow_item(plan: BuildPlanSpec) -> str:
    done_count = sum(1 for step in plan.steps if step.is_completed)
    return (
        f"- **[project:{plan.project_id}] {plan.title}** — "
        f"{done_count}/{len(plan.steps)} steps complete (_bucket://workflows/{plan.project_id}_)"
    )


def _pack_bucket(
    items: list[T],
    formatter: Callable[[T], str],
    remaining_budget: int,
    allow_overflow: bool = False,
) -> tuple[list[T], list[str], int, int]:
    """Greedily pack ranked bucket items inside `remaining_budget` tokens."""
    kept: list[T] = []
    rendered_blocks: list[str] = []
    tokens_used = 0
    dropped = 0

    for item in items:
        block = formatter(item)
        cost = estimate_tokens(block)
        if allow_overflow or (tokens_used + cost <= remaining_budget):
            kept.append(item)
            rendered_blocks.append(block)
            tokens_used += cost
        else:
            dropped += 1

    return kept, rendered_blocks, tokens_used, dropped


def pack_context_buckets(
    *,
    university_slug: str,
    university_name: str,
    makerspace_name: str,
    project_description: str,
    inferred_subsystems: list[Subsystem],
    token_budget: int,
    pinned_safety: list[SafetyProtocol],
    matched_safety: list[SafetyProtocol],
    certifications: list[CertificationTier],
    missing_certifications: list[str],
    machines: list[MachineSpec],
    inventory: list[InventoryItem],
    workflows: list[BuildPlanSpec],
    hazard_warnings: list[str],
    steward_signoff_likely_required: bool,
) -> AssembledContextPayload:
    """Assemble and pack modular context buckets with deterministic safety-first priority.

    Priority order:
      1. Pinned Safety (`always_include=True` — NEVER truncated)
      2. Triggered Safety Protocols (`matched_safety`)
      3. Certifications & Prerequisites
      4. Operational / Matched Machines
      5. Active Project Workflows
      6. Stockroom Inventory Items (strictly bounded by remaining budget)
    """
    header_block = (
        f"# ARCHITECT CONTEXT HARNESS — {university_name} ({makerspace_name})\n"
        f"Tenant: `{university_slug}` | Subsystems: `{', '.join(s.value for s in inferred_subsystems) or 'general'}`\n"
    )
    if hazard_warnings:
        header_block += (
            "## DETERMINISTIC HAZARD ALERTS\n"
            + "\n".join(f"- ⚠️ {w}" for w in hazard_warnings)
            + "\n"
        )
    if missing_certifications:
        header_block += (
            "## MISSING STUDENT CERTIFICATIONS\n"
            + f"- Student currently lacks required badge(s): `{', '.join(missing_certifications)}`\n"
        )

    header_tokens = estimate_tokens(header_block)
    remaining = max(0, token_budget - header_tokens)

    # 1. Pinned Safety (NEVER dropped, even if tight budget)
    kept_pinned, pinned_blocks, pinned_tokens, _ = _pack_bucket(
        pinned_safety, format_safety_item, remaining, allow_overflow=True
    )
    remaining = max(0, remaining - pinned_tokens)

    # 2. Triggered Safety SOPs (high priority; allow overflow for mandatory steward gates)
    signoff_safety = [p for p in matched_safety if p.requires_steward_signoff or p.forbidden_materials]
    other_safety = [p for p in matched_safety if p not in signoff_safety]

    kept_gate_safety, gate_blocks, gate_tokens, _ = _pack_bucket(
        signoff_safety, format_safety_item, remaining, allow_overflow=True
    )
    remaining = max(0, remaining - gate_tokens)

    kept_other_safety, other_safety_blocks, other_safety_tokens, dropped_safety = _pack_bucket(
        other_safety, format_safety_item, remaining, allow_overflow=False
    )
    remaining = max(0, remaining - other_safety_tokens)

    all_kept_safety = kept_pinned + kept_gate_safety + kept_other_safety
    all_safety_blocks = pinned_blocks + gate_blocks + other_safety_blocks
    safety_total_tokens = pinned_tokens + gate_tokens + other_safety_tokens

    # 3. Certifications
    kept_certs, cert_blocks, cert_tokens, dropped_certs = _pack_bucket(
        certifications, format_certification_item, remaining
    )
    remaining = max(0, remaining - cert_tokens)

    # 4. Machines
    kept_machines, machine_blocks, machine_tokens, dropped_machines = _pack_bucket(
        machines, format_machine_item, remaining
    )
    remaining = max(0, remaining - machine_tokens)

    # 5. Workflows
    kept_workflows, workflow_blocks, workflow_tokens, dropped_workflows = _pack_bucket(
        workflows, format_workflow_item, remaining
    )
    remaining = max(0, remaining - workflow_tokens)

    # 6. Inventory (remaining budget)
    kept_inventory, inv_blocks, inv_tokens, dropped_inventory = _pack_bucket(
        inventory, format_inventory_item, remaining
    )

    sections = [header_block]
    if all_safety_blocks:
        sections.append("## BUCKET: bucket://safety\n" + "\n\n".join(all_safety_blocks))
    if cert_blocks:
        sections.append("## BUCKET: bucket://certifications\n" + "\n".join(cert_blocks))
    if machine_blocks:
        sections.append("## BUCKET: bucket://machines\n" + "\n".join(machine_blocks))
    if workflow_blocks:
        sections.append("## BUCKET: bucket://workflows\n" + "\n".join(workflow_blocks))
    if inv_blocks:
        sections.append("## BUCKET: bucket://inventory\n" + "\n".join(inv_blocks))

    rendered_context = "\n\n".join(sections).strip()
    total_tokens = (
        header_tokens
        + safety_total_tokens
        + cert_tokens
        + machine_tokens
        + workflow_tokens
        + inv_tokens
    )

    stats = [
        BucketPackingStats(
            bucket=BucketKind.SAFETY,
            items_included=len(all_kept_safety),
            items_dropped=dropped_safety,
            tokens_used=safety_total_tokens,
            pinned=True,
        ),
        BucketPackingStats(
            bucket=BucketKind.CERTIFICATIONS,
            items_included=len(kept_certs),
            items_dropped=dropped_certs,
            tokens_used=cert_tokens,
        ),
        BucketPackingStats(
            bucket=BucketKind.MACHINES,
            items_included=len(kept_machines),
            items_dropped=dropped_machines,
            tokens_used=machine_tokens,
        ),
        BucketPackingStats(
            bucket=BucketKind.WORKFLOWS,
            items_included=len(kept_workflows),
            items_dropped=dropped_workflows,
            tokens_used=workflow_tokens,
        ),
        BucketPackingStats(
            bucket=BucketKind.INVENTORY,
            items_included=len(kept_inventory),
            items_dropped=dropped_inventory,
            tokens_used=inv_tokens,
        ),
    ]

    return AssembledContextPayload(
        university_slug=university_slug,
        university_name=university_name,
        makerspace_name=makerspace_name,
        project_description=project_description,
        inferred_subsystems=inferred_subsystems,
        token_budget=token_budget,
        total_tokens_used=total_tokens,
        safety_pinned_tokens=pinned_tokens + gate_tokens,
        budget_stats=stats,
        safety_protocols=all_kept_safety,
        certifications=kept_certs,
        missing_certifications=missing_certifications,
        machines=kept_machines,
        inventory=kept_inventory,
        workflows=kept_workflows,
        hazard_warnings=hazard_warnings,
        steward_signoff_likely_required=steward_signoff_likely_required,
        rendered_context=rendered_context,
    )
