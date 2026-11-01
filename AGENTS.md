# AGENTS.md

Notes and invariants for coding agents working in `https://github.com/grapeot/image-to-figure-skill`.

## Commands

Check for a `.venv` at the repository root first. Activate it if present; otherwise create it with uv. Install dependencies with `uv pip install`.

```bash
uv venv
source .venv/bin/activate
uv pip install -e '.[dev,providers,print]'
ruff check .
python -m pytest -q                  # offline; Blender tests skip when no Blender is found
IMG2FIG_LIVE=1 python -m pytest -m live -q   # opt-in, spends money, needs OPENAI_API_KEY and FAL_KEY
```

The default suite is offline: it never contacts OpenAI or fal and needs no keys. Provider clients are replaced by fakes in `tests/conftest.py`.

## Repository invariants

1. **Default branch is `master`.** Every change goes through a feature branch and a PR into `master`. Branch protection requires a PR and applies to administrators.
2. **No spend without `--execute`.** Every command that calls a paid API prints a plan with a cost estimate and stops unless `--execute` is given. The budget check (`--budget` / `IMG2FIG_BUDGET_USD` against the work directory's spend ledger) runs before every paid call.
3. **Keys come from environment variables only** (`OPENAI_API_KEY`, `FAL_KEY`). Never read them from files in the repository, never print them, never put them in JSON output, logs or fixtures.
4. **One JSON object per command.** Every `img2fig` subcommand, including on error, prints exactly one JSON object to stdout (`--help` excepted). Progress goes to stderr. Exit codes are documented in `docs/design.md` and must stay stable.
5. **Pass errors through.** Keep the provider's HTTP status, error type and message, and the external tool's exit code and stderr tail, in error output.
6. **Blender is an external binary.** Scripts under `src/image_to_figure/blender_scripts/` run inside Blender's Python and must not import the package. They take arguments after `--` and write a JSON result file.
7. **No third-party or personal content in the repository.** No character designs, renders, models or reference images you do not own, no private paths, no keys. Test inputs are synthetic and generated at test time. Documentation images, if any, must be original.
8. **Update `docs/working.md`** with a dated changelog entry when you change behavior.

## Layout

```text
image-to-figure-skill/
├── README.md
├── AGENTS.md
├── LICENSE
├── pyproject.toml
├── docs/design.md                    # goals, boundaries, architecture, exit codes, costs
├── docs/test.md                      # what the suites cover and what counts as verified
├── docs/working.md                   # changelog and lessons learned
├── skills/image-to-figure/SKILL.md   # agent instructions
├── src/image_to_figure/              # CLI and library
│   └── blender_scripts/              # run inside Blender
└── tests/
```
