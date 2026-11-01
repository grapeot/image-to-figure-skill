"""img2fig: one character image to a 3D collectible figure.

Every subcommand prints exactly one JSON object to stdout. Exit codes: see errors.py and docs/design.md.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import traceback
from pathlib import Path
from typing import Any

from . import __version__, costs, crop, fal3d, images, orientation, prompts
from . import blender as blender_mod
from . import sheet as sheet_mod
from . import video as video_mod
from .errors import (
    EXIT_INTERNAL,
    EXIT_OK,
    Img2FigError,
    MissingDependency,
    ProviderError,
    UsageError,
    emit,
    log,
)
from .workdir import VIEW_NAMES, Ledger, WorkDir, budget_from, next_edit, next_version

DEFAULT_WORKDIR = "figure_work"
ALL_VIEWS = ("front", "left", "back", "right", "q34", "face")


class JsonArgParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # noqa: D401 - argparse hook
        raise UsageError(message, {"usage": self.format_usage().strip()})


# ---------------------------------------------------------------- helpers


def _wd(args) -> WorkDir:
    wd = WorkDir(args.workdir or os.environ.get("IMG2FIG_WORKDIR") or DEFAULT_WORKDIR)
    wd.root.mkdir(parents=True, exist_ok=True)
    return wd


def _existing(path: str | Path | None, what: str, wd: WorkDir | None = None) -> Path:
    """An existing file. A relative path is tried against the current directory, then the work directory."""
    if path is None:
        raise UsageError(f"{what} is required")
    raw = Path(path).expanduser()
    p = raw.resolve()
    if not p.is_file() and wd is not None and not raw.is_absolute() and (wd.root / raw).is_file():
        p = (wd.root / raw).resolve()
    if not p.is_file():
        raise UsageError(f"{what} not found: {p}", {"path": str(p)})
    return p


def _latest_png(directory: Path, prefix: str) -> Path | None:
    cands = [p for p in directory.glob(f"{prefix}*.png") if p.is_file()]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def _read_prompt_file(path: str | None) -> str | None:
    if not path:
        return None
    return _existing(path, "--prompt-file").read_text(encoding="utf-8").strip()


def _image_size_of(path: Path) -> str:
    from PIL import Image

    with Image.open(path) as im:
        return f"{im.width}x{im.height}"


def _paid_image(args, wd: WorkDir, *, stage: str, prompt: str, inputs: list[Path], out_path: Path,
                size: str) -> dict[str, Any]:
    try:
        costs.parse_size(size)
    except ValueError as e:
        raise UsageError(str(e)) from e
    if args.quality not in images.QUALITIES:
        raise UsageError(f"--quality must be one of {images.QUALITIES}")
    est = costs.estimate_image(size, args.quality, len(inputs), len(prompt))
    ledger = Ledger(wd.ledger_path)
    budget = budget_from(args.budget)
    model = images.image_model(args.image_model)
    plan = {
        "stage": stage,
        "provider": "openai",
        "endpoint": "images.edit" if inputs else "images.generate",
        "model": model,
        "size": size,
        "quality": args.quality,
        "inputs": [str(p) for p in inputs],
        "output": str(out_path),
        "prompt": prompt,
    }
    if not args.execute:
        info: dict[str, Any]
        try:
            info = ledger.check(est, budget)
            info["within_budget"] = True
        except Img2FigError as e:
            info = {**e.detail, "within_budget": False}
        return {"dry_run": True, "plan": plan, "estimate_usd": est, "budget": info,
                "next": f"rerun with --execute to spend about ${costs.round_up_cents(est):.2f}"}
    with ledger.locked():
        info = ledger.check(est, budget)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.with_suffix(".prompt.txt").write_text(prompt + "\n", encoding="utf-8")
        log(f"{stage}: {plan['endpoint']} {model} {size} {args.quality} -> {out_path.name}")
        base = {"stage": stage, "provider": "openai", "model": model, "size": size, "quality": args.quality,
                "estimate_usd": est}
        try:
            res = images.generate(prompt=prompt, out_path=out_path, size=size, quality=args.quality, model=model,
                                  inputs=inputs)
        except Img2FigError as e:
            if not isinstance(e, (UsageError, MissingDependency)):  # the request may have been billed
                ledger.record({**base, "status": "failed", "actual_usd": None, "error": e.message})
            raise
        actual = costs.cost_from_usage(res.get("usage"))
        ledger.record({**base, "status": "ok", "actual_usd": actual, "usage": res.get("usage"),
                       "output": str(out_path)})
    return {"dry_run": False, "image": str(out_path), "prompt_file": str(out_path.with_suffix(".prompt.txt")),
            "estimate_usd": est, "cost_from_usage_usd": actual, "usage": res.get("usage"),
            "spent_in_workdir_usd": ledger.spent(), "budget": info}


# ---------------------------------------------------------------- commands


def cmd_doctor(args) -> dict[str, Any]:
    b = blender_mod.find_blender()
    ff = video_mod.find_ffmpeg()
    try:
        import openai  # noqa: F401

        has_openai = True
    except ImportError:
        has_openai = False
    try:
        import fal_client  # noqa: F401

        has_fal = True
    except ImportError:
        has_fal = False
    keys = {"OPENAI_API_KEY": bool(os.environ.get("OPENAI_API_KEY")), "FAL_KEY": bool(os.environ.get("FAL_KEY"))}
    ready = {
        "image_stages": keys["OPENAI_API_KEY"] and has_openai,
        "model_stage": keys["FAL_KEY"] and has_fal,
        "blender_stages": b is not None,
        "turntable": b is not None and ff is not None,
    }
    return {
        "version": __version__,
        "keys_present": keys,
        "python_packages": {"openai": has_openai, "fal_client": has_fal},
        "blender": {"path": b, "version": blender_mod.blender_version(b) if b else None},
        "ffmpeg": ff,
        "image_model": images.image_model(None),
        "budget_usd": budget_from(None),
        "ready": ready,
    }


def cmd_estimate(args) -> dict[str, Any]:
    try:
        return _estimate(args)
    except ValueError as e:
        raise UsageError(f"cannot estimate: {e}") from e


def _estimate(args) -> dict[str, Any]:
    items = []
    for raw in args.stages.split(","):
        raw = raw.strip()
        if not raw:
            continue
        name, _, val = raw.partition("=")
        if name == "lookdev":
            n = int(val or 1)
            items.append({"stage": "lookdev", "count": n,
                          "usd": n * costs.estimate_image(args.lookdev_size, args.quality, 2)})
        elif name == "turnaround":
            n = int(val or 1)
            items.append({"stage": "turnaround", "count": n,
                          "usd": n * costs.estimate_image(args.turnaround_size, args.quality, 2)})
        elif name == "edit":
            n = int(val or 1)
            items.append({"stage": "edit", "count": n,
                          "usd": n * costs.estimate_image(args.turnaround_size, args.quality, 2)})
        elif name == "model":
            provs = list(fal3d.PROVIDERS) if val == "all" else (val.split("+") if val else [fal3d.DEFAULT_PROVIDER])
            for p in provs:
                if p not in fal3d.PROVIDERS:
                    raise UsageError(f"unknown provider {p!r}", {"providers": list(fal3d.PROVIDERS)})
                items.append({"stage": f"model:{p}", "count": 1, "usd": costs.estimate_model(p)})
        else:
            raise UsageError(f"unknown stage {name!r}; use lookdev, turnaround, edit, model[=tripo|all|a+b]")
    for it in items:
        it["usd"] = round(it["usd"], 4)
    total = round(sum(i["usd"] for i in items), 4)
    return {"items": items, "total_usd": total, "note": "estimates; providers bill the real amount"}


def cmd_lookdev(args) -> dict[str, Any]:
    wd = _wd(args)
    refs = [_existing(r, "--ref") for r in args.ref]
    if not refs and not (args.description or args.prompt_file):
        raise UsageError("give at least one --ref image, or --description / --prompt-file for text-only look-dev")
    prompt = _read_prompt_file(args.prompt_file) or prompts.lookdev_prompt(args.description, args.heads)
    out = Path(args.out).resolve() if args.out else next_version(wd.lookdev, "lookdev")
    return _paid_image(args, wd, stage="lookdev", prompt=prompt, inputs=refs, out_path=out, size=args.size)


def cmd_turnaround(args) -> dict[str, Any]:
    wd = _wd(args)
    look = _existing(args.lookdev, "--lookdev", wd) if args.lookdev else _latest_png(wd.lookdev, "lookdev_v")
    if look is None:
        raise UsageError("no look-dev image: pass --lookdev or run `img2fig lookdev` first")
    refs = [_existing(r, "--ref") for r in args.ref]
    prompt = _read_prompt_file(args.prompt_file) or prompts.turnaround_prompt(args.description, len(refs))
    out = Path(args.out).resolve() if args.out else next_version(wd.turnaround, "sheet")
    res = _paid_image(args, wd, stage="turnaround", prompt=prompt, inputs=[look, *refs], out_path=out,
                      size=args.size)
    if not res["dry_run"]:
        res["next"] = f"img2fig crop --workdir {wd.root} --sheet {out}"
    return res


def cmd_edit(args) -> dict[str, Any]:
    wd = _wd(args)
    src = _existing(args.image, "--image", wd) if args.image else _latest_png(wd.turnaround, "sheet_v")
    if src is None:
        raise UsageError("no image to edit: pass --image or run `img2fig turnaround` first")
    refs = [_existing(r, "--ref") for r in args.ref]
    if not args.fix and not args.prompt_file:
        raise UsageError("describe each correction with --fix (repeatable) or pass --prompt-file")
    prompt = _read_prompt_file(args.prompt_file) or prompts.edit_prompt(args.fix, len(refs))
    out = Path(args.out).resolve() if args.out else next_edit(src)
    size = args.size or _image_size_of(src)
    res = _paid_image(args, wd, stage="edit", prompt=prompt, inputs=[src, *refs], out_path=out, size=size)
    if not res["dry_run"]:
        res["next"] = f"compare {src.name} and {out.name}, then: img2fig crop --workdir {wd.root} --sheet {out}"
    return res


def cmd_crop(args) -> dict[str, Any]:
    wd = _wd(args)
    sheet = _existing(args.sheet, "--sheet", wd) if args.sheet else _latest_png(wd.turnaround, "sheet_v")
    if sheet is None:
        raise UsageError("no sheet: pass --sheet or run `img2fig turnaround` first")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else wd.views
    names = tuple(args.names.split(",")) if args.names else VIEW_NAMES
    info = crop.crop_sheet(sheet, out_dir, expected=args.expected, names=names, tolerance=args.tolerance,
                           pad_frac=args.pad, merge_gap_frac=args.merge_gap)
    from .workdir import write_json_atomic

    write_json_atomic(out_dir / "crop.json", info)
    return info


def cmd_model(args) -> dict[str, Any]:
    wd = _wd(args)
    provs = list(fal3d.PROVIDERS) if args.provider == "all" else args.provider.split(",")
    unknown = [p for p in provs if p not in fal3d.PROVIDERS]
    if unknown:
        raise UsageError(f"unknown provider(s) {unknown}", {"providers": list(fal3d.PROVIDERS)})
    views_dir = Path(args.views_dir).resolve() if args.views_dir else wd.views
    views = wd.view_paths(views_dir)
    fal3d.check_views(views)
    est = round(sum(costs.estimate_model(p) for p in provs), 4)
    ledger = Ledger(wd.ledger_path)
    budget = budget_from(args.budget)
    opts = {"face_limit": args.face_limit, "seed": args.seed}
    placeholder = {v: f"<uploaded {views[v].name}>" for v in VIEW_NAMES}
    plan = [{"provider": p, "endpoint": fal3d.PROVIDERS[p][0], "arguments": fal3d.PROVIDERS[p][1](placeholder, opts),
             "estimate_usd": costs.estimate_model(p)} for p in provs]
    if not args.execute:
        try:
            info = ledger.check(est, budget)
            info["within_budget"] = True
        except Img2FigError as e:
            info = {**e.detail, "within_budget": False}
        return {"dry_run": True, "plan": plan, "views": {k: str(v) for k, v in views.items()}, "estimate_usd": est,
                "budget": info, "next": f"rerun with --execute to spend about ${costs.round_up_cents(est):.2f}"}
    with ledger.locked():
        info = ledger.check(est, budget)
        fal = fal3d.fal_factory()
        urls = fal3d.upload_views(fal, views, views_dir / "uploads.json")
        results = fal3d.run_many(fal, provs, urls, wd.models, opts)
        for r in results:
            # failed jobs are recorded too: a job can fail after the provider has billed it
            ledger.record({"stage": "model", "provider": r["provider"], "endpoint": fal3d.PROVIDERS[r["provider"]][0],
                           "status": "ok" if r.get("ok") else "failed",
                           "estimate_usd": costs.estimate_model(r["provider"]), "actual_usd": None,
                           "output": r.get("model"), "error": (r.get("error") or {}).get("message")})
    out = {"dry_run": False, "results": results, "estimate_usd": est, "spent_in_workdir_usd": ledger.spent(),
           "budget": info}
    failed = [r for r in results if not r.get("ok")]
    if failed:
        raise ProviderError(f"{len(failed)} of {len(results)} provider(s) failed", out)
    ok_models = [r["model"] for r in results if r.get("model")]
    if ok_models:
        out["next"] = f"img2fig prep --workdir {wd.root} --model {ok_models[0]}"
    return out


def _guess_hint(model: Path) -> float | None:
    for p in (model.parent.name, model.parent.parent.name):
        if p in fal3d.PROVIDERS:
            return fal3d.PROVIDERS[p][2]
    return None


def _default_model(wd: WorkDir) -> Path | None:
    d = wd.root / "models"
    preferred = d / fal3d.DEFAULT_PROVIDER / "model.glb"
    if preferred.is_file():
        return preferred
    found = sorted(d.glob("*/model.glb"))
    return found[0] if len(found) == 1 else None


def _render(args, wd: WorkDir, input_path: Path, views: list[str], out_dir: Path, prefix: str = "",
            rot: float = 0.0) -> dict[str, Any]:
    cli = ["--input", str(input_path), "--out-dir", str(out_dir), "--views", ",".join(views),
           "--view-transform", args.view_transform, f"--rot={rot}", f"--prefix={prefix}"]
    return blender_mod.run_script("render.py", cli, wd.logs / f"render_{prefix or 'figure'}.log")


def _compare_sheet(wd: WorkDir, views_dir: Path, renders: dict[str, str], out: Path) -> dict[str, Any]:
    vp = wd.view_paths(views_dir)
    top = [(f"input {v}", vp[v]) for v in VIEW_NAMES]
    bottom = [(f"render {v}", Path(renders[v]) if v in renders else None) for v in VIEW_NAMES]
    return sheet_mod.build_sheet([top, bottom], out)


def _floats(spec: str, n: int, what: str) -> list[float]:
    try:
        vals = [float(v) for v in spec.split(",")]
    except ValueError:
        vals = []
    if len(vals) != n:
        raise UsageError(f"{what} must be {n} comma-separated numbers, got {spec!r} "
                         f"(write negative values as {what}=-0.08,0.6)")
    return vals


def cmd_prep(args) -> dict[str, Any]:
    wd = _wd(args)
    for b in args.blush:
        _floats(b, 2, "--blush")
    _floats(args.blush_color, 3, "--blush-color")
    model = _existing(args.model, "--model", wd) if args.model else _default_model(wd)
    if model is None:
        raise UsageError("no model: pass --model or run `img2fig model` first")
    views_dir = Path(args.views_dir).resolve() if args.views_dir else wd.root / "views"
    front = views_dir / "view_front.png"
    hint = _guess_hint(model) if args.provider_hint is None else args.provider_hint
    out: dict[str, Any] = {"model": str(model)}
    if args.rot == "auto":
        if front.is_file():
            o = blender_mod.run_script("orient.py", ["--model", str(model), "--out-dir", str(wd.figure / "orient")],
                                       wd.logs / "orient.log")
            choice = orientation.choose(front, o["renders"], hint=hint)
            rot = choice["rotation_deg"]
            out["orientation"] = {**choice, "renders": o["renders"], "reference": str(front)}
        else:
            rot = hint or 0.0
            out["orientation"] = {"rotation_deg": rot, "note": f"no front view at {front}; used the provider "
                                  f"default ({rot:g} deg). Check the front render."}
    else:
        try:
            rot = float(args.rot)
        except ValueError as e:
            raise UsageError("--rot must be 'auto' or a number of degrees") from e
        out["orientation"] = {"rotation_deg": rot, "note": "given with --rot"}
    blend = wd.figure / "figure.blend"
    glb = wd.figure / "figure_clean.glb"
    cli = ["--model", str(model), "--out-blend", str(blend), "--out-glb", str(glb), f"--rot={rot}",
           "--faces", str(args.faces), "--grade", args.grade, f"--keep-gloss={args.keep_gloss}",
           f"--blush-size={args.blush_size}", f"--blush-color={args.blush_color}"]
    for b in args.blush:
        cli.append(f"--blush={b}")
    out["prep"] = blender_mod.run_script("prep.py", cli, wd.logs / "prep.log")
    if not args.no_render:
        r = _render(args, wd, blend, list(ALL_VIEWS), wd.renders)
        out["renders"] = r["renders"]
        if all((views_dir / f"view_{v}.png").is_file() for v in VIEW_NAMES):
            out["compare"] = _compare_sheet(wd, views_dir, r["renders"], wd.renders / "compare.jpg")
    out["next"] = ("look at renders/compare.jpg (input views vs renders) and the face render; fix wrong details at "
                   "the image stage (img2fig edit), not in the mesh")
    return out


def cmd_render(args) -> dict[str, Any]:
    wd = _wd(args)
    inp = _existing(args.input, "--input", wd) if args.input else _existing(wd.root / "figure" / "figure.blend",
                                                                          "figure/figure.blend")
    views = args.views.split(",")
    bad = [v for v in views if v not in ALL_VIEWS]
    if bad:
        raise UsageError(f"unknown views {bad}", {"views": list(ALL_VIEWS)})
    out_dir = Path(args.out_dir).resolve() if args.out_dir else wd.renders
    res = _render(args, wd, inp, views, out_dir, args.prefix, args.rot)
    if args.compare:
        views_dir = Path(args.views_dir).resolve() if args.views_dir else wd.root / "views"
        res["compare"] = _compare_sheet(wd, views_dir, res["renders"], out_dir / f"{args.prefix}compare.jpg")
    return res


def cmd_sheet(args) -> dict[str, Any]:
    wd = _wd(args)
    rows = []
    for spec in args.row:
        paths = [Path(p).expanduser().resolve() for p in spec.split(",") if p]
        rows.append([(p.stem, p) for p in paths])
    if not rows:
        vp = wd.view_paths()
        rows = [[(f"input {v}", vp[v]) for v in VIEW_NAMES],
                [(f"render {v}", wd.root / "renders" / f"{v}.png") for v in VIEW_NAMES]]
    out = Path(args.out).resolve() if args.out else wd.renders / "sheet.jpg"
    return sheet_mod.build_sheet(rows, out, row_height=args.row_height)


def cmd_turntable(args) -> dict[str, Any]:
    wd = _wd(args)
    inp = _existing(args.input, "--input", wd) if args.input else _existing(wd.root / "figure" / "figure.blend",
                                                                          "figure/figure.blend")
    if args.fps <= 0 or args.frames <= 0 or args.samples <= 0:
        raise UsageError("--fps, --frames and --samples must be positive")
    if not video_mod.find_ffmpeg():
        raise MissingDependency("ffmpeg not found on PATH (or set IMG2FIG_FFMPEG)")
    frames_dir = wd.turntable / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    cli = ["--input", str(inp), "--out-dir", str(frames_dir), "--frames", str(args.frames), "--res", args.res,
           "--samples", str(args.samples), "--view-transform", args.view_transform, f"--rot={args.rot}"]
    if args.no_base:
        cli.append("--no-base")
    r = blender_mod.run_script("turntable.py", cli, wd.logs / "turntable.log")
    out = Path(args.out).resolve() if args.out else wd.turntable / "turntable.mp4"
    v = video_mod.encode(frames_dir, r["pattern"], args.fps, out, wd.logs / "ffmpeg.log")
    if not args.keep_frames:
        shutil.rmtree(frames_dir)
    return {**v, "frames": r["frames"], "seconds": round(r["frames"] / args.fps, 2),
            "frames_dir": str(frames_dir) if args.keep_frames else None}


def cmd_spend(args) -> dict[str, Any]:
    wd = _wd(args)
    ledger = Ledger(wd.ledger_path)
    entries = ledger.load()["entries"]
    budget = budget_from(args.budget)
    spent = ledger.spent()
    return {"ledger": str(wd.ledger_path), "spent_usd": spent, "budget_usd": budget,
            "remaining_usd": round(budget - spent, 4), "calls": len(entries),
            "failed_calls": sum(1 for e in entries if e.get("status") == "failed"),
            "by_stage": {k: round(sum(float(e.get("actual_usd") if e.get("actual_usd") is not None
                                            else e.get("estimate_usd", 0)) for e in entries
                                      if e.get("stage") == k), 4)
                         for k in sorted({e.get("stage") for e in entries})},
            "note": "image calls count the cost implied by token usage; 3D calls and failed calls count the estimate"}


def cmd_point(args) -> dict[str, Any]:
    """Front-render pixel -> figure fractions for --blush. Assumes the default 900x1200 front render."""
    from .blender_geometry import pixel_to_figure

    return pixel_to_figure(args.x, args.y, args.res)


# ---------------------------------------------------------------- parser


def build_parser() -> JsonArgParser:
    p = JsonArgParser(prog="img2fig", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=__version__)
    common = JsonArgParser(add_help=False)
    common.add_argument("--workdir", help=f"work directory (default $IMG2FIG_WORKDIR or ./{DEFAULT_WORKDIR})")
    paid = JsonArgParser(add_help=False)
    paid.add_argument("--execute", action="store_true", help="actually call the paid API (default: plan only)")
    paid.add_argument("--budget", type=float,
                      help="USD cap for this work directory (default $IMG2FIG_BUDGET_USD, else 5)")
    img = JsonArgParser(add_help=False)
    img.add_argument("--quality", default="high", help="low|medium|high|xhigh|max (default high)")
    img.add_argument("--image-model", help=f"default $IMG2FIG_IMAGE_MODEL or {images.DEFAULT_MODEL}")
    img.add_argument("--prompt-file", help="use this prompt instead of the built-in template")
    img.add_argument("--out", help="output path (default: next version in the work directory)")
    render_opts = JsonArgParser(add_help=False)
    render_opts.add_argument("--view-transform", default="agx", choices=["agx", "standard"])

    sub = p.add_subparsers(dest="command", required=True, parser_class=JsonArgParser)

    s = sub.add_parser("doctor", parents=[common], help="check keys, Python extras, Blender and ffmpeg")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("estimate", parents=[common], help="cost estimate for a set of stages (no network)")
    s.add_argument("--stages", default="lookdev,turnaround,edit,model",
                   help="comma list: lookdev[=N], turnaround[=N], edit[=N], model[=tripo|all|tripo+rodin]")
    s.add_argument("--quality", default="high")
    s.add_argument("--lookdev-size", default="2048x1152")
    s.add_argument("--turnaround-size", default="3840x2160")
    s.set_defaults(func=cmd_estimate)

    s = sub.add_parser("lookdev", parents=[common, paid, img], help="look-dev render of the character as a figure")
    s.add_argument("--ref", action="append", default=[], help="reference image of the character (repeatable)")
    s.add_argument("--description", help="details the figure must keep (hair, outfit, accessories and sides)")
    s.add_argument("--heads", type=float, default=3, help="figure proportions in heads tall (default 3)")
    s.add_argument("--size", default="2048x1152")
    s.set_defaults(func=cmd_lookdev)

    s = sub.add_parser("turnaround", parents=[common, paid, img], help="four-view sheet from the look-dev image")
    s.add_argument("--lookdev", help="look-dev image (default: latest in the work directory)")
    s.add_argument("--ref", action="append", default=[], help="original character reference (repeatable)")
    s.add_argument("--description", help="details the figure must keep")
    s.add_argument("--size", default="3840x2160")
    s.set_defaults(func=cmd_turnaround)

    s = sub.add_parser("edit", parents=[common, paid, img], help="targeted fixes on a sheet (keeps the rest)")
    s.add_argument("--image", help="image to edit (default: latest sheet)")
    s.add_argument("--fix", action="append", default=[], help="one correction, in words (repeatable)")
    s.add_argument("--ref", action="append", default=[], help="image showing how the fixed part should look")
    s.add_argument("--size", help="default: the input image's size")
    s.set_defaults(func=cmd_edit)

    s = sub.add_parser("crop", parents=[common], help="split a sheet into view_front/left/back/right.png")
    s.add_argument("--sheet", help="default: latest sheet in the work directory")
    s.add_argument("--out-dir")
    s.add_argument("--expected", type=int, default=4)
    s.add_argument("--names", help="comma list of view names in sheet order (default front,left,back,right)")
    s.add_argument("--tolerance", type=int, default=24, help="max channel difference still counted as background")
    s.add_argument("--pad", type=float, default=0.06, help="padding as a fraction of the widest figure")
    s.add_argument("--merge-gap", type=float, default=0.006, help="merge runs closer than this fraction of width")
    s.set_defaults(func=cmd_crop)

    s = sub.add_parser("model", parents=[common, paid], help="multiview image-to-3D through fal")
    s.add_argument("--provider", default=fal3d.DEFAULT_PROVIDER,
                   help=f"one of {', '.join(fal3d.PROVIDERS)}, a comma list, or 'all' for a bake-off")
    s.add_argument("--views-dir")
    s.add_argument("--face-limit", type=int, help="target face count (Tripo, Hunyuan, Meshy)")
    s.add_argument("--seed", type=int, help="geometry seed (Tripo)")
    s.set_defaults(func=cmd_model)

    s = sub.add_parser("prep", parents=[common, render_opts], help="orient, normalise, decimate, grade; renders")
    s.add_argument("--model", help="default: models/tripo/model.glb")
    s.add_argument("--rot", default="auto", help="'auto' (compare with the front view) or degrees about Z")
    s.add_argument("--provider-hint", type=float, help="fallback rotation when auto is ambiguous")
    s.add_argument("--views-dir")
    s.add_argument("--faces", type=int, default=350_000, help="decimate to at most this many faces")
    s.add_argument("--grade", default="matte", choices=["matte", "light", "dark", "none"])
    s.add_argument("--keep-gloss", type=float, default=0.0,
                   help="keep roughness and metal where the source roughness is below this (e.g. 0.3)")
    s.add_argument("--blush", action="append", default=[], help="X,Z cheek point in figure fractions (repeatable)")
    s.add_argument("--blush-size", type=float, default=1.0)
    s.add_argument("--blush-color", default="1.0,0.50,0.56")
    s.add_argument("--no-render", action="store_true")
    s.set_defaults(func=cmd_prep)

    s = sub.add_parser("render", parents=[common, render_opts], help="review renders from fixed cameras")
    s.add_argument("--input", help="figure .blend or a model file (default figure/figure.blend)")
    s.add_argument("--views", default=",".join(ALL_VIEWS))
    s.add_argument("--rot", type=float, default=0.0, help="extra rotation about Z (for raw model files)")
    s.add_argument("--out-dir")
    s.add_argument("--prefix", default="")
    s.add_argument("--compare", action="store_true", help="also build a sheet against the input views")
    s.add_argument("--views-dir")
    s.set_defaults(func=cmd_render)

    s = sub.add_parser("sheet", parents=[common], help="comparison sheet from rows of images")
    s.add_argument("--row", action="append", default=[], help="comma list of images for one row (repeatable)")
    s.add_argument("--out")
    s.add_argument("--row-height", type=int, default=600)
    s.set_defaults(func=cmd_sheet)

    s = sub.add_parser("turntable", parents=[common, render_opts], help="360-degree turntable video")
    s.add_argument("--input", help="default figure/figure.blend")
    s.add_argument("--frames", type=int, default=192)
    s.add_argument("--fps", type=int, default=32)
    s.add_argument("--res", default="854x480")
    s.add_argument("--samples", type=int, default=32)
    s.add_argument("--rot", type=float, default=0.0)
    s.add_argument("--no-base", action="store_true", help="no acrylic display base")
    s.add_argument("--out")
    s.add_argument("--keep-frames", action="store_true")
    s.set_defaults(func=cmd_turntable)

    s = sub.add_parser("spend", parents=[common], help="money spent from this work directory and budget left")
    s.add_argument("--budget", type=float)
    s.set_defaults(func=cmd_spend)

    s = sub.add_parser("point", parents=[common], help="front-render pixel to figure fractions (for --blush)")
    s.add_argument("--x", type=float, required=True)
    s.add_argument("--y", type=float, required=True)
    s.add_argument("--res", default="900x1200")
    s.set_defaults(func=cmd_point)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    command = None
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        command = args.command
        result = args.func(args)
        emit({"ok": True, "command": command, **result})
        return EXIT_OK
    except Img2FigError as e:
        emit({"ok": False, "command": command, "exit_code": e.exit_code, "error": e.to_json()})
        return e.exit_code
    except KeyboardInterrupt:
        emit({"ok": False, "command": command, "exit_code": 130, "error": {"type": "interrupted"}})
        return 130
    except Exception as e:  # noqa: BLE001 - last resort, keep the one-JSON contract
        emit({"ok": False, "command": command, "exit_code": EXIT_INTERNAL,
              "error": {"type": "internal_error", "message": f"{type(e).__name__}: {e}",
                        "traceback_tail": traceback.format_exc()[-3000:]}})
        return EXIT_INTERNAL


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
