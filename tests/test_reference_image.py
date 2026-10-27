"""Problem 2: an uploaded reference image reaches the image model."""
import asyncio
import io
from dataclasses import fields

import pytest
from PIL import Image

from app.assets import activities
from app.assets.adapters.image_fixture import FixtureImageGenerationAdapter
from app.assets.adapters.image_openai import OpenAIImageGenerationAdapter
from app.assets.dto import GenerateHeroImageInput
from app.common.exceptions import ValidationError
from app.workflows import CampaignWorkflowInput, RetryAssetWorkflowInput


def _png(color=(255, 106, 19), size=(300, 500)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_reference_url_is_part_of_every_contract_on_the_path():
    for contract in (CampaignWorkflowInput, RetryAssetWorkflowInput, GenerateHeroImageInput):
        names = {f.name for f in fields(contract)}
        assert "reference_image_url" in names, contract.__name__


def test_fixture_adapter_uses_the_reference_image():
    adapter = FixtureImageGenerationAdapter()
    plain = asyncio.run(adapter.generate("p", "k"))
    with_ref = asyncio.run(adapter.generate("p", "k", reference_image=_png()))
    assert plain.read_bytes() != with_ref.read_bytes()


class _FakeImages:
    def __init__(self):
        self.calls = []

    async def _result(self):
        import base64
        from types import SimpleNamespace

        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(_png()).decode())])

    async def generate(self, **kwargs):
        self.calls.append(("generate", kwargs))
        return await self._result()

    async def edit(self, **kwargs):
        self.calls.append(("edit", kwargs))
        return await self._result()


def _openai_adapter_with_fake(**kwargs):
    adapter = OpenAIImageGenerationAdapter(api_token="test-key", **kwargs)
    fake = _FakeImages()
    adapter._client = type("C", (), {"images": fake})()
    return adapter, fake


def test_openai_adapter_sends_reference_as_image_input():
    adapter, fake = _openai_adapter_with_fake(input_fidelity="high")
    reference = _png()
    asyncio.run(adapter.generate("prompt", "key", reference_image=reference))

    method, kwargs = fake.calls[-1]
    assert method == "edit"
    assert kwargs["image"][1] == reference
    assert kwargs["extra_body"] == {"input_fidelity": "high"}
    assert kwargs["quality"] == "high"


def test_openai_adapter_without_reference_uses_text_to_image():
    adapter, fake = _openai_adapter_with_fake()
    asyncio.run(adapter.generate("prompt", "key"))
    method, kwargs = fake.calls[-1]
    assert method == "generate"
    assert kwargs["size"] == "1024x1536"


def test_load_reference_image_normalises_to_png(monkeypatch):
    jpeg = io.BytesIO()
    Image.new("RGB", (3000, 2000), (10, 10, 10)).save(jpeg, format="JPEG")

    async def fake_fetch(url):
        return jpeg.getvalue()

    monkeypatch.setattr(activities, "_fetch_bytes", fake_fetch)
    data = asyncio.run(activities._load_reference_image("http://example/ref.jpg"))
    image = Image.open(io.BytesIO(data))
    assert image.format == "PNG"
    assert max(image.size) <= activities._REFERENCE_MAX_EDGE_PX


def test_load_reference_image_rejects_non_images(monkeypatch):
    async def fake_fetch(url):
        return b"<html>not an image</html>"

    monkeypatch.setattr(activities, "_fetch_bytes", fake_fetch)
    with pytest.raises(ValidationError):
        asyncio.run(activities._load_reference_image("http://example/ref.jpg"))
