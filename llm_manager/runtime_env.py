"""Shared runtime-environment loading for API and engine processes.

Values are never logged here. Process environment has highest precedence,
followed by the shared environment file and then the project-local fallback.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def read_runtime_env(
    *,
    project_env_path: Path,
    global_env_path: Path,
    process_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return effective runtime values without mutating ``os.environ``."""

    process = dict(os.environ if process_env is None else process_env)
    effective = read_env_file(project_env_path)
    for key, value in read_env_file(global_env_path).items():
        if value:
            effective[key] = value
    effective.update({str(key): str(value) for key, value in process.items()})
    return effective
