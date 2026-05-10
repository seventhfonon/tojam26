"""BGM asset route (no full ``create_app`` — avoids SQLite vs migration mismatch)."""

from __future__ import annotations

from pathlib import Path

from flask import Flask


def test_ode_to_joy_mp3_exists_on_disk():
    root = Path(__file__).resolve().parents[1]
    p = root / "app" / "assets" / "audio" / "JazzWithAmbience.ogg"
    assert p.is_file()
    assert p.stat().st_size > 10_000


def test_serve_game_audio_returns_mp3():
    from app.routes import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    c = app.test_client()
    r = c.get("/assets/audio/JazzWithAmbience.ogg")
    assert r.status_code == 200
    assert r.mimetype == "audio/mpeg"
    assert len(r.data) > 10_000


def test_serve_game_audio_rejects_bad_suffix():
    from app.routes import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    c = app.test_client()
    assert c.get("/assets/audio/wrong.exe").status_code == 404


def test_serve_game_audio_missing_file():
    from app.routes import bp

    app = Flask(__name__)
    app.register_blueprint(bp)
    c = app.test_client()
    assert c.get("/assets/audio/does_not_exist.mp3").status_code == 404
