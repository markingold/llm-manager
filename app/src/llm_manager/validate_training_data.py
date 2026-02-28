import os
import json
import time
import requests
from pathlib import Path

DATA_DIR = Path("./data")
OUTPUT_DIR = Path("./results")
OUTPUT_DIR.mkdir(exist_ok=True)

SMART_ASSISTANT_URL = "http://localhost:8100/command"
MODULE_CALL_URL = "http://localhost:8100/command_internal"
LLM_VERIFIER_URL = "http://localhost:8500/v1/chat/completions"

def list_jsonl_files():
    return [f for f in DATA_DIR.glob("*.jsonl")]

def choose_file(files):
    print("Available JSONL files:")
    for i, file in enumerate(files):
        print(f"{i+1}. {file.name}")
    choice = int(input("\nChoose a file to validate (number): "))
    return files[choice - 1]

def choose_logging_mode():
    print("\nLogging Options:")
    print("1. Log only FAILURES")
    print("2. Log both PASSES and FAILURES")
    choice = input("Select logging mode (1 or 2): ").strip()
    return choice == "2"

def choose_routing_mode():
    print("\nRouting Options:")
    print("1. Test using Smart Assistant only (/command)")
    print("2. Test module call only (/command_internal)")
    print("3. Test both and compare")
    choice = input("Select route mode (1, 2, or 3): ").strip()
    if choice == "1":
        return "assistant"
    elif choice == "2":
        return "module"
    else:
        return "both"

def send_to_assistant(prompt):
    try:
        print("🧠 Sending to Smart Assistant...")
        response = requests.post(SMART_ASSISTANT_URL, json={"command": prompt}, timeout=10)
        data = response.json()
        if isinstance(data, dict) and "text" in data:
            print(f"💬 Assistant said: {data['text']}")
        else:
            print(f"💬 Assistant response: {data}")
        return data
    except Exception as e:
        print(f"❌ Assistant call failed: {e}")
        return {"error": str(e)}

def call_module_directly(module, function, args):
    try:
        print("🔧 Calling Smart Assistant module via /command_internal...")
        payload = {
            "module": module,
            "function": function,
            "args": args
        }
        response = requests.post(MODULE_CALL_URL, json=payload, timeout=10)
        data = response.json()
        if isinstance(data, dict) and "text" in data:
            print(f"⚙️  Function result: {data['text']}")
        else:
            print(f"⚙️  Function response: {data}")
        return data
    except Exception as e:
        print(f"❌ Remote module call failed: {e}")
        return {"error": str(e)}

def verify_result_with_llm(prompt, assistant_response, direct_response):
    system_prompt = (
        "You are a validator. Your task is to determine if the assistant response "
        "and the direct function result both correctly and consistently respond to the user's command.\n"
        "Respond ONLY with a JSON object like: {\"pass\": true, \"reason\": \"...\"}"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps({
            "prompt": prompt,
            "assistant_response": assistant_response,
            "function_response": direct_response
        }, indent=2)}
    ]
    try:
        print("🧪 Verifying with LLM...")
        response = requests.post(LLM_VERIFIER_URL, json={
            "model": "local-validator",
            "messages": messages,
            "max_tokens": 200
        }, timeout=10)
        result = response.json()
        return json.loads(result["choices"][0]["message"]["content"])
    except Exception as e:
        print(f"❌ Verifier call failed: {e}")
        return {"pass": False, "reason": f"LLM verification error: {e}"}

def main():
    files = list_jsonl_files()
    if not files:
        print("❌ No .jsonl files found in /data.")
        return

    selected = choose_file(files)
    log_all = choose_logging_mode()
    route_mode = choose_routing_mode()

    print(f"\n🔍 Validating: {selected.name} | Mode: {route_mode}\n")

    output_path = OUTPUT_DIR / f"validated_{selected.stem}_{int(time.time())}.jsonl"
    results = []
    failed = []
    passed = []

    with open(selected, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            print(f"\n🔄 Validating prompt {idx + 1}...")
            try:
                pair = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"❌ JSON decode error on line {idx+1}: {e}")
                continue

            prompt = pair.get("prompt")
            # --- handle "response" whether it arrives as a string or a dict ---
            raw_resp = pair.get("response", {})
            if isinstance(raw_resp, str):
                try:
                    response = json.loads(raw_resp)          # was the old behaviour
                except json.JSONDecodeError as e:
                    print(f"❌ Unable to decode response JSON on line {idx+1}: {e}")
                    response = {}
            elif isinstance(raw_resp, dict):
                response = raw_resp                          # new: already a dict
            else:
                response = {}                                # fall-back safety
            module = response.get("module")
            function = response.get("function")
            args = response.get("args", {})

            assistant_resp = {}
            direct_resp = {}

            if route_mode in ("assistant", "both"):
                assistant_resp = send_to_assistant(prompt)
            if route_mode in ("module", "both"):
                direct_resp = call_module_directly(module, function, args)

            if route_mode == "assistant":
                direct_resp = {"skipped": True}
            elif route_mode == "module":
                assistant_resp = {"skipped": True}

            verdict = verify_result_with_llm(prompt, assistant_resp, direct_resp)

            entry = {
                "prompt": prompt,
                "expected": response,
                "assistant_response": assistant_resp,
                "function_response": direct_resp,
                "verdict": verdict
            }

            results.append(entry)

            if not verdict.get("pass", False):
                failed.append({
                    "prompt": prompt,
                    "reason": verdict.get("reason", "Unknown"),
                    "assistant_response": assistant_resp,
                    "function_response": direct_resp
                })
            else:
                passed.append(entry)

            print(f"✅ {prompt} → Pass: {verdict.get('pass')}")

    # Write filtered results
    with open(output_path, "w", encoding="utf-8") as f_out:
        for item in results:
            if log_all or not item["verdict"].get("pass", False):
                f_out.write(json.dumps(item) + "\n")

        summary = {
            "summary": {
                "file": selected.name,
                "route_mode": route_mode,
                "total": len(results),
                "passed": len(passed),
                "failed": len(failed),
                "failed_prompts": failed
            }
        }
        f_out.write(json.dumps(summary, indent=2) + "\n")

    print(f"\n📁 Results saved to: {output_path.resolve()}")
    print(f"📊 Passed: {len(passed)} | Failed: {len(failed)}")

if __name__ == "__main__":
    main()
