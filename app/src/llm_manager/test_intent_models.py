#!/usr/bin/env python3
"""
Benchmark every LoRA intent model against a fixed set of test commands.

• Cycles through each directory named  lora_*  in  text-generation-webui/user_data/models
• Activates the model via the existing symlink-and-pm2 switcher
• Waits until the model reports ready (or times out and just sleeps)
• Sends each command (from data/test_commands.txt by default) to the
  Smart-Assistant /command endpoint
• Asks an external validator LLM (on :5001) whether the assistant reply
  fulfills the command
• Logs results to   results/<lora_key>_validate_<timestamp>.jsonl
  and writes an overall summary JSON

Adjust endpoints or time-outs with CLI flags.
"""

import argparse
import json
import os
import time
import requests
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
#  Import helpers from your existing switch-model script
# --------------------------------------------------------------------------- #
try:
    from switch_model import (
        list_intent_models,
        switch,
        resolve_display_name,
    )

except ModuleNotFoundError as e:
    sys.exit("❌ Could not import lora_switcher.py - make sure it is on PYTHONPATH.")

# --------------------------------------------------------------------------- #
#  Default paths & constants
# --------------------------------------------------------------------------- #
DEFAULT_CMD_FILE      = Path("data/test_commands.txt")
DEFAULT_OUT_DIR       = Path("results")

DEFAULT_ASSISTANT_URL = "http://localhost:8100/command"
DEFAULT_VERIFIER_URL  = "http://localhost:8500/v1/chat/completions"
DEFAULT_STATUS_URL    = "http://localhost:8500/status"    # Oobabooga API status

DEFAULT_ASSIST_TIMEOUT = 15   # seconds
DEFAULT_VERIFY_TIMEOUT = 15
DEFAULT_STATUS_TIMEOUT = 120  # max seconds to wait for model report
DEFAULT_FALLBACK_SLEEP = 15   # sleep if status check fails

# --------------------------------------------------------------------------- #
#  Argument parsing
# --------------------------------------------------------------------------- #
parser = argparse.ArgumentParser(description="Benchmark LoRA intent models.")
parser.add_argument(
    "-c", "--commands", type=Path, default=DEFAULT_CMD_FILE,
    help="Path to file with one test command per line (default: %(default)s)",
)
parser.add_argument(
    "-o", "--output-dir", type=Path, default=DEFAULT_OUT_DIR,
    help="Folder to store result files (default: %(default)s)",
)
parser.add_argument(
    "-m", "--models", metavar="LIST", default="",
    help="Comma-separated list of lora_* model keys to test "
         "(default: discover all lora_* dirs).",
)
parser.add_argument(
    "--assistant-url", default=DEFAULT_ASSISTANT_URL,
    help="Smart-Assistant /command endpoint (default: %(default)s)",
)
parser.add_argument(
    "--verifier-url", default=DEFAULT_VERIFIER_URL,
    help="LLM verifier endpoint (default: %(default)s)",
)
parser.add_argument(
    "--status-url", default=DEFAULT_STATUS_URL,
    help="URL that returns JSON containing the currently-loaded model "
         "(default: %(default)s)",
)
parser.add_argument(
    "--status-timeout", type=int, default=DEFAULT_STATUS_TIMEOUT,
    help="Max seconds to wait for model ready (default: %(default)s)",
)
parser.add_argument(
    "--sleep-fallback", type=int, default=DEFAULT_FALLBACK_SLEEP,
    help="Seconds to sleep if status endpoint is unavailable (default: %(default)s)",
)
parser.add_argument(
    "--log-all", action="store_true",
    help="Log PASSES as well as FAILURES (default: only failures)",
)

args = parser.parse_args()

args.output_dir.mkdir(exist_ok=True)


# --------------------------------------------------------------------------- #
#  Helper functions
# --------------------------------------------------------------------------- #
def load_commands(path: Path) -> list[str]:
    if not path.exists():
        sys.exit(f"❌ Command file not found: {path}")
    cmds: list[str] = []
    with path.open() as f:
        for raw in f:
            stripped = raw.strip()
            if stripped and not stripped.startswith("#"):
                cmds.append(stripped)
    if not cmds:
        sys.exit("❌ No commands found in file.")
    return cmds


def send_to_assistant(cmd: str) -> dict:
    try:
        r = requests.post(
            args.assistant_url,
            json={"command": cmd},
            timeout=DEFAULT_ASSIST_TIMEOUT,
        )
        return r.json()
    except Exception as e:
        return {"error": f"assistant_call_failed: {e}"}


def verify_with_llm(cmd: str, assistant_resp: dict) -> dict:
    system_prompt = (
        "You are a validator. Decide whether 'assistant_response' correctly "
        "fulfills 'command'. Respond ONLY with JSON: "
        '{"pass": true/false, "reason": "..."}'
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(
            {"command": cmd, "assistant_response": assistant_resp}, indent=2
        )},
    ]
    try:
        r = requests.post(
            args.verifier_url,
            json={
                "model": "local-validator",
                "messages": messages,
                "max_tokens": 200,
            },
            timeout=DEFAULT_VERIFY_TIMEOUT,
        )
        content = r.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        return {"pass": False, "reason": f"LLM verification error: {e}"}


# --------------------------------------------------------------------------- #
#  Wait helper – now a simple timer
# --------------------------------------------------------------------------- #
def wait_for_model_ready(_target_key: str) -> None:
    """
    Fixed warm-up pause.

    We’re no longer polling /status. After pm2 restarts the intent model
    server, just wait a set number of seconds and assume the model is live.
    The duration comes from --sleep-fallback (default 15 s).
    """
    secs = args.sleep_fallback
    print(f"⏳ Waiting {secs}s for model to load …")
    time.sleep(secs)


def log_entry(fh, entry: dict):
    if args.log_all or not entry["verdict"].get("pass", True):
        fh.write(json.dumps(entry) + "\n")


# --------------------------------------------------------------------------- #
#  Main benchmark loop
# --------------------------------------------------------------------------- #
def main():
    commands = load_commands(args.commands)

    if args.models:
        models = [m.strip() for m in args.models.split(",") if m.strip()]
    else:
        models = list_intent_models()

    if not models:
        sys.exit("❌ No LoRA models to test.")

    timestamp = int(time.time())
    summary = []

    for idx, model_key in enumerate(models, 1):
        print(f"\n=== [{idx}/{len(models)}] Switching to {model_key} ===")
        switch(model_key, "intent")
        wait_for_model_ready(model_key)

        out_file = (
            args.output_dir / f"{model_key}_validate_{timestamp}.jsonl"
        )
        total = passed = failed = 0

        with out_file.open("w") as fout:
            for cmd in commands:
                assistant_resp = send_to_assistant(cmd)
                verdict = verify_with_llm(cmd, assistant_resp)

                entry = {
                    "model": model_key,
                    "command": cmd,
                    "assistant_response": assistant_resp,
                    "verdict": verdict,
                }
                log_entry(fout, entry)

                total += 1
                if verdict.get("pass"):
                    passed += 1
                else:
                    failed += 1

        summary.append(
            {
                "model": model_key,
                "display_name": resolve_display_name(model_key),
                "results_file": out_file.name,
                "total": total,
                "passed": passed,
                "failed": failed,
            }
        )
        print(
            f"📝 {model_key}: {passed}/{total} passed "
            f"(results → {os.path.relpath(out_file, start=Path.cwd())})"
        )

    summary_path = args.output_dir / f"summary_{timestamp}.json"
    with summary_path.open("w") as s:
        json.dump(summary, s, indent=2)

        print("\n🏁 Benchmark complete.")
        try:
            display_path = out_file.resolve().relative_to(Path.cwd())
        except ValueError:
            # Fallback: just show the absolute path
            display_path = out_file.resolve()

        print(
            f"📝 {model_key}: {passed}/{total} passed "
            f"(results → {display_path})"
        )
        print("📊 Summary →", os.path.relpath(summary_path, start=Path.cwd()))



if __name__ == "__main__":
    main()
