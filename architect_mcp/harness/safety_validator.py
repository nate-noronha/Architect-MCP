"""Deterministic hazard scanner & Steward safety gate validator driven by active TenantPack rules."""

import re

from pydantic import BaseModel, Field

from architect_mcp.models import MachineSpec, MachineStatus, SafetyProtocol, StepInput, TenantPack

_HIGH_VOLTAGE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*v(?:dc|ac|olt)?\b", re.IGNORECASE)

# Baseline universal shop hazards that ALWAYS force Steward review even if a campus omits SOP metadata.
_UNIVERSAL_SIGNOFF_KEYWORDS = (
    "lathe",
    "milling",
    "vertical mill",
    "knee mill",
    "cnc",
    "table saw",
    "cold saw",
    "bandsaw",
    "band saw",
    "waterjet",
    "welder",
    "tig",
    "mig",
    "mains ac",
    "120v",
    "240v",
)


class HazardAssessment(BaseModel):
    """Deterministic hazard analysis result for text prompts or build steps."""

    requires_steward_signoff: bool = False
    forbidden_material_detected: list[str] = Field(default_factory=list)
    matched_machines: list[str] = Field(default_factory=list)
    triggered_safety_ids: list[str] = Field(default_factory=list)
    unavailable_machines: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    required_ppe: list[str] = Field(default_factory=list)


def _contains_term(text_lower: str, term: str) -> bool:
    cleaned = term.strip().lower()
    if not cleaned:
        return False
    if len(cleaned) <= 3:
        return bool(re.search(rf"\b{re.escape(cleaned)}\b", text_lower))
    return cleaned in text_lower


def _check_voltage_hazard(text_lower: str) -> list[str]:
    alerts: list[str] = []
    for match in _HIGH_VOLTAGE_RE.finditer(text_lower):
        try:
            volts = float(match.group(1))
        except ValueError:
            continue
        if volts > 50.0:
            alerts.append(
                f"High-voltage hazard detected ({match.group(0)} > 50V threshold): mandatory Shop Steward wiring inspection."
            )
    return alerts


def assess_text_hazards(text: str, pack: TenantPack) -> HazardAssessment:
    """Scan arbitrary project descriptions or step instructions against campus safety & machines."""
    text_lower = (text or "").lower()
    requires_signoff = False
    forbidden_hits: list[str] = []
    matched_machines: list[str] = []
    triggered_safety_ids: list[str] = []
    unavailable_machines: list[str] = []
    warnings: list[str] = []
    ppe_seen: dict[str, None] = {}

    # 1. Voltage >50V check
    voltage_warnings = _check_voltage_hazard(text_lower)
    if voltage_warnings:
        requires_signoff = True
        warnings.extend(voltage_warnings)

    # 2. Universal heavy machinery keywords
    for kw in _UNIVERSAL_SIGNOFF_KEYWORDS:
        if _contains_term(text_lower, kw):
            requires_signoff = True
            warnings.append(
                f"Hazardous process keyword '{kw}' matched: mandatory Shop Steward safety check-off."
            )

    # 3. Tenant-specific machines (signoff, status, forbidden materials)
    for machine in pack.machines:
        machine_terms = [machine.id.replace("-", " "), machine.name, *machine.tags]
        machine_mentioned = any(_contains_term(text_lower, t) for t in machine_terms)
        if not machine_mentioned:
            continue

        matched_machines.append(machine.id)
        if machine.requires_steward_signoff:
            requires_signoff = True
            warnings.append(
                f"Equipment '{machine.name}' requires mandatory Shop Steward sign-off."
            )

        if machine.status in {MachineStatus.MAINTENANCE, MachineStatus.DOWN}:
            unavailable_machines.append(machine.id)
            warnings.append(
                f"Machine '{machine.name}' is currently '{machine.status.value}' ({machine.units_available}/{machine.units_total} available)."
            )

        for forbidden in machine.forbidden_materials:
            base_mat = forbidden.split("(")[0].strip().lower()
            if _contains_term(text_lower, base_mat):
                forbidden_hits.append(f"{forbidden} (on {machine.name})")
                warnings.append(
                    f"FORBIDDEN MATERIAL '{forbidden}' detected alongside {machine.name}!"
                )

    # 4. Tenant-specific safety SOPs
    for proto in pack.safety_protocols:
        proto_matched = False
        if any(_contains_term(text_lower, kw) for kw in proto.trigger_keywords):
            proto_matched = True
        if any(mid in matched_machines for mid in proto.applies_to_machines):
            proto_matched = True

        for forbidden in proto.forbidden_materials:
            base_mat = forbidden.split("(")[0].strip().lower()
            if _contains_term(text_lower, base_mat):
                proto_matched = True
                label = f"{forbidden} ({proto.id})"
                if label not in forbidden_hits:
                    forbidden_hits.append(label)
                warnings.append(
                    f"FORBIDDEN MATERIAL '{forbidden}' violates protocol [{proto.id}] {proto.title}!"
                )

        if proto_matched:
            triggered_safety_ids.append(proto.id)
            for ppe in proto.required_ppe:
                ppe_seen[ppe] = None
            if proto.requires_steward_signoff:
                requires_signoff = True

    # Deduplicate warnings cleanly preserving order
    unique_warnings = list(dict.fromkeys(warnings))
    return HazardAssessment(
        requires_steward_signoff=requires_signoff,
        forbidden_material_detected=list(dict.fromkeys(forbidden_hits)),
        matched_machines=list(dict.fromkeys(matched_machines)),
        triggered_safety_ids=list(dict.fromkeys(triggered_safety_ids)),
        unavailable_machines=list(dict.fromkeys(unavailable_machines)),
        warnings=unique_warnings,
        required_ppe=list(ppe_seen.keys()),
    )


def validate_build_steps(steps: list[StepInput], pack: TenantPack) -> list[StepInput]:
    """Deterministically enforce Steward safety gates & safety notes across build steps."""
    validated: list[StepInput] = []
    for step in steps:
        combined_text = " ".join(
            [step.title, step.instructions, step.area or "", " ".join(step.machines_used)]
        )
        assessment = assess_text_hazards(combined_text, pack)

        must_signoff = step.requires_steward_signoff or assessment.requires_steward_signoff
        merged_notes_parts: list[str] = []
        if step.safety_notes:
            merged_notes_parts.append(step.safety_notes.strip())
        if assessment.warnings:
            merged_notes_parts.append(" | ".join(assessment.warnings[:3]))
        if assessment.required_ppe:
            merged_notes_parts.append(f"PPE: {', '.join(assessment.required_ppe)}")

        validated.append(
            step.model_copy(
                update={
                    "requires_steward_signoff": must_signoff,
                    "safety_notes": " — ".join(merged_notes_parts) if merged_notes_parts else None,
                    "machines_used": list(
                        dict.fromkeys([*step.machines_used, *assessment.matched_machines])
                    ),
                }
            )
        )
    return validated
