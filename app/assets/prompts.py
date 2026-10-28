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


# LEGACY (pre style-specific art direction) -- one house style for every brand:
# _HOUSE_STYLE = (
#     "Style: premium commercial product photography for a paid social ad "
#     "(Meta feed, Stories and Reels). Shot on a full-frame camera with a "
#     "sharp prime lens, controlled studio-quality key light with soft fill "
#     "and a subtle rim light that separates the product from the background. "
#     "Clean, modern, editorial art direction; restrained props that support "
#     "the product rather than compete with it; natural, true-to-life colors "
#     "and materials; crisp focus on the product with gentle background "
#     "depth of field. Photorealistic, not illustrated, not 3D-rendered-"
#     "looking, no HDR halos, no oversaturation."
# )

# What the reference ads (premium fragrance, Hira) have in common, independent of
# vertical: a deliberate colour story (one dominant hue + one accent), sculpted light,
# tactile surfaces, props that tell the product's story, and calm space for type.
_HOUSE_STYLE = (
    "Style: high-end commercial still-life photography for a paid social ad "
    "(Meta feed, Stories and Reels), at the level of a premium brand's launch "
    "campaign. Full-frame camera, sharp prime lens, shallow depth of field. "
    "One deliberate colour story: a dominant hue with a single accent, graded "
    "consistently across backdrop, surface and props. Tactile, premium surfaces "
    "and materials. Props that tell the product's story -- only elements "
    "suggested by the brief (flavours, ingredients, usage moment) -- arranged "
    "with restraint so the product stays the hero. Photorealistic, not "
    "illustrated, not 3D-rendered-looking, no HDR halos, no oversaturation."
)

# Art direction per overlay type system, so image and typography are designed
# together rather than the type being dropped onto whatever the model produced.
_STYLE_DIRECTION = {
    "editorial_serif": (
        "Art direction: luxury editorial still life. Low-key, sculpted light "
        "with deep, controlled shadows and a warm rim light tracing the "
        "product's edges; rich jewel tones with a metallic accent; surfaces such "
        "as stone, velvet, glass or polished wood; a hint of atmosphere (soft "
        "haze or bokeh). Quiet, confident, expensive."
    ),
    "bold_athletic": (
        "Art direction: high-energy performance still life. Hard directional "
        "key light with crisp shadows and strong contrast; a bold saturated "
        "accent colour against a dark or neutral set; gritty real-world "
        "surfaces (concrete, rubber gym flooring, brushed steel, chalk dust); "
        "one dynamic element frozen mid-motion (splash, powder burst, water "
        "droplets) that never covers the product label. Powerful, clean, modern."
    ),
    "modern_clean": (
        "Art direction: bright, clean studio set. Soft daylight-quality key "
        "light with gentle shadows; a seamless coloured backdrop in the "
        "palette's lightest tone; one or two simple, relevant props or "
        "furniture pieces; crisp, airy, contemporary."
    ),
}

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
    then the fixed house style, the art direction for the spec's typography
    style, framing and hard rules, then -- only when the user supplied one --
    the reference-image fidelity instructions.
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
    # LEGACY (pre style-specific art direction):
    # parts += [_HOUSE_STYLE, _FRAMING, _HARD_RULES]
    style = getattr(spec, "typography_style", None) or "modern_clean"
    parts += [
        _HOUSE_STYLE,
        _STYLE_DIRECTION.get(style, _STYLE_DIRECTION["modern_clean"]),
        _FRAMING,
        _HARD_RULES,
    ]
    if has_reference_image:
        parts.append(_REFERENCE_RULES)
    return "\n\n".join(parts)
