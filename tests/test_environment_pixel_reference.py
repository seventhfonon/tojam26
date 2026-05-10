"""Tests for reference-image luminance sampling onto the environment pixel grid."""

from __future__ import annotations

import pytest

import app.environment_pixel_reference as epr


def test_apply_uniform_tick_noise_zero_amplitude():
    cells = [[0.2, 0.8], [0.0, 1.0]]
    epr.apply_uniform_tick_noise(cells, 0.0)
    assert cells == [[0.2, 0.8], [0.0, 1.0]]


def test_apply_uniform_tick_noise_shifts_and_clamps(monkeypatch):
    monkeypatch.setattr(epr.random, "uniform", lambda _a, _b: 0.04)
    cells = [[0.5]]
    epr.apply_uniform_tick_noise(cells, 0.1)
    assert cells[0][0] == pytest.approx(0.54)

    monkeypatch.setattr(epr.random, "uniform", lambda _a, _b: 0.5)
    cells2 = [[0.95]]
    epr.apply_uniform_tick_noise(cells2, 1.0)
    assert cells2[0][0] == 1.0

    monkeypatch.setattr(epr.random, "uniform", lambda _a, _b: -0.5)
    cells3 = [[0.05]]
    epr.apply_uniform_tick_noise(cells3, 1.0)
    assert cells3[0][0] == 0.0


def test_environment_pixel_reference_constant_color(tmp_path, monkeypatch):
    pytest.importorskip("PIL", reason="reference image sampling uses Pillow")
    from PIL import Image

    png = tmp_path / epr.REFERENCE_FILENAME
    Image.new("RGB", (20, 20), (255, 0, 0)).save(png)
    monkeypatch.setattr(epr, "_reference_png_path", lambda: png)
    epr._REFERENCE_RGB = None
    epr._GRID_CACHE.clear()

    grid = epr.environment_pixel_cells_from_reference_image(4, 4)
    assert grid is not None
    expected = round(0.299 * 255 / 255.0, 6)
    for row in grid:
        for v in row:
            assert abs(v - expected) < 1e-5


def test_environment_pixel_reference_corners_black_centre_white(tmp_path, monkeypatch):
    """2×2 grid on 2×2 image: TL/TR/BL black, BR white."""
    pytest.importorskip("PIL", reason="reference image sampling uses Pillow")
    from PIL import Image

    png = tmp_path / epr.REFERENCE_FILENAME
    px = Image.new("RGB", (2, 2))
    px.putpixel((0, 0), (0, 0, 0))
    px.putpixel((1, 0), (0, 0, 0))
    px.putpixel((0, 1), (0, 0, 0))
    px.putpixel((1, 1), (255, 255, 255))
    px.save(png)
    monkeypatch.setattr(epr, "_reference_png_path", lambda: png)
    epr._REFERENCE_RGB = None
    epr._GRID_CACHE.clear()

    grid = epr.environment_pixel_cells_from_reference_image(2, 2)
    assert grid == [[0.0, 0.0], [0.0, 1.0]]
