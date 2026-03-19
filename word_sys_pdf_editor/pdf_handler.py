import fitz
import numpy as np
import cairo
import io
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import traceback
import re

from gi.repository import GdkPixbuf, Gdk, Pango, PangoCairo
from .models import EditableText, FLAG_BOLD, FLAG_ITALIC, EditableImage, EditableShape
from .utils import find_specific_font_variant, get_default_unicode_font_path

_surface_cache = {"surface": None, "data_ref": None}

def _get_font_args_for_pymupdf(text_obj):
    font_arg = {}
    font_to_embed_path = find_specific_font_variant(
        text_obj.font_family_base,
        text_obj.is_bold,
        text_obj.is_italic
    )

    if font_to_embed_path:
        style_suffix = ""
        if text_obj.is_bold: style_suffix += "Bold"
        if text_obj.is_italic: style_suffix += "Italic"
        if not style_suffix: style_suffix = "Regular"

        safe_family_name = re.sub(r'\W+', '', text_obj.font_family_base or "UnknownFont")
        internal_font_name = f"WordSys_{safe_family_name}_{style_suffix}"
        
        font_arg = {"fontfile": font_to_embed_path, "fontname": internal_font_name}
        print(f"DEBUG (FontHelper): Using TTF: {font_to_embed_path} as '{internal_font_name}'")
        return font_arg, None
    else:
        generic_unicode_font = get_default_unicode_font_path()
        if generic_unicode_font:
            internal_font_name = "WordSysEditFont_GenericUnicode"
            font_arg = {"fontfile": generic_unicode_font, "fontname": internal_font_name}
            print(f"DEBUG (FontHelper): WARNING: Could not find specific TTF. Using generic fallback: {generic_unicode_font}")
            return font_arg, None
        else:
            base14_name = text_obj.pdf_fontname_base14
            if text_obj.is_bold and text_obj.is_italic: base14_name += "bo"
            elif text_obj.is_bold: base14_name += "b"
            elif text_obj.is_italic: base14_name += "i"
            font_arg = {"fontname": base14_name}
            print(f"DEBUG (FontHelper): CRITICAL WARNING: No TTF found. Falling back to Base 14 font: '{base14_name}'.")
            if any(ord(c) > 127 for c in text_obj.text):
                 return None, "Cannot save non-ASCII text: No suitable Unicode font found."
            return font_arg, None

def load_pdf_document(filepath):
    try:
        doc = fitz.open(filepath)
        if doc.needs_pass:
            doc.close()
            return None, "Password protected PDFs are not supported yet."
        return doc, None
    except Exception as e:
        return None, f"Error opening PDF: {e}\nPath: {filepath}"

def close_pdf_document(doc):
    if doc:
        try:
            doc.close()
        except Exception as e:
            print(f"Error closing PDF document: {e}")

def get_page_count(doc):
    return doc.page_count if doc else 0

def generate_thumbnail(doc, page_index, target_width=150):
    if not doc or not (0 <= page_index < doc.page_count):
        return None
    try:
        page = doc.load_page(page_index)
        
        page_w = page.rect.width
        if page_w == 0: 
            page_w = 1 
            
        zoom_factor = target_width / page_w
        matrix = fitz.Matrix(zoom_factor, zoom_factor)

        pix = page.get_pixmap(matrix=matrix, alpha=False)
        gdk_pixbuf = GdkPixbuf.Pixbuf.new_from_data(
            pix.samples, GdkPixbuf.Colorspace.RGB, False, 8,
            pix.width, pix.height, pix.stride
        )
        return gdk_pixbuf
    except Exception as thumb_error:
        print(f"Warning: Could not generate thumbnail for page {page_index+1}: {thumb_error}")
        placeholder_pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, target_width, int(target_width * 1.414))
        placeholder_pixbuf.fill(0xaaaaaaFF)
        return placeholder_pixbuf

def pixmap_to_cairo_surface(pix):
    data = None
    fmt = None
    stride = 0
    data_ref = None

    try:
        if pix.alpha:
            if pix.n != 4: return None, None
            fmt = cairo.FORMAT_ARGB32
            samples_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, 4))
            bgra_data = np.zeros_like(samples_np)
            bgra_data[..., 0] = samples_np[..., 2] # B
            bgra_data[..., 1] = samples_np[..., 1] # G
            bgra_data[..., 2] = samples_np[..., 0] # R
            bgra_data[..., 3] = samples_np[..., 3] # A
            data = bytearray(bgra_data.tobytes())
            stride = pix.stride
            data_ref = data

        else:
            if pix.n != 3: return None, None
            bgra_data = np.zeros((pix.height, pix.width, 4), dtype=np.uint8)
            try:
                 rgb_view = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, 3))
            except ValueError:
                 rgb_view = np.frombuffer(pix.samples, dtype=np.uint8).copy().reshape((pix.height, pix.width, 3))

            bgra_data[:, :, 0] = rgb_view[:, :, 2]  # Blue
            bgra_data[:, :, 1] = rgb_view[:, :, 1]  # Green
            bgra_data[:, :, 2] = rgb_view[:, :, 0]  # Red
            bgra_data[:, :, 3] = 255                # Alpha
            data = bgra_data.data 
            fmt = cairo.FORMAT_ARGB32
            stride = pix.width * 4 
            data_ref = bgra_data 

        if data is None: return None, None

        surface = cairo.ImageSurface.create_for_data(data, fmt, pix.width, pix.height, stride)
        return surface, data_ref

    except Exception as e:
        print(f"Error creating Cairo surface from pixmap: {e}")
        return None, None


def draw_page_to_cairo(cr, doc, page_index, zoom_level):
    if not doc or not (0 <= page_index < doc.page_count):
        cr.set_source_rgb(0.7, 0.7, 0.7)
        cr.paint()
        return False, "Invalid document or page index."

    try:
        page = doc.load_page(page_index)
        zoom_matrix = fitz.Matrix(zoom_level, zoom_level)
        pix = page.get_pixmap(matrix=zoom_matrix, alpha=True)
        samples_bytes = bytes(pix.samples)

        pixbuf = GdkPixbuf.Pixbuf.new_from_data(
            samples_bytes, GdkPixbuf.Colorspace.RGB, True, 8,
            pix.width, pix.height, pix.stride
        )

        if pixbuf:
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.paint()

            Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
            cr.paint()
            return True, None
        else:
            error_msg = "Failed to create GdkPixbuf from page pixmap."
            print(error_msg)
            cr.set_source_rgb(1.0, 0.8, 0.8)
            cr.paint()
            layout = PangoCairo.create_layout(cr)
            layout.set_text(f"Error: {error_msg}", -1)
            font_desc = Pango.FontDescription("Sans 10")
            layout.set_font_description(font_desc)
            cr.move_to(10, 10)
            PangoCairo.show_layout(cr, layout)
            return False, error_msg

    except Exception as e:
        error_msg = f"Error rendering page {page_index+1} via GdkPixbuf: {e}"
        print(error_msg)
        cr.set_source_rgb(1.0, 0.0, 0.0)
        cr.paint()
        cr.set_source_rgb(1.0,1.0,1.0)
        layout = PangoCairo.create_layout(cr)
        layout.set_text(error_msg, -1)
        font_desc = Pango.FontDescription("Sans 10")
        layout.set_font_description(font_desc)
        cr.move_to(10, 10)
        PangoCairo.show_layout(cr, layout)
        return False, error_msg


def extract_editable_text(doc, page_index):
    editable_texts = []
    if not doc or not (0 <= page_index < doc.page_count):
        return [], "Invalid document or page index for text extraction."
    try:
        page = doc.load_page(page_index)
        text_dict = page.get_text("dict", flags=0)

        for block in text_dict.get("blocks", []):
            if block.get("type") == 0:
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    if not spans:
                        continue
                    
                    combined_text = ""
                    min_x = float('inf')
                    max_x = float('-inf')
                    min_y = float('inf')
                    max_y = float('-inf')
                    first_span = None
                    
                    for span in spans:
                        text = span.get("text", "")
                        bbox = span.get("bbox")
                        
                        if not text:
                            continue
                        
                        if first_span is None:
                            first_span = span
                        
                        combined_text += text
                        
                        if bbox:
                            min_x = min(min_x, bbox[0])
                            min_y = min(min_y, bbox[1])
                            max_x = max(max_x, bbox[2])
                            max_y = max(max_y, bbox[3])
                    
                    if not combined_text or first_span is None:
                        continue
                    
                    combined_text = combined_text.strip()
                    if not combined_text:
                        continue
                    
                    bbox = [min_x, min_y, max_x, max_y] if min_x != float('inf') else first_span.get("bbox", [0, 0, 100, 100])
                    
                    span_data = first_span.copy() if first_span else {}
                    span_data["bbox"] = tuple(bbox)
                    span_data["text"] = combined_text
                    
                    editable = EditableText(
                        x=bbox[0], y=bbox[1], text=combined_text,
                        font_size=first_span.get("size", 11) if first_span else 11,
                        font_family="Liberation Sans",
                        color=first_span.get("color", 0) if first_span else 0,
                        span_data=span_data,
                        baseline=first_span.get("origin", (0, bbox[3]))[1] if first_span else bbox[3]
                    )
                    editable.page_number = page_index
                    editable_texts.append(editable)
                    print(f"DEBUG: Extracted text: '{combined_text}' bbox={bbox}")
        
        print(f"DEBUG: Total text objects extracted from page {page_index}: {len(editable_texts)}")
        return editable_texts, None
    except Exception as e:
        error_msg = f"Error extracting text from page {page_index}: {e}"
        print(error_msg)
        traceback.print_exc()
        return [], error_msg

def _get_base14_font_variant(base_name, is_bold, is_italic):
    mapping = {'helv': 'Helvetica', 'timr': 'Times', 'cour': 'Courier'}
    pdf_base = mapping.get(base_name, 'Helvetica')
    if is_bold and is_italic:
        if pdf_base == 'Helvetica': return 'Helvetica-BoldOblique'
        if pdf_base == 'Times': return 'Times-BoldItalic'
        if pdf_base == 'Courier': return 'Courier-BoldOblique'
    elif is_bold:
        if pdf_base == 'Helvetica': return 'Helvetica-Bold'
        if pdf_base == 'Times': return 'Times-Bold'
        if pdf_base == 'Courier': return 'Courier-Bold'
    elif is_italic:
        if pdf_base == 'Helvetica': return 'Helvetica-Oblique'
        if pdf_base == 'Times': return 'Times-Italic'
        if pdf_base == 'Courier': return 'Courier-Oblique'
    else:
        if pdf_base == 'Helvetica': return 'Helvetica'
        if pdf_base == 'Times': return 'Times-Roman'
        if pdf_base == 'Courier': return 'Courier'
    return pdf_base

def apply_text_edit(doc, text_obj: EditableText, new_text: str):
    if not doc or text_obj.page_number is None:
        return False, "Invalid document or page number."

    font_arg, error_msg = _get_font_args_for_pymupdf(text_obj)
    if error_msg:
        return False, error_msg

    try:
        page = doc.load_page(text_obj.page_number)
        
        print(f"DEBUG apply_text_edit: is_new={text_obj.is_new}, new_text='{new_text}'")
        print(f"DEBUG: text_obj bbox: {text_obj.bbox}")
        
        if new_text.strip():
            text_color = (0, 0, 0)
            if text_obj.color:
                if isinstance(text_obj.color, (tuple, list)) and len(text_obj.color) >= 3:
                    text_color = tuple(float(c) for c in text_obj.color[:3])
                elif isinstance(text_obj.color, int):
                    blue = (text_obj.color & 255) / 255.0
                    green = ((text_obj.color >> 8) & 255) / 255.0
                    red = ((text_obj.color >> 16) & 255) / 255.0
                    text_color = (red, green, blue)
            
            print(f"DEBUG: Inserting updated text '{new_text}' at point ({text_obj.x}, {text_obj.baseline})")
            
            line_point = fitz.Point(text_obj.x, text_obj.baseline)
            rc = page.insert_text(
                line_point,
                new_text,
                fontsize=text_obj.font_size,
                color=text_color,
                overlay=True,
                **font_arg
            )
            print(f"DEBUG: insert_text returned: {rc}")
            if rc < 0:
                print(f"ERROR: insert_text failed with rc={rc}")
                return False, f"PyMuPDF insert_text error: {rc}"

        return True, None
    except Exception as e:
        print(f"ERROR applying text edit: {e}")
        traceback.print_exc()
        return False, f"Error during text application: {e}"
#original
'''  
def save_document(doc, save_path, incremental=False):
    if not doc:
        return False, "No document to save."
    try:
        garbage_level = 0 if incremental else 4

        if incremental:
            encryption_setting = fitz.PDF_ENCRYPT_KEEP
        else:
            encryption_setting = fitz.PDF_ENCRYPT_NONE

        doc.save(
            save_path,
            garbage=garbage_level,
            deflate=True,
            incremental=incremental,
            encryption=encryption_setting
        )
        return True, None
    except Exception as e:
        if "save to original must be incremental" in str(e) and not incremental:
             return False, f"Error saving PDF: Cannot overwrite original file without using incremental save. Try 'Save As...'."
        if "incremental writes with garbage collection" in str(e):
             return False, f"Error saving PDF: Internal conflict - Cannot perform garbage collection during incremental save."
        if "incremental writes when changing encryption" in str(e):
             return False, f"Error saving PDF: Cannot change encryption during incremental save. Check original file encryption."
        return False, f"Error saving PDF: {e}"
'''
#patched
def save_document(doc, save_path, incremental=False):
    if not doc:
        return False, "Kaydedilecek belge yok."

    temp_path = f"{save_path}.tmp_save"

    try:
        doc.save(
            temp_path,
            garbage=4,
            deflate=True,
            incremental=False,  
            encryption=fitz.PDF_ENCRYPT_NONE
        )

        os.replace(temp_path, save_path)
        return True, None

    except Exception as e:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        
        return False, f"PDF kaydedilirken hata oluştu: {e}"
    
def _export_via_libreoffice(doc, source_pdf_path, output_path, target_format):
    libreoffice_executable = shutil.which('libreoffice')
    if not libreoffice_executable:
        libreoffice_executable = shutil.which('soffice')
    if not libreoffice_executable:
        return False, f"LibreOffice Not Found. Install 'libreoffice-writer' to enable {target_format.upper()} export."
    print(f"DEBUG [{target_format.upper()} Export]: Using LibreOffice executable: {libreoffice_executable}")

    final_output_dir = Path(output_path).parent
    final_output_dir.mkdir(parents=True, exist_ok=True)

    temp_pdf_path = None
    try:
        # Save temp PDF directly in the destination folder to avoid /tmp permission issues
        fd, temp_pdf_path = tempfile.mkstemp(suffix=".pdf", prefix="wordsys_export_", dir=str(final_output_dir))
        os.close(fd)
        print(f"DEBUG [{target_format.upper()} Export]: Saving document state to temporary file: {temp_pdf_path}")

        # Produce a completely clean, garbage-collected PDF in memory.
        # This is CRITICAL because LibreOffice's writer_pdf_import will fail with
        # 'source file could not be loaded' if the PDF has complex iterative streams.
        try:
            pdf_bytes = doc.tobytes(garbage=4, clean=True, deflate=True)
            with open(temp_pdf_path, 'wb') as f:
                f.write(pdf_bytes)
            save_success = True
            save_msg = ""
        except Exception as e:
            # Fallback to standard save_document
            save_success, save_msg = save_document(doc, temp_pdf_path, incremental=False)

        if not save_success:
            if os.path.exists(temp_pdf_path): os.unlink(temp_pdf_path)
            return False, f"Failed to save temporary PDF for export: {save_msg}"
        
        if not os.path.exists(temp_pdf_path) or os.path.getsize(temp_pdf_path) == 0:
            print(f"ERROR [{target_format.upper()} Export]: Temporary PDF '{temp_pdf_path}' was not created or is empty.")
            if os.path.exists(temp_pdf_path): os.unlink(temp_pdf_path)
            return False, "Failed to create a valid temporary PDF for export."

        print(f"DEBUG [{target_format.upper()} Export]: Temp PDF Path = {temp_pdf_path} (Size: {os.path.getsize(temp_pdf_path)} bytes)")
        temp_pdf_path_obj = Path(temp_pdf_path)
        temp_pdf_name_no_ext = temp_pdf_path_obj.stem

        print(f"DEBUG [{target_format.upper()} Export]: Final {target_format.upper()} Output Dir = {final_output_dir}")
        print(f"DEBUG [{target_format.upper()} Export]: Desired Final {target_format.upper()} Path = {output_path}")

        python_cwd = Path(os.getcwd())
        expected_output_in_python_cwd = python_cwd / f"{temp_pdf_name_no_ext}.{target_format}"
        print(f"DEBUG [{target_format.upper()} Export]: Python's Current Working Directory (for output): {python_cwd}")
        print(f"DEBUG [{target_format.upper()} Export]: Expected {target_format.upper()} in Python CWD: {expected_output_in_python_cwd}")
        
        if os.path.exists(expected_output_in_python_cwd):
            print(f"DEBUG [{target_format.upper()} Export]: Removing leftover in CWD: {expected_output_in_python_cwd}")
            os.remove(expected_output_in_python_cwd)
        if os.path.exists(output_path):
            print(f"DEBUG [{target_format.upper()} Export]: Removing leftover final target: {output_path}")
            os.remove(output_path)

        # Simplify format
        if target_format == 'odt':
            convert_format = 'odt'
        elif target_format == 'docx':
            convert_format = 'docx'  
        else:
            convert_format = target_format

        infilter = 'writer_pdf_import'
        if target_format in ('pptx', 'odp'):
            infilter = 'impress_pdf_import'

        command = [
            libreoffice_executable,
            '--headless',
            '--invisible',
            '--nologo',
            f'--infilter={infilter}',
            '--convert-to', convert_format,
            '--outdir', str(temp_pdf_path_obj.parent),
            str(temp_pdf_path)
        ]

        expected_output_location = temp_pdf_path_obj.parent / f"{temp_pdf_name_no_ext}.{target_format}"

        print(f"DEBUG [{target_format.upper()} Export]: Expected {target_format.upper()} at: {expected_output_location}")
        if os.path.exists(expected_output_location):
            print(f"DEBUG [{target_format.upper()} Export]: Removing leftover: {expected_output_location}")
            os.remove(expected_output_location)

        print(f"DEBUG [{target_format.upper()} Export]: Running command: {' '.join(command)}")

        current_env = os.environ.copy()
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            env=current_env
        )

        print(f"DEBUG [{target_format.upper()} Export]: LibreOffice Return Code: {process.returncode}")
        if process.stdout:
            print(f"DEBUG [{target_format.upper()} Export]: LibreOffice stdout:\n---\n{process.stdout.strip()}\n---")
        if process.stderr:
            print(f"DEBUG [{target_format.upper()} Export]: LibreOffice stderr:\n---\n{process.stderr.strip()}\n---")

        if "0xc10" in process.stderr or "SfxBaseModel::impl_store" in process.stderr:
            error_msg = f"LibreOffice I/O Write Error during conversion. Stderr: {process.stderr.strip()}"
            if "no export filter" in process.stderr.lower():
                 error_msg += " (Also saw 'no export filter' - check LO installation and write permissions)"
            print(f"ERROR [{target_format.upper()} Export]: {error_msg}")
            return False, error_msg
        
        if ("no export filter" in process.stderr.lower() or "no export filter" in process.stdout.lower()) and process.returncode != 0 :
            error_msg = f"LibreOffice reported: No export filter for {target_format.upper()} found. Ensure 'libreoffice-writer' is fully installed."
            print(f"ERROR [{target_format.upper()} Export]: {error_msg}")
            return False, error_msg
            
        if process.returncode != 0:
            error_msg = f"LibreOffice conversion failed (code {process.returncode}).\nError:\n{process.stderr or process.stdout}"
            print(f"ERROR [{target_format.upper()} Export]: {error_msg}")
            return False, error_msg

        if os.path.exists(expected_output_location):
            print(f"DEBUG [{target_format.upper()} Export]: Found {target_format.upper()} at: {expected_output_location}")
            try:
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(expected_output_location), str(output_path))
                print(f"DEBUG [{target_format.upper()} Export]: Moved {target_format.upper()} to final destination: {output_path}")
                return True, None
            except Exception as move_e:
                error_msg = f"Found converted {target_format.upper()} ({expected_output_location}) but failed to move to {output_path}: {move_e}"
                print(f"ERROR [{target_format.upper()} Export]: {error_msg}")
                return False, error_msg
        else:
            error_msg = f"LibreOffice conversion seemed to finish, but the output {target_format.upper()} ({expected_output_location}) could not be located."
            print(f"ERROR [{target_format.upper()} Export]: {error_msg}")
            return False, error_msg

    except subprocess.TimeoutExpired:
        return False, "LibreOffice conversion timed out (took longer than 120 seconds)."
    except Exception as e:
        print(f"ERROR [{target_format.upper()} Export]: General exception during {target_format.upper()} export process: {e}")
        return False, f"Error during {target_format.upper()} export process: {e}"
    finally:
        if temp_pdf_path and os.path.exists(temp_pdf_path):
            try:
                os.unlink(temp_pdf_path)
                print(f"DEBUG [{target_format.upper()} Export]: Cleaned up temp PDF: {temp_pdf_path}")
            except Exception as unlink_e:
                print(f"Warning: Could not delete temporary file {temp_pdf_path}: {unlink_e}")

def export_pdf_as_odt(doc, source_pdf_path, output_odt_path):
    if not output_odt_path.lower().endswith('.odt'):
        output_odt_path += '.odt'
    return _export_via_libreoffice(doc, source_pdf_path, output_odt_path, 'odt')

def export_pdf_as_docx(doc, source_pdf_path, output_docx_path):
    if not output_docx_path.lower().endswith('.docx'):
        output_docx_path += '.docx'
    return _export_via_libreoffice(doc, source_pdf_path, output_docx_path, 'docx')

def export_pdf_as_pptx(doc, source_pdf_path, output_pptx_path):
    if not output_pptx_path.lower().endswith('.pptx'):
        output_pptx_path += '.pptx'
    return _export_via_libreoffice(doc, source_pdf_path, output_pptx_path, 'pptx')

def export_pdf_as_odp(doc, source_pdf_path, output_odp_path):
    if not output_odp_path.lower().endswith('.odp'):
        output_odp_path += '.odp'
    return _export_via_libreoffice(doc, source_pdf_path, output_odp_path, 'odp')


def export_pdf_as_odt_alias(doc, source_pdf_path, output_odt_path):
    return export_pdf_as_odt(doc, source_pdf_path, output_odt_path)


def _export_pdf_via_libreoffice(doc, output_path, target_format, format_label):
    if target_format == 'docx':
        return export_pdf_as_docx(doc, None, output_path)
    elif target_format == 'odt':
        return export_pdf_as_odt(doc, None, output_path)
    return False, f"Unsupported format: {target_format}"


def export_pdf_as_text(doc, output_txt_path):
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
        return True, None
    except Exception as e:
        return False, f"Error exporting as text: {e}"

def extract_editable_images(doc, page_index):
    editable_images = []
    if not doc or not (0 <= page_index < doc.page_count):
        return [], "Görüntü çıkarma için geçersiz belge veya sayfa dizini."
    
    try:
        page = doc.load_page(page_index)
        image_info_list = page.get_image_info(xrefs=True)
        
        if not image_info_list:
            print(f"DEBUG: No images found via get_image_info on page {page_index}. Trying alternative method...")
            image_info_list = []
        
        for img_info in image_info_list:
            try:
                bbox = img_info.get('bbox')
                xref = img_info.get('xref')
                
                if not bbox or not xref:
                    continue

                rect = fitz.Rect(bbox)
                if rect.is_empty or not rect.is_valid:
                    continue

                try:
                    image_data = doc.extract_image(xref)
                    if not image_data or 'image' not in image_data:
                        print(f"DEBUG: Could not extract image data for xref {xref}")
                        continue
                    
                    image_bytes = image_data["image"]
                    
                    image_obj = EditableImage(
                        bbox=bbox,
                        page_number=page_index,
                        xref=xref,
                        image_bytes=image_bytes
                    )
                    editable_images.append(image_obj)
                except Exception as extract_error:
                    print(f"DEBUG: Error extracting image bytes for xref {xref}: {extract_error}")
                    continue
                    
            except (ValueError, TypeError) as e:
                print(f"Uyarı: Sayfa {page_index+1} içindeki bir resim (xref={img_info.get('xref')}) atlandı: {e}")
                continue
        
        print(f"DEBUG: Extracted {len(editable_images)} images from page {page_index}")
        return editable_images, None
    except Exception as e:
        error_msg = f"Sayfa {page_index+1} içinden resimler çıkarılırken hata oluştu: {e}"
        print(error_msg)
        traceback.print_exc()
        return [], error_msg

def add_image_to_page(doc, page_number, image_path, rect):
    if not doc or page_number is None:
        return False, "Resim eklemek için geçersiz belge veya sayfa numarası."
    try:
        page = doc.load_page(page_number)
        page.insert_image(rect, filename=image_path)
        return True, None
    except FileNotFoundError:
        return False, f"Resim dosyası bulunamadı: {image_path}"
    except Exception as e:
        print(f"HATA resim ekleniyor: {e}")
        traceback.print_exc()
        return False, f"Resim yerleştirme sırasında hata: {e}"

def delete_image_from_page(doc, image_obj: EditableImage):
    if not doc or image_obj.page_number is None:
        return False, "Resim silmek için geçersiz belge veya sayfa numarası."
    try:
        page = doc.load_page(image_obj.page_number)

        redact_rect = fitz.Rect(image_obj.bbox)
        if not redact_rect.is_empty and redact_rect.is_valid:
            page.add_redact_annot(redact_rect)
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE)
            doc.load_page(image_obj.page_number)
            return True, None
        else:
            return False, "Resim sınırlayıcı kutusu geçersiz."
    except Exception as e:
        print(f"HATA resim siliniyor: {e}")
        traceback.print_exc()
        return False, f"Resim silme sırasında hata: {e}"

def delete_shape_from_page(doc, shape_obj: EditableShape):
    if not doc or shape_obj.page_number is None:
        return False, "Şekil silmek için geçersiz belge veya sayfa numarası."
    try:
        page = doc.load_page(shape_obj.page_number)

        x0, y0, x1, y1 = shape_obj.bbox
        redact_rect = fitz.Rect(x0 - 20, y0 - 20, x1 + 20, y1 + 20)
        if not redact_rect.is_empty and redact_rect.is_valid:
            page.add_redact_annot(redact_rect)
            try:
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=True)
            except TypeError:
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
            doc.load_page(shape_obj.page_number)
            return True, None
        else:
            return False, "Şekil sınırlayıcı kutusu geçersiz."
    except Exception as e:
        print(f"HATA şekil siliniyor: {e}")
        traceback.print_exc()
        return False, f"Şekil silme sırasında hata: {e}"


def extract_editable_shapes(doc, page_index):
    editable_shapes = []
    if not doc or not (0 <= page_index < doc.page_count):
        return [], "Invalid document or page index for shape extraction."
    try:
        page = doc.load_page(page_index)
        drawings = page.get_drawings()
        for drawing in drawings:
            try:
                rect = drawing.get('rect')
                if not rect:
                    continue
                r = fitz.Rect(rect)
                if r.is_empty or not r.is_valid:
                    continue
                if r.width < 2 or r.height < 2:
                    continue

                bbox = (r.x0, r.y0, r.x1, r.y1)

                items = drawing.get('items', [])
                shape_type = EditableShape.SHAPE_RECTANGLE  # default
                for item in items:
                    if item[0] == 'c':  
                        shape_type = EditableShape.SHAPE_ELLIPSE
                        break

                raw_fill = drawing.get('fill')     
                raw_stroke = drawing.get('color')  # stroke colour
                raw_width = drawing.get('width', 1.0)

                is_transparent = (raw_fill is None)
                fill_color = raw_fill if raw_fill else (1.0, 1.0, 1.0)
                stroke_color = raw_stroke if raw_stroke else (0.0, 0.0, 0.0)
                stroke_width = float(raw_width) if raw_width else 1.0

                shape_obj = EditableShape(
                    shape_type=shape_type,
                    bbox=bbox,
                    fill_color=fill_color,
                    stroke_color=stroke_color,
                    stroke_width=stroke_width,
                    page_number=page_index,
                    is_new=False,
                    is_transparent=is_transparent
                )
                shape_obj.is_baked = True
                editable_shapes.append(shape_obj)
            except Exception as item_err:
                print(f"Warning: skipping drawing item: {item_err}")
                continue

        print(f"DEBUG: Extracted {len(editable_shapes)} shapes from page {page_index}")
        return editable_shapes, None
    except Exception as e:
        error_msg = f"Error extracting shapes from page {page_index}: {e}"
        print(error_msg)
        traceback.print_exc()
        return [], error_msg

# ── Page-snapshot registry ─────────────────────────────────────────────────────
# Stores the original content-stream bytes for each (doc_id, page_num) pair
# so that we can rebuild a page from scratch without destroying neighbours.
_page_snapshots: dict = {}  # {(doc_id, page_num): bytes}

def save_page_snapshot(doc, page_num: int, force: bool = False):
    """Save the current content streams of a page as a rebuild baseline.
    Call this once per page before any of our objects are written to it.
    If a snapshot already exists for this page it is NOT overwritten unless
    force=True.  This is critical: _load_page refreshes (called after edits)
    must not overwrite the clean baseline with already-baked content.
    """
    key = (id(doc), page_num)
    if key in _page_snapshots and not force:
        return  # already captured — don't overwrite with edited content
    try:
        page = doc.load_page(page_num)
        # Merge streams into one and save as bytes
        page.clean_contents()
        xrefs = page.get_contents()
        content = b""
        for xref in xrefs:
            raw = doc.xref_stream(xref)
            if raw:
                content += raw
        _page_snapshots[key] = content
    except Exception as e:
        print(f"Warning: could not save snapshot for page {page_num}: {e}")


def restore_page_from_snapshot(doc, page_num: int) -> bool:
    """Restore a page to its snapshot baseline (removes all content we added).
    Returns True on success.
    """
    key = (id(doc), page_num)
    if key not in _page_snapshots:
        return False
    try:
        content = _page_snapshots[key]
        page = doc.load_page(page_num)
        page.clean_contents()
        xrefs = page.get_contents()
        if xrefs:
            doc.update_stream(xrefs[0], content)
            # Remove extra xrefs if page had multiple streams
            for extra_xref in xrefs[1:]:
                try:
                    doc.xref_set_key(extra_xref, "Length", "0")
                except Exception:
                    pass
        else:
            # No content stream at all - insert one
            xref = doc._newXref()
            doc.update_stream(xref, content)
        return True
    except Exception as e:
        print(f"Warning: could not restore snapshot for page {page_num}: {e}")
        return False

def release_page_snapshots(doc):
    """Remove all snapshots for a document (call when document is closed)."""
    doc_id = id(doc)
    keys_to_remove = [k for k in _page_snapshots if k[0] == doc_id]
    for k in keys_to_remove:
        del _page_snapshots[k]

def _apply_single_object_to_page(doc, page, obj):
    """Write a single object to a page (additive only, no erasure)."""
    if isinstance(obj, EditableText):
        if obj.text:
            font_arg, error_msg = _get_font_args_for_pymupdf(obj)
            if error_msg:
                return False, error_msg
            lines = obj.text.split('\n')
            line_height = obj.font_size * 1.2
            for i, line in enumerate(lines):
                pos = fitz.Point(obj.x, obj.baseline + (i * line_height))
                page.insert_text(pos, line, fontsize=obj.font_size,
                                 color=obj.color, overlay=True, **font_arg)
    elif isinstance(obj, EditableImage):
        page.insert_image(obj.bbox, stream=obj.image_bytes, keep_proportion=False)
    elif isinstance(obj, EditableShape):
        rect = fitz.Rect(obj.bbox)
        shape = page.new_shape()
        if obj.shape_type == EditableShape.SHAPE_RECTANGLE:
            shape.draw_rect(rect)
        elif obj.shape_type == EditableShape.SHAPE_ELLIPSE:
            shape.draw_oval(rect)
        fill = tuple(float(c) for c in obj.fill_color) if not obj.is_transparent else None
        stroke = tuple(float(c) for c in obj.stroke_color)
        shape.finish(color=stroke, fill=fill, width=obj.stroke_width)
        shape.commit()
    return True, None

def rebuild_page(doc, page_num: int, all_texts, all_shapes, all_images,
                 exclude_obj=None):
    """Rebuild a page from its snapshot then re-apply all tracked objects.

    This is the core of the zero-deletion architecture:
    - Restore the page to the saved original content (erasing previous writes).
    - Re-apply ALL current editable objects for this page in order,
      EXCEPT exclude_obj (which will be re-written by the caller afterwards
      at its new/updated position — preventing double-writing).
    No redaction, no white boxes, no neighbour deletion.
    """
    if not restore_page_from_snapshot(doc, page_num):
        # No snapshot - just proceed without restoring (best effort)
        print(f"Warning: no snapshot for page {page_num}, skipping restore")
    try:
        page = doc.load_page(page_num)
        # Apply texts, then images, then shapes (shapes on top).
        # Skip exclude_obj so the caller can write it once at its new position.
        for obj in all_texts:
            if getattr(obj, 'page_number', None) == page_num and obj is not exclude_obj:
                _apply_single_object_to_page(doc, page, obj)
        for obj in all_images:
            if getattr(obj, 'page_number', None) == page_num and obj is not exclude_obj:
                _apply_single_object_to_page(doc, page, obj)
        for obj in all_shapes:
            if getattr(obj, 'page_number', None) == page_num and obj is not exclude_obj:
                _apply_single_object_to_page(doc, page, obj)
        return True, None
    except Exception as e:
        print(f"ERROR: rebuild_page failed for page {page_num}: {e}")
        traceback.print_exc()
        return False, str(e)

def apply_object_edit(doc, obj):
    """Write a single object to the PDF (additive only, no erasure).
    For moves and format changes, the caller must call rebuild_page() first
    with exclude_obj=obj to restore the page baseline without re-adding this
    object, then call this function to write it once at its new position.
    """
    if not doc or not hasattr(obj, 'page_number') or obj.page_number is None:
        return False, "Invalid object or page number."
    try:
        page = doc.load_page(obj.page_number)
        return _apply_single_object_to_page(doc, page, obj)
    except Exception as e:
        print(f"ERROR: An error occurred while applying object edit: {e}")
        traceback.print_exc()
        return False, f"Error while applying object edit: {e}"
    
def create_new_pdf():
    try:
        doc = fitz.open()
        doc.new_page(width=595, height=842)
        return doc, None
    except Exception as e:
        return None, f"Yeni PDF oluşturulurken hata: {e}"

def insert_blank_page(doc, page_index=None, width=None, height=None):
    try:
        if width is None or height is None:
            if doc.page_count > 0:
                first_page = doc[0]
                default_width = first_page.rect.width
                default_height = first_page.rect.height
            else:
                default_width = 595
                default_height = 842
            
            if width is None:
                width = default_width
            if height is None:
                height = default_height
        
        doc.new_page(width=width, height=height)
        return True, f"Sayfa sonuna eklendi (Sayfa {doc.page_count})"
    
    except Exception as e:
        return False, f"Sayfa eklenirken hata: {e}"

def merge_pdf_pages(target_doc, source_pdf_path, insert_position=None):
    try:
        source_doc = fitz.open(source_pdf_path)
        source_page_count = source_doc.page_count
        
        if source_page_count == 0:
            return False, "Kaynak PDF boş.", 0
        
        target_doc.insert_pdf(source_doc, from_page=0, to_page=source_page_count - 1)
        
        source_doc.close()
        
        return True, f"{source_page_count} sayfa başarıyla birleştirildi.", source_page_count
    
    except Exception as e:
        return False, f"PDF birleştirme sırasında hata: {e}", 0

def move_page(doc, from_index, to_index):
    try:
        if from_index < 0 or from_index >= doc.page_count:
            return False, "Geçersiz sayfa indeksi."
        
        if to_index < 0 or to_index >= doc.page_count:
            return False, "Geçersiz hedef indeksi."
        
        if from_index == to_index:
            return True, "Sayfa zaten bu konumda."
        
        doc.move_page(from_index, to_index)
        
        return True, f"Sayfa {from_index + 1} → {to_index + 1} konumuna taşındı."
    
    except Exception as e:
        return False, f"Sayfa taşıma sırasında hata: {e}"

def delete_page(doc, page_index):
    try:
        if not doc:
            return False, "Belge yok."
        
        if doc.page_count <= 1:
            return False, "Son sayfa silinemez. Belgede en az bir sayfa bulunmalıdır."
        
        if page_index < 0 or page_index >= doc.page_count:
            return False, f"Geçersiz sayfa indeksi: {page_index + 1}"
        
        doc.delete_page(page_index)
        return True, f"Sayfa {page_index + 1} silindi."
    
    except Exception as e:
        return False, f"Sayfa silme sırasında hata: {e}"