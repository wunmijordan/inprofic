"""Build web-ready INPROFIC assets from the approved source artwork.

Shared brand files preserve the exact cropped artwork. PWA app icons use the
approved N mark on a transparent canvas; the mobile launch loader uses the
full transparent wordmark directly and is intentionally kept separate.
"""

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "inprofic logos"
BRAND = ROOT / "apps" / "core" / "static" / "core" / "brand"
PWA = ROOT / "apps" / "core" / "static" / "core" / "pwa"


def approved_source(source_name: str, canonical_name: str) -> Path:
    """Use the original artwork when present, otherwise its committed crop."""
    source = SOURCE / source_name
    return source if source.exists() else BRAND / canonical_name


SOURCE_ASSETS = {
    "inprofic-mark.png": approved_source("Inprofic 2-1.png", "inprofic-mark.png"),
    "inprofic-mark-on-dark.png": approved_source("Inprofic 4-1.png", "inprofic-mark-on-dark.png"),
    "inprofic-wordmark-on-dark.png": approved_source("Inprofic 4.png", "inprofic-wordmark-on-dark.png"),
    "inprofic-wordmark-on-light.png": approved_source("inprofic 3.png", "inprofic-wordmark-on-light.png"),
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


def pwa_icon(mark: Image.Image, size: int) -> Image.Image:
    """Render the approved N mark with transparent platform-safe padding."""
    return padded_mark(mark, size=size, fill_ratio=0.70)


def save_pwa_assets(images: dict[str, Image.Image]) -> None:
    PWA.mkdir(parents=True, exist_ok=True)
    mark = images["inprofic-mark.png"]
    outputs = {
        "icon-mark-180.png": pwa_icon(mark, 180),
        "icon-mark-192.png": pwa_icon(mark, 192),
        "icon-mark-512.png": pwa_icon(mark, 512),
        # Keep legacy filenames correct for already-installed manifests while
        # new manifests use the explicit icon-mark URLs below.
        "icon-180.png": pwa_icon(mark, 180),
        "icon-192.png": pwa_icon(mark, 192),
        "icon-512.png": pwa_icon(mark, 512),
        "icon-maskable-192.png": pwa_icon(mark, 192),
        "icon-maskable-512.png": pwa_icon(mark, 512),
    }
    for name, image in outputs.items():
        image.save(PWA / name, format="PNG", optimize=True)


if __name__ == "__main__":
    save_pwa_assets(save_brand_assets())
