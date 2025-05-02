import gi
import os
from pathlib import Path
import threading

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gio, GLib, Adw, Gdk, GdkPixbuf, Pango, GObject, PangoCairo

from . import pdf_handler
from .models import PdfPage, EditableText
from .ui_components import PageThumbnailFactory, show_error_dialog, show_confirm_dialog
from .utils import UNICODE_FONT_PATH

class PdfEditorWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_title("Pardus PDF Editor")
        self.set_default_size(1200, 800)
        self.set_icon_name("pardus-pdf-editor")

        self.current_file_path = None
        self.doc = None # fitz.Document handle
        self.current_page_index = 0
        self.zoom_level = 1.0
        self.pages_model = Gio.ListStore(item_type=PdfPage)
        self.editable_texts = [] # List of EditableText objects for current page
        self.selected_text = None
        self.text_edit_popover = None
        self.text_edit_view = None
        self.is_saving = False
        self.document_modified = False # Track if changes requiring save exist
        self.tool_mode = "select" # Current tool: 'select', 'add_text'

        self._build_ui()
        self._setup_controllers()
        self._connect_actions()
        self._apply_css()
        self._update_ui_state() # Initial state

        if not UNICODE_FONT_PATH:
             show_error_dialog(self, "Could not find a suitable Unicode font (like DejaVuSans.ttf). Editing text with special characters (e.g., Turkish) might not work correctly.", "Font Warning")


    def _apply_css(self):
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .toolbar { padding: 6px; }
            .pdf-view { background-color: #808080; }
            .statusbar { padding: 4px 8px; border-top: 1px solid @borders; background-color: @theme_bg_color; }
            popover > .box { padding: 10px; }
            textview { font-family: monospace; min-height: 80px; margin-bottom: 6px; }
            .tool-button.active { background-color: @theme_selected_bg_color; }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_ui(self):
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(self.main_box)

        # Header Bar
        header = Adw.HeaderBar()
        self.main_box.append(header)

        self.open_button = Gtk.Button(label="Aç")
        self.open_button.connect("clicked", self.on_open_clicked)
        header.pack_start(self.open_button)

        self.save_button = Gtk.Button(label="Kaydet")
        self.save_button.get_style_context().add_class("suggested-action")
        self.save_button.connect("clicked", self.on_save_clicked)
        header.pack_start(self.save_button)

        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        header.pack_end(menu_button)
        menu = Gio.Menu()
        menu.append("Farklı Kaydet...", "win.save_as")
        menu.append("Farklı Dışarı Çıkart...", "win.export_as")
        menu.append("Kapat", "app.quit")
        popover_menu = Gtk.PopoverMenu.new_from_model(menu)
        menu_button.set_popover(popover_menu)

        # Main Paned Layout
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True, vexpand=True)
        self.main_box.append(self.paned)

        self._create_sidebar() # Creates and sets start child

        # Main Content Area (Toolbar + PDF View)
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, vexpand=True)
        self._create_main_toolbar()
        content_box.append(self.main_toolbar)

        self.overlay = Gtk.Overlay(vexpand=True, hexpand=True)
        self.pdf_scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True,
                                             hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                             vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.pdf_view = Gtk.DrawingArea(content_width=1, content_height=1,
                                        hexpand=True, vexpand=True)
        self.pdf_view.set_draw_func(self.draw_pdf_page)
        self.pdf_view.add_css_class('pdf-view')

        self.pdf_viewport = Gtk.Viewport() # Needed if drawing area > scrolled window
        self.pdf_viewport.set_child(self.pdf_view)
        self.pdf_scroll.set_child(self.pdf_viewport)
        self.overlay.set_child(self.pdf_scroll)
        content_box.append(self.overlay)

        self.paned.set_end_child(content_box)
        self.paned.set_position(200) # Initial sidebar width

        # Status Bar
        status_bar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, vexpand=False)
        status_bar_box.add_css_class('statusbar')
        self.status_label = Gtk.Label(label="Hiçbir belge yüklenmedi.", xalign=0.0)
        status_bar_box.append(self.status_label)
        self.main_box.append(status_bar_box)

        # Placeholder Label
        self.empty_label = Gtk.Label(label="Bir PDF dosyasını açın veya bırakın.", vexpand=True,
                                     halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.empty_label.add_css_class("dim-label")
        self.overlay.add_overlay(self.empty_label)


    def _create_sidebar(self):
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                              margin_start=6, margin_end=6, margin_top=6, margin_bottom=6)
        sidebar_box.set_size_request(180, -1)

        # --- Tools ---
        tools_label = Gtk.Label(label="Araçlar", xalign=0.0)
        tools_label.add_css_class('title-4')
        sidebar_box.append(tools_label)

        tools_grid = Gtk.Grid(row_spacing=6, column_spacing=6) # Use grid for potential 2-column layout
        self.select_tool_button = Gtk.Button(icon_name="input-mouse-symbolic", label="Seç")
        self.select_tool_button.set_tooltip_text("Metin Seç (Varsayılan)")
        self.select_tool_button.connect('clicked', self.on_tool_selected, "select")
        self.select_tool_button.add_css_class("tool-button")
        tools_grid.attach(self.select_tool_button, 0, 0, 1, 1)

        self.add_text_tool_button = Gtk.Button(icon_name="insert-text-symbolic", label="Metin Ekle")
        self.add_text_tool_button.set_tooltip_text("Yeni Metin Kutusu Ekle")
        self.add_text_tool_button.connect('clicked', self.on_tool_selected, "add_text")
        self.add_text_tool_button.add_css_class("tool-button")
        tools_grid.attach(self.add_text_tool_button, 1, 0, 1, 1)
        sidebar_box.append(tools_grid)

        # Separator
        sidebar_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, margin_top=6, margin_bottom=6))

        # --- Thumbnails ---
        thumbnails_label = Gtk.Label(label="Sayfalar", xalign=0.0)
        thumbnails_label.add_css_class('title-4')
        sidebar_box.append(thumbnails_label)

        factory = PageThumbnailFactory()
        self.thumbnails_list = Gtk.GridView.new(None, factory)
        self.thumbnails_list.set_max_columns(1)
        self.thumbnails_list.set_min_columns(1)
        self.thumbnails_list.set_vexpand(True) # Allow list to expand

        self.thumbnail_selection_model = Gtk.SingleSelection(model=self.pages_model)
        self.thumbnails_list.set_model(self.thumbnail_selection_model)
        self.thumbnail_selection_model.connect("selection-changed", self.on_thumbnail_selected)

        thumbnails_scroll = Gtk.ScrolledWindow(vexpand=True) # Make scroll window expand
        thumbnails_scroll.set_child(self.thumbnails_list)
        thumbnails_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_box.append(thumbnails_scroll)

        self.paned.set_start_child(sidebar_box)


    def _create_main_toolbar(self):
        self.main_toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.main_toolbar.add_css_class('toolbar')

        # Zoom
        zoom_out = Gtk.Button.new_from_icon_name("zoom-out-symbolic")
        zoom_out.set_tooltip_text("Uzaklaştır (Ctrl+Aşağı Kaydır)")
        zoom_out.connect("clicked", self.on_zoom_out)
        self.zoom_label = Gtk.Label(label="100%")
        zoom_in = Gtk.Button.new_from_icon_name("zoom-in-symbolic")
        zoom_in.set_tooltip_text("Yakınlaştır (Ctrl+KaydırmaYukarı)")
        zoom_in.connect("clicked", self.on_zoom_in)
        self.main_toolbar.append(zoom_out)
        self.main_toolbar.append(self.zoom_label)
        self.main_toolbar.append(zoom_in)

        self.main_toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=6, margin_end=6))

        # Page Navigation
        self.prev_button = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        self.prev_button.set_tooltip_text("Önceki Sayfa")
        self.prev_button.connect("clicked", self.on_prev_page)
        self.page_label = Gtk.Label(label="Sayfa 0 / 0")
        self.next_button = Gtk.Button.new_from_icon_name("go-next-symbolic")
        self.next_button.set_tooltip_text("Sonraki Sayfa")
        self.next_button.connect("clicked", self.on_next_page)
        self.main_toolbar.append(self.prev_button)
        self.main_toolbar.append(self.page_label)
        self.main_toolbar.append(self.next_button)

        self.main_toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=6, margin_end=6))

        # Text Formatting (enabled when text selected or add text active)
        self.font_store = Gtk.ListStore(str, str) # Display Name, PDF Font Name (Base14 fallback)
        self.font_store.append(["Sans", "helv"])
        self.font_store.append(["Serif", "timr"])
        self.font_store.append(["Monospace", "cour"])
        self.font_combo = Gtk.ComboBox(model=self.font_store)
        cell = Gtk.CellRendererText()
        self.font_combo.pack_start(cell, True)
        self.font_combo.add_attribute(cell, "text", 0)
        self.font_combo.set_active(0)
        self.font_combo.set_tooltip_text("Yazı Tipleri (yeni/düzenlenmiş metin için)")
        self.font_combo.connect("changed", self.on_text_format_changed)
        self.main_toolbar.append(self.font_combo)

        self.font_size_spin = Gtk.SpinButton.new_with_range(6, 96, 1)
        self.font_size_spin.set_value(11)
        self.font_size_spin.set_tooltip_text("Yazı Tipi Boyutu")
        self.font_size_spin.connect("value-changed", self.on_text_format_changed)
        self.main_toolbar.append(self.font_size_spin)

        self.color_button = Gtk.ColorButton()
        default_rgba = Gdk.RGBA()
        default_rgba.parse("black")
        self.color_button.set_rgba(default_rgba)
        self.color_button.set_tooltip_text("Yazı Tipi Rengi")
        self.color_button.connect("color-set", self.on_text_format_changed)
        self.main_toolbar.append(self.color_button)


    def _setup_controllers(self):
        # Drag and Drop
        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect('drop', self.on_drop)
        self.add_controller(drop_target)

        # Zoom Scroll
        scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll_controller.connect('scroll', self.on_scroll_zoom)
        self.pdf_view.add_controller(scroll_controller)

        # Click/Tap Gesture
        click_controller = Gtk.GestureClick.new()
        click_controller.connect('pressed', self.on_pdf_view_pressed)
        # No longer need released separately, n_press in pressed handles double click
        # click_controller.connect('released', self.on_pdf_view_released)
        self.pdf_view.add_controller(click_controller)

        # Keyboard Input
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect('key-pressed', self.on_key_pressed)
        self.add_controller(key_controller)


    def _connect_actions(self):
        action_save_as = Gio.SimpleAction.new('save_as', None)
        action_save_as.connect('activate', self.on_save_as)
        self.add_action(action_save_as)

        action_export_as = Gio.SimpleAction.new('export_as', None)
        action_export_as.connect('activate', self.on_export_as)
        self.add_action(action_export_as)


    def _update_ui_state(self):
        has_doc = self.doc is not None
        page_count = pdf_handler.get_page_count(self.doc)
        has_pages = page_count > 0
        can_go_prev = has_pages and self.current_page_index > 0
        can_go_next = has_pages and self.current_page_index < page_count - 1

        # Base controls sensitivity
        self.save_button.set_sensitive(has_doc and self.document_modified)
        self.lookup_action("save_as").set_enabled(has_doc)
        self.lookup_action("export_as").set_enabled(has_doc)
        self.prev_button.set_sensitive(can_go_prev)
        self.next_button.set_sensitive(can_go_next)

        # Text formatting controls sensitivity
        # Enabled if text selected OR if add_text tool is active
        text_tool_active = self.tool_mode == "add_text"
        text_selected = self.selected_text is not None
        format_enabled = text_selected or text_tool_active
        self.font_combo.set_sensitive(format_enabled)
        self.font_size_spin.set_sensitive(format_enabled)
        self.color_button.set_sensitive(format_enabled)

        # Tool button active state
        self.select_tool_button.get_style_context().remove_class('active')
        self.add_text_tool_button.get_style_context().remove_class('active')
        if self.tool_mode == "select":
            self.select_tool_button.get_style_context().add_class('active')
            self.pdf_view.set_cursor(None) # Default cursor
        elif self.tool_mode == "add_text":
            self.add_text_tool_button.get_style_context().add_class('active')
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("crosshair"))

        # View visibility
        self.empty_label.set_visible(not has_doc)
        self.pdf_scroll.set_visible(has_doc)

        if has_doc:
            self.update_page_label()
            if self.document_modified and not self.get_title().endswith("*"):
                 self.set_title(self.get_title() + "*")
            elif not self.document_modified and self.get_title().endswith("*"):
                 self.set_title(self.get_title()[:-1])
        else:
            self.page_label.set_text("Sayfa 0 / 0")
            self.zoom_label.set_text("100%")
            self.status_label.set_text("Hiçbir belge yüklenmedi.")
            self.set_title("Pardus PDF Editor")
            self.document_modified = False


    def load_document(self, filepath):
        if self.check_unsaved_changes():
            # User cancelled closing the previous doc
            return

        self.close_document() # Close previous document first

        self.status_label.set_text(f"Yükleniyor {os.path.basename(filepath)}...")
        GLib.idle_add(self._show_loading_state) # Update UI immediately

        # Run PDF loading in background thread to avoid blocking UI
        def _load_async():
            doc, error_msg = pdf_handler.load_pdf_document(filepath)
            GLib.idle_add(self._finish_loading, doc, error_msg, filepath)

        thread = threading.Thread(target=_load_async)
        thread.daemon = True # Allow app to exit even if thread is running
        thread.start()

    def _show_loading_state(self):
        """Update UI to show loading is in progress."""
        # Disable most controls during load
        self.open_button.set_sensitive(False)
        self.save_button.set_sensitive(False)
        # Keep menu actions enabled? Maybe disable save/export
        self.lookup_action("save_as").set_enabled(False)
        self.lookup_action("export_as").set_enabled(False)
        self.prev_button.set_sensitive(False)
        self.next_button.set_sensitive(False)
        self.font_combo.set_sensitive(False)
        self.font_size_spin.set_sensitive(False)
        self.color_button.set_sensitive(False)
        self.select_tool_button.set_sensitive(False)
        self.add_text_tool_button.set_sensitive(False)
        self.empty_label.set_visible(True)
        self.pdf_scroll.set_visible(False)
        # Ensure status bar is updated (already done in load_document)


    def _finish_loading(self, doc, error_msg, filepath):
        """Callback after background loading finishes."""
        if error_msg:
            show_error_dialog(self, error_msg)
            self.status_label.set_text("Doküman yüklenemedi.")
            self.close_document() # Ensure clean state
        elif doc:
            self.doc = doc
            self.current_file_path = filepath
            self.current_page_index = 0
            self.set_title(f"Pardus PDF Editor - {os.path.basename(filepath)}")
            self.status_label.set_text(f"Küçük resimler yükleniyor...")
            GLib.idle_add(self._load_thumbnails) # Load thumbnails in idle callback

        # Re-enable controls after loading attempt (success or fail)
        self.open_button.set_sensitive(True)
        self.select_tool_button.set_sensitive(True)
        self.add_text_tool_button.set_sensitive(True)
        self._update_ui_state() # Update based on loaded doc state


    def _load_thumbnails(self):
        if not self.doc:
            return

        self.pages_model.remove_all()
        page_count = pdf_handler.get_page_count(self.doc)

        # Load thumbnails incrementally using idle_add to avoid blocking
        self.thumb_load_iter = 0
        def _load_next_thumb():
            if self.thumb_load_iter < page_count:
                 index = self.thumb_load_iter
                 thumb = pdf_handler.generate_thumbnail(self.doc, index)
                 if thumb:
                     pdf_page_obj = PdfPage(index=index, thumbnail=thumb)
                     self.pages_model.append(pdf_page_obj)
                 self.thumb_load_iter += 1
                 # Update status periodically
                 if index % 5 == 0 or index == page_count - 1:
                     self.status_label.set_text(f"Küçük resim yüklendi {index + 1}/{page_count}")
                 return GLib.SOURCE_CONTINUE # Schedule next load
            else:
                 # Finished loading thumbnails
                 self.status_label.set_text(f"Yüklendi: {os.path.basename(self.current_file_path)}")
                 # Load the first page content now that thumbnails are done (or loading)
                 if page_count > 0:
                      self._load_page(0)
                 else: # Handle empty PDF case
                     self._update_ui_state()
                 return GLib.SOURCE_REMOVE # Stop scheduling

        GLib.idle_add(_load_next_thumb)


    def _load_page(self, page_index):
        """Loads content and editable text for a specific page."""
        if not self.doc or not (0 <= page_index < pdf_handler.get_page_count(self.doc)):
            return

        self.current_page_index = page_index
        self.selected_text = None
        self.hide_text_editor()

        # Extract text elements
        texts, error = pdf_handler.extract_editable_text(self.doc, page_index)
        if error:
             show_error_dialog(self, f"Metin yapısı sayfadan çıkarılamadı {page_index + 1}.\n{error}")
             self.editable_texts = []
        else:
             self.editable_texts = texts

        # Trigger redraw
        page = self.doc.load_page(page_index)
        width = int(page.rect.width * self.zoom_level)
        height = int(page.rect.height * self.zoom_level)
        self.pdf_view.set_content_width(width)
        self.pdf_view.set_content_height(height)
        self.pdf_view.queue_draw() # Request redraw

        self._sync_thumbnail_selection()
        self._update_ui_state() # Update buttons, labels etc.


    def close_document(self):
        """Closes the current document and resets the UI."""
        pdf_handler.close_pdf_document(self.doc)
        self.doc = None
        self.current_file_path = None
        self.current_page_index = 0
        self.editable_texts = []
        self.selected_text = None
        self.hide_text_editor()
        self.pages_model.remove_all()
        self.document_modified = False
        self.pdf_view.set_content_width(1)
        self.pdf_view.set_content_height(1)
        self.pdf_view.queue_draw() # Clear the view
        self._update_ui_state() # Reset UI to initial state


    # pardus-pdf-editor-project/pardus_pdf_editor/window.py

# pardus-pdf-editor-project/pardus_pdf_editor/window.py

# Add the incremental flag to the method definition
    def save_document(self, save_path, incremental=False):
        """Saves the document to the specified path."""
        if not self.doc or self.is_saving:
            return
        self.is_saving = True
        self.status_label.set_text(f"Kaydediliyor {os.path.basename(save_path)}...")
        # Ensure UI updates before potentially long save

        # Apply any pending editor changes *before* saving
        if self.text_edit_popover and self.text_edit_popover.is_visible():
            self._apply_and_hide_editor(force_apply=True) # Ensure changes applied

        # Run save in background? Might be better for very large files/slow saves
        # For now, keep it synchronous as it's often fast.
        # Pass the incremental flag down to the actual handler function
        success, error_msg = pdf_handler.save_document(self.doc, save_path, incremental=incremental)

        self.is_saving = False
        if success:
            # Only update current_file_path if it was a standard save or the first save as
            # Check if the save path IS the current path or if there was no current path before
            if save_path == self.current_file_path or self.current_file_path is None:
                self.current_file_path = save_path # Update path if needed

            self.document_modified = False # Reset modified flag
            # Update title based on the actual path used for saving
            self.set_title(f"Pardus PDF Editor - {os.path.basename(save_path)}")
            self.status_label.set_text(f"Doküman kaydedildi: {os.path.basename(save_path)}")
            # Reload the current page to reflect cleaned state if clean_contents was used in save
            # self._load_page(self.current_page_index) # Optional: uncomment if save cleans content
        else:
            show_error_dialog(self, f"PDF kaydedilirken hata oluştu: {error_msg}")
            self.status_label.set_text("Kaydetme başarısız oldu.")

        self._update_ui_state() # Update save button state etc.

    def draw_pdf_page(self, area, cr, width, height):
        """Draw callback for the PDF view DrawingArea."""
        success, msg = pdf_handler.draw_page_to_cairo(cr, self.doc, self.current_page_index, self.zoom_level)

        # Draw selection highlight if needed, on top of the page content
        if success and self.selected_text and self.selected_text.bbox:
            cr.save()
            cr.set_source_rgba(0.2, 0.4, 1.0, 0.3) # Semi-transparent blue
            x1, y1, x2, y2 = self.selected_text.bbox
            rect_x = x1 * self.zoom_level
            rect_y = y1 * self.zoom_level
            rect_w = (x2 - x1) * self.zoom_level
            rect_h = (y2 - y1) * self.zoom_level
            cr.rectangle(rect_x, rect_y, rect_w, rect_h)
            cr.fill()
            cr.restore()


    def _find_text_at_pos(self, page_x, page_y):
        """Finds the EditableText object at the given page coordinates."""
        # Iterate in reverse (topmost drawn last, checked first)
        for text_obj in reversed(self.editable_texts):
            if not text_obj.bbox: continue # Skip objects without bounds
            x1, y1, x2, y2 = text_obj.bbox
            # Add a small tolerance for clicking near edges
            tolerance = 2 / self.zoom_level # 2 screen pixels tolerance in page units
            if (x1 - tolerance) <= page_x <= (x2 + tolerance) and \
               (y1 - tolerance) <= page_y <= (y2 + tolerance):
                return text_obj
        return None

    def _update_text_format_controls(self, text_obj):
        """Updates toolbar controls to match the selected text object."""
        if not text_obj: return

        # Font Family (Best effort mapping back, less reliable)
        active_font_index = 0 # Default to Sans
        font_name_to_find = text_obj.pdf_fontname # Use mapped base14 name
        for i, row in enumerate(self.font_store):
            if row[1] == font_name_to_find:
                 active_font_index = i
                 break
            # Fallback check based on common parts of name
            elif row[0].lower() in text_obj.font_family.lower():
                 active_font_index = i
        self.font_combo.set_active(active_font_index)

        # Font Size
        self.font_size_spin.set_value(text_obj.font_size)

        # Color
        rgba = Gdk.RGBA()
        rgba.red, rgba.green, rgba.blue = text_obj.color
        rgba.alpha = 1.0
        self.color_button.set_rgba(rgba)

    def _get_current_format_settings(self):
        """Gets font/size/color from the toolbar controls."""
        # Font
        font_family_display = "Sans"
        font_pdf_name = "helv" # Default fallback
        iter = self.font_combo.get_active_iter()
        if iter:
            font_family_display = self.font_store[iter][0]
            font_pdf_name = self.font_store[iter][1]

        # Size
        font_size = self.font_size_spin.get_value()

        # Color
        rgba = self.color_button.get_rgba()
        color = (rgba.red, rgba.green, rgba.blue)

        return font_family_display, font_pdf_name, font_size, color


    def _setup_text_editor(self, text_obj):
        self.hide_text_editor() # Ensure any previous editor is hidden
        if not text_obj: return

        self.editing_text_object = text_obj # Keep track of which object is being edited

        self.text_edit_popover = Gtk.Popover(autohide=False, has_arrow=True, position=Gtk.PositionType.BOTTOM)

        # Main container box for the popover content
        popover_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6
            # Note: min_content_width is NOT set here
        )
        popover_box.add_css_class("box")
        self.text_edit_popover.set_child(popover_box)

        # Scrolled window containing the text view
        # Set minimum width and height here
        scroll = Gtk.ScrolledWindow(
            min_content_height=120,           # Set minimum height for the scroll area
            max_content_height=400,           # Optional: Increased max height
            min_content_width=250,            # Set minimum width for the scroll area's content
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, # Allow horizontal scroll if needed
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC
        )
        popover_box.append(scroll)

        # The actual text editing widget
        self.text_edit_view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        # Add some internal margins for better padding
        self.text_edit_view.set_left_margin(5)
        self.text_edit_view.set_right_margin(5)
        self.text_edit_view.set_top_margin(5)
        self.text_edit_view.set_bottom_margin(5)
        # Set initial text from the EditableText object
        self.text_edit_view.get_buffer().set_text(text_obj.text)
        self.text_edit_view.add_css_class("textview") # Apply CSS styling
        scroll.set_child(self.text_edit_view) # Put TextView inside ScrolledWindow

        # Container for the 'Done' button, aligned to the end
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.END)
        done_button = Gtk.Button(label="Bitir")
        done_button.add_css_class("suggested-action") # Style as primary action
        done_button.connect("clicked", self.on_text_edit_done) # Connect click signal
        button_box.append(done_button)
        popover_box.append(button_box) # Add button box below the text area

        # --- Popover Positioning Logic ---
        # Calculate the position on the PDF view to point the popover arrow towards
        if text_obj.bbox: # If editing existing text with a known bounding box
             x1, y1, x2, y2 = text_obj.bbox
             widget_x = x1 * self.zoom_level       # Top-left x in widget coordinates
             widget_y = y2 * self.zoom_level       # Baseline y in widget coordinates (anchor below text)
             widget_w = (x2 - x1) * self.zoom_level # Width in widget coordinates
        else: # If adding new text, use the initial click coordinates
             widget_x = text_obj.x * self.zoom_level
             widget_y = text_obj.baseline * self.zoom_level # Use estimated baseline y
             widget_w = 100 # Default width estimate for positioning anchor

        # Create the rectangle the popover should point to
        rect = Gdk.Rectangle()
        rect.x = int(widget_x)
        rect.y = int(widget_y)
        rect.width = int(max(widget_w, 50)) # Ensure minimum anchor width
        rect.height = 1 # Minimal height for anchor point

        # Configure and show the popover
        self.text_edit_popover.set_parent(self.pdf_view) # Anchor relative to the drawing area
        self.text_edit_popover.set_pointing_to(rect)     # Point arrow to the calculated rectangle
        self.text_edit_popover.popup()                   # Show the popover
        self.text_edit_view.grab_focus()                 # Set keyboard focus to the text editor

    def hide_text_editor(self):
        """Hides the text editor popover."""
        if self.text_edit_popover:
            self.text_edit_popover.popdown()
            self.text_edit_popover = None
            self.text_edit_view = None
            self.editing_text_object = None # Clear reference


    def _apply_and_hide_editor(self, force_apply=False):
        """Reads text from editor, applies changes to PDF, hides editor."""
        if not self.text_edit_view or not self.editing_text_object:
            self.hide_text_editor()
            return

        # Store ref and clear state *before* potential blocking operations
        text_obj_to_apply = self.editing_text_object
        buffer = self.text_edit_view.get_buffer()
        start = buffer.get_start_iter()
        end = buffer.get_end_iter()
        new_text = buffer.get_text(start, end, True)

        original_text = text_obj_to_apply.original_text if not text_obj_to_apply.is_new else ""
        text_actually_changed = (new_text != original_text) or text_obj_to_apply.is_new

        # Hide the editor first, looks smoother
        self.hide_text_editor()

        # Apply changes only if text content differs from original, or it's a new item,
        # or if formatting was changed (check modified flag), or if forced.
        if force_apply or text_actually_changed or text_obj_to_apply.modified:
            self.status_label.set_text("Metin değişiklikleri uygulanıyor...")

            success, error_msg = pdf_handler.apply_text_edit(self.doc, text_obj_to_apply, new_text)

            if success:
                self.document_modified = True # Mark document as modified
                # Reload page to show results and update editable_texts list
                # Store scroll position before reload
                current_scroll_adj_v = self.pdf_scroll.get_vadjustment().get_value()
                current_scroll_adj_h = self.pdf_scroll.get_hadjustment().get_value()
                self._load_page(self.current_page_index)
                # Restore scroll position after reload (use idle_add)
                GLib.idle_add(lambda: self.pdf_scroll.get_vadjustment().set_value(current_scroll_adj_v))
                GLib.idle_add(lambda: self.pdf_scroll.get_hadjustment().set_value(current_scroll_adj_h))
                self.status_label.set_text("Metin değişiklikleri uygulandı.")
            else:
                show_error_dialog(self, f"Metin değişiklikleri uygulanamadı: {error_msg}")
                # Optionally reload page anyway to revert visual state?
                self._load_page(self.current_page_index)
                self.status_label.set_text("Metin değişiklikleri uygulanamadı.")
        else:
            # No changes detected, just hide editor (already done)
            self.status_label.set_text("Uygulanacak değişiklik yok.")

        # Reset the selection after editing is done
        self.selected_text = None
        self.pdf_view.queue_draw()
        self._update_ui_state()


    def check_unsaved_changes(self):
        """Checks for unsaved changes and prompts the user. Returns True if user cancels."""
        if self.document_modified:
            confirm = show_confirm_dialog(self,
                                          "Kaydedilmemiş değişiklikler var. Kapatmadan/açmadan önce bunları kaydetmek istiyor musunuz?",
                                          title="Kaydedilmemiş Değişiklikler",
                                          destructive=False) # Use normal confirm button
            if confirm: # User wants to save
                 if self.current_file_path:
                     self.save_document(self.current_file_path)
                     # Check if save was successful? Assume it was for now.
                     return False # Proceed with action
                 else:
                     # Need to trigger Save As flow, then proceed. This requires more complex logic (callbacks).
                     # For now, simplify: tell user to Save As manually first.
                     show_error_dialog(self, "Değişiklikleri kaydetmek için lütfen önce 'Farklı Kaydet...' seçeneğini kullanın.")
                     return True # Cancel the action (close/open)

            # If user clicks Cancel on the confirm dialog, we need to know.
            # show_confirm_dialog needs modification to return 'cancel' explicitly.
            # Assuming the current implementation: If not confirm, means discard changes or cancel action.
            # Let's assume discard for now. A better dialog would have Save/Discard/Cancel.
            print("Kaydedilmemiş değişiklikler siliniyor.")
            # Fall through to allow the action (close/open).
            return False # Allow action (changes discarded)
        return False # No unsaved changes, allow action


    # --- Event Handlers ---

    def on_drop(self, drop_target, value, x, y):
        if isinstance(value, Gio.File):
            filepath = value.get_path()
            if filepath and filepath.lower().endswith('.pdf'):
                # Use GLib.idle_add to avoid potential reentrancy issues
                GLib.idle_add(self.load_document, filepath)
                return True
        return False

    def on_open_clicked(self, button):
        if self.check_unsaved_changes():
             return # User cancelled

        dialog = Gtk.FileChooserDialog(title="PDF Dosyasını Aç", transient_for=self, modal=True,
                                       action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Open", Gtk.ResponseType.ACCEPT)
        filter_pdf = Gtk.FileFilter(name="PDF files (*.pdf)")
        filter_pdf.add_pattern("*.pdf")
        filter_pdf.add_mime_type("application/pdf")
        dialog.add_filter(filter_pdf)
        filter_all = Gtk.FileFilter(name="All files")
        filter_all.add_pattern("*")
        dialog.add_filter(filter_all)

        def on_response(d, response):
            if response == Gtk.ResponseType.ACCEPT:
                file = d.get_file()
                if file:
                    # Use GLib.idle_add for loading after dialog closes
                    GLib.idle_add(self.load_document, file.get_path())
            d.destroy()

        dialog.connect("response", on_response)
        dialog.present()


    # pardus-pdf-editor-project/pardus_pdf_editor/window.py

	# pardus-pdf-editor-project/pardus_pdf_editor/window.py

    def on_save_clicked(self, button):
        if self.current_file_path:
            # Pass incremental=True for standard "Save" (overwriting original)
            self.save_document(self.current_file_path, incremental=True)
        else:
            self.on_save_as(None, None) # Trigger Save As (which will use incremental=False)
        
    def on_save_as(self, action, param):
        if not self.doc: return

        dialog = Gtk.FileChooserDialog(title="PDF'yi Farklı Kaydet...", transient_for=self, modal=True,
                                       action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Save", Gtk.ResponseType.ACCEPT)
        dialog.set_current_name(os.path.basename(self.current_file_path or "edited_document.pdf"))
        filter_pdf = Gtk.FileFilter(name="PDF files (*.pdf)")
        filter_pdf.add_pattern("*.pdf")
        filter_pdf.add_mime_type("application/pdf")
        dialog.add_filter(filter_pdf)

        def on_response(d, response):
            if response == Gtk.ResponseType.ACCEPT:
                file = d.get_file()
                if file:
                    path = file.get_path()
                    if not path.lower().endswith('.pdf'): path += '.pdf'
                    # Pass incremental=False for "Save As" (new file)
                    self.save_document(path, incremental=False)
            d.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def on_export_as(self, action, param):
        if not self.doc: return

        dialog = Gtk.FileChooserDialog(title="Farklı Dışa Aktar...", transient_for=self, modal=True,
                                       action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Export", Gtk.ResponseType.ACCEPT)
        base_name = Path(self.current_file_path).stem if self.current_file_path else "document"
        dialog.set_current_name(base_name)

        filters = {
            "PDF": ("PDF files (*.pdf)", "*.pdf", "application/pdf"),
            "DOCX": ("Word Document (*.docx)", "*.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            "TXT": ("Text File (*.txt)", "*.txt", "text/plain"),
        }
        for name, (pattern_name, pattern, mime) in filters.items():
            ff = Gtk.FileFilter(name=f"{name} - {pattern_name}")
            ff.add_pattern(pattern)
            if mime: ff.add_mime_type(mime)
            dialog.add_filter(ff)

        def on_response(d, response):
            if response == Gtk.ResponseType.ACCEPT:
                file = d.get_file()
                chosen_filter = d.get_filter()
                if file and chosen_filter:
                    path = file.get_path()
                    filter_name = chosen_filter.get_name().split(" - ")[0]
                    self._execute_export(filter_name, path)
            d.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _execute_export(self, format_name, output_path):
        """Performs the actual export based on selected format."""
        self.status_label.set_text(f"Farklı olarak dışa aktarılıyor {format_name}...")

        success = False
        error_msg = "Bilinmeyen dışa aktarma biçimi."

        try:
            if format_name == "DOCX":
                if not output_path.lower().endswith('.docx'): output_path += '.docx'
                success, error_msg = pdf_handler.export_pdf_as_docx(self.doc, self.current_file_path, output_path)
            elif format_name == "TXT":
                if not output_path.lower().endswith('.txt'): output_path += '.txt'
                success, error_msg = pdf_handler.export_pdf_as_text(self.doc, output_path)
            elif format_name == "PDF":
                if not output_path.lower().endswith('.pdf'): output_path += '.pdf'
                # Exporting as PDF is like "Save As", use incremental=False
                success, error_msg = pdf_handler.save_document(self.doc, output_path, incremental=False)
                if success:
                    # Don't reset document_modified flag on export unless it's to the original path
                    # (which is unlikely here but technically possible)
                    # if output_path == self.current_file_path:
                    #    self.document_modified = False
                    self._update_ui_state() # Update UI state potentially
            else:
                 success = False # Handled by initial error_msg

            if success:
                 self.status_label.set_text(f"Belge olarak dışa aktarıldı {format_name}: {os.path.basename(output_path)}")
            else:
                 show_error_dialog(self, f"Dışa Aktarma Başarısız: {error_msg}")
                 self.status_label.set_text(f"Export as {format_name} failed.")

        except Exception as e:
             show_error_dialog(self, f"Dışa aktarma sırasında beklenmeyen hata: {e}")
             self.status_label.set_text("Dışa aktarma başarısız oldu.")


    def on_zoom_in(self, button=None):
        if not self.doc: return
        self.zoom_level = min(8.0, self.zoom_level * 1.2)
        self.zoom_label.set_text(f"{int(self.zoom_level * 100)}%")
        self._load_page(self.current_page_index) # Reload page content

    def on_zoom_out(self, button=None):
        if not self.doc: return
        self.zoom_level = max(0.1, self.zoom_level / 1.2)
        self.zoom_label.set_text(f"{int(self.zoom_level * 100)}%")
        self._load_page(self.current_page_index)

    def on_scroll_zoom(self, controller, dx, dy):
        if controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            if dy < 0: self.on_zoom_in()
            elif dy > 0: self.on_zoom_out()
            return True # Event handled
        return False # Allow normal scroll

    def on_prev_page(self, button):
        if self.doc and self.current_page_index > 0:
            self._load_page(self.current_page_index - 1)

    def on_next_page(self, button):
        if self.doc and self.current_page_index < pdf_handler.get_page_count(self.doc) - 1:
            self._load_page(self.current_page_index + 1)

    def update_page_label(self):
        count = pdf_handler.get_page_count(self.doc)
        self.page_label.set_text(f"Sayfa {self.current_page_index + 1} of {count}" if count > 0 else "Sayfa 0 / 0")

    def on_thumbnail_selected(self, selection_model, position, n_items):
         selected_index = selection_model.get_selected()
         if selected_index != Gtk.INVALID_LIST_POSITION and selected_index != self.current_page_index:
              # Prevent recursive call if _load_page triggers selection change
              if hasattr(self, '_syncing_thumb') and self._syncing_thumb: return
              self._load_page(selected_index)

    def _sync_thumbnail_selection(self):
         if not self.doc or not self.thumbnail_selection_model: return
         self._syncing_thumb = True
         self.thumbnail_selection_model.set_selected(self.current_page_index)
         # TODO: Add reliable scrolling of GridView item into view if needed
         self._syncing_thumb = False


    def on_pdf_view_pressed(self, gesture, n_press, x, y):
        if not self.doc: return

        page_x = x / self.zoom_level
        page_y = y / self.zoom_level

        if self.tool_mode == "select":
            clicked_text = self._find_text_at_pos(page_x, page_y)

            if clicked_text:
                # Double click (n_press > 1) on selected text -> edit
                if clicked_text == self.selected_text and n_press > 1:
                    self._setup_text_editor(clicked_text)
                # Single click on a text element
                elif self.selected_text != clicked_text:
                    self.selected_text = clicked_text
                    self.hide_text_editor() # Hide editor if selecting different text
                    self.pdf_view.queue_draw() # Show selection highlight
                    self._update_text_format_controls(self.selected_text)
            else:
                # Clicked outside any text
                if self.selected_text:
                    # If editor was open, apply changes. Otherwise, deselect.
                    if self.text_edit_popover and self.text_edit_popover.is_visible():
                        self._apply_and_hide_editor()
                    else:
                        self.selected_text = None
                        self.hide_text_editor()
                        self.pdf_view.queue_draw() # Remove highlight

            self._update_ui_state() # Update toolbar sensitivity etc.

        elif self.tool_mode == "add_text":
            # If an editor is already open, apply/hide it first before adding new
            if self.text_edit_popover and self.text_edit_popover.is_visible():
                 self._apply_and_hide_editor()
                 return # Wait for next click to add text

            # Get current format settings from toolbar
            font_fam, font_pdf, font_size, color = self._get_current_format_settings()

            # Create a new EditableText object at the click location
            # Baseline estimation: click point y is roughly the top, baseline is lower
            baseline_y = page_y + (font_size * 0.9) # Estimate baseline based on font size
            new_text_obj = EditableText(
                x=page_x, y=page_y, text="Yeni Metin", # Default text
                font_size=font_size,
                font_family=font_fam, # Store display name
                color=color,
                is_new=True,
                baseline=baseline_y
            )
            new_text_obj.pdf_fontname = font_pdf # Store pdf name fallback
            new_text_obj.page_number = self.current_page_index

            # Add temporarily to list for potential immediate editing
            # It will be properly added/managed after editing confirmation
            # self.editable_texts.append(new_text_obj) # No, edit first
            self.selected_text = new_text_obj # Select the new potential text
            self._setup_text_editor(new_text_obj) # Open editor immediately
            # Switch back to select tool after placing the text box? Or keep add text active?
            # Let's keep add_text active for now. User can switch manually.
            # self.on_tool_selected(None, "select") # Option: Switch back automatically

            self._update_ui_state()


    def on_text_format_changed(self, widget, *args):
        # This applies only when text is selected *and* editor is NOT open
        if self.selected_text and not (self.text_edit_popover and self.text_edit_popover.is_visible()):
            # Get new format settings
            font_fam, font_pdf, font_size, color = self._get_current_format_settings()

            # Update the selected object's properties immediately
            # Check if format actually changed to set modified flag
            changed = False
            if self.selected_text.font_size != font_size:
                self.selected_text.font_size = font_size
                changed = True
            if self.selected_text.color != color:
                 self.selected_text.color = color
                 changed = True
            # Font family change is harder to track reliably, assume change if combo changes
            # We should really compare against original format if possible.
            # Simple approach: If combo changes, mark modified.
            iter = self.font_combo.get_active_iter()
            if iter:
                 current_pdf_name = self.font_store[iter][1]
                 if self.selected_text.pdf_fontname != current_pdf_name:
                     self.selected_text.font_family = self.font_store[iter][0]
                     self.selected_text.pdf_fontname = current_pdf_name
                     changed = True

            if changed:
                self.selected_text.modified = True
                self.document_modified = True # Mark doc modified if format changes
                # Trigger apply? No, current logic requires editor interaction or Del key.
                # We could add an "Apply Format" button or apply immediately via redaction.
                # For now, the change is stored, waiting for edit/delete.
                print(f"Şunun için saklanan format değişikliği: {self.selected_text.text[:20]}")
                self._update_ui_state() # Update save button state


    def on_text_edit_done(self, button):
        self._apply_and_hide_editor(force_apply=True) # Force apply on Done click


    def on_key_pressed(self, controller, keyval, keycode, state):
        # Escape Key
        if keyval == Gdk.KEY_Escape:
            if self.text_edit_popover and self.text_edit_popover.is_visible():
                 # Cancel edit: just hide popover, discard textview changes
                 self.hide_text_editor()
                 # If it was a *new* text object being edited, remove selection
                 if self.selected_text and self.selected_text.is_new:
                      self.selected_text = None
                      self.pdf_view.queue_draw()
                 # Otherwise, keep selection on the original text
                 elif self.selected_text:
                      self.pdf_view.queue_draw() # Keep highlight
                 self._update_ui_state()
                 return True
            elif self.selected_text:
                 # Deselect text
                 self.selected_text = None
                 self.pdf_view.queue_draw()
                 self._update_ui_state()
                 return True
            elif self.tool_mode == "add_text":
                 # Cancel Add Text mode, switch back to Select
                 self.on_tool_selected(None, "select")
                 return True

        # Delete Key
        elif keyval == Gdk.KEY_Delete:
             if self.selected_text and not (self.text_edit_popover and self.text_edit_popover.is_visible()):
                  # Delete selected text item
                  confirm = show_confirm_dialog(self, f"Seçilen metni sil?\n'{self.selected_text.text[:50]}...'")
                  if confirm:
                       self.status_label.set_text("Metin siliniyor...")
                       # Apply empty string to delete via redaction/insertion logic
                       success, error_msg = pdf_handler.apply_text_edit(self.doc, self.selected_text, "")
                       if success:
                            self.document_modified = True
                            # Reload page to reflect deletion
                            self._load_page(self.current_page_index)
                            self.status_label.set_text("Metin silindi.")
                       else:
                            show_error_dialog(self, f"Metin silinemedi: {error_msg}")
                            self.status_label.set_text("Metin silinemedi.")
                       # Deselect after operation
                       self.selected_text = None
                       self._update_ui_state()
                  return True # Event handled

        return False # Event not handled


    def on_tool_selected(self, button, tool_name):
        """Handles selection of tools like 'select' or 'add_text'."""
        # If switching away from text editor popover, handle it
        if self.text_edit_popover and self.text_edit_popover.is_visible():
             # Decide whether to apply or discard changes when switching tools
             # Let's apply changes automatically for now.
             print("Araç değiştirilmeden önce değişiklikler uygulanıyor...")
             self._apply_and_hide_editor(force_apply=True)
             # If apply fails, maybe don't switch tool? For now, switch anyway.

        # Deselect any selected text when changing tool
        if self.selected_text:
            self.selected_text = None
            self.pdf_view.queue_draw()

        self.tool_mode = tool_name
        print(f"Araç şu şekilde değiştirildi: {self.tool_mode}")
        self._update_ui_state() # Updates button appearance, cursor, toolbar sensitivity


    def do_close_request(self):
        """Handle window close request (X button)."""
        if self.check_unsaved_changes():
            # User cancelled the close operation
            return True # Prevent closing
        else:
            # Proceed with closing
            self.close_document()
            # Chain up to default handler is not needed explicitly for Adw.ApplicationWindow usually
            # super().do_close_request()
            return False # Allow closing
