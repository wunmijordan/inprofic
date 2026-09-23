"""Build web-ready INPROFIC assets from the approved 2560px source artwork.

The visible logo pixels are only cropped (never enlarged) for the shared brand
files. PWA derivatives are rendered from those full-resolution crops at their
required platform sizes, with the full wordmark intentionally used so the
mobile launch artwork carries the complete INPROFIC name.
"""

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "inprofic logos"
BRAND = ROOT / "apps" / "core" / "static" / "core" / "brand"
PWA = ROOT / "apps" / "core" / "static" / "core" / "pwa"

SOURCE_ASSETS = {
    "inprofic-mark.png": SOURCE / "Inprofic 2-1.png",
    "inprofic-mark-on-dark.png": SOURCE / "Inprofic 4-1.png",
    "inprofic-wordmark-on-dark.png": SOURCE / "Inprofic 4.png",
    "inprofic-wordmark-on-light.png": SOURCE / "inprofic 3.png",
}


def cropped(source: Path) -> Image.Image:
    image = Image.open(source).convert("RGBA")
    bounds = image.getbbox()
    if bounds is None:
        raise ValueError(f"Logo source is empty: {source}")
    return image.crop(bounds)


def padded_mark(mark: Image.Image, size: int = 128, fill_ratio: float = 0.82) -> Image.Image:
    """Place the approved mark on a transparent square with optical favicon padding."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    target_height = round(size * fill_ratio)
    target_width = round(mark.width * target_height / mark.height)
    rendered = mark.resize((target_width, target_height), Image.Resampling.LANCZOS)
    canvas.alpha_composite(rendered, ((size - target_width) // 2, (size - target_height) // 2))
    return canvas

def save_brand_assets() -> dict[str, Image.Image]:
    BRAND.mkdir(parents=True, exist_ok=True)
    images = {name: cropped(source) for name, source in SOURCE_ASSETS.items()}
    for name, image in images.items():
        image.save(BRAND / name, format="PNG", optimize=True)
    favicon_sources = {
        "inprofic-favicon.png": images["inprofic-mark.png"],
        "inprofic-favicon-on-dark.png": images["inprofic-mark-on-dark.png"],
    }
    for name, mark in favicon_sources.items():
        padded_mark(mark).save(BRAND / name, format="PNG", optimize=True)
    return images


def pwa_icon(wordmark: Image.Image, size: int, background: str, width_ratio: float) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), background)
    target_width = round(size * width_ratio)
    target_height = round(wordmark.height * target_width / wordmark.width)
    rendered = wordmark.resize((target_width, target_height), Image.Resampling.LANCZOS)
    canvas.alpha_composite(rendered, ((size - target_width) // 2, (size - target_height) // 2))
    return canvas.convert("RGB")


def save_pwa_assets(images: dict[str, Image.Image]) -> None:
    PWA.mkdir(parents=True, exist_ok=True)
    light_wordmark = images["inprofic-wordmark-on-light.png"]
    dark_wordmark = images["inprofic-wordmark-on-dark.png"]
    outputs = {
        "icon-180.png": pwa_icon(light_wordmark, 180, "#FFF1E8", 0.82),
        "icon-192.png": pwa_icon(light_wordmark, 192, "#FFF1E8", 0.82),
        "icon-512.png": pwa_icon(light_wordmark, 512, "#FFF1E8", 0.82),
        "icon-maskable-192.png": pwa_icon(dark_wordmark, 192, "#050733", 0.62),
        "icon-maskable-512.png": pwa_icon(dark_wordmark, 512, "#050733", 0.62),
    }
    for name, image in outputs.items():
        image.save(PWA / name, format="PNG", optimize=True)


if __name__ == "__main__":
    save_pwa_assets(save_brand_assets())
