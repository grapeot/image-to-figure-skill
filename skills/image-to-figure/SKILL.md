---
name: image-to-figure
description: Turn one character image into a 3D collectible figure with the img2fig CLI - look-dev render, four-view turnaround, view cropping, targeted image edits, multiview image-to-3D on fal (Tripo by default, optional bake-off), Blender prep (orientation, normalise, decimate, soft-vinyl grade, blush), review renders, comparison sheet, turntable video, and 3D-print repair (watertight solid, fragments, thin-wall check, hollowing, base, STL/3MF, validation report). Paid calls are dry-run first with a cost estimate and a budget guard. Use when someone wants a figure-style 3D model or turntable of a character they have the rights to use.
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

## 3D printing

`img2fig print` turns a model (default `figure/figure_clean.glb`; any GLB, STL, OBJ or PLY with `--model`; with `--up auto`, GLB/glTF/OBJ are read as Y-up and STL/PLY as Z-up) into a print-ready solid. Needs the `print` extra (`uv pip install -e '.[print]'`); installing `pymeshlab` as well (GPL, not a dependency) adds an explicit self-intersection count to the report.

```bash
img2fig print --height 100 --base pedestal                 # solid, FDM or resin
img2fig print --height 130 --base pedestal                 # FDM with thin parts: print larger rather than thicken
img2fig print --height 100 --base pedestal --hollow 2.0    # resin: 2 mm wall, drain hole(s)
img2fig print --model raw_tripo.glb --rot -90 --height 80  # a raw Tripo GLB faces +X
```

Done when: the command exits 0 and `checks.printable` is true (watertight, a valid 2-manifold by manifold3d, positive volume); `checks.parts` is 1 unless loose parts were kept on purpose; `checks.self_intersecting_faces` is 0 when it was measured; `output.height_mm` equals `--height` plus any pedestal within one voxel; you looked at `print/figure_section.png` (walls, cavity and drain holes) and the `figure_preview_*.png` renders, and reported every entry in `warnings`, in particular thin parts (`checks.wall_thickness`: `thin_part_volume_fraction`, `largest_thin_parts` with their positions in mm; `relief_volume_fraction` is fine surface detail, reported but never thickened) and what to do about them. If `--thicken` was used, you compared the preview renders with an unthickened run and saw no new bumps on the face, hair or accessories; without Blender (no previews), check that `thicken.added_mm3` is of the same order as `wall_thickness_before.thin_part_volume_mm3` (bumps show up as added volume far beyond it) and look at the section image.

How it works: the surface is voxelised (default voxel = height / 500), gaps are sealed with the smallest morphological closing that encloses an interior (up to 8 voxels; the `voxelize` step reports `gap_closing_voxels`) and the inside filled, so open edges, overlapping shells and self-intersections disappear; a signed distance field then carries the base cut, pedestal, hollowing and drain holes; marching cubes, Taubin smoothing and manifold3d produce the final mesh. Details smaller than about one voxel are lost, and parts closer than the closing gap fuse.

Options: `--min-wall` (default 1.0 mm; resin can go lower, FDM usually wants 1.2 or more) sets what counts as thin; `--thicken` (off by default) grows thin parts to that thickness, see below; `--fragments merge|remove|keep` with `--merge-gap` (default 2 mm) decides floating parts: in merge mode parts within the gap are attached with a bridge and farther ones are kept and reported, never silently dropped; crumbs under 1 mm3 are always removed; `--base flat|pedestal|none`; `--hollow WALL` with `--drain-hole` drills one hole per cavity from below (pockets under 20 mm3 stay solid); `--color` writes a vertex-colour PLY from the model texture for full-colour services. The STL and 3MF are single-colour.

Thin parts. The report separates thin parts (sheets, fins, strands, ribbons and cords that stick out more than `--min-wall` from a thicker body, with both surfaces close together) from surface relief (embossed lines, strand edges, small dots and convex edges on a thick body). For FDM, prefer printing larger: every thin part scales with the figure (a 0.9 mm ribbon at 100 mm is 1.17 mm at 130 mm), nothing is reshaped, and the slicer's thin-wall handling (Arachne walls, "detect thin walls") prints most of what remains; a fraction of a percent of the volume under the wall is normal and fine. Use `--thicken` when the size is fixed and a few thin parts must print solid (a ribbon loop, an ahoge, a hanging cord, a skirt hem): it grows only thin parts, each to about `--min-wall`, and blunts their tips; relief is never grown. When thin parts remain after `--thicken`, the warning says so and suggests printing larger or slicer settings, not another run. Edges of the definition: relief that rises more than `--min-wall` from the body (a tall narrow crest, a deep embossed strand) counts as a thin part and gets blunted; a short thin web spanning a gap between two thick parts (up to about twice `--min-wall` across) is reported as relief, and a slicer may drop it and leave a hole, so check webs in the slicer preview. Growth goes in every direction, so `--thicken` also lengthens tips slightly and can fuse thin parts that are closer together than `--min-wall`.

Observed while building it: the binary distance field leaves visible terraces unless the result is smoothed (Taubin, `--smooth`, default 10 iterations, keeps volume); projecting vertices back onto the source surface instead folded thin sheets and was dropped. Smoothing and mesh simplification can both introduce self-intersections in thin regions; the command tries less smoothing and tighter tolerances until the check is clean and reports what it settled on (`smooth_and_simplify` step). Hollowing must leave undrainable pockets solid, otherwise they become sealed voids. On a real image-to-3D figure, two symmetric 120 mm3 pieces were modelled as separate shells 1.3 mm from the body; an early version removed them because they sat just beyond a 1 mm merge gap, which is why distant parts are now kept and reported instead. On a real 100 mm figure with `--min-wall 1.2`, an early `--thicken` covered the face, hair, bows and a plush accessory in small round bumps: it treated everything a 1.2 mm ball could not reach as thin, including shallow relief such as strand edges, embossed lashes and dots, and grew each of those by 0.6 mm on every side (5.5% more volume). Without thickening the same figure was clean, and printing it at 130 mm left about 0.7% of the volume under 1 mm for the slicer. Thickening now grows only thin parts, and on that figure it added about a third as much material with no bumps; still look at the previews whenever you use it.

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
  print/figure.stl, figure.3mf, figure_color.ply (--color), figure_report.json, figure_section.png, figure_preview_*.png
  logs/*.log
  spend.json
```
