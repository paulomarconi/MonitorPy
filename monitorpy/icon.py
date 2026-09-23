"""Drawing the system tray icon."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ICON_FILENAME = "MonitorPy.ico"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

_BLACK = (17, 19, 23, 255)
_WHITE = (255, 255, 255, 255)


def _icon_search_paths():
    if getattr(sys, "frozen", False):
        yield Path(sys._MEIPASS) / ICON_FILENAME
        yield Path(sys.executable).parent / ICON_FILENAME
    else:
        yield Path(__file__).resolve().parent.parent / ICON_FILENAME


def draw_monitor_icon(size, supersample=4):
    """Draw the black-framed, white-outlined day/night monitor glyph.

    Rendered at `size * supersample` and downsampled, so edges stay crisp
    at small tray sizes instead of blurring from a single large master.
    """
    big = size * supersample
    scale = big / 256
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    dc = ImageDraw.Draw(img)

    x1, y1, x2, y2 = 20 * scale, 34 * scale, 236 * scale, 182 * scale
    frame_r = 22 * scale
    frame_w = max(1, round(6 * scale))
    dc.rounded_rectangle([x1, y1, x2, y2], radius=frame_r, fill=_BLACK)
    dc.rounded_rectangle([x1, y1, x2, y2], radius=frame_r, outline=_WHITE, width=frame_w)

    inset = 10 * scale
    ix1, iy1, ix2, iy2 = x1 + inset, y1 + inset, x2 - inset, y2 - inset
    dc.polygon([(ix1, iy1), (ix2, iy1), (ix2, iy2)], fill=_WHITE)
    dc.polygon([(ix1, iy1), (ix1, iy2), (ix2, iy2)], fill=_BLACK)
    dc.line([(ix1, iy1), (ix2, iy1), (ix2, iy2), (ix1, iy2), (ix1, iy1)],
            fill=_BLACK, width=max(1, round(4 * scale)))

    cx = big / 2
    stand_w = max(1, frame_w - round(2 * scale))
    dc.rounded_rectangle([cx - 8 * scale, y2, cx + 8 * scale, y2 + 20 * scale],
                          radius=4 * scale, fill=_BLACK, outline=_WHITE, width=stand_w)
    dc.rounded_rectangle([cx - 34 * scale, y2 + 18 * scale, cx + 34 * scale, y2 + 32 * scale],
                          radius=6 * scale, fill=_BLACK, outline=_WHITE, width=stand_w)

    return img.resize((size, size), Image.LANCZOS)


def build_ico(dest_path, sizes=ICO_SIZES):
    frames = [draw_monitor_icon(s) for s in sizes]
    frames[-1].save(dest_path, sizes=[(s, s) for s in sizes],
                     append_images=frames[:-1])


def load_tray_icon_image(size=64):
    for path in _icon_search_paths():
        if not path.is_file():
            continue
        try:
            im = Image.open(path)
            if (size, size) in im.info.get("sizes", set()):
                im.size = (size, size)
            im = im.convert("RGBA")
            if im.size != (size, size):
                im = im.resize((size, size), Image.LANCZOS)
            return im
        except OSError:
            continue
    return draw_monitor_icon(size)


def create_tray_icon_image(width, height, color1, color2):
    image = Image.new('RGB', (width, height), color1)
    dc = ImageDraw.Draw(image)
    # Draw monitor frame (white rectangle border)
    frame_x1, frame_y1 = 0, 0
    frame_x2, frame_y2 = width - 1, height - 1
    dc.rectangle([frame_x1, frame_y1, frame_x2, frame_y2], fill=None, outline='white', width=3)
    # Draw inner monitor bezel
    bezel_x1, bezel_y1 = 2, 2
    bezel_x2, bezel_y2 = width - 3, height - 3
    dc.rectangle([bezel_x1, bezel_y1, bezel_x2, bezel_y2], fill=None, outline='white', width=1)
    # Draw diagonally split rectangle - left side white, right side black
    x1, y1 = 4, 4
    x2, y2 = width - 5, height - 5
    # Left/top-left triangle: white
    dc.polygon([(x1, y1), (x2, y1), (x1, y2)], fill=color2)
    # Right/bottom-right triangle: black
    dc.polygon([(x2, y1), (x2, y2), (x1, y2)], fill=color1)
    # Draw outline
    dc.rectangle([x1, y1, x2, y2], fill=None, outline='white', width=1)
    return image
