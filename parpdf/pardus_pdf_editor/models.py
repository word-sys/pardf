from gi.repository import GObject, GdkPixbuf
from .utils import normalize_color

class EditableText:
    """Represents a potentially editable text span extracted from the PDF."""
    def __init__(self, x, y, text, font_size=11, font_family="Sans",
                 color=(0, 0, 0), span_data=None, is_new=False, baseline=None):
        self.x = x # Original or insertion bbox[0]
        self.y = y # Original or insertion bbox[1]
        self.text = text
        self.original_text = text if not is_new else ""
        self.font_size = font_size
        self.is_new = is_new # Flag indicating if this was added by the user

        # Basic font mapping (can be expanded) - less critical now we embed
        font_mapping = {
            'Sans': 'helv',
            'Serif': 'timr',
            'Monospace': 'cour',
            'Helvetica': 'helv',
            'Times': 'timr',
            'Times-Roman': 'timr',
            'Courier': 'cour',
        }
        pdf_font_name = span_data.get('font', 'Helvetica') if span_data else 'Helvetica'
        self.font_family = pdf_font_name # Store the original name for display/reference
        self.pdf_fontname = font_mapping.get(pdf_font_name.split('+')[-1].split('-')[0], 'helv') # Attempt map to base14 (fallback only)

        self.color = normalize_color(color)
        self.original_color = self.color

        self.selected = False
        self.editing = False
        self.span_data = span_data # Original span dict if available
        self.modified = is_new # New items start as 'modified' relative to original PDF
        # Use span bbox if available, otherwise estimate based on insertion point/size
        self.bbox = span_data.get("bbox") if span_data else (x, y, x + (len(text) * font_size * 0.6), y + font_size) # Rough estimation for new text
        # Use provided baseline or estimate from bbox
        self.baseline = baseline if baseline is not None else (self.bbox[3] if self.bbox else y + font_size * 0.9)
        self.page_number = None # Set when extracted/added

        self.dragging = False # Placeholder for future dragging feature
        self.drag_start_x = 0
        self.drag_start_y = 0


class PdfPage(GObject.GObject):
    """GObject wrapper for PDF page info used in the thumbnail list."""
    __gtype_name__ = 'PdfPage'
    index = GObject.Property(type=int)
    thumbnail = GObject.Property(type=GdkPixbuf.Pixbuf)

    def __init__(self, index, thumbnail):
        super().__init__(index=index, thumbnail=thumbnail)