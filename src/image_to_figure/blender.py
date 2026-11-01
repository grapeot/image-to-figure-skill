"""Run the scripts in `blender_scripts/` with an external Blender binary."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .errors import MissingDependency, ToolError, log

SCRIPTS = Path(__file__).resolve().parent / "blender_scripts"
MAC_APP = "/Applications/Blender.app/Contents/MacOS/Blender"


def find_blender() -> str | None:
    env = os.environ.get("IMG2FIG_BLENDER")
    if env:
        return env if Path(env).exists() or shutil.which(env) else None
    found = shutil.which("blender")
    if found:
        return found
    if Path(MAC_APP).exists():
        return MAC_APP
    return None


def require_blender() -> str:
    b = find_blender()
    if not b:
        raise MissingDependency(
            "Blender not found: install Blender 4.2 or newer and put `blender` on PATH, or set IMG2FIG_BLENDER",
            {"env": "IMG2FIG_BLENDER"},
        )
    return b


def blender_version(binary: str) -> str | None:
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    first = out.strip().splitlines()[0] if out.strip() else ""
    return first or None


def _tail(text: str, n: int = 40) -> str:
    return "\n".join(text.splitlines()[-n:])


def run_script(script: str, args: list[str], log_path: Path, timeout: float = 3600) -> dict[str, Any]:
    """Run blender_scripts/<script> in background mode and return the JSON it writes with --result."""
    binary = require_blender()
    script_path = SCRIPTS / script
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fd, result_path = tempfile.mkstemp(prefix="img2fig_", suffix=".json")
    os.close(fd)
    os.unlink(result_path)
    cmd = [binary, "-b", "--factory-startup", "--python-exit-code", "1", "-P", str(script_path), "--",
           *args, "--result", result_path]
    log(f"blender {script} (log: {log_path})")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise ToolError(f"Blender timed out after {timeout:.0f}s running {script}", {"cmd": cmd}) from e
    log_path.write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
    result = None
    if os.path.exists(result_path):
        try:
            result = json.loads(Path(result_path).read_text(encoding="utf-8"))
        finally:
            os.unlink(result_path)
    if proc.returncode != 0 or result is None:
        raise ToolError(
            f"Blender exited with code {proc.returncode} running {script}"
            + ("" if result is not None else " and wrote no result"),
            {"exit_code": proc.returncode, "log": str(log_path), "stderr_tail": _tail(proc.stderr),
             "stdout_tail": _tail(proc.stdout)},
        )
    result["log"] = str(log_path)
    return result
