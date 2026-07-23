from __future__ import annotations

from pathlib import Path

from llm_manager import deployment


def test_source_deployment_assets_pass_doctor_and_bootstrap(tmp_path: Path):
    source_root = Path(__file__).resolve().parents[1]
    result = deployment.doctor(source_root)
    assert result["ok"] is True, result["errors"]

    home = tmp_path / "home"
    config_dir = tmp_path / "etc"
    web_dir = tmp_path / "www"
    systemd_dir = tmp_path / "systemd"
    sudoers_dir = tmp_path / "sudoers"
    run_dir = tmp_path / "run"
    bootstrapped = deployment.bootstrap(source_root, home, config_dir, web_dir, systemd_dir, sudoers_dir, run_dir)
    assert bootstrapped["ok"] is True
    assert (config_dir / "provider_models.json").exists()
    assert (web_dir / "index.html").exists()
    assert (home / "run/state").is_dir()
    assert (home / "secrets").stat().st_mode & 0o777 == 0o700
    assert (systemd_dir / "llm-manager-api.service").exists()
    assert (sudoers_dir / "llm-manager").stat().st_mode & 0o777 == 0o440
    assert (run_dir / "engine_launcher.py").stat().st_mode & 0o777 == 0o755
    assert (run_dir / "launch_llamacpp.py").stat().st_mode & 0o777 == 0o755
    assert (config_dir / "backend-pins.json").exists()

    (web_dir / "index.html").write_text("operator-owned")
    second = deployment.bootstrap(source_root, home, config_dir, web_dir, systemd_dir, sudoers_dir, run_dir)
    assert str(config_dir / "provider_models.json") in second["skipped_existing"]
    assert (web_dir / "index.html").read_text() == "operator-owned"
