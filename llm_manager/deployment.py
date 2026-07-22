from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from .config_migrations import CONFIG_SCHEMA_VERSION, migrate_provider_models, migrate_provider_policies
from .runtime_store import RUNTIME_SCHEMA_VERSION, SQLiteRuntimeStore

REQUIRED_ASSETS = (
    "config/provider_models.json",
    "config/provider_policies.json",
    "config/settings.example.env",
    "config/engine-chat.env",
    "config/engine-intent.env",
    "config/engine-small.env",
    "config/engine-embed.env",
    "systemd/llm-manager-api.service",
    "systemd/llm-manager-engine@.service",
    "sudoers/llm-manager",
    "run/engine_launcher.py",
    "run/launch_tgw.py",
    "run/launch_vllm.py",
    "run/launch_tabbyapi.py",
    "web/index.html",
    "web/app.js",
    "web/js/api.js",
    "web/js/ui-core.js",
)


def default_asset_root() -> Path:
    source_root = Path(__file__).resolve().parent.parent
    if (source_root / "deploy" / "systemd").exists():
        return source_root
    try:
        return Path(distribution("llm-manager").locate_file("share/llm-manager"))
    except PackageNotFoundError:
        return source_root


def _asset_path(asset_root: Path, relative: str) -> Path:
    if relative.startswith("systemd/") and (asset_root / "deploy" / relative).exists():
        return asset_root / "deploy" / relative
    if relative.startswith("sudoers/") and (asset_root / "deploy" / relative).exists():
        return asset_root / "deploy" / relative
    return asset_root / relative


def doctor(asset_root: Path) -> dict:
    errors: list[str] = []
    checked: list[str] = []
    for relative in REQUIRED_ASSETS:
        path = _asset_path(asset_root, relative)
        checked.append(str(path))
        if not path.is_file():
            errors.append(f"missing packaged asset: {relative}")

    config_checks = (
        ("config/provider_models.json", migrate_provider_models),
        ("config/provider_policies.json", migrate_provider_policies),
    )
    for relative, migrator in config_checks:
        path = _asset_path(asset_root, relative)
        if not path.is_file():
            continue
        try:
            document = json.loads(path.read_text())
            migrated, _ = migrator(document)
            if migrated.get("schema_version") != CONFIG_SCHEMA_VERSION:
                errors.append(f"{relative} did not migrate to schema {CONFIG_SCHEMA_VERSION}")
        except Exception as exc:
            errors.append(f"invalid {relative}: {exc}")

    for relative in ("systemd/llm-manager-api.service", "systemd/llm-manager-engine@.service"):
        path = _asset_path(asset_root, relative)
        if not path.is_file():
            continue
        content = path.read_text()
        if "ExecStart=" not in content or "EnvironmentFile=" not in content:
            errors.append(f"{relative} lacks ExecStart or EnvironmentFile")
        if "{{" in content or "}}" in content:
            errors.append(f"{relative} contains unresolved template tokens")

    with tempfile.TemporaryDirectory(prefix="llm-manager-doctor-") as temp_dir:
        store = SQLiteRuntimeStore(Path(temp_dir) / "runtime.db")
        if store.schema_version() != RUNTIME_SCHEMA_VERSION:
            errors.append("runtime SQLite migrations did not reach the current schema")

    return {
        "ok": not errors,
        "asset_root": str(asset_root),
        "config_schema_version": CONFIG_SCHEMA_VERSION,
        "runtime_schema_version": RUNTIME_SCHEMA_VERSION,
        "checked_count": len(checked),
        "errors": errors,
    }


def bootstrap(
    asset_root: Path,
    home: Path,
    config_dir: Path,
    web_dir: Path,
    systemd_dir: Path | None = None,
    sudoers_dir: Path | None = None,
    run_dir: Path | None = None,
) -> dict:
    created: list[str] = []
    skipped: list[str] = []
    extra_dirs = [path for path in (systemd_dir, sudoers_dir, run_dir) if path is not None]
    for path in (home / "run/state", home / "run/logs", home / "secrets", config_dir, web_dir, *extra_dirs):
        path.mkdir(parents=True, exist_ok=True)
    (home / "secrets").chmod(0o700)

    copies = {
        "config/provider_models.json": config_dir / "provider_models.json",
        "config/provider_policies.json": config_dir / "provider_policies.json",
        "config/settings.example.env": config_dir / "llm-manager.env.example",
        "config/engine-chat.env": config_dir / "engine-chat.env",
        "config/engine-intent.env": config_dir / "engine-intent.env",
        "config/engine-small.env": config_dir / "engine-small.env",
        "config/engine-embed.env": config_dir / "engine-embed.env",
    }
    if systemd_dir is not None:
        copies.update({
            "systemd/llm-manager-api.service": systemd_dir / "llm-manager-api.service",
            "systemd/llm-manager-engine@.service": systemd_dir / "llm-manager-engine@.service",
        })
    if sudoers_dir is not None:
        copies["sudoers/llm-manager"] = sudoers_dir / "llm-manager"
    if run_dir is not None:
        for filename in ("engine_launcher.py", "launch_tgw.py", "launch_vllm.py", "launch_tabbyapi.py"):
            copies[f"run/{filename}"] = run_dir / filename
    for relative, destination in copies.items():
        if destination.exists():
            skipped.append(str(destination))
            continue
        shutil.copy2(_asset_path(asset_root, relative), destination)
        if relative.startswith("sudoers/"):
            destination.chmod(0o440)
        elif relative.startswith(("systemd/", "run/")):
            destination.chmod(0o755 if relative.startswith("run/") else 0o644)
        else:
            destination.chmod(0o640)
        created.append(str(destination))

    source_web = _asset_path(asset_root, "web")
    if source_web.is_dir():
        for source in source_web.rglob("*"):
            if not source.is_file():
                continue
            destination = web_dir / source.relative_to(source_web)
            if destination.exists():
                skipped.append(str(destination))
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            destination.chmod(0o644)
            created.append(str(destination))

    return {"ok": True, "created": created, "skipped_existing": skipped}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Validate or bootstrap an llm-manager installation")
    parser.add_argument("command", choices=("doctor", "bootstrap"))
    parser.add_argument("--asset-root", type=Path, default=None)
    parser.add_argument("--home", type=Path, default=Path("/var/lib/llm-manager"))
    parser.add_argument("--config-dir", type=Path, default=Path("/etc/llm-manager"))
    parser.add_argument("--web-dir", type=Path, default=Path("/usr/share/llm-manager/web"))
    parser.add_argument("--systemd-dir", type=Path, default=Path("/etc/systemd/system"))
    parser.add_argument("--sudoers-dir", type=Path, default=Path("/etc/sudoers.d"))
    parser.add_argument("--run-dir", type=Path, default=Path("/usr/share/llm-manager/run"))
    args = parser.parse_args(argv)
    asset_root = (args.asset_root or default_asset_root()).resolve()
    result = (
        doctor(asset_root)
        if args.command == "doctor"
        else bootstrap(
            asset_root,
            args.home,
            args.config_dir,
            args.web_dir,
            args.systemd_dir,
            args.sudoers_dir,
            args.run_dir,
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
