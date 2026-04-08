#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from PIL import Image, ImageDraw
from pathlib import Path

THUMBNAILS_DIR = Path('/var/www/photo_gal/thumbnails')
THUMBNAILS_DIR.mkdir(exist_ok=True)

def create_play_icon(size):
    """Create a circular play button with triangle"""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # White circle
    draw.ellipse([0, 0, size-1, size-1], fill=(255, 255, 255, 240))
    # Dark triangle
    padding = size // 3
    tri_h = size - padding * 2
    points = [
        (padding + tri_h // 5, padding),
        (padding + tri_h // 5, size - padding),
        (padding + tri_h, size // 2)
    ]
    draw.polygon(points, fill=(80, 60, 40))
    return img

def generate_video_thumbnail(filename):
    thumb_path = THUMBNAILS_DIR / filename
    W, H = 300, 400  # Match card 3/4 aspect ratio
    icon_size = 80

    # Create warm solid background (amber/brown)
    img = Image.new('RGB', (W, H), (140, 100, 60))

    # Create play icon
    icon = create_play_icon(icon_size)

    # Center the icon
    paste_x = W // 2 - icon_size // 2
    paste_y = H // 2 - icon_size // 2
    img.paste(icon, (paste_x, paste_y), icon)

    img.save(thumb_path, 'JPEG', quality=85)
    print(f'Generated: {filename}')

if __name__ == '__main__':
    import sys
    for arg in sys.argv[1:]:
        generate_video_thumbnail(arg)