"""Static guarantees about the repository (AGENTS.md invariants 3 and 7)."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "image_to_figure"

KEY_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),  # OpenAI-style secret
    re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{32}"),  # fal-style key
    re.compile(r"/Users/[A-Za-z]"),
    re.compile(r"/home/[a-z]+/"),
]


def tracked_files():
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        files = [ROOT / f for f in out.splitlines() if f]
    except (OSError, subprocess.CalledProcessError):
        files = [p for p in ROOT.rglob("*") if p.is_file() and ".venv" not in p.parts and ".git" not in p.parts]
    return [f for f in files if f.suffix in {".py", ".md", ".toml", ".yml", ".yaml", ".txt", ".example", ""}
            and f.exists()]


def test_no_keys_or_machine_paths_in_repo():
    offenders = []
    for f in tracked_files():
        text = f.read_text(encoding="utf-8", errors="ignore")
        for pat in KEY_PATTERNS:
            for m in pat.finditer(text):
                offenders.append(f"{f.relative_to(ROOT)}: {m.group(0)[:30]}")
    assert offenders == []


def test_keys_only_from_environment():
    for f in SRC.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert "dotenv" not in text, f.name
        assert ".env\"" not in text and ".env'" not in text, f.name


def test_no_binary_assets_tracked():
    """Only original documentation images under docs/images/ may be tracked; never models or work products."""
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    exts = {".png", ".jpg", ".jpeg", ".webp", ".glb", ".gltf", ".blend", ".stl", ".3mf", ".obj", ".ply", ".mp4"}
    bad = [f for f in out if Path(f).suffix.lower() in exts and not f.startswith("docs/images/")]
    bad += [f for f in out if f.startswith("docs/images/") and Path(f).suffix.lower() not in {".png", ".jpg"}]
    assert bad == []
