"""YAML configuration loaders for `campus.yaml`, `certifications/*.yaml`, and `machines/*.yaml`."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from architect_mcp.models import CampusConfig, CertificationTier, MachineSpec


class YAMLConfigError(ValueError):
    """Raised when a university YAML file is missing or fails schema validation."""


def _read_yaml_file(path: Path) -> Any:
    """Safely read YAML from disk, attaching file context on syntax errors."""
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except OSError as exc:
        raise YAMLConfigError(f"Could not read YAML file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise YAMLConfigError(f"Malformed YAML syntax in {path}: {exc}") from exc

    if data is None:
        return {}
    return data


def _extract_records(data: Any, key: str, path: Path) -> list[dict[str, Any]]:
    """Support either a top-level list or `{<key>: [...]}` wrapper in YAML configs."""
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict) and key in data:
        records = data[key]
    elif isinstance(data, dict) and not data:
        return []
    else:
        raise YAMLConfigError(
            f"Expected list or '{key}: [...]' mapping in {path}, got {type(data).__name__}."
        )

    if not isinstance(records, list):
        raise YAMLConfigError(f"'{key}' in {path} must be a list of objects.")
    return records


def load_campus_config(tenant_dir: Path) -> CampusConfig:
    """Load and validate `<tenant_dir>/campus.yaml`."""
    campus_path = tenant_dir / "campus.yaml"
    if not campus_path.is_file():
        raise YAMLConfigError(
            f"Missing required campus configuration file: {campus_path}"
        )

    raw = _read_yaml_file(campus_path)
    if not isinstance(raw, dict):
        raise YAMLConfigError(f"Expected YAML mapping in {campus_path}")

    try:
        return CampusConfig.model_validate(raw)
    except ValidationError as exc:
        raise YAMLConfigError(f"Invalid campus.yaml schema in {campus_path}: {exc}") from exc


def load_certifications_directory(cert_dir: Path) -> list[CertificationTier]:
    """Load all `.yaml`/`.yml` files in `<tenant_dir>/certifications/`."""
    if not cert_dir.exists():
        return []

    tiers: list[CertificationTier] = []
    for yaml_path in sorted(
        [*cert_dir.glob("*.yaml"), *cert_dir.glob("*.yml")],
        key=lambda p: p.name,
    ):
        raw = _read_yaml_file(yaml_path)
        records = _extract_records(raw, "certifications", yaml_path)
        for idx, item in enumerate(records, start=1):
            try:
                tiers.append(CertificationTier.model_validate(item))
            except ValidationError as exc:
                raise YAMLConfigError(
                    f"Invalid certification #{idx} in {yaml_path}: {exc}"
                ) from exc
    return tiers


def load_machines_directory(machines_dir: Path) -> list[MachineSpec]:
    """Load all `.yaml`/`.yml` files in `<tenant_dir>/machines/`."""
    if not machines_dir.exists():
        return []

    machines: list[MachineSpec] = []
    for yaml_path in sorted(
        [*machines_dir.glob("*.yaml"), *machines_dir.glob("*.yml")],
        key=lambda p: p.name,
    ):
        raw = _read_yaml_file(yaml_path)
        records = _extract_records(raw, "machines", yaml_path)
        for idx, item in enumerate(records, start=1):
            try:
                machines.append(MachineSpec.model_validate(item))
            except ValidationError as exc:
                raise YAMLConfigError(
                    f"Invalid machine entry #{idx} in {yaml_path}: {exc}"
                ) from exc
    return machines
