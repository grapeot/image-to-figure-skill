"""Cost estimates for paid calls.

All numbers are estimates in USD, recorded on 2026-10-31 from the providers' public price pages. They are used for
the dry-run plan and the budget guard, not for billing. Check the provider dashboards for real charges.
"""

from __future__ import annotations

import math
import re
from typing import Any

# OpenAI GPT Image: priced per token. Output-token counts at 1024x1024 for each quality tier, from OpenAI's published
# examples. Larger images do not cost proportionally more: at high quality we measured 1372 output tokens at 1024x1536,
# 1413 at 2048x1152 and 3336 at 3840x2160 (2026-10-31), so the estimate scales with the square root of the megapixels,
# which stays above every measurement. Rates are per million tokens.
IMAGE_OUTPUT_TOKENS_PER_MP = {"low": 196, "medium": 439, "high": 1756, "xhigh": 3122, "max": 7024}
IMAGE_RATE_OUTPUT = 30.0  # USD per 1M image output tokens
IMAGE_RATE_INPUT_IMAGE = 8.0  # USD per 1M image input tokens
IMAGE_RATE_INPUT_TEXT = 5.0  # USD per 1M text input tokens
INPUT_IMAGE_TOKENS_EST = 1600  # tokens per reference image (high-fidelity inputs can cost more)
SAFETY_MARGIN = 1.25  # the estimate errs high so the budget guard errs safe

# fal.ai multiview image-to-3D, per call with the arguments this tool sends (see fal3d.PROVIDERS).
MODEL_COST_USD = {
    "tripo": 0.60,  # HD texture 0.40 + detailed geometry 0.20
    "hunyuan": 0.675,
    "rodin": 1.20,
    "meshy": 1.52,
}

_SIZE_RE = re.compile(r"^(\d{3,5})x(\d{3,5})$")


def parse_size(size: str) -> tuple[int, int]:
    m = _SIZE_RE.match(size.strip())
    if not m:
        raise ValueError(f"size must look like 2048x1152, got {size!r}")
    return int(m.group(1)), int(m.group(2))


def estimate_image(size: str, quality: str, n_input_images: int, prompt_chars: int = 2000) -> float:
    """Upper-leaning estimate for one generated or edited image."""
    if quality not in IMAGE_OUTPUT_TOKENS_PER_MP:
        raise ValueError(f"unknown quality {quality!r}; choose from {sorted(IMAGE_OUTPUT_TOKENS_PER_MP)}")
    w, h = parse_size(size)
    mp = (w * h) / (1024 * 1024)
    out_tokens = IMAGE_OUTPUT_TOKENS_PER_MP[quality] * math.sqrt(mp)
    usd = out_tokens * IMAGE_RATE_OUTPUT / 1e6
    usd += n_input_images * INPUT_IMAGE_TOKENS_EST * IMAGE_RATE_INPUT_IMAGE / 1e6
    usd += (prompt_chars / 4) * IMAGE_RATE_INPUT_TEXT / 1e6
    return round(usd * SAFETY_MARGIN, 4)


def cost_from_usage(usage: dict[str, Any] | None) -> float | None:
    """Cost implied by the token usage OpenAI returns with an image response, at the rates above."""
    if not usage:
        return None
    out_tokens = usage.get("output_tokens")
    if out_tokens is None:
        return None
    details = usage.get("input_tokens_details") or {}
    img_in = details.get("image_tokens")
    txt_in = details.get("text_tokens")
    if img_in is None and txt_in is None:
        txt_in = usage.get("input_tokens", 0)
        img_in = 0
    usd = out_tokens * IMAGE_RATE_OUTPUT / 1e6 + (img_in or 0) * IMAGE_RATE_INPUT_IMAGE / 1e6
    usd += (txt_in or 0) * IMAGE_RATE_INPUT_TEXT / 1e6
    return round(usd, 4)


def estimate_model(provider: str) -> float:
    if provider not in MODEL_COST_USD:
        raise ValueError(f"unknown provider {provider!r}; choose from {sorted(MODEL_COST_USD)}")
    return MODEL_COST_USD[provider]


def round_up_cents(usd: float) -> float:
    return math.ceil(usd * 100) / 100
