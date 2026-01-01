import gi
import os
from pathlib import Path

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk
import fitz


class PrintPreviewDialog(Gtk.Dialog):    
    def __init__(self, parent_window, pdf_doc, current_page_index):
        super().__init__(
            title="Print Preview - Word-Sys's PDF Editor",
            transient_for=parent_window,
            modal=True
        )
        
        self.pdf_doc = pdf_doc
        self.current_page_index = current_page_index
        self.zoom_level = 1.0
        self.displayed_page = 0
        
        self.set_default_size(900, 700)
        
        content_area = self.get_content_area()
        content_area.set_spacing(10)
        content_area.set_margin_start(12)
        content_area.set_margin_end(12)
        content_area.set_margin_top(12)
        content_area.set_margin_bottom(12)
        
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        self.page_label = Gtk.Label()
        self.prev_button = Gtk.Button(label="Prev", icon_name="go-previous-symbolic")
        self.prev_button.connect("clicked", self._on_prev_page)
        self.next_button = Gtk.Button(label="Next", icon_name="go-next-symbolic")
        self.next_button.connect("clicked", self._on_next_page)
        
        toolbar.append(self.prev_button)
        toolbar.append(self.page_label)
        toolbar.append(self.next_button)
        
        toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        
        zoom_out = Gtk.Button(label="Zoom -", icon_name="zoom-out-symbolic")
        zoom_out.connect("clicked", lambda w: self._set_zoom(self.zoom_level - 0.1))
        
        self.zoom_label = Gtk.Label(label="100%")
        
        zoom_in = Gtk.Button(label="Zoom +", icon_name="zoom-in-symbolic")
        zoom_in.connect("clicked", lambda w: self._set_zoom(self.zoom_level + 0.1))
        
        toolbar.append(zoom_out)
        toolbar.append(self.zoom_label)
        toolbar.append(zoom_in)
        
        content_area.append(toolbar)
        
        scroll = Gtk.ScrolledWindow(
            hexpand=True,
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC
        )
        
        self.preview_drawing = Gtk.DrawingArea(
            hexpand=True,
            vexpand=True
        )
        self.preview_drawing.set_draw_func(self._draw_preview)
        
        viewport = Gtk.Viewport()
        viewport.set_child(self.preview_drawing)
        scroll.set_child(viewport)
        
        content_area.append(scroll)
        
        settings_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        
        pages_frame = Gtk.Frame(label="Pages to Print")
        pages_inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_start=6, margin_end=6, margin_top=6, margin_bottom=6)
        
        self.all_pages_radio = Gtk.CheckButton(label="All Pages")
        self.all_pages_radio.set_active(True)
        self.all_pages_radio.connect("toggled", self._on_pages_changed)
        pages_inner.append(self.all_pages_radio)
        
        self.current_page_radio = Gtk.CheckButton(label="Current Page", group=self.all_pages_radio)
        self.current_page_radio.connect("toggled", self._on_pages_changed)
        pages_inner.append(self.current_page_radio)
        
        pages_frame.set_child(pages_inner)
        settings_box.append(pages_frame)
        
        settings_frame = Gtk.Frame(label="Print Settings")
        settings_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=6, margin_end=6, margin_top=6, margin_bottom=6)
        
        copies_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        copies_label = Gtk.Label(label="Copies:")
        self.copies_spin = Gtk.SpinButton.new_with_range(1, 100, 1)
        self.copies_spin.set_value(1)
        copies_box.append(copies_label)
        copies_box.append(self.copies_spin)
        settings_inner.append(copies_box)
        
        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        color_label = Gtk.Label(label="Color Mode:")
        self.color_combo = Gtk.ComboBoxText()
        self.color_combo.append("color", "Color")
        self.color_combo.append("grayscale", "Grayscale")
        self.color_combo.set_active_id("color")
        color_box.append(color_label)
        color_box.append(self.color_combo)
        settings_inner.append(color_box)
        
        orientation_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        orientation_label = Gtk.Label(label="Orientation:")
        self.orientation_combo = Gtk.ComboBoxText()
        self.orientation_combo.append("portrait", "Portrait")
        self.orientation_combo.append("landscape", "Landscape")
        self.orientation_combo.set_active_id("portrait")
        orientation_box.append(orientation_label)
        orientation_box.append(self.orientation_combo)
        settings_inner.append(orientation_box)
        
        settings_frame.set_child(settings_inner)
        settings_box.append(settings_frame)
        
        content_area.append(settings_box)
        
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("Print", Gtk.ResponseType.OK)
        
        self.set_default_response(Gtk.ResponseType.OK)
        
        self._update_preview()
    
    def _on_prev_page(self, button):
        if self.displayed_page > 0:
            self.displayed_page -= 1
            self._update_preview()
    
    def _on_next_page(self, button):
        if self.displayed_page < self.pdf_doc.page_count - 1:
            self.displayed_page += 1
            self._update_preview()
    
    def _on_pages_changed(self, radio):
        if radio.get_active():
            if radio == self.all_pages_radio:
                self.prev_button.set_sensitive(True)
                self.next_button.set_sensitive(True)
            else:
                self.prev_button.set_sensitive(False)
                self.next_button.set_sensitive(False)
                self.displayed_page = self.current_page_index
            self._update_preview()
    
    def _set_zoom(self, level):
        self.zoom_level = max(0.1, min(level, 3.0))
        self.zoom_label.set_text(f"{int(self.zoom_level * 100)}%")
        self.preview_drawing.queue_draw()
    
    def _update_preview(self):
        page_count = self.pdf_doc.page_count
        if self.all_pages_radio.get_active():
            self.page_label.set_text(f"Page {self.displayed_page + 1} of {page_count}")
        else:
            self.page_label.set_text(f"Page {self.current_page_index + 1} of {page_count} (Current)")
        
        self.prev_button.set_sensitive(self.displayed_page > 0 and self.all_pages_radio.get_active())
        self.next_button.set_sensitive(self.displayed_page < page_count - 1 and self.all_pages_radio.get_active())
        
        self.preview_drawing.queue_draw()
    
    def _draw_preview(self, drawing_area, cr, width, height):
        try:
            page = self.pdf_doc[self.displayed_page]
            
            pix = page.get_pixmap(matrix=fitz.Matrix(self.zoom_level, self.zoom_level))
            img_data = pix.tobytes("ppm")
            
            from gi.repository import GdkPixbuf
            loader = GdkPixbuf.PixbufLoader.new()
            loader.write(img_data)
            loader.close()
            pixbuf = loader.get_pixbuf()
            
            if pixbuf:
                img_width = pixbuf.get_width()
                img_height = pixbuf.get_height()
                
                x = max(0, (width - img_width) / 2)
                y = max(0, (height - img_height) / 2)
                
                cr.set_source_rgb(1, 1, 1)
                cr.paint()
                
                cr.translate(x, y)
                Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
                cr.paint()
                
        except Exception as e:
            print(f"Error drawing preview: {e}")
            cr.set_source_rgb(1, 1, 1)
            cr.paint()
    
    def get_print_settings(self):
        return {
            'all_pages': self.all_pages_radio.get_active(),
            'current_page': self.current_page_index if self.current_page_radio.get_active() else None,
            'copies': int(self.copies_spin.get_value()),
            'color_mode': self.color_combo.get_active_id(),
            'orientation': self.orientation_combo.get_active_id(),
        }


def show_print_dialog(parent_window, pdf_doc, current_page_index):
    dialog = PrintPreviewDialog(parent_window, pdf_doc, current_page_index)
    response = dialog.run()
    
    result = None
    if response == Gtk.ResponseType.OK:
        result = dialog.get_print_settings()
    
    dialog.destroy()
    return result


def print_pdf(pdf_doc, settings):
    try:  
        if settings['all_pages']:
            pages_info = f"All {pdf_doc.page_count} pages"
        else:
            pages_info = f"Page {settings['current_page'] + 1}"
        
        message = (
            f"Print Settings:\n"
            f"Pages: {pages_info}\n"
            f"Copies: {settings['copies']}\n"
            f"Color Mode: {settings['color_mode']}\n"
            f"Orientation: {settings['orientation']}"
        )
        
        print(f"[PRINT] {message}")
        return True, message
        
    except Exception as e:
        return False, str(e)
