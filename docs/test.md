# Test strategy

```bash
python -m pytest -q                          # offline suite; Blender tests run only where Blender is installed
IMG2FIG_LIVE=1 python -m pytest -m live -q   # opt-in, real APIs, about $0.02
```

The default suite needs no keys and never touches the network: `tests/conftest.py` removes the key variables and replaces the OpenAI client factory, the fal client factory and the downloader with fakes that fail loudly if a test reaches them unexpectedly. CI runs ruff and the offline suite on Python 3.10 and 3.12; it has no Blender, so the Blender tests are skipped there.

## Unit tests (offline)

- Costs and ledger: image estimates scale with pixels, quality tier and reference count; size parsing; the cost implied by returned usage; every provider has a price; the ledger sums actual over estimate, refuses a call that would exceed the budget (exit 3) and reports remaining money; versioned file names for sheets and edits.
- Prompts: the no-invented-details clause, the four view names in order, the shared ground line, reference numbering, edit fixes numbered, at least one fix required.
- Crop: four equal-size views from a synthetic sheet, shared ground line, figure centred in its view, touching figures and wrong counts fail with the runs and a hint (exit 6), mild background noise tolerated, neighbour pixels painted with the background, warning for narrow views.
- Providers: generate vs edit endpoint selection, model override, OpenAI error pass-through (status and body), missing key (exit 4), missing input; fal upload cache keyed by file hash (an edited view is uploaded again, unchanged views are not), Tripo argument order and options, result download and largest-GLB selection, a failing provider does not stop the others in a bake-off, results without a GLB.
- Orientation and geometry: the face-matching render wins over sides and back; ambiguous scores fall back to the provider hint; pixel-to-figure conversion; the Python-side camera constants match the Blender-side ones.
- Tools: ffmpeg command, missing ffmpeg, Blender runner with a fake binary (result read, non-zero exit passes stderr through as exit 5, missing binary as exit 4), Blender scripts never import the package.
- CLI contract: every command prints exactly one JSON object whose `ok` matches the exit code; default and invalid budgets, corrupt ledger, failed paid calls recorded as failed, `spend` summary, relative paths resolved against the work directory; dry runs spend nothing and write nothing; `--execute` writes the image, the prompt file and a ledger entry; budget refusal happens before any request (flag and environment variable); provider errors exit 1 without a ledger entry; defaults pick the latest file of the previous stage; edits keep the input size; bake-off partial failure exits 1 and records only the providers that succeeded; internal errors keep the contract (exit 7).
- Source scan: no API-key-like strings or machine paths in tracked files, keys never read from dotenv files, no binary assets tracked.

## Blender integration (offline, needs Blender)

`tests/test_blender_integration.py` builds a synthetic textured figure with `tests/blender_fixtures/make_figure.py` in two orientations (facing -Y and facing +X), renders reference views from the correct one, and runs `prep` on the rotated one: the orientation check must pick 270 degrees unambiguously, decimation must respect `--faces`, height must be 2.9 units, blush points must hit the surface, and all six renders plus the comparison sheet must exist. It also runs every grade with `--keep-gloss`, a short turntable through ffmpeg, and checks that a broken input file surfaces as a Blender tool error with a log path.

## Live

`tests/test_live.py` makes one low-quality 1024x1024 text-only look-dev call with a $0.10 budget. The full pipeline was run by hand on a self-generated original character; results and spend are recorded in `docs/working.md`.

## What counts as verified

The offline suite passes in CI on both Python versions, the Blender integration tests pass locally, and a manual end-to-end run produced a comparison sheet whose renders match the input views from all four sides and a playable turntable.
