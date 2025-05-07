from gi.repository import GObject, GdkPixbuf
from .utils import normalize_color
import re

# PyMuPDF Font Flags
FLAG_ITALIC = 1 << 1
FLAG_BOLD = 1 << 4

# Basic mapping from common base names found in PDFs to Base 14 roots
BASE14_FALLBACK_MAP = {
    'helvetica': 'helv', 'arial': 'helv', 'sans': 'helv',
    'times': 'timr', 'timesnewroman': 'timr', 'serif': 'timr',
    'courier': 'cour', 'couriernew': 'cour', 'mono': 'cour', 'monospace': 'cour'
}

class EditableText:
    def __init__(self, x, y, text, font_size=11, font_family="Helvetica", # Default changed
                 color=(0, 0, 0), span_data=None, is_new=False, baseline=None):
        self.x = x; self.y = y; self.text = text; self.original_text = text if not is_new else ""
        self.font_size = font_size; self.is_new = is_new

        # --- Font Info Extraction ---
        flags = span_data.get('flags', 0) if span_data else 0
        pdf_font_name = span_data.get('font', 'Helvetica') if span_data else 'Helvetica'

        self.font_family_original = pdf_font_name # Store the original PDF font name

        # Determine bold/italic from flags
        self.is_bold = bool(flags & FLAG_BOLD)
        self.is_italic = bool(flags & FLAG_ITALIC)

        # --- Determine Base Family Name (for searching) ---
        # Try to extract a cleaner base name like 'DejaVu Sans', 'Times New Roman', 'Arial'
        cleaned_name = re.sub(r'^[A-Z]{6}\+', '', pdf_font_name) # Remove prefix like 'ABCDEE+'
        # Remove common style suffixes (case-insensitive)
        style_suffixes = ['Regular', 'Roman', 'Normal', 'Medium', 'Book',
                          'Bold', 'Bd', 'Heavy', 'Black',
                          'Italic', 'It', 'Oblique', 'Kursiv',
                          'BoldItalic', 'BoldIt', 'BoldOblique', 'BdIt']
        base_name_parts = cleaned_name.split('-') # Split by hyphen first
        base_name = base_name_parts[0]
        # More aggressive cleaning if needed (remove MT, PS etc.)? Be careful not to remove parts of the name.
        # base_name = re.sub(r'(MT|PS)$', '', base_name, flags=re.IGNORECASE)

        # Check if the name itself contains style indicators (if flags failed)
        lower_base_name = base_name.lower() # Check against the potentially cleaned base name
        if not self.is_bold and ('bold' in lower_base_name or 'heavy' in lower_base_name or 'black' in lower_base_name):
            self.is_bold = True
        if not self.is_italic and ('italic' in lower_base_name or 'oblique' in lower_base_name):
            self.is_italic = True
            
        # Store the derived base family name
        self.font_family_base = base_name
        # print(f"DEBUG: Original:'{pdf_font_name}' -> Derived Base:'{self.font_family_base}' B:{self.is_bold} I:{self.is_italic}")

        self.original_is_bold = self.is_bold
        self.original_is_italic = self.is_italic

        # --- Determine Base14 Fallback ---
        # Use a simple check on the derived base name for Base14 category
        lower_clean_base = re.sub(r'[^a-zA-Z]', '', self.font_family_base).lower()
        self.pdf_fontname_base14 = 'helv' # Default Base14 fallback
        for name_key, base_val in BASE14_FALLBACK_MAP.items():
             if name_key in lower_clean_base:
                 self.pdf_fontname_base14 = base_val
                 break
        # print(f"DEBUG: Base14 fallback category: '{self.pdf_fontname_base14}'")
        # --- End Font Info ---

        self.color = normalize_color(color); self.original_color = self.color
        self.selected = False; self.editing = False; self.span_data = span_data
        self.modified = is_new
        self.bbox = span_data.get("bbox") if span_data else (x, y, x + (len(text) * font_size * 0.6), y + font_size)
        self.baseline = baseline if baseline is not None else (self.bbox[3] if self.bbox else y + font_size * 0.9)
        self.page_number = None
        self.dragging = False; self.drag_start_x = 0; self.drag_start_y = 0


class PdfPage(GObject.GObject):
    """GObject wrapper for PDF page info used in the thumbnail list."""
    __gtype_name__ = 'PdfPage'
    index = GObject.Property(type=int)
    thumbnail = GObject.Property(type=GdkPixbuf.Pixbuf)

    def __init__(self, index, thumbnail):
        super().__init__(index=index, thumbnail=thumbnail)