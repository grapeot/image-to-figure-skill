# Working notes

## Changelog

### 2026-10-31

- Scaffold: README, AGENTS.md, design and test stubs, skill stub, CI (ruff + offline pytest), MIT license.
- v0.1 CLI `img2fig`: doctor, estimate, lookdev, turnaround, edit, crop, model, prep, render, sheet, turntable, point.
- Paid commands are dry-run by default; `--execute` checks the budget against the work directory's `spend.json` ledger first and records each successful call.
- OpenAI images are called through the official SDK (`images.generate` without inputs, `images.edit` with inputs); fal through `fal_client` with a hash-keyed upload cache, parallel bake-off and per-provider error isolation.
- Crop by background column runs into equal-size views with a shared vertical extent; neighbour pixels painted with the background; wrong run counts fail with exit 6 and a hint.
- Blender stages run as an external binary with `--python-exit-code 1` and a JSON result file: orientation renders, prep (join, rotate, normalise, decimate, clean GLB export, grade with optional gloss preservation, blush), review renders, turntable frames; ffmpeg encodes the video.
- Automatic orientation: flat Workbench renders at four rotations scored against the front view (silhouette IoU plus colour difference), with the provider's known orientation as the fallback when ambiguous.
- Tests (before review fixes): 69 offline (fakes for OpenAI, fal and Blender), 4 Blender integration tests on a synthetic textured figure (runs locally, skipped in CI), one opt-in live test.
- Live end-to-end run on a self-generated original character (an explorer girl in a yellow raincoat): character image, look-dev, 4K turnaround, crop, Tripo, prep, renders and turntable. No edit was needed: the turnaround kept the clip and the satchel on the correct side in every view and added nothing. Orientation check picked 270 degrees with a 34% margin. Renders matched all four input views. Spend $0.82 (images $0.22 by token usage, Tripo $0.60).
- Recalibrated the image cost estimate after the live run: output tokens grow with roughly the square root of the pixel count (1372 / 1413 / 3336 tokens at 1.5 / 2.25 / 7.9 MP), so the linear estimate was 3-4x high.
- Independent review fixes: failed paid calls are recorded (status `failed`, counted at their estimate); default budget of $5 and positive-budget validation; an exclusive lock around check, call and record; provider files download into `files/` so a provider file named `model.glb` no longer collides; unexpected local errors in one bake-off provider no longer hide the others; clean GLB textures now really use JPEG quality 90 (`export_image_quality`); invalid budget, corrupt ledger, bad estimate input, non-executable Blender/ffmpeg and zero fps exit 2 or 4 instead of 7; relative input paths fall back to the work directory; new `spend` command.
- Fal progress is logged once per state change and polled every 2 s (the client's default 0.1 s interval flooded stderr).

## Lessons Learned

- Pass negative numbers to argparse options with `=` (`--blush=-0.1,0.5`, `--rot=-90`). Without it argparse treats `-0.1,0.5` as an unknown option. This applies both to `img2fig` and to the arguments it passes to the Blender scripts, which always use the `=` form.
- Mix nodes in Blender have several sockets named "A" and "B" (float, vector, colour); pick sockets by identifier (`A_Color`, `Factor_Float`), not by name.
- Blender 5 keeps action F-curves in layered channel bags; `action.fcurves` can be empty. The turntable script looks in both places to set linear interpolation.
- The orientation check renders with the Standard view transform and a transparent film so colours are the texture's sRGB values and alpha is the silhouette; AgX would shift colours and weaken the colour term.
- AgX with the default matte grade visibly washes out saturated colours (the yellow raincoat came out pale). `--grade light` lifts saturation more; `--view-transform standard` keeps reference colours at the cost of clipped highlights.
