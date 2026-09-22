"""Drawing the system tray icon."""
from PIL import Image, ImageDraw


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
