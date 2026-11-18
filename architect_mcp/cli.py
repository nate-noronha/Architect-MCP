"""Admin & Compliance CLI (`architect-admin` / `architect-mcp <subcommand>`).

Allows makerspace directors to validate drop-in university tenant packs,
ingest/verify stockroom CSV exports, and inspect the Dynamic Context Harness
without launching an AI host client.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import yaml

from architect_mcp.config import PROJECT_ROOT, get_settings
from architect_mcp.harness.bucket_manager import BucketManager
from architect_mcp.harness.router import ContextRouter
from architect_mcp.loaders import (
    TenantLoadError,
    list_available_tenants,
    load_tenant_pack,
    parse_inventory_csv,
    validate_tenant_pack,
)

ADMIN_SUBCOMMANDS = frozenset(
    {
        "validate-pack",
        "ingest-csv",
        "inspect-harness",
        "list-tenants",
        "init-pack",
        "print-config",
    }
)


def cmd_list_tenants(
    tenant_packs_dir: Optional[Path] = None,
    json_output: bool = False,
) -> int:
    """List all discovered university tenant packs and their bucket counts."""
    settings = get_settings()
    packs_dir = tenant_packs_dir or settings.tenant_packs_dir
    slugs = list_available_tenants(packs_dir)

    rows: list[dict[str, Any]] = []
    for slug in slugs:
        try:
            pack = load_tenant_pack(tenant_slug=slug, tenant_packs_dir=packs_dir)
            rows.append(
                {
                    "slug": slug,
                    "university_name": pack.campus.university_name,
                    "makerspace_name": pack.campus.makerspace_name,
                    "buckets": pack.bucket_counts(),
                }
            )
        except Exception as exc:
            rows.append({"slug": slug, "error": str(exc)})

    if json_output:
        print(json.dumps({"tenant_packs_dir": str(packs_dir), "tenants": rows}, indent=2))
        return 0

    print(f"Tenant packs directory: {packs_dir}")
    if not rows:
        print("  (no tenant packs found)")
        return 0

    for row in rows:
        if "error" in row:
            print(f"  - {row['slug']}: [LOAD ERROR] {row['error']}")
        else:
            b = row["buckets"]
            print(
                f"  - {row['slug']} ({row['university_name']} — {row['makerspace_name']}): "
                f"safety={b['safety']}, certs={b['certifications']}, "
                f"machines={b['machines']}, inventory={b['inventory']}"
            )
    return 0


def cmd_validate_pack(
    tenant_slug: Optional[str] = None,
    tenant_packs_dir: Optional[Path] = None,
    validate_all: bool = False,
    json_output: bool = False,
) -> int:
    """Validate one or all university tenant packs for cross-bucket compliance."""
    settings = get_settings()
    packs_dir = tenant_packs_dir or settings.tenant_packs_dir

    slugs = (
        list_available_tenants(packs_dir)
        if validate_all
        else [tenant_slug or settings.tenant_slug]
    )
    if not slugs:
        if json_output:
            print(json.dumps({"valid": False, "error": "No tenant packs found."}, indent=2))
        else:
            print(f"No tenant packs found in {packs_dir}", file=sys.stderr)
        return 1

    reports: list[dict[str, Any]] = []
    any_failed = False

    for slug in slugs:
        try:
            pack = load_tenant_pack(tenant_slug=slug, tenant_packs_dir=packs_dir)
            report = validate_tenant_pack(pack)
            has_pinned_safety = any(p.always_include for p in pack.safety_protocols)
            warnings = list(report.warnings)
            if not has_pinned_safety:
                warnings.append(
                    "No safety protocol has `always_include: true` (recommended for global shop PPE rules)."
                )

            entry = {
                "university_slug": slug,
                "university_name": pack.campus.university_name,
                "valid": report.is_valid,
                "errors": report.errors,
                "warnings": warnings,
                "bucket_counts": report.bucket_counts,
            }
            if not report.is_valid:
                any_failed = True
            reports.append(entry)
        except (TenantLoadError, Exception) as exc:
            any_failed = True
            reports.append(
                {
                    "university_slug": slug,
                    "valid": False,
                    "errors": [str(exc)],
                    "warnings": [],
                    "bucket_counts": {},
                }
            )

    if json_output:
        print(json.dumps({"all_valid": not any_failed, "reports": reports}, indent=2))
        return 1 if any_failed else 0

    for entry in reports:
        status_label = "PASS" if entry["valid"] else "FAIL"
        print(f"[{status_label}] {entry['university_slug']}")
        if entry.get("bucket_counts"):
            print(f"  Buckets: {entry['bucket_counts']}")
        for err in entry["errors"]:
            print(f"  ERROR: {err}")
        for warn in entry["warnings"]:
            print(f"  WARN:  {warn}")

    return 1 if any_failed else 0


def cmd_ingest_csv(
    csv_path: Path,
    tenant_slug: Optional[str] = None,
    tenant_packs_dir: Optional[Path] = None,
    apply_to_pack: bool = False,
    json_output: bool = False,
) -> int:
    """Parse and validate a makerspace inventory CSV, optionally installing it into a tenant pack."""
    resolved_csv = csv_path.expanduser().resolve()
    if not resolved_csv.exists() or not resolved_csv.is_file():
        msg = f"CSV file not found: {resolved_csv}"
        if json_output:
            print(json.dumps({"ok": False, "error": msg}, indent=2))
        else:
            print(f"ERROR: {msg}", file=sys.stderr)
        return 1

    csv_text = resolved_csv.read_text(encoding="utf-8")
    items, warnings = parse_inventory_csv(csv_text, source_name=resolved_csv.name)
    status_counts = Counter(item.stock_status.value for item in items)

    applied_path: Optional[str] = None
    pack_valid: Optional[bool] = None
    if apply_to_pack:
        settings = get_settings()
        slug = tenant_slug or settings.tenant_slug
        packs_dir = tenant_packs_dir or settings.tenant_packs_dir
        inv_dir = packs_dir / slug / "inventory"
        if not (packs_dir / slug).exists():
            msg = f"Target tenant pack '{slug}' not found in {packs_dir}"
            if json_output:
                print(json.dumps({"ok": False, "error": msg}, indent=2))
            else:
                print(f"ERROR: {msg}", file=sys.stderr)
            return 1
        inv_dir.mkdir(parents=True, exist_ok=True)
        dest = inv_dir / resolved_csv.name
        if dest.resolve() != resolved_csv:
            shutil.copy2(resolved_csv, dest)
        applied_path = str(dest)
        pack = load_tenant_pack(tenant_slug=slug, tenant_packs_dir=packs_dir)
        pack_valid = validate_tenant_pack(pack).is_valid

    result: dict[str, Any] = {
        "ok": len(items) > 0,
        "source_csv": str(resolved_csv),
        "items_parsed": len(items),
        "stock_status_breakdown": dict(status_counts),
        "warnings": warnings,
        "applied_to": applied_path,
        "pack_valid_after_ingest": pack_valid,
    }

    if json_output:
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1

    print(f"Parsed {len(items)} inventory item(s) from {resolved_csv.name}")
    print(f"  Stock status breakdown: {dict(status_counts)}")
    if applied_path:
        print(f"  Installed into tenant pack: {applied_path} (pack_valid={pack_valid})")
    for warn in warnings:
        print(f"  WARN: {warn}")
    return 0 if result["ok"] else 1


def cmd_inspect_harness(
    prompt: str,
    tenant_slug: Optional[str] = None,
    tenant_packs_dir: Optional[Path] = None,
    token_budget: Optional[int] = None,
    student_certs: Optional[list[str]] = None,
    json_output: bool = False,
) -> int:
    """Run the Dynamic Context Harness on a prompt and display bucket packing diagnostics."""
    settings = get_settings()
    slug = tenant_slug or settings.tenant_slug
    packs_dir = tenant_packs_dir or settings.tenant_packs_dir
    budget = token_budget or settings.default_token_budget

    manager = BucketManager.from_tenant(tenant_slug=slug, tenant_packs_dir=packs_dir)
    router = ContextRouter(manager)
    payload = router.assemble(
        project_description=prompt,
        student_certs=student_certs,
        token_budget=budget,
    )

    if json_output:
        print(json.dumps(payload.model_dump(mode="json"), indent=2))
        return 0

    print(f"=== Architect Context Harness Inspection ({payload.university_slug}) ===")
    print(f"Makerspace:      {payload.makerspace_name}")
    print(f"Subsystems:      {[s.value for s in payload.inferred_subsystems]}")
    print(f"Token Budget:    {payload.total_tokens_used} / {payload.token_budget} (safety pinned: {payload.safety_pinned_tokens})")
    print(f"Steward Signoff: {'REQUIRED' if payload.steward_signoff_likely_required else 'Not Triggered'}")
    if payload.missing_certifications:
        print(f"Missing Certs:   {payload.missing_certifications}")
    if payload.hazard_warnings:
        print("Hazard Warnings:")
        for w in payload.hazard_warnings:
            print(f"  - {w}")
    print(
        f"Matched Items:   safety={len(payload.safety_protocols)}, "
        f"certs={len(payload.certifications)}, machines={len(payload.machines)}, "
        f"inventory={len(payload.inventory)}"
    )
    print("\n--- Rendered Context ---\n")
    print(payload.rendered_context)
    return 0


def cmd_init_pack(
    university_slug: str,
    university_name: Optional[str] = None,
    makerspace_name: Optional[str] = None,
    tenant_packs_dir: Optional[Path] = None,
    json_output: bool = False,
) -> int:
    """Scaffold a new drop-in university tenant pack from the starter template."""
    settings = get_settings()
    packs_dir = tenant_packs_dir or settings.tenant_packs_dir
    target_dir = packs_dir / university_slug

    if target_dir.exists():
        msg = f"Tenant pack '{university_slug}' already exists at {target_dir}"
        if json_output:
            print(json.dumps({"ok": False, "error": msg}, indent=2))
        else:
            print(f"ERROR: {msg}", file=sys.stderr)
        return 1

    template_dir = PROJECT_ROOT / "tenant_packs" / "university-template"
    if not template_dir.exists():
        msg = f"Starter template not found at {template_dir}"
        if json_output:
            print(json.dumps({"ok": False, "error": msg}, indent=2))
        else:
            print(f"ERROR: {msg}", file=sys.stderr)
        return 1

    packs_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template_dir, target_dir)

    campus_file = target_dir / "campus.yaml"
    campus_data = yaml.safe_load(campus_file.read_text(encoding="utf-8")) or {}
    campus_data["university_slug"] = university_slug
    campus_data["university_name"] = university_name or university_slug.replace("-", " ").title()
    campus_data["makerspace_name"] = makerspace_name or f"{campus_data['university_name']} Makerspace"
    campus_file.write_text(yaml.safe_dump(campus_data, sort_keys=False), encoding="utf-8")

    pack = load_tenant_pack(tenant_slug=university_slug, tenant_packs_dir=packs_dir)
    report = validate_tenant_pack(pack)

    result = {
        "ok": report.is_valid,
        "university_slug": university_slug,
        "path": str(target_dir),
        "bucket_counts": report.bucket_counts,
        "errors": report.errors,
    }
    if json_output:
        print(json.dumps(result, indent=2))
    else:
        print(f"Initialized tenant pack '{university_slug}' at {target_dir}")
        print(f"  Buckets: {report.bucket_counts}")
        print(f"  Valid:   {report.is_valid}")
    return 0 if report.is_valid else 1


def cmd_print_config(
    client: str = "claude-desktop",
    tenant_slug: Optional[str] = None,
    tenant_packs_dir: Optional[Path] = None,
) -> int:
    """Print ready-to-paste MCP client configuration JSON for Claude Desktop, Cursor, or Antigravity."""
    settings = get_settings()
    slug = tenant_slug or settings.tenant_slug
    packs_dir = tenant_packs_dir or settings.tenant_packs_dir

    env_block: dict[str, str] = {
        "ARCHITECT_TENANT": slug,
        "ARCHITECT_TENANT_DIR": str(packs_dir),
        "ARCHITECT_TOKEN_BUDGET": str(settings.default_token_budget),
    }

    payload = {
        "mcpServers": {
            "architect": {
                "command": sys.executable,
                "args": ["-m", "architect_mcp.server", "--transport", "stdio"],
                "env": env_block,
            }
        }
    }

    if client == "antigravity":
        payload["mcpServers"]["architect"]["description"] = (
            f"Architect-MCP Makerspace Context Harness ({slug})"
        )

    print(json.dumps(payload, indent=2))
    return 0


def build_admin_parser() -> argparse.ArgumentParser:
    """Build the argparse CLI for `architect-admin` / `architect-mcp` admin subcommands."""
    parser = argparse.ArgumentParser(
        prog="architect-admin",
        description="Architect-MCP Admin & Compliance CLI for university makerspace directors",
    )
    parser.add_argument(
        "--tenant-dir",
        type=Path,
        default=None,
        help="Override tenant packs directory (defaults to ARCHITECT_TENANT_DIR or ./tenant_packs)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list-tenants
    p_list = subparsers.add_parser(
        "list-tenants", help="List all discovered university tenant packs"
    )
    p_list.add_argument("--json", action="store_true", help="Output JSON")

    # validate-pack
    p_val = subparsers.add_parser(
        "validate-pack", help="Validate cross-bucket integrity of a university tenant pack"
    )
    p_val.add_argument("tenant_slug", nargs="?", default=None, help="Tenant slug to validate")
    p_val.add_argument("--all", action="store_true", help="Validate all tenant packs")
    p_val.add_argument("--json", action="store_true", help="Output JSON")

    # ingest-csv
    p_csv = subparsers.add_parser(
        "ingest-csv", help="Parse and validate a makerspace inventory CSV"
    )
    p_csv.add_argument("csv_path", type=Path, help="Path to inventory CSV file")
    p_csv.add_argument("--tenant", default=None, help="Target tenant slug")
    p_csv.add_argument(
        "--apply",
        action="store_true",
        help="Copy validated CSV into the tenant pack inventory/ folder",
    )
    p_csv.add_argument("--json", action="store_true", help="Output JSON")

    # inspect-harness
    p_ins = subparsers.add_parser(
        "inspect-harness",
        help="Simulate Context Harness routing & token packing for a build prompt",
    )
    p_ins.add_argument("prompt", help="Natural-language hardware build prompt")
    p_ins.add_argument("--tenant", default=None, help="Tenant slug")
    p_ins.add_argument("--budget", type=int, default=None, help="Token budget override")
    p_ins.add_argument(
        "--certs",
        nargs="*",
        default=None,
        help="Student certification IDs or names currently held",
    )
    p_ins.add_argument("--json", action="store_true", help="Output JSON")

    # init-pack
    p_init = subparsers.add_parser(
        "init-pack",
        help="Scaffold a new university tenant pack from the starter template",
    )
    p_init.add_argument("university_slug", help="Slug for the new university (e.g. stanford-prl)")
    p_init.add_argument("--university", default=None, help="Full university name")
    p_init.add_argument("--makerspace", default=None, help="Campus makerspace name")
    p_init.add_argument("--json", action="store_true", help="Output JSON")

    # print-config
    p_cfg = subparsers.add_parser(
        "print-config",
        help="Print ready-to-paste MCP client JSON config for Claude Desktop, Cursor, or Antigravity",
    )
    p_cfg.add_argument(
        "--client",
        choices=["claude-desktop", "cursor", "antigravity"],
        default="claude-desktop",
        help="Target AI host client",
    )
    p_cfg.add_argument("--tenant", default=None, help="University tenant slug")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Entrypoint for `architect-admin` CLI."""
    parser = build_admin_parser()
    args = parser.parse_args(argv)

    if args.command == "list-tenants":
        return cmd_list_tenants(
            tenant_packs_dir=args.tenant_dir,
            json_output=args.json,
        )
    if args.command == "validate-pack":
        return cmd_validate_pack(
            tenant_slug=args.tenant_slug,
            tenant_packs_dir=args.tenant_dir,
            validate_all=args.all,
            json_output=args.json,
        )
    if args.command == "ingest-csv":
        return cmd_ingest_csv(
            csv_path=args.csv_path,
            tenant_slug=args.tenant,
            tenant_packs_dir=args.tenant_dir,
            apply_to_pack=args.apply,
            json_output=args.json,
        )
    if args.command == "inspect-harness":
        return cmd_inspect_harness(
            prompt=args.prompt,
            tenant_slug=args.tenant,
            tenant_packs_dir=args.tenant_dir,
            token_budget=args.budget,
            student_certs=args.certs,
            json_output=args.json,
        )
    if args.command == "init-pack":
        return cmd_init_pack(
            university_slug=args.university_slug,
            university_name=args.university,
            makerspace_name=args.makerspace,
            tenant_packs_dir=args.tenant_dir,
            json_output=args.json,
        )
    if args.command == "print-config":
        return cmd_print_config(
            client=args.client,
            tenant_slug=args.tenant,
            tenant_packs_dir=args.tenant_dir,
        )
    return 1


def entrypoint() -> None:
    """Console script wrapper exiting with CLI status code."""
    sys.exit(main())


if __name__ == "__main__":
    entrypoint()
