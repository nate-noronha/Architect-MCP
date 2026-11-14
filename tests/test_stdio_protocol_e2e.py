"""Slice 6 tests: University starter pack onboarding, config generator & live MCP stdio E2E."""

import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from architect_mcp.cli import main as cli_main
from architect_mcp.config import PROJECT_ROOT


def test_university_template_starter_pack_is_valid(capsys: pytest.CaptureFixture[str]):
    """Verify the built-in `university-template` starter pack passes validation."""
    exit_code = cli_main(["validate-pack", "university-template", "--json"])
    assert exit_code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["all_valid"] is True
    assert data["reports"][0]["bucket_counts"]["safety"] >= 1
    assert data["reports"][0]["bucket_counts"]["machines"] >= 1
    assert data["reports"][0]["bucket_counts"]["inventory"] >= 1


def test_cli_init_pack_scaffolds_and_validates_new_university(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """Verify `init-pack` creates a ready-to-use university pack from the template."""
    packs_dir = tmp_path / "tenant_packs"
    exit_code = cli_main(
        [
            "--tenant-dir",
            str(packs_dir),
            "init-pack",
            "stanford-prl",
            "--university",
            "Stanford University",
            "--makerspace",
            "Product Realization Lab",
            "--json",
        ]
    )
    assert exit_code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["university_slug"] == "stanford-prl"
    assert (packs_dir / "stanford-prl" / "campus.yaml").exists()

    # Duplicate slug should fail cleanly with exit code 1
    dup_code = cli_main(
        ["--tenant-dir", str(packs_dir), "init-pack", "stanford-prl", "--json"]
    )
    assert dup_code == 1


def test_cli_print_config_outputs_valid_client_json(capsys: pytest.CaptureFixture[str]):
    """Verify `print-config` outputs valid MCP JSON for Claude Desktop, Cursor, and Antigravity."""
    for client in ("claude-desktop", "cursor", "antigravity"):
        exit_code = cli_main(
            ["print-config", "--client", client, "--tenant", "hmc-makerspace"]
        )
        assert exit_code == 0
        cfg = json.loads(capsys.readouterr().out)
        arch = cfg["mcpServers"]["architect"]
        assert arch["command"] == sys.executable
        assert arch["args"] == ["-m", "architect_mcp.server", "--transport", "stdio"]
        assert arch["env"]["ARCHITECT_TENANT"] == "hmc-makerspace"


@pytest.mark.anyio
async def test_live_stdio_mcp_subprocess_roundtrip(tmp_path: Path):
    """Spawn `architect_mcp.server` over real stdio pipes and exercise MCP JSON-RPC calls."""
    env = os.environ.copy()
    env["ARCHITECT_TENANT"] = "hmc-makerspace"
    env["ARCHITECT_TENANT_DIR"] = str(PROJECT_ROOT / "tenant_packs")
    env["DATABASE_URL"] = f"sqlite:///{tmp_path / 'stdio_e2e.db'}"

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "architect_mcp.server", "--transport", "stdio"],
        env=env,
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init_res = await session.initialize()
            assert init_res.server_info.name == "architect-mcp"

            tools_res = await session.list_tools()
            tool_names = {t.name for t in tools_res.tools}
            assert "assemble_context_harness" in tool_names
            assert "save_hardware_build_plan" in tool_names
            assert "approve_steward_safety_signoff" in tool_names

            resources_res = await session.list_resources()
            assert any(
                str(r.uri) == "architect://stewards/queue"
                for r in resources_res.resources
            )

            prompts_res = await session.list_prompts()
            assert any(p.name == "architect-build-plan" for p in prompts_res.prompts)

            # Call assemble_context_harness over the wire
            harness_res = await session.call_tool(
                "assemble_context_harness",
                {"project_description": "Mill aluminum motor bracket", "token_budget": 3000},
            )
            assert harness_res.is_error is False
            harness_payload = json.loads(harness_res.content[0].text)
            assert harness_payload["university_slug"] == "hmc-makerspace"
            assert harness_payload["steward_signoff_likely_required"] is True

            # Save a hazardous build plan & verify safety gate blocks unapproved completion
            save_res = await session.call_tool(
                "save_hardware_build_plan",
                {
                    "title": "Wire-Tested Lathe Shaft",
                    "student_id": "e2e@hmc.edu",
                    "steps": [
                        {
                            "step_order": 1,
                            "subsystem": "mechanical",
                            "title": "Turn shaft on engine lathe",
                            "instructions": "Face and turn 6061 aluminum rod",
                            "machines_used": ["lathe"],
                        }
                    ],
                },
            )
            saved = json.loads(save_res.content[0].text)
            assert saved["status"] == "SAVED"
            pid = saved["project_id"]

            blocked_res = await session.call_tool(
                "complete_build_step",
                {"project_id": pid, "step_order": 1, "student_id": "e2e@hmc.edu"},
            )
            blocked = json.loads(blocked_res.content[0].text)
            assert blocked["status"] == "BLOCKED_BY_SAFETY_GATE"
