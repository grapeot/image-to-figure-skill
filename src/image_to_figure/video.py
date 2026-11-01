"""Encode turntable frames with an external ffmpeg binary."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .errors import MissingDependency, ToolError


def find_ffmpeg() -> str | None:
    return os.environ.get("IMG2FIG_FFMPEG") or shutil.which("ffmpeg")


def encode_cmd(ffmpeg: str, frames_dir: Path, pattern: str, fps: int, out: Path, crf: int = 18) -> list[str]:
    return [
        ffmpeg, "-y", "-loglevel", "error", "-framerate", str(fps), "-start_number", "1",
        "-i", str(frames_dir / pattern),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf), "-movflags", "+faststart",
        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
        str(out),
    ]


def encode(frames_dir: Path, pattern: str, fps: int, out: Path, log_path: Path) -> dict:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise MissingDependency("ffmpeg not found on PATH (or set IMG2FIG_FFMPEG)", {"env": "IMG2FIG_FFMPEG"})
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = encode_cmd(ffmpeg, frames_dir, pattern, fps, out)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        raise MissingDependency(f"cannot run ffmpeg at {ffmpeg}: {e}", {"env": "IMG2FIG_FFMPEG"}) from e
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
    if proc.returncode != 0 or not out.exists():
        raise ToolError(f"ffmpeg exited with code {proc.returncode}",
                        {"exit_code": proc.returncode, "stderr_tail": proc.stderr[-3000:], "cmd": cmd})
    return {"video": str(out), "bytes": out.stat().st_size, "fps": fps}
