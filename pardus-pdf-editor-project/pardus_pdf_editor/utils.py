import os
import platform
from pathlib import Path
import numpy as np

def normalize_color(color_val):
    """Normalizes color to a tuple of floats (r, g, b) between 0.0 and 1.0."""
    if isinstance(color_val, (int, float)):
        val = float(color_val)
        if val > 1.0:
             val = val / 255.0
        val = max(0.0, min(1.0, val))
        return (val, val, val)
    elif isinstance(color_val, (list, tuple)) and len(color_val) >= 3:
        rgb = list(color_val[:3])
        for i in range(3):
            if isinstance(rgb[i], (int, float)):
                val = float(rgb[i])
                if val > 1.0:
                    val = val / 255.0
                rgb[i] = max(0.0, min(1.0, val))
            else:
                rgb[i] = 0.0
        return tuple(rgb)
    return (0.0, 0.0, 0.0)

def find_font_file(preferred_fonts=["DejaVuSans.ttf", "NotoSans-Regular.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"]):
    """Tries to find a suitable TTF font file for embedding."""
    system = platform.system()
    font_dirs = []

    if system == "Linux":
        font_dirs.extend([
            "/usr/share/fonts/truetype/dejavu/",
            "/usr/share/fonts/truetype/noto/",
            "/usr/share/fonts/truetype/liberation/",
            "/usr/share/fonts/truetype/msttcorefonts/",
            "/usr/share/fonts/TTF/",
            "/usr/share/fonts/",
            os.path.expanduser("~/.local/share/fonts"),
            os.path.expanduser("~/.fonts"),
        ])
    elif system == "Windows":
        font_dirs.append(os.path.join(os.environ.get('SYSTEMROOT', 'C:\\Windows'), 'Fonts'))
    elif system == "Darwin": # macOS
        font_dirs.extend([
            "/System/Library/Fonts/",
            "/Library/Fonts/",
            os.path.expanduser("~/Library/Fonts"),
        ])

    # Add more generic paths
    font_dirs = [Path(d) for d in font_dirs if d and Path(d).is_dir()]

    found_font = None

    # Search preferred fonts first
    for font_name in preferred_fonts:
        for directory in font_dirs:
            try:
                # Use rglob for recursive search within likely directories
                potential_files = list(directory.rglob(font_name))
                if potential_files:
                    found_font = potential_files[0]
                    print(f"Found preferred font: {found_font}")
                    return str(found_font)
            except Exception as e:
                print(f"Error searching in {directory}: {e}") # Permissions etc.
                continue

    # If preferred not found, search more broadly for any .ttf (less reliable)
    print("Preferred fonts not found, searching for any TTF...")
    for directory in font_dirs:
         try:
             for item in directory.rglob("*.ttf"):
                 if item.is_file():
                     print(f"Found fallback font: {item}")
                     return str(item)
         except Exception as e:
             print(f"Error searching in {directory}: {e}")
             continue

    print("Warning: Could not find a suitable TTF font file for embedding Unicode text.")
    return None

# Pre-find the font on startup
UNICODE_FONT_PATH = find_font_file()