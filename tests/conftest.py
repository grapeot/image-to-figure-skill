"""Shared fixtures. The default suite is offline: provider factories are replaced so no network call can happen."""

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from image_to_figure import blender as blender_mod
from image_to_figure import fal3d, images
from image_to_figure.cli import main

ROOT = Path(__file__).resolve().parents[1]


def pytest_collection_modifyitems(config, items):
    live = os.environ.get("IMG2FIG_LIVE") == "1"
    has_blender = blender_mod.find_blender() is not None
    for item in items:
        if "live" in item.keywords and not live:
            item.add_marker(pytest.mark.skip(reason="live API test; set IMG2FIG_LIVE=1 to run (spends money)"))
        if "blender" in item.keywords and not has_blender:
            item.add_marker(pytest.mark.skip(reason="no Blender binary found"))


@pytest.fixture(autouse=True)
def offline(monkeypatch, request):
    """No keys and no real provider clients unless the test is marked live."""
    if "live" in request.keywords:
        return

    def no_openai():
        raise AssertionError("OpenAI client created in an offline test")

    def no_fal():
        raise AssertionError("fal client created in an offline test")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("IMG2FIG_BUDGET_USD", raising=False)
    monkeypatch.delenv("IMG2FIG_WORKDIR", raising=False)
    monkeypatch.setattr(images, "client_factory", no_openai)
    monkeypatch.setattr(fal3d, "fal_factory", no_fal)


def png_bytes(size=(64, 32), color=(200, 100, 50)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class FakeImages:
    def __init__(self, fail: Exception | None = None):
        self.calls = []
        self.fail = fail

    def _resp(self):
        b64 = base64.b64encode(png_bytes()).decode()
        usage = {"input_tokens": 1200, "output_tokens": 6000,
                 "input_tokens_details": {"image_tokens": 1000, "text_tokens": 200}, "total_tokens": 7200}
        return SimpleNamespace(data=[SimpleNamespace(b64_json=b64)], usage=usage)

    def generate(self, **kw):
        self.calls.append(("generate", kw))
        if self.fail:
            raise self.fail
        return self._resp()

    def edit(self, **kw):
        kw = {**kw, "image": [getattr(f, "name", f) for f in kw["image"]]}
        self.calls.append(("edit", kw))
        if self.fail:
            raise self.fail
        return self._resp()


@pytest.fixture
def fake_openai(monkeypatch):
    fake = FakeImages()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(images, "client_factory", lambda: SimpleNamespace(images=fake))
    return fake


class FakeFal:
    def __init__(self, fail_providers=()):
        self.uploads = []
        self.subscribed = []
        self.fail_providers = set(fail_providers)

    def upload_file(self, path):
        self.uploads.append(Path(path).name)
        return f"https://files.example.com/{len(self.uploads)}_{Path(path).name}"

    def subscribe(self, endpoint, arguments, **kw):
        self.subscribed.append((endpoint, arguments))
        for name, (ep, _, _) in fal3d.PROVIDERS.items():
            if ep == endpoint and name in self.fail_providers:
                raise FakeHTTPError("Unprocessable", 422, '{"detail":"bad image"}')
        return {"model_mesh": {"url": f"https://files.example.com/out/{endpoint.replace('/', '_')}_model.glb"},
                "base": {"url": "https://files.example.com/out/base.glb"},
                "rendered_image": {"url": "https://files.example.com/out/preview.png"}}


class FakeHTTPError(Exception):
    def __init__(self, message, status_code, body):
        super().__init__(message)
        self.status_code = status_code
        self.response = SimpleNamespace(text=body)


@pytest.fixture
def fake_fal(monkeypatch):
    fake = FakeFal()
    monkeypatch.setenv("FAL_KEY", "test-key-not-real")
    monkeypatch.setattr(fal3d, "fal_factory", lambda: fake)

    def download(url, dest):
        # the "model" file is bigger than the "base" file so the largest-GLB rule picks it
        dest.write_bytes(b"x" * (2000 if "model" in url else 100))

    monkeypatch.setattr(fal3d, "downloader", download)
    return fake


def draw_sheet(path: Path, size=(1600, 700), n=4, gap_ok=True, bg=(214, 214, 216), noise=0):
    """Synthetic turnaround: n simple figures (head + body) in a row on a flat background."""
    import random

    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    w, h = size
    slot = w // n
    for i in range(n):
        cx = slot * i + slot // 2
        half = slot // 2 - (20 if gap_ok else -6)
        body_w = min(half, slot // 3)
        d.rectangle((cx - body_w, int(h * 0.45), cx + body_w, int(h * 0.92)), fill=(40 + 40 * i, 60, 140))
        d.ellipse((cx - body_w, int(h * 0.10), cx + body_w, int(h * 0.48)), fill=(240, 205, 180))
        if not gap_ok:  # arms reaching into the neighbour slot
            d.rectangle((cx - half - 10, int(h * 0.5), cx + half + 10, int(h * 0.55)), fill=(30, 30, 30))
    if noise:
        rnd = random.Random(0)
        px = im.load()
        for _ in range(w * h // 20):
            x, y = rnd.randrange(w), rnd.randrange(h)
            r, g, b = px[x, y]
            px[x, y] = (min(255, r + rnd.randint(-noise, noise)), g, b)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


@pytest.fixture
def run_cli(capsys):
    """Run img2fig in-process; returns (exit_code, parsed JSON). Asserts exactly one JSON object on stdout."""

    def run(*argv):
        code = main([str(a) for a in argv])
        out = capsys.readouterr().out
        data = json.loads(out)  # fails if there is anything besides one JSON document
        assert isinstance(data, dict)
        assert data.get("ok") is (code == 0)
        return code, data

    return run


@pytest.fixture
def workdir(tmp_path):
    return tmp_path / "work"


@pytest.fixture
def views(workdir):
    d = workdir / "views"
    d.mkdir(parents=True)
    for i, v in enumerate(("front", "left", "back", "right")):
        Image.new("RGB", (100, 200), (10 * i, 20, 30)).save(d / f"view_{v}.png")
    return d

