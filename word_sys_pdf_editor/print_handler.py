import gi
import os
import tempfile
import traceback

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf
import cairo
import fitz
from .i18n import _


def print_document(parent_window, doc):
    if not doc or doc.page_count == 0:
        return False, _("print_no_doc")

    print_op = Gtk.PrintOperation.new()

    if hasattr(parent_window, '_print_settings') and parent_window._print_settings:
        print_op.set_print_settings(parent_window._print_settings)
    if hasattr(parent_window, '_page_setup') and parent_window._page_setup:
        print_op.set_default_page_setup(parent_window._page_setup)

    print_op.set_n_pages(doc.page_count)
    print_op.set_use_full_page(False)
    print_op.set_embed_page_setup(True)
    print_op.set_job_name("word-sys PDF Print")

    if hasattr(parent_window, 'current_page_index'):
        print_op.set_current_page(parent_window.current_page_index)

    def on_draw_page(operation, print_context, page_nr):
        try:
            cr = print_context.get_cairo_context()

            print_width = print_context.get_width()
            print_height = print_context.get_height()

            page = doc.load_page(page_nr)
            page_rect = page.rect
            pdf_w = page_rect.width
            pdf_h = page_rect.height

            if pdf_w <= 0 or pdf_h <= 0:
                return

            scale_x = print_width / pdf_w
            scale_y = print_height / pdf_h
            scale = min(scale_x, scale_y)
            render_scale = scale * 2.0
            mat = fitz.Matrix(render_scale, render_scale)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_w = pix.width
            img_h = pix.height
            samples = pix.samples

            import array
            stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_ARGB32, img_w)
            buf = bytearray(stride * img_h)

            src_stride = pix.stride
            for y_row in range(img_h):
                src_offset = y_row * src_stride
                dst_offset = y_row * stride
                for x_col in range(img_w):
                    si = src_offset + x_col * 3
                    di = dst_offset + x_col * 4
                    r = samples[si]
                    g = samples[si + 1]
                    b = samples[si + 2]
                    buf[di] = b
                    buf[di + 1] = g
                    buf[di + 2] = r
                    buf[di + 3] = 255  # alpha

            surface = cairo.ImageSurface.create_for_data(
                buf, cairo.FORMAT_ARGB32, img_w, img_h, stride
            )

            final_w = pdf_w * scale
            final_h = pdf_h * scale
            offset_x = (print_width - final_w) / 2
            offset_y = (print_height - final_h) / 2

            cr.save()
            cr.translate(offset_x, offset_y)
            cr.scale(final_w / img_w, final_h / img_h)
            cr.set_source_surface(surface, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_BILINEAR)
            cr.paint()
            cr.restore()

        except Exception as e:
            print(f"ERROR in print draw-page for page {page_nr}: {e}")
            traceback.print_exc()

    def on_done(operation, result):
        if result == Gtk.PrintOperationResult.ERROR:
            print("PRINT ERROR")
        elif result == Gtk.PrintOperationResult.APPLY:
            parent_window._print_settings = operation.get_print_settings()
            parent_window._page_setup = operation.get_default_page_setup()
            print(f"[PRINT] {_('print_success')}")

    print_op.connect("draw-page", on_draw_page)
    print_op.connect("done", on_done)

    try:
        result = print_op.run(
            Gtk.PrintOperationAction.PRINT_DIALOG,
            parent_window
        )

        if result == Gtk.PrintOperationResult.ERROR:
            return False, _("print_error_occurred")
        elif result == Gtk.PrintOperationResult.CANCEL:
            return False, None  
        elif result == Gtk.PrintOperationResult.APPLY:
            return True, _("print_success")
        elif result == Gtk.PrintOperationResult.IN_PROGRESS:
            return True, _("print_in_progress")
        else:
            return True, None

    except Exception as e:
        print(f"ERROR starting print operation: {e}")
        traceback.print_exc()
        return False, _("print_start_failed").format(e)
