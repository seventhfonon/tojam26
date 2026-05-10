"""PNG sequences for the Social dashboard theater heatmap (``app/assets/images``)."""

from __future__ import annotations

import logging
from pathlib import Path

from . import constants
from .environment_pixel_reference import luminance_grid_from_rgb_buffer_aspect_crop

log = logging.getLogger(__name__)

_GRID_CACHE: dict[tuple[str, int, int, int], list[list[float]]] = {}

# When no screening is active, hold this folder's first frame (static “idle” TV).
IDLE_MOVIE_PIXEL_SUBDIR = "barts_comet"

# Catalog ``movie_id`` → asset folder under ``app/assets/images``.
MOVIE_PIXEL_ASSET_SUBDIR_BY_ID: dict[str, str] = {
    "atomic_cafe": "atomic_cafe",
    "the_day_after": "day_after",
    "mad_max": "mad_max",
    "simpsons_s06e14_barts_comet": "barts_comet",
}


def asset_subdir_for_movie_id(movie_id: str) -> str | None:
    return MOVIE_PIXEL_ASSET_SUBDIR_BY_ID.get(movie_id)


def images_root() -> Path:
    return Path(__file__).resolve().parent / "assets" / "images"


def movie_frame_path(subdir: str, frame_index: int) -> Path:
    n = int(constants.SOCIAL_MOVIE_PIXEL_SEQUENCE_FRAME_COUNT)
    idx = frame_index % n if n > 0 else 0
    return images_root() / subdir / f"frame_{idx:02d}.png"


def movie_cells(subdir: str, frame_index: int, grid_cols: int, grid_rows: int) -> list[list[float]] | None:
    """Luminance grid for one frame, or ``None`` if assets are missing or Pillow fails."""
    if grid_cols < 1 or grid_rows < 1:
        return None
    n = int(constants.SOCIAL_MOVIE_PIXEL_SEQUENCE_FRAME_COUNT)
    if n < 1:
        return None

    idx = frame_index % n
    key = (subdir, idx, grid_cols, grid_rows)
    cached = _GRID_CACHE.get(key)
    if cached is not None:
        return [list(row) for row in cached]

    path = movie_frame_path(subdir, idx)
    if not path.is_file():
        return None

    try:
        from PIL import Image
    except ImportError:
        log.warning("Pillow not installed; cannot load movie heatmap frame")
        return None

    try:
        with Image.open(path) as im:
            rgb = im.convert("RGB")
            w, h = rgb.size
            if w < 1 or h < 1:
                return None
            cells = luminance_grid_from_rgb_buffer_aspect_crop(
                w, h, rgb.tobytes(), grid_cols, grid_rows
            )
    except OSError:
        log.warning("Movie heatmap frame unreadable at %s", path)
        return None

    _GRID_CACHE[key] = [list(row) for row in cells]
    return [list(row) for row in cells]
