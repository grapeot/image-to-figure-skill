import json

import pytest
from PIL import Image

from conftest import draw_sheet


def test_doctor_reports_without_keys(run_cli, workdir):
    code, out = run_cli("doctor", "--workdir", workdir)
    assert code == 0
    assert out["keys_present"] == {"OPENAI_API_KEY": False, "FAL_KEY": False}
    assert out["ready"]["image_stages"] is False


def test_usage_error_is_json_exit_2(run_cli):
    code, out = run_cli("lookdev", "--no-such-flag")
    assert code == 2 and out["error"]["type"] == "usage_error"
    code, out = run_cli()
    assert code == 2


def test_estimate(run_cli):
    code, out = run_cli("estimate", "--stages", "lookdev,turnaround,edit=2,model=all")
    assert code == 0
    stages = [i["stage"] for i in out["items"]]
    assert stages[:3] == ["lookdev", "turnaround", "edit"]
    assert {"model:tripo", "model:rodin", "model:meshy", "model:hunyuan"} <= set(stages)
    assert out["total_usd"] == pytest.approx(sum(i["usd"] for i in out["items"]), abs=1e-3)
    code, out = run_cli("estimate", "--stages", "model=nope")
    assert code == 2


def test_lookdev_dry_run_spends_nothing(run_cli, workdir, tmp_path):
    ref = tmp_path / "ref.png"
    Image.new("RGB", (8, 8)).save(ref)
    code, out = run_cli("lookdev", "--workdir", workdir, "--ref", ref, "--description", "green hat")
    assert code == 0 and out["dry_run"] is True
    assert out["plan"]["endpoint"] == "images.edit"
    assert "green hat" in out["plan"]["prompt"]
    assert out["estimate_usd"] > 0
    assert not (workdir / "spend.json").exists()
    assert not list((workdir / "lookdev").glob("*.png"))


def test_lookdev_needs_ref_or_text(run_cli, workdir):
    code, out = run_cli("lookdev", "--workdir", workdir)
    assert code == 2


def test_lookdev_execute_records_spend(run_cli, workdir, tmp_path, fake_openai):
    ref = tmp_path / "ref.png"
    Image.new("RGB", (8, 8)).save(ref)
    code, out = run_cli("lookdev", "--workdir", workdir, "--ref", ref, "--execute")
    assert code == 0 and out["dry_run"] is False
    assert out["image"].endswith("lookdev_v1.png")
    assert (workdir / "lookdev" / "lookdev_v1.prompt.txt").exists()
    ledger = json.loads((workdir / "spend.json").read_text())
    assert ledger["entries"][0]["stage"] == "lookdev"
    assert ledger["entries"][0]["actual_usd"] == out["cost_from_usage_usd"]
    assert "test-key-not-real" not in json.dumps(out)


def test_budget_refuses_before_any_call(run_cli, workdir, tmp_path, fake_openai):
    ref = tmp_path / "ref.png"
    Image.new("RGB", (8, 8)).save(ref)
    code, out = run_cli("lookdev", "--workdir", workdir, "--ref", ref, "--execute", "--budget", "0.01")
    assert code == 3 and out["error"]["type"] == "budget_exceeded"
    assert fake_openai.calls == []
    # the dry run reports the same without failing
    code, out = run_cli("lookdev", "--workdir", workdir, "--ref", ref, "--budget", "0.01")
    assert code == 0 and out["budget"]["within_budget"] is False


def test_budget_from_env(run_cli, workdir, tmp_path, fake_openai, monkeypatch):
    monkeypatch.setenv("IMG2FIG_BUDGET_USD", "0.01")
    ref = tmp_path / "ref.png"
    Image.new("RGB", (8, 8)).save(ref)
    code, _ = run_cli("lookdev", "--workdir", workdir, "--ref", ref, "--execute")
    assert code == 3


def test_execute_without_key_exit_4(run_cli, workdir, tmp_path, monkeypatch):
    from image_to_figure import images

    monkeypatch.setattr(images, "client_factory", images._default_client)
    ref = tmp_path / "ref.png"
    Image.new("RGB", (8, 8)).save(ref)
    code, out = run_cli("lookdev", "--workdir", workdir, "--ref", ref, "--execute")
    assert code == 4 and out["error"]["detail"]["env"] == "OPENAI_API_KEY"


def test_provider_error_exit_1(run_cli, workdir, tmp_path, fake_openai):
    from conftest import FakeHTTPError

    fake_openai.fail = FakeHTTPError("rate limited", 429, "slow down")
    ref = tmp_path / "ref.png"
    Image.new("RGB", (8, 8)).save(ref)
    code, out = run_cli("lookdev", "--workdir", workdir, "--ref", ref, "--execute")
    assert code == 1
    assert out["error"]["detail"]["status_code"] == 429
    assert not (workdir / "spend.json").exists()


def test_turnaround_uses_latest_lookdev_and_versions(run_cli, workdir, fake_openai):
    (workdir / "lookdev").mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(workdir / "lookdev" / "lookdev_v1.png")
    code, out = run_cli("turnaround", "--workdir", workdir, "--execute")
    assert code == 0
    assert out["image"].endswith("turnaround/sheet_v1.png")
    kw = fake_openai.calls[0][1]
    assert kw["size"] == "3840x2160"
    assert kw["image"][0].endswith("lookdev_v1.png")
    code, out = run_cli("turnaround", "--workdir", workdir, "--execute")
    assert out["image"].endswith("sheet_v2.png")


def test_edit_keeps_input_size_and_names_output(run_cli, workdir, fake_openai):
    (workdir / "turnaround").mkdir(parents=True)
    Image.new("RGB", (1536, 1024)).save(workdir / "turnaround" / "sheet_v1.png")
    code, out = run_cli("edit", "--workdir", workdir, "--fix", "remove the tail on the back view")
    assert code == 0 and out["plan"]["size"] == "1536x1024"
    assert out["plan"]["output"].endswith("sheet_v1_e1.png")
    code, out = run_cli("edit", "--workdir", workdir)
    assert code == 2  # no --fix


def test_crop_command(run_cli, workdir):
    draw_sheet(workdir / "turnaround" / "sheet_v1.png")
    code, out = run_cli("crop", "--workdir", workdir)
    assert code == 0
    assert (workdir / "views" / "view_right.png").exists()
    assert (workdir / "views" / "crop.json").exists()


def test_crop_failure_exit_6(run_cli, workdir):
    draw_sheet(workdir / "turnaround" / "sheet_v1.png", gap_ok=False)
    code, out = run_cli("crop", "--workdir", workdir)
    assert code == 6 and out["error"]["type"] == "check_failed"


def test_model_dry_run_and_execute(run_cli, workdir, views, fake_fal):
    code, out = run_cli("model", "--workdir", workdir)
    assert code == 0 and out["dry_run"] is True
    assert out["plan"][0]["endpoint"] == "tripo3d/h3.1/multiview-to-3d"
    assert fake_fal.subscribed == []
    code, out = run_cli("model", "--workdir", workdir, "--execute")
    assert code == 0
    assert out["results"][0]["model"].endswith("models/tripo/model.glb")
    assert "prep" in out["next"]
    led = json.loads((workdir / "spend.json").read_text())
    assert led["entries"][0]["estimate_usd"] == 0.6


def test_model_bakeoff_partial_failure_exit_1(run_cli, workdir, views, fake_fal):
    fake_fal.fail_providers = {"meshy"}
    code, out = run_cli("model", "--workdir", workdir, "--provider", "all", "--execute")
    assert code == 1
    results = {r["provider"]: r for r in out["error"]["detail"]["results"]}
    assert results["meshy"]["ok"] is False and results["tripo"]["ok"] is True
    led = json.loads((workdir / "spend.json").read_text())
    assert {e["provider"] for e in led["entries"]} == {"tripo", "hunyuan", "rodin"}


def test_model_without_views_exit_2(run_cli, workdir):
    code, out = run_cli("model", "--workdir", workdir)
    assert code == 2


def test_prep_validates_blush_before_blender(run_cli, workdir, tmp_path):
    m = tmp_path / "m.glb"
    m.write_bytes(b"glTF")
    code, out = run_cli("prep", "--workdir", workdir, "--model", m, "--blush", "0.1")
    assert code == 2 and "negative" in out["error"]["message"]


def test_point(run_cli):
    code, out = run_cli("point", "--x", "450", "--y", "600")
    assert code == 0 and out["x"] == 0 and out["blush_arg"].startswith("--blush=")
    code, out = run_cli("point", "--x", "-5", "--y", "600")
    assert code == 2


def test_sheet_defaults_and_rows(run_cli, workdir, views, tmp_path):
    code, out = run_cli("sheet", "--workdir", workdir)
    assert code == 0 and len(out["missing"]) == 4  # renders not there yet: grey placeholders
    a = tmp_path / "a.png"
    Image.new("RGB", (30, 60)).save(a)
    code, out = run_cli("sheet", "--workdir", workdir, "--row", f"{a},{a}", "--out", tmp_path / "s.jpg")
    assert code == 0 and out["missing"] == []


def test_internal_error_keeps_contract(run_cli, workdir, monkeypatch):
    from image_to_figure import cli

    def boom(args):
        raise RuntimeError("bug")

    monkeypatch.setattr(cli, "cmd_doctor", boom)
    parser = cli.build_parser
    monkeypatch.setattr(cli, "build_parser", lambda: _patched(parser(), boom))
    code, out = run_cli("doctor")
    assert code == 7 and out["error"]["type"] == "internal_error"


def _patched(parser, fn):
    for action in parser._subparsers._group_actions:
        action.choices["doctor"].set_defaults(func=fn)
    return parser
