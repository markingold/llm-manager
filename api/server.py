import os, json, subprocess, time, uuid, threading, signal, shutil
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import requests

# -----------------------------------------------------------------------------
# Paths / config
# -----------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = Path(os.getenv("SERVER_MODELS_DIR", os.getenv("MODELS_DIR", "/srv/2bananas/engines/models")))
CONFIG_PATH = ROOT / "model_configs.json"
ENV_PATH = ROOT / "secrets" / ".env"
SCRIPTS_DIR = ROOT / "app" / "src" / "llm_manager"
LOGS_DIR = ROOT / "run" / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# -----------------------------------------------------------------------------
# Defaults / env
# -----------------------------------------------------------------------------
DEFAULTS = {
  "LLM_CHAT_API_BASE":   os.getenv("LLM_CHAT_API_BASE",   "http://127.0.0.1:8500"),
  "LLM_INTENT_API_BASE": os.getenv("LLM_INTENT_API_BASE", "http://127.0.0.1:8501"),
  "LLM_SMALL_API_BASE":  os.getenv("LLM_SMALL_API_BASE",  "http://127.0.0.1:8502"),
  "SMART_ASSISTANT_URL": os.getenv("SMART_ASSISTANT_URL", "http://127.0.0.1:8100/command"),
  "CUDA_VISIBLE_DEVICES":os.getenv("CUDA_VISIBLE_DEVICES","0"),
  "PM2_CHAT":   os.getenv("PM2_CHAT",   "llm_chat"),
  "PM2_INTENT": os.getenv("PM2_INTENT", "llm_lora_intent"),
  "PM2_SMALL":  os.getenv("PM2_SMALL",  "llm_small"),
}

# -----------------------------------------------------------------------------
# Bind knobs (systemd-friendly)
# -----------------------------------------------------------------------------
LLM_MANAGER_HOST = os.getenv("LLM_MANAGER_HOST", "127.0.0.1")
LLM_MANAGER_PORT = int(os.getenv("LLM_MANAGER_PORT", os.getenv("PORT", "8101")))

app = FastAPI(title="LLM Manager API", version="1.2")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# -----------------------------------------------------------------------------
# Helpers: env, models, symlinks
# -----------------------------------------------------------------------------
def read_env() -> dict:
    env = DEFAULTS.copy()
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if not line.strip() or line.strip().startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

def write_env(env: dict):
    lines = [f"{k}={v}" for k, v in env.items()]
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text("\n".join(lines) + "\n")
    os.chmod(ENV_PATH, 0o600)

def list_intent_models():
    if not MODELS_DIR.exists():
        return []
    return sorted([p.name for p in MODELS_DIR.iterdir() if p.is_dir() and p.name.startswith("lora_")])

def list_non_intent_models():
    if not MODELS_DIR.exists():
        return []
    ignore = {"intent_active_model","chat_active_model","small_active_model"}
    return sorted([
        p.name for p in MODELS_DIR.iterdir()
        if p.is_dir() and p.name not in ignore and not p.name.startswith("lora_")
    ])

def _make_symlink(link: Path, target: Path):
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(target, target_is_directory=True)

def current_links():
    def tgt(p: Path):
        try:
            return str(p.resolve())
        except Exception:
            return None
    ck = MODELS_DIR / "chat_active_model"
    ik = MODELS_DIR / "intent_active_model"
    sk = MODELS_DIR / "small_active_model"
    return {
        "chat":   tgt(ck) if ck.exists() else None,
        "intent": tgt(ik) if ik.exists() else None,
        "small":  tgt(sk) if sk.exists() else None,
    }

# -----------------------------------------------------------------------------
# Helpers: systemd + pm2
# -----------------------------------------------------------------------------
def _pm2(cmd: list[str]):
    try:
        subprocess.run(["pm2"] + cmd, check=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"pm2 error: {e}")

def _systemctl(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    # Using sudo because your units are managed at system scope
    return subprocess.run(["sudo", "/bin/systemctl"] + args, capture_output=True, text=True, timeout=timeout)

def _systemctl_restart(unit: str):
    r = _systemctl(["restart", unit])
    if r.returncode != 0:
        raise HTTPException(status_code=500, detail=f"systemctl restart failed for {unit}: {(r.stderr or r.stdout)[-500:]}")

def _systemctl_start(unit: str):
    r = _systemctl(["start", unit])
    if r.returncode != 0:
        raise HTTPException(status_code=500, detail=f"systemctl start failed for {unit}: {(r.stderr or r.stdout)[-500:]}")

def _systemctl_stop(unit: str):
    r = _systemctl(["stop", unit])
    if r.returncode != 0:
        raise HTTPException(status_code=500, detail=f"systemctl stop failed for {unit}: {(r.stderr or r.stdout)[-500:]}")

def _systemctl_show(unit: str) -> dict:
    r = _systemctl(["show", unit, "--no-pager", "--property=Id,ActiveState,SubState,MainPID,ExecStart"])
    if r.returncode != 0:
        return {"error": (r.stderr or r.stdout).strip()}
    out = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out

def _journal_tail(unit: str, lines: int = 160) -> str:
    try:
        r = subprocess.run(
            ["sudo", "/bin/journalctl", "-u", unit, "-n", str(lines), "--no-pager"],
            capture_output=True,
            text=True,
            timeout=30
        )
        return r.stdout or r.stderr or ""
    except Exception as e:
        return f"[journalctl error] {e}"

def _is_listening(port: int) -> bool:
    try:
        r = subprocess.run(["bash", "-lc", f"ss -ltnp | grep -q ':{port} ' && echo yes || echo no"], capture_output=True, text=True)
        return (r.stdout or "").strip() == "yes"
    except Exception:
        return False

def _bounce_engine(mode: str):
    # Prefer systemd units when provided; fallback to pm2.
    a = os.getenv("SYSTEMD_LLM_A")
    b = os.getenv("SYSTEMD_LLM_B")
    c = os.getenv("SYSTEMD_LLM_C")
    unit = {"chat": a, "intent": b, "small": c}.get(mode)

    if unit:
        _systemctl_restart(unit)
        return {"ok": True, "method": "systemd", "unit": unit}

    # Legacy fallback
    env = read_env()
    pm2name = {"chat": env.get("PM2_CHAT"), "intent": env.get("PM2_INTENT"), "small": env.get("PM2_SMALL")}.get(mode)
    if pm2name:
        _pm2(["restart", pm2name])
        return {"ok": True, "method": "pm2", "proc": pm2name}

    return {"ok": False, "method": "none", "detail": "No SYSTEMD_LLM_* unit and no PM2_* proc configured"}

# -----------------------------------------------------------------------------
# Core: model switch
# -----------------------------------------------------------------------------
def switch_model(mode: str, model_dir: str, bounce: bool):
    # Historical UI used 'util' for the third slot -> treat as small.
    if mode == "util":
        mode = "small"
    if mode not in ("chat", "intent", "small"):
        raise HTTPException(400, "mode must be chat|intent|small|util")

    target = MODELS_DIR / model_dir
    if not target.exists():
        raise HTTPException(404, f"model dir not found: {target}")

    link = {
        "chat":   MODELS_DIR / "chat_active_model",
        "intent": MODELS_DIR / "intent_active_model",
        "small":  MODELS_DIR / "small_active_model",
    }[mode]

    _make_symlink(link, target)
    if bounce:
        _bounce_engine(mode)

    return {"ok": True, "link": str(link), "target": str(target), "mode": mode}

# -----------------------------------------------------------------------------
# Jobs: tiny runner for train/merge/convert
# -----------------------------------------------------------------------------
JOBS: dict[str, dict] = {}

def _tail(path: Path, n: int = 120) -> list[str]:
    if not path.exists():
        return []
    try:
        out = subprocess.check_output(["tail", "-n", str(n), str(path)], stderr=subprocess.DEVNULL)
        return out.decode("utf-8", errors="replace").splitlines()
    except Exception:
        return path.read_text(errors="replace").splitlines()[-n:]

def _launch_job(kind: str, args: dict) -> dict:
    job_id = uuid.uuid4().hex[:12]
    log_path = LOGS_DIR / f"{int(time.time())}_{kind}_{job_id}.log"

    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = read_env().get("CUDA_VISIBLE_DEVICES", "0")

    script_map = {
        "train":   SCRIPTS_DIR / "train_lora.py",
        "merge":   SCRIPTS_DIR / "merge_lora.py",
        "convert": SCRIPTS_DIR / "convert_lora.py",
    }
    script = script_map.get(kind)
    if not script or not script.exists():
        raise HTTPException(404, f"{kind}_script not found: {script}")

    cmd = None
    if kind == "train":
        if args.get("train_all"):
            cmd = ["python3", str(script), "--train_all"]
        else:
            mk = args.get("model_key")
            if not mk:
                raise HTTPException(400, "model_key required for train (or set train_all)")
            cmd = ["python3", str(script), "--model_key", mk]
        if args.get("force"):
            cmd.append("--force")
        if args.get("data_path"):
            cmd += ["--data_path", args["data_path"]]

    elif kind == "merge":
        if args.get("merge_all"):
            cmd = ["python3", str(script), "--merge_all"]
        else:
            mk = args.get("model_key")
            if not mk:
                raise HTTPException(400, "model_key required for merge (or set merge_all)")
            cmd = ["python3", str(script), "--model_key", mk]
        if args.get("force"):
            cmd.append("--force")

    elif kind == "convert":
        if args.get("convert_all"):
            cmd = ["python3", str(script), "--convert_all"]
        else:
            mk = args.get("model_key")
            if not mk:
                raise HTTPException(400, "model_key required for convert (or set convert_all)")
            cmd = ["python3", str(script), "--model_key", mk]
        if args.get("force"):
            cmd.append("--force")
    else:
        raise HTTPException(400, "kind must be train|merge|convert")

    with open(log_path, "w", buffering=1) as lf:
        lf.write(f"### {kind} job {job_id} @ {datetime.now().isoformat()}\n")
        lf.write("$ " + " ".join(cmd) + "\n\n")

    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=open(log_path, "a"), stderr=subprocess.STDOUT, env=env)
    JOBS[job_id] = {
        "id": job_id, "kind": kind, "args": args, "cmd": cmd,
        "pid": proc.pid, "start_ts": time.time(), "end_ts": None,
        "status": "running", "log": str(log_path),
    }

    def _watch():
        rc = proc.wait()
        j = JOBS.get(job_id)
        if j:
            j["end_ts"] = time.time()
            j["returncode"] = rc
            j["status"] = "ok" if rc == 0 else "error"

    threading.Thread(target=_watch, daemon=True).start()
    return JOBS[job_id]

# -----------------------------------------------------------------------------
# API models
# -----------------------------------------------------------------------------
class SwitchReq(BaseModel):
    model_config = {"protected_namespaces": ()}
    mode: str            # chat|intent|small|util
    model_dir: str
    bounce: bool = True

class Knobs(BaseModel):
    model_config = {"protected_namespaces": ()}
    LLM_CHAT_API_BASE: str | None = None
    LLM_INTENT_API_BASE: str | None = None
    LLM_SMALL_API_BASE: str | None = None
    SMART_ASSISTANT_URL: str | None = None
    CUDA_VISIBLE_DEVICES: str | None = None
    PM2_CHAT: str | None = None
    PM2_INTENT: str | None = None
    PM2_SMALL: str | None = None

class JobStart(BaseModel):
    model_config = {"protected_namespaces": ()}
    kind: str
    model_key: str | None = None
    force: bool | None = None
    train_all: bool | None = None
    merge_all: bool | None = None
    convert_all: bool | None = None
    data_path: str | None = None

# -----------------------------------------------------------------------------
# Routes: baseline (restore everything the old UI used)
# -----------------------------------------------------------------------------
@app.get("/health")
def health():
    env = read_env()
    info = {
        "ok": True,
        "time": datetime.utcnow().isoformat() + "Z",
        "active": current_links(),
        "pm2": {"chat": env.get("PM2_CHAT"), "intent": env.get("PM2_INTENT"), "small": env.get("PM2_SMALL")},
        "api_bases": {"chat": env.get("LLM_CHAT_API_BASE"), "intent": env.get("LLM_INTENT_API_BASE"), "small": env.get("LLM_SMALL_API_BASE")},
    }
    # quick non-fatal pings
    try:
        r = requests.get(env["LLM_CHAT_API_BASE"].rstrip("/") + "/health", timeout=1)
        info["chat_up"] = (r.status_code == 200)
    except Exception:
        info["chat_up"] = False
    try:
        r = requests.get(env["LLM_SMALL_API_BASE"].rstrip("/") + "/health", timeout=1)
        info["small_up"] = (r.status_code == 200)
    except Exception:
        info["small_up"] = False
    return info

@app.get("/system")
def system():
    try:
        load1, load5, load15 = os.getloadavg()
    except Exception:
        load1 = load5 = load15 = 0.0
    total, used, free = shutil.disk_usage("/")
    mem_total = mem_free = mem_avail = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                mem_total = int(line.split()[1]) * 1024
            if line.startswith("MemFree:"):
                mem_free = int(line.split()[1]) * 1024
            if line.startswith("MemAvailable:"):
                mem_avail = int(line.split()[1]) * 1024
    except Exception:
        pass
    return {
        "time": datetime.utcnow().isoformat() + "Z",
        "loadavg": [load1, load5, load15],
        "disk": {"total": total, "used": used, "free": free},
        "mem": {"total": mem_total, "free": mem_free, "available": mem_avail},
    }

@app.get("/models")
def models():
    return {
        "chat": list_non_intent_models(),
        "intent": list_intent_models(),
        "small": list_non_intent_models(),
        "active": current_links(),
    }

@app.post("/switch")
def switch(req: SwitchReq):
    return switch_model(req.mode, req.model_dir, req.bounce)

@app.get("/knobs")
def get_knobs():
    return read_env()

@app.post("/knobs")
def set_knobs(k: Knobs):
    env = read_env()
    for k_, v in k.dict().items():
        if v is not None:
            env[k_] = v
    write_env(env)
    return env

# Old UI calls /bounce/<mode>. Keep it, but make it systemd-aware.
@app.post("/bounce/{mode}")
def bounce(mode: str):
    if mode not in ("chat", "intent", "small"):
        raise HTTPException(400, "mode must be chat|intent|small")
    return _bounce_engine(mode)

# -----------------------------------------------------------------------------
# Test helpers (old UI expected these)
# -----------------------------------------------------------------------------
def _test_openai(base: str, model: str, q: str, max_tokens: int = 32):
    base = base.rstrip("/")
    r = requests.post(
        f"{base}/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": q}], "max_tokens": max_tokens},
        timeout=30
    )
    r.raise_for_status()
    j = r.json()
    text = j.get("choices", [{}])[0].get("message", {}).get("content")
    return {"ok": True, "answer": text, "raw": j}

@app.get("/test-chat")
def test_chat(q: str = "What is the capital of France?"):
    env = read_env()
    try:
        return _test_openai(env["LLM_CHAT_API_BASE"], "chat_active_model", q, max_tokens=64)
    except Exception as e:
        raise HTTPException(502, f"chat api error: {e}")

@app.get("/test-intent")
def test_intent(q: str = "Return ONLY the word OK."):
    env = read_env()
    try:
        return _test_openai(env["LLM_INTENT_API_BASE"], "intent_active_model", q, max_tokens=32)
    except Exception as e:
        raise HTTPException(502, f"intent api error: {e}")

@app.get("/test-util")
def test_util(q: str = "Say OK and nothing else."):
    env = read_env()
    try:
        return _test_openai(env["LLM_SMALL_API_BASE"], "small_active_model", q, max_tokens=32)
    except Exception as e:
        raise HTTPException(502, f"small api error: {e}")

# -----------------------------------------------------------------------------
# Jobs endpoints (train/merge/convert)
# -----------------------------------------------------------------------------
@app.get("/jobs")
def jobs_list():
    out = []
    for j in JOBS.values():
        out.append({
            "id": j["id"], "kind": j["kind"], "status": j["status"],
            "start_ts": j["start_ts"], "end_ts": j.get("end_ts"),
            "pid": j["pid"], "returncode": j.get("returncode"),
            "log": j["log"], "args": j["args"]
        })
    out.sort(key=lambda x: x["start_ts"], reverse=True)
    return out

@app.get("/jobs/{job_id}")
def jobs_detail(job_id: str, tail: int = 120):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return {**j, "tail": _tail(Path(j["log"]), n=tail)}

@app.post("/jobs")
def jobs_start(req: JobStart):
    return _launch_job(req.kind, req.dict())

@app.post("/jobs/{job_id}/cancel")
def jobs_cancel(job_id: str):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    try:
        os.kill(j["pid"], signal.SIGTERM)
        j["status"] = "cancelling"
        return {"ok": True}
    except ProcessLookupError:
        j["status"] = "ended"
        return {"ok": False, "detail": "process already ended"}

# -----------------------------------------------------------------------------
# Newer: Engines endpoints for the new Engine Controls UI
# -----------------------------------------------------------------------------
def _engine_def(mode: str) -> dict:
    env = read_env()
    if mode == "chat":
        return {"mode": "chat", "unit": os.getenv("SYSTEMD_LLM_A") or "", "base": env["LLM_CHAT_API_BASE"], "port": 8500}
    if mode == "intent":
        return {"mode": "intent", "unit": os.getenv("SYSTEMD_LLM_B") or "", "base": env["LLM_INTENT_API_BASE"], "port": 8501}
    if mode == "small":
        return {"mode": "small", "unit": os.getenv("SYSTEMD_LLM_C") or "", "base": env["LLM_SMALL_API_BASE"], "port": 8502}
    raise HTTPException(400, "mode must be chat|intent|small")

@app.get("/engines/status")
def engines_status():
    chat = _engine_def("chat")
    intent = _engine_def("intent")
    small = _engine_def("small")

    def pack(e: dict) -> dict:
        unit = e["unit"]
        return {
            "unit": unit or "(not set)",
            "base": e["base"],
            "port": e["port"],
            "listening": _is_listening(e["port"]),
            "systemd": _systemctl_show(unit) if unit else {"error": "SYSTEMD unit not configured"},
            "active": current_links().get(e["mode"]),
        }

    return {"chat": pack(chat), "intent": pack(intent), "small": pack(small), "time": datetime.utcnow().isoformat() + "Z"}

@app.post("/engines/{mode}/{action}")
def engines_action(mode: str, action: str):
    e = _engine_def(mode)
    unit = e["unit"]
    if not unit:
        raise HTTPException(400, f"SYSTEMD unit not configured for {mode} (SYSTEMD_LLM_*)")

    if action == "start":
        _systemctl_start(unit)
    elif action == "stop":
        _systemctl_stop(unit)
    elif action == "restart":
        _systemctl_restart(unit)
    else:
        raise HTTPException(400, "action must be start|stop|restart")

    return {"ok": True, "mode": mode, "action": action, "unit": unit}

@app.post("/engines/solo/{mode}")
def engines_solo(mode: str):
    # Stop other units first, then start requested.
    target = _engine_def(mode)
    units = {
        "chat": os.getenv("SYSTEMD_LLM_A") or "",
        "intent": os.getenv("SYSTEMD_LLM_B") or "",
        "small": os.getenv("SYSTEMD_LLM_C") or "",
    }
    if not units.get(mode):
        raise HTTPException(400, f"SYSTEMD unit not configured for {mode}")

    for m, u in units.items():
        if m != mode and u:
            _systemctl_stop(u)

    _systemctl_start(units[mode])
    return {"ok": True, "solo": mode, "stopped": [m for m in units if m != mode and units[m]], "started": units[mode]}

@app.get("/engines/{mode}/logs")
def engines_logs(mode: str, lines: int = 160):
    e = _engine_def(mode)
    unit = e["unit"]
    if not unit:
        raise HTTPException(400, f"SYSTEMD unit not configured for {mode}")
    return {"ok": True, "mode": mode, "unit": unit, "tail": _journal_tail(unit, lines=lines)}

# -----------------------------------------------------------------------------
def main():
    uvicorn.run(app, host=LLM_MANAGER_HOST, port=LLM_MANAGER_PORT)

if __name__ == "__main__":
    main()
