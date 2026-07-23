#!/usr/bin/env python3
"""Retired compatibility entrypoint for the pre-transactional model switcher."""

import sys


def main() -> None:
    print(
        "This model switcher is retired because it bypassed llm-manager's "
        "validation, readiness checks, and rollback. Use the dashboard or "
        "POST /models/load instead.",
        file=sys.stderr,
    )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
