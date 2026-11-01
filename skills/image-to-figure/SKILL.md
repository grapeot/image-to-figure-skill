---
name: image-to-figure
description: Turn one character image into a 3D collectible figure with the img2fig CLI - look-dev render, four-view turnaround, view cropping, targeted image edits, multiview image-to-3D on fal (Tripo by default, optional bake-off), Blender prep (orientation, normalise, decimate, soft-vinyl grade, blush), review renders, comparison sheet and turntable video. Paid calls are dry-run first with a cost estimate and a budget guard. Use when someone wants a figure-style 3D model or turntable of a character they have the rights to use.
---

# image-to-figure

## Goal

From one character image (an illustration, a concept sheet or a photo of a figure) produce a textured 3D figure that matches the character from every side: a Blender master file, a clean GLB, review renders against the turnaround views, and a turntable video. The owner approves spending and judges the look; you drive the stages and catch problems before they cost money.

Not in scope: rigging and animation, characters the owner has no rights to use, and hand-modelling the character in Blender.

## Done when

1. `views/view_front.png`, `view_left.png`, `view_back.png`, `view_right.png` exist, all the same size, from `img2fig crop` with exit code 0.
2. You compared the four views with the reference image and with each other, and reported in writing: every accessory is on the correct side of the body in every view, nothing appears that is not in the reference (look hardest at the back view, pockets, belts and hair ornaments), and the pose and ground line match across views. Any difference was fixed with `img2fig edit` and the sheet re-cropped before the model stage.
3. `models/<provider>/model.glb` exists from `img2fig model --execute`.
4. `img2fig prep` exited 0, its `orientation` is not `ambiguous` (or you confirmed the front render faces the camera), `prep.dimensions_units[2]` is within 0.01 of 2.9, and `prep.faces_out` is at or below `--faces` (for triangulated input such as provider GLBs).
5. You looked at `renders/compare.jpg` (input views on top, renders below) and `renders/face.png`, and reported per view what matches and what does not.
6. `turntable/turntable.mp4` exists (`img2fig turntable`, exit 0) if the owner wanted a video.
7. `img2fig spend` shows a total within what the owner approved.

## Spending rules

- Commands that call a paid API (`lookdev`, `turnaround`, `edit`, `model`) only print a plan and an estimate unless `--execute` is given. Show the plan's estimate to the owner and get a go-ahead, either per call or as a budget they set; then pass that budget as `--budget` (or set `IMG2FIG_BUDGET_USD`; without either the cap is $5). The budget is checked against the work directory's ledger (`spend.json`, summarised by `img2fig spend`) before every paid call, and the call is refused (exit 3) if it would go over. Failed calls are recorded at their estimate, since some fail after the provider has billed them.
- `img2fig estimate --stages lookdev,turnaround,edit,model` prices a whole run. Typical order of magnitude: under $0.30 per image call, $0.60 per Tripo model, about $4 for a four-provider bake-off. The estimates err high; `cost_from_usage_usd` on image calls is the token-based figure.
- A bake-off (`--provider all`) costs about seven Tripo runs. Do it only when the owner asks or Tripo clearly fails on this character.

## Workflow

```bash
img2fig doctor                                    # keys present? Blender, ffmpeg found?
img2fig lookdev --ref character.png --description "..."            # plan; add --execute after approval
img2fig turnaround --ref character.png --description "..."         # uses the latest look-dev image
img2fig crop                                       # views/view_{front,left,back,right}.png
img2fig edit --fix "Back view: remove the round tail under the bow; plain skirt there."   # only if needed
img2fig crop                                       # re-crop: picks the newest sheet or edit
img2fig model                                      # Tripo, plan; --execute after approval
img2fig prep                                       # orient, normalise, decimate, grade, renders, compare.jpg
img2fig turntable                                  # turntable/turntable.mp4
```

All commands share `--workdir DIR` (default `$IMG2FIG_WORKDIR` or `./figure_work`); inputs default to the latest file of the previous stage in that directory. Every command prints one JSON object on stdout; read `ok`, the paths, `warning`/`warnings`, `next`, and on failure `error.type`, `error.message` and `error.detail` (provider HTTP status and body, Blender or ffmpeg exit code and stderr tail, log path).

Exit codes: 0 ok (including dry runs), 1 provider error, 2 usage error, 3 budget exceeded, 4 missing key/binary/package, 5 Blender or ffmpeg failed, 6 a result check failed (the sheet did not split into the expected views), 7 internal bug.

### Writing `--description`

List what the figure must keep, with sides stated from the character's point of view and the viewer's in brackets: "green clover hair clip on her LEFT side (viewer's right in the front view)". Describe what the reference image actually shows, not what you intended to generate: image models sometimes move an accessory to the other side, and the reference wins.

### Prep options that matter

- `--rot auto` (default) renders the model flat-shaded at 0/90/180/270 degrees and compares each with `view_front.png`. If the result is ambiguous it falls back to the provider's known orientation (known for Tripo only; otherwise the output says to check the front render). Pass `--rot DEG` to override.
- `--grade matte` (default), `light` (pale outfits: whites stay clean), `dark` (deepens dark cloth), `none`. `--keep-gloss 0.3` keeps the original roughness and metal on parts that were glossy in the source.
- `--blush=X,Z` (repeatable) paints a soft cheek blush at a point given in figure fractions; get X,Z by measuring the cheek in `renders/front.png` and running `img2fig point --x PX --y PY`. Write negative values with `=` (`--blush=-0.08,0.6`), otherwise argparse reads them as options.
- `--faces` (default 350000) caps the face count; the normal and colour textures keep fine detail.

## Known pitfalls (all observed)

- **The image model invents details, and the 3D model builds them.** A turnaround once gained a pom-pom tail on the back and a badge in the jacket pockets that the character never had; the 3D model reproduced both faithfully. Fix this at the image stage (`edit` with one `--fix` per problem, then `crop` and `model` again). Do not patch the mesh. A first edit can leave a different invention behind (the badge became a cupcake-shaped decoration), so compare again after every edit.
- **Small features blur.** Each view of a 3840x2160 sheet is only about 1000 px wide, so a plush toy's face or the fingers come out soft in the model. A larger sheet is not available; judge the face render and tell the owner instead of promising sharp small details.
- **Providers disagree on orientation.** Tripo's GLBs face +X after import (needs -90 degrees). The auto check handles it; still look at the front render.
- **An all-matte grade kills crystal and gloss parts.** Use `--keep-gloss` or `--grade none` when the character has crystal, metal or patent parts.
- **Hand-built procedural characters do not reach figure quality.** A character modelled from primitives in Blender could not get organic hair right; the image-to-3D model did. Do not fall back to procedural modelling for organic shapes.
- **AgX vs Standard.** Renders use AgX by default: highlights do not clip, but pale and white materials go greyish and less saturated, which is why the grades lift saturation. `--view-transform standard` matches the reference colours more closely but clips bright areas. The orientation check renders with Standard so colours compare fairly.
- **sRGB vs linear in Blender.** Reading pixel colours of a PNG inside Blender (`Image.pixels`) returned encoded sRGB values, not linear ones. Decode before using them as linear colours if you extend the Blender scripts.

## Resources and boundaries

- Keys come from environment variables only: `OPENAI_API_KEY` (image stages) and `FAL_KEY` (model stage). Never print them or write them to files.
- Blender as an external binary (tested on 5.2; 4.2 or newer expected to work) (`blender` on PATH or `IMG2FIG_BLENDER`); ffmpeg for the turntable.
- The work directory holds generated images, models and renders of the owner's character. Do not publish, upload or commit them unless the owner asks; treat characters the owner licensed from someone else as private.
- Prompts are in `src/image_to_figure/prompts.py`; `--prompt-file` replaces a template for one call.

## Output layout

```text
<workdir>/
  lookdev/lookdev_vN.png (+ .prompt.txt)
  turnaround/sheet_vN.png, sheet_vN_eM.png (edits)
  views/view_{front,left,back,right}.png, crop.json, uploads.json
  models/<provider>/result.json, model.glb, preview files
  figure/figure.blend (graded master), figure_clean.glb (oriented, decimated, ungraded), orient/
  renders/{front,left,back,right,q34,face}.png, compare.jpg
  turntable/turntable.mp4
  logs/*.log
  spend.json
```
