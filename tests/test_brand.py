"""Tests for the local brand assets served by HA's brands integration."""

from pathlib import Path

from PIL import Image
import pytest

BRAND_DIR = Path(__file__).parent.parent / "custom_components" / "tel2" / "brand"


@pytest.mark.parametrize(
    ("name", "size"),
    [
        ("icon.png", 256),
        ("icon@2x.png", 512),
    ],
)
def test_brand_image(name: str, size: int) -> None:
    path = BRAND_DIR / name
    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.mode == "RGBA"
        assert image.size == (size, size)
        assert image.getpixel((0, 0))[3] == 0
        assert image.getbbox() is not None
    assert path.stat().st_size < 50_000
