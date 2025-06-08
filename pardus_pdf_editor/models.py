from gi.repository import GObject, GdkPixbuf
from .utils import normalize_color
import re

FLAG_MONO = 1 
FLAG_SERIF = 1 << 1 
FLAG_SYMBOLIC = 1 << 2 
FLAG_SCRIPT = 1 << 3 
FLAG_BOLD = 1 << 4
FLAG_ITALIC = 1 << 5 

BASE14_FALLBACK_MAP = {
    'helvetica': 'helv', 'arial': 'helv', 'sans': 'helv', 'verdana': 'helv', 'tahoma': 'helv',
    'times': 'timr', 'timesnewroman': 'timr', 'serif': 'timr', 'georgia': 'timr',
    'courier': 'cour', 'couriernew': 'cour', 'mono': 'cour', 'monospace': 'cour', 'consolas': 'cour'
}

class EditableText:
    def __init__(self, x, y, text, font_size=11, font_family="Helvetica",
                 color=(0, 0, 0), span_data=None, is_new=False, baseline=None):
        
        self.x = x
        self.y = y
        self.text = text
        self.original_text = text if not is_new else ""
        self.font_size = float(font_size)
        self.is_new = is_new

        pdf_font_name_original = "Helvetica"
        flags = 0
        
        if span_data:
            pdf_font_name_original = span_data.get('font', "Helvetica")
            flags = span_data.get('flags', 0)

        self.font_family_original = pdf_font_name_original 

        self.is_bold = bool(flags & FLAG_BOLD) 
        self.is_italic = bool(flags & FLAG_ITALIC)

        name_after_prefix_removal = re.sub(r'^[A-Z]{6}\+', '', pdf_font_name_original)
        
        potential_family_name = name_after_prefix_removal
        
        style_patterns = [
            (r"(BoldItalic|BoldOblique|BdI|Z|BI)$", "BoldItalic"),
            (r"(Bold|Bd|Heavy|Black|DemiBold|SmBd|SemiBold)$", "Bold"),
            (r"(Italic|It|Oblique|Kursiv|I|Obl)$", "Italic"),
            (r"(Regular|Roman|Normal|Medium|Book|Rg|Text)$", "Regular")
        ]

        detected_style_parts = [] 

        temp_name = potential_family_name
        for pattern, style_tag in style_patterns:
            m = re.search(r"([-_ ]?" + pattern + r")$", temp_name, re.IGNORECASE)
            if m:
                if style_tag == "BoldItalic":
                    if not self.is_bold: self.is_bold = True
                    if not self.is_italic: self.is_italic = True
                    detected_style_parts.extend(["Bold", "Italic"])
                elif style_tag == "Bold":
                    if not self.is_bold: self.is_bold = True
                    detected_style_parts.append("Bold")
                elif style_tag == "Italic":
                    if not self.is_italic: self.is_italic = True
                    detected_style_parts.append("Italic")
                temp_name = temp_name[:m.start()].strip("-_ ")
        
        cleaned_family_name = temp_name if temp_name else name_after_prefix_removal

        cleaned_family_name = re.sub(r'(MT|PS)$', '', cleaned_family_name, flags=re.IGNORECASE).strip()

        cleaned_family_name_spaced = re.sub(r"(\w)([A-Z])", r"\1 \2", cleaned_family_name)
        self.font_family_base = ' '.join(word.capitalize() for word in cleaned_family_name_spaced.replace('-', ' ').replace('_', ' ').split())
        
        if not self.font_family_base:
            self.font_family_base = "Unknown"
        
        lower_base = self.font_family_base.lower()
        if not self.is_bold and any(s in lower_base for s in ["bold", "heavy", "black"]):
             pass 
        if not self.is_italic and any(s in lower_base for s in ["italic", "oblique"]):
             pass

        self.original_is_bold = self.is_bold
        self.original_is_italic = self.is_italic
        normalized_for_base14 = re.sub(r'[^a-zA-Z0-9]', '', self.font_family_base).lower()
        self.pdf_fontname_base14 = 'helv'
        for name_key, base14_val in BASE14_FALLBACK_MAP.items():
            if name_key in normalized_for_base14:
                self.pdf_fontname_base14 = base14_val
                break
        
        self.color = normalize_color(color)
        self.original_color = self.color

        self.selected = False
        self.editing = False
        self.span_data = span_data
        self.modified = is_new 

        if span_data and "bbox" in span_data:
            self.bbox = span_data["bbox"]
        else: 
            estimated_width = len(self.text) * self.font_size * 0.6 
            self.bbox = (self.x, self.y, self.x + estimated_width, self.y + self.font_size)

        if baseline is not None:
            self.baseline = float(baseline)
        elif span_data and "origin" in span_data:
            self.baseline = float(span_data["origin"][1])
        elif self.bbox: 
            self.baseline = float(self.bbox[3] - (self.font_size * 0.1)) 
        else: 
            self.baseline = float(self.y + (self.font_size * 0.9))

        self.page_number = None 
        self.dragging = False
        self.drag_start_x = 0
        self.drag_start_y = 0

class EditableImage:
    def __init__(self, bbox, page_number, xref):
        self.bbox = bbox
        self.page_number = page_number
        self.xref = xref
        self.selected = False

class PdfPage(GObject.GObject):
    __gtype_name__ = 'PdfPage'
    index = GObject.Property(type=int)
    thumbnail = GObject.Property(type=GdkPixbuf.Pixbuf)

    def __init__(self, index, thumbnail):
        super().__init__(index=index, thumbnail=thumbnail)
