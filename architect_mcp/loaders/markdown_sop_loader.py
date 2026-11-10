"""Markdown SOP parser (with YAML frontmatter) for `bucket://safety`."""

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from architect_mcp.models import SafetyProtocol

_FRONTMATTER_RE = re.compile(
    r"^\s*---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n(.*))?$",
    re.DOTALL,
)
_H1_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


class SafetySOPParseError(ValueError):
    """Raised when a safety Markdown SOP fails frontmatter or schema validation."""


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "safety-protocol"


def parse_safety_markdown(text: str, source_name: str = "inline.md") -> SafetyProtocol:
    """Parse one safety Markdown SOP with optional YAML frontmatter into a SafetyProtocol."""
    if not text or not text.strip():
        raise SafetySOPParseError(f"Safety SOP file '{source_name}' is empty.")

    meta: dict[str, Any] = {}
    body = text.strip()

    match = _FRONTMATTER_RE.match(text)
    if match:
        frontmatter_raw, body_raw = match.group(1), match.group(2) or ""
        try:
            parsed = yaml.safe_load(frontmatter_raw) or {}
        except yaml.YAMLError as exc:
            raise SafetySOPParseError(
                f"Malformed YAML frontmatter in {source_name}: {exc}"
            ) from exc

        if not isinstance(parsed, dict):
            raise SafetySOPParseError(
                f"Frontmatter in {source_name} must be a YAML key-value mapping."
            )
        meta = dict(parsed)
        body = body_raw.strip()

    if not body:
        raise SafetySOPParseError(
            f"Safety SOP '{source_name}' has no Markdown instruction body."
        )

    if "title" not in meta:
        h1_match = _H1_RE.search(body)
        meta["title"] = (
            h1_match.group(1).strip()
            if h1_match
            else Path(source_name).stem.replace("_", " ").replace("-", " ").title()
        )

    if "id" not in meta:
        meta["id"] = _slugify(Path(source_name).stem)

    meta["content"] = body
    meta["source_file"] = source_name

    try:
        return SafetyProtocol.model_validate(meta)
    except ValidationError as exc:
        raise SafetySOPParseError(
            f"Invalid safety protocol metadata in {source_name}: {exc}"
        ) from exc


def load_safety_directory(safety_dir: Path) -> list[SafetyProtocol]:
    """Load all `.md` SOP documents (and optional `.yaml` rules) in `<tenant_dir>/safety/`."""
    if not safety_dir.exists():
        return []

    protocols: list[SafetyProtocol] = []
    for md_path in sorted(safety_dir.glob("*.md"), key=lambda p: p.name):
        protocols.append(
            parse_safety_markdown(
                md_path.read_text(encoding="utf-8"), source_name=md_path.name
            )
        )

    for yaml_path in sorted(
        [*safety_dir.glob("*.yaml"), *safety_dir.glob("*.yml")],
        key=lambda p: p.name,
    ):
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        entries = raw.get("protocols", raw) if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            raise SafetySOPParseError(
                f"Safety YAML file {yaml_path} must contain a list or 'protocols:' key."
            )
        for idx, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                raise SafetySOPParseError(
                    f"Entry #{idx} in {yaml_path} must be a YAML object."
                )
            entry_copy = dict(entry)
            entry_copy.setdefault("source_file", yaml_path.name)
            protocols.append(SafetyProtocol.model_validate(entry_copy))

    return protocols
