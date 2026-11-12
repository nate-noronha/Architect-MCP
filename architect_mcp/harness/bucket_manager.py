"""Modular Context Bucket registry and indexed lookup across all 5 university buckets."""

import re
from pathlib import Path
from typing import Optional

from architect_mcp.loaders import load_tenant_pack
from architect_mcp.models import (
    BucketKind,
    BuildPlanSpec,
    CertificationTier,
    InventoryItem,
    MachineSpec,
    SafetyProtocol,
    StockStatus,
    Subsystem,
    TenantPack,
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "want",
        "with",
        "using",
        "build",
        "make",
        "project",
        "need",
    }
)


def tokenize(text: str) -> set[str]:
    """Extract normalized alphanumeric search tokens, filtering trivial English stopwords."""
    return {
        tok
        for tok in _TOKEN_RE.findall((text or "").lower())
        if len(tok) > 1 and tok not in _STOPWORDS
    }


class BucketManager:
    """Indexes and serves the 5 modular context buckets for a university tenant pack."""

    def __init__(self, tenant_pack: TenantPack) -> None:
        self.pack = tenant_pack
        self._workflows: dict[int, BuildPlanSpec] = {}
        self._certs_by_id: dict[str, CertificationTier] = {
            c.id: c for c in tenant_pack.certifications
        }
        self._machines_by_id: dict[str, MachineSpec] = {
            m.id: m for m in tenant_pack.machines
        }
        self._safety_by_id: dict[str, SafetyProtocol] = {
            s.id: s for s in tenant_pack.safety_protocols
        }

    @classmethod
    def from_tenant(
        cls,
        tenant_slug: Optional[str] = None,
        tenant_packs_dir: Optional[Path] = None,
    ) -> "BucketManager":
        """Load a university tenant pack from disk and initialize its bucket index."""
        pack = load_tenant_pack(
            tenant_slug=tenant_slug, tenant_packs_dir=tenant_packs_dir
        )
        return cls(pack)

    @property
    def university_slug(self) -> str:
        return self.pack.university_slug

    def bucket_summary(self) -> dict[str, int]:
        """Return item counts across all 5 buckets including active workflows."""
        counts = self.pack.bucket_counts()
        counts[BucketKind.WORKFLOWS.value] = len(self._workflows)
        return counts

    # ------------------------------------------------------------------
    # bucket://safety
    # ------------------------------------------------------------------
    def list_safety_protocols(self) -> list[SafetyProtocol]:
        """Return all safety protocols ordered by descending priority."""
        return sorted(
            self.pack.safety_protocols, key=lambda p: p.priority, reverse=True
        )

    def get_pinned_safety_protocols(self) -> list[SafetyProtocol]:
        """Return global safety rules that must always stay pinned in context."""
        return [p for p in self.list_safety_protocols() if p.always_include]

    # ------------------------------------------------------------------
    # bucket://certifications
    # ------------------------------------------------------------------
    def get_certification(self, cert_id: str) -> Optional[CertificationTier]:
        return self._certs_by_id.get(cert_id)

    def resolve_certification_chain(self, cert_id: str) -> list[CertificationTier]:
        """Return prerequisite certs followed by the requested certification tier."""
        visited: set[str] = set()
        ordered: list[CertificationTier] = []

        def _visit(cid: str) -> None:
            if cid in visited or cid not in self._certs_by_id:
                return
            visited.add(cid)
            tier = self._certs_by_id[cid]
            for prereq in tier.prerequisites:
                _visit(prereq)
            ordered.append(tier)

        _visit(cert_id)
        return ordered

    def list_certifications(self) -> list[CertificationTier]:
        return list(self.pack.certifications)

    # ------------------------------------------------------------------
    # bucket://machines
    # ------------------------------------------------------------------
    def get_machine(self, machine_id: str) -> Optional[MachineSpec]:
        return self._machines_by_id.get(machine_id)

    def find_machines(
        self,
        query: Optional[str] = None,
        subsystems: Optional[list[Subsystem]] = None,
        area: Optional[str] = None,
    ) -> list[MachineSpec]:
        """Query campus equipment by keyword, subsystem, or shop area."""
        q_tokens = tokenize(query or "")
        q_lower = (query or "").strip().lower()
        sub_set = set(subsystems or [])
        area_lower = area.strip().lower() if area else None

        scored: list[tuple[int, MachineSpec]] = []
        for machine in self.pack.machines:
            if area_lower and machine.area.lower() != area_lower:
                continue
            if sub_set and not (set(machine.subsystems) & sub_set):
                continue

            if not q_tokens:
                scored.append((1, machine))
                continue

            searchable = " ".join(
                [
                    machine.id,
                    machine.name,
                    machine.model_number or "",
                    machine.description,
                    " ".join(machine.tags),
                    " ".join(machine.allowed_materials),
                ]
            )
            m_tokens = tokenize(searchable)
            overlap = len(q_tokens & m_tokens)
            phrase_bonus = 3 if q_lower and q_lower in searchable.lower() else 0
            score = overlap + phrase_bonus
            if score > 0:
                scored.append((score, machine))

        scored.sort(key=lambda pair: (-pair[0], pair[1].name))
        return [m for _, m in scored]

    # ------------------------------------------------------------------
    # bucket://inventory
    # ------------------------------------------------------------------
    def search_inventory(
        self,
        query: str = "",
        area: Optional[str] = None,
        only_in_stock: bool = False,
        limit: int = 25,
    ) -> list[InventoryItem]:
        """Search live stockroom inventory by name, SKU, location, or category."""
        q_tokens = tokenize(query)
        q_lower = query.strip().lower()
        area_lower = area.strip().lower() if area else None

        matches: list[tuple[int, InventoryItem]] = []
        for item in self.pack.inventory:
            if only_in_stock and item.stock_status == StockStatus.OUT_OF_STOCK:
                continue
            if area_lower and area_lower not in item.area.lower() and area_lower not in item.location_display.lower():
                continue

            if not q_tokens:
                matches.append((1, item))
                continue

            haystack = " ".join(
                [item.sku, item.name, item.category, item.item_type, item.location_display]
            )
            item_tokens = tokenize(haystack)
            overlap = len(q_tokens & item_tokens)
            substring_bonus = 4 if q_lower and q_lower in haystack.lower() else 0
            score = overlap * 2 + substring_bonus
            if score > 0:
                matches.append((score, item))

        matches.sort(key=lambda pair: (-pair[0], pair[1].name))
        return [item for _, item in matches[:limit]]

    # ------------------------------------------------------------------
    # bucket://workflows
    # ------------------------------------------------------------------
    def register_workflow(self, plan: BuildPlanSpec) -> BuildPlanSpec:
        """Store or update an active build plan in memory (DB-backed in Slice 4)."""
        key = plan.project_id if plan.project_id is not None else (len(self._workflows) + 1)
        stored = plan.model_copy(update={"project_id": key})
        self._workflows[key] = stored
        return stored

    def list_workflows(self, student_id: Optional[str] = None) -> list[BuildPlanSpec]:
        plans = list(self._workflows.values())
        if student_id:
            plans = [p for p in plans if p.student_id == student_id]
        return plans
