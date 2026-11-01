# Design

## Goal and boundaries

One character image in, a 3D collectible figure out: a textured model that matches the character from every side, a Blender master with a soft-vinyl look, review renders and a turntable. An AI coding agent drives the stages; the human approves spending and judges the look.

The tool does not rig or animate, does not hand-model characters, does not upload results anywhere, and never spends money without `--execute`.

## Why this pipeline

- **Image-to-3D instead of procedural modelling.** A character modelled from primitives could not reach figure quality for organic shapes such as hair. Multiview image-to-3D could.
- **Four views instead of one.** Multiview models reconstruct the back and sides from real pixels instead of guessing. The cost is that the image model's turnaround becomes the specification: anything it invents gets built. Fixes therefore happen at the image stage (`edit`), which is cheap and keeps the mesh untouched.
- **Look-dev first.** Turning an illustration straight into a turnaround mixes two jumps (2D style to 3D figure style, one view to four). The look-dev image fixes the figure style first; the turnaround then only has to rotate it.
- **Tripo as the default.** In a bake-off on a chibi character (Hunyuan3D 3.1 Pro, Tripo H3.1, Rodin 2.5, Meshy 7.1) Tripo's model was the one chosen for the figure. The others stay available for a bake-off on new characters.

## Architecture

```text
src/image_to_figure/
  cli.py            argparse subcommands, JSON envelope, exit codes
  errors.py         error classes (each carries an exit code and a pass-through detail dict), emit/log
  workdir.py        work-directory layout, versioned file names, spend ledger and budget check
  costs.py          cost estimates (image tokens, per-call 3D prices), cost implied by returned usage
  prompts.py        look-dev, turnaround and edit templates
  images.py         OpenAI images.generate / images.edit through the official SDK
  crop.py           sheet -> equal-size views by background column runs
  fal3d.py          fal providers, hash-keyed upload cache, parallel bake-off, downloads
  orientation.py    choose the rotation whose flat render best matches the front view
  blender.py        find Blender, run a script in background mode, read its JSON result
  blender_geometry.py  front-camera geometry shared with the Blender side (pixel -> figure fractions)
  sheet.py          comparison sheets
  video.py          ffmpeg encode
  blender_scripts/  run inside Blender's Python; never import the package
    common.py       import/join/normalise, studio, cameras, ray casts
    orient.py       flat Workbench renders at 0/90/180/270 degrees with a transparent film
    prep.py         orient, normalise, decimate, export clean GLB, grade, blush, save .blend
    render.py       review renders
    turntable.py    turntable frames
```

The figure convention inside Blender: height 2.9 units, feet on z = 0, centred on x/y, facing -Y. The figure's left side is +X, so the "left" camera sits on +X and its image matches the turnaround's left view (the figure facing the viewer's left).

### Command contract

Every subcommand prints exactly one JSON object to stdout, including on errors (`--help` and `--version` excepted). Progress goes to stderr. On failure the object is `{"ok": false, "command", "exit_code", "error": {"type", "message", "detail"}}`, where `detail` carries the provider's HTTP status and body, or Blender's or ffmpeg's exit code, stderr tail and log path.

| Exit | Type | Meaning |
|---|---|---|
| 0 | ok | success, including dry runs |
| 1 | provider_error | OpenAI or fal returned an error or could not be reached |
| 2 | usage_error | bad arguments or missing input files |
| 3 | budget_exceeded | the call would exceed the budget; nothing was sent |
| 4 | missing_dependency | key, Blender, ffmpeg or Python extra missing |
| 5 | tool_error | Blender or ffmpeg exited non-zero |
| 6 | check_failed | a result check failed (the sheet did not split into the expected number of views) |
| 7 | internal_error | a bug in img2fig; the traceback tail is included |

### Spending

Paid commands (`lookdev`, `turnaround`, `edit`, `model`) build the full plan (endpoint, model, size, inputs, full prompt or arguments) and an estimate, and stop there without `--execute`. With `--execute`, the budget (`--budget`, else `IMG2FIG_BUDGET_USD`, else $5; it must be positive) is checked against the ledger in `spend.json` before the request, under an exclusive file lock held until the call is recorded, so parallel commands in one work directory cannot overspend. Each call is recorded with its estimate and, for successful image calls, the cost implied by the returned token usage. Failed calls are recorded too (status `failed`, counted at their estimate), because a job can fail after the provider billed it; a 4xx refusal is therefore over-counted, which keeps the guard on the safe side. `img2fig spend` summarises the ledger.

### Orientation check

Providers disagree on which way their GLBs face after import. `prep --rot auto` renders the model flat-shaded (Workbench, texture colour, Standard view transform, transparent film) from the front at four rotations, normalises each render and the front view to the subject's bounding box, and scores `0.5 * (1 - silhouette IoU) + mean colour difference over the overlap`. The colour term separates front from back, whose silhouettes are near mirror images. If the best two scores are within 8% of each other the result is marked ambiguous and the provider's known orientation is used when there is one (only Tripo's is known).

### Material grade

The grade keeps the provider's textures and changes how they are shaded: a saturation lift (AgX greys pale colours), an optional tone curve, roughness remapped to 0.55-0.8, no metal, lower specular, a little subsurface. `--keep-gloss T` keeps the original roughness and metal wherever the source roughness is below T, for crystal and patent parts. The blush is a sum of soft ellipsoids in object space multiplied into the base colour, placed by ray casts from the front.

## Print repair

`print3d.py` turns any figure mesh into a print-ready solid on a voxel grid (`img2fig print`):

1. Load, convert glTF Y-up to Z-up, optional rotation, scale so the figure is `--height` mm after the flat cut.
2. Voxelise: dense surface samples mark surface voxels; a closing of k = 1..4 voxels seals gaps and `binary_fill_holes` fills the interior (accepted when the enclosed interior is at least 10% of the shell). If nothing gets enclosed (large openings), boundary loops are capped with fans on the mesh and the fill is retried.
3. Fragments: 26-connected components other than the largest are removed when under 1 mm3 (or always with `--fragments remove`), bridged with a lens-shaped union when within `--merge-gap` (default 2 mm), and otherwise kept and reported, so a real part of the design is never dropped silently.
4. Thin features: a ball opening of diameter `--min-wall` (via distance transforms) finds what a ball that size cannot reach; `--thicken` grows those regions by half the wall.
5. Signed distance field (Euclidean distance transforms, light Gaussian smoothing, a one-voxel shift that removes the half-voxel inflation of surface-touching voxels). The base cut, pedestal, hollowing (`max(f, -(f + wall))` applied only near drainable cavities) and drain holes (vertical cylinders from below into each cavity's lowest point) are max/min operations on this field.
6. Marching cubes on the padded field, then a small search: Taubin smoothing at full, half and zero iterations (sole pinned to its plane) times manifold3d simplification at voxel/10, /20, /40 and none, stopping at the first candidate pymeshlab (when installed) finds free of self-intersections, otherwise keeping the one with the fewest. Meshes keep manifold3d's vertex indexing, because merging coincident positions turns touching sheets into pinches. Zero-volume slivers are dropped.
7. Validation: trimesh watertight/winding/volume, manifold3d status and genus, part count, pymeshlab self-intersections (optional), thin-wall statistics on the final field; STL and 3MF export; section image and Blender preview renders.

Exit 6 if the result is not watertight and manifold. Measured on a 30 mm cube at 0.25 mm voxels: 29.93 mm per side.

## Cost model

Image calls: output tokens for each quality tier at 1024x1024 (OpenAI's published figures), scaled with the square root of the megapixels (measured usage grows sublinearly: 3336 output tokens for a 3840x2160 sheet at high quality), $30 per million output tokens, $8 per million image input tokens (about 1,600 per reference image), $5 per million text tokens, and a 25% margin. 3D calls: per-call prices for the arguments this tool sends (Tripo $0.60, Hunyuan3D $0.675, Rodin $1.20, Meshy $1.52). Numbers are in `costs.py` with their date.
