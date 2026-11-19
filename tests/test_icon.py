"""Tray icon drawing and loading."""
from monitorpy import icon
from monitorpy.icon import draw_monitor_icon, load_tray_icon_image


def test_drawn_icon_has_the_requested_size_and_transparency():
    img = draw_monitor_icon(64)
    assert img.size == (64, 64)
    assert img.mode == "RGBA"
    assert img.getpixel((0, 0))[3] == 0          # corner outside the monitor is transparent
    assert img.getpixel((32, 20))[3] == 255      # the screen area is opaque


def test_tray_icon_loads_at_the_requested_size():
    img = load_tray_icon_image(32)
    assert img.size == (32, 32) and img.mode == "RGBA"


def test_tray_icon_falls_back_to_drawing_when_the_ico_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(icon, "_icon_search_paths", lambda: iter([tmp_path / "missing.ico"]))
    img = load_tray_icon_image(48)
    assert img.size == (48, 48) and img.mode == "RGBA"
