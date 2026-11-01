"""Multiview image-to-3D through fal.ai.

Each provider takes the four views (front, left, back, right) and returns a textured GLB. Tripo is the default; the
others are kept for a bake-off. Uploads are cached by file hash, so an edited view is uploaded again while unchanged
views are not.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from .errors import MissingDependency, ProviderError, UsageError, log
from .workdir import VIEW_NAMES, write_json_atomic

DEFAULT_PROVIDER = "tripo"


def _tripo(u: dict[str, str], opts: dict[str, Any]) -> dict[str, Any]:
    args = {
        "image_urls": [u[v] for v in VIEW_NAMES],
        "texture": True,
        "pbr": True,
        "texture_quality": "detailed",
        "geometry_quality": "detailed",
        "texture_alignment": "original_image",
    }
    if opts.get("face_limit"):
        args["face_limit"] = int(opts["face_limit"])
    if opts.get("seed") is not None:
        args["model_seed"] = int(opts["seed"])
    return args


def _hunyuan(u: dict[str, str], opts: dict[str, Any]) -> dict[str, Any]:
    return {
        "input_image_url": u["front"],
        "left_image_url": u["left"],
        "back_image_url": u["back"],
        "right_image_url": u["right"],
        "generate_type": "Normal",
        "face_count": int(opts.get("face_limit") or 500000),
    }


def _rodin(u: dict[str, str], opts: dict[str, Any]) -> dict[str, Any]:
    return {
        "image_urls": [u[v] for v in VIEW_NAMES],
        "tier": "Gen-2.5-High",
        "material": "PBR",
        "geometry_file_format": "glb",
        "texture_delight": True,
        "preview_render": True,
    }


def _meshy(u: dict[str, str], opts: dict[str, Any]) -> dict[str, Any]:
    return {
        "image_urls": [u[v] for v in VIEW_NAMES],
        "should_texture": True,
        "should_remesh": True,
        "target_polycount": int(opts.get("face_limit") or 100000),
        "topology": "triangle",
        "symmetry_mode": "auto",
    }


# name -> (fal endpoint, argument builder, rotation about Z that makes the imported GLB face -Y, if known)
PROVIDERS: dict[str, tuple[str, Callable[[dict[str, str], dict[str, Any]], dict[str, Any]], float | None]] = {
    "tripo": ("tripo3d/h3.1/multiview-to-3d", _tripo, -90.0),
    "hunyuan": ("fal-ai/hunyuan-3d/v3.1/pro/image-to-3d", _hunyuan, None),
    "rodin": ("fal-ai/hyper3d/rodin/v2.5", _rodin, None),
    "meshy": ("meshy/v7.1/multi-image-to-3d", _meshy, None),
}


def _default_fal() -> Any:
    if not os.environ.get("FAL_KEY"):
        raise MissingDependency("FAL_KEY is not set in the environment", {"env": "FAL_KEY"})
    try:
        import fal_client
    except ImportError as e:  # pragma: no cover
        raise MissingDependency(
            "the fal-client package is not installed; run: uv pip install -e '.[providers]'", {"import_error": str(e)}
        ) from e
    return fal_client


# Tests replace these with fakes.
fal_factory: Callable[[], Any] = _default_fal


def _download(url: str, dest: Path) -> None:
    urllib.request.urlretrieve(url, dest)  # noqa: S310 - provider result URLs


downloader: Callable[[str, Path], None] = _download


def _provider_error(e: Exception, provider: str) -> ProviderError:
    detail: dict[str, Any] = {"provider": provider, "exception": type(e).__name__, "message": str(e)}
    for attr in ("status_code", "error_type"):
        v = getattr(e, attr, None)
        if v is not None:
            detail[attr] = v
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            detail["body"] = resp.text[:4000]
        except Exception:  # noqa: BLE001
            pass
    status = detail.get("status_code")
    prefix = f"fal returned HTTP {status}" if status else "fal request failed"
    return ProviderError(f"{prefix} for {provider}: {e}", detail)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_views(views: dict[str, Path]) -> None:
    missing = [str(p) for p in views.values() if not p.is_file()]
    if missing:
        raise UsageError("view images missing; run `img2fig crop` first", {"missing": missing})


def upload_views(fal: Any, views: dict[str, Path], cache_path: Path) -> dict[str, str]:
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    urls: dict[str, str] = {}
    for name in VIEW_NAMES:
        p = views[name]
        digest = sha256(p)
        hit = cache.get(digest)
        if hit:
            urls[name] = hit
            continue
        try:
            url = fal.upload_file(p)
        except Exception as e:  # noqa: BLE001
            raise _provider_error(e, "upload") from e
        cache[digest] = url
        urls[name] = url
    write_json_atomic(cache_path, cache)
    return urls


def _urls_in(obj: Any):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "url" and isinstance(v, str):
                yield v
            else:
                yield from _urls_in(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _urls_in(v)


def run_provider(
    fal: Any, provider: str, urls: dict[str, str], out_dir: Path, opts: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Run one provider, save result.json and every file it links, and return a summary.

    Raises ProviderError on failure (the caller decides whether to continue with other providers).
    """
    endpoint, build, rot = PROVIDERS[provider]
    args = build(urls, opts or {})
    d = out_dir / provider
    d.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    last = {"state": None}

    def on_update(status: Any) -> None:  # progress to stderr, once per state change
        state = type(status).__name__
        if state != last["state"]:
            last["state"] = state
            log(f"[{provider}] {state} after {time.time() - t0:.0f}s")

    try:
        res = fal.subscribe(endpoint, arguments=args, with_logs=False, on_queue_update=on_update, interval=2.0)
    except Exception as e:  # noqa: BLE001
        (d / "error.json").write_text(json.dumps({"error": repr(e)}, indent=2))
        raise _provider_error(e, provider) from e
    write_json_atomic(d / "result.json", res)
    files: list[str] = []
    failed: list[dict[str, str]] = []
    for url in sorted(set(_urls_in(res))):
        name = url.split("?")[0].rsplit("/", 1)[-1] or "file"
        try:
            downloader(url, d / name)
            files.append(name)
        except Exception as e:  # noqa: BLE001
            failed.append({"url": url, "error": repr(e)})
    glbs = sorted(f for f in files if f.lower().endswith(".glb"))
    model = None
    if glbs:
        # prefer the largest GLB (some providers also return a low-poly or base mesh)
        glbs.sort(key=lambda f: (d / f).stat().st_size, reverse=True)
        model = d / "model.glb"
        if model.exists() or model.is_symlink():
            model.unlink()
        try:
            os.link(d / glbs[0], model)
        except OSError:
            shutil.copyfile(d / glbs[0], model)
    summary = {
        "provider": provider,
        "endpoint": endpoint,
        "seconds": round(time.time() - t0, 1),
        "result_json": str(d / "result.json"),
        "files": files,
        "model": str(model) if model else None,
        "orientation_hint_deg": rot,
    }
    if failed:
        summary["download_failures"] = failed
    if model is None:
        summary["warning"] = "no GLB in the provider result; see result.json"
    return summary


def run_many(fal: Any, providers: list[str], urls: dict[str, str], out_dir: Path, opts: dict[str, Any]) -> list[dict]:
    """Run providers in parallel; one failure does not stop the others."""

    def one(p: str) -> dict[str, Any]:
        try:
            return {"ok": True, **run_provider(fal, p, urls, out_dir, opts)}
        except ProviderError as e:
            return {"ok": False, "provider": p, "error": e.to_json()}

    with ThreadPoolExecutor(max(1, len(providers))) as ex:
        return list(ex.map(one, providers))
