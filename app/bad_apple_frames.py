"""PNG sequence for the environment heatmap Bad Apple clip (``app/assets/images/bad_apple``)."""

from __future__ import annotations

import logging
from pathlib import Path

from . import constants
from .environment_pixel_reference import luminance_grid_from_rgb_buffer

log = logging.getLogger(__name__)

_GRID_CACHE: dict[tuple[int, int, int], list[list[float]]] = {}


def bad_apple_frames_dir() -> Path:
    return Path(__file__).resolve().parent / "assets" / "images" / "bad_apple"


def bad_apple_frame_path(frame_index: int) -> Path:
    n = int(constants.BAD_APPLE_FRAME_COUNT)
    idx = frame_index % n if n > 0 else 0
    return bad_apple_frames_dir() / f"frame_{idx:02d}.png"


def bad_apple_frames_ready() -> bool:
    """True when every ``frame_00.png`` … ``frame_{N-1}.png`` exists."""
    n = int(constants.BAD_APPLE_FRAME_COUNT)
    if n < 1:
        return False
    d = bad_apple_frames_dir()
    return all((d / f"frame_{i:02d}.png").is_file() for i in range(n))


def bad_apple_cells(frame_index: int, grid_cols: int, grid_rows: int) -> list[list[float]] | None:
    """Luminance grid for one frame, or ``None`` if assets are missing or Pillow fails."""
    if grid_cols < 1 or grid_rows < 1:
        return None
    if not bad_apple_frames_ready():
        return None

    idx = frame_index % int(constants.BAD_APPLE_FRAME_COUNT)
    key = (idx, grid_cols, grid_rows)
    cached = _GRID_CACHE.get(key)
    if cached is not None:
        return [list(row) for row in cached]

    path = bad_apple_frame_path(idx)
    try:
        from PIL import Image
    except ImportError:
        log.warning("Pillow not installed; cannot load Bad Apple frame")
        return None

    if not path.is_file():
        return None

    try:
        with Image.open(path) as im:
            rgb = im.convert("RGB")
            w, h = rgb.size
            if w < 1 or h < 1:
                return None
            cells = luminance_grid_from_rgb_buffer(w, h, rgb.tobytes(), grid_cols, grid_rows)
    except OSError:
        log.warning("Bad Apple frame unreadable at %s", path)
        return None

    _GRID_CACHE[key] = [list(row) for row in cells]
    return [list(row) for row in cells]
