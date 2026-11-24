"""The brand images that Home Assistant and its Marketplace show for the integration (decision 16)."""

from pathlib import Path

from PIL import Image
import pytest

BRAND = Path(__file__).parents[1] / "custom_components" / "sonoff2hue" / "brand"


@pytest.mark.parametrize(("name", "size"), [("icon.png", 256), ("icon@2x.png", 512)])
def test_the_icon_is_a_trimmed_square_with_a_transparent_background(name: str, size: int) -> None:
    with Image.open(BRAND / name) as image:
        assert image.format == "PNG"
        assert image.size == (size, size)
        assert image.mode == "RGBA"
        alpha = image.getchannel("A")
        # Trimmed: the icon reaches all four edges. Transparent: outside its rounded corners.
        assert alpha.getbbox() == (0, 0, size, size)
        assert alpha.getpixel((0, 0)) == alpha.getpixel((size - 1, size - 1)) == 0
