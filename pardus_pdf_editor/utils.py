import os
import platform
from pathlib import Path
import re
import threading
from gi.repository import GLib

FONT_SCAN_COMPLETED = threading.Event()
SYSTEM_FONTS = {} 
FONT_FAMILY_LIST_SORTED = []

def _get_font_dirs():
    system = platform.system()
    font_dirs = []
    if system == "Linux":
        font_dirs.extend([
            "/usr/share/fonts/truetype", "/usr/share/fonts/opentype",
            "/usr/local/share/fonts",
            os.path.expanduser("~/.local/share/fonts"),
            os.path.expanduser("~/.fonts"),
            "/usr/share/fonts/truetype/dejavu/", "/usr/share/fonts/truetype/noto/",
            "/usr/share/fonts/truetype/liberation/", "/usr/share/fonts/truetype/msttcorefonts/",
            "/usr/share/fonts/TTF/", "/usr/share/fonts/OTF/", "/usr/share/fonts/",
        ])
    elif system == "Windows":
        font_dirs.append(os.path.join(os.environ.get('SYSTEMROOT', 'C:\\Windows'), 'Fonts'))
    elif system == "Darwin":
        font_dirs.extend(["/System/Library/Fonts/", "/Library/Fonts/", os.path.expanduser("~/Library/Fonts")])
    
    unique_dirs = []
    for d_str in font_dirs:
        if d_str:
            d_path = Path(d_str)
            if d_path.is_dir() and d_path not in unique_dirs:
                unique_dirs.append(d_path)
    return unique_dirs

def parse_font_name(filename):
    name_part = filename.stem
    
    styles_map = {
        "BoldItalic": [r"BoldItalic", r"BoldOblique", r"BdI", r"Z", r"BI"],
        "Bold":       [r"Bold", r"Bd", r"Heavy", r"Black", r"DemiBold", r"SmBd", r"S_B"],
        "Italic":     [r"Italic", r"It", r"Oblique", r"Kursiv", r"I", r"Obl"],
        "Regular":    [r"Regular", r"Roman", r"Normal", r"Medium", r"Book", r"Rg", r"W4", r"W5", r"Text"]
    }

    detected_style_key = "Regular"
    cleaned_name = name_part

    for style_key, patterns in styles_map.items():
        for pattern in patterns:
            match = re.search(r"([_ -]?" + pattern + r")$", cleaned_name, re.IGNORECASE)
            if match:
                detected_style_key = style_key
                cleaned_name = cleaned_name[:match.start()]
                break
        if detected_style_key != "Regular" and style_key != "Regular": 
            break 

    family_name_candidate = re.sub(r"[ _-]+$", "", cleaned_name)
    if not family_name_candidate: 
        family_name_candidate = name_part

    family_name_candidate = re.sub(r'(MT|PS)$', '', family_name_candidate)
    
    family_name_candidate = re.sub(r"([a-z])([A-Z])", r"\1 \2", family_name_candidate)
    display_family_name = ' '.join(word.capitalize() for word in family_name_candidate.replace('-', ' ').replace('_', ' ').split())

    if not display_family_name:
        return None, None

    return display_family_name, detected_style_key

def scan_system_fonts_async(callback_on_done=None):
    def _scan():
        global SYSTEM_FONTS, FONT_FAMILY_LIST_SORTED, FONT_SCAN_COMPLETED
        print("Starting system font scan...")
        font_dirs = _get_font_dirs()
        temp_fonts_data = {}

        for directory in font_dirs:
            try:
                for item in directory.rglob('*.[ot]tf'):
                    if item.is_file():
                        family_name, style_key = parse_font_name(item)
                        if family_name and style_key:
                            if family_name not in temp_fonts_data:
                                temp_fonts_data[family_name] = {}
                            if style_key not in temp_fonts_data[family_name]:
                                temp_fonts_data[family_name][style_key] = str(item)
            except Exception as e:
                print(f"Warning: Error scanning directory {directory}: {e}")
        
        SYSTEM_FONTS = temp_fonts_data
        FONT_FAMILY_LIST_SORTED = sorted(SYSTEM_FONTS.keys())
        FONT_SCAN_COMPLETED.set()
        print(f"Font scan completed. Found {len(FONT_FAMILY_LIST_SORTED)} families.")

        if callback_on_done:
            GLib.idle_add(callback_on_done)

    thread = threading.Thread(target=_scan, daemon=True)
    thread.start()

def find_specific_font_variant(family_name, is_bold=False, is_italic=False):
    if not FONT_SCAN_COMPLETED.is_set():
        print("Waiting for font scan to complete before finding variant...")
        FONT_SCAN_COMPLETED.wait(timeout=10)
        if not FONT_SCAN_COMPLETED.is_set():
            print("Error: Font scan timed out.")
            return None

    if family_name in SYSTEM_FONTS:
        family_variants = SYSTEM_FONTS[family_name]
        if is_bold and is_italic and "BoldItalic" in family_variants:
            return family_variants["BoldItalic"]
        if is_bold and "Bold" in family_variants:
            return family_variants["Bold"]
        if is_italic and "Italic" in family_variants:
            return family_variants["Italic"]
        if "Regular" in family_variants:
            return family_variants["Regular"]
        if family_variants:
            return next(iter(family_variants.values()))
    return None

FONT_DIRS_CACHE = None
def find_specific_font_variant(family_name, is_bold=False, is_italic=False):
    global FONT_DIRS_CACHE
    if FONT_DIRS_CACHE is None:
        FONT_DIRS_CACHE = _get_font_dirs()
        print(f"DEBUG: Font directories to search: {FONT_DIRS_CACHE}")

    if not family_name:
        return None

    base_pattern = re.sub(r'\s+', '[ -_]*', family_name)
    
    if is_bold and is_italic:
        style_patterns = [r'Bold[ -_]?Italic', r'Bold[ -_]?Oblique', r'BdI', r'Z']
    elif is_bold:
        style_patterns = [r'Bold', r'Bd', r'Heavy', r'Black', r'W6', r'W7', r'W8', r'W9', r'S_B']
    elif is_italic:
        style_patterns = [r'Italic', r'It', r'Oblique', r'Kursiv', r'I']
    else:
        style_patterns = [r'Regular', r'Roman', r'Normal', r'Medium', r'Book', r'Rg', r'W4', r'W5', r'^' + base_pattern + r'\.(ttf|otf)$']

    print(f"DEBUG: Searching for font: Family='{family_name}', Bold={is_bold}, Italic={is_italic}")
    
    for directory in FONT_DIRS_CACHE:
        try:
            for style_pattern in style_patterns:
                regex_pattern = re.compile(r'^' + base_pattern + r'[ -_]?' + style_pattern + r'\.(ttf|otf)$', re.IGNORECASE)
                
                for item in directory.glob('*.*'):
                    if item.is_file() and regex_pattern.match(item.name):
                        print(f"Found specific font: {item}")
                        return str(item)
            if not is_bold and not is_italic:
                 regex_base_only = re.compile(r'^' + base_pattern + r'\.(ttf|otf)$', re.IGNORECASE)
                 for item in directory.glob('*.*'):
                     if item.is_file() and regex_base_only.match(item.name):
                         print(f"Found specific font (base name only): {item}")
                         return str(item)

        except Exception as e:
            print(f"Warning: Error searching in {directory}: {e}")
            continue 

    print(f"Warning: Could not find specific variant for '{family_name}' B:{is_bold} I:{is_italic}")
    return None

def find_generic_fallback_font(preferred_fonts=["DejaVuSans.ttf", "NotoSans-Regular.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"]):
    global FONT_DIRS_CACHE
    if FONT_DIRS_CACHE is None:
        FONT_DIRS_CACHE = _get_font_dirs()

    for font_name in preferred_fonts:
        for directory in FONT_DIRS_CACHE:
            try:
                potential_files = list(directory.glob(f'**/{font_name}'))
                if potential_files:
                    found_font = potential_files[0]
                    print(f"Found preferred fallback font: {found_font}")
                    return str(found_font)
            except Exception as e:
                print(f"Warning: Error searching in {directory}: {e}")
                continue

    print("Preferred fallback fonts not found, searching for any TTF/OTF...")
    for directory in FONT_DIRS_CACHE:
         try:
             for item in directory.rglob('*.[ot]tf'):
                 if item.is_file():
                     print(f"Found generic fallback font: {item}")
                     return str(item)
         except Exception as e:
             print(f"Warning: Error searching in {directory}: {e}")
             continue

    print("CRITICAL: Could not find any suitable TTF/OTF font file for embedding.")
    return None

UNICODE_FONT_PATH = None

def get_default_unicode_font_path():
    global UNICODE_FONT_PATH
    if UNICODE_FONT_PATH:
        return UNICODE_FONT_PATH

    if not FONT_SCAN_COMPLETED.is_set():
        print("Waiting for font scan to complete for default unicode font...")
        FONT_SCAN_COMPLETED.wait(timeout=10)
    
    preferred_defaults = ["DejaVu Sans", "Noto Sans", "Liberation Sans", "Arial"]
    for family in preferred_defaults:
        path = find_specific_font_variant(family, False, False)
        if path:
            UNICODE_FONT_PATH = path
            print(f"Default Unicode font set to: {UNICODE_FONT_PATH}")
            return UNICODE_FONT_PATH
            
    if FONT_FAMILY_LIST_SORTED and SYSTEM_FONTS:
        for family_name in FONT_FAMILY_LIST_SORTED:
            if "Regular" in SYSTEM_FONTS[family_name]:
                UNICODE_FONT_PATH = SYSTEM_FONTS[family_name]["Regular"]
                print(f"Default Unicode font (fallback) set to: {UNICODE_FONT_PATH}")
                return UNICODE_FONT_PATH
        if FONT_FAMILY_LIST_SORTED:
            first_family = FONT_FAMILY_LIST_SORTED[0]
            if SYSTEM_FONTS[first_family]:
                 UNICODE_FONT_PATH = next(iter(SYSTEM_FONTS[first_family].values()))
                 print(f"Default Unicode font (absolute fallback) set to: {UNICODE_FONT_PATH}")
                 return UNICODE_FONT_PATH
                 
    print("CRITICAL: No fallback Unicode font could be determined after scan.")
    return None

def normalize_color(color_val):
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