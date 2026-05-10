"""Sample a fixed reference image onto the environment dashboard raster grid."""

from __future__ import annotations

import logging
import random
from pathlib import Path

log = logging.getLogger(__name__)

_REFERENCE_RGB: tuple[int, int, bytes] | None = None
_GRID_CACHE: dict[tuple[int, int], list[list[float]]] = {}

REFERENCE_FILENAME = "environment_pixel_reference.png"


def _reference_png_path() -> Path:
    return Path(__file__).resolve().parent / "static" / REFERENCE_FILENAME


def _load_reference_rgb() -> tuple[int, int, bytes] | None:
    global _REFERENCE_RGB
    if _REFERENCE_RGB is not None:
        return _REFERENCE_RGB
    path = _reference_png_path()
    if not path.is_file():
        log.warning("environment pixel reference missing at %s", path)
        return None
    try:
        from PIL import Image
    except ImportError:
        log.warning("Pillow not installed; cannot load environment pixel reference image")
        return None

    with Image.open(path) as im:
        rgb = im.convert("RGB")
        w, h = rgb.size
        if w < 1 or h < 1:
            return None
        _REFERENCE_RGB = (w, h, rgb.tobytes())
    return _REFERENCE_RGB


def _luminance_01(r: int, g: int, b: int) -> float:
    """sRGB-ish luminance in ``[0, 1]`` (gamma-encoded weights)."""
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def _pixel_at(rgb_bytes: bytes, width: int, x: int, y: int) -> tuple[int, int, int]:
    i = (y * width + x) * 3
    return rgb_bytes[i], rgb_bytes[i + 1], rgb_bytes[i + 2]


def luminance_grid_from_rgb_buffer(
    img_w: int, img_h: int, rgb_bytes: bytes, grid_cols: int, grid_rows: int
) -> list[list[float]]:
    """Row-major luminance samples at grid-cell centres from packed RGB bytes."""
    cells: list[list[float]] = []
    for r in range(grid_rows):
        row: list[float] = []
        fy = (r + 0.5) / grid_rows * img_h
        sy = min(img_h - 1, max(0, int(fy)))
        for c in range(grid_cols):
            fx = (c + 0.5) / grid_cols * img_w
            sx = min(img_w - 1, max(0, int(fx)))
            lum = _luminance_01(*_pixel_at(rgb_bytes, img_w, sx, sy))
            row.append(round(lum, 6))
        cells.append(row)
    return cells


def luminance_grid_from_rgb_buffer_aspect_crop(
    img_w: int,
    img_h: int,
    rgb_bytes: bytes,
    grid_cols: int,
    grid_rows: int,
) -> list[list[float]]:
    """Row-major luminance samples over a **center crop** whose aspect matches the grid.

    Same cell layout as :func:`luminance_grid_from_rgb_buffer`, but the sampled region is a
    centered rectangle with aspect ratio ``grid_cols : grid_rows`` (typically square), using the
    largest such rectangle contained in the image — “cover” mapping without non-uniform stretch.
    """
    cells: list[list[float]] = []
    if grid_cols < 1 or grid_rows < 1 or img_w < 1 or img_h < 1:
        return cells

    target_ar = float(grid_cols) / float(grid_rows)
    img_ar = float(img_w) / float(img_h)

    if img_ar > target_ar + 1e-9:
        crop_h = img_h
        crop_w = max(1, min(img_w, int(round(img_h * target_ar))))
        x0 = max(0, (img_w - crop_w) // 2)
        y0 = 0
    elif img_ar < target_ar - 1e-9:
        crop_w = img_w
        crop_h = max(1, min(img_h, int(round(img_w / target_ar))))
        x0 = 0
        y0 = max(0, (img_h - crop_h) // 2)
    else:
        crop_w, crop_h = img_w, img_h
        x0, y0 = 0, 0

    for r in range(grid_rows):
        row: list[float] = []
        fy = y0 + (r + 0.5) / grid_rows * crop_h
        sy = min(img_h - 1, max(0, int(fy)))
        for c in range(grid_cols):
            fx = x0 + (c + 0.5) / grid_cols * crop_w
            sx = min(img_w - 1, max(0, int(fx)))
            lum = _luminance_01(*_pixel_at(rgb_bytes, img_w, sx, sy))
            row.append(round(lum, 6))
        cells.append(row)
    return cells


def environment_pixel_cells_from_reference_image(grid_cols: int, grid_rows: int) -> list[list[float]] | None:
    """Return row-major ``[row][col]`` luminance samples at grid-cell centres, or ``None`` if unavailable."""
    if grid_cols < 1 or grid_rows < 1:
        return None

    key = (grid_cols, grid_rows)
    cached = _GRID_CACHE.get(key)
    if cached is not None:
        return [list(row) for row in cached]

    packed = _load_reference_rgb()
    if packed is None:
        return None

    img_w, img_h, rgb_bytes = packed
    cells = luminance_grid_from_rgb_buffer(img_w, img_h, rgb_bytes, grid_cols, grid_rows)

    _GRID_CACHE[key] = [list(x) for x in cells]
    return cells


def apply_uniform_tick_noise(cells: list[list[float]], half_range: float) -> None:
    """Add independent uniform ``[-half_range, half_range]`` to each cell; clamp to ``[0, 1]``.

    Mutates ``cells`` in place (expected to already be a mutable grid copy).
    """
    if half_range <= 0:
        return
    hr = float(half_range)
    for row in cells:
        for i, v in enumerate(row):
            row[i] = round(max(0.0, min(1.0, float(v) + random.uniform(-hr, hr))), 6)
