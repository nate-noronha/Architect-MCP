"""Core domain schemas for Architect-MCP's 5 modular context buckets and university tenant packs."""

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class BucketKind(str, Enum):
    """Canonical context bucket identifiers exposed by Architect-MCP."""

    SAFETY = "safety"
    CERTIFICATIONS = "certifications"
    INVENTORY = "inventory"
    MACHINES = "machines"
    WORKFLOWS = "workflows"


class Subsystem(str, Enum):
    """Hardware engineering subsystem categories."""

    MECHANICAL = "mechanical"
    ELECTRICAL = "electrical"
    FIRMWARE = "firmware"
    FABRICATION = "fabrication"
    TEXTILES = "textiles"
    ASSEMBLY = "assembly"
    GENERAL = "general"


class SafetySeverity(str, Enum):
    """Severity and gating tier for safety protocols."""

    INFO = "info"
    CAUTION = "caution"
    WARNING = "warning"
    STEWARD_SIGNOFF_REQUIRED = "steward_signoff_required"
    FORBIDDEN = "forbidden"


class StockStatus(str, Enum):
    """Normalized availability states across university makerspace CSV exports."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    OUT_OF_STOCK = "out_of_stock"
    LIMITED_COUNT = "limited_count"


class MachineStatus(str, Enum):
    """Live equipment readiness states."""

    OPERATIONAL = "operational"
    MAINTENANCE = "maintenance"
    DOWN = "down"
    RESERVED = "reserved"


class RoleLevel(str, Enum):
    """User authorization tiers."""

    STUDENT = "student"
    CERTIFIED_USER = "certified_user"
    STEWARD = "steward"
    ADMIN = "admin"


def _normalize_str_list(value: object) -> list[str]:
    """Normalize None, comma/semicolon strings, or lists into stripped non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        raw_parts = value.replace(";", ",").split(",")
        return [part.strip() for part in raw_parts if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


class CampusArea(BaseModel):
    """A physical shop, bay, or stockroom within a university makerspace."""

    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    facility_code: str = Field(min_length=1)
    description: str = ""
    required_base_cert: Optional[str] = None
    policy_links: dict[str, str] = Field(default_factory=dict)


class CampusConfig(BaseModel):
    """Top-level institutional metadata defined in `campus.yaml`."""

    university_slug: str = Field(min_length=1)
    university_name: str = Field(min_length=1)
    makerspace_name: str = Field(min_length=1)
    location: str = ""
    website: Optional[str] = None
    contact_email: Optional[str] = None
    default_token_budget: int = Field(default=4000, ge=500)
    areas: list[CampusArea] = Field(default_factory=list)
    emergency_contacts: dict[str, str] = Field(default_factory=dict)


class SafetyProtocol(BaseModel):
    """Context item in `bucket://safety`: SOPs, PPE rules, and hazard gates."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    bucket: Literal[BucketKind.SAFETY] = BucketKind.SAFETY
    severity: SafetySeverity = SafetySeverity.WARNING
    priority: int = Field(default=50, ge=1, le=100)
    always_include: bool = False
    applies_to_areas: list[str] = Field(default_factory=list)
    applies_to_machines: list[str] = Field(default_factory=list)
    applies_to_subsystems: list[Subsystem] = Field(default_factory=list)
    trigger_keywords: list[str] = Field(default_factory=list)
    required_ppe: list[str] = Field(default_factory=list)
    forbidden_materials: list[str] = Field(default_factory=list)
    requires_steward_signoff: bool = False
    content: str = Field(min_length=1)
    source_file: str = ""

    @field_validator(
        "applies_to_areas",
        "applies_to_machines",
        "trigger_keywords",
        "required_ppe",
        "forbidden_materials",
        mode="before",
    )
    @classmethod
    def _coerce_lists(cls, val: object) -> list[str]:
        return _normalize_str_list(val)


class CertificationTier(BaseModel):
    """Context item in `bucket://certifications`: student training badges and prerequisites."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    bucket: Literal[BucketKind.CERTIFICATIONS] = BucketKind.CERTIFICATIONS
    level: int = Field(default=1, ge=1)
    description: str = ""
    granting_area: str = ""
    prerequisites: list[str] = Field(default_factory=list)
    unlocks_machines: list[str] = Field(default_factory=list)
    requires_in_person_checkoff: bool = True
    training_url: Optional[str] = None

    @field_validator("prerequisites", "unlocks_machines", mode="before")
    @classmethod
    def _coerce_lists(cls, val: object) -> list[str]:
        return _normalize_str_list(val)


class MachineSpec(BaseModel):
    """Context item in `bucket://machines`: equipment capabilities, constraints, and status."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    bucket: Literal[BucketKind.MACHINES] = BucketKind.MACHINES
    model_number: Optional[str] = None
    area: str = Field(min_length=1)
    subsystems: list[Subsystem] = Field(default_factory=list)
    status: MachineStatus = MachineStatus.OPERATIONAL
    units_available: int = Field(default=1, ge=0)
    units_total: int = Field(default=1, ge=1)
    required_cert: Optional[str] = None
    requires_steward_signoff: bool = False
    work_envelope: Optional[str] = None
    allowed_materials: list[str] = Field(default_factory=list)
    forbidden_materials: list[str] = Field(default_factory=list)
    manual_url: Optional[str] = None
    setup_video_url: Optional[str] = None
    description: str = ""
    tags: list[str] = Field(default_factory=list)

    @field_validator(
        "allowed_materials", "forbidden_materials", "tags", mode="before"
    )
    @classmethod
    def _coerce_lists(cls, val: object) -> list[str]:
        return _normalize_str_list(val)


class InventoryItem(BaseModel):
    """Context item in `bucket://inventory`: live stockroom and cabinet bin availability."""

    sku: str = Field(min_length=1)
    name: str = Field(min_length=1)
    bucket: Literal[BucketKind.INVENTORY] = BucketKind.INVENTORY
    item_type: str = "component"
    category: str = "general"
    area: str = "makerspace"
    locations: list[str] = Field(default_factory=list)
    location_display: str = ""
    quantity_raw: str = "0"
    quantity_numeric: Optional[int] = None
    quantity_total: Optional[int] = None
    stock_status: StockStatus = StockStatus.LIMITED_COUNT
    unit: str = "pcs"
    required_certs: list[str] = Field(default_factory=list)
    required_roles: list[str] = Field(default_factory=lambda: ["User"])
    notes: Optional[str] = None

    @field_validator("locations", "required_certs", "required_roles", mode="before")
    @classmethod
    def _coerce_lists(cls, val: object) -> list[str]:
        return _normalize_str_list(val)


class StepInput(BaseModel):
    """A single hardware assembly or machining step inside `bucket://workflows`."""

    step_order: int = Field(ge=1)
    subsystem: Subsystem = Subsystem.ASSEMBLY
    title: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    area: Optional[str] = None
    machines_used: list[str] = Field(default_factory=list)
    requires_steward_signoff: bool = False
    safety_notes: Optional[str] = None
    is_completed: bool = False
    safety_approved_by: Optional[str] = None
    safety_approved_at: Optional[datetime] = None


class BOMInput(BaseModel):
    """A material or component entry in a build plan."""

    part_name: str = Field(min_length=1)
    quantity_needed: int = Field(default=1, ge=1)
    matched_sku: Optional[str] = None
    in_stock_on_campus: bool = False
    location_hint: Optional[str] = None
    external_purchase_note: Optional[str] = None


class BuildPlanSpec(BaseModel):
    """Context item in `bucket://workflows`: student project breakdown & gate states."""

    project_id: Optional[int] = None
    university_slug: str = Field(min_length=1)
    student_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    bucket: Literal[BucketKind.WORKFLOWS] = BucketKind.WORKFLOWS
    steps: list[StepInput] = Field(default_factory=list)
    bom_items: list[BOMInput] = Field(default_factory=list)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class TenantPack(BaseModel):
    """Complete hydrated institutional knowledge pack for one university makerspace."""

    campus: CampusConfig
    safety_protocols: list[SafetyProtocol] = Field(default_factory=list)
    certifications: list[CertificationTier] = Field(default_factory=list)
    machines: list[MachineSpec] = Field(default_factory=list)
    inventory: list[InventoryItem] = Field(default_factory=list)
    load_warnings: list[str] = Field(default_factory=list)
    source_path: str = ""

    @property
    def university_slug(self) -> str:
        return self.campus.university_slug

    def bucket_counts(self) -> dict[str, int]:
        """Return item counts per context bucket for diagnostics and MCP resources."""
        return {
            BucketKind.SAFETY.value: len(self.safety_protocols),
            BucketKind.CERTIFICATIONS.value: len(self.certifications),
            BucketKind.MACHINES.value: len(self.machines),
            BucketKind.INVENTORY.value: len(self.inventory),
        }
