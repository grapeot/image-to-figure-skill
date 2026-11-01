"""Make a figure mesh printable: watertight solid, fragments handled, thin features found (and optionally
thickened), optional hollowing with drain holes, flat base or pedestal, scaled to a height in millimetres, exported as
STL and 3MF with a validation report.

Approach: everything happens on a voxel grid. Image-to-3D meshes are close to closed but carry open edges, non-manifold
edges, overlapping shells, self-intersections and loose crumbs. Voxelising the surface, sealing gaps below two voxels
with a morphological closing and filling the interior gives an occupancy grid that is solid by construction. From it a
signed distance field is built (negative inside, millimetres), and every later operation is arithmetic on that field:
offsets are level shifts, hollowing is `max(f, -(f + wall))`, the base cut and pedestal are a half-space and a cylinder
combined with max/min, drain holes subtract cylinders. Marching cubes on the final field yields a closed, consistently
oriented, self-intersection-free surface; manifold3d then simplifies it and confirms it is a valid 2-manifold.

The price is resolution: details smaller than about one voxel are smoothed away. The voxel size defaults to
height / 500 (0.2 mm for a 100 mm figure), which is below the nozzle or pixel size of most printers.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .errors import CheckFailed, MissingDependency, UsageError, log

MAX_VOXELS = 80_000_000
INFLATION_SHIFT = 1.0  # voxels; calibrated so a 30 mm cube comes out within about 1% in volume


def _deps():
    try:
        import manifold3d
        import scipy.ndimage as ndi
        import trimesh
        from skimage import measure
    except ImportError as e:  # pragma: no cover - depends on the install
        raise MissingDependency(
            "print repair needs the 'print' extra: uv pip install -e '.[print]'", {"import_error": str(e)}
        ) from e
    return trimesh, ndi, measure, manifold3d


@dataclass
class PrintOptions:
    height_mm: float = 100.0
    voxel_mm: float | None = None  # default height / 500, clamped to [0.1, 0.6]
    up: str = "auto"  # "y" for glTF, "z" for STL/OBJ/PLY/3MF; auto picks by extension
    rot_deg: float = 0.0  # rotation about the vertical axis after the up-axis fix (raw Tripo GLBs need -90)
    min_wall_mm: float = 1.0
    thicken: bool = False
    fragments: str = "merge"  # merge | remove | keep
    merge_gap_mm: float = 2.0
    min_fragment_mm3: float = 1.0  # floating parts smaller than this are always removed
    hollow_wall_mm: float | None = None
    drain_hole_mm: float = 3.0
    base: str = "flat"  # flat | pedestal | none
    flat_cut_mm: float = 0.3
    pedestal_height_mm: float = 3.0
    pedestal_margin_mm: float = 3.0
    smooth: float = 0.6  # Gaussian sigma (voxels) on the distance field
    simplify_mm: float | None = None  # default voxel / 10
    color: bool = False
    smooth_iterations: int = 10  # Taubin smoothing of the final surface; 0 to disable


@dataclass
class Report:
    input: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    output: dict[str, Any] = field(default_factory=dict)
    checks: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- loading and diagnostics


def load_mesh(path: Path, opts: PrintOptions):
    trimesh, _, _, _ = _deps()
    if not path.is_file():
        raise UsageError(f"model not found: {path}", {"path": str(path)})
    loaded = trimesh.load(str(path), process=False)
    if isinstance(loaded, trimesh.Scene):
        meshes = [g for g in loaded.dump() if isinstance(g, trimesh.Trimesh)] if hasattr(loaded, "dump") else []
        if not meshes:
            meshes = [g for g in loaded.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not meshes:
            raise UsageError("no triangle mesh in the file", {"path": str(path)})
        mesh = trimesh.util.concatenate(meshes)
    else:
        mesh = loaded
    up = opts.up
    if up == "auto":
        up = "y" if path.suffix.lower() in (".glb", ".gltf", ".obj") else "z"
    colors = None
    if opts.color:
        try:
            colors = np.asarray(mesh.visual.to_color().vertex_colors)[:, :3]
        except Exception:  # noqa: BLE001 - colour is optional
            colors = None
    m = trimesh.Trimesh(mesh.vertices, mesh.faces, process=False)
    if colors is not None and len(colors) == len(m.vertices):
        m.visual.vertex_colors = np.c_[colors, np.full(len(colors), 255)]
    if up == "y":  # glTF is Y-up with the figure facing +Z; print frame is Z-up facing -Y
        m.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    if opts.rot_deg:
        m.apply_transform(trimesh.transformations.rotation_matrix(math.radians(opts.rot_deg), [0, 0, 1]))
    ext = m.bounds[1] - m.bounds[0]
    if ext[2] <= 0:
        raise UsageError("model has zero height", {"bounds": m.bounds.tolist()})
    cut = opts.flat_cut_mm if opts.base in ("flat", "pedestal") else 0.0
    m.apply_scale((opts.height_mm + cut) / ext[2])  # the flat cut removes `cut`; the figure ends at height_mm
    c = m.bounds.mean(axis=0)
    m.apply_translation([-c[0], -c[1], -m.bounds[0][2]])
    return m, up


def diagnose(mesh) -> dict[str, Any]:
    """Topology of the input as delivered (after merging coincident vertices)."""
    trimesh, _, _, _ = _deps()
    merged = trimesh.Trimesh(mesh.vertices, mesh.faces, process=True)
    edges = merged.edges_sorted
    _, counts = np.unique(edges, axis=0, return_counts=True)
    try:
        n_comp = int(trimesh.graph.connected_components(merged.face_adjacency, nodes=np.arange(len(merged.faces)),
                                                         min_len=1).__len__())
    except Exception:  # noqa: BLE001
        n_comp = None
    return {
        "faces": int(len(merged.faces)),
        "vertices": int(len(merged.vertices)),
        "watertight": bool(merged.is_watertight),
        "winding_consistent": bool(merged.is_winding_consistent),
        "boundary_edges": int((counts == 1).sum()),
        "non_manifold_edges": int((counts > 2).sum()),
        "components": n_comp,
    }


# ---------------------------------------------------------------- voxel helpers


class Grid:
    def __init__(self, lo: np.ndarray, hi: np.ndarray, voxel: float, pad: int):
        self.voxel = voxel
        self.origin = lo - pad * voxel
        self.shape = tuple(int(math.ceil((hi[i] - lo[i]) / voxel)) + 2 * pad + 1 for i in range(3))

    @property
    def size(self) -> int:
        return int(np.prod(self.shape))

    def index(self, pts: np.ndarray) -> np.ndarray:
        idx = np.floor((pts - self.origin) / self.voxel).astype(np.int64)
        return np.clip(idx, 0, np.array(self.shape) - 1)

    def axes(self):
        return [self.origin[i] + (np.arange(self.shape[i]) + 0.5) * self.voxel for i in range(3)]


def _edt(mask: np.ndarray, voxel: float) -> np.ndarray:
    _, ndi, _, _ = _deps()
    return ndi.distance_transform_edt(mask).astype(np.float32) * voxel


def _surface_voxels(mesh, grid: Grid) -> tuple[np.ndarray, int]:
    trimesh, _, _, _ = _deps()
    v = grid.voxel
    count = int(min(30_000_000, max(200_000, 6 * mesh.area / (v * v))))
    pts, _ = trimesh.sample.sample_surface(mesh, count, seed=0)
    pts = np.vstack([pts, mesh.vertices])
    surface = np.zeros(grid.shape, dtype=bool)
    idx = grid.index(pts)
    surface[idx[:, 0], idx[:, 1], idx[:, 2]] = True
    return surface, count


def cap_holes(mesh):
    """Close every boundary loop with a fan around its centroid. Crude, but it gives the voxel fill a closed shell."""
    trimesh, _, _, _ = _deps()
    import networkx as nx

    m = trimesh.Trimesh(mesh.vertices, mesh.faces, process=True)
    edges = m.edges_sorted
    uniq, counts = np.unique(edges, axis=0, return_counts=True)
    boundary = uniq[counts == 1]
    if len(boundary) == 0:
        return None
    verts = [m.vertices]
    faces = [m.faces]
    n = len(m.vertices)
    for loop in nx.cycle_basis(nx.from_edgelist(boundary.tolist())):
        if len(loop) < 3:
            continue
        c = m.vertices[loop].mean(axis=0)
        verts.append(c[None, :])
        faces.append(np.array([[loop[i], loop[(i + 1) % len(loop)], n] for i in range(len(loop))]))
        n += 1
    if n == len(m.vertices):
        return None
    return trimesh.Trimesh(np.vstack(verts), np.vstack(faces), process=False)


def _fill(surface: np.ndarray) -> tuple[np.ndarray | None, int]:
    """Seal gaps of up to 2k voxels with a closing, fill the enclosed interior. None if nothing gets enclosed."""
    _, ndi, _, _ = _deps()
    struct = ndi.generate_binary_structure(3, 1)
    for k in (1, 2, 3, 4):
        shell = ndi.binary_dilation(surface, structure=struct, iterations=k)
        filled = ndi.binary_fill_holes(shell)
        # an open mesh encloses nothing (or only small closed accessories); a closed one encloses a real interior
        if int(filled.sum() - shell.sum()) > 0.1 * int(shell.sum()):
            return ndi.binary_erosion(filled, structure=struct, iterations=k, border_value=0) | surface, k
    return None, 0


def voxelize(mesh, grid: Grid, report: Report) -> np.ndarray:
    trimesh, ndi, _, _ = _deps()
    v = grid.voxel
    surface, count = _surface_voxels(mesh, grid)
    solid, used = _fill(surface)
    filled_holes = False
    if solid is None:
        # large openings: close them on the mesh itself (planar-ish hole filling), then try again
        patched = cap_holes(mesh)
        if patched is not None:
            surface, count = _surface_voxels(patched, grid)
            solid, used = _fill(surface)
            filled_holes = solid is not None
    if solid is None:
        report.warnings.append(
            "could not enclose an interior even after closing gaps of 8 voxels: the mesh has large openings. "
            "The result is the surface shell only; check the renders."
        )
        solid = ndi.binary_dilation(surface, structure=ndi.generate_binary_structure(3, 1), iterations=1)
        used = 0
    report.steps.append({"step": "voxelize", "voxel_mm": v, "grid": list(grid.shape), "surface_samples": count,
                         "gap_closing_voxels": used, "mesh_holes_filled_first": filled_holes,
                         "solid_voxels": int(solid.sum())})
    return solid


def handle_fragments(solid: np.ndarray, grid: Grid, opts: PrintOptions, report: Report) -> np.ndarray:
    _, ndi, _, _ = _deps()
    v = grid.voxel
    labels, n = ndi.label(solid, structure=np.ones((3, 3, 3), bool))
    if n <= 1:
        report.steps.append({"step": "fragments", "found": 0})
        return solid
    sizes = np.bincount(labels.ravel(), minlength=n + 1)[1:]
    main_label = int(np.argmax(sizes)) + 1
    main = labels == main_label
    dist_to_main = _edt(~main, v)
    voxel_mm3 = v ** 3
    actions = []
    out = main.copy()
    for lab in range(1, n + 1):
        if lab == main_label:
            continue
        comp = labels == lab
        vol = float(sizes[lab - 1] * voxel_mm3)
        gap = float(dist_to_main[comp].min()) - v
        idx = np.argwhere(comp)
        centre = (grid.origin + (idx.mean(axis=0) + 0.5) * v).round(2).tolist()
        entry = {"volume_mm3": round(vol, 3), "gap_mm": round(max(gap, 0.0), 3), "centre_mm": centre}
        if vol < opts.min_fragment_mm3 or opts.fragments == "remove":
            entry["action"] = "removed"
        elif opts.fragments == "merge" and gap <= opts.merge_gap_mm:
            # lens-shaped bridge: voxels within reach of both the fragment and the body span the whole gap
            r = max(gap, 0.0) + 1.5 * v
            bridge = (_edt(~comp, v) <= r) & (dist_to_main <= r)
            out |= comp | bridge
            entry["action"] = "merged"
        elif opts.fragments == "merge":
            # never delete a real piece of the design silently: keep it and say so
            out |= comp
            entry["action"] = "kept"
            report.warnings.append(f"floating part of {vol:.1f} mm3 at {centre} is {gap:.2f} mm from the body "
                                   f"(more than --merge-gap {opts.merge_gap_mm}); kept as a loose piece. Raise "
                                   "--merge-gap to attach it or use --fragments remove to drop it")
        else:
            out |= comp
            entry["action"] = "kept"
            report.warnings.append(f"floating part of {vol:.1f} mm3 at {centre} kept; it prints as a loose piece")
        actions.append(entry)
    actions.sort(key=lambda e: -e["volume_mm3"])
    summary = {a: sum(1 for e in actions if e["action"] == a) for a in ("removed", "merged", "kept")}
    report.steps.append({"step": "fragments", "found": len(actions), **summary, "largest": actions[:10]})
    return out


# How far (in voxels) the distance to the surface at voxel centres overstates a wall's half-thickness. In the final
# solid (field <= 0 at voxel centres) it is 0 to 0.5 voxel depending on parity, and the check takes the lenient end
# (resolution is a voxel either way). The raw voxel solid marks every voxel the surface touches and reads about one
# voxel more; using a full voxel there makes the check before thickening at least as strict as the one after it.
FINAL_BIAS = 0.0
RAW_BIAS = 1.0


def thin_masks(solid: np.ndarray, v: float, min_wall: float, bias: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Split what a ball of diameter min_wall cannot reach into thin parts and surface relief.

    `thin` is every voxel of the solid outside the ball opening: thin sheets and strands, but also raised relief
    (embossed lines, dots, strand edges) and convex edges on a thick body. Only `parts` may be thickened: thin
    voxels that reach farther than min_wall from the thick core, i.e. a sheet, fin or strand whose two surfaces are
    close together, plus their roots (a geodesic dilation inside the thin mask). Relief on a thick body stays within
    its own height of the core (a convex corner within 0.73 x the ball radius), so it never qualifies; growing relief
    is what turns fine surface detail into round bumps. `bias` (voxels) is how much the distance to the surface at
    voxel centres overstates the true half-thickness for this kind of solid. Also returns that distance.
    """
    _, ndi, _, _ = _deps()
    r = min_wall / 2
    eps = 1e-3 * v  # distances are float32 multiples of the voxel
    inside = _edt(solid, v)
    centres = inside >= r + bias * v - eps
    if not centres.any():  # nothing is thick enough: all of it is thin
        return solid.copy(), solid.copy(), inside
    opened = _edt(~centres, v) <= r + bias * v + eps
    thin = solid & ~opened
    seeds = thin & (_edt(~opened, v) > min_wall + eps)
    if seeds.any():
        parts = ndi.binary_dilation(seeds, structure=np.ones((3, 3, 3), bool),
                                    iterations=int(math.ceil(min_wall / v)) + 1, mask=thin)
    else:
        parts = seeds
    return thin, parts, inside


def _clusters(mask: np.ndarray, grid: Grid, limit: int = 8) -> tuple[int, list[dict[str, Any]]]:
    """Number of 26-connected regions of at least 0.5 mm3, and the largest ones with their centres."""
    _, ndi, _, _ = _deps()
    v = grid.voxel
    labels, n = ndi.label(mask, structure=np.ones((3, 3, 3), bool))
    if not n:
        return 0, []
    sizes = np.bincount(labels.ravel(), minlength=n + 1)[1:]
    out = []
    for lab in np.argsort(sizes)[::-1][:limit]:
        if sizes[lab] * v ** 3 < 0.5:
            break
        idx = np.argwhere(labels == lab + 1)
        out.append({"volume_mm3": round(float(sizes[lab] * v ** 3), 3),
                    "centre_mm": (grid.origin + (idx.mean(axis=0) + 0.5) * v).round(2).tolist()})
    return int((sizes * v ** 3 >= 0.5).sum()), out


def thin_regions(solid: np.ndarray, grid: Grid, min_wall: float,
                 bias: float = FINAL_BIAS) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Thin parts (thickening candidates), surface relief, and statistics for the report."""
    _, ndi, _, _ = _deps()
    v = grid.voxel
    thin, parts, inside = thin_masks(solid, v, min_wall, bias)
    relief = thin & ~parts
    total = int(solid.sum())
    ridge = solid & (ndi.maximum_filter(inside, size=3) <= inside + 1e-6) & (inside > 0)
    t = np.maximum(2 * (inside[ridge] - bias * v), v)
    n_parts, clusters = _clusters(parts, grid)

    def frac(mask):
        return round(float(mask.sum() / total), 5) if total else 0.0

    stats = {
        "min_wall_mm": min_wall,
        "thin_volume_mm3": round(float(thin.sum() * v ** 3), 3),
        "thin_volume_fraction": frac(thin),
        "thin_part_volume_mm3": round(float(parts.sum() * v ** 3), 3),
        "thin_part_volume_fraction": frac(parts),
        "thin_part_regions": n_parts,
        "largest_thin_parts": clusters,
        "relief_volume_fraction": frac(relief),
        "note": f"thin_volume counts everything a {min_wall} mm ball cannot reach. Thin parts are sheets, fins and "
                f"strands that stick out more than {min_wall} mm from a thicker body with both surfaces close "
                "together (the only regions --thicken grows); relief is raised detail and convex edges on a thicker "
                "body (left as is). Regions are counted from 0.5 mm3.",
        "thinnest_feature_mm": {
            "p1": round(float(np.percentile(t, 1)), 3) if t.size else None,
            "p5": round(float(np.percentile(t, 5)), 3) if t.size else None,
            "note": "percentiles of 2 x distance-to-surface along the medial ridge; tapering tips read near zero, "
                    f"so the minimum is not meaningful; resolution about one voxel ({v:.2f} mm)",
        },
    }
    return parts, relief, stats


def thicken_parts(solid: np.ndarray, parts: np.ndarray, grid: Grid, min_wall: float) -> tuple[np.ndarray, int]:
    """Grow thin parts of the raw voxel solid to at least min_wall, never surface relief.

    Each thin-part voxel grows by (min_wall / 2 - local half-thickness), rounded up to whole voxels, where the local
    half-thickness is the largest distance to the surface within that radius. A 0.6 mm fin becomes about 1.2 mm,
    not 0.6 mm plus a full wall. Work is done on the bounding box of the parts. Returns the new solid and the number
    of voxels added.
    """
    _, ndi, _, _ = _deps()
    v = grid.voxel
    if not parts.any():
        return solid, 0
    target = min_wall / 2
    k = int(math.ceil(target / v))
    pad = k + 2
    idx = np.argwhere(parts)
    lo = np.maximum(idx.min(axis=0) - pad, 0)
    hi = np.minimum(idx.max(axis=0) + pad + 1, np.array(solid.shape))
    box = tuple(slice(a, b) for a, b in zip(lo, hi, strict=True))
    sub, sub_parts = solid[box], parts[box]
    zz, yy, xx = np.mgrid[-k:k + 1, -k:k + 1, -k:k + 1]
    ball = (zz ** 2 + yy ** 2 + xx ** 2) <= k * k
    half = ndi.maximum_filter(_edt(sub, v), footprint=ball) - RAW_BIAS * v
    need = np.where(sub_parts, np.clip(target - half, 0.0, target), 0.0)
    grown = np.zeros_like(sub)
    eps = 1e-3 * v  # distances are float32 multiples of the voxel
    for level in np.arange(1, k + 1) * v:  # whole-voxel growth levels, rounded up
        src = sub_parts & (need > level - v + eps)
        if not src.any():
            break
        grown |= _edt(~src, v) <= level + eps
    added = int((grown & ~sub).sum())
    out = solid.copy()
    out[box] = sub | grown
    return out, added


# ---------------------------------------------------------------- field operations


def footprint(mesh, height: float) -> tuple[np.ndarray, float]:
    """Centre (x, y) and radius of the lowest 8% of the model."""
    low = mesh.vertices[mesh.vertices[:, 2] <= mesh.bounds[0][2] + 0.08 * height]
    if len(low) == 0:
        low = mesh.vertices
    c = (low[:, :2].min(axis=0) + low[:, :2].max(axis=0)) / 2
    r = float(np.linalg.norm(low[:, :2] - c, axis=1).max())
    return c, r


def _cylinder_sdf(X, Y, Z, cx, cy, radius, z0, z1):
    r = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2) - radius
    zc, hz = (z0 + z1) / 2, (z1 - z0) / 2
    return np.maximum(r, np.abs(Z - zc) - hz)


def build_field(solid: np.ndarray, grid: Grid, mesh, opts: PrintOptions, report: Report) -> np.ndarray:
    _, ndi, _, _ = _deps()
    v = grid.voxel
    # signed distance, negative inside. Voxels touched by the surface count as solid, which puts the zero level about
    # half a voxel outside the true surface; shifting the field (INFLATION_SHIFT) moves it back.
    f = _edt(~solid, v) - _edt(solid, v) + INFLATION_SHIFT * v
    if opts.smooth > 0:
        f = ndi.gaussian_filter(f, sigma=opts.smooth).astype(np.float32)
    ax, ay, az = grid.axes()
    X, Y, Z = np.meshgrid(ax, ay, az, indexing="ij", copy=False, sparse=True)
    zmin = float(mesh.bounds[0][2])

    if opts.base in ("flat", "pedestal"):
        z_cut = zmin + opts.flat_cut_mm
        f = np.maximum(f, (z_cut - Z).astype(np.float32))
        report.steps.append({"step": "flat_base", "cut_mm": opts.flat_cut_mm})
    if opts.base == "pedestal":
        c, r = footprint(mesh, opts.height_mm)
        radius = r + opts.pedestal_margin_mm
        z_top = zmin + opts.flat_cut_mm + 0.6  # feet sink 0.6 mm into the pedestal
        z_bot = z_top - opts.pedestal_height_mm - 0.6
        cyl = _cylinder_sdf(X, Y, Z, c[0], c[1], radius, z_bot, z_top).astype(np.float32)
        f = np.minimum(f, cyl)
        report.steps.append({"step": "pedestal", "centre_mm": c.round(2).tolist(), "radius_mm": round(radius, 2),
                             "height_mm": opts.pedestal_height_mm})

    if opts.hollow_wall_mm:
        w = opts.hollow_wall_mm
        cavity = f < -w
        labels, n = ndi.label(cavity)
        holes = []
        if n:
            sizes = np.bincount(labels.ravel(), minlength=n + 1)[1:]
            keep = [lab + 1 for lab in range(n) if sizes[lab] * v ** 3 >= 20.0]  # tiny pockets stay solid
            small = n - len(keep)
            # hollow only the kept cavities: the inner-wall field is applied within a few voxels of them, so small
            # pockets (which could not be drained) remain solid instead of becoming sealed voids
            near = _edt(~np.isin(labels, keep), v) <= 3 * v if keep else np.zeros_like(cavity)
            g = np.maximum(f, np.where(near, -(f + w), np.float32(-1e3)).astype(np.float32))
            # drain hole per cavity: vertical cylinder from below the model up into the cavity's lowest voxel,
            # at the lowest cavity voxel nearest the cavity's own centre line
            zlow = float(az[0])
            for lab in keep:
                idx = np.argwhere(labels == lab)
                kmin = idx[:, 2].min()
                bottom = idx[idx[:, 2] == kmin]
                centre = idx[:, :2].mean(axis=0)
                pick = bottom[np.argmin(np.linalg.norm(bottom[:, :2] - centre, axis=1))]
                hx, hy = ax[pick[0]], ay[pick[1]]
                ztop = float(az[pick[2]]) + 2 * v
                hole = _cylinder_sdf(X, Y, Z, hx, hy, opts.drain_hole_mm / 2, zlow - 1.0, ztop).astype(np.float32)
                g = np.maximum(g, -hole)
                holes.append({"centre_mm": [round(float(hx), 2), round(float(hy), 2)],
                              "diameter_mm": opts.drain_hole_mm,
                              "cavity_mm3": round(float(sizes[lab - 1] * v ** 3), 1)})
            # pockets too small to drain are left solid
            f = g if keep else f
            if small:
                report.warnings.append(f"{small} small interior pocket(s) under 20 mm3 left solid")
        else:
            report.warnings.append("model is too thin everywhere for a hollow wall of this thickness; left solid")
        report.steps.append({"step": "hollow", "wall_mm": w, "cavities": len(holes), "drain_holes": holes})
    return f


def field_to_mesh(f: np.ndarray, grid: Grid, report: Report):
    trimesh, _, measure, _ = _deps()
    padded = np.pad(f, 1, mode="constant", constant_values=float(f.max()) + 1.0)
    verts, faces, _, _ = measure.marching_cubes(padded, level=0.0, spacing=(grid.voxel,) * 3)
    verts += grid.origin + 0.5 * grid.voxel - grid.voxel  # undo the pad, voxel centres
    mesh = trimesh.Trimesh(verts, faces, process=True)
    if mesh.volume < 0:
        mesh.invert()
    report.steps.append({"step": "marching_cubes", "faces": int(len(mesh.faces))})
    return mesh


def smooth_surface(mesh, iterations: int, report: Report):
    """Taubin smoothing (no shrinkage) to remove the terraces a binary-grid distance field leaves behind.

    The flat sole is pinned back onto its plane afterwards so the print still sits flat.
    """
    trimesh, _, _, _ = _deps()
    zmin = float(mesh.bounds[0][2])
    flat = mesh.vertices[:, 2] < zmin + 1e-6
    trimesh.smoothing.filter_taubin(mesh, lamb=0.5, nu=-0.53, iterations=iterations)
    verts = np.asarray(mesh.vertices).copy()
    verts[flat, 2] = zmin
    verts[:, 2] = np.maximum(verts[:, 2], zmin)
    mesh.vertices = verts
    report.steps.append({"step": "smooth", "method": "taubin", "iterations": iterations})
    return mesh


def to_manifold(mesh):
    _, _, _, m3 = _deps()
    mm = m3.Mesh(vert_properties=np.asarray(mesh.vertices, dtype=np.float32),
                 tri_verts=np.asarray(mesh.faces, dtype=np.uint32))
    return m3.Manifold(mm)


def from_manifold(man):
    """Keep manifold3d's vertex indexing: merging coincident positions would turn touching sheets into pinches."""
    trimesh, _, _, _ = _deps()
    out = man.to_mesh()
    return trimesh.Trimesh(np.asarray(out.vert_properties)[:, :3], np.asarray(out.tri_verts), process=False)


def self_intersections(mesh) -> int | None:
    """Count of self-intersecting faces via pymeshlab when it is installed (optional; it is GPL-licensed)."""
    try:
        import pymeshlab
    except ImportError:
        return None
    ms = pymeshlab.MeshSet()
    ms.add_mesh(pymeshlab.Mesh(np.asarray(mesh.vertices, dtype=np.float64), np.asarray(mesh.faces, dtype=np.int32)))
    ms.compute_selection_by_self_intersections_per_face()
    return int(ms.current_mesh().selected_face_number())


def transfer_colors(src, dst) -> bool:
    try:
        from scipy.spatial import cKDTree
    except ImportError:  # pragma: no cover
        return False
    cols = getattr(src.visual, "vertex_colors", None)
    if cols is None or len(cols) != len(src.vertices):
        return False
    _, idx = cKDTree(src.vertices).query(dst.vertices, k=1)
    dst.visual.vertex_colors = np.asarray(cols)[idx]
    return True


# ---------------------------------------------------------------- main entry


def thin_warnings(after: dict[str, Any], opts: PrintOptions) -> list[str]:
    """What is still thin in the result, and what to do about it. Never suggests --thicken when it was used."""
    w = opts.min_wall_mm
    out = []
    frac = after["thin_part_volume_fraction"]
    if frac > 0.001:
        where = "; largest at " + ", ".join(str(c["centre_mm"]) for c in after["largest_thin_parts"][:3]) \
            if after["largest_thin_parts"] else ""
        scale = (f"Thin parts scale with the figure: printing larger (raise --height above {opts.height_mm:g}) "
                 "is the most reliable fix, and the slicer's thin-wall handling (Arachne walls, 'detect thin walls')"
                 " prints most of the rest")
        if opts.thicken:
            out.append(f"after --thicken, {frac * 100:.2f}% of the volume is still in parts thinner than {w} mm "
                       f"({after['thin_part_regions']} region(s){where}). {scale}; check them in the slicer preview")
        else:
            out.append(f"{frac * 100:.2f}% of the volume is in parts thinner than {w} mm (sheets, fins or strands; "
                       f"{after['thin_part_regions']} region(s){where}). {scale}. If they must print solid at this "
                       "size, --thicken grows only these parts to the wall and blunts their tips")
    if after["relief_volume_fraction"] > 0.001:
        out.append(f"{after['relief_volume_fraction'] * 100:.2f}% of the volume is fine surface relief, edges, "
                   f"or short webs (up to about {2 * w:g} mm across) between thicker parts, all narrower than {w} mm; "
                   "left as is (--thicken never grows it). The printer softens relief it cannot resolve, and a slicer "
                   "may drop a thin web and leave a hole there; a larger print keeps more of both")
    return out


def repair(model: Path, out_dir: Path, opts: PrintOptions, name: str = "figure") -> dict[str, Any]:
    trimesh, ndi, _, m3 = _deps()
    t0 = time.time()
    report = Report()
    if opts.fragments not in ("merge", "remove", "keep"):
        raise UsageError("--fragments must be merge, remove or keep")
    if opts.base not in ("flat", "pedestal", "none"):
        raise UsageError("--base must be flat, pedestal or none")
    if opts.height_mm <= 0:
        raise UsageError("--height must be positive")
    if opts.voxel_mm is not None and opts.voxel_mm <= 0:
        raise UsageError("--voxel must be positive (omit it for the default height / 500)")
    if opts.min_wall_mm <= 0:
        raise UsageError("--min-wall must be positive")
    mesh, up = load_mesh(model, opts)
    v = opts.voxel_mm or min(0.6, max(0.1, opts.height_mm / 500))
    v0 = v
    report.input = {"path": str(model), "up_axis": up, **diagnose(mesh),
                    "scaled_bounds_mm": mesh.bounds.round(3).tolist()}
    log(f"print: input {report.input['faces']} faces, watertight={report.input['watertight']}")

    lo, hi = mesh.bounds[0].copy(), mesh.bounds[1].copy()
    if opts.base == "pedestal":
        c, r = footprint(mesh, opts.height_mm)
        rad = r + opts.pedestal_margin_mm
        lo[:2] = np.minimum(lo[:2], c - rad)
        hi[:2] = np.maximum(hi[:2], c + rad)
        lo[2] -= opts.pedestal_height_mm + 1.0

    def make_grid(v):
        # room for thickening as whole voxels of padding, so the lattice (and every untouched region) is the same
        # with and without --thicken
        return Grid(lo, hi, v, pad=4 + (int(math.ceil(opts.min_wall_mm / v)) if opts.thicken else 0))

    grid = make_grid(v)
    while grid.size > MAX_VOXELS:
        v *= 1.25
        grid = make_grid(v)
    if v != v0:
        report.warnings.append(f"voxel size raised from {v0:.3f} to {v:.3f} mm to stay under {MAX_VOXELS:,} voxels; "
                               "details under one voxel are lost")
    if opts.hollow_wall_mm is not None and opts.hollow_wall_mm < 2 * v:
        raise UsageError(f"--hollow wall {opts.hollow_wall_mm} mm is below two voxels ({2 * v:.2f} mm); "
                         "use a thicker wall or a smaller --voxel")
    if opts.hollow_wall_mm is not None and opts.drain_hole_mm < 2 * v:
        report.warnings.append(f"--drain-hole {opts.drain_hole_mm} mm is under two voxels ({2 * v:.2f} mm) and may "
                               "close up; use at least 2 mm for resin to drain")
    log(f"print: grid {grid.shape} at {v:.3f} mm")

    solid = voxelize(mesh, grid, report)
    solid = handle_fragments(solid, grid, opts, report)
    parts, relief, before = thin_regions(solid, grid, opts.min_wall_mm, bias=RAW_BIAS)
    report.steps.append({"step": "wall_thickness_before", **before})
    if opts.thicken:
        solid, added = thicken_parts(solid, parts, grid, opts.min_wall_mm)
        report.steps.append({"step": "thicken", "added_mm3": round(added * v ** 3, 3),
                             "thin_part_volume_mm3": before["thin_part_volume_mm3"],
                             "thin_part_regions": before["thin_part_regions"],
                             "relief_left_mm3": round(float(relief.sum() * v ** 3), 3),
                             "note": f"thin parts grown towards {opts.min_wall_mm} mm (all of thin_part_volume, "
                                     "including regions under 0.5 mm3 that thin_part_regions does not count); "
                                     "surface relief left as is"})
        if added:
            solid = ndi.binary_fill_holes(solid)  # growth can close small pockets; fill them

    f = build_field(solid, grid, mesh, opts, report)
    final_solid = f <= 0
    _, _, after = thin_regions(final_solid, grid, opts.min_wall_mm)
    del solid, parts, relief, final_solid

    raw = field_to_mesh(f, grid, report)
    del f
    # Final surface: try smoothing levels and simplification tolerances, keep the first candidate the
    # self-intersection check accepts (or the one with the fewest intersections when none is clean)
    tol0 = opts.simplify_mm if opts.simplify_mm is not None else v / 10
    base_man = to_manifold(raw)
    status = str(base_man.status())
    unsmoothed = from_manifold(base_man) if "NoError" in status else raw
    levels = sorted({opts.smooth_iterations, opts.smooth_iterations // 2, 0}, reverse=True)
    best = None  # (si, order, man, mesh, iterations, tol)
    tried = []
    order = 0
    if "NoError" in status:
        for iters in levels:
            cand_mesh = unsmoothed if iters == 0 else smooth_surface(unsmoothed.copy(), iters, Report())
            cm = to_manifold(cand_mesh)
            if "NoError" not in str(cm.status()):
                tried.append({"smooth": iters, "result": "invalid after smoothing"})
                continue
            for tol in (tol0, tol0 / 2, tol0 / 4, 0.0):
                sm = cm.simplify(tol) if tol > 0 else cm
                mesh_c = from_manifold(sm)
                si = self_intersections(mesh_c)
                tried.append({"smooth": iters, "tolerance_mm": round(tol, 4), "self_intersecting_faces": si})
                key = (si if si is not None else 0, order)
                order += 1
                if best is None or key < best[:2]:
                    best = (key[0], key[1], sm, mesh_c, iters, tol)
                if not si:
                    break
            if best is not None and best[0] == 0:
                break
    if best is not None:
        si, _, man, out_mesh, used_iters, tol = best
    else:
        si, man, out_mesh, used_iters, tol = self_intersections(raw), base_man, raw, 0, 0.0
    report.steps.append({"step": "smooth_and_simplify", "smooth_iterations": used_iters, "tolerance_mm": tol,
                         "attempts": tried})
    if used_iters < opts.smooth_iterations:
        report.warnings.append(f"smoothing reduced to {used_iters} iterations to avoid self-intersections; "
                               "faint terraces may show")
    parts = None
    if "NoError" in status:
        pieces = man.decompose()
        kept = [pc for pc in pieces if abs(pc.volume()) > 2 * v ** 3]  # drop zero-volume slivers from simplification
        if len(kept) < len(pieces):
            report.steps.append({"step": "slivers", "removed": len(pieces) - len(kept)})
            man = m3.Manifold.compose(kept)
            out_mesh = from_manifold(man)
        parts = len(kept)
    # the flat cut leaves the sole at z = cut; put the print on the bed and report what was achieved
    out_mesh.apply_translation([0, 0, -out_mesh.bounds[0][2]])

    colored = False
    if opts.color:
        colored = transfer_colors(mesh, out_mesh)
        if not colored:
            report.warnings.append("--color: the input has no usable texture or vertex colours")

    out_dir.mkdir(parents=True, exist_ok=True)
    stl = out_dir / f"{name}.stl"
    tmf = out_dir / f"{name}.3mf"
    out_mesh.export(stl)
    files = {"stl": str(stl)}
    try:
        out_mesh.export(tmf)
        files["3mf"] = str(tmf)
    except Exception as e:  # noqa: BLE001 - 3MF needs optional XML deps
        report.warnings.append(f"3MF export failed: {type(e).__name__}: {e}")
    if colored:
        ply = out_dir / f"{name}_color.ply"
        out_mesh.export(ply)
        files["color_ply"] = str(ply)

    b = out_mesh.bounds
    if si:
        report.warnings.append(f"{si} faces still self-intersect after simplification (pymeshlab check)")
    report.output = {
        "figure_height_mm": opts.height_mm,
        "pedestal_height_mm": opts.pedestal_height_mm if opts.base == "pedestal" else 0.0,
        "files": files,
        "faces": int(len(out_mesh.faces)),
        "bounds_mm": b.round(3).tolist(),
        "size_mm": (b[1] - b[0]).round(3).tolist(),
        "height_mm": round(float(b[1][2] - b[0][2]), 3),  # overall, including any pedestal
        "volume_mm3": round(float(out_mesh.volume), 1),
        "volume_ml": round(float(out_mesh.volume) / 1000, 2),
        "surface_area_mm2": round(float(out_mesh.area), 1),
        "voxel_mm": round(v, 4),
        "simplify_tolerance_mm": round(tol, 4),
    }
    report.checks = {
        "watertight": bool(out_mesh.is_watertight),
        "winding_consistent": bool(out_mesh.is_winding_consistent),
        "is_volume": bool(out_mesh.is_volume),
        "manifold3d_status": status,
        "parts": parts,
        "genus": int(man.genus()) if "NoError" in status else None,
        "self_intersecting_faces": si,
        "self_intersection_check": "pymeshlab" if si is not None else "not run (pymeshlab not installed); "
                                   "marching cubes output does not self-intersect by construction",
        "wall_thickness": after,
        "flat_base": opts.base in ("flat", "pedestal"),
    }
    printable = report.checks["watertight"] and report.checks["is_volume"] and "NoError" in status
    report.checks["printable"] = bool(printable)
    report.warnings.extend(thin_warnings(after, opts))
    if parts and parts > 1:
        report.warnings.append(f"the result has {parts} separate parts")
    result = {"seconds": round(time.time() - t0, 1), **asdict(report)}
    if not printable:
        raise CheckFailed("repaired mesh did not pass the watertight/manifold checks", result)
    return result


def section_image(stl: Path, out: Path, size: int = 900) -> dict[str, Any]:
    """Cross-section through the middle of the print (XZ plane, seen from the front): walls, cavities, drain holes."""
    trimesh, _, _, _ = _deps()
    from PIL import Image, ImageChops, ImageDraw

    mesh = trimesh.load(str(stl), force="mesh")
    centre = mesh.bounds.mean(axis=0)
    sec = mesh.section(plane_origin=centre, plane_normal=[0, 1, 0])
    if sec is None:
        return {"section": None}
    loops = [np.asarray(d)[:, [0, 2]] for d in sec.discrete]  # X and Z
    lo, hi = mesh.bounds[0][[0, 2]], mesh.bounds[1][[0, 2]]
    scale = (size - 40) / max(hi - lo)
    w, h = int((hi[0] - lo[0]) * scale) + 40, int((hi[1] - lo[1]) * scale) + 40
    acc = Image.new("1", (w, h), 0)
    for loop in loops:
        pts = [(20 + (x - lo[0]) * scale, h - 20 - (z - lo[1]) * scale) for x, z in loop]
        if len(pts) < 3:
            continue
        m = Image.new("1", (w, h), 0)
        ImageDraw.Draw(m).polygon(pts, fill=1)
        acc = ImageChops.logical_xor(acc, m)  # even-odd fill: cavities stay empty
    img = Image.new("RGB", (w, h), (245, 245, 245))
    img.paste((60, 60, 70), mask=acc)
    ImageDraw.Draw(img).text((8, 6), f"section at y = {centre[1]:.1f} mm (front view)", fill=(200, 40, 40))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return {"section": str(out), "loops": len(loops)}
