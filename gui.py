"""Compatibility launcher for the Tauri desktop UI.

Run this from the configured conda environment:

    python gui.py

The verified backend remains Python. This launcher tells the Tauri shell which
Python executable to use for gui_backend.py, monitor.py, and bootstrap.py.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    npm = shutil.which("npm")
    if not npm:
        print("未找到 npm。请先安装 Node.js，然后运行 npm install。", file=sys.stderr)
        return 1

    env = os.environ.copy()
    env["SJTU_MONITOR_PYTHON"] = sys.executable
    env["SJTU_MONITOR_ROOT"] = str(ROOT)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")

    node_modules = ROOT / "node_modules"
    if not node_modules.exists():
        print("未找到 node_modules。请先运行 npm install。", file=sys.stderr)
        return 1

    cmd = [npm, "run", "tauri", "dev"]
    print("$ " + " ".join(cmd))
    try:
        return subprocess.call(cmd, cwd=ROOT, env=env)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
