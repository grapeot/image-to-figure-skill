"""Prompt templates for the image stages.

The templates are deliberately strict about two things that decide whether image-to-3D succeeds: the image model must
not invent details (whatever appears in the turnaround gets built), and the four views must share scale, ground line
and pose so the 3D model can fuse them.
"""

from __future__ import annotations

STYLE = (
    "soft matte vinyl and clay toy material, rounded shapes, collectible art-toy figure style rendered in Blender, "
    "gentle even studio lighting"
)

LOOKDEV = """3D look-development render of the exact character in the reference image(s) as a cute chibi collectible \
figure, about {heads} heads tall, {style}, plain pastel background. Two views side by side: front view and 3/4 view. \
No display base.

Copy the character's design faithfully from the reference: same hair, face, colours, outfit, accessories and their \
sides (left/right). Do not add accessories, pockets, badges, tails, logos, text or any other detail that is not in the \
reference.
{description}
Pose: standing, arms relaxed down at the sides slightly away from the body, hands open, feet slightly apart, so arms \
and legs read as separate shapes. No text."""

TURNAROUND = """Orthographic turnaround of the exact chibi collectible figure in image 1 (if image 1 shows several \
views, its front view is the reference), for 3D reconstruction. {refs_note}Four views side by side in one row, \
evenly spaced with clear empty background between them, same scale, same height, feet on the same ground line: \
1) front view, 2) left side view (the figure faces LEFT, showing its own left side), 3) back view, 4) right side \
view (the figure faces RIGHT, showing its own right side). True orthographic profile views, not three-quarter.

Keep the figure identical to image 1: same material, proportions, colours, hair, face, outfit and accessories, each on \
the same side of the body. Do not add any detail that is not visible in image 1; for the back view, continue the \
visible materials plainly rather than inventing new decorations (no tails, bows, pockets, badges or logos that image 1 \
does not show).
{description}
Pose identical in all four views: standing straight, arms relaxed down at the sides slightly away from the body, \
hands open. No display base. Flat neutral light grey background, soft even studio lighting, no cast shadows, no \
text, no labels."""

EDIT = """Edit image 1 (a four-view turnaround of a chibi vinyl figure: front, left side, back, right side). Keep \
everything pixel-for-pixel identical (same four figures, same poses, same positions, same scale, same lighting, same \
plain background) EXCEPT these corrections:
{fixes}
{refs_note}Do not add any logo, text, letters or emblem anywhere. Change nothing else."""


def _description_block(description: str | None) -> str:
    d = (description or "").strip()
    return f"\nCharacter details to keep: {d}\n" if d else ""


def lookdev_prompt(description: str | None = None, heads: float = 3) -> str:
    heads_txt = f"{heads:g}"
    return LOOKDEV.format(heads=heads_txt, style=STYLE, description=_description_block(description))


def turnaround_prompt(description: str | None = None, n_refs: int = 0) -> str:
    refs_note = ""
    if n_refs:
        idx = ", ".join(str(i) for i in range(2, 2 + n_refs))
        refs_note = f"Image(s) {idx} show the original character design for details. "
    return TURNAROUND.format(refs_note=refs_note, description=_description_block(description))


def edit_prompt(fixes: list[str], n_refs: int = 0) -> str:
    if not fixes:
        raise ValueError("at least one --fix is required")
    lines = "\n".join(f"{i}. {f.strip()}" for i, f in enumerate(fixes, 1))
    refs_note = ""
    if n_refs:
        idx = ", ".join(str(i) for i in range(2, 2 + n_refs))
        refs_note = f"Use image(s) {idx} as the reference for how the corrected parts should look. "
    return EDIT.format(fixes=lines, refs_note=refs_note)
