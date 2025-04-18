import fitz # PyMuPDF
import numpy as np
import cairo
import io
import os
from pathlib import Path
import subprocess
import shutil
import tempfile

from gi.repository import GdkPixbuf, Gdk, Pango, PangoCairo

from .models import EditableText
from .utils import UNICODE_FONT_PATH # Import the found font path

# Store references to keep cairo surface data alive
_surface_cache = {"surface": None, "data_ref": None}

def load_pdf_document(filepath):
    """Opens a PDF document using fitz."""
    try:
        doc = fitz.open(filepath)
        if doc.needs_pass:
            doc.close()
            return None, "Password protected PDFs are not supported yet."
        return doc, None # Return document and no error
    except Exception as e:
        return None, f"Error opening PDF: {e}\nPath: {filepath}"

def close_pdf_document(doc):
    """Closes the fitz document."""
    if doc:
        try:
            doc.close()
        except Exception as e:
            print(f"Error closing PDF document: {e}")

def get_page_count(doc):
    """Returns the number of pages in the document."""
    return doc.page_count if doc else 0

def generate_thumbnail(doc, page_index, size=128):
    """Generates a GdkPixbuf thumbnail for a given page."""
    if not doc or not (0 <= page_index < doc.page_count):
        return None
    try:
        page = doc.load_page(page_index)
        zoom_factor = min(size / page.rect.width, size / page.rect.height)
        matrix = fitz.Matrix(zoom_factor, zoom_factor)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        gdk_pixbuf = GdkPixbuf.Pixbuf.new_from_data(
            pix.samples, GdkPixbuf.Colorspace.RGB, False, 8,
            pix.width, pix.height, pix.stride
        )
        return gdk_pixbuf
    except Exception as thumb_error:
         print(f"Warning: Could not generate thumbnail for page {page_index+1}: {thumb_error}")
         placeholder_pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, int(size * 0.8), size)
         placeholder_pixbuf.fill(0xaaaaaaFF) # Grey placeholder
         return placeholder_pixbuf

def pixmap_to_cairo_surface(pix):
    """Converts a PyMuPDF Pixmap to a Cairo ImageSurface."""
    data = None
    fmt = None
    stride = 0
    data_ref = None

    try:
        if pix.alpha:
            if pix.n != 4: return None, None
            fmt = cairo.FORMAT_ARGB32
            # PyMuPDF samples are RGBA, Cairo ARGB32 expects BGRA on little-endian. Need shuffle.
            samples_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, 4))
            bgra_data = np.zeros_like(samples_np)
            bgra_data[..., 0] = samples_np[..., 2] # B
            bgra_data[..., 1] = samples_np[..., 1] # G
            bgra_data[..., 2] = samples_np[..., 0] # R
            bgra_data[..., 3] = samples_np[..., 3] # A
            data = bytearray(bgra_data.tobytes()) # Use bytes from shuffled array
            stride = pix.stride # Stride should still be correct
            data_ref = data

        else: # RGB
            if pix.n != 3: return None, None
            # Create target ARGB (BGRA) numpy array
            bgra_data = np.zeros((pix.height, pix.width, 4), dtype=np.uint8)
            # Create view or copy of source RGB data
            try:
                 rgb_view = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, 3))
            except ValueError: # Handle non-contiguous stride if necessary
                 rgb_view = np.frombuffer(pix.samples, dtype=np.uint8).copy().reshape((pix.height, pix.width, 3))

            bgra_data[:, :, 0] = rgb_view[:, :, 2]  # Blue
            bgra_data[:, :, 1] = rgb_view[:, :, 1]  # Green
            bgra_data[:, :, 2] = rgb_view[:, :, 0]  # Red
            bgra_data[:, :, 3] = 255                # Alpha
            data = bgra_data.data # Get buffer protocol object
            fmt = cairo.FORMAT_ARGB32
            stride = pix.width * 4 # Recalculate stride for 4 components
            data_ref = bgra_data # Keep numpy array alive

        if data is None: return None, None

        surface = cairo.ImageSurface.create_for_data(data, fmt, pix.width, pix.height, stride)
        return surface, data_ref

    except Exception as e:
        print(f"Error creating Cairo surface from pixmap: {e}")
        return None, None


def draw_page_to_cairo(cr, doc, page_index, zoom_level):
    """Renders a PDF page onto a Cairo context."""
    global _surface_cache
    _surface_cache = {"surface": None, "data_ref": None} # Clear previous

    if not doc or not (0 <= page_index < doc.page_count):
        cr.set_source_rgb(0.7, 0.7, 0.7)
        cr.paint()
        return False, "Invalid document or page index."

    try:
        page = doc.load_page(page_index)
        zoom_matrix = fitz.Matrix(zoom_level, zoom_level)
        pix = page.get_pixmap(matrix=zoom_matrix, alpha=True) # Use Alpha for potential transparency

        surface, data_ref = pixmap_to_cairo_surface(pix)

        if surface and data_ref:
             _surface_cache["surface"] = surface
             _surface_cache["data_ref"] = data_ref # CRITICAL: Keep data alive
             cr.set_source_rgb(1.0, 1.0, 1.0) # Set source to white
             cr.paint()
             cr.set_source_surface(surface, 0, 0)
             cr.paint()
             return True, None # Success
        else:
            error_msg = "Failed to create Cairo surface from page pixmap."
            print(error_msg)
            # Draw fallback
            cr.set_source_rgb(1.0, 0.8, 0.8)
            cr.paint()
            cr.set_source_rgb(0,0,0)
            layout = PangoCairo.create_layout(cr)
            layout.set_text(error_msg, -1)
            font_desc = Pango.FontDescription("Sans 10")
            layout.set_font_description(font_desc)
            PangoCairo.show_layout(cr, layout)
            return False, error_msg

    except Exception as e:
        error_msg = f"Error rendering page {page_index+1}: {e}"
        print(error_msg)
        _surface_cache = {"surface": None, "data_ref": None} # Clear on error
        # Draw fallback
        cr.set_source_rgb(1.0, 0.0, 0.0)
        cr.paint()
        cr.set_source_rgb(1.0, 1.0, 1.0)
        layout = PangoCairo.create_layout(cr)
        layout.set_text(error_msg, -1)
        font_desc = Pango.FontDescription("Sans 10")
        layout.set_font_description(font_desc)
        PangoCairo.show_layout(cr, layout)
        return False, error_msg


def extract_editable_text(doc, page_index):
    """Extracts text spans as EditableText objects for the given page."""
    editable_texts = []
    if not doc or not (0 <= page_index < doc.page_count):
        return [], "Invalid document or page index for text extraction."

    try:
        page = doc.load_page(page_index)
        text_dict = page.get_text("dict", flags=11) # Detailed info

        for block in text_dict.get("blocks", []):
            if block.get("type") == 0: # Text block
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        bbox = span.get("bbox")
                        if not text or not bbox: continue

                        editable = EditableText(
                            x=bbox[0], y=bbox[1], text=text,
                            font_size=span.get("size", 11),
                            color=span.get("color", 0),
                            span_data=span,
                            baseline=span.get("origin", (0, bbox[3]))[1] # Use origin y if available
                        )
                        editable.page_number = page_index
                        editable_texts.append(editable)
        return editable_texts, None # Success

    except Exception as e:
        error_msg = f"Error extracting text from page {page_index}: {e}"
        print(error_msg)
        return [], error_msg


def apply_text_edit(doc, text_obj: EditableText, new_text: str):
    """Applies changes (modify, delete, add) to the PDF page."""
    if not doc or text_obj.page_number is None:
        return False, "Invalid document or page number for editing."

    if not UNICODE_FONT_PATH and any(ord(c) > 127 for c in new_text):
         return False, "Cannot save non-ASCII text: No suitable Unicode font found."
    elif not UNICODE_FONT_PATH:
         print("Warning: No Unicode font found. Using default PDF fonts (may fail for special chars).")
         font_arg = {"fontname": text_obj.pdf_fontname} # Fallback to base14 name
    else:
         # Use the found TTF font file for embedding
         # Define an internal name for the font within the PDF
         internal_font_name = "PardusEditFont"
         font_arg = {"fontfile": UNICODE_FONT_PATH, "fontname": internal_font_name}

    try:
        page = doc.load_page(text_obj.page_number)

        # --- 1. Redact Original Area (only if modifying existing text) ---
        if not text_obj.is_new and text_obj.bbox:
            # Use original bbox for redaction
            redact_rect = fitz.Rect(text_obj.bbox)
            redact_rect.normalize()
            # Clip to page bounds just in case
            page_rect = page.rect
            redact_rect.intersect(page_rect)

            if redact_rect.is_empty or not redact_rect.is_valid:
                 print(f"Warning: Invalid or empty redaction rectangle for text: {text_obj.original_text[:20]}...")
            else:
                # Add redaction annotation - mark area for removal
                annot = page.add_redact_annot(redact_rect, text=" ") # Fill doesn't matter
                if not annot:
                    print("Warning: Could not add redaction annotation.")
                    # Optionally: return False, "Failed to mark area for redaction."

                # Apply redactions NOW to clear the space before inserting new text.
                # This invalidates some page object references, but is necessary here.
                # Only redact vector graphics/text, leave images untouched usually.
                apply_result = page.apply_redactions()
                if not apply_result:
                    print("Warning: Applying redactions failed or did nothing.")
                    # Maybe attempt to remove the annotation we added?
                    # page.delete_annot(annot) # Requires re-finding the annotation if reference is lost
                    # Optionally: return False, "Failed to apply redactions."


        # --- 2. Insert New Text (if new_text is not empty) ---
        if new_text:
            insert_point = fitz.Point(text_obj.x, text_obj.baseline) # Use original baseline
            fontsize = text_obj.font_size
            color = text_obj.color

            # Handle potential multi-line text
            lines = new_text.split('\n')
            line_height_factor = 1.2 # Standard line height factor
            line_height = fontsize * line_height_factor
            current_y = text_obj.baseline

            for i, line in enumerate(lines):
                line_point = fitz.Point(text_obj.x, current_y)
                try:
                    # Insert using the determined font arguments (embedded or base14)
                    rc = page.insert_text(
                        line_point,
                        line,
                        fontsize=fontsize,
                        color=color,
                        rotate=0,
                        **font_arg # Pass fontfile/fontname or just fontname
                    )
                    if rc < 0:
                        print(f"Warning: PyMuPDF insert_text returned error {rc} for line: {line}")
                        # Try falling back to a basic font if embedding failed? Might require more logic.
                except Exception as insert_e:
                     print(f"ERROR: Text insertion failed for line '{line}': {insert_e}")
                     # If one line fails, maybe stop or report specific error?
                     return False, f"Failed to insert text line '{line}': {insert_e}"

                current_y += line_height # Move down for next line

        # --- 3. Update EditableText state (caller might replace the list) ---
        # The object itself might be replaced after page reload, but update flags
        text_obj.text = new_text
        text_obj.modified = True # Mark modified (or deleted if new_text is empty)
        # Bbox recalculation is complex, skip for now. Rely on page reload.
        text_obj.is_new = False # Once applied, it's no longer 'new' in the context of needing only insertion

        # --- 4. Optional Cleanup (better done once on save) ---
        # page.clean_contents()

        return True, None # Success

    except Exception as e:
        error_msg = f"Error applying text changes: {e}"
        print(error_msg)
        # Attempt to reload the page in the main window to revert visual state?
        return False, error_msg


# pardus-pdf-editor-project/pardus_pdf_editor/pdf_handler.py

def save_document(doc, save_path, incremental=False):
    """Saves the document using fitz."""
    if not doc:
        return False, "No document to save."
    try:
        garbage_level = 0 if incremental else 4

        # Determine encryption setting based on incremental flag
        if incremental:
            # Preserve original encryption status for incremental saves
            # PDF_ENCRYPT_KEEP tells save() to reuse the current encryption method/permissions.
            encryption_setting = fitz.PDF_ENCRYPT_KEEP
        else:
            # Save unencrypted for non-incremental saves (Save As/Export) by default
            encryption_setting = fitz.PDF_ENCRYPT_NONE # Explicitly specify no encryption

        doc.save(
            save_path,
            garbage=garbage_level,
            deflate=True,
            incremental=incremental,
            encryption=encryption_setting # Pass the determined encryption setting
        )
        return True, None
    except Exception as e:
        # Keep the specific error checks from before
        if "save to original must be incremental" in str(e) and not incremental:
             return False, f"Error saving PDF: Cannot overwrite original file without using incremental save. Try 'Save As...'."
        if "incremental writes with garbage collection" in str(e):
             return False, f"Error saving PDF: Internal conflict - Cannot perform garbage collection during incremental save."
        # Add check for the error we just fixed
        if "incremental writes when changing encryption" in str(e):
             # This error *shouldn't* happen now, but check just in case
             return False, f"Error saving PDF: Cannot change encryption during incremental save. Check original file encryption."
        # Return the general error for other issues
        return False, f"Error saving PDF: {e}"

def export_pdf_as_docx(doc, source_pdf_path, output_docx_path):
    """Exports PDF to DOCX using LibreOffice."""
    if not shutil.which('libreoffice'):
        return False, "LibreOffice Not Found. Install it to enable DOCX export."

    temp_pdf_path = None
    try:
        # Save the potentially modified PDF to a temporary file for LO
        # Use NamedTemporaryFile within the source PDF's directory for LO context? Or /tmp?
        # Using tempfile.mkstemp ensures the file is created securely.
        fd, temp_pdf_path = tempfile.mkstemp(suffix=".pdf", prefix="pardus_export_")
        os.close(fd) # We just need the path, fitz will open/write it.

        # Save current doc state to the temp file
        save_success, save_msg = save_document(doc, temp_pdf_path)
        if not save_success:
            if os.path.exists(temp_pdf_path): os.unlink(temp_pdf_path)
            return False, f"Failed to save temporary PDF for export: {save_msg}"

        output_dir = os.path.dirname(output_docx_path)
        # LibreOffice often creates output named like the input in the output dir
        expected_output_name = Path(temp_pdf_path).stem + ".docx"
        expected_output_file = os.path.join(output_dir, expected_output_name)

        # Remove potentially leftover file from previous failed attempt
        if os.path.exists(expected_output_file):
             os.remove(expected_output_file)

        command = [
            'libreoffice', '--headless', '--convert-to', 'docx',
            '--outdir', output_dir, temp_pdf_path
        ]
        process = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120) # Add timeout

        if process.returncode != 0:
            error_msg = f"LibreOffice conversion failed (code {process.returncode}).\nError:\n{process.stderr or process.stdout}"
            print(error_msg)
            return False, error_msg

        # Check if the expected file exists and rename it
        if os.path.exists(expected_output_file):
            shutil.move(expected_output_file, output_docx_path)
            return True, None # Success
        else:
            error_msg = "LibreOffice conversion finished, but the output DOCX file was not found."
            print(error_msg)
            print(f"Expected: {expected_output_file}")
            return False, error_msg

    except subprocess.TimeoutExpired:
        return False, "LibreOffice conversion timed out (took longer than 120 seconds)."
    except Exception as e:
        return False, f"Error during DOCX export: {e}"
    finally:
        # Clean up temporary PDF
        if temp_pdf_path and os.path.exists(temp_pdf_path):
            try:
                os.unlink(temp_pdf_path)
            except Exception as unlink_e:
                print(f"Warning: Could not delete temporary file {temp_pdf_path}: {unlink_e}")


def export_pdf_as_text(doc, output_txt_path):
    """Exports the PDF document content as a single text file."""
    if not doc:
        return False, "No document to export."
    try:
        with open(output_txt_path, 'w', encoding='utf-8') as txt_file:
            for page_num in range(doc.page_count):
                page = doc.load_page(page_num)
                text = page.get_text("text", sort=True)
                txt_file.write(f"--- Page {page_num + 1} ---\n\n")
                txt_file.write(text)
                txt_file.write("\n\n")
        return True, None # Success
    except Exception as e:
        return False, f"Error exporting as text: {e}"
