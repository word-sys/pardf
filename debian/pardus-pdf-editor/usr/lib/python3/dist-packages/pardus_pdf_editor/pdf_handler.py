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

from .models import EditableText, FLAG_BOLD, FLAG_ITALIC, EditableImage
from .utils import find_specific_font_variant, get_default_unicode_font_path

_surface_cache = {"surface": None, "data_ref": None}

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
            data = bgra_data.data # Buffer protocol
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
        text_dict = page.get_text("dict", flags=11)

        for block in text_dict.get("blocks", []):
            if block.get("type") == 0:
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
                            baseline=span.get("origin", (0, bbox[3]))[1]
                        )
                        editable.page_number = page_index
                        editable_texts.append(editable)
        return editable_texts, None
    except Exception as e:
        error_msg = f"Error extracting text from page {page_index}: {e}"
        print(error_msg)
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
        print("Error: Invalid document or page number for editing.")
        return False, "Invalid document or page number for editing."

    print(f"Apply edit for: '{text_obj.font_family_original}', Base: '{text_obj.font_family_base}', B:{text_obj.is_bold}, I:{text_obj.is_italic}")

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
        internal_font_name = f"Pardus_{safe_family_name}_{style_suffix}"
        font_arg = {"fontfile": font_to_embed_path, "fontname": internal_font_name}
        print(f"Using specific TTF: {font_to_embed_path} as '{internal_font_name}' for B:{text_obj.is_bold}, I:{text_obj.is_italic}")
    else:
        generic_unicode_font = get_default_unicode_font_path()
        if generic_unicode_font:
            internal_font_name = "PardusEditFont_GenericUnicode"
            font_arg = {"fontfile": generic_unicode_font, "fontname": internal_font_name}
            print(f"Warning: Could not find specific TTF for '{text_obj.font_family_base}' (B:{text_obj.is_bold}, I:{text_obj.is_italic}). Using generic fallback: {generic_unicode_font}. Style might not match perfectly.")
        else:
            base14_name = text_obj.pdf_fontname_base14
            if text_obj.is_bold and text_obj.is_italic: base14_name += "bo"
            elif text_obj.is_bold: base14_name += "b"
            elif text_obj.is_italic: base14_name += "i"
            font_arg = {"fontname": base14_name }
            print(f"CRITICAL WARNING: No TTF found. Falling back to Base 14 font: '{base14_name}'. Unicode may fail, style approximate.")
            if any(ord(c) > 127 for c in new_text):
                 return False, "Cannot save non-ASCII text: No suitable Unicode font found and Base14 fallback selected."

    try:
        page = doc.load_page(text_obj.page_number)
        if not text_obj.is_new and text_obj.bbox:
            redact_rect = fitz.Rect(text_obj.bbox)
            redact_rect.normalize()
            page_rect = page.rect
            cover_rect = redact_rect.intersect(page_rect)
            if not cover_rect.is_empty and cover_rect.is_valid:
                annot = page.add_redact_annot(cover_rect)
                if annot:
                    page.apply_redactions()
                    page = doc.load_page(text_obj.page_number)
                else:
                    print("Warning: Could not add redaction annotation.")

        if new_text:
            insert_point = fitz.Point(text_obj.x, text_obj.baseline)
            fontsize = text_obj.font_size
            color = text_obj.color
            lines = new_text.split('\n')
            line_height_factor = 1.2
            line_height = fontsize * line_height_factor
            current_y = text_obj.baseline

            for i, line_text in enumerate(lines):
                line_point = fitz.Point(text_obj.x, current_y)
                rc = page.insert_text(
                    line_point,
                    line_text,
                    fontsize=fontsize,
                    color=color,
                    rotate=0,
                    overlay=True,
                    **font_arg
                )
                if rc < 0:
                    print(f"Warning: PyMuPDF insert_text returned error {rc} for line: {line_text}")
                current_y += line_height

        text_obj.text = new_text
        text_obj.modified = True
        text_obj.is_new = False
        return True, None
    except Exception as e:
        print(f"ERROR applying text edit: {e}")
        traceback.print_exc()
        return False, f"Error during text application: {e}"
    
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

def export_pdf_as_docx(doc, source_pdf_path, output_docx_path):
    libreoffice_executable = shutil.which('libreoffice')
    if not libreoffice_executable:
        libreoffice_executable = shutil.which('soffice')
    if not libreoffice_executable:
        return False, "LibreOffice Not Found. Install 'libreoffice-writer' to enable DOCX export."
    print(f"DEBUG [DOCX Export]: Using LibreOffice executable: {libreoffice_executable}")

    temp_pdf_path = None
    try:
        fd, temp_pdf_path = tempfile.mkstemp(suffix=".pdf", prefix="pardus_export_")
        os.close(fd)
        print(f"DEBUG [DOCX Export]: Saving document state to temporary file: {temp_pdf_path}")

        save_success, save_msg = save_document(doc, temp_pdf_path, incremental=False)
        if not save_success:
            if os.path.exists(temp_pdf_path): os.unlink(temp_pdf_path)
            return False, f"Failed to save temporary PDF for export: {save_msg}"
        
        if not os.path.exists(temp_pdf_path) or os.path.getsize(temp_pdf_path) == 0:
            print(f"ERROR [DOCX Export]: Temporary PDF '{temp_pdf_path}' was not created or is empty.")
            if os.path.exists(temp_pdf_path): os.unlink(temp_pdf_path)
            return False, "Failed to create a valid temporary PDF for export."

        print(f"DEBUG [DOCX Export]: Temp PDF Path = {temp_pdf_path} (Size: {os.path.getsize(temp_pdf_path)} bytes)")
        temp_pdf_path_obj = Path(temp_pdf_path)
        temp_pdf_name_no_ext = temp_pdf_path_obj.stem

        final_output_dir = Path(output_docx_path).parent
        final_output_dir.mkdir(parents=True, exist_ok=True)
        print(f"DEBUG [DOCX Export]: Final DOCX Output Dir = {final_output_dir}")
        print(f"DEBUG [DOCX Export]: Desired Final DOCX Path = {output_docx_path}")

        python_cwd = Path(os.getcwd())
        expected_output_in_python_cwd = python_cwd / f"{temp_pdf_name_no_ext}.docx"
        print(f"DEBUG [DOCX Export]: Python's Current Working Directory (for output): {python_cwd}")
        print(f"DEBUG [DOCX Export]: Expected DOCX in Python CWD: {expected_output_in_python_cwd}")
        
        if os.path.exists(expected_output_in_python_cwd):
            print(f"DEBUG [DOCX Export]: Removing leftover in CWD: {expected_output_in_python_cwd}")
            os.remove(expected_output_in_python_cwd)
        if os.path.exists(output_docx_path):
            print(f"DEBUG [DOCX Export]: Removing leftover final target: {output_docx_path}")
            os.remove(output_docx_path)

        command = [
            libreoffice_executable,
            '--headless',
            '--invisible',
            '--nologo',
            '--infilter=writer_pdf_import',
            '--convert-to', 'docx',
            '--outdir', str(temp_pdf_path_obj.parent),
            str(temp_pdf_path)
        ]

        expected_output_location = temp_pdf_path_obj.parent / f"{temp_pdf_name_no_ext}.docx"

        print(f"DEBUG [DOCX Export]: Expected DOCX at: {expected_output_location}")
        if os.path.exists(expected_output_location):
            print(f"DEBUG [DOCX Export]: Removing leftover: {expected_output_location}")
            os.remove(expected_output_location)

        print(f"DEBUG [DOCX Export]: Running command: {' '.join(command)}")

        current_env = os.environ.copy()
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            env=current_env
        )

        print(f"DEBUG [DOCX Export]: LibreOffice Return Code: {process.returncode}")
        if process.stdout:
            print(f"DEBUG [DOCX Export]: LibreOffice stdout:\n---\n{process.stdout.strip()}\n---")
        if process.stderr:
            print(f"DEBUG [DOCX Export]: LibreOffice stderr:\n---\n{process.stderr.strip()}\n---")

        if "0xc10" in process.stderr or "SfxBaseModel::impl_store" in process.stderr:
            error_msg = f"LibreOffice I/O Write Error during conversion. Stderr: {process.stderr.strip()}"
            if "no export filter" in process.stderr.lower():
                 error_msg += " (Also saw 'no export filter' - check LO installation and write permissions)"
            print(f"ERROR [DOCX Export]: {error_msg}")
            return False, error_msg
        
        if ("no export filter" in process.stderr.lower() or "no export filter" in process.stdout.lower()) and process.returncode != 0 :
            error_msg = "LibreOffice reported: No export filter for DOCX found. Ensure 'libreoffice-writer' is fully installed."
            print(f"ERROR [DOCX Export]: {error_msg}")
            return False, error_msg
            
        if process.returncode != 0:
            error_msg = f"LibreOffice conversion failed (code {process.returncode}).\nError:\n{process.stderr or process.stdout}"
            print(f"ERROR [DOCX Export]: {error_msg}")
            return False, error_msg

        if os.path.exists(expected_output_location):
            print(f"DEBUG [DOCX Export]: Found DOCX at: {expected_output_location}")
            try:
                Path(output_docx_path).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(expected_output_location), str(output_docx_path))
                print(f"DEBUG [DOCX Export]: Moved DOCX to final destination: {output_docx_path}")
                return True, None
            except Exception as move_e:
                error_msg = f"Found converted DOCX ({expected_output_location}) but failed to move to {output_docx_path}: {move_e}"
                print(f"ERROR [DOCX Export]: {error_msg}")
                return False, error_msg
        else:
            error_msg = f"LibreOffice conversion seemed to finish, but the output DOCX ({expected_output_location}) could not be located."
            print(f"ERROR [DOCX Export]: {error_msg}")
            return False, error_msg

    except subprocess.TimeoutExpired:
        return False, "LibreOffice conversion timed out (took longer than 120 seconds)."
    except Exception as e:
        print(f"ERROR [DOCX Export]: General exception during DOCX export process: {e}")
        return False, f"Error during DOCX export process: {e}"
    finally:
        if temp_pdf_path and os.path.exists(temp_pdf_path):
            try:
                os.unlink(temp_pdf_path)
                print(f"DEBUG [DOCX Export]: Cleaned up temp PDF: {temp_pdf_path}")
            except Exception as unlink_e:
                print(f"Warning: Could not delete temporary file {temp_pdf_path}: {unlink_e}")

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
        
        for img_info in image_info_list:
            try:
                bbox = img_info['bbox']
                xref = img_info['xref']

                rect = fitz.Rect(bbox)
                if rect.is_empty or not rect.is_valid:
                    continue

                image_bytes = doc.extract_image(xref)["image"]
                
                image_obj = EditableImage(
                    bbox=bbox,
                    page_number=page_index,
                    xref=xref,
                    image_bytes=image_bytes
                )
                editable_images.append(image_obj)
            except (ValueError, TypeError) as e:
                print(f"Uyarı: Sayfa {page_index+1} içindeki bir resim (xref={img_info.get('xref')}) atlandı: {e}")
                continue
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
            doc.load_page(image_obj.page_number) # Değişikliklerin yansıması için sayfayı yeniden yükle
            return True, None
        else:
            return False, "Resim sınırlayıcı kutusu geçersiz."
    except Exception as e:
        print(f"HATA resim siliniyor: {e}")
        traceback.print_exc()
        return False, f"Resim silme sırasında hata: {e}"

def apply_object_edit(doc, obj):
    if not doc or not hasattr(obj, 'page_number') or obj.page_number is None:
        return False, "Geçersiz nesne veya sayfa numarası."

    try:
        page = doc.load_page(obj.page_number)

        original_bbox_to_clear = obj.original_bbox

        if original_bbox_to_clear:
            redact_rect = fitz.Rect(original_bbox_to_clear)
            if not redact_rect.is_empty and redact_rect.is_valid:
                img_flag = fitz.PDF_REDACT_IMAGE_REMOVE if isinstance(obj, EditableImage) else fitz.PDF_REDACT_IMAGE_NONE
                page.add_redact_annot(redact_rect)
                page.apply_redactions(images=img_flag)
                page = doc.load_page(obj.page_number)

        if isinstance(obj, EditableText):
            if obj.text:
                font_path = find_specific_font_variant(obj.font_family_base, obj.is_bold, obj.is_italic)
                if not font_path:
                    font_path = get_default_unicode_font_path()
                if not font_path:
                    return False, "Yazmak için uygun font bulunamadı."

                estimated_baseline = obj.y + (obj.font_size * 0.9)

                lines = obj.text.split('\n')
                line_height = obj.font_size * 1.2

                for i, line in enumerate(lines):
                    pos = fitz.Point(obj.x, estimated_baseline + (i * line_height))
                    page.insert_text(pos,
                                    line,
                                    fontsize=obj.font_size,
                                    color=obj.color,
                                    fontfile=font_path,
                                    overlay=True)

        elif isinstance(obj, EditableImage):
            page.insert_image(obj.bbox, stream=obj.image_bytes)

        obj.original_bbox = obj.bbox
        obj.modified = False
        if isinstance(obj, EditableText):
            obj.is_new = False

        return True, None

    except Exception as e:
        print(f"HATA: Nesne düzenlemesi uygulanırken hata oluştu: {e}")
        traceback.print_exc()
        return False, f"Nesne düzenlemesi uygulanırken hata: {e}"
