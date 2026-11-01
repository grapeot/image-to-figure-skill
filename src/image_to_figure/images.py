"""OpenAI image generation and editing, called directly through the official SDK."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any, Callable

from .errors import MissingDependency, ProviderError, UsageError

DEFAULT_MODEL = "gpt-image-2.5-sunburst"
QUALITIES = ("low", "medium", "high", "xhigh", "max")


def image_model(arg: str | None) -> str:
    return arg or os.environ.get("IMG2FIG_IMAGE_MODEL") or DEFAULT_MODEL


def _default_client() -> Any:
    if not os.environ.get("OPENAI_API_KEY"):
        raise MissingDependency("OPENAI_API_KEY is not set in the environment", {"env": "OPENAI_API_KEY"})
    try:
        import openai
    except ImportError as e:  # pragma: no cover - depends on the install
        raise MissingDependency(
            "the openai package is not installed; run: uv pip install -e '.[providers]'", {"import_error": str(e)}
        ) from e
    return openai.OpenAI()


# Tests replace this with a fake.
client_factory: Callable[[], Any] = _default_client


def _usage_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    for attr in ("model_dump", "dict"):
        fn = getattr(usage, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001 - best effort
                pass
    return None


def _provider_error(e: Exception) -> ProviderError:
    detail: dict[str, Any] = {"exception": type(e).__name__, "message": str(e)}
    for attr in ("status_code", "code", "type", "param", "request_id"):
        v = getattr(e, attr, None)
        if v is not None:
            detail[attr] = v
    body = getattr(e, "body", None)
    if body is not None:
        detail["body"] = body
    resp = getattr(e, "response", None)
    if resp is not None and "body" not in detail:
        try:
            detail["body"] = resp.text[:4000]
        except Exception:  # noqa: BLE001
            pass
    status = detail.get("status_code")
    prefix = f"OpenAI returned HTTP {status}" if status else "OpenAI request failed"
    return ProviderError(f"{prefix}: {e}", detail)


def generate(
    *,
    prompt: str,
    out_path: Path,
    size: str,
    quality: str = "high",
    model: str | None = None,
    inputs: list[Path] | None = None,
) -> dict[str, Any]:
    """Generate (no inputs) or edit (with input images) one image and save it as PNG.

    Returns {"path", "model", "size", "quality", "usage", "endpoint"}.
    """
    if quality not in QUALITIES:
        raise UsageError(f"quality must be one of {QUALITIES}", {"quality": quality})
    inputs = list(inputs or [])
    for p in inputs:
        if not Path(p).is_file():
            raise UsageError(f"input image not found: {p}", {"path": str(p)})
    model = image_model(model)
    client = client_factory()
    files = []
    try:
        if inputs:
            files = [Path(p).open("rb") for p in inputs]
            endpoint = "images.edit"
            resp = client.images.edit(
                model=model, image=files, prompt=prompt, quality=quality, size=size, output_format="png"
            )
        else:
            endpoint = "images.generate"
            resp = client.images.generate(model=model, prompt=prompt, quality=quality, size=size, output_format="png")
    except (MissingDependency, UsageError):
        raise
    except Exception as e:  # noqa: BLE001 - every SDK error is passed through
        raise _provider_error(e) from e
    finally:
        for f in files:
            f.close()

    data = getattr(resp, "data", None) or []
    b64 = next((getattr(d, "b64_json", None) for d in data if getattr(d, "b64_json", None)), None)
    if not b64:
        raise ProviderError("OpenAI response contained no image data", {"response": str(resp)[:2000]})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(base64.b64decode(b64))
    return {
        "path": str(out_path),
        "model": model,
        "size": size,
        "quality": quality,
        "endpoint": endpoint,
        "usage": _usage_dict(getattr(resp, "usage", None)),
    }
