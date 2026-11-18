"""Slice 5 tests: Admin & Compliance CLI (`architect-admin` / `architect-mcp <subcommand>`)."""

import json
import shutil
import sys
from pathlib import Path

import pytest

from architect_mcp.cli import main as cli_main
from architect_mcp.config import PROJECT_ROOT
from architect_mcp.server import main as server_main


def test_cli_validate_pack_all_builtin_tenants(capsys: pytest.CaptureFixture[str]):
    """Verify `validate-pack --all --json` passes for all built-in university packs."""
    exit_code = cli_main(["validate-pack", "--all", "--json"])
    assert exit_code == 0

    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["all_valid"] is True
    slugs = {r["university_slug"] for r in data["reports"]}
    assert {"hmc-makerspace", "mit-makerworks-demo"}.issubset(slugs)


def test_cli_validate_pack_missing_tenant_returns_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """Verify missing tenant pack fails validation cleanly with exit code 1."""
    exit_code = cli_main(
        ["--tenant-dir", str(tmp_path), "validate-pack", "nonexistent-uni", "--json"]
    )
    assert exit_code == 1
    data = json.loads(capsys.readouterr().out)
    assert data["all_valid"] is False
    assert data["reports"][0]["valid"] is False


def test_cli_list_tenants_outputs_discovered_packs(capsys: pytest.CaptureFixture[str]):
    """Verify `list-tenants --json` returns campus metadata and bucket counts."""
    exit_code = cli_main(["list-tenants", "--json"])
    assert exit_code == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["tenants"]) >= 2
    hmc = next(t for t in data["tenants"] if t["slug"] == "hmc-makerspace")
    assert hmc["buckets"]["safety"] >= 1
    assert hmc["buckets"]["inventory"] >= 1


def test_cli_ingest_csv_validates_and_applies_to_pack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """Verify `ingest-csv --apply` parses a CSV and installs it into a tenant pack."""
    packs_dir = tmp_path / "tenant_packs"
    shutil.copytree(PROJECT_ROOT / "tenant_packs" / "hmc-makerspace", packs_dir / "hmc-makerspace")

    csv_file = tmp_path / "new_stock.csv"
    csv_file.write_text(
        "sku,name,category,facility,location,quantity,unit\n"
        "NEW-101,6061 Aluminum Bar 1in,Raw Stock,Machine Shop,Rack B2,12,pcs\n"
        "NEW-102,M4 Nylon Locknut,Fasteners,Main Area,Bin 4,0,pcs\n",
        encoding="utf-8",
    )

    exit_code = cli_main(
        [
            "--tenant-dir",
            str(packs_dir),
            "ingest-csv",
            str(csv_file),
            "--tenant",
            "hmc-makerspace",
            "--apply",
            "--json",
        ]
    )
    assert exit_code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["items_parsed"] == 2
    assert data["pack_valid_after_ingest"] is True
    assert (packs_dir / "hmc-makerspace" / "inventory" / "new_stock.csv").exists()


def test_cli_inspect_harness_returns_budget_and_safety_flags(
    capsys: pytest.CaptureFixture[str],
):
    """Verify `inspect-harness --json` surfaces safety flags and token budget usage."""
    exit_code = cli_main(
        [
            "inspect-harness",
            "Turn 6061 shaft on engine lathe with 60V battery pack",
            "--tenant",
            "hmc-makerspace",
            "--budget",
            "3500",
            "--json",
        ]
    )
    assert exit_code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["university_slug"] == "hmc-makerspace"
    assert data["steward_signoff_likely_required"] is True
    assert data["total_tokens_used"] <= 3500
    assert len(data["hazard_warnings"]) >= 1


def test_server_main_dispatches_admin_subcommands(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """Verify `architect-mcp validate-pack --all` routes through server.main cleanly."""
    monkeypatch.setattr(sys, "argv", ["architect-mcp", "validate-pack", "--all"])
    with pytest.raises(SystemExit) as exc_info:
        server_main()
    assert exc_info.value.code == 0
    assert "[PASS] hmc-makerspace" in capsys.readouterr().out
