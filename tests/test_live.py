"""Opt-in live checks against the real providers. They spend money (about $0.02 total).

Run with: IMG2FIG_LIVE=1 OPENAI_API_KEY=... python -m pytest -m live -q
"""

import pytest
from PIL import Image

pytestmark = pytest.mark.live


def test_live_text_only_lookdev(run_cli, tmp_path):
    code, out = run_cli("lookdev", "--workdir", tmp_path, "--description",
                        "an original round robot mascot with a teal body and one antenna",
                        "--size", "1024x1024", "--quality", "low", "--execute", "--budget", "0.10")
    assert code == 0, out
    assert Image.open(out["image"]).size == (1024, 1024)
    assert out["usage"] and out["usage"].get("output_tokens")
