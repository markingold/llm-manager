from fastapi import APIRouter
from fastapi.responses import JSONResponse
from typing import Dict, Any, List, Optional
import json, re, pathlib

router = APIRouter()

MODELS_DIR = pathlib.Path(os.getenv('SERVER_MODELS_DIR', os.getenv('MODELS_DIR', '/srv/2bananas/engines/models')))

_QWEN_RX = re.compile(r"qwen", re.I)
_LLAMA_RX = re.compile(r"llama", re.I)
_MISTRAL_RX = re.compile(r"mistral|mixtral", re.I)
_EXL2_RX = re.compile(r"exl2", re.I)

def _guess_chat_template(name: str, model_dir: pathlib.Path) -> Optional[str]:
    # Transformers may include chat_template in tokenizer_config.json or config.json
    for fname in ("tokenizer_config.json", "config.json"):
        f = model_dir / fname
        if f.is_file():
            try:
                data = json.loads(f.read_text(encoding="utf-8", errors="ignore"))
                if data.get("chat_template"):
                    return "custom"
            except Exception:
                pass
    n = name.lower()
    if _QWEN_RX.search(n): return "qwen2"
    if _LLAMA_RX.search(n): return "llama3"
    if _MISTRAL_RX.search(n): return "mistral"
    return None

def _detect_kind(model_dir: pathlib.Path) -> str:
    name = model_dir.name
    if _EXL2_RX.search(name):
        return "exllamav2"
    if (model_dir/"config.json").exists() or (model_dir/"tokenizer.json").exists():
        return "transformers"
    # Fallback: look for "exl2" anywhere under the folder
    for p in model_dir.rglob("*"):
        if "exl2" in p.name.lower():
            return "exllamav2"
    return "transformers"

def _quant_options(kind: str, name: str) -> List[str]:
    if kind == "transformers":
        return ["4bit","8bit","fp16","fp32"]
    # exllamav2: baked pack, try to hint
    opts: List[str] = []
    if re.search(r"6[_\.]?5", name): opts.append("6.5bpw")
    if re.search(r"4bit|int4|4[_\.]?0", name, re.I): opts.append("4bit")
    if re.search(r"8bit|int8|8[_\.]?0", name, re.I): opts.append("8bit")
    return opts or ["pack"]

def _list_models() -> Dict[str, Any]:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(MODELS_DIR.iterdir()):
        if not p.is_dir():
            continue
        try:
            next(p.iterdir())
        except StopIteration:
            continue
        name = p.name
        kind = _detect_kind(p)
        quant = _quant_options(kind, name)
        tmpl = _guess_chat_template(name, p)
        items.append({
            "name": name,
            "path": str(p),
            "kind": kind,
            "quant": quant,
            "chat_template": tmpl
        })
    names = [m["name"] for m in items]
    return {
        "chat": names,
        "intent": names,
        "util": names,    # a.k.a. llm-c
        "meta": { m["name"]: m for m in items },
        "active": {"chat": None, "intent": None, "util": None}
    }

@router.get("/models")
def get_models_root():
    return JSONResponse(_list_models())

@router.get("/api/models")
def get_models_compat():
    return JSONResponse(_list_models())
