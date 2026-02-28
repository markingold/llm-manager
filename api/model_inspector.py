import json, os, re, pathlib

MODELS_DIR = os.getenv("SERVER_MODELS_DIR", os.getenv("MODELS_DIR", "/srv/2bananas/engines/models"))

# Very light heuristics; safe defaults when unsure
CHAT_TEMPLATE_GUESS = [
    (re.compile(r'(?i)qwen'), "chatml"),
    (re.compile(r'(?i)llama[-\s_]?3'), "llama-3"),
    (re.compile(r'(?i)llama'), "llama"),
    (re.compile(r'(?i)mistral|mixtral'), "mistral"),
    (re.compile(r'(?i)phi[-\s_]?4|phi'), "phi"),
]

def _looks_exl2(model_path: pathlib.Path) -> bool:
    # Any *.exl2 shard present?
    return any(p.suffix.lower()==".exl2" for p in model_path.glob("**/*"))

def _looks_transformer(model_path: pathlib.Path) -> bool:
    # HF Transformers style: config.json exists
    return (model_path / "config.json").exists()

def _guess_chat_template_from_files(model_path: pathlib.Path):
    # Try tokenizer_config.json -> chat_template
    tok_cfg = model_path / "tokenizer_config.json"
    if tok_cfg.exists():
        try:
            data = json.loads(tok_cfg.read_text(encoding="utf-8"))
            tpl = data.get("chat_template")
            if isinstance(tpl, str) and tpl.strip():
                return "from-tokenizer", tpl
        except Exception:
            pass
    return None, None

def _guess_chat_template_from_name(model_name: str):
    for rx, tpl in CHAT_TEMPLATE_GUESS:
        if rx.search(model_name):
            return tpl
    return "auto"

def inspect_one(model_name: str):
    base = pathlib.Path(MODELS_DIR)
    model_path = base / model_name
    exists = model_path.exists()
    kind = "unknown"
    loader = None
    quant_options = []
    chat_template_mode = "auto"
    chat_template_raw = None

    if exists:
        if _looks_exl2(model_path):
            kind = "exl2"
            loader = "exllamav2"
            quant_options = ["fp16"]  # exllamav2 handles quant internally; no bnb flags
            chat_template_mode = _guess_chat_template_from_name(model_name)
        elif _looks_transformer(model_path):
            kind = "transformers"
            loader = "transformers"
            # We support bitsandbytes suggestions if installed
            quant_options = ["fp16", "fp32", "bnb-8bit", "bnb-4bit"]
            mode, tpl = _guess_chat_template_from_files(model_path)
            if mode == "from-tokenizer":
                chat_template_mode = "tokenizer"
                chat_template_raw = tpl
            else:
                chat_template_mode = _guess_chat_template_from_name(model_name)
        else:
            kind = "unknown"
            loader = "transformers"
            quant_options = ["fp16", "fp32", "bnb-8bit", "bnb-4bit"]
            chat_template_mode = "auto"

    # Compose suggested TGW args (no PM2 here)
    # Common TGW flags we already use in ecosystems:
    common = [
        "--extensions", "openai",
        "--old-colors",
        "--model-dir", "/srv/2bananas/engines/models",
        "--model", model_name,
        "--listen", "--listen-host", "127.0.0.1",
        "--nowebui",
    ]
    loader_part = ["--loader", loader] if loader else []
    chat_part = []
    if chat_template_mode == "tokenizer":
        # Text Generation WebUI supports --chat-template "auto" or file paths.
        # When tokenizer contains a template, "auto" usually picks it up; expose raw for visibility.
        chat_part = ["--chat-template", "auto"]
    else:
        chat_part = ["--chat-template", chat_template_mode]  # typically "auto"/"chatml"/"llama-3"/etc.

    # Quant suggestions (for transformers loader only)
    quant_suggestions = []
    if kind == "transformers":
        quant_suggestions.append({"name":"fp16","args":[]})
        quant_suggestions.append({"name":"fp32","args":["--dtype","float32"]})
        quant_suggestions.append({"name":"bnb-8bit","args":["--load-in-8bit"]})
        quant_suggestions.append({"name":"bnb-4bit","args":["--load-in-4bit"]})

    suggestion = {
        "exists": exists,
        "model_name": model_name,
        "path": str(model_path),
        "kind": kind,
        "loader": loader,
        "quant_options": quant_options,
        "chat_template_mode": chat_template_mode,
        "chat_template_raw": chat_template_raw,
        "tgw_common_args": common,
        "tgw_loader_args": loader_part,
        "tgw_chat_args": chat_part,
        "tgw_quant_presets": quant_suggestions
    }
    return suggestion

def batch(names):
    return {name: inspect_one(name) for name in names}
