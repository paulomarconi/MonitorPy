"""Tray icon drawing."""
from monitorpy.icon import create_tray_icon_image


def test_icon_has_the_requested_size_and_mode():
    img = create_tray_icon_image(64, 64, 'black', 'white')
    assert img.size == (64, 64)
    assert img.mode == "RGB"


def test_icon_corner_is_the_first_color_and_border_is_white():
    img = create_tray_icon_image(64, 64, 'black', 'white')
    assert img.getpixel((0, 0)) == (255, 255, 255)   # the outer border is always drawn white
    assert img.getpixel((32, 32)) in ((0, 0, 0), (255, 255, 255))  # inside the split rectangle


def test_icon_works_at_a_non_square_non_default_size():
    img = create_tray_icon_image(32, 16, 'red', 'blue')
    assert img.size == (32, 16)
