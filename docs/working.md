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

### 2026-11-01

- `img2fig print`: 3D-print repair on a voxel grid (watertight solid, fragment merge/keep/remove, thin-feature report and optional thickening, hollowing with per-cavity drain holes, flat base or pedestal, scaling to a height in mm), STL and 3MF export, optional vertex-colour PLY, validation report, cross-section image and Blender previews. New `print` extra (trimesh, manifold3d, scipy, scikit-image, networkx, lxml); pymeshlab is optional for the self-intersection count.
- Tried and dropped: pymeshlab's uniform resampling (left cracks and non-manifold edges on a provider mesh) and projecting the result back onto the source surface (folded thin sheets into thousands of self-intersections). Kept: binary-grid distance field plus Taubin smoothing.
- Calibrated the distance-field shift so a 30 mm cube comes out at 29.93 mm per side at 0.25 mm voxels (the first version was 0.5 mm too fat per side).
- Fixed on the way: the interior-enclosed test failed on thin closed objects; large openings are now capped on the mesh before filling; the fragment bridge reached only half the gap; small undrainable pockets were hollowed into sealed voids; a real figure lost two 120 mm3 separate shells to a 1 mm merge gap, so distant parts are now kept and reported (merge gap default 2 mm).

## Lessons Learned

- Pass negative numbers to argparse options with `=` (`--blush=-0.1,0.5`, `--rot=-90`). Without it argparse treats `-0.1,0.5` as an unknown option. This applies both to `img2fig` and to the arguments it passes to the Blender scripts, which always use the `=` form.
- Mix nodes in Blender have several sockets named "A" and "B" (float, vector, colour); pick sockets by identifier (`A_Color`, `Factor_Float`), not by name.
- Blender 5 keeps action F-curves in layered channel bags; `action.fcurves` can be empty. The turntable script looks in both places to set linear interpolation.
- The orientation check renders with the Standard view transform and a transparent film so colours are the texture's sRGB values and alpha is the silhouette; AgX would shift colours and weaken the colour term.
- AgX with the default matte grade visibly washes out saturated colours (the yellow raincoat came out pale). `--grade light` lifts saturation more; `--view-transform standard` keeps reference colours at the cost of clipped highlights.
- Voxel-based print repair inherits two resolution limits that users must hear about: details under about one voxel vanish and parts closer than about three voxels fuse (the morphological closing that seals mesh gaps also welds narrow gaps between parts).
- trimesh's `process=True` merges coincident vertices; on marching-cubes output that can turn two touching sheets into non-manifold pinches, so a mesh manifold3d accepts suddenly reads as not watertight. Keep manifold3d's indexing for checks.
- Never delete large floating parts by default. On image-to-3D figures, parts of the design (hair pieces, accessories) are often separate shells a millimetre or so from the body.

