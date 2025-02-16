#!/usr/bin/env python3
import sys
import gi
import pypdf
import cairo
import tempfile
from pathlib import Path
from PIL import Image
import io
import fitz
import os
import numpy as np
import subprocess
import shutil

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gio, GLib, Adw, Gdk, GdkPixbuf, Pango, GObject, PangoCairo

class PdfPage(GObject.GObject):
    def __init__(self, index, thumbnail):
        super().__init__()
        self.index = index
        self.thumbnail = thumbnail

class PageThumbnailFactory(Gtk.SignalListItemFactory):
    def __init__(self):
        super().__init__()
        self.connect("setup", self._on_setup)
        self.connect("bind", self._on_bind)

    def _on_setup(self, factory, list_item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        image = Gtk.Picture()
        label = Gtk.Label()
        box.append(image)
        box.append(label)
        list_item.set_child(box)

    def _on_bind(self, factory, list_item):
        box = list_item.get_child()
        picture = box.get_first_child()
        label = box.get_last_child()
        pdf_page = list_item.get_item()

        texture = Gdk.Texture.new_for_pixbuf(pdf_page.thumbnail)
        picture.set_paintable(texture)
        label.set_text(f"Page {pdf_page.index + 1}")
        
class PdfEditorWindow(Gtk.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_title("Pardus PDF Editor")
        self.set_default_size(1200, 800)

        self.current_file = None
        self.pdf_document = None
        self.current_page = 0
        self.zoom_level = 1.0
        self.pages_model = Gio.ListStore(item_type=PdfPage)
        self.doc = None

        self.setup_ui()
        self.setup_zoom_controller()
        self.setup_text_editing()

        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect('drop', self.on_drop)
        self.add_controller(drop_target)

        export_action = Gio.SimpleAction.new('export_as', None)
        export_action.connect('activate', self.on_export_as)
        self.add_action(export_action)

    def setup_ui(self):
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(self.main_box)
        
        self.create_header_bar()
        
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.main_box.append(self.paned)

        self.create_sidebar()
        self.create_main_content()
        self.create_status_bar()
        
    def create_file_dialog(self, title, action, parent=None):
        dialog = Gtk.FileChooserDialog(
            title=title,
            parent=parent or self,
            action=action,
        )
        dialog.add_buttons(
            "_Cancel", Gtk.ResponseType.CANCEL,
            "_Open" if action == Gtk.FileChooserAction.OPEN else "_Save", 
            Gtk.ResponseType.ACCEPT,
        )
        
        dialog.set_modal(True)
        
        return dialog
    
    def create_text_toolbar(self):
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        font_store = Gtk.ListStore(str)
        for font in ["Sans", "Serif", "Monospace"]:
            font_store.append([font])
        
        font_combo = Gtk.ComboBox(model=font_store)
        cell = Gtk.CellRendererText()
        font_combo.pack_start(cell, True)
        font_combo.add_attribute(cell, "text", 0)
        font_combo.set_active(0)
        toolbar.append(font_combo)
        
        size_spin = Gtk.SpinButton.new_with_range(8, 72, 1)
        size_spin.set_value(12)
        toolbar.append(size_spin)
        
        color_button = Gtk.ColorButton()
        toolbar.append(color_button)
        
        return toolbar

    def create_header_bar(self):
        header = Gtk.HeaderBar()
        self.set_titlebar(header)

        open_button = Gtk.Button(label="Open")
        open_button.connect("clicked", self.on_open_clicked)
        header.pack_start(open_button)

        save_button = Gtk.Button(label="Save")
        save_button.connect("clicked", self.on_save_clicked)
        header.pack_start(save_button)

        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        header.pack_end(menu_button)

        menu = Gio.Menu()
        
        file_menu = Gio.Menu()
        file_menu.append("Save As", "win.save_as")
        file_menu.append("Export", "win.export_as")
        menu.append_submenu("File", file_menu)
        
        edit_menu = Gio.Menu()
        edit_menu.append("Preferences", "win.preferences")
        menu.append_submenu("Edit", edit_menu)
        
        popover = Gtk.PopoverMenu()
        popover.set_menu_model(menu)
        menu_button.set_popover(popover)

    def create_sidebar(self):
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sidebar_box.set_size_request(200, -1)
        
        thumbnails_label = Gtk.Label(label="Pages")
        thumbnails_label.add_css_class('heading')
        sidebar_box.append(thumbnails_label)
        
        factory = PageThumbnailFactory()
        self.thumbnails_list = Gtk.ListView.new(
            Gtk.NoSelection(),
            factory
        )
        self.thumbnails_list.set_model(Gtk.SingleSelection.new(self.pages_model))
        
        thumbnails_scroll = Gtk.ScrolledWindow()
        thumbnails_scroll.set_child(self.thumbnails_list)
        thumbnails_scroll.set_vexpand(True)
        sidebar_box.append(thumbnails_scroll)
        
        tools_label = Gtk.Label(label="Tools")
        tools_label.add_css_class('heading')
        sidebar_box.append(tools_label)
        
        tools_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        tools_box.add_css_class('linked')
        
        tools = [
            ("Add Text", "insert-text-symbolic", self.on_add_text),
            ("Add Image", "insert-image-symbolic", self.on_add_image),
            ("Add Signature", "document-sign-symbolic", self.on_add_signature),
            ("Highlight", "marker-symbolic", self.on_highlight),
            ("Comment", "comment-symbolic", self.on_add_comment)
        ]
        
        for tool_name, icon_name, callback in tools:
            button = Gtk.Button()
            button.set_child(Gtk.Box(spacing=6))
            button.get_child().append(Gtk.Image.new_from_icon_name(icon_name))
            button.get_child().append(Gtk.Label(label=tool_name))
            button.connect("clicked", callback)
            tools_box.append(button)
        
        sidebar_box.append(tools_box)
        self.paned.set_start_child(sidebar_box)

    def create_main_content(self):
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)    
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .toolbar {
                padding: 6px;
                background: @theme_bg_color;
                border-bottom: 1px solid @borders;
            }
            .pdf-view {
                background: #f0f0f0;
            }
            .tool-button {
                padding: 6px 12px;
            }
            .statusbar {
                padding: 4px 8px;
                background: @theme_bg_color;
                border-top: 1px solid @borders;
            }
        """)
        
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        
        toolbar = self.create_toolbar()
        content_box.append(toolbar)
        
        self.pdf_scroll = Gtk.ScrolledWindow()
        self.pdf_scroll.set_hexpand(True)
        self.pdf_scroll.set_vexpand(True)
        self.pdf_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        
        viewport = Gtk.Viewport()
        self.pdf_scroll.set_child(viewport)
        
        self.pdf_view = Gtk.DrawingArea()
        self.pdf_view.set_draw_func(self.draw_pdf)
        self.pdf_view.add_css_class('pdf-view')
        viewport.set_child(self.pdf_view)
        
        content_box.append(self.pdf_scroll)
        self.paned.set_end_child(content_box)

    def create_toolbar(self):
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.add_css_class('toolbar')
        
        zoom_out_button = Gtk.Button.new_from_icon_name("zoom-out-symbolic")
        zoom_out_button.connect("clicked", self.on_zoom_out)
        toolbar.append(zoom_out_button)
        
        self.zoom_label = Gtk.Label(label="100%")
        toolbar.append(self.zoom_label)
        
        zoom_in_button = Gtk.Button.new_from_icon_name("zoom-in-symbolic")
        zoom_in_button.connect("clicked", self.on_zoom_in)
        toolbar.append(zoom_in_button)
        
        toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        
        prev_button = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        prev_button.connect("clicked", self.on_prev_page)
        toolbar.append(prev_button)
        
        self.page_label = Gtk.Label()
        toolbar.append(self.page_label)
        
        next_button = Gtk.Button.new_from_icon_name("go-next-symbolic")
        next_button.connect("clicked", self.on_next_page)
        toolbar.append(next_button)
        
        toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        
        self.font_store = Gtk.ListStore(str)
        for font in ["Sans", "Serif", "Monospace"]:
            self.font_store.append([font])
        
        self.font_combo = Gtk.ComboBox(model=self.font_store)
        cell = Gtk.CellRendererText()
        self.font_combo.pack_start(cell, True)
        self.font_combo.add_attribute(cell, "text", 0)
        self.font_combo.set_active(0)
        self.font_combo.connect("changed", self.on_font_changed)
        toolbar.append(self.font_combo)
        
        self.font_size_spin = Gtk.SpinButton.new_with_range(8, 72, 1)
        self.font_size_spin.set_value(12)
        self.font_size_spin.connect("value-changed", self.on_font_size_changed)
        toolbar.append(self.font_size_spin)
        
        self.color_button = Gtk.ColorButton()
        self.color_button.connect("color-set", self.on_color_changed)
        toolbar.append(self.color_button)
        
        return toolbar

    def create_status_bar(self):
        status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        status_bar.add_css_class('statusbar')
        
        self.status_label = Gtk.Label(label="Ready")
        status_bar.append(self.status_label)
        
        self.main_box.append(status_bar)

    def on_export_as(self, action, param):
        if not self.doc:
            self.status_label.set_text("No document to export")
            return
            
        dialog = self.create_file_dialog(
            "Export As",
            Gtk.FileChooserAction.SAVE
        )
        
        filter_pdf = Gtk.FileFilter()
        filter_pdf.set_name("PDF files")
        filter_pdf.add_mime_type("application/pdf")
        filter_pdf.add_pattern("*.pdf")
        dialog.add_filter(filter_pdf)
        
        filter_docx = Gtk.FileFilter()
        filter_docx.set_name("Word Documents (DOCX)")
        filter_docx.add_mime_type("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        filter_docx.add_pattern("*.docx")
        dialog.add_filter(filter_docx)
        
        filter_txt = Gtk.FileFilter()
        filter_txt.set_name("Text Files")
        filter_txt.add_mime_type("text/plain")
        filter_txt.add_pattern("*.txt")
        dialog.add_filter(filter_txt)
        
        dialog.connect("response", self.on_export_response)
        dialog.present()

    def on_export_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            path = file.get_path()
            filter_info = dialog.get_filter()
            filter_name = filter_info.get_name() if filter_info else "PDF files"
            
            if filter_name == "Word Documents (DOCX)" or path.lower().endswith('.docx'):
                self.export_as_docx(path)
            elif filter_name == "Text Files" or path.lower().endswith('.txt'):
                self.export_as_text(path)
            else:
                if not path.lower().endswith('.pdf'):
                    path += '.pdf'
                self.doc.save(path)
                self.status_label.set_text(f"Document exported as PDF: {os.path.basename(path)}")
        
        dialog.destroy()

    def export_as_docx(self, output_path):
        try:
            if not output_path.lower().endswith('.docx'):
                output_path += '.docx'
                
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_pdf:
                temp_pdf_path = temp_pdf.name
                self.doc.save(temp_pdf_path)
            
            if shutil.which('libreoffice'):
                subprocess.run([
                    'libreoffice', '--headless', '--convert-to', 'docx',
                    '--outdir', os.path.dirname(output_path),
                    temp_pdf_path
                ], check=True)
                
                converted_file = os.path.join(
                    os.path.dirname(output_path),
                    os.path.basename(temp_pdf_path).replace('.pdf', '.docx')
                )
                if os.path.exists(converted_file):
                    shutil.move(converted_file, output_path)
                    self.status_label.set_text(f"Document exported as DOCX: {os.path.basename(output_path)}")
                else:
                    self.status_label.set_text("Failed to convert to DOCX")
            else:
                self.show_error_dialog("LibreOffice not found. It's required for DOCX conversion.")
                
            if os.path.exists(temp_pdf_path):
                os.unlink(temp_pdf_path)
                
        except Exception as e:
            self.show_error_dialog(f"Error exporting as DOCX: {str(e)}")

    def export_as_text(self, output_path):
        try:
            if not output_path.lower().endswith('.txt'):
                output_path += '.txt'
                
            with open(output_path, 'w', encoding='utf-8') as txt_file:
                for page_num in range(self.doc.page_count):
                    page = self.doc[page_num]
                    text = page.get_text("text")
                    txt_file.write(f"--- Page {page_num + 1} ---\n\n")
                    txt_file.write(text)
                    txt_file.write("\n\n")
                    
            self.status_label.set_text(f"Document exported as text: {os.path.basename(output_path)}")
        except Exception as e:
            self.show_error_dialog(f"Error exporting as text: {str(e)}")

    def update_text_controls(self, text):
        for i, row in enumerate(self.font_store):
            if row[0] == text.font_family:
                self.font_combo.set_active(i)
                break
        
        self.font_size_spin.set_value(text.font_size)
        
        rgba = Gdk.RGBA()
        if isinstance(text.color, (tuple, list)):
            rgba.red, rgba.green, rgba.blue = text.color
        else:
            rgba.red = rgba.green = rgba.blue = float(text.color)
        rgba.alpha = 1.0
        self.color_button.set_rgba(rgba)

    def draw_pdf(self, area, cr, width, height):
        if not self.doc or self.current_page >= self.doc.page_count:
            return
        
        page = self.doc[self.current_page]
        zoom_matrix = fitz.Matrix(self.zoom_level, self.zoom_level)
        pix = page.get_pixmap(matrix=zoom_matrix, alpha=False)
        
        img = self.pixmap_to_cairo_surface(pix)
        cr.set_source_surface(img, 0, 0)
        cr.paint()
        
        if self.selected_text and self.selected_text.selected:
            cr.save()
            cr.set_source_rgba(0.2, 0.4, 1.0, 0.2)
            
            if self.selected_text.bbox:
                x1, y1, x2, y2 = self.selected_text.bbox
                cr.rectangle(
                    x1 * self.zoom_level,
                    y1 * self.zoom_level,
                    (x2 - x1) * self.zoom_level,
                    (y2 - y1) * self.zoom_level
                )
                cr.fill()
            cr.restore()

    def pixmap_to_cairo_surface(self, pix):
        import numpy as np
        
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        
        rgba = np.zeros((pix.height, pix.width, 4), dtype=np.uint8)
        rgba[:, :, 0] = img[:, :, 2]  # R
        rgba[:, :, 1] = img[:, :, 1]  # G
        rgba[:, :, 2] = img[:, :, 0]  # B
        rgba[:, :, 3] = 255          # A
        
        surface = cairo.ImageSurface.create_for_data(
            rgba.data,
            cairo.FORMAT_ARGB32,
            pix.width,
            pix.height,
            pix.width * 4
        )
        
        return surface

    def get_pango_alignment(self, alignment):
        """Convert alignment string to Pango alignment"""
        alignments = {
            "left": Pango.Alignment.LEFT,
            "center": Pango.Alignment.CENTER,
            "right": Pango.Alignment.RIGHT,
            "justify": Pango.Alignment.CENTER
        }
        return alignments.get(alignment, Pango.Alignment.LEFT)
    
    def create_cairo_surface(self, pix):
        """Convert PyMuPDF pixmap to cairo surface"""
        try:
            img = np.frombuffer(pix.samples, dtype=np.uint8)
            img = img.reshape(pix.height, pix.width, 3)
            
            alpha = np.full((pix.height, pix.width, 1), 255, dtype=np.uint8)
            img = np.concatenate([img, alpha], axis=2)
            
            surface = cairo.ImageSurface.create_for_data(
                img.data,
                cairo.FORMAT_ARGB32,
                pix.width,
                pix.height,
                pix.width * 4
            )
            return surface
        except Exception as e:
            print(f"Error creating surface: {e}")
            return None

    def load_document(self, filepath):
        try:
            self.doc = fitz.open(filepath)
            self.current_file = filepath
            self.current_page = 0
            self.editable_texts = []
            self.extract_pdf_text(self.current_page)        
            self.update_page_label()
            self.pdf_view.queue_draw()
            self.status_label.set_text(f"Loaded: {os.path.basename(filepath)}")
            
        except Exception as e:
            self.show_error_dialog(f"Error opening PDF: {str(e)}")

    def show_error_dialog(self, message):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=message
        )
        dialog.connect("response", lambda d, r: d.destroy())
        dialog.present()

    def extract_pdf_text(self, page_number):
        if not self.doc:
            return
                
        page = self.doc[page_number]
        text_dict = page.get_text("dict", flags=11)
        
        self.editable_texts = []
        
        def process_blocks(blocks):
            for block in blocks:
                if "lines" not in block:
                    continue
                        
                for line in block["lines"]:
                    for span in line["spans"]:
                        text = span.get("text", "").strip()
                        if not text:
                            continue
                                
                        bbox = span.get("bbox")
                        font_name = span.get("font", "Helvetica")
                        font_size = span.get("size", 12)
                        color = span.get("color", (0, 0, 0))
                        
                        editable_text = EditableText(
                            x=bbox[0],
                            y=bbox[1],
                            text=text,
                            font_size=font_size,
                            font_family=font_name,
                            color=color,
                            text_instance=span
                        )
                        
                        editable_text.bbox = bbox
                        editable_text.baseline = bbox[3]
                        editable_text.text_width = bbox[2] - bbox[0]
                        editable_text.text_height = bbox[3] - bbox[1]
                        editable_text.page_number = page_number
                        
                        self.editable_texts.append(editable_text)
        
        process_blocks(text_dict.get("blocks", []))


    def normalize_color(self, color):
        if isinstance(color, (int, float)):
            val = max(0, min(1, color))
            return (val, val, val)
        elif isinstance(color, (list, tuple)):
            if len(color) == 3:
                return tuple(max(0, min(1, c)) for c in color)
            elif len(color) == 4:
                return tuple(max(0, min(1, c)) for c in color[:3])
        return (0, 0, 0)
    
    def apply_text_changes(self):
        if not self.doc or not self.selected_text or not self.selected_text.modified:
            return
                
        page = self.doc[self.current_page]
        text = self.selected_text
        
        try:
            if text.bbox:
                rect = fitz.Rect(*text.bbox)
                page.add_redact_annot(rect)
                page.apply_redactions()
            
            font_mapping = {
                'Sans': 'Helvetica',
                'Serif': 'Times-Roman',
                'Monospace': 'Courier',
                'Helvetica': 'Helvetica',
                'Times': 'Times-Roman',
                'Times-Roman': 'Times-Roman',
                'Courier': 'Courier'
            }
            
            font_name = font_mapping.get(text.font_family, 'Helvetica')
            normalized_color = self.normalize_color(text.color)
            text_kwargs = {
                "fontname": font_name,
                "fontsize": text.font_size,
                "color": normalized_color
            }
            
            insert_point = (text.x, text.baseline)           
            font_xref = page.insert_font(fontname=font_name)
            page.insert_text(
                point=insert_point,
                text=text.text,
                **text_kwargs
            )
            
            text.modified = False
            self.pdf_view.queue_draw()
            
        except Exception as e:
            print(f"Error applying text changes: {str(e)}")
            text.text = text.original_text
            text.modified = False

    def on_font_changed(self, combo):
        if self.selected_text:
            iter = combo.get_active_iter()
            if iter is not None:
                font = self.font_store[iter][0]
                self.selected_text.font_family = font
                self.pdf_view.queue_draw()

    def on_font_size_changed(self, spin_button):
        if self.selected_text:
            self.selected_text.font_size = spin_button.get_value()
            self.pdf_view.queue_draw()

    def on_color_changed(self, button):
        if self.selected_text:
            rgba = button.get_rgba()
            self.selected_text.color = (rgba.red, rgba.green, rgba.blue)
            self.pdf_view.queue_draw()

    def setup_text_entry(self, text):
        if self.text_entry:
            self.text_entry.unparent()

        entry = Gtk.Entry()
        entry.set_text(text.text)
        entry.set_opacity(0)
        entry.set_can_focus(True)
        entry.set_size_request(1, 1)
        self.main_box.append(entry)

        font_desc = Pango.FontDescription()
        font_desc.set_family(text.font_family)
        font_desc.set_size(int(text.font_size * Pango.SCALE))
        entry.get_style_context().add_provider(Gtk.CssProvider(), Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        entry.grab_focus()
        entry.connect('changed', self.on_text_entry_changed, text)
        entry.connect('activate', self.on_text_entry_activate, text)

        focus_controller = Gtk.EventControllerFocus.new()
        focus_controller.connect('leave', self.on_text_entry_focus_out, text)
        entry.add_controller(focus_controller)
        
        self.text_entry = entry

    def on_text_entry_changed(self, entry, text):
        text.text = entry.get_text()
        text.modified = True
        self.pdf_view.queue_draw()

    def on_text_entry_activate(self, entry, text):
        self.apply_text_changes()
        text.editing = False
        entry.unparent()
        self.text_entry = None

    def on_text_entry_focus_out(self, controller, text):
        if self.text_entry:
            self.apply_text_changes()
            text.editing = False
            self.text_entry.unparent()
            self.text_entry = None
            self.pdf_view.queue_draw()

    def setup_text_editing(self):
        self.editable_texts = []
        self.selected_text = None
        self.editing_buffer = ""
        self.text_entry = None
        
        click_controller = Gtk.GestureClick.new()
        click_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click_controller.connect('pressed', self.on_text_click)
        self.pdf_view.add_controller(click_controller)
        
        key_controller = Gtk.EventControllerKey.new()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect('key-pressed', self.on_text_key_pressed)
        key_controller.connect('key-released', self.on_text_key_released)
        self.add_controller(key_controller)

    def on_text_click(self, gesture, n_press, x, y):
        if not self.doc:
            return False
            
        if self.text_entry:
            self.text_entry.unparent()
            self.text_entry = None

        x = x / self.zoom_level
        y = y / self.zoom_level
        
        page = self.doc[self.current_page]
        text_blocks = page.get_text("dict", flags=11)
        
        for block in text_blocks.get("blocks", []):
            if "lines" not in block:
                continue
                
            for line in block["lines"]:
                for span in line["spans"]:
                    bbox = span.get("bbox")
                    if bbox:
                        x1, y1, x2, y2 = bbox
                        if (x >= x1 and x <= x2 and y >= y1 and y <= y2):
                            if self.selected_text:
                                self.selected_text.selected = False
                                if self.selected_text.modified:
                                    self.apply_text_changes()

                            text = EditableText(
                                x=bbox[0],
                                y=bbox[1],
                                text=span.get("text", ""),
                                font_size=span.get("size", 12),
                                font_family=span.get("font", "Helvetica"),
                                color=span.get("color", (0, 0, 0)),
                                text_instance=span
                            )
                            text.bbox = bbox
                            text.baseline = bbox[3]
                            text.text_width = bbox[2] - bbox[0]
                            text.text_height = bbox[3] - bbox[1]
                            text.selected = True
                            text.editing = n_press == 2
                            
                            self.selected_text = text
                            self.editing_buffer = text.text

                            if text.editing:
                                self.setup_text_entry(text)
                            
                            self.pdf_view.queue_draw()
                            return True

        if self.selected_text:
            self.selected_text.selected = False
            self.selected_text = None
            self.pdf_view.queue_draw()
        
        return False

    def on_text_release(self, gesture, n_press, x, y):
        if self.selected_text:
            self.selected_text.dragging = False

    def on_text_motion(self, controller, x, y):
        if self.selected_text and self.selected_text.dragging:
            x = x / self.zoom_level
            y = y / self.zoom_level
            
            new_x = x - self.selected_text.drag_start_x
            new_y = y - self.selected_text.drag_start_y
            
            self.selected_text.x = new_x
            self.selected_text.baseline = new_y + self.selected_text.text_height
            
            self.pdf_view.queue_draw()


    def on_text_key_pressed(self, controller, keyval, keycode, state):
        if self.text_entry and self.text_entry.has_focus():
            return False
            
        if self.selected_text and self.selected_text.editing:
            if not self.text_entry:
                self.setup_text_entry(self.selected_text)
            return False
        
        return False
    
    def on_text_key_released(self, controller, keyval, keycode, state):
        if self.selected_text and self.selected_text.editing:
            if keyval == Gdk.KEY_Escape:
                if self.text_entry:
                    self.text_entry.destroy()
                    self.text_entry = None
                self.selected_text.editing = False
                self.selected_text.text = self.selected_text.original_text
                self.selected_text.modified = False
                self.pdf_view.queue_draw()
                return True
        return False


    def on_key_pressed(self, controller, keyval, keycode, state):
        if self.text_layer.editing and self.text_layer.selected_element:
            element = self.text_layer.selected_element
            if keyval == Gdk.KEY_Return:
                self.text_layer.editing = False
                self.text_layer.selected_element = None
            elif keyval == Gdk.KEY_BackSpace:
                element.text = element.text[:-1]
            else:
                char = chr(keyval)
                element.text += char
            self.pdf_view.queue_draw()
            return True
        return False

    def on_click(self, gesture, n_press, x, y):
        if self.text_layer.editing:
            element = TextElement(x, y, "")
            self.text_layer.text_elements.append(element)
            self.text_layer.selected_element = element
            self.text_layer.editing = True
            self.pdf_view.queue_draw()

    def on_drop(self, drop_target, value, x, y):
        if isinstance(value, Gio.File):
            self.load_document(value.get_path())
            return True
        return False

    def on_open_clicked(self, button):
        dialog = self.create_file_dialog(
            "Open PDF",
            Gtk.FileChooserAction.OPEN
        )
        
        filter_pdf = Gtk.FileFilter()
        filter_pdf.set_name("PDF files")
        filter_pdf.add_mime_type("application/pdf")
        dialog.add_filter(filter_pdf)
        
        dialog.connect("response", self.on_open_response)
        dialog.present()

    def on_open_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            if file:
                self.load_document(file.get_path())
        dialog.destroy()

    def on_save_clicked(self, button):
        if self.current_file:
            self.doc.save(self.current_file)
            self.status_label.set_text("Document saved")
        else:
            dialog = self.create_file_dialog(
                "Save PDF As",
                Gtk.FileChooserAction.SAVE
            )
            dialog.connect("response", self.on_save_as_response)
            dialog.present()
            
    def on_save_as(self, action, param):
        dialog = Gtk.FileChooserDialog(
            title="Save PDF As",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            "_Cancel", Gtk.ResponseType.CANCEL,
            "_Save", Gtk.ResponseType.ACCEPT,
        )
        
        dialog.connect("response", self.on_save_as_response)
        dialog.present()

    def on_save_as_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            path = file.get_path()
            if not path.endswith('.pdf'):
                path += '.pdf'
            self.doc.save(path)
            self.current_file = path
            self.status_label.set_text("Document saved")
        dialog.destroy()

    def on_zoom_in(self, button):
        old_zoom = self.zoom_level
        self.zoom_level = min(4.0, self.zoom_level * 1.2)
        if old_zoom != self.zoom_level:
            self.zoom_label.set_text(f"{int(self.zoom_level * 100)}%")
            if self.doc:
                page = self.doc[self.current_page]
                self.pdf_view.set_content_width(int(page.rect.width * self.zoom_level))
                self.pdf_view.set_content_height(int(page.rect.height * self.zoom_level))
            self.pdf_view.queue_draw()

    def on_zoom_out(self, button):
        old_zoom = self.zoom_level
        self.zoom_level = max(0.25, self.zoom_level / 1.2)
        if old_zoom != self.zoom_level:
            self.zoom_label.set_text(f"{int(self.zoom_level * 100)}%")
            if self.doc:
                page = self.doc[self.current_page]
                self.pdf_view.set_content_width(int(page.rect.width * self.zoom_level))
                self.pdf_view.set_content_height(int(page.rect.height * self.zoom_level))
            self.pdf_view.queue_draw()

    def setup_zoom_controller(self):
        scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.BOTH_AXES |
            Gtk.EventControllerScrollFlags.DISCRETE
        )
        scroll_controller.connect('scroll', self.on_scroll_zoom)
        self.pdf_view.add_controller(scroll_controller)

    def on_scroll_zoom(self, controller, dx, dy):
        if controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            if dy < 0:
                self.on_zoom_in(None)
            elif dy > 0:
                self.on_zoom_out(None)
            return True
        return False

    def on_prev_page(self, button):
        if self.doc and self.current_page > 0:
            self.current_page -= 1
            self.editable_texts = []
            self.extract_pdf_text(self.current_page)
            self.update_page_label()
            self.pdf_view.queue_draw()

    def on_next_page(self, button):
        if self.doc and self.current_page < self.doc.page_count - 1:
            self.current_page += 1
            self.editable_texts = []
            self.extract_pdf_text(self.current_page)
            self.update_page_label()
            self.pdf_view.queue_draw()

    def update_page_label(self):
        if self.doc:
            self.page_label.set_text(f"Page {self.current_page + 1} of {self.doc.page_count}")
        else:
            self.page_label.set_text("No document")

    def on_add_text(self, button):
        if not self.doc:
            return
            
        dialog = Gtk.Dialog(
            title="Add Text",
            parent=self,
            modal=True
        )
        dialog.add_buttons(
            "_Cancel", Gtk.ResponseType.CANCEL,
            "_Add", Gtk.ResponseType.ACCEPT
        )
        
        content_area = dialog.get_content_area()
        
        text_entry = Gtk.Entry()
        text_entry.set_placeholder_text("Enter text...")
        content_area.append(text_entry)
        
        font_size_spin = Gtk.SpinButton.new_with_range(8, 72, 1)
        font_size_spin.set_value(12)
        content_area.append(font_size_spin)
        
        dialog.connect("response", self.on_add_text_response, text_entry, font_size_spin)
        dialog.present()

    def on_add_text_response(self, dialog, response, text_entry, font_size_spin):
        if response == Gtk.ResponseType.ACCEPT:
            text = text_entry.get_text()
            font_size = font_size_spin.get_value()

            page = self.doc[self.current_page]
            rect = page.rect
            new_text = EditableText(
                x=rect.width/2,
                y=rect.height/2,
                text=text,
                font_size=font_size
            )
            self.editable_texts.append(new_text)
            self.pdf_view.queue_draw()
        
        dialog.destroy()

    def on_add_image(self, button):
        dialog = self.create_file_dialog(
            "Choose Image",
            Gtk.FileChooserAction.OPEN
        )
        
        filter_images = Gtk.FileFilter()
        filter_images.set_name("Image files")
        filter_images.add_mime_type("image/jpeg")
        filter_images.add_mime_type("image/png")
        dialog.add_filter(filter_images)
        
        dialog.connect("response", self.on_add_image_response)
        dialog.present()

    def on_add_image_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT and self.doc:
            file = dialog.get_file()
            image_path = file.get_path()
            
            page = self.doc[self.current_page]
            rect = page.rect
            page.insert_image(
                rect=fitz.Rect(rect.width/4, rect.height/4, 
                              3*rect.width/4, 3*rect.height/4),
                filename=image_path
            )
            self.pdf_view.queue_draw()
        
        dialog.destroy()

    def on_add_signature(self, button):
        dialog = Gtk.Dialog(
            title="Add Signature",
            parent=self,
            modal=True
        )
        dialog.add_buttons(
            "_Cancel", Gtk.ResponseType.CANCEL,
            "_Add", Gtk.ResponseType.ACCEPT
        )
        
        content_area = dialog.get_content_area()
        drawing_area = Gtk.DrawingArea()
        drawing_area.set_size_request(400, 200)

        content_area.append(drawing_area)
        dialog.present()

    def on_highlight(self, button):
        if not self.doc:
            return
            
        page = self.doc[self.current_page]
        self.pdf_view.queue_draw()

    def on_add_comment(self, button):
        if not self.doc:
            return
            
        dialog = Gtk.Dialog(
            title="Add Comment",
            parent=self,
            modal=True
        )
        dialog.add_buttons(
            "_Cancel", Gtk.ResponseType.CANCEL,
            "_Add", Gtk.ResponseType.ACCEPT
        )
        
        content_area = dialog.get_content_area()
        comment_entry = Gtk.TextView()
        comment_entry.set_wrap_mode(Gtk.WrapMode.WORD)
        scroll = Gtk.ScrolledWindow()
        scroll.set_child(comment_entry)
        content_area.append(scroll)
        
        dialog.connect("response", self.on_add_comment_response, comment_entry)
        dialog.present()

    def on_add_comment_response(self, dialog, response, comment_entry):
        if response == Gtk.ResponseType.ACCEPT:
            buffer = comment_entry.get_buffer()
            comment_text = buffer.get_text(
                buffer.get_start_iter(),
                buffer.get_end_iter(),
                True
            )
            
            page = self.doc[self.current_page]
            rect = page.rect
            annot = page.add_text_annot(
                point=(rect.width-50, 50),
                text=comment_text
            )
            self.pdf_view.queue_draw()
        
        dialog.destroy()

class TextLayer:
    def __init__(self):
        self.text_elements = []
        self.selected_element = None
        self.editing = False

class TextElement:
    def __init__(self, x, y, text, font_size=12, font_family="Sans", color=(0, 0, 0)):
        self.x = x
        self.y = y
        self.text = text
        self.font_size = font_size
        self.font_family = font_family
        self.color = color
        self.width = 0
        self.height = 0

class EditableText:
    def __init__(self, x, y, text, font_size=12, font_family="Helvetica", 
                 color=(0, 0, 0), text_instance=None):
        self.x = x
        self.y = y
        self.text = text
        self.original_text = text
        self.font_size = font_size
        font_mapping = {
            'Sans': 'Helvetica',
            'Serif': 'Times-Roman',
            'Monospace': 'Courier',
            'Helvetica': 'Helvetica',
            'Times': 'Times-Roman',
            'Times-Roman': 'Times-Roman',
            'Courier': 'Courier'
        }
        self.font_family = font_mapping.get(font_family, 'Helvetica')
        self.color = color
        self.selected = False
        self.editing = False
        self.text_instance = text_instance
        self.baseline = y
        self.text_width = 0
        self.text_height = 0
        self.dragging = False
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.original_x = x
        self.original_y = y
        self.modified = False
        self.bbox = None
        self.page_number = None
        
class PdfEditorApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id='org.pardus.pdfeditor',
                        flags=Gio.ApplicationFlags.FLAGS_NONE)
        
        quit_action = Gio.SimpleAction.new('quit', None)
        quit_action.connect('activate', self.on_quit)
        self.add_action(quit_action)
        
        self.set_accels_for_action('app.quit', ['<Control>q'])

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = PdfEditorWindow(application=self)
        win.present()

    def on_quit(self, action, param):
        self.quit()

def main(version):
    app = PdfEditorApplication()
    return app.run(sys.argv)

if __name__ == '__main__':
    sys.exit(main(sys.argv))