#!/usr/bin/env python3
"""
Run a baseline smoke suite across local chat/small/intent model inventories.

Outputs:
- docs/reports/baseline_local_models_<timestamp>.json
- docs/reports/baseline_local_models_<timestamp>.md

This runner is intentionally strict:
- JSON test accepts only exact JSON object output (or a fenced JSON block only)
- Arithmetic tests require exact final integer values
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import urlopen


BASE_URL = "http://127.0.0.1:8101"
REPORTS_DIR = Path("docs/reports")


@dataclass
class TestDef:
    test_id: str
    prompt: str
    mode: str


@dataclass
class PromotionGates:
    promote_min_pass_rate: float = 0.9
    conditional_min_pass_rate: float = 0.7
    hard_tests_must_pass: tuple[str, ...] = (
        "arithmetic_739x481",
        "arithmetic_order_ops",
        "arithmetic_913x47",
        "arithmetic_order_ops_v2",
    )


def _http_json(url: str, *, method: str = "GET", body: dict[str, Any] | None = None, timeout: int = 90) -> dict[str, Any]:
    import urllib.request

    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _extract_output_text(payload: dict[str, Any]) -> str:
    answer = payload.get("answer")
    if isinstance(answer, str):
        return answer

    raw = payload.get("raw")
    if isinstance(raw, dict):
        payload = raw

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        content = message.get("content")
        if isinstance(content, str):
            return content
    return ""


def _extract_json_candidate(text: str) -> str | None:
    trimmed = text.strip()

    fenced = re.fullmatch(r"```json\s*(\{[\s\S]*\})\s*```", trimmed, flags=re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()

    raw = re.fullmatch(r"(\{[\s\S]*\})", trimmed)
    if raw:
        return raw.group(1).strip()

    return None


def _check_strict_json(text: str) -> tuple[bool, dict[str, Any]]:
    candidate = _extract_json_candidate(text)
    if candidate is None:
        return False, {"reason": "output_not_pure_json_or_json_fence"}

    try:
        parsed = json.loads(candidate)
    except Exception as exc:
        return False, {"reason": "json_parse_error", "error": str(exc)}

    if not isinstance(parsed, dict):
        return False, {"reason": "json_not_object", "parsed_type": type(parsed).__name__}

    expected_keys = {"answer", "confidence"}
    actual_keys = set(parsed.keys())
    if actual_keys != expected_keys:
        return False, {
            "reason": "json_keys_mismatch",
            "expected_keys": sorted(expected_keys),
            "actual_keys": sorted(actual_keys),
        }

    answer = parsed.get("answer")
    confidence = parsed.get("confidence")

    if not isinstance(answer, str) or not answer.strip():
        return False, {"reason": "answer_invalid"}

    if not isinstance(confidence, (int, float)):
        return False, {"reason": "confidence_not_number"}

    conf = float(confidence)
    if conf < 0.0 or conf > 1.0:
        return False, {"reason": "confidence_out_of_range", "confidence": conf}

    if "blue" not in answer.lower():
        return False, {"reason": "answer_missing_blue", "answer": answer}

    return True, {"parsed": parsed}


def _check_contains(text: str, needle: str) -> tuple[bool, dict[str, Any]]:
    ok = needle.lower() in text.lower()
    return ok, {"needle": needle}


def _check_exact_integer(text: str, expected: int) -> tuple[bool, dict[str, Any]]:
    trimmed = text.strip()
    if re.fullmatch(r"-?\d+", trimmed):
        return int(trimmed) == expected, {"expected": expected, "mode": "exact"}

    nums = re.findall(r"-?\d+", trimmed)
    if not nums:
        return False, {"expected": expected, "mode": "no_integer_found"}

    values = [int(n) for n in nums]
    return expected in values, {
        "expected": expected,
        "mode": "contains_integer",
        "count": len(values),
        "first_five": values[:5],
    }


def _check_sentiment_label(text: str, expected: str = "positive") -> tuple[bool, dict[str, Any]]:
    trimmed = text.strip().lower()
    has_positive = bool(re.search(r"\bpositive\b", trimmed))
    has_negative = "negative" in trimmed
    return has_positive and not has_negative, {
        "expected": expected,
        "has_positive": has_positive,
        "has_negative": has_negative,
    }


def _evaluate_output(test_id: str, output: str) -> tuple[bool, dict[str, Any]]:
    if test_id == "valid_json_strict":
        return _check_strict_json(output)
    if test_id == "trivia_capital":
        return _check_contains(output, "tokyo")
    if test_id == "arithmetic_739x481":
        return _check_exact_integer(output, 355459)
    if test_id == "arithmetic_order_ops":
        return _check_exact_integer(output, 1034)
    if test_id == "arithmetic_913x47":
        return _check_exact_integer(output, 42911)
    if test_id == "arithmetic_order_ops_v2":
        return _check_exact_integer(output, 960)
    if test_id == "sentiment_label":
        return _check_sentiment_label(output)
    return False, {"reason": f"unknown_test_id:{test_id}"}


def _make_tests() -> list[TestDef]:
    return [
        TestDef(
            test_id="valid_json_strict",
            mode="all",
            prompt=(
                "Return only a JSON object (or a single ```json fenced object) with exactly two keys: "
                "answer and confidence. Answer what color the daytime sky usually appears. "
                "Confidence must be a number between 0 and 1. No extra text."
            ),
        ),
        TestDef(
            test_id="trivia_capital",
            mode="all",
            prompt="What is the capital of Japan? Reply in one short sentence.",
        ),
        TestDef(
            test_id="arithmetic_739x481",
            mode="all",
            prompt="Compute 739*481 and return only the final integer with no words.",
        ),
        TestDef(
            test_id="arithmetic_order_ops",
            mode="all",
            prompt="Compute (27*43)-(19*7)+6 and return only the final integer with no words.",
        ),
        TestDef(
            test_id="arithmetic_913x47",
            mode="all",
            prompt="Compute 913*47 and return only the final integer with no words.",
        ),
        TestDef(
            test_id="arithmetic_order_ops_v2",
            mode="all",
            prompt="Compute (84*13)-(17*9)+21 and return only the final integer with no words.",
        ),
        TestDef(
            test_id="sentiment_label",
            mode="all",
            prompt='Label sentiment as one token only: positive or negative. Text: "I love this product and would buy again."',
        ),
    ]


def _api_test_path(mode: str, prompt: str, no_thinking: bool = True) -> str:
    suffix = "&no_thinking=1" if no_thinking else ""
    q = quote(prompt)
    if mode == "chat":
        return f"/test-chat?q={q}{suffix}"
    if mode == "intent":
        return f"/test-intent?q={q}{suffix}"
    if mode == "small":
        return f"/test-util?q={q}{suffix}"
    raise ValueError(f"unknown mode:{mode}")


def _wait_lane_ready(mode: str, timeout_s: int = 180) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            status = _http_json(f"{BASE_URL}/engines/status")
            slot = status.get(mode) if isinstance(status, dict) else None
            if isinstance(slot, dict) and bool(slot.get("listening")):
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def _switch_model(mode: str, model: str, bounce: bool = True) -> tuple[bool, str | None]:
    try:
        _http_json(
            f"{BASE_URL}/switch",
            method="POST",
            body={"mode": mode, "model_dir": model, "bounce": bounce},
            timeout=120,
        )
    except Exception as exc:
        return False, str(exc)

    if not _wait_lane_ready(mode):
        return False, f"lane_not_listening_after_switch:{mode}"
    return True, None


def _lane_models() -> dict[str, list[str]]:
    models = _http_json(f"{BASE_URL}/models")
    return {
        "chat": list(models.get("chat") or []),
        "small": list(models.get("small") or []),
        "intent": list(models.get("intent") or []),
    }


def run_suite(*, limit_per_mode: int | None = None, bounce: bool = True) -> dict[str, Any]:
    tests = _make_tests()
    lanes = _lane_models()

    if limit_per_mode is not None and limit_per_mode > 0:
        for k in lanes:
            lanes[k] = lanes[k][:limit_per_mode]

    results: list[dict[str, Any]] = []
    by_mode: dict[str, dict[str, int]] = {}

    for mode, models in lanes.items():
        by_mode[mode] = {"models": len(models), "runs": 0, "passes": 0, "errors": 0}

        for model in models:
            switched, switch_error = _switch_model(mode, model, bounce=bounce)

            for t in tests:
                if t.mode not in ("all", mode):
                    continue

                row: dict[str, Any] = {
                    "mode": mode,
                    "model": model,
                    "test_id": t.test_id,
                    "ok": False,
                    "error": None,
                    "output": None,
                    "details": {},
                    "switch_ok": switched,
                    "switch_error": switch_error,
                }

                by_mode[mode]["runs"] += 1

                if not switched:
                    row["error"] = switch_error or "switch_failed"
                    by_mode[mode]["errors"] += 1
                    results.append(row)
                    continue

                try:
                    path = _api_test_path(mode, t.prompt, no_thinking=True)
                    payload = _http_json(f"{BASE_URL}{path}", timeout=90)
                    output = _extract_output_text(payload)
                    ok, details = _evaluate_output(t.test_id, output)
                    row["ok"] = ok
                    row["output"] = output
                    row["details"] = details
                    if ok:
                        by_mode[mode]["passes"] += 1
                except Exception as exc:
                    row["error"] = str(exc)
                    by_mode[mode]["errors"] += 1

                results.append(row)

    by_model: dict[str, dict[str, int]] = {}
    by_test: dict[str, dict[str, int]] = {}

    for r in results:
        model_key = f"{r['mode']}::{r['model']}"
        bm = by_model.setdefault(model_key, {"runs": 0, "passes": 0, "errors": 0})
        bt = by_test.setdefault(r["test_id"], {"runs": 0, "passes": 0, "errors": 0})

        bm["runs"] += 1
        bt["runs"] += 1

        if r.get("error"):
            bm["errors"] += 1
            bt["errors"] += 1
        elif r.get("ok"):
            bm["passes"] += 1
            bt["passes"] += 1

    total_runs = sum(m["runs"] for m in by_mode.values())
    total_passes = sum(m["passes"] for m in by_mode.values())
    total_errors = sum(m["errors"] for m in by_mode.values())

    return {
        "summary": {
            "generated_ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "tests": [t.test_id for t in tests],
            "modes": by_mode,
            "total_runs": total_runs,
            "total_passes": total_passes,
            "total_errors": total_errors,
            "pass_rate": round((total_passes / total_runs), 4) if total_runs else 0.0,
        },
        "by_model": by_model,
        "by_test": by_test,
        "results": results,
    }


def _write_reports(report: dict[str, Any]) -> tuple[Path, Path]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = REPORTS_DIR / f"baseline_local_models_{stamp}.json"
    md_path = REPORTS_DIR / f"baseline_local_models_{stamp}.md"

    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    s = report["summary"]
    lines = [
        "# Local Model Baseline Report",
        "",
        f"Generated: {s['generated_ts']}",
        "",
        "## Summary",
        "",
        f"- Total runs: {s['total_runs']}",
        f"- Total passes: {s['total_passes']}",
        f"- Total errors: {s['total_errors']}",
        f"- Pass rate: {s['pass_rate']}",
        "",
        "## By Mode",
        "",
    ]

    for mode, m in s["modes"].items():
        pr = round((m["passes"] / m["runs"]) if m["runs"] else 0.0, 4)
        lines.append(
            f"- {mode}: models={m['models']} runs={m['runs']} passes={m['passes']} errors={m['errors']} pass_rate={pr}"
        )

    lines += ["", "## By Test", ""]
    for test_id, b in sorted(report["by_test"].items()):
        pr = round((b["passes"] / b["runs"]) if b["runs"] else 0.0, 4)
        lines.append(f"- {test_id}: runs={b['runs']} passes={b['passes']} errors={b['errors']} pass_rate={pr}")

    lines += ["", "## By Model", ""]
    for model_key, b in sorted(report["by_model"].items()):
        pr = round((b["passes"] / b["runs"]) if b["runs"] else 0.0, 4)
        lines.append(f"- {model_key}: runs={b['runs']} passes={b['passes']} errors={b['errors']} pass_rate={pr}")

    lines += ["", "## Failure Samples", ""]
    shown = 0
    for row in report["results"]:
        if shown >= 20:
            break
        if row.get("ok") and not row.get("error"):
            continue
        lines.append(
            f"- mode={row['mode']} model={row['model']} test={row['test_id']} ok={row['ok']} error={row['error']}"
        )
        if row.get("output"):
            compact = str(row["output"]).replace("\n", " ")
            if len(compact) > 160:
                compact = compact[:160]
            lines.append(f"  output: {compact}")
        shown += 1

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def _build_promotion_decisions(report: dict[str, Any], gates: PromotionGates) -> dict[str, Any]:
    lane_models: dict[str, dict[str, dict[str, Any]]] = {"chat": {}, "small": {}}

    for row in report.get("results", []):
        mode = row.get("mode")
        if mode not in lane_models:
            continue
        model = str(row.get("model"))
        item = lane_models[mode].setdefault(
            model,
            {
                "runs": 0,
                "passes": 0,
                "errors": 0,
                "tests": {},
            },
        )
        item["runs"] += 1
        test_id = str(row.get("test_id"))
        if row.get("error"):
            item["errors"] += 1
            item["tests"][test_id] = False
        elif row.get("ok"):
            item["passes"] += 1
            item["tests"][test_id] = True
        else:
            item["tests"][test_id] = False

    by_lane: dict[str, list[dict[str, Any]]] = {}
    for mode in ("chat", "small"):
        ranked: list[dict[str, Any]] = []
        for model, stats in lane_models[mode].items():
            runs = stats["runs"]
            pass_rate = (stats["passes"] / runs) if runs else 0.0
            hard_all_pass = all(bool(stats["tests"].get(tid, False)) for tid in gates.hard_tests_must_pass)

            if stats["errors"] == 0 and hard_all_pass and pass_rate >= gates.promote_min_pass_rate:
                tier = "promote"
            elif stats["errors"] == 0 and hard_all_pass and pass_rate >= gates.conditional_min_pass_rate:
                tier = "conditional"
            else:
                tier = "hold"

            ranked.append(
                {
                    "model": model,
                    "tier": tier,
                    "runs": runs,
                    "passes": stats["passes"],
                    "errors": stats["errors"],
                    "pass_rate": round(pass_rate, 4),
                    "hard_all_pass": hard_all_pass,
                    "hard_tests": {tid: bool(stats["tests"].get(tid, False)) for tid in gates.hard_tests_must_pass},
                    "json": bool(stats["tests"].get("valid_json_strict", False)),
                    "trivia": bool(stats["tests"].get("trivia_capital", False)),
                    "sentiment": bool(stats["tests"].get("sentiment_label", False)),
                }
            )

        ranked.sort(key=lambda x: (x["pass_rate"], -x["errors"], x["model"]), reverse=True)
        for i, row in enumerate(ranked, 1):
            row["rank"] = i
        by_lane[mode] = ranked

    return {
        "source_generated_ts": report.get("summary", {}).get("generated_ts"),
        "overall_pass_rate": report.get("summary", {}).get("pass_rate", 0.0),
        "gates": {
            "promote_min_pass_rate": gates.promote_min_pass_rate,
            "conditional_min_pass_rate": gates.conditional_min_pass_rate,
            "hard_tests_must_pass": list(gates.hard_tests_must_pass),
        },
        "by_lane": by_lane,
    }


def _write_promotion_reports(decisions: dict[str, Any], source_json_path: Path) -> tuple[Path, Path]:
    stamp = source_json_path.stem.replace("baseline_local_models_", "")
    json_path = REPORTS_DIR / f"promotion_recommendation_{stamp}.json"
    md_path = REPORTS_DIR / f"promotion_recommendation_{stamp}.md"

    json_path.write_text(json.dumps(decisions, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    lines = [
        "# Promotion Recommendation From Strict Baseline",
        "",
        f"- Source report: {source_json_path}",
        f"- Generated: {decisions.get('source_generated_ts')}",
        f"- Overall pass rate: {decisions.get('overall_pass_rate')}",
        "",
        "## Gates",
        "",
        f"- promote_min_pass_rate: {decisions['gates']['promote_min_pass_rate']}",
        f"- conditional_min_pass_rate: {decisions['gates']['conditional_min_pass_rate']}",
        f"- hard_tests_must_pass: {', '.join(decisions['gates']['hard_tests_must_pass'])}",
        "",
    ]

    for lane in ("chat", "small"):
        lines += [f"## Lane Ranking - {lane.capitalize()}", ""]
        for row in decisions.get("by_lane", {}).get(lane, []):
            ht = row.get("hard_tests", {})
            lines.append(
                f"{row['rank']}. {row['model']} | tier={row['tier']} | pass_rate={row['pass_rate']} | "
                f"passes={row['passes']}/{row['runs']} | errors={row['errors']} | "
                f"json={int(row['json'])} trivia={int(row['trivia'])} sentiment={int(row['sentiment'])} | "
                f"hard_all_pass={int(row['hard_all_pass'])} "
                f"(a1={int(ht.get('arithmetic_739x481', False))}, "
                f"a2={int(ht.get('arithmetic_order_ops', False))}, "
                f"a3={int(ht.get('arithmetic_913x47', False))}, "
                f"a4={int(ht.get('arithmetic_order_ops_v2', False))})"
            )
        lines.append("")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run strict local baseline suite and write report artifacts.")
    parser.add_argument("--base-url", default=BASE_URL, help="API base url, default http://127.0.0.1:8101")
    parser.add_argument("--limit-per-mode", type=int, default=0, help="Optional cap for models per mode (0 = all)")
    parser.add_argument("--no-bounce", action="store_true", help="Skip bounce=true on /switch requests")
    parser.add_argument(
        "--promote-min-pass-rate",
        type=float,
        default=0.9,
        help="Minimum pass rate for promote tier (requires hard tests and zero errors)",
    )
    parser.add_argument(
        "--conditional-min-pass-rate",
        type=float,
        default=0.7,
        help="Minimum pass rate for conditional tier (requires hard tests and zero errors)",
    )
    args = parser.parse_args()

    globals()["BASE_URL"] = args.base_url.rstrip("/")

    report = run_suite(
        limit_per_mode=(args.limit_per_mode if args.limit_per_mode > 0 else None),
        bounce=not args.no_bounce,
    )
    json_path, md_path = _write_reports(report)

    gates = PromotionGates(
        promote_min_pass_rate=float(args.promote_min_pass_rate),
        conditional_min_pass_rate=float(args.conditional_min_pass_rate),
    )
    decisions = _build_promotion_decisions(report, gates)
    decision_json_path, decision_md_path = _write_promotion_reports(decisions, json_path)

    print(json.dumps({
        "json_report": str(json_path),
        "md_report": str(md_path),
        "promotion_json_report": str(decision_json_path),
        "promotion_md_report": str(decision_md_path),
        "summary": report.get("summary", {}),
        "gates": decisions.get("gates", {}),
    }, indent=2))


if __name__ == "__main__":
    main()
