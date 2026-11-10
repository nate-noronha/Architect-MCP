"""Dynamic Context Router orchestrating intent scoring & token-budgeted bucket assembly."""

from typing import Optional

from architect_mcp.harness.bucket_manager import BucketManager, tokenize
from architect_mcp.harness.safety_validator import assess_text_hazards
from architect_mcp.harness.token_budget import AssembledContextPayload, pack_context_buckets
from architect_mcp.models import (
    CertificationTier,
    MachineSpec,
    SafetyProtocol,
    Subsystem,
)

# Universal engineering vocabulary mapping natural language prompts -> subsystems.
_SUBSYSTEM_KEYWORDS: dict[Subsystem, tuple[str, ...]] = {
    Subsystem.MECHANICAL: (
        "lathe",
        "mill",
        "cnc",
        "waterjet",
        "aluminum",
        "steel",
        "brass",
        "shaft",
        "bracket",
        "plate",
        "bearing",
        "bolt",
        "fastener",
        "cad",
        "6061",
        "torque",
        "chassis",
    ),
    Subsystem.FABRICATION: (
        "3d print",
        "prusa",
        "sla",
        "resin",
        "laser",
        "acrylic",
        "plywood",
        "wood",
        "table saw",
        "cut",
        "enclosure",
    ),
    Subsystem.ELECTRICAL: (
        "pcb",
        "solder",
        "voltage",
        "battery",
        "lipo",
        "esc",
        "motor",
        "wire",
        "power",
        "multimeter",
        "connector",
        "xt60",
        "heat shrink",
        "48v",
        "120v",
    ),
    Subsystem.FIRMWARE: (
        "firmware",
        "stm32",
        "arduino",
        "esp32",
        "microcontroller",
        "mcu",
        "code",
        "pwm",
        "spi",
        "i2c",
        "encoder",
        "pid",
    ),
    Subsystem.TEXTILES: (
        "leather",
        "sewing",
        "stitch",
        "fabric",
        "canvas",
        "embroidery",
        "webbing",
        "strap",
    ),
    Subsystem.ASSEMBLY: (
        "assemble",
        "caliper",
        "sandpaper",
        "drill",
        "mount",
        "fasten",
        "tether",
    ),
}


def infer_subsystems(
    project_description: str,
    explicit_subsystems: Optional[list[Subsystem | str]] = None,
) -> list[Subsystem]:
    """Combine explicitly requested subsystems with keywords inferred from the prompt."""
    resolved: list[Subsystem] = []
    for raw in explicit_subsystems or []:
        try:
            sub = raw if isinstance(raw, Subsystem) else Subsystem(raw.strip().lower())
            if sub not in resolved:
                resolved.append(sub)
        except ValueError:
            continue

    desc_lower = (project_description or "").lower()
    for subsystem, keywords in _SUBSYSTEM_KEYWORDS.items():
        if any(kw in desc_lower for kw in keywords) and subsystem not in resolved:
            resolved.append(subsystem)

    return resolved or [Subsystem.GENERAL]


class ContextRouter:
    """Routes a natural-language hardware goal across the 5 modular context buckets."""

    def __init__(self, bucket_manager: BucketManager) -> None:
        self.manager = bucket_manager

    def _score_safety(
        self,
        proto: SafetyProtocol,
        query_tokens: set[str],
        inferred_subs: set[Subsystem],
        matched_machine_ids: set[str],
        triggered_safety_ids: set[str],
    ) -> int:
        if proto.always_include:
            return 0  # Handled separately as pinned safety rules

        score = 0
        if proto.id in triggered_safety_ids:
            score += 50
        if set(proto.applies_to_machines) & matched_machine_ids:
            score += 30
        if set(proto.applies_to_subsystems) & inferred_subs:
            score += 10

        kw_tokens = tokenize(" ".join([proto.title, *proto.trigger_keywords, *proto.forbidden_materials]))
        score += len(query_tokens & kw_tokens) * 8
        return score

    def _score_machines(
        self,
        query_tokens: set[str],
        inferred_subs: set[Subsystem],
        explicit_machine_ids: set[str],
    ) -> list[MachineSpec]:
        scored: list[tuple[int, MachineSpec]] = []
        for machine in self.manager.pack.machines:
            score = 0
            if machine.id in explicit_machine_ids:
                score += 40
            if set(machine.subsystems) & inferred_subs:
                score += 10

            searchable = " ".join(
                [
                    machine.id.replace("-", " "),
                    machine.name,
                    machine.description,
                    " ".join(machine.tags),
                    " ".join(machine.allowed_materials),
                ]
            )
            overlap = len(query_tokens & tokenize(searchable))
            score += overlap * 12

            if score > 0:
                scored.append((score, machine))

        scored.sort(key=lambda pair: (-pair[0], pair[1].name))
        return [m for _, m in scored]

    def assemble(
        self,
        project_description: str,
        subsystems: Optional[list[Subsystem | str]] = None,
        student_id: Optional[str] = None,
        student_certs: Optional[list[str]] = None,
        token_budget: Optional[int] = None,
    ) -> AssembledContextPayload:
        """Select, cross-link, and pack the optimal multi-bucket context for a student prompt."""
        budget = token_budget or self.manager.pack.campus.default_token_budget
        inferred_subs = infer_subsystems(project_description, subsystems)
        sub_set = set(inferred_subs)
        query_tokens = tokenize(project_description)

        # 1. Deterministic hazard scan
        hazards = assess_text_hazards(project_description, self.manager.pack)
        explicit_machine_ids = set(hazards.matched_machines)
        triggered_safety_ids = set(hazards.triggered_safety_ids)

        # 2. Match machines & expand safety matching
        matched_machines = self._score_machines(
            query_tokens, sub_set, explicit_machine_ids
        )
        matched_machine_ids = {m.id for m in matched_machines} | explicit_machine_ids

        # 3. Partition safety protocols into pinned vs matched non-pinned
        pinned_safety = self.manager.get_pinned_safety_protocols()
        matched_safety_scored: list[tuple[int, SafetyProtocol]] = []
        for proto in self.manager.list_safety_protocols():
            if proto.always_include:
                continue
            score = self._score_safety(
                proto,
                query_tokens,
                sub_set,
                matched_machine_ids,
                triggered_safety_ids,
            )
            if score > 0:
                matched_safety_scored.append((score, proto))
        matched_safety_scored.sort(key=lambda pair: (-pair[0], -pair[1].priority))
        matched_safety = [p for _, p in matched_safety_scored]

        # 4. Resolve required certifications & prerequisites for matched equipment
        cert_map: dict[str, CertificationTier] = {}
        needed_cert_ids: list[str] = []
        for machine in matched_machines:
            if machine.required_cert:
                needed_cert_ids.append(machine.required_cert)
                for tier in self.manager.resolve_certification_chain(machine.required_cert):
                    cert_map[tier.id] = tier

        if not cert_map and self.manager.pack.certifications:
            # Always include baseline level-1 campus cert when relevant machines weren't explicitly named
            for cert in self.manager.pack.certifications:
                if cert.level == 1:
                    cert_map[cert.id] = cert

        held = {c.strip().lower() for c in (student_certs or []) if c.strip()}
        missing_certs: list[str] = []
        if student_certs is not None:
            for cid in dict.fromkeys(needed_cert_ids):
                tier = cert_map.get(cid)
                if cid.lower() not in held and (not tier or tier.name.lower() not in held):
                    missing_certs.append(cid)

        # 5. Match stockroom inventory items
        matched_inventory = self.manager.search_inventory(
            query=project_description, only_in_stock=False, limit=20
        )

        # 6. Fetch active workflows if student_id supplied
        workflows = self.manager.list_workflows(student_id=student_id) if student_id else []

        signoff_likely = hazards.requires_steward_signoff or any(
            m.requires_steward_signoff for m in matched_machines[:3]
        )

        return pack_context_buckets(
            university_slug=self.manager.university_slug,
            university_name=self.manager.pack.campus.university_name,
            makerspace_name=self.manager.pack.campus.makerspace_name,
            project_description=project_description,
            inferred_subsystems=inferred_subs,
            token_budget=budget,
            pinned_safety=pinned_safety,
            matched_safety=matched_safety,
            certifications=list(cert_map.values()),
            missing_certifications=missing_certs,
            machines=matched_machines,
            inventory=matched_inventory,
            workflows=workflows,
            hazard_warnings=hazards.warnings,
            steward_signoff_likely_required=signoff_likely,
        )
