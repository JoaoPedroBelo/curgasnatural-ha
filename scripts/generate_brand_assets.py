"""Rasterise the CUR Gás Natural brand assets from the official SVG.

Source of truth is `custom_components/curgasnatural/brand/cur-logo.svg`, taken
verbatim from the customer portal
(https://portal.curgasnatural.pt/curpath/assets/logo/cur-logo.svg). The logo is
used nominatively, to identify the service this integration talks to — the same
basis on which the `home-assistant/brands` repository carries third-party logos.

Outputs follow the Home Assistant brand spec:

    icon.png      256x256        square, the burst mark alone
    icon@2x.png   512x512
    logo.png      <=512 x <=256  the full horizontal lockup
    logo@2x.png   <=1024 x <=512

Run with `make brand` (needs Pillow + cairosvg; cairosvg needs the cairo library,
`brew install cairo` on macOS).

This script lives outside `custom_components/` on purpose: the HACS release zip
only packs the component directory, so users never download it.
"""

from __future__ import annotations

import io
import pathlib

import cairosvg
from PIL import Image

BRAND = (
    pathlib.Path(__file__).resolve().parent.parent
    / "custom_components/curgasnatural/brand"
)
SVG = BRAND / "cur-logo.svg"

# The SVG declares viewBox "0 0 70 48" with a 69.8182-wide clip path.
VIEWBOX_WIDTH = 69.8182

# The burst mark, plus the "C" it wraps around, occupies the left of the lockup; the
# rest is the "CUR GÁS NATURAL" wordmark. Cropping by x here, rather than surgically
# extracting paths, keeps this robust against the SVG being re-exported.
MARK_WIDTH_UNITS = 25.6

# Breathing room around the mark inside the square icon canvas.
ICON_MARGIN = 0.08

# Render well above the target size, then downsample: cairo's own scaling of the
# thin burst rays is harsher than Lanczos.
RENDER_WIDTH = 4096


def render_svg(width: int) -> Image.Image:
    """Render the source SVG to an RGBA image of the given width."""
    png = cairosvg.svg2png(url=str(SVG), output_width=width)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def fit_within(img: Image.Image, max_width: int, max_height: int) -> Image.Image:
    """Downscale ``img`` to fit the box, preserving aspect ratio."""
    scale = min(max_width / img.width, max_height / img.height)
    return img.resize(
        (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
        Image.LANCZOS,
    )


def trim(img: Image.Image) -> Image.Image:
    """Drop fully transparent borders."""
    bbox = img.getbbox()
    return img.crop(bbox) if bbox else img


def make_logo(max_width: int, max_height: int) -> Image.Image:
    """The full horizontal lockup, trimmed of transparent padding."""
    return fit_within(trim(render_svg(RENDER_WIDTH)), max_width, max_height)


def make_icon(size: int) -> Image.Image:
    """The burst mark alone, centred on a transparent square canvas."""
    full = render_svg(RENDER_WIDTH)
    cut = round(full.width * (MARK_WIDTH_UNITS / VIEWBOX_WIDTH))
    mark = trim(full.crop((0, 0, cut, full.height)))

    inner = round(size * (1 - 2 * ICON_MARGIN))
    mark = fit_within(mark, inner, inner)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(mark, ((size - mark.width) // 2, (size - mark.height) // 2), mark)
    return canvas


def main() -> None:
    if not SVG.is_file():
        raise SystemExit(f"missing source SVG: {SVG}")

    assets = (
        ("icon.png", make_icon(256)),
        ("icon@2x.png", make_icon(512)),
        ("logo.png", make_logo(512, 256)),
        ("logo@2x.png", make_logo(1024, 512)),
    )
    for name, img in assets:
        path = BRAND / name
        img.save(path, "PNG", optimize=True)
        size = path.stat().st_size / 1024
        print(f"  {name:14} {img.width}x{img.height:<5} {size:6.1f} KB")


if __name__ == "__main__":
    main()
