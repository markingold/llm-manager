#!/usr/bin/env python3
"""Fail CI when tracked source contains likely credentials or secret files."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PATHS = re.compile(r"(^|/)(?:secrets?/|\.env(?:\.|$))", re.IGNORECASE)
SECRET_PATTERNS = {
    "private key": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\r\n]{40,}"
    ),
    "Hugging Face token": re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    "API token": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b"),
    "GitHub token": re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{30,}\b"),
}


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def main() -> int:
    files = tracked_files()
    findings: list[str] = []
    for relative in files:
        if FORBIDDEN_PATHS.search(relative):
            findings.append(f"forbidden secret path is tracked: {relative}")
            continue
        path = ROOT / relative
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in SECRET_PATTERNS.items():
            match = pattern.search(content)
            if match:
                line = content.count("\n", 0, match.start()) + 1
                findings.append(f"{relative}:{line}: possible {label}")

    if findings:
        print("Secret scan failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print(f"Secret scan passed ({len(files)} tracked files checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
