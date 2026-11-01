"""Error types, exit codes and the one-JSON-object-per-command output contract."""

from __future__ import annotations

import json
import sys
from typing import Any

EXIT_OK = 0
EXIT_PROVIDER = 1  # a paid API (OpenAI, fal) returned an error or could not be reached
EXIT_USAGE = 2  # bad arguments or missing input files
EXIT_BUDGET = 3  # the call would exceed the budget; nothing was sent
EXIT_MISSING = 4  # a required key, binary or Python extra is missing
EXIT_TOOL = 5  # Blender or ffmpeg exited non-zero
EXIT_CHECK = 6  # a check on the result failed (e.g. the sheet did not split into the expected views)
EXIT_INTERNAL = 7  # a bug in img2fig itself; the traceback tail is in the output

EXIT_CODES = {
    "ok": EXIT_OK,
    "provider_error": EXIT_PROVIDER,
    "usage_error": EXIT_USAGE,
    "budget_exceeded": EXIT_BUDGET,
    "missing_dependency": EXIT_MISSING,
    "tool_error": EXIT_TOOL,
    "check_failed": EXIT_CHECK,
    "internal_error": EXIT_INTERNAL,
}


class Img2FigError(Exception):
    """Base error. `detail` is passed through to the JSON output unchanged."""

    kind = "error"
    exit_code = EXIT_PROVIDER

    def __init__(self, message: str, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.kind, "message": self.message}
        if self.detail:
            out["detail"] = self.detail
        return out


class UsageError(Img2FigError):
    kind = "usage_error"
    exit_code = EXIT_USAGE


class ProviderError(Img2FigError):
    kind = "provider_error"
    exit_code = EXIT_PROVIDER


class BudgetError(Img2FigError):
    kind = "budget_exceeded"
    exit_code = EXIT_BUDGET


class MissingDependency(Img2FigError):
    kind = "missing_dependency"
    exit_code = EXIT_MISSING


class ToolError(Img2FigError):
    kind = "tool_error"
    exit_code = EXIT_TOOL


class CheckFailed(Img2FigError):
    kind = "check_failed"
    exit_code = EXIT_CHECK


def emit(obj: dict[str, Any], stream=None) -> None:
    """Print exactly one JSON object to stdout."""
    stream = stream or sys.stdout
    stream.write(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n")
    stream.flush()


def log(msg: str) -> None:
    """Progress messages go to stderr so stdout stays one JSON object."""
    sys.stderr.write(msg.rstrip() + "\n")
    sys.stderr.flush()
