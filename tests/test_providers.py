import pytest
from PIL import Image

from conftest import FakeHTTPError
from image_to_figure import fal3d, images
from image_to_figure.errors import MissingDependency, ProviderError, UsageError


def test_generate_without_inputs_uses_generate(fake_openai, tmp_path):
    res = images.generate(prompt="p", out_path=tmp_path / "a.png", size="1024x1024", quality="low")
    assert fake_openai.calls[0][0] == "generate"
    kw = fake_openai.calls[0][1]
    assert kw["size"] == "1024x1024" and kw["quality"] == "low" and kw["model"] == images.DEFAULT_MODEL
    assert Image.open(res["path"]).size == (64, 32)
    assert res["usage"]["output_tokens"] == 6000


def test_generate_with_inputs_uses_edit(fake_openai, tmp_path):
    ref = tmp_path / "ref.png"
    Image.new("RGB", (8, 8)).save(ref)
    images.generate(prompt="p", out_path=tmp_path / "a.png", size="1024x1024", inputs=[ref])
    assert fake_openai.calls[0][0] == "edit"
    assert fake_openai.calls[0][1]["image"] == [str(ref)]


def test_model_override_from_env(fake_openai, tmp_path, monkeypatch):
    monkeypatch.setenv("IMG2FIG_IMAGE_MODEL", "some-other-image-model")
    images.generate(prompt="p", out_path=tmp_path / "a.png", size="1024x1024")
    assert fake_openai.calls[0][1]["model"] == "some-other-image-model"


def test_openai_error_passthrough(fake_openai, tmp_path):
    err = FakeHTTPError("Your request was rejected", 400, '{"error":{"code":"moderation_blocked"}}')
    err.body = {"error": {"code": "moderation_blocked", "message": "Your request was rejected"}}
    fake_openai.fail = err
    with pytest.raises(ProviderError) as e:
        images.generate(prompt="p", out_path=tmp_path / "a.png", size="1024x1024")
    d = e.value.detail
    assert d["status_code"] == 400
    assert d["body"]["error"]["code"] == "moderation_blocked"
    assert "HTTP 400" in e.value.message


def test_missing_key_is_missing_dependency(monkeypatch, tmp_path):
    monkeypatch.setattr(images, "client_factory", images._default_client)
    with pytest.raises(MissingDependency) as e:
        images.generate(prompt="p", out_path=tmp_path / "a.png", size="1024x1024")
    assert e.value.exit_code == 4


def test_missing_input_is_usage_error(fake_openai, tmp_path):
    with pytest.raises(UsageError):
        images.generate(prompt="p", out_path=tmp_path / "a.png", size="1024x1024", inputs=[tmp_path / "nope.png"])


def test_upload_cache_by_hash(fake_fal, views):
    paths = {v: views / f"view_{v}.png" for v in ("front", "left", "back", "right")}
    urls1 = fal3d.upload_views(fake_fal, paths, views / "uploads.json")
    assert len(fake_fal.uploads) == 4
    urls2 = fal3d.upload_views(fake_fal, paths, views / "uploads.json")
    assert urls1 == urls2 and len(fake_fal.uploads) == 4  # cached
    Image.new("RGB", (100, 200), (255, 0, 0)).save(paths["back"])  # edited view
    urls3 = fal3d.upload_views(fake_fal, paths, views / "uploads.json")
    assert len(fake_fal.uploads) == 5 and urls3["back"] != urls1["back"] and urls3["front"] == urls1["front"]


def test_tripo_arguments_order_and_options():
    u = {v: v + ".png" for v in ("front", "left", "back", "right")}
    args = fal3d.PROVIDERS["tripo"][1](u, {"face_limit": 200000, "seed": 7})
    assert args["image_urls"] == ["front.png", "left.png", "back.png", "right.png"]
    assert args["face_limit"] == 200000 and args["model_seed"] == 7
    assert fal3d.PROVIDERS["tripo"][2] == -90.0


def test_run_provider_downloads_and_picks_largest_glb(fake_fal, tmp_path):
    urls = {v: f"https://x/{v}" for v in ("front", "left", "back", "right")}
    res = fal3d.run_provider(fake_fal, "tripo", urls, tmp_path)
    assert res["model"].endswith("tripo/model.glb")
    assert (tmp_path / "tripo" / "model.glb").stat().st_size == 2000
    assert (tmp_path / "tripo" / "files" / "model.glb").exists()  # provider's own model.glb kept intact
    assert (tmp_path / "tripo" / "result.json").exists()
    assert "preview.png" in res["files"]


def test_run_many_keeps_going_after_a_failure(fake_fal, tmp_path):
    fake_fal.fail_providers = {"rodin"}
    urls = {v: f"https://x/{v}" for v in ("front", "left", "back", "right")}
    res = {r["provider"]: r for r in fal3d.run_many(fake_fal, ["tripo", "rodin"], urls, tmp_path, {})}
    assert res["tripo"]["ok"] is True
    assert res["rodin"]["ok"] is False
    assert res["rodin"]["error"]["detail"]["status_code"] == 422
    assert "bad image" in res["rodin"]["error"]["detail"]["body"]


def test_check_views_lists_missing(tmp_path):
    with pytest.raises(UsageError) as e:
        fal3d.check_views({"front": tmp_path / "view_front.png"})
    assert e.value.detail["missing"]


def test_no_glb_in_result_is_a_warning(fake_fal, tmp_path, monkeypatch):
    monkeypatch.setattr(fake_fal, "subscribe", lambda *a, **k: {"image": {"url": "https://x/only.png"}})
    res = fal3d.run_provider(fake_fal, "meshy", {v: "u" for v in ("front", "left", "back", "right")}, tmp_path)
    assert res["model"] is None and "warning" in res



def test_unexpected_local_error_does_not_hide_other_results(fake_fal, tmp_path, monkeypatch):
    real = fal3d.run_provider

    def flaky(fal, p, *a, **k):
        if p == "rodin":
            raise RuntimeError("boom")
        return real(fal, p, *a, **k)

    monkeypatch.setattr(fal3d, "run_provider", flaky)
    urls = {v: f"https://x/{v}" for v in ("front", "left", "back", "right")}
    res = {r["provider"]: r for r in fal3d.run_many(fake_fal, ["tripo", "rodin"], urls, tmp_path, {})}
    assert res["tripo"]["ok"] and not res["rodin"]["ok"]
    assert "boom" in res["rodin"]["error"]["message"]


def test_gpt_image_2_6_rejects_xhigh_and_max(fake_openai, tmp_path):
    for tier in ("xhigh", "max"):
        with pytest.raises(UsageError):
            images.generate(prompt="p", out_path=tmp_path / "a.png", size="1024x1024", quality=tier)
    assert fake_openai.calls == []
    images.generate(
        prompt="p", out_path=tmp_path / "b.png", size="1024x1024", quality="max", model="gpt-image-2.5-sunburst"
    )
    assert fake_openai.calls[0][1]["quality"] == "max"
