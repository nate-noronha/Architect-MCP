"""Resilient CSV inventory loader supporting both make.hmc.edu and standard stockroom schemas."""

import csv
import io
import re
from pathlib import Path
from typing import Optional

from architect_mcp.models import InventoryItem, StockStatus

_RATIO_RE = re.compile(r"^(\d+)\s*/\s*(\d+)$")
_LOCATION_SPLIT_RE = re.compile(r"\s*(?:->|>|\||;)\s*")

# Case-insensitive column aliases mapping diverse university CSV exports to canonical fields.
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "sku": ("sku", "id", "part_id", "part_number", "item_code"),
    "name": ("name", "part_name", "item", "item_name", "title", "description"),
    "item_type": ("type", "item_type", "kind"),
    "category": ("category", "cat", "group", "subsystem"),
    "area": ("area", "facility", "room", "lab", "zone"),
    "location": ("locations", "location", "bin", "cabinet", "shelf"),
    "quantity": ("quantity", "qty", "stock", "availability", "status"),
    "unit": ("unit", "units", "uom"),
    "required_certs": ("required certs", "required_certs", "certs", "certifications", "cert"),
    "required_roles": ("required roles", "required_roles", "roles", "role"),
    "notes": ("notes", "note", "comments"),
}


def _slug_sku(name: str, row_idx: int) -> str:
    base = re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")[:32]
    return f"{base or 'ITEM'}-{row_idx:03d}"


def _parse_quantity_and_status(raw_qty: str) -> tuple[Optional[int], Optional[int], StockStatus]:
    """Interpret qualitative ('High', 'Low'), ratio ('3/8'), or integer quantities."""
    cleaned = (raw_qty or "").strip()
    lower = cleaned.lower()

    if lower in {"high", "in stock", "plenty", "available"}:
        return None, None, StockStatus.HIGH
    if lower in {"medium", "med", "moderate"}:
        return None, None, StockStatus.MEDIUM
    if lower in {"low", "running low", "reorder"}:
        return None, None, StockStatus.LOW
    if lower in {"none", "out", "out of stock", "empty"}:
        return 0, None, StockStatus.OUT_OF_STOCK

    ratio_match = _RATIO_RE.match(cleaned)
    if ratio_match:
        avail = int(ratio_match.group(1))
        total = int(ratio_match.group(2))
        if avail <= 0:
            return 0, total, StockStatus.OUT_OF_STOCK
        if total > 0 and (avail / total) <= 0.35:
            return avail, total, StockStatus.LOW
        return avail, total, StockStatus.LIMITED_COUNT

    try:
        numeric = int(cleaned)
        if numeric < 0:
            raise ValueError("negative quantity")
        if numeric == 0:
            return 0, None, StockStatus.OUT_OF_STOCK
        if numeric <= 3:
            return numeric, None, StockStatus.LOW
        if numeric >= 15:
            return numeric, None, StockStatus.HIGH
        return numeric, None, StockStatus.LIMITED_COUNT
    except ValueError as exc:
        raise ValueError(
            f"Unrecognized quantity value '{cleaned}'. Expected integer, ratio (e.g. 3/8), or High/Medium/Low."
        ) from exc


def _resolve_field_map(fieldnames: list[str]) -> dict[str, str]:
    """Map canonical keys to the actual header strings in a CSV file."""
    lower_to_actual = {fn.strip().lower(): fn for fn in fieldnames if fn and fn.strip()}
    resolved: dict[str, str] = {}
    for canonical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lower_to_actual:
                resolved[canonical] = lower_to_actual[alias]
                break
    return resolved


def parse_inventory_csv(
    csv_text: str, source_name: str = "inventory.csv"
) -> tuple[list[InventoryItem], list[str]]:
    """Parse inventory CSV text into validated InventoryItem entries + row-level error messages."""
    items: list[InventoryItem] = []
    errors: list[str] = []

    if not csv_text or not csv_text.strip():
        return items, [f"{source_name}: CSV payload is empty."]

    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    if not reader.fieldnames:
        return items, [f"{source_name}: CSV header row missing or unreadable."]

    field_map = _resolve_field_map(list(reader.fieldnames))
    for req in ("name", "quantity"):
        if req not in field_map:
            return items, [
                f"{source_name}: Missing required column matching '{req}'. Found headers: {', '.join(reader.fieldnames)}"
            ]

    for row_idx, row in enumerate(reader, start=2):
        name_val = (row.get(field_map["name"]) or "").strip()
        if not name_val:
            errors.append(f"{source_name} row {row_idx}: Name cannot be blank.")
            continue

        sku_val = (
            (row.get(field_map["sku"]) or "").strip()
            if "sku" in field_map
            else ""
        ) or _slug_sku(name_val, row_idx)

        qty_raw = (row.get(field_map["quantity"]) or "").strip()
        try:
            qty_num, qty_total, stock_status = _parse_quantity_and_status(qty_raw)
        except ValueError as exc:
            errors.append(f"{source_name} row {row_idx} ({name_val}): {exc}")
            continue

        raw_location = (
            (row.get(field_map["location"]) or "").strip()
            if "location" in field_map
            else ""
        )
        loc_parts = [p for p in _LOCATION_SPLIT_RE.split(raw_location) if p] if raw_location else []

        raw_area = (
            (row.get(field_map["area"]) or "").strip()
            if "area" in field_map
            else ""
        )
        area_val = raw_area or (loc_parts[0] if loc_parts else "Makerspace")

        item_type_val = (
            (row.get(field_map["item_type"]) or "").strip().lower()
            if "item_type" in field_map
            else ""
        ) or "component"

        category_val = (
            (row.get(field_map["category"]) or "").strip()
            if "category" in field_map
            else ""
        ) or item_type_val

        unit_val = (
            (row.get(field_map["unit"]) or "").strip()
            if "unit" in field_map
            else ""
        ) or "pcs"

        certs_raw = (
            (row.get(field_map["required_certs"]) or "").strip()
            if "required_certs" in field_map
            else ""
        )
        roles_raw = (
            (row.get(field_map["required_roles"]) or "").strip()
            if "required_roles" in field_map
            else ""
        ) or "User"
        notes_val = (
            (row.get(field_map["notes"]) or "").strip()
            if "notes" in field_map
            else None
        ) or None

        items.append(
            InventoryItem(
                sku=sku_val,
                name=name_val,
                item_type=item_type_val,
                category=category_val,
                area=area_val,
                locations=loc_parts,
                location_display=" -> ".join(loc_parts) if loc_parts else area_val,
                quantity_raw=qty_raw,
                quantity_numeric=qty_num,
                quantity_total=qty_total,
                stock_status=stock_status,
                unit=unit_val,
                required_certs=certs_raw,
                required_roles=roles_raw,
                notes=notes_val,
            )
        )

    return items, errors


def load_inventory_directory(inventory_dir: Path) -> tuple[list[InventoryItem], list[str]]:
    """Load and merge all `.csv` files under `<tenant_dir>/inventory/`."""
    if not inventory_dir.exists():
        return [], []

    all_items: list[InventoryItem] = []
    all_warnings: list[str] = []
    for csv_path in sorted(inventory_dir.glob("*.csv"), key=lambda p: p.name):
        items, warnings = parse_inventory_csv(
            csv_path.read_text(encoding="utf-8"), source_name=csv_path.name
        )
        all_items.extend(items)
        all_warnings.extend(warnings)

    return all_items, all_warnings
