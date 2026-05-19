#!/usr/bin/env python3
"""
launch_tgw.py - Normalize and launch text-generation-webui for a slot.

Default behavior is slot-safe and API-only (no WebUI). Standalone WebUI mode
can be enabled explicitly with --webui.
"""

import argparse
import glob
import os
import pathlib
import re
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "api"))
from model_inspector import detect_kind, detect_loader

MODELS_DIR = os.getenv(
    "SERVER_MODELS_DIR",
    os.getenv("MODELS_DIR", "/srv/2bananas/engines/models"),
)
ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / "secrets" / ".env"
GLOBAL_ENV_PATH = pathlib.Path(
    os.getenv("LLM_MANAGER_GLOBAL_ENV_PATH", "/srv/2bananas/secrets/global.env")
)


def read_env_file(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def read_runtime_env() -> dict:
    env = dict(os.environ)

    # Shared global env keys are preferred when not already set by process env.
    for key, value in read_env_file(GLOBAL_ENV_PATH).items():
        if value and key not in env:
            env[key] = value

    # Project-local secrets provide fallback values when global/env do not define one.
    for key, value in read_env_file(ENV_PATH).items():
        if key not in env:
            env[key] = value
    return env


def env_flag(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def env_int(value: str | None, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except Exception:
        return default
    return parsed if parsed > 0 else default


def _pid_cmdline(pid: int) -> str:
    path = pathlib.Path(f"/proc/{pid}/cmdline")
    if not path.exists():
        return ""
    try:
        raw = path.read_bytes()
    except Exception:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()


def _listening_pids(port: int) -> set[int]:
    try:
        proc = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True, check=False)
    except Exception:
        return set()

    pids: set[int] = set()
    port_marker = f":{int(port)}"
    for line in (proc.stdout or "").splitlines():
        if port_marker not in line:
            continue
        for pid_match in re.findall(r"pid=(\d+)", line):
            try:
                pids.add(int(pid_match))
            except Exception:
                continue
    return pids


def _wait_pid_exit(pid: int, timeout_s: float) -> bool:
    end = time.monotonic() + max(0.0, float(timeout_s))
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.2)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _cleanup_stale_port_owners(port: int, term_timeout_s: float) -> None:
    pids = sorted(_listening_pids(port))
    if not pids:
        return

    for pid in pids:
        cmdline = _pid_cmdline(pid)
        if not cmdline:
            continue

        is_tgw_like = (
            "text-generation-webui" in cmdline
            or " server.py" in f" {cmdline}"
            or cmdline.endswith("server.py")
        )
        if not is_tgw_like:
            print(f"[launch-tgw] guardrail: port {port} occupied by non-TGW pid={pid}; leaving untouched", flush=True)
            continue

        print(f"[launch-tgw] guardrail: terminating stale TGW pid={pid} on port {port}", flush=True)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError:
            print(f"[launch-tgw] guardrail: no permission to terminate pid={pid}", flush=True)
            continue

        if _wait_pid_exit(pid, term_timeout_s):
            continue

        print(f"[launch-tgw] guardrail: forcing SIGKILL on stale pid={pid}", flush=True)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
        except PermissionError:
            print(f"[launch-tgw] guardrail: no permission to SIGKILL pid={pid}", flush=True)
            continue
        _wait_pid_exit(pid, 2.0)


def _candidate_exllama_lock_paths(runtime_env: dict) -> list[pathlib.Path]:
    explicit = str(runtime_env.get("TGW_EXLLAMA_LOCK_PATHS") or "").strip()
    if explicit:
        rows = [token.strip() for token in explicit.split(",") if token.strip()]
        return [pathlib.Path(row).expanduser() for row in rows]

    default_globs = [
        "~/.cache/torch_extensions/*/exllamav2_ext/lock",
        "~/.cache/torch_extensions/*/exllamav2_ext*/lock",
    ]
    found: list[pathlib.Path] = []
    seen: set[str] = set()
    for pattern in default_globs:
        for row in glob.glob(os.path.expanduser(pattern)):
            if row in seen:
                continue
            seen.add(row)
            found.append(pathlib.Path(row))
    return found


def _cleanup_stale_exllama_locks(runtime_env: dict) -> None:
    stale_seconds = env_int(runtime_env.get("TGW_EXLLAMA_LOCK_STALE_SECONDS"), 300)
    remove_any = env_flag(runtime_env.get("TGW_EXLLAMA_LOCK_FORCE_REMOVE"), False)

    now = time.time()
    for lock_path in _candidate_exllama_lock_paths(runtime_env):
        if not lock_path.exists():
            continue
        try:
            age_s = now - lock_path.stat().st_mtime
        except Exception:
            age_s = float(stale_seconds + 1)

        if not remove_any and age_s < float(stale_seconds):
            print(
                f"[launch-tgw] guardrail: keeping recent ExLlama lock {lock_path} (age={int(max(age_s, 0))}s)",
                flush=True,
            )
            continue

        try:
            lock_path.unlink(missing_ok=True)
            print(
                f"[launch-tgw] guardrail: removed ExLlama lock {lock_path} (age={int(max(age_s, 0))}s)",
                flush=True,
            )
        except Exception as e:
            print(f"[launch-tgw] guardrail: failed to remove lock {lock_path}: {e}", flush=True)


def _apply_startup_guardrails(api_port: int, runtime_env: dict) -> None:
    if not env_flag(runtime_env.get("TGW_STARTUP_GUARDRAILS"), True):
        return

    if env_flag(runtime_env.get("TGW_GUARDRAIL_CLEAN_PORT"), True):
        term_timeout_s = env_int(runtime_env.get("TGW_GUARDRAIL_TERM_TIMEOUT_SECONDS"), 8)
        _cleanup_stale_port_owners(api_port, float(term_timeout_s))

    if env_flag(runtime_env.get("TGW_GUARDRAIL_CLEAN_EXLLAMA_LOCKS"), True):
        _cleanup_stale_exllama_locks(runtime_env)


def main():
    parser = argparse.ArgumentParser(description="Launch text-generation-webui with auto loader detection")
    parser.add_argument("--api-port", required=True, type=int, help="API listen port")
    parser.add_argument("--model", required=True, help="Model directory name or symlink in model dir")
    parser.add_argument("--max-seq-len", required=True, help="Context length")
    parser.add_argument("--listen-host", default="127.0.0.1", help="Listen host")
    parser.add_argument("--model-dir", default=MODELS_DIR, help="Models directory")
    webui_mode = parser.add_mutually_exclusive_group()
    webui_mode.add_argument("--webui", action="store_true", help="Enable TGW WebUI for this process")
    webui_mode.add_argument("--no-webui", action="store_true", help="Force API-only mode for this process")
    args = parser.parse_args()

    runtime_env = read_runtime_env()
    if args.webui:
        tgw_webui_enabled = True
    elif args.no_webui:
        tgw_webui_enabled = False
    else:
        tgw_webui_enabled = env_flag(
            runtime_env.get("TGW_WEBUI_ENABLED", runtime_env.get("TGW_CHAT_WEBUI_ENABLED")),
            False,
        )

    tgw_webui_port = env_int(
        runtime_env.get("TGW_WEBUI_PORT", runtime_env.get("TGW_CHAT_WEBUI_PORT")),
        7860,
    )
    tgw_webui_bind_host = str(
        runtime_env.get("TGW_WEBUI_BIND_HOST", runtime_env.get("TGW_CHAT_WEBUI_BIND_HOST"))
        or args.listen_host
    ).strip() or args.listen_host

    _apply_startup_guardrails(int(args.api_port), runtime_env)

    model_path = pathlib.Path(args.model_dir) / args.model
    if not model_path.exists():
        raise SystemExit(f"[launch-tgw] model path not found: {model_path}")
    resolved = model_path.resolve()

    kind = detect_kind(resolved)
    loader = detect_loader(kind)

    print(
        f"[launch-tgw] model={args.model} resolved={resolved.name} kind={kind} loader={loader} webui={'on' if tgw_webui_enabled else 'off'}",
        flush=True,
    )

    cmd = [
        sys.executable,
        "server.py",
        "--api",
        "--api-port",
        str(int(args.api_port)),
        "--listen",
        "--listen-host",
        tgw_webui_bind_host,
        "--extensions",
        "openai",
        "--old-colors",
        "--loader",
        loader,
        "--model-dir",
        str(args.model_dir),
        "--model",
        args.model,
    ]

    if tgw_webui_enabled:
        cmd += ["--listen-port", str(tgw_webui_port)]
    else:
        cmd += ["--nowebui"]

    if loader == "llama.cpp":
        cmd += ["--ctx-size", str(args.max_seq_len)]
    else:
        cmd += ["--max_seq_len", str(args.max_seq_len)]

    os.execv(sys.executable, cmd)


if __name__ == "__main__":
    main()
