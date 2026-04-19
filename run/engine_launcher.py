#!/usr/bin/env python3
"""
engine_launcher.py - Compatibility wrapper for existing systemd units.

New backend-specific launchers now live under run/launch_*.py.
This file intentionally remains as a stable entrypoint for existing ExecStart
definitions and forwards to launch_tgw.py.
"""

import argparse
import os
import pathlib
import sys


def main():
    parser = argparse.ArgumentParser(description="Compatibility wrapper forwarding to run/launch_tgw.py")
    parser.add_argument("--api-port", required=True, help="API listen port")
    parser.add_argument("--model", required=True, help="Model directory name (or symlink)")
    parser.add_argument("--max-seq-len", required=True, help="Max sequence length")
    parser.add_argument("--listen-host", default="127.0.0.1", help="Listen host")
    args = parser.parse_args()

    launch_tgw = pathlib.Path(__file__).resolve().parent / "launch_tgw.py"
    cmd = [
        sys.executable,
        str(launch_tgw),
        "--api-port",
        args.api_port,
        "--model", args.model,
        "--max-seq-len",
        args.max_seq_len,
        "--listen-host",
        args.listen_host,
    ]
    os.execv(sys.executable, cmd)


if __name__ == "__main__":
    main()
