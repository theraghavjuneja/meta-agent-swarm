"""Problem 1 / 2: the hero-image prompt carries house style, hard rules, and
reference-fidelity instructions only when a reference exists."""
from app.assets.prompts import build_hero_image_prompt
from tests.conftest import make_spec


def test_prompt_includes_campaign_material_and_hard_rules():
    spec = make_spec()
    prompt = build_hero_image_prompt(spec)

    assert spec.scene_description in prompt
    assert spec.composition_guidance in prompt
    assert "#FF6A13" in prompt
    assert "matte black tub" in prompt
    # The two failure modes seen in data/assets: collages and baked-in text.
    assert "No collage" in prompt
    assert "No text of any kind" in prompt
    # Negative space reserved for the overlay.
    assert "reserved for a headline" in prompt


def test_hard_rules_come_after_llm_written_scene():
    spec = make_spec(scene_description="A neon sign that reads BUY NOW behind the tub.")
    prompt = build_hero_image_prompt(spec)
    assert prompt.index(spec.scene_description) < prompt.index("Hard rules")


def test_reference_rules_only_when_reference_supplied():
    spec = make_spec()
    assert "reference image" not in build_hero_image_prompt(spec)
    with_ref = build_hero_image_prompt(spec, has_reference_image=True)
    assert "reference image shows the exact product" in with_ref
    assert "same label text" in with_ref
