import os
import platform
from pathlib import Path
import re
import threading
from gi.repository import GLib

FONT_SCAN_COMPLETED = threading.Event()
SYSTEM_FONTS = {}
FONT_FAMILY_LIST_SORTED = []

def _get_embedded_font_dir():
    try:
        base_dir = Path(__file__).resolve().parent
        fonts_dir = base_dir / "fonts"
        if fonts_dir.is_dir():
            print(f"DEBUG: Gömülü font klasörü bulundu: {fonts_dir}")
            return fonts_dir
    except Exception as e:
        print(f"HATA: Gömülü font klasörü bulunamadı: {e}")
    return None

def _get_font_dirs():
    font_dirs = []

    embedded_dir = _get_embedded_font_dir()
    if embedded_dir:
        font_dirs.append(embedded_dir)

    system = platform.system()
    if system == "Linux":
        system_font_paths = [
            "/usr/share/fonts",
            "/usr/local/share/fonts",
            os.path.expanduser("~/.local/share/fonts"),
            os.path.expanduser("~/.fonts"),
        ]
    elif system == "Windows":
        system_font_paths = [os.path.join(os.environ.get('SYSTEMROOT', 'C:\\Windows'), 'Fonts')]
    elif system == "Darwin":
        system_font_paths = ["/System/Library/Fonts/", "/Library/Fonts/", os.path.expanduser("~/Library/Fonts")]
    else:
        system_font_paths = []

    for d_str in system_font_paths:
        if d_str:
            d_path = Path(d_str)
            if d_path.is_dir() and d_path not in font_dirs:
                font_dirs.append(d_path)

    print(f"DEBUG: Taranacak tüm font klasörleri: {font_dirs}")
    return font_dirs

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

    family_name_candidate = re.sub(r'(MT|PS)$', '', family_name_candidate, flags=re.IGNORECASE).strip()

    family_name_candidate = re.sub(r"([a-z])([A-Z])", r"\1 \2", family_name_candidate)
    display_family_name = ' '.join(word.capitalize() for word in family_name_candidate.replace('-', ' ').replace('_', ' ').split())

    if not display_family_name:
        return None, None

    return display_family_name, detected_style_key

def scan_system_fonts_async(callback_on_done=None):
    def _scan():
        global SYSTEM_FONTS, FONT_FAMILY_LIST_SORTED, FONT_SCAN_COMPLETED
        print("Sistem ve gömülü font taraması başlıyor...")
        font_dirs = _get_font_dirs()
        temp_fonts_data = {}

        for directory in font_dirs:
            try:
                for item in list(directory.rglob('*.ttf')) + list(directory.rglob('*.otf')):
                    if item.is_file():
                        family_name, style_key = parse_font_name(item)
                        if family_name and style_key:
                            if family_name not in temp_fonts_data:
                                temp_fonts_data[family_name] = {}
                            if style_key not in temp_fonts_data[family_name]:
                                temp_fonts_data[family_name][style_key] = str(item)
            except Exception as e:
                print(f"Uyarı: Klasör taranırken hata oluştu {directory}: {e}")

        SYSTEM_FONTS = temp_fonts_data
        FONT_FAMILY_LIST_SORTED = sorted(SYSTEM_FONTS.keys())
        FONT_SCAN_COMPLETED.set()
        print(f"Font taraması tamamlandı. {len(FONT_FAMILY_LIST_SORTED)} font ailesi bulundu.")

        if callback_on_done:
            GLib.idle_add(callback_on_done)

    thread = threading.Thread(target=_scan, daemon=True)
    thread.start()

def find_specific_font_variant(family_name, is_bold=False, is_italic=False):
    if not FONT_SCAN_COMPLETED.is_set():
        print("Varyant bulmadan önce font taramasının bitmesi bekleniyor...")
        FONT_SCAN_COMPLETED.wait(timeout=5)
        if not FONT_SCAN_COMPLETED.is_set():
            print("HATA: Font taraması zaman aşımına uğradı.")
            return None

    normalized_family_name = family_name.replace(" ", "").lower() if family_name else ""
    
    found_family_key = None
    if family_name in SYSTEM_FONTS:
        found_family_key = family_name
    else:
        for key in SYSTEM_FONTS:
            normalized_key = key.replace(" ", "").lower()
            if normalized_key == normalized_family_name:
                found_family_key = key
                print(f"DEBUG: Found normalized font match: '{family_name}' -> '{key}'")
                break

    if found_family_key:
        family_variants = SYSTEM_FONTS[found_family_key]
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
    
    print(f"WARNING: Could not find any font file for family '{family_name}' (normalized: '{normalized_family_name}')")
    
    return None

UNICODE_FONT_PATH = None

def get_default_unicode_font_path():
    global UNICODE_FONT_PATH
    if UNICODE_FONT_PATH:
        return UNICODE_FONT_PATH

    if not FONT_SCAN_COMPLETED.is_set():
        print("Varsayılan unicode font için taramanın bitmesi bekleniyor...")
        FONT_SCAN_COMPLETED.wait(timeout=10)

    preferred_defaults = ["DejaVu Sans", "Noto Sans", "Liberation Sans", "Arial"]
    for family in preferred_defaults:
        path = find_specific_font_variant(family, False, False)
        if path:
            UNICODE_FONT_PATH = path
            print(f"Varsayılan Unicode fontu şuna ayarlandı: {UNICODE_FONT_PATH}")
            return UNICODE_FONT_PATH

    if FONT_FAMILY_LIST_SORTED and SYSTEM_FONTS:
        for family_name in FONT_FAMILY_LIST_SORTED:
            if "Regular" in SYSTEM_FONTS[family_name]:
                UNICODE_FONT_PATH = SYSTEM_FONTS[family_name]["Regular"]
                print(f"Varsayılan Unicode fontu (yedek) şuna ayarlandı: {UNICODE_FONT_PATH}")
                return UNICODE_FONT_PATH
        if FONT_FAMILY_LIST_SORTED:
            first_family = FONT_FAMILY_LIST_SORTED[0]
            if SYSTEM_FONTS[first_family]:
                 UNICODE_FONT_PATH = next(iter(SYSTEM_FONTS[first_family].values()))
                 print(f"Varsayılan Unicode fontu (mutlak yedek) şuna ayarlandı: {UNICODE_FONT_PATH}")
                 return UNICODE_FONT_PATH

    print("KRİTİK: Taramadan sonra hiçbir yedek Unicode fontu belirlenemedi.")
    return None


def normalize_color(color_val):
    if color_val is None:
        return (0.0, 0.0, 0.0)

    if isinstance(color_val, (int, float)):
        if isinstance(color_val, int) and color_val > 255:
             blue = (color_val & 255) / 255.0
             green = ((color_val >> 8) & 255) / 255.0
             red = ((color_val >> 16) & 255) / 255.0
             return (red, green, blue)
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
