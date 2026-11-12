"""Runtime configuration loaded from environment variables (zero hardcoded secrets)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings for Architect-MCP."""

    tenant_slug: str
    tenant_packs_dir: Path
    database_url: str
    default_token_budget: int

    @property
    def active_tenant_path(self) -> Path:
        return self.tenant_packs_dir / self.tenant_slug


def get_settings() -> Settings:
    """Read configuration dynamically from environment variables."""
    tenant_dir_raw = os.getenv("ARCHITECT_TENANT_DIR", "")
    tenant_packs_dir = (
        Path(tenant_dir_raw).expanduser().resolve()
        if tenant_dir_raw
        else (PROJECT_ROOT / "tenant_packs")
    )

    budget_raw = os.getenv("ARCHITECT_TOKEN_BUDGET", "4000")
    try:
        token_budget = max(500, int(budget_raw))
    except ValueError:
        token_budget = 4000

    return Settings(
        tenant_slug=os.getenv("ARCHITECT_TENANT", "hmc-makerspace").strip()
        or "hmc-makerspace",
        tenant_packs_dir=tenant_packs_dir,
        database_url=os.getenv("DATABASE_URL", "sqlite:///./architect_mcp.db"),
        default_token_budget=token_budget,
    )
