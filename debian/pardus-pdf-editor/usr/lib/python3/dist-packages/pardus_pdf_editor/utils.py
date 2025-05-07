import os
import platform
from pathlib import Path
import numpy as np
import re # For regex matching

def _get_font_dirs():
    system = platform.system()
    font_dirs = []
    # ... (code to populate font_dirs based on OS - keep existing logic) ...
    if system == "Linux":
        font_dirs.extend([
            "/usr/share/fonts/truetype", # Search common parent dirs first
            "/usr/share/fonts/opentype",
            "/usr/local/share/fonts",
            os.path.expanduser("~/.local/share/fonts"),
            os.path.expanduser("~/.fonts"),
            # Specific dirs last as fallback
            "/usr/share/fonts/truetype/dejavu/",
            "/usr/share/fonts/truetype/noto/",
            "/usr/share/fonts/truetype/liberation/",
            "/usr/share/fonts/truetype/msttcorefonts/",
            "/usr/share/fonts/TTF/", # More generic
            "/usr/share/fonts/OTF/",
            "/usr/share/fonts/",
        ])
    elif system == "Windows":
        font_dirs.append(os.path.join(os.environ.get('SYSTEMROOT', 'C:\\Windows'), 'Fonts'))
    elif system == "Darwin": # macOS
        font_dirs.extend([
            "/System/Library/Fonts/",
            "/Library/Fonts/",
            os.path.expanduser("~/Library/Fonts"),
        ])
    # Filter out duplicates and non-existent dirs
    unique_dirs = []
    for d in font_dirs:
        if d and Path(d).is_dir() and d not in unique_dirs:
            unique_dirs.append(d)
    return [Path(d) for d in unique_dirs]

FONT_DIRS_CACHE = None # Cache found directories
def find_specific_font_variant(family_name, is_bold=False, is_italic=False):
    """
    Tries to find a specific font file matching family name and style.
    This is heuristic and relies on common naming conventions.
    Returns the path to the font file or None.
    """
    global FONT_DIRS_CACHE
    if FONT_DIRS_CACHE is None:
        FONT_DIRS_CACHE = _get_font_dirs()
        print(f"DEBUG: Font directories to search: {FONT_DIRS_CACHE}")

    if not family_name:
        return None

    # --- Define patterns for styles ---
    # Base name (allow spaces, case-insensitive matching)
    base_pattern = re.sub(r'\s+', '[ -_]*', family_name) # Replace space with optional space/hyphen/underscore
    
    # Style patterns (adapt these based on common font file names)
    if is_bold and is_italic:
        # Order matters sometimes (Bold Italic vs Italic Bold)
        style_patterns = [r'Bold[ -_]?Italic', r'Bold[ -_]?Oblique', r'BdI', r'Z'] # 'Z' is sometimes used for Bold Italic
    elif is_bold:
        style_patterns = [r'Bold', r'Bd', r'Heavy', r'Black', r'W6', r'W7', r'W8', r'W9', r'S_B'] # S_B for some Noto
    elif is_italic:
        style_patterns = [r'Italic', r'It', r'Oblique', r'Kursiv', r'I']
    else: # Regular
        style_patterns = [r'Regular', r'Roman', r'Normal', r'Medium', r'Book', r'Rg', r'W4', r'W5', r'^' + base_pattern + r'\.(ttf|otf)$'] # Match exact name + ext if no other style found

    # --- Search Logic ---
    print(f"DEBUG: Searching for font: Family='{family_name}', Bold={is_bold}, Italic={is_italic}")
    
    # Iterate through directories and patterns
    for directory in FONT_DIRS_CACHE:
        try:
            # Search for the specific style first
            for style_pattern in style_patterns:
                # Construct regex: base name, potential separator, style, common extensions
                # Case-insensitive search
                # Use word boundaries (\b) where appropriate? Maybe too restrictive.
                # Allow optional space/hyphen/underscore between base and style
                regex_pattern = re.compile(r'^' + base_pattern + r'[ -_]?' + style_pattern + r'\.(ttf|otf)$', re.IGNORECASE)
                
                # print(f"DEBUG: Trying pattern: {regex_pattern.pattern} in {directory}") # Verbose
                
                for item in directory.glob('*.*'): # Check all files first level
                    if item.is_file() and regex_pattern.match(item.name):
                        print(f"Found specific font: {item}")
                        return str(item)
                # Recursive search if needed (can be slow)
                # for item in directory.rglob('*.*'):
                #     if item.is_file() and regex_pattern.match(item.name):
                #         print(f"Found specific font (recursive): {item}")
                #         return str(item)

            # If regular style was requested and specific "Regular" etc. wasn't found,
            # try matching just the base name (already partially handled in style_patterns)
            if not is_bold and not is_italic:
                 regex_base_only = re.compile(r'^' + base_pattern + r'\.(ttf|otf)$', re.IGNORECASE)
                 for item in directory.glob('*.*'):
                     if item.is_file() and regex_base_only.match(item.name):
                         print(f"Found specific font (base name only): {item}")
                         return str(item)

        except Exception as e:
            print(f"Warning: Error searching in {directory}: {e}")
            continue # Ignore errors for specific directories

    print(f"Warning: Could not find specific variant for '{family_name}' B:{is_bold} I:{is_italic}")
    return None

# --- Fallback Font Function ---
def find_generic_fallback_font(preferred_fonts=["DejaVuSans.ttf", "NotoSans-Regular.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"]):
    """Tries to find a generic TTF font file for embedding as a fallback."""
    global FONT_DIRS_CACHE
    if FONT_DIRS_CACHE is None:
        FONT_DIRS_CACHE = _get_font_dirs()

    # Search preferred generic fonts first
    for font_name in preferred_fonts:
        for directory in FONT_DIRS_CACHE:
            try:
                # Simple glob search for preferred names
                potential_files = list(directory.glob(f'**/{font_name}')) # Recursive glob
                if potential_files:
                    found_font = potential_files[0]
                    print(f"Found preferred fallback font: {found_font}")
                    return str(found_font)
            except Exception as e:
                print(f"Warning: Error searching in {directory}: {e}")
                continue

    # If preferred not found, search more broadly for *any* .ttf/.otf (less reliable)
    print("Preferred fallback fonts not found, searching for any TTF/OTF...")
    for directory in FONT_DIRS_CACHE:
         try:
             for item in directory.rglob('*.[ot]tf'): # Search recursively for otf or ttf
                 if item.is_file():
                     print(f"Found generic fallback font: {item}")
                     return str(item)
         except Exception as e:
             print(f"Warning: Error searching in {directory}: {e}")
             continue

    print("CRITICAL: Could not find any suitable TTF/OTF font file for embedding.")
    return None

# --- Determine the main Unicode font path on startup ---
# Try finding DejaVu Sans first, then fall back to generic search
UNICODE_FONT_PATH = find_specific_font_variant("DejaVu Sans", False, False)
if not UNICODE_FONT_PATH:
    UNICODE_FONT_PATH = find_generic_fallback_font()

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