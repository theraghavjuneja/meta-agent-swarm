"""Image-generation prompt for the hero image -- the one place to revise it.

The hero image is the shared visual anchor for every deliverable: both ad
formats and the video are derived from its exact bytes (see
compositing.py). That makes it the single highest-leverage prompt in the
pipeline, so it is kept here, isolated from the activity plumbing.

Why the prompt is this prescriptive
-----------------------------------
Passing the LLM-written ``scene_description`` through verbatim left every
art-direction decision to chance, and image models fill that vacuum with
the most "ad-like" thing they have seen: collages, split-screen storyboards,
multi-panel mood boards, and -- worst of all -- their own typography
(headlines, "PRE-ORDER NOW" buttons, feature badges such as "100% vegan").
That baked-in text then collides with the deterministic headline/CTA
overlay, and badges invent product claims the brief never supplied.

So the brief-specific parts (scene, composition, palette, product identity)
are wrapped in a fixed house style that is identical for every campaign:

* one continuous photographic scene, never a collage or grid;
* zero rendered text, logos, badges or UI -- all copy is overlaid later;
* the product as the single focal point in the middle band of a portrait
  frame, with calm negative space above and below, so the 1:1 and 9:16
  crops both keep the product and have a clean zone for headline and CTA.

The fixed rules come *after* the campaign-specific material so that, if the
LLM-written scene asks for something contradictory (e.g. "headline text on
a neon sign"), the later, explicit rule wins.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.creative.models import CreativeSpec

__all__ = ["build_hero_image_prompt"]


_HOUSE_STYLE = (
    "Style: premium commercial product photography for a paid social ad "
    "(Meta feed, Stories and Reels). Shot on a full-frame camera with a "
    "sharp prime lens, controlled studio-quality key light with soft fill "
    "and a subtle rim light that separates the product from the background. "
    "Clean, modern, editorial art direction; restrained props that support "
    "the product rather than compete with it; natural, true-to-life colors "
    "and materials; crisp focus on the product with gentle background "
    "depth of field. Photorealistic, not illustrated, not 3D-rendered-"
    "looking, no HDR halos, no oversaturation."
)

_FRAMING = (
    "Framing (portrait, 2:3): the product is the single, unmistakable focal "
    "point, centered horizontally, sitting in the middle band of the frame "
    "and filling roughly 30-40% of the frame height. Keep the top quarter "
    "and the bottom fifth of the frame as calm, uncluttered, evenly lit "
    "background (seamless backdrop, wall, sky, surface or soft bokeh) with "
    "no objects in it -- that space is reserved for a headline and a "
    "call-to-action button that will be added in post-production. Nothing "
    "important may touch the frame edges."
)

_HARD_RULES = (
    "Hard rules -- these override anything above:\n"
    "- One single continuous photograph. No collage, grid, split screen, "
    "panels, diptych, storyboard, mood board, frames, borders, insets or "
    "multiple views of the product.\n"
    "- No text of any kind anywhere in the image: no headlines, captions, "
    "slogans, prices, buttons, badges, icons, stickers, watermarks, "
    "signage or UI elements. The only permitted lettering is the label "
    "that is physically printed on the product packaging itself.\n"
    "- Do not depict claims, certifications, awards, nutrition panels or "
    "benefit call-outs.\n"
    "- Exactly one product unit unless the scene explicitly calls for more; "
    "no duplicated or mirrored products.\n"
    "- Hands and people, if present, are anatomically correct and "
    "secondary to the product."
)

_REFERENCE_RULES = (
    "Product reference: the attached reference image shows the exact "
    "product being advertised. Reproduce that product faithfully -- same "
    "container shape and proportions, same cap/lid, same colors, same label "
    "layout, same logo and same label text. Do not redesign, relabel, "
    "restyle, recolor or simplify it, and do not invent a different "
    "package. Place that exact product into the scene described above with "
    "lighting and reflections that match the scene. Ignore the reference "
    "image's own background, framing and lighting."
)


def _product_line(spec: CreativeSpec) -> str:
    identity = spec.product_identity or {}
    name = str(identity.get("name") or "").strip()
    traits = [str(t).strip() for t in identity.get("key_visual_traits") or [] if str(t).strip()]
    line = f"Product: {name}." if name else "Product: the advertised product."
    if traits:
        line += " Key visual traits that must be visible: " + "; ".join(traits) + "."
    return line


def build_hero_image_prompt(spec: CreativeSpec, *, has_reference_image: bool = False) -> str:
    """Return the full hero-image prompt for a creative spec.

    Campaign-specific material first (product, scene, composition, palette),
    then the fixed house style, framing and hard rules, then -- only when
    the user supplied one -- the reference-image fidelity instructions.
    """
    parts = [
        "Create one photorealistic hero product photograph for a paid social ad campaign.",
        _product_line(spec),
        f"Scene: {spec.scene_description.strip()}",
    ]
    if spec.composition_guidance:
        parts.append(f"Creative direction for composition: {spec.composition_guidance.strip()}")
    if spec.palette:
        parts.append(
            "Color grading: build the lighting, backdrop and props around this "
            f"palette -- {', '.join(spec.palette)} -- so the image reads as one "
            "cohesive, on-brand color story."
        )
    parts += [_HOUSE_STYLE, _FRAMING, _HARD_RULES]
    if has_reference_image:
        parts.append(_REFERENCE_RULES)
    return "\n\n".join(parts)
