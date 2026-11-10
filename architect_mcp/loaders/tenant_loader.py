"""Top-level university tenant pack orchestrator and schema cross-validator."""

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from architect_mcp.config import get_settings
from architect_mcp.loaders.csv_inventory_loader import load_inventory_directory
from architect_mcp.loaders.markdown_sop_loader import (
    SafetySOPParseError,
    load_safety_directory,
)
from architect_mcp.loaders.yaml_config_loader import (
    YAMLConfigError,
    load_campus_config,
    load_certifications_directory,
    load_machines_directory,
)
from architect_mcp.models import TenantPack


class TenantLoadError(RuntimeError):
    """Raised when a university tenant directory cannot be loaded."""


class TenantValidationReport(BaseModel):
    """Structured health & completeness report for any drop-in university tenant pack."""

    university_slug: str
    is_valid: bool
    bucket_counts: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def list_available_tenants(tenant_packs_dir: Optional[Path] = None) -> list[str]:
    """Return sorted directory names containing a `campus.yaml` file."""
    base_dir = tenant_packs_dir or get_settings().tenant_packs_dir
    if not base_dir.is_dir():
        return []
    return sorted(
        entry.name
        for entry in base_dir.iterdir()
        if entry.is_dir() and (entry / "campus.yaml").is_file()
    )


def load_tenant_pack(
    tenant_slug: Optional[str] = None,
    tenant_packs_dir: Optional[Path] = None,
) -> TenantPack:
    """Hydrate all modular context buckets for a university tenant pack on disk."""
    settings = get_settings()
    slug = tenant_slug or settings.tenant_slug
    base_dir = tenant_packs_dir or settings.tenant_packs_dir
    tenant_dir = base_dir / slug

    if not tenant_dir.is_dir():
        available = list_available_tenants(base_dir)
        raise TenantLoadError(
            f"Tenant directory '{tenant_dir}' does not exist. Available tenants: {available}"
        )

    try:
        campus = load_campus_config(tenant_dir)
        safety_protocols = load_safety_directory(tenant_dir / "safety")
        certifications = load_certifications_directory(tenant_dir / "certifications")
        machines = load_machines_directory(tenant_dir / "machines")
    except (YAMLConfigError, SafetySOPParseError) as exc:
        raise TenantLoadError(str(exc)) from exc

    inventory, csv_warnings = load_inventory_directory(tenant_dir / "inventory")

    return TenantPack(
        campus=campus,
        safety_protocols=safety_protocols,
        certifications=certifications,
        machines=machines,
        inventory=inventory,
        load_warnings=csv_warnings,
        source_path=str(tenant_dir),
    )


def validate_tenant_pack(pack: TenantPack) -> TenantValidationReport:
    """Verify bucket cross-references, duplicate IDs, and institutional completeness."""
    errors: list[str] = []
    warnings: list[str] = list(pack.load_warnings)

    area_slugs = {area.slug for area in pack.campus.areas}
    area_names = {area.name.lower() for area in pack.campus.areas}
    cert_ids = {cert.id for cert in pack.certifications}
    cert_names = {cert.name.lower() for cert in pack.certifications}

    if not pack.safety_protocols:
        errors.append("Bucket 'safety' is empty; at least one safety SOP is required.")
    elif not any(proto.always_include for proto in pack.safety_protocols):
        warnings.append(
            "No safety protocol marked 'always_include: true'; general shop rules may not be pinned."
        )

    seen_machine_ids: set[str] = set()
    for machine in pack.machines:
        if machine.id in seen_machine_ids:
            errors.append(f"Duplicate machine ID detected: '{machine.id}'")
        seen_machine_ids.add(machine.id)

        if machine.required_cert and (
            machine.required_cert not in cert_ids
            and machine.required_cert.lower() not in cert_names
        ):
            errors.append(
                f"Machine '{machine.id}' references unknown certification '{machine.required_cert}'."
            )

        if area_slugs and (
            machine.area not in area_slugs and machine.area.lower() not in area_names
        ):
            warnings.append(
                f"Machine '{machine.id}' uses area '{machine.area}' which is not listed in campus.yaml areas."
            )

    seen_skus: set[str] = set()
    for item in pack.inventory:
        if item.sku in seen_skus:
            warnings.append(f"Duplicate inventory SKU '{item.sku}' across CSV rows.")
        seen_skus.add(item.sku)

    return TenantValidationReport(
        university_slug=pack.university_slug,
        is_valid=len(errors) == 0,
        bucket_counts=pack.bucket_counts(),
        errors=errors,
        warnings=warnings,
    )
