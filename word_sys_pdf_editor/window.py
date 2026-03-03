import copy
from .undo_manager import UndoManager, EditObjectCommand, AddObjectCommand, DeleteObjectCommand

import gi
import os
from pathlib import Path
import cairo
import threading
import math
import re
from pathlib import Path
import fitz

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gio, GLib, Adw, Gdk, GdkPixbuf, Pango, GObject, PangoCairo

from . import pdf_handler
from . import print_handler
from .welcome_view import WelcomeView 
from .models import PdfPage, EditableText, BASE14_FALLBACK_MAP, EditableImage, EditableShape
from .ui_components import PageThumbnailFactory, show_error_dialog, show_confirm_dialog
from . import utils

class PdfEditorWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_title("Word-Sys's PDF Editor")
        self.set_default_size(1200, 800)
        # Set window icon from icon theme
        self.set_icon_name("f-pv1")

        self.current_file_path = None
        self.original_file_path = None
        self.allow_incremental_save = True
        self.doc = None 
        self.current_page_index = 0
        self.zoom_level = 1.0
        self.pages_model = Gio.ListStore(item_type=PdfPage)
        self.editable_texts = [] 
        self.editable_images = []
        self.editable_shapes = []
        self.selected_text = None
        self.selected_image = None
        self.selected_shape = None
        self.text_edit_popover = None
        self.text_edit_view = None
        self.is_saving = False
        self.dragged_object = None
        self.drag_start_pos = (0, 0)
        self.drag_object_start_pos = (0, 0)
        self.resize_handle = None  # Track which handle is being dragged
        self.resize_start_bbox = None  # Store original bbox during resize
        self.dragging_to_create = False  # Track if we're creating a shape/image by dragging
        self.temp_shape = None  # Temporary shape being created
        self.temp_image_bbox = None  # Temporary image bbox being placed
        self.temp_image_path = None  # Temporary image path being placed
        self.drag_start_page_pos = None  # Starting position in page coordinates
        # Shape properties for next creation
        self.next_shape_fill = (255, 255, 255)
        self.next_shape_stroke = (0, 0, 0)
        self.next_shape_stroke_width = 2.0
        self.next_shape_transparent = True
        self.document_modified = False 
        self.tool_mode = "select" 
        self.current_pdf_page_width = 0
        self.current_pdf_page_height = 0
        self.bold_button = None
        self.italic_button = None
        self.font_scan_in_progress = True
        self.undo_manager = UndoManager(self)
        self.pending_format_change_obj = None
        self.before_format_change_state = None
        self.is_repaired_file = False

        self._build_ui()
        self._setup_controllers()
        self._connect_actions()
        self._apply_css()
        self._update_ui_state() 

        self.status_label.set_text("Sistem fontları taranıyor...")
        utils.scan_system_fonts_async(callback_on_done=self._on_font_scan_complete)

    def _on_font_scan_complete(self):
        self.font_scan_in_progress = False
        self.font_scan_in_progress = False
        print("DEBUG: _on_font_scan_complete triggered.")
        
        final_utils_unicode_font_path = utils.get_default_unicode_font_path()
        print(f"DEBUG: Value from utils.get_default_unicode_font_path(): {final_utils_unicode_font_path}")
        print(f"DEBUG: Current utils.UNICODE_FONT_PATH (after call): {utils.UNICODE_FONT_PATH}") 

        self._populate_font_combo() 

        if not utils.UNICODE_FONT_PATH:
             show_error_dialog(self, "Uygun bir Unicode yazı tipi bulunamadı (örneğin DejaVuSans.ttf). Özel karakterler (örn. Türkçe) içeren metinlerin düzenlenmesi düzgün çalışmayabilir.", "Yazı Tipi Uyarısı")
        
        self.status_label.set_text("Fontlar yüklendi. Bir dosya açın." if not self.doc else f"Yüklendi: {os.path.basename(self.current_file_path)}")
        self._update_ui_state()

    def _populate_font_combo(self):
        print(f"DEBUG: Populating font combo. utils.FONT_SCAN_COMPLETED: {utils.FONT_SCAN_COMPLETED.is_set()}")
        print(f"DEBUG: utils.FONT_FAMILY_LIST_SORTED has {len(utils.FONT_FAMILY_LIST_SORTED)} items.")

        self.font_store.clear() 
        if utils.FONT_FAMILY_LIST_SORTED:
            for family_name in utils.FONT_FAMILY_LIST_SORTED:
                self.font_store.append([family_name, family_name])
            if len(self.font_store) > 0:
                self.font_combo.set_active(0) 
            else:
                 self.font_store.append(["<Font Yok (Hata)>", ""])
                 self.font_combo.set_active(0)
        else:
            self.font_store.append(["<Font Yok>", ""])
            self.font_combo.set_active(0)
            print("WARNING: utils.FONT_FAMILY_LIST_SORTED is empty. Combo shows <Font Yok>.")
        
        self._update_ui_state()

    def _apply_css(self):
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .toolbar { padding: 6px; }
            .pdf-view { background-color: #6c6c6c; } /* Slightly lighter gray background maybe */
            .statusbar { padding: 4px 8px; border-top: 1px solid @borders; background-color: @theme_bg_color; }
            popover > .box { padding: 10px; }

            /* Base textview style inside popover */
            textview {
                font-family: monospace;
                min-height: 80px;
                margin-bottom: 6px;
                border-radius: 6px; /* More rounded */
                border: 1px solid @borders;
                background-color: @theme_bg_color;
                padding: 4px 6px; /* Internal padding */
            }

            /* Specific style for textview when adding NEW text */
            textview.new-text-entry {
                border: 2px solid @accent_color; /* Thicker blue border */
                background-color: @popover_bg_color; /* Match popover background (usually dark) */
                /* Optional: Add inner shadow for depth if needed */
                /* box-shadow: inset 0 1px 2px rgba(0,0,0,0.3); */
            }

            .tool-button.active { background-color: @theme_selected_bg_color; }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_ui(self):
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(self.main_box)

        header = Adw.HeaderBar()
        self.main_box.append(header)

        self.new_button = Gtk.Button(label="Yeni")
        self.new_button.connect("clicked", self.on_new_clicked)
        header.pack_start(self.new_button)

        self.open_button = Gtk.Button(label="Aç")
        self.open_button.connect("clicked", self.on_open_clicked)
        header.pack_start(self.open_button)

        save_button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        save_button_box.get_style_context().add_class("linked")

        self.save_button = Gtk.Button(label="Kaydet")
        self.save_button.get_style_context().add_class("suggested-action")
        self.save_button.connect("clicked", self.on_save_clicked)
        save_button_box.append(self.save_button)

        #patched original
        '''
        save_menu = Gio.Menu()
        save_menu.append("Farklı Kaydet...", "win.save_as")
        save_menu_button = Gtk.MenuButton(
            icon_name="pan-down-symbolic",
            menu_model=save_menu
        )
        save_button_box.append(save_menu_button)
        '''

        header.pack_start(save_button_box)

        self.undo_button = Gtk.Button.new_from_icon_name("edit-undo-symbolic")
        self.undo_button.set_tooltip_text("Geri Al (Ctrl+Z)")
        self.undo_button.connect("clicked", lambda w: self.undo_manager.undo())
        header.pack_start(self.undo_button)

        self.redo_button = Gtk.Button.new_from_icon_name("edit-redo-symbolic")
        self.redo_button.set_tooltip_text("Yinele (Ctrl+Y)")
        self.redo_button.connect("clicked", lambda w: self.undo_manager.redo())
        header.pack_start(self.redo_button)

        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        header.pack_end(menu_button)
        menu = Gio.Menu()
        #menu.append("Baskı Önizlemesi...", "win.print")
        menu.append("Farklı Kaydet...", "win.save_as")
        menu.append("Farklı Dışarı Çıkart...", "win.export_as")
        menu.append_section(None, Gio.Menu())
        menu.append("Hakkında", "win.about")
        menu.append("Kapat", "app.quit")
        popover_menu = Gtk.PopoverMenu.new_from_model(menu)
        menu_button.set_popover(popover_menu)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.main_box.append(self.stack)

        welcome_view = WelcomeView(parent_window=self)
        self.stack.add_named(welcome_view, "welcome")

        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True, vexpand=True, shrink_start_child=False)
        
        self._create_sidebar()

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, vexpand=True)
        self._create_main_toolbar()
        content_box.append(self.main_toolbar)
        
        self.pdf_scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True,
                                            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.pdf_view = Gtk.DrawingArea(content_width=1, content_height=1,
                                        hexpand=True, vexpand=True)
        self.pdf_view.set_draw_func(self.draw_pdf_page)
        self.pdf_view.add_css_class('pdf-view')

        self.pdf_viewport = Gtk.Viewport()
        self.pdf_viewport.set_child(self.pdf_view)
        self.pdf_scroll.set_child(self.pdf_viewport)
        
        content_box.append(self.pdf_scroll)

        self.paned.set_end_child(content_box)
        self.paned.set_position(200)

        self.stack.add_named(self.paned, "editor")

        status_bar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, vexpand=False)
        status_bar_box.add_css_class('statusbar')
        self.status_label = Gtk.Label(label="Hiçbir belge yüklenmedi.", xalign=0.0)
        status_bar_box.append(self.status_label)
        self.main_box.append(status_bar_box)

    def _create_sidebar(self):
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                            margin_start=6, margin_end=6, margin_top=10, margin_bottom=6)
        sidebar_box.set_size_request(190, -1)

        tools_label = Gtk.Label(label="Araçlar", xalign=0.0)
        tools_label.add_css_class('title-4')
        sidebar_box.append(tools_label)

        tools_grid = Gtk.Grid(
            row_spacing=6, 
            column_spacing=6,
            column_homogeneous=True
        )
        
        self.select_tool_button = Gtk.Button(icon_name="input-mouse-symbolic", label="Değiştir")
        self.select_tool_button.set_tooltip_text("Nesneleri Seç ve Değiştir (V)")
        self.select_tool_button.connect('clicked', self.on_tool_selected, "select")
        self.select_tool_button.add_css_class("tool-button")
        tools_grid.attach(self.select_tool_button, 0, 0, 1, 1)

        self.add_text_tool_button = Gtk.Button(icon_name="insert-text-symbolic", label="Metin Ekle")
        self.add_text_tool_button.set_tooltip_text("Yeni Metin Kutusu Ekle (T)")
        self.add_text_tool_button.connect('clicked', self.on_tool_selected, "add_text")
        self.add_text_tool_button.add_css_class("tool-button")
        tools_grid.attach(self.add_text_tool_button, 1, 0, 1, 1)

        self.add_image_tool_button = Gtk.Button(icon_name="insert-image-symbolic", label="Resim Ekle")
        self.add_image_tool_button.set_tooltip_text("Yeni Resim Ekle (I)")
        self.add_image_tool_button.connect('clicked', self.on_tool_selected, "add_image")
        self.add_image_tool_button.add_css_class("tool-button")
        tools_grid.attach(self.add_image_tool_button, 0, 1, 1, 1)

        self.drag_tool_button = Gtk.Button(icon_name="object-move-symbolic", label="Taşı")
        self.drag_tool_button.set_tooltip_text("Nesneleri Sürükle ve Taşı (M)")
        self.drag_tool_button.connect('clicked', self.on_tool_selected, "drag")
        self.drag_tool_button.add_css_class("tool-button")
        tools_grid.attach(self.drag_tool_button, 1, 1, 1, 1)

        self.add_ellipse_tool_button = Gtk.Button(icon_name="shape-circle-symbolic", label="Elips")
        self.add_ellipse_tool_button.set_tooltip_text("Elips Şekli Ekle (C)")
        self.add_ellipse_tool_button.connect('clicked', self.on_tool_selected, "add_ellipse")
        self.add_ellipse_tool_button.add_css_class("tool-button")
        tools_grid.attach(self.add_ellipse_tool_button, 0, 2, 1, 1)

        self.add_rectangle_tool_button = Gtk.Button(icon_name="shape-rectangle-symbolic", label="Kare")
        self.add_rectangle_tool_button.set_tooltip_text("Dikdörtgen Şekli Ekle (R)")
        self.add_rectangle_tool_button.connect('clicked', self.on_tool_selected, "add_rectangle")
        self.add_rectangle_tool_button.add_css_class("tool-button")
        tools_grid.attach(self.add_rectangle_tool_button, 1, 2, 1, 1)
        
        sidebar_box.append(tools_grid)

        sidebar_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, margin_top=6, margin_bottom=6))

        thumbnails_label = Gtk.Label(label="Sayfalar", xalign=0.0)
        thumbnails_label.add_css_class('title-4')
        sidebar_box.append(thumbnails_label)

        factory = PageThumbnailFactory()
        self.thumbnails_list = Gtk.GridView.new(None, factory)
        self.thumbnails_list.set_max_columns(1)
        self.thumbnails_list.set_min_columns(1)
        self.thumbnails_list.set_vexpand(True)

        self.thumbnail_selection_model = Gtk.SingleSelection(model=self.pages_model)
        self.thumbnails_list.set_model(self.thumbnail_selection_model)
        self.thumbnail_selection_model.connect("selection-changed", self.on_thumbnail_selected)

        thumbnails_scroll = Gtk.ScrolledWindow(vexpand=True)
        thumbnails_scroll.set_child(self.thumbnails_list)
        thumbnails_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_box.append(thumbnails_scroll)

        self.paned.set_start_child(sidebar_box)

    def _create_main_toolbar(self):
        self.main_toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.main_toolbar.add_css_class('toolbar')

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

        self.prev_button = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        self.prev_button.set_tooltip_text("Önceki Sayfa")
        self.prev_button.connect("clicked", self.on_prev_page)
        self.page_label = Gtk.Label(label="Sayfa 0 / 0")
        self.next_button = Gtk.Button.new_from_icon_name("go-next-symbolic")
        self.next_button.set_tooltip_text("Sonraki Sayfa")
        self.next_button.connect("clicked", self.on_next_page)
        
        self.add_page_button = Gtk.Button.new_from_icon_name("document-new-symbolic")
        self.add_page_button.set_tooltip_text("Yeni Sayfa Ekle")
        self.add_page_button.connect("clicked", self.on_add_page)
        
        self.main_toolbar.append(self.prev_button)
        self.main_toolbar.append(self.page_label)
        self.main_toolbar.append(self.next_button)
        self.main_toolbar.append(self.add_page_button)

        self.main_toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=6, margin_end=6))

        self.font_store = Gtk.ListStore(str, str)
        self.font_store.append(["Fontlar yükleniyor...", ""])
        self.font_combo = Gtk.ComboBox(model=self.font_store)
        cell = Gtk.CellRendererText()
        self.font_combo.pack_start(cell, True)
        self.font_combo.add_attribute(cell, "text", 0)
        self.font_combo.set_active(0)
        self.font_combo.set_tooltip_text("Yazı Tipi Ailesi")
        self.font_combo.connect("changed", self.on_text_format_changed)
        self.font_combo.set_sensitive(False)
        self.main_toolbar.append(self.font_combo)

        self.font_size_spin = Gtk.SpinButton.new_with_range(6, 96, 1)
        self.font_size_spin.set_value(11);
        self.font_size_spin.set_tooltip_text("Yazı Tipi Boyutu")
        self.font_size_spin.connect("value-changed", self.on_text_format_changed)
        self.main_toolbar.append(self.font_size_spin)

        self.bold_button = Gtk.ToggleButton(icon_name="format-text-bold-symbolic")
        self.bold_button.set_tooltip_text("Bold")
        self.bold_button.connect("toggled", self.on_text_format_changed)
        self.main_toolbar.append(self.bold_button)

        self.italic_button = Gtk.ToggleButton(icon_name="format-text-italic-symbolic")
        self.italic_button.set_tooltip_text("Italic")
        self.italic_button.connect("toggled", self.on_text_format_changed)
        self.main_toolbar.append(self.italic_button)

        self.color_button = Gtk.ColorButton()
        default_rgba = Gdk.RGBA()
        default_rgba.parse("black")
        self.color_button.set_rgba(default_rgba)
        self.color_button.set_tooltip_text("Yazı Tipi Rengi")
        self.color_button.connect("color-set", self.on_text_format_changed)
        self.main_toolbar.append(self.color_button)

        # Shape styling controls
        self.main_toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=6, margin_end=6))

        self.shape_fill_button = Gtk.ColorButton()
        self.shape_fill_button.set_title("Şekil Dolgu Rengini Seç")
        fill_rgba = Gdk.RGBA()
        fill_rgba.parse("white")
        self.shape_fill_button.set_rgba(fill_rgba)
        self.shape_fill_button.set_tooltip_text("Şekil Dolgu Rengi")
        self.shape_fill_button.connect("color-set", self.on_shape_format_changed)
        self.main_toolbar.append(self.shape_fill_button)

        self.shape_transparent_toggle = Gtk.ToggleButton(label="Saydam")
        self.shape_transparent_toggle.set_active(True)  # Default transparent
        self.shape_transparent_toggle.set_tooltip_text("Şekli Saydam Yap (Dolgu Yok) / Renk Dolu")
        self.shape_transparent_toggle.connect("toggled", self.on_shape_format_changed)
        self.main_toolbar.append(self.shape_transparent_toggle)

        self.shape_stroke_button = Gtk.ColorButton()
        self.shape_stroke_button.set_title("Şekil Çizgi Rengini Seç")
        stroke_rgba = Gdk.RGBA()
        stroke_rgba.parse("black")
        self.shape_stroke_button.set_rgba(stroke_rgba)
        self.shape_stroke_button.set_tooltip_text("Şekil Çizgi Rengi")
        self.shape_stroke_button.connect("color-set", self.on_shape_format_changed)
        self.main_toolbar.append(self.shape_stroke_button)

        self.shape_stroke_width_spin = Gtk.SpinButton.new_with_range(0.5, 10, 0.5)
        self.shape_stroke_width_spin.set_value(2.0)
        self.shape_stroke_width_spin.set_tooltip_text("Şekil Çizgi Kalınlığı")
        self.shape_stroke_width_spin.connect("value-changed", self.on_shape_format_changed)
        self.main_toolbar.append(self.shape_stroke_width_spin)

    def _setup_controllers(self):
        # Main window drag/drop for opening/merging PDFs
        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect('drop', self.on_drop)
        self.add_controller(drop_target)

        scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll_controller.connect('scroll', self.on_scroll_zoom)
        self.pdf_view.add_controller(scroll_controller)

        click_controller = Gtk.GestureClick.new()
        click_controller.connect('pressed', self.on_pdf_view_pressed)
        self.pdf_view.add_controller(click_controller)

        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect('key-pressed', self.on_key_pressed)
        self.add_controller(key_controller)

        drag_controller = Gtk.GestureDrag.new()
        drag_controller.set_button(Gdk.BUTTON_PRIMARY)
        drag_controller.connect("drag-begin", self.on_drag_begin)
        drag_controller.connect("drag-update", self.on_drag_update)
        drag_controller.connect("drag-end", self.on_drag_end)
        self.pdf_view.add_controller(drag_controller)

        # Drop target for thumbnails (for PDF merge)
        thumbnail_drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        thumbnail_drop.connect('drop', self.on_thumbnail_drop)
        self.thumbnails_list.add_controller(thumbnail_drop)

    def _connect_actions(self):
        action_save_as = Gio.SimpleAction.new('save_as', None)
        action_save_as.connect('activate', self.on_save_as)
        self.add_action(action_save_as)

        action_export_as = Gio.SimpleAction.new('export_as', None)
        action_export_as.connect('activate', self.on_export_as)
        self.add_action(action_export_as)

        action_print = Gio.SimpleAction.new('print', None)
        action_print.connect('activate', self.on_print_activated)
        self.add_action(action_print)

        action_about = Gio.SimpleAction.new('about', None)
        action_about.connect('activate', self.on_about_activated)
        self.add_action(action_about)

        action_quick_guide = Gio.SimpleAction.new('quick_guide', None)
        action_quick_guide.connect('activate', self._on_quick_guide_activated)
        self.add_action(action_quick_guide)

        action_undo = Gio.SimpleAction.new("undo", None)
        action_undo.connect("activate", lambda a, p: self.undo_manager.undo())
        self.add_action(action_undo)

        action_redo = Gio.SimpleAction.new("redo", None)
        action_redo.connect("activate", lambda a, p: self.undo_manager.redo())
        self.add_action(action_redo)

        app = self.get_application()
        if app:
            app.set_accels_for_action("win.undo", ["<Control>z"])
            app.set_accels_for_action("win.redo", ["<Control>y", "<Control><Shift>z"])
            app.set_accels_for_action("win.print", ["<Control>p"])

    def _update_ui_state(self):
        has_doc = self.doc is not None
        page_count = pdf_handler.get_page_count(self.doc) if self.doc else 0
        has_pages = page_count > 0
        can_go_prev = has_pages and self.current_page_index > 0
        can_go_next = has_pages and self.current_page_index < page_count - 1

        self.save_button.set_sensitive(has_doc and self.document_modified)
        self.lookup_action("save_as").set_enabled(has_doc)
        self.lookup_action("export_as").set_enabled(has_doc)
        self.lookup_action("print").set_enabled(has_doc)
        self.prev_button.set_sensitive(can_go_prev)
        self.next_button.set_sensitive(can_go_next)

        text_tool_active = self.tool_mode == "add_text"
        text_selected = self.selected_text is not None
        shape_selected = self.selected_shape is not None
        format_enabled_base = ((self.selected_text is not None) or (self.tool_mode == "add_text")) and (self.selected_image is None) and (self.selected_shape is None)
        
        self.font_combo.set_sensitive(format_enabled_base and not self.font_scan_in_progress)
        self.font_size_spin.set_sensitive(format_enabled_base)
        self.color_button.set_sensitive(format_enabled_base)
        if self.bold_button: self.bold_button.set_sensitive(format_enabled_base)
        if self.italic_button: self.italic_button.set_sensitive(format_enabled_base)

        # Shape format controls - enabled when shape is selected OR shape tool is active
        shape_controls_enabled = shape_selected or self.tool_mode in ("add_ellipse", "add_rectangle")
        self.shape_fill_button.set_sensitive(shape_controls_enabled)
        self.shape_stroke_button.set_sensitive(shape_controls_enabled)
        self.shape_stroke_width_spin.set_sensitive(shape_controls_enabled)
        self.shape_transparent_toggle.set_sensitive(shape_controls_enabled)
        
        if shape_selected:
            self.shape_transparent_toggle.handler_block_by_func(self.on_shape_format_changed)
            self.shape_transparent_toggle.set_active(self.selected_shape.is_transparent)
            self.shape_transparent_toggle.handler_unblock_by_func(self.on_shape_format_changed)

        if text_selected:
            self._update_text_format_controls(self.selected_text)
        elif not (self.text_edit_popover and self.text_edit_popover.is_visible()):
            self._update_text_format_controls(None)

        if shape_selected:
            self._update_shape_format_controls(self.selected_shape)
        else:
            self._update_shape_format_controls(None)

        self.select_tool_button.get_style_context().remove_class('active')
        self.add_text_tool_button.get_style_context().remove_class('active')
        self.add_image_tool_button.get_style_context().remove_class('active')
        self.drag_tool_button.get_style_context().remove_class('active')
        self.add_ellipse_tool_button.get_style_context().remove_class('active')
        self.add_rectangle_tool_button.get_style_context().remove_class('active')
        
        if self.tool_mode == "select":
            self.select_tool_button.get_style_context().add_class('active')
            self.pdf_view.set_cursor(None)
        elif self.tool_mode == "add_text":
            self.add_text_tool_button.get_style_context().add_class('active')
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        elif self.tool_mode == "add_image":
            self.add_image_tool_button.get_style_context().add_class('active')
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("cell"))
        elif self.tool_mode == "drag":
            self.drag_tool_button.get_style_context().add_class('active')
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("move"))
        elif self.tool_mode == "add_ellipse":
            self.add_ellipse_tool_button.get_style_context().add_class('active')
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        elif self.tool_mode == "add_rectangle":
            self.add_rectangle_tool_button.get_style_context().add_class('active')
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("crosshair"))

        if has_doc:
            self.stack.set_visible_child_name("editor")
        else:
            self.stack.set_visible_child_name("welcome")

        if has_doc:
            self.update_page_label()
            if self.document_modified and not self.get_title().endswith("*"):
                self.set_title(self.get_title() + "*")
            elif not self.document_modified and self.get_title().endswith("*"):
                self.set_title(self.get_title()[:-1])
        else:
            self.page_label.set_text("Sayfa 0 / 0")
            self.zoom_label.set_text("100%")
            self.status_label.set_text("Bir dosya açın veya sürükleyip bırakın.")
            self.set_title("Word-Sys's PDF Editor")
            self.document_modified = False
        
        self._update_undo_redo_buttons()

    def on_about_activated(self, action, param):
        about_dialog = Gtk.AboutDialog(transient_for=self, modal=True)

        about_dialog.set_program_name("Word-Sys's PDF Editor")
        about_dialog.set_version("1.7.2") 
        about_dialog.set_authors(["Barın Güzeldemirci (word-sys)"])
        
        try:
            about_dialog.set_license_type(Gtk.License.GPL_3_0_OR_LATER)
        except AttributeError:
            try:
                about_dialog.set_license_type(Gtk.License.GPL_3_0)
            except AttributeError:
                print("Warning: Gtk.License.GPL_3_0 not found, setting custom license type.")
                about_dialog.set_license_type(Gtk.License.CUSTOM)
        try:
            license_path = Path(__file__).resolve().parent.parent / "LICENSE"
            if not license_path.exists():
                license_path = Path("/usr/share/common-licenses/GPL-3")

            if license_path.exists():
                with open(license_path, 'r', encoding='utf-8') as f:
                    license_text = f.read()
                about_dialog.set_license(license_text)
                about_dialog.set_wrap_license(True)
            else:
                about_dialog.set_license("GNU General Public License v3.0 or later.\nFull text could not be loaded.")
        except Exception as e:
            print(f"Error reading LICENSE file: {e}")
            about_dialog.set_license("Error reading license text.")

        about_dialog.set_website("https://github.com/word-sys/pardf")
        about_dialog.set_website_label("Proje GitHub Sayfası")

        comments_text = "Linux için Basit PDF Metin ve Şekil Düzenleyici.\n\n" + \
                        """Word-Sys's PDF Editor is an open-source tool for editing content in PDF files on Linux."""
        about_dialog.set_comments(comments_text)

        try:
            app_icon_path = Path(__file__).resolve().parent / "img" / "f-pv1.svg"
            if app_icon_path.exists():
                texture = Gdk.Texture.new_from_filename(str(app_icon_path))
                about_dialog.set_logo(texture)
            else:
                about_dialog.set_logo_icon_name("application-x-executable")
        except Exception as e:
            print(f"Warning: Could not load application icon for About dialog: {e}")
            about_dialog.set_logo_icon_name("application-x-executable")

        about_dialog.set_copyright("© 2024-2025 Barın Güzeldemirci")
        about_dialog.present()

    #original
    '''
    def load_document(self, filepath):
        if self.check_unsaved_changes():
            return

        self.close_document()

        self.status_label.set_text(f"Yükleniyor {os.path.basename(filepath)}...")
        GLib.idle_add(self._show_loading_state)

        def _load_async():
            doc, error_msg = pdf_handler.load_pdf_document(filepath)
            GLib.idle_add(self._finish_loading, doc, error_msg, filepath)

        thread = threading.Thread(target=_load_async)
        thread.daemon = True
        thread.start()
    '''

    #patched
    def load_document(self, filepath, target_page=0):
        if self.check_unsaved_changes():
            return

        self.close_document()

        self.status_label.set_text(f"Yükleniyor {os.path.basename(filepath)}...")
        GLib.idle_add(self._show_loading_state)

        def _load_async():
            doc, error_msg = pdf_handler.load_pdf_document(filepath)
            GLib.idle_add(self._finish_loading, doc, error_msg, filepath, target_page)

        thread = threading.Thread(target=_load_async)
        thread.daemon = True
        thread.start()

    def _show_loading_state(self):
        self.open_button.set_sensitive(False)
        self.save_button.set_sensitive(False)
        self.lookup_action("save_as").set_enabled(False)
        self.lookup_action("export_as").set_enabled(False)
        self.prev_button.set_sensitive(False)
        self.next_button.set_sensitive(False)
        self.font_combo.set_sensitive(False)
        self.font_size_spin.set_sensitive(False)
        self.color_button.set_sensitive(False)
        self.select_tool_button.set_sensitive(False)
        self.add_text_tool_button.set_sensitive(False)
        self.stack.set_visible_child_name("welcome")

    #original
    '''
    def _finish_loading(self, doc, error_msg, filepath):
        if error_msg:
            show_error_dialog(self, error_msg)
            self.status_label.set_text("Doküman yüklenemedi.")
            self.close_document()
        elif doc:
            self.doc = doc
            self.is_repaired_file = doc.is_repaired
            if self.is_repaired_file:
                print("DEBUG: Bu PDF dosyası açılırken onarıldı. Artımlı kaydetme devre dışı.")
            self.current_file_path = filepath
            self.original_file_path = filepath
            self.allow_incremental_save = True
            self.current_page_index = 0
            self.set_title(f"Word-Sys's PDF Editor - {os.path.basename(filepath)}")
            self.status_label.set_text(f"Küçük resimler yükleniyor...")
            GLib.idle_add(self._load_thumbnails)

        self.open_button.set_sensitive(True)
        self.select_tool_button.set_sensitive(True)
        self.add_text_tool_button.set_sensitive(True)
        self._update_ui_state()
    '''

    #patched
    def _finish_loading(self, doc, error_msg, filepath, target_page=0):
        if error_msg:
            show_error_dialog(self, error_msg)
            self.status_label.set_text("Doküman yüklenemedi.")
            self.close_document()
        elif doc:
            self.doc = doc
            self.is_repaired_file = doc.is_repaired
            if self.is_repaired_file:
                print("DEBUG: Bu PDF dosyası açılırken onarıldı.")
            self.current_file_path = filepath
            self.original_file_path = filepath
            self.allow_incremental_save = True
            
            self.current_page_index = target_page 
            
            self.set_title(f"Word-Sys's PDF Editor - {os.path.basename(filepath)}")
            self.status_label.set_text(f"Küçük resimler yükleniyor...")
            
            self.target_page_after_load = target_page
            GLib.idle_add(self._load_thumbnails)

        self.open_button.set_sensitive(True)
        self.select_tool_button.set_sensitive(True)
        self.add_text_tool_button.set_sensitive(True)
        self._update_ui_state()

    def _load_thumbnails(self):
        if not self.doc:
            return

        self.pages_model.remove_all()
        page_count = pdf_handler.get_page_count(self.doc)

        self.thumb_load_iter = 0
        def _load_next_thumb():
            if self.thumb_load_iter < page_count:
                index = self.thumb_load_iter
                
                thumb = pdf_handler.generate_thumbnail(self.doc, index, target_width=150)

                if thumb:
                    pdf_page_obj = PdfPage(index=index, thumbnail=thumb)
                    self.pages_model.append(pdf_page_obj)
                self.thumb_load_iter += 1
                if index % 5 == 0 or index == page_count - 1:
                    self.status_label.set_text(f"Küçük resim yüklendi {index + 1}/{page_count}")
                return GLib.SOURCE_CONTINUE 
            else:
                if self.current_file_path:
                    self.status_label.set_text(f"Yüklendi: {os.path.basename(self.current_file_path)}")
                else:
                    self.status_label.set_text("Yeni belge yüklendi.")                
                if page_count > 0:
                    target = getattr(self, 'target_page_after_load', 0)
                    if target >= page_count: target = 0
                    self._load_page(target)
                else:
                    self._update_ui_state()
                return GLib.SOURCE_REMOVE

        GLib.idle_add(_load_next_thumb)


    def _load_page(self, page_index, preserve_scroll=False):
        current_v_scroll = 0
        current_h_scroll = 0
        if preserve_scroll:
            v_adj = self.pdf_scroll.get_vadjustment()
            h_adj = self.pdf_scroll.get_hadjustment()
            if v_adj:
                current_v_scroll = v_adj.get_value()
            if h_adj:
                current_h_scroll = h_adj.get_value()

        if not self.doc or not (0 <= page_index < pdf_handler.get_page_count(self.doc)):
            print(f"Warning: Invalid attempt to load page {page_index}.")
            return

        self.commit_pending_format_change()
        self.undo_manager.clear()

        self.current_page_index = page_index
        self.selected_text = None
        self.selected_image = None
        self.selected_shape = None
        self.hide_text_editor()

        texts, error = pdf_handler.extract_editable_text(self.doc, page_index)
        if error:
            show_error_dialog(self, f"Could not extract text structure from page {page_index + 1}.\n{error}")
            self.editable_texts = []
        else:
            self.editable_texts = texts
            
        images, error = pdf_handler.extract_editable_images(self.doc, page_index)
        if error:
            show_error_dialog(self, f"Sayfa {page_index + 1} içinden resimler çıkarılamadı.\n{error}")
            self.editable_images = []
        else:
            self.editable_images = images

        page = self.doc.load_page(page_index)
        self.current_pdf_page_width = int(page.rect.width * self.zoom_level)
        self.current_pdf_page_height = int(page.rect.height * self.zoom_level)

        print(f"DEBUG: Setting pdf_view content size: {self.current_pdf_page_width} x {self.current_pdf_page_height}")
        self.pdf_view.set_content_width(self.current_pdf_page_width)
        self.pdf_view.set_content_height(self.current_pdf_page_height)

        self.pdf_view.queue_draw()
        
        if preserve_scroll:
            GLib.idle_add(self.pdf_scroll.get_vadjustment().set_value, current_v_scroll)
            GLib.idle_add(self.pdf_scroll.get_hadjustment().set_value, current_h_scroll)

        self._sync_thumbnail_selection()
        self._update_ui_state()

    def close_document(self):
        self.undo_manager.clear()
        self.is_repaired_file = False
        pdf_handler.close_pdf_document(self.doc)
        self.doc = None
        self.current_file_path = None
        self.current_page_index = 0
        self.editable_texts = []
        self.editable_images = []
        self.editable_shapes = []
        self.selected_text = None
        self.selected_image = None
        self.selected_shape = None
        self.hide_text_editor()
        self.pages_model.remove_all()
        self.document_modified = False
        self.pdf_view.set_content_width(1)
        self.pdf_view.set_content_height(1)
        self.pdf_view.queue_draw()
        self._update_ui_state()

    #original
    '''
    def save_document(self, save_path, incremental=False):
        if not self.doc or self.is_saving:
            return
        self.is_saving = True
        self.status_label.set_text(f"Kaydediliyor {os.path.basename(save_path)}...")

        if self.text_edit_popover and self.text_edit_popover.is_visible():
            self._apply_and_hide_editor(force_apply=True)

        success, error_msg = pdf_handler.save_document(self.doc, save_path, incremental=incremental)

        self.is_saving = False
        if success:
            self.current_file_path = save_path
            self.document_modified = False
            self.set_title(f"Word-Sys's PDF Editor - {os.path.basename(save_path)}")
            self.status_label.set_text(f"Belge kaydedildi: {os.path.basename(save_path)}")
            
            self._load_page(self.current_page_index, preserve_scroll=True)
        else:
            show_error_dialog(self, f"PDF kaydedilirken hata oluştu: {error_msg}")
            self.status_label.set_text("Kaydetme başarısız oldu.")

        self._update_ui_state()
    '''

    #patchedv1
    '''
    def save_document(self, save_path, incremental=False):
        if not self.doc or self.is_saving:
            return

        self.is_saving = True
        self.status_label.set_text(f"Kaydediliyor {os.path.basename(save_path)}...")

        if self.text_edit_popover and self.text_edit_popover.is_visible():
            self._apply_and_hide_editor(force_apply=True)

        success, error_msg = pdf_handler.save_document(self.doc, save_path, incremental=incremental)

        self.is_saving = False
        
        if success:
            self.current_file_path = save_path
            self.document_modified = False
            self.set_title(f"Word-Sys's PDF Editor - {os.path.basename(save_path)}")
            self.status_label.set_text(f"Belge kaydedildi: {os.path.basename(save_path)}")
            self._load_page(self.current_page_index, preserve_scroll=True)
        else:
            show_error_dialog(self, f"PDF kaydedilirken hata oluştu: {error_msg}")
            self.status_label.set_text("Kaydetme başarısız oldu.")

        self._update_ui_state()
    '''

    #patchedv2
    def save_document(self, save_path, incremental=False):
        if not self.doc or self.is_saving:
            return
        page_to_restore = self.current_page_index
        self.is_saving = True
        self.status_label.set_text(f"Kaydediliyor {os.path.basename(save_path)}...")
        if self.text_edit_popover and self.text_edit_popover.is_visible():
            self._apply_and_hide_editor(force_apply=True)
            
        # Bake unbaked shapes permanently before saving (PyMuPDF Redaction isn't reliable for live vector editing)
        for shape in self.editable_shapes:
            if not getattr(shape, 'is_baked', False):
                pdf_handler.apply_object_edit(self.doc, shape)
                shape.is_baked = True

        success, error_msg = pdf_handler.save_document(self.doc, save_path, incremental=False)
        self.is_saving = False
        
        if success:
            print(f"DEBUG: Kayıt başarılı. Dosya yeniden yükleniyor: {save_path}")
            self.document_modified = False 
            self.load_document(save_path, target_page=page_to_restore)
            self.status_label.set_text(f"Kaydedildi ve yeniden yüklendi: {os.path.basename(save_path)}")
        else:
            show_error_dialog(self, f"PDF kaydedilirken hata oluştu: {error_msg}")
            self.status_label.set_text("Kaydetme başarısız oldu.")

        self._update_ui_state()

    def draw_pdf_page(self, area, cr, width, height):
        if not self.doc or self.current_pdf_page_width <= 0:
            cr.set_source_rgb(0.42, 0.42, 0.42)
            cr.paint()
            return

        page_w = self.current_pdf_page_width
        page_h = self.current_pdf_page_height
        page_offset_x = max(0, (width - page_w) / 2.0)
        page_offset_y = max(0, (height - page_h) / 2.0)

        cr.set_source_rgb(0.42, 0.42, 0.42)
        cr.paint()

        cr.save()
        cr.set_source_rgba(0, 0, 0, 0.15)
        cr.rectangle(page_offset_x + 4.0, page_offset_y + 4.0, page_w, page_h)
        cr.fill()
        cr.restore()

        cr.save()
        cr.translate(page_offset_x, page_offset_y)
        try:
            page_surface = cr.get_target().create_similar_image(cairo.FORMAT_ARGB32, int(page_w), int(page_h))
        except Exception:
            page_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(page_w), int(page_h))

        page_cr = cairo.Context(page_surface)
        pdf_handler.draw_page_to_cairo(page_cr, self.doc, self.current_page_index, self.zoom_level)

        cr.set_source_surface(page_surface, 0, 0)
        cr.paint()
        cr.restore()

        if self.dragged_object:
            # Draw semi-transparent shadow at the original position
            if self.dragged_object.original_bbox:
                orig_x1, orig_y1, orig_x2, orig_y2 = self.dragged_object.original_bbox
                cr.save()
                # Semi-transparent gray overlay (not solid white) so user sees where object was
                cr.set_source_rgba(0.85, 0.85, 0.85, 0.55)
                cr.rectangle(page_offset_x + (orig_x1 * self.zoom_level),
                            page_offset_y + (orig_y1 * self.zoom_level),
                            (orig_x2 - orig_x1) * self.zoom_level,
                            (orig_y2 - orig_y1) * self.zoom_level)
                cr.fill()
                # Dashed border around original position
                cr.set_source_rgba(0.5, 0.5, 0.5, 0.7)
                cr.set_line_width(1.5)
                cr.set_dash([4.0, 3.0])
                cr.rectangle(page_offset_x + (orig_x1 * self.zoom_level),
                            page_offset_y + (orig_y1 * self.zoom_level),
                            (orig_x2 - orig_x1) * self.zoom_level,
                            (orig_y2 - orig_y1) * self.zoom_level)
                cr.stroke()
                cr.set_dash([])
                cr.restore()

            x1, y1, x2, y2 = self.dragged_object.bbox
            ghost_x = page_offset_x + (x1 * self.zoom_level)
            ghost_y = page_offset_y + (y1 * self.zoom_level)
            ghost_w = (x2 - x1) * self.zoom_level
            ghost_h = (y2 - y1) * self.zoom_level

            cr.save()
            if isinstance(self.dragged_object, EditableImage) and self.dragged_object.image_bytes:
                try:
                    loader = GdkPixbuf.PixbufLoader.new()
                    loader.write(self.dragged_object.image_bytes)
                    loader.close()
                    pixbuf = loader.get_pixbuf()
                    if pixbuf and int(ghost_w) > 0 and int(ghost_h) > 0:
                        scaled_pixbuf = pixbuf.scale_simple(int(ghost_w), int(ghost_h), GdkPixbuf.InterpType.BILINEAR)
                        if scaled_pixbuf:
                            Gdk.cairo_set_source_pixbuf(cr, scaled_pixbuf, ghost_x, ghost_y)
                            cr.paint_with_alpha(0.6)
                except Exception as e:
                    # Fallback: draw a semi-transparent colored rectangle
                    cr.set_source_rgba(0.2, 0.5, 0.8, 0.5)
                    cr.rectangle(ghost_x, ghost_y, ghost_w, ghost_h)
                    cr.fill()
            elif isinstance(self.dragged_object, EditableText):
                layout = PangoCairo.create_layout(cr)
                font_desc_str = f"{self.dragged_object.font_family_base} {self.dragged_object.font_size * self.zoom_level}"
                if self.dragged_object.is_bold: font_desc_str += " Bold"
                if self.dragged_object.is_italic: font_desc_str += " Italic"

                font_desc = Pango.FontDescription(font_desc_str)
                layout.set_font_description(font_desc)
                layout.set_text(self.dragged_object.text, -1)

                r, g, b = self.dragged_object.color
                cr.set_source_rgba(r, g, b, 0.6)
                
                # Pango layout draws from top-left, we must align the layout top to the ghost_y bounding box
                # However, original code used move_to(ghost_x, ghost_y) which drew it offset if the bbox included ascenders
                # The bbox already handles the bounding box correctly, so drawing at ghost_x, ghost_y is fine for top-left
                cr.move_to(ghost_x, ghost_y)
                PangoCairo.show_layout(cr, layout)
            elif isinstance(self.dragged_object, EditableShape):
                # Draw ghost for shapes too
                if not self.dragged_object.is_transparent:
                    fill_r, fill_g, fill_b = self.dragged_object.fill_color
                    cr.set_source_rgba(fill_r, fill_g, fill_b, 0.4)
                    if self.dragged_object.shape_type == EditableShape.SHAPE_RECTANGLE:
                        cr.rectangle(ghost_x, ghost_y, ghost_w, ghost_h)
                        cr.fill()
                    elif self.dragged_object.shape_type == EditableShape.SHAPE_ELLIPSE:
                        if ghost_w > 0 and ghost_h > 0:
                            cr.save()
                            cr.translate(ghost_x + ghost_w / 2.0, ghost_y + ghost_h / 2.0)
                            cr.scale(ghost_w / 2.0, ghost_h / 2.0)
                            cr.arc(0, 0, 1, 0, 2 * math.pi)
                            cr.restore()
                            cr.fill()
                stroke_r, stroke_g, stroke_b = self.dragged_object.stroke_color
                cr.set_source_rgba(stroke_r, stroke_g, stroke_b, 0.6)
                cr.set_line_width(self.dragged_object.stroke_width)
                if self.dragged_object.shape_type == EditableShape.SHAPE_RECTANGLE:
                    cr.rectangle(ghost_x, ghost_y, ghost_w, ghost_h)
                    cr.stroke()
                elif self.dragged_object.shape_type == EditableShape.SHAPE_ELLIPSE:
                    if ghost_w > 0 and ghost_h > 0:
                        cr.save()
                        cr.translate(ghost_x + ghost_w / 2.0, ghost_y + ghost_h / 2.0)
                        cr.scale(ghost_w / 2.0, ghost_h / 2.0)
                        cr.arc(0, 0, 1, 0, 2 * math.pi)
                        cr.restore()
                        cr.stroke()
            cr.restore()
            
        # Draw all shapes on current page (100% overlay, no PyMuPDF baking until save)
        for shape in self.editable_shapes:
            if shape.page_number != self.current_page_index:
                continue
            
            x1, y1, x2, y2 = shape.bbox
            draw_x = page_offset_x + (x1 * self.zoom_level)
            draw_y = page_offset_y + (y1 * self.zoom_level)
            draw_w = (x2 - x1) * self.zoom_level
            draw_h = (y2 - y1) * self.zoom_level
            
            if abs(draw_w) < 1.0 or abs(draw_h) < 1.0:
                continue
            
            cr.save()
            if not shape.is_transparent:
                fill_r, fill_g, fill_b = shape.fill_color
                cr.set_source_rgba(fill_r, fill_g, fill_b, 1.0)
                if shape.shape_type == EditableShape.SHAPE_RECTANGLE:
                    cr.rectangle(draw_x, draw_y, draw_w, draw_h)
                    cr.fill()
                elif shape.shape_type == EditableShape.SHAPE_ELLIPSE:
                    cr.save()
                    cr.translate(draw_x + draw_w / 2.0, draw_y + draw_h / 2.0)
                    cr.scale(draw_w / 2.0, draw_h / 2.0)
                    cr.arc(0, 0, 1, 0, 2 * math.pi)
                    cr.restore()
                    cr.fill()
            
            stroke_r, stroke_g, stroke_b = shape.stroke_color
            cr.set_source_rgba(stroke_r, stroke_g, stroke_b, 1.0)
            cr.set_line_width(shape.stroke_width)
            
            if shape.shape_type == EditableShape.SHAPE_RECTANGLE:
                cr.rectangle(draw_x, draw_y, draw_w, draw_h)
                cr.stroke()
            elif shape.shape_type == EditableShape.SHAPE_ELLIPSE:
                cr.save()
                cr.translate(draw_x + draw_w / 2.0, draw_y + draw_h / 2.0)
                cr.scale(draw_w / 2.0, draw_h / 2.0)
                cr.arc(0, 0, 1, 0, 2 * math.pi)
                cr.restore()
                cr.stroke()
            cr.restore()

        
        # Draw temporary shape if being created
        if self.temp_shape:
            x1, y1, x2, y2 = self.temp_shape.bbox
            draw_x = page_offset_x + (x1 * self.zoom_level)
            draw_y = page_offset_y + (y1 * self.zoom_level)
            draw_w = (x2 - x1) * self.zoom_level
            draw_h = (y2 - y1) * self.zoom_level
            
            stroke_r, stroke_g, stroke_b = self.temp_shape.stroke_color
            cr.set_source_rgba(stroke_r, stroke_g, stroke_b, 0.7)  # Slightly transparent while creating
            cr.set_line_width(self.temp_shape.stroke_width)
            
            if self.temp_shape.shape_type == EditableShape.SHAPE_RECTANGLE:
                cr.rectangle(draw_x, draw_y, draw_w, draw_h)
                cr.stroke()
            elif self.temp_shape.shape_type == EditableShape.SHAPE_ELLIPSE:
                cr.save()
                cr.translate(draw_x + draw_w / 2.0, draw_y + draw_h / 2.0)
                cr.scale(draw_w / 2.0, draw_h / 2.0)
                cr.arc(0, 0, 1, 0, 2 * math.pi)
                cr.restore()
                cr.stroke()

        # Draw temporary image zone if being created - use GTK4 accent color
        if self.temp_image_bbox:
            x1, y1, x2, y2 = self.temp_image_bbox
            draw_x = page_offset_x + (x1 * self.zoom_level)
            draw_y = page_offset_y + (y1 * self.zoom_level)
            draw_w = (x2 - x1) * self.zoom_level
            draw_h = (y2 - y1) * self.zoom_level

            # Use GTK4 accent color (same lookup as selection rect)
            style_context = area.get_style_context()
            found_img, img_rgba = style_context.lookup_color("accent_color")
            if not found_img:
                found_img, img_rgba = style_context.lookup_color("theme_selected_bg_color")
            if not found_img:
                img_rgba = Gdk.RGBA()
                img_rgba.parse("#3584e4")

            cr.set_source_rgba(img_rgba.red, img_rgba.green, img_rgba.blue, 0.25)
            cr.rectangle(draw_x, draw_y, draw_w, draw_h)
            cr.fill()

            cr.set_source_rgba(img_rgba.red, img_rgba.green, img_rgba.blue, 0.85)
            cr.set_line_width(2.0)
            cr.set_dash([5.0, 4.0])
            cr.rectangle(draw_x, draw_y, draw_w, draw_h)
            cr.stroke()
            cr.set_dash([])

        selected_obj = self.selected_text or self.selected_image or self.selected_shape
        if selected_obj and not self.dragged_object:
            is_image = isinstance(selected_obj, EditableImage)
            style_context = area.get_style_context()
            color_name = "accent_color"
            default_color = "#3584e4"

            found, rgba = style_context.lookup_color("accent_color")
            if not found:
                # Try fallback colors
                found, rgba = style_context.lookup_color("theme_selected_bg_color")
            if not found:
                rgba = Gdk.RGBA()
                rgba.parse("#3584e4")  # Final fallback

            x1, y1, x2, y2 = selected_obj.bbox
            padding = 3.0
            rect_x = page_offset_x + (x1 * self.zoom_level) - padding
            rect_y = page_offset_y + (y1 * self.zoom_level) - padding
            rect_w = (x2 - x1) * self.zoom_level + (2 * padding)
            rect_h = (y2 - y1) * self.zoom_level + (2 * padding)

            cr.save()
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.95)
            cr.set_line_width(2.5 if is_image else 2.0)
            if is_image:
                cr.set_dash([4.0, 4.0])

            radius = min(5.0, rect_w / 2.0, rect_h / 2.0)
            cr.new_sub_path()
            cr.arc(rect_x + radius, rect_y + radius, radius, math.pi, 1.5 * math.pi)
            cr.arc(rect_x + rect_w - radius, rect_y + radius, radius, 1.5 * math.pi, 2.0 * math.pi)
            cr.arc(rect_x + rect_w - radius, rect_y + rect_h - radius, radius, 0, 0.5 * math.pi)
            cr.arc(rect_x + radius, rect_y + rect_h - radius, radius, 0.5 * math.pi, math.pi)
            cr.close_path()
            cr.stroke()
            cr.restore()

            # Draw resize handles for selected objects (not for text - use font size to resize text)
            is_text = isinstance(selected_obj, EditableText)
            if not is_text:
                handle_size = 8.0
                handle_color_rgba = rgba
                
                # Define the 8 resize handle positions (corners and edges)
                handles = [
                    ("nw", rect_x, rect_y),                                    # top-left
                    ("ne", rect_x + rect_w, rect_y),                          # top-right
                    ("sw", rect_x, rect_y + rect_h),                          # bottom-left
                    ("se", rect_x + rect_w, rect_y + rect_h),                 # bottom-right
                    ("n", rect_x + rect_w / 2.0, rect_y),                     # top
                    ("s", rect_x + rect_w / 2.0, rect_y + rect_h),            # bottom
                    ("w", rect_x, rect_y + rect_h / 2.0),                     # left
                    ("e", rect_x + rect_w, rect_y + rect_h / 2.0),            # right
                ]
                
                cr.save()
                cr.set_source_rgba(handle_color_rgba.red, handle_color_rgba.green, handle_color_rgba.blue, 1.0)
                for handle_name, handle_x, handle_y in handles:
                    # Draw small square handle
                    cr.rectangle(handle_x - handle_size / 2.0, handle_y - handle_size / 2.0, handle_size, handle_size)
                    cr.fill()
                    # Draw white border
                    cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
                    cr.rectangle(handle_x - handle_size / 2.0, handle_y - handle_size / 2.0, handle_size, handle_size)
                    cr.set_line_width(1.0)
                    cr.stroke()
                    # Reset source for next handle
                    cr.set_source_rgba(handle_color_rgba.red, handle_color_rgba.green, handle_color_rgba.blue, 1.0)
                cr.restore()

    def _find_text_at_pos(self, page_x, page_y):
        for text_obj in reversed(self.editable_texts):
            if not text_obj.bbox: continue
            x1, y1, x2, y2 = text_obj.bbox
            tolerance = 2 / self.zoom_level
            if (x1 - tolerance) <= page_x <= (x2 + tolerance) and \
               (y1 - tolerance) <= page_y <= (y2 + tolerance):
                return text_obj
        return None

    def _find_image_at_pos(self, page_x, page_y):
        for img_obj in reversed(self.editable_images):
            if not img_obj.bbox: continue
            x1, y1, x2, y2 = img_obj.bbox
            if x1 <= page_x <= x2 and y1 <= page_y <= y2:
                return img_obj
        return None

    def _find_shape_at_pos(self, page_x, page_y):
        """Find a shape at the given position on current page"""
        for shape_obj in reversed(self.editable_shapes):
            if shape_obj.page_number != self.current_page_index:
                continue
            if not shape_obj.bbox: 
                continue
            x1, y1, x2, y2 = shape_obj.bbox
            tolerance = 3 / self.zoom_level
            if (x1 - tolerance) <= page_x <= (x2 + tolerance) and \
               (y1 - tolerance) <= page_y <= (y2 + tolerance):
                return shape_obj
        return None

    def _find_resize_handle_at_pos(self, drawn_x, drawn_y, selected_obj):
        """Find which resize handle is being clicked (if any). Text objects cannot be resized with handles."""
        if not selected_obj or not selected_obj.bbox:
            return None
        
        # Text objects cannot be resized with handles (use font size instead)
        if isinstance(selected_obj, EditableText):
            return None
        
        x1, y1, x2, y2 = selected_obj.bbox
        page_offset_x = max(0, (self.pdf_view.get_allocated_width() - self.current_pdf_page_width) / 2)
        page_offset_y = max(0, (self.pdf_view.get_allocated_height() - self.current_pdf_page_height) / 2)
        
        padding = 3.0
        rect_x = page_offset_x + (x1 * self.zoom_level) - padding
        rect_y = page_offset_y + (y1 * self.zoom_level) - padding
        rect_w = (x2 - x1) * self.zoom_level + (2 * padding)
        rect_h = (y2 - y1) * self.zoom_level + (2 * padding)
        
        handle_size = 8.0
        handle_tolerance = 12.0  # Larger click area for easier selection
        
        # Check all 8 resize handles
        handles = [
            ("nw", rect_x, rect_y),
            ("ne", rect_x + rect_w, rect_y),
            ("sw", rect_x, rect_y + rect_h),
            ("se", rect_x + rect_w, rect_y + rect_h),
            ("n", rect_x + rect_w / 2.0, rect_y),
            ("s", rect_x + rect_w / 2.0, rect_y + rect_h),
            ("w", rect_x, rect_y + rect_h / 2.0),
            ("e", rect_x + rect_w, rect_y + rect_h / 2.0),
        ]
        
        for handle_name, handle_x, handle_y in handles:
            if abs(drawn_x - handle_x) < handle_tolerance and abs(drawn_y - handle_y) < handle_tolerance:
                return handle_name
        
        return None

    def _handle_add_image_action(self, page_x_unzoomed, page_y_unzoomed):
        dialog = Gtk.FileChooserDialog(
            title="Lütfen bir resim dosyası seçin",
            transient_for=self, modal=True, action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons("_İptal", Gtk.ResponseType.CANCEL, "_Aç", Gtk.ResponseType.ACCEPT)

        filter_img = Gtk.FileFilter(name="Resim dosyaları")
        for mime in ["image/png", "image/jpeg", "image/gif", "image/bmp"]:
            filter_img.add_mime_type(mime)
        dialog.add_filter(filter_img)

        def on_response(d, response_id):
            if response_id == Gtk.ResponseType.ACCEPT:
                file = d.get_file()
                if file:
                    image_path = file.get_path()
                    try:
                        with open(image_path, 'rb') as f:
                            image_bytes = f.read()
                        
                        pixbuf = GdkPixbuf.Pixbuf.new_from_file(image_path)
                        img_w, img_h = pixbuf.get_width(), pixbuf.get_height()

                        target_w = 150.0 
                        target_h = (img_h / img_w) * target_w if img_w > 0 else 150.0
                        rect = (page_x_unzoomed, page_y_unzoomed, 
                                page_x_unzoomed + target_w, page_y_unzoomed + target_h)

                        new_image_obj = EditableImage(
                            bbox=rect,
                            page_number=self.current_page_index,
                            xref=None, 
                            image_bytes=image_bytes
                        )

                        command = AddObjectCommand(self, new_image_obj)
                        command.execute()  
                        self.undo_manager.add_command(command) 

                    except Exception as e:
                        show_error_dialog(self, f"Resim dosyası işlenirken bir hata oluştu:\n{e}", "Resim Hatası")
            d.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _update_text_format_controls(self, text_obj):
        if not text_obj or self.font_scan_in_progress:
            if not self.font_scan_in_progress and self.font_combo.get_sensitive():
                self.font_combo.handler_block_by_func(self.on_text_format_changed)
                self.font_combo.set_active(0)
                self.font_combo.handler_unblock_by_func(self.on_text_format_changed)

            self.font_size_spin.handler_block_by_func(self.on_text_format_changed)
            self.font_size_spin.set_value(11)
            self.font_size_spin.handler_unblock_by_func(self.on_text_format_changed)

            default_rgba = Gdk.RGBA(); default_rgba.parse("black")
            self.color_button.handler_block_by_func(self.on_text_format_changed)
            self.color_button.set_rgba(default_rgba)
            self.color_button.handler_unblock_by_func(self.on_text_format_changed)

            if self.bold_button:
                self.bold_button.handler_block_by_func(self.on_text_format_changed)
                self.bold_button.set_active(False)
                self.bold_button.handler_unblock_by_func(self.on_text_format_changed)
            if self.italic_button:
                self.italic_button.handler_block_by_func(self.on_text_format_changed)
                self.italic_button.set_active(False)
                self.italic_button.handler_unblock_by_func(self.on_text_format_changed)
            return

        signals_blocked = False
        try:
            for widget in [self.font_combo, self.font_size_spin, self.color_button, self.bold_button, self.italic_button]:
                if widget: widget.handler_block_by_func(self.on_text_format_changed)
            signals_blocked = True

            active_font_index = -1
            target_family_base = text_obj.font_family_base 
            normalized_target_family_base = target_family_base.replace(" ", "").lower() if target_family_base else ""

            if target_family_base and utils.FONT_FAMILY_LIST_SORTED:
                model = self.font_combo.get_model()
                if model:
                    for i, row in enumerate(model):
                        combo_family_key = row[1]
                        normalized_combo_key = combo_family_key.replace(" ", "").lower()
                        if normalized_combo_key == normalized_target_family_base:
                            active_font_index = i
                            break
                
                if active_font_index == -1:
                    print(f"Warning: Normalized font '{normalized_target_family_base}' from selected text not directly in combo. Trying partial on original names.")
                    for i, row in enumerate(model):
                        combo_family_key = row[1]
                        if target_family_base and combo_family_key and target_family_base.lower() in combo_family_key.lower():
                            active_font_index = i
                            break
                        elif target_family_base and combo_family_key and combo_family_key.lower() in target_family_base.lower():
                            active_font_index = i
                            break
                    if active_font_index == -1 and len(model) > 0:
                         active_font_index = 0 
                         print(f"Warning: No good match for '{target_family_base}' even with normalization/partial, defaulting combo to index 0.")


            if active_font_index != -1 and active_font_index < len(self.font_store):
                 self.font_combo.set_active(active_font_index)
            elif len(self.font_store) > 0:
                 self.font_combo.set_active(0)
            
            self.font_combo.set_tooltip_text(f"Yazı Tipi Ailesi (Orijinal PDF: {text_obj.font_family_original})")

            self.font_size_spin.set_value(text_obj.font_size)
            rgba = Gdk.RGBA(); rgba.red, rgba.green, rgba.blue = text_obj.color; rgba.alpha = 1.0
            self.color_button.set_rgba(rgba)
            if self.bold_button: self.bold_button.set_active(text_obj.is_bold)
            if self.italic_button: self.italic_button.set_active(text_obj.is_italic)

        finally:
            if signals_blocked:
                 for widget in [self.font_combo, self.font_size_spin, self.color_button, self.bold_button, self.italic_button]:
                    if widget: widget.handler_unblock_by_func(self.on_text_format_changed)

    def _get_current_format_settings(self):
        font_family_display = "Sans"
        font_pdf_name = "helv"
        iter = self.font_combo.get_active_iter()
        if iter:
            font_family_display = self.font_store[iter][0]
            font_pdf_name = self.font_store[iter][1]

        font_size = self.font_size_spin.get_value()

        rgba = self.color_button.get_rgba()
        color = (rgba.red, rgba.green, rgba.blue)

        is_bold = self.bold_button.get_active() if self.bold_button else False
        is_italic = self.italic_button.get_active() if self.italic_button else False

        return font_family_display, font_pdf_name, font_size, color, is_bold, is_italic

    def _update_shape_format_controls(self, shape_obj):
        """Update shape format controls based on selected shape"""
        try:
            # Block signals to prevent triggering on_shape_format_changed
            self.shape_fill_button.handler_block_by_func(self.on_shape_format_changed)
            self.shape_stroke_button.handler_block_by_func(self.on_shape_format_changed)
            self.shape_stroke_width_spin.handler_block_by_func(self.on_shape_format_changed)

            if not shape_obj:
                # Reset to defaults
                fill_rgba = Gdk.RGBA()
                fill_rgba.parse("white")
                self.shape_fill_button.set_rgba(fill_rgba)

                stroke_rgba = Gdk.RGBA()
                stroke_rgba.parse("black")
                self.shape_stroke_button.set_rgba(stroke_rgba)

                self.shape_stroke_width_spin.set_value(2.0)
            else:
                # Update controls from shape properties
                fill_r, fill_g, fill_b = shape_obj.fill_color
                fill_rgba = Gdk.RGBA()
                fill_rgba.red, fill_rgba.green, fill_rgba.blue = fill_r, fill_g, fill_b
                fill_rgba.alpha = 1.0
                self.shape_fill_button.set_rgba(fill_rgba)

                stroke_r, stroke_g, stroke_b = shape_obj.stroke_color
                stroke_rgba = Gdk.RGBA()
                stroke_rgba.red, stroke_rgba.green, stroke_rgba.blue = stroke_r, stroke_g, stroke_b
                stroke_rgba.alpha = 1.0
                self.shape_stroke_button.set_rgba(stroke_rgba)

                self.shape_stroke_width_spin.set_value(shape_obj.stroke_width)

        finally:
            # Unblock signals
            self.shape_fill_button.handler_unblock_by_func(self.on_shape_format_changed)
            self.shape_stroke_button.handler_unblock_by_func(self.on_shape_format_changed)
            self.shape_stroke_width_spin.handler_unblock_by_func(self.on_shape_format_changed)

    def _setup_text_editor(self, text_obj, drawing_area_click_x=None, drawing_area_click_y=None):
        self.hide_text_editor()
        if not text_obj: return

        self.editing_text_object = text_obj
        self.text_edit_popover = Gtk.Popover(autohide=False, has_arrow=True, position=Gtk.PositionType.TOP)

        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        popover_box.add_css_class("box")
        self.text_edit_popover.set_child(popover_box)

        if not text_obj.is_new and text_obj.original_text:
            preview_label = Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, xalign=0.0)
            preview_text = (text_obj.original_text[:75] + '...') if len(text_obj.original_text) > 75 else text_obj.original_text
            preview_label.set_markup(f"<small><i>Orijinal: {GLib.markup_escape_text(preview_text)}</i></small>")
            preview_label.set_margin_bottom(6)
            popover_box.append(preview_label)

        scroll = Gtk.ScrolledWindow(
            min_content_height=80,
            max_content_height=300, 
            min_content_width=250,
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, 
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC
        )
        popover_box.append(scroll)
        self.text_edit_view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.text_edit_view.set_left_margin(5); self.text_edit_view.set_right_margin(5)
        self.text_edit_view.set_top_margin(5); self.text_edit_view.set_bottom_margin(5)
        self.text_edit_view.get_buffer().set_text(text_obj.text)
        self.text_edit_view.add_css_class("textview")
        if text_obj.is_new:
            self.text_edit_view.add_css_class("new-text-entry")
        
        # Add focus controller to handle focus-out events
        focus_controller = Gtk.EventControllerFocus()
        focus_controller.connect("leave", lambda w: False)  # Return False to propagate event (GDK_EVENT_PROPAGATE)
        self.text_edit_view.add_controller(focus_controller)
        
        scroll.set_child(self.text_edit_view)
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.END)
        done_button = Gtk.Button(label="Bitir")
        done_button.add_css_class("suggested-action")
        done_button.connect("clicked", self.on_text_edit_done)
        button_box.append(done_button)
        popover_box.append(button_box)

        drawing_area_width = self.pdf_view.get_allocated_width()
        drawing_area_height = self.pdf_view.get_allocated_height()
        page_w = self.current_pdf_page_width
        page_h = self.current_pdf_page_height
        page_offset_x = max(0, (drawing_area_width - page_w) / 2)
        page_offset_y = max(0, (drawing_area_height - page_h) / 2)

        rect_to_point_to = Gdk.Rectangle()

        if text_obj.is_new and drawing_area_click_x is not None and drawing_area_click_y is not None:
            anchor_offset = 5 
            rect_to_point_to.x = int(drawing_area_click_x)
            rect_to_point_to.y = int(drawing_area_click_y - anchor_offset) 
            rect_to_point_to.width = 1
            rect_to_point_to.height = 1
        elif text_obj.bbox:
            x1_unzoomed, y1_unzoomed, x2_unzoomed, y2_unzoomed = text_obj.bbox
            widget_x1 = page_offset_x + (x1_unzoomed * self.zoom_level)
            widget_y1 = page_offset_y + (y1_unzoomed * self.zoom_level)
            widget_w = (x2_unzoomed - x1_unzoomed) * self.zoom_level

            rect_to_point_to.x = int(widget_x1 + widget_w / 2) 
            rect_to_point_to.y = int(widget_y1)                
            rect_to_point_to.width = 1
            rect_to_point_to.height = 1
        else:
            rect_to_point_to.x = drawing_area_width // 2
            rect_to_point_to.y = drawing_area_height // 2
            rect_to_point_to.width = 1; rect_to_point_to.height = 1
            print("Warning: Could not determine precise popover anchor.")

        self.text_edit_popover.set_parent(self.pdf_view)
        self.text_edit_popover.set_pointing_to(rect_to_point_to)
        self.text_edit_popover.popup()
        self.text_edit_view.grab_focus()

    def hide_text_editor(self):
        if self.text_edit_popover:
            self.text_edit_popover.popdown()
            self.text_edit_popover = None
            self.text_edit_view = None
            self.editing_text_object = None

    def _apply_and_hide_editor(self, force_apply=False):
        if not self.text_edit_view or not self.editing_text_object:
            self.hide_text_editor()
            return

        text_obj_to_apply = self.editing_text_object
        old_properties = copy.deepcopy(text_obj_to_apply.__dict__)

        buffer = self.text_edit_view.get_buffer()
        new_text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        
        self.hide_text_editor()

        if text_obj_to_apply.is_new:
            text_obj_to_apply.text = new_text
            
            # Recalculate bbox based on new text
            x1, y1, _, _ = text_obj_to_apply.bbox
            import cairo
            import gi
            gi.require_version('Pango', '1.0')
            from gi.repository import Pango
            
            _surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            _cr = cairo.Context(_surf)
            layout = PangoCairo.create_layout(_cr)
            desc = Pango.FontDescription.from_string(f"{text_obj_to_apply.font_family_base} {text_obj_to_apply.font_size}")
            layout.set_font_description(desc)
            layout.set_text(new_text, -1)
            
            _p_w, _p_h = layout.get_size()
            _w = (_p_w / Pango.SCALE) * 0.75
            _h = (_p_h / Pango.SCALE) * 0.75
            text_obj_to_apply.bbox = (x1, y1, x1 + _w, y1 + _h)
            
            command = AddObjectCommand(self, text_obj_to_apply)
            command.execute()
            self.undo_manager.add_command(command)
            self._load_page(self.current_page_index, preserve_scroll=True)
        else:
            new_properties = copy.deepcopy(text_obj_to_apply.__dict__)
            new_properties['text'] = new_text
            if old_properties['text'] != new_properties['text']:
                x1, y1, _, _ = new_properties['bbox']
                import cairo
                import gi
                gi.require_version('Pango', '1.0')
                from gi.repository import Pango
                
                _surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                _cr = cairo.Context(_surf)
                layout = PangoCairo.create_layout(_cr)
                desc = Pango.FontDescription.from_string(f"{new_properties['font_family_base']} {new_properties['font_size']}")
                layout.set_font_description(desc)
                layout.set_text(new_text, -1)
                
                _p_w, _p_h = layout.get_size()
                _w = (_p_w / Pango.SCALE) * 0.75
                _h = (_p_h / Pango.SCALE) * 0.75
                new_properties['bbox'] = (x1, y1, x1 + _w, y1 + _h)

                command = EditObjectCommand(self, text_obj_to_apply, old_properties, new_properties)
                command.execute() 
                self.undo_manager.add_command(command)
                self._load_page(self.current_page_index, preserve_scroll=True)
        
        self.selected_text = None
        self._update_ui_state()
        self.pdf_view.queue_draw()
    #original
    '''
    def check_unsaved_changes(self):
        if self.document_modified:
            confirm = show_confirm_dialog(self,
                                          "Kaydedilmemiş değişiklikler var. Kapatmadan/açmadan önce bunları kaydetmek istiyor musunuz?",
                                          title="Kaydedilmemiş Değişiklikler",
                                          destructive=False)
            if confirm:
                 if self.current_file_path:
                     self.save_document(self.current_file_path)
                     return False
                 else:
                     show_error_dialog(self, "Değişiklikleri kaydetmek için lütfen önce 'Farklı Kaydet...' seçeneğini kullanın.")
                     return True

            print("Kaydedilmemiş değişiklikler siliniyor.")
            return False
        return False
    '''

    #patched
    def check_unsaved_changes(self):
        if self.document_modified:
            confirm = show_confirm_dialog(self,
                                          "Kaydedilmemiş değişiklikler var. Kapatmadan önce kaydetmek ister misiniz?",
                                          title="Kaydedilmemiş Değişiklikler",
                                          destructive=False)
            if confirm:
                 if self.current_file_path:
                     self.save_document(self.current_file_path, incremental=False)
                     return False
                 else:
                     self.on_save_as(None, None)
                     return True

            print("Kaydedilmemiş değişiklikler siliniyor.")
            return False
        return False

    def on_drop(self, drop_target, value, x, y):
        if isinstance(value, Gio.File):
            filepath = value.get_path()
            if filepath and filepath.lower().endswith('.pdf'):
                # Check if we have an open document - if yes, offer merge option
                if self.doc:
                    self._offer_merge_or_open(filepath)
                else:
                    GLib.idle_add(self.load_document, filepath)
                return True
        return False

    def _offer_merge_or_open(self, filepath):
        """Offer user choice to merge or replace PDF"""
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text="PDF İçeri Aktar",
            secondary_text=f"'{os.path.basename(filepath)}' dosyasını mevcut PDF'ye birleştirmek veya açmak istediğinizden emin misiniz?"
        )
        
        dialog.add_buttons(
            "_Aç (Değiştir)", Gtk.ResponseType.NO,
            "_Birleştir", Gtk.ResponseType.YES,
            "_İptal", Gtk.ResponseType.CANCEL
        )
        dialog.set_default_response(Gtk.ResponseType.YES)
        
        def on_response(d, resp_id):
            d.destroy()
            if resp_id == Gtk.ResponseType.YES:
                # Merge at current page position
                self._merge_pdf_at_position(filepath, self.current_page_index + 1)
            elif resp_id == Gtk.ResponseType.NO:
                # Replace document
                if self.check_unsaved_changes():
                    return
                GLib.idle_add(self.load_document, filepath)
        
        dialog.connect("response", on_response)
        dialog.present()

    def _merge_pdf_at_position(self, source_pdf_path, insert_position):
        """Merge a PDF at the specified position"""
        success, message, pages_inserted = pdf_handler.merge_pdf_pages(
            self.doc, source_pdf_path, insert_position
        )
        
        if success:
            self.document_modified = True
            self.status_label.set_text(message)
            self._load_thumbnails()
            self._load_page(insert_position)
            self._update_ui_state()
        else:
            show_error_dialog(self, message, "PDF Birleştirme Hatası")

    def on_thumbnail_drop(self, drop_target, value, x, y):
        """Handle dropping PDF files on thumbnails list for merge"""
        # Check if it's a PDF file drop
        if isinstance(value, Gio.File):
            filepath = value.get_path()
            if filepath and filepath.lower().endswith('.pdf'):
                # Merge at end by default (GridView doesn't support precise positioning)
                insert_position = pdf_handler.get_page_count(self.doc)
                self._merge_pdf_at_position(filepath, insert_position)
                return True
        
        return False

    def on_open_clicked(self, button):
        if self.check_unsaved_changes():
             return

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
                    GLib.idle_add(self.load_document, file.get_path())
            d.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    #original
    '''
    def on_save_clicked(self, button):
        self.commit_pending_format_change()

        if self.is_repaired_file:
            show_error_dialog(
                self,
                "Bu PDF dosyası açılırken hatalar içerdiği için onarıldı. Veri kaybını önlemek için, belgeyi 'Farklı Kaydet' seçeneği ile yeni bir dosya olarak kaydetmeniz gerekiyor.",
                "Güvenli Kaydetme Uyarısı"
            )
            self.on_save_as(None, None)
        
        elif self.current_file_path:
            self.save_document(self.current_file_path, incremental=False)
        
        else:
            self.on_save_as(None, None)
    '''

    #patched
    def on_save_clicked(self, button):
        self.on_save_as(None, None)

    def on_save_as(self, action, param):
        self.commit_pending_format_change()
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
                success, error_msg = pdf_handler.save_document(self.doc, output_path, incremental=False)
                if success:
                    self._update_ui_state()
            else:
                 success = False

            if success:
                 self.status_label.set_text(f"Belge olarak dışa aktarıldı {format_name}: {os.path.basename(output_path)}")
            else:
                 show_error_dialog(self, f"Dışa Aktarma Başarısız: {error_msg}")
                 self.status_label.set_text(f"Export as {format_name} failed.")

        except Exception as e:
             show_error_dialog(self, f"Dışa aktarma sırasında beklenmeyen hata: {e}")
             self.status_label.set_text("Dışa aktarma başarısız oldu.")

    def on_print_activated(self, action, param):
        """Handle print action with preview dialog"""
        if not self.doc:
            show_error_dialog(self, "Baskı yapmak için lütfen bir PDF dosyası açın.", "Dosya Açılmadı")
            return
        
        # Show print preview dialog
        settings = print_handler.show_print_dialog(self, self.doc, self.current_page_index)
        
        if settings:
            success, message = print_handler.print_pdf(self.doc, settings)
            if success:
                self.status_label.set_text("Baskı işlemi tamamlandı.")
                print(f"[PRINT SUCCESS] {message}")
            else:
                show_error_dialog(self, f"Baskı hatası: {message}", "Baskı Başarısız")
                self.status_label.set_text("Baskı başarısız oldu.")
        else:
            self.status_label.set_text("Baskı işlemi iptal edildi.")

    def on_zoom_in(self, button=None):
        if not self.doc: return
        self.zoom_level = min(8.0, self.zoom_level * 1.2)
        self.zoom_label.set_text(f"{int(self.zoom_level * 100)}%")
        self._load_page(self.current_page_index)

    def on_zoom_out(self, button=None):
        if not self.doc: return
        self.zoom_level = max(0.1, self.zoom_level / 1.2)
        self.zoom_label.set_text(f"{int(self.zoom_level * 100)}%")
        self._load_page(self.current_page_index)

    def on_scroll_zoom(self, controller, dx, dy):
        if controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            if dy < 0: self.on_zoom_in()
            elif dy > 0: self.on_zoom_out()
            return True
        return False

    def on_prev_page(self, button):
        if self.doc and self.current_page_index > 0:
            self._load_page(self.current_page_index - 1)

    def on_next_page(self, button):
        if self.doc and self.current_page_index < pdf_handler.get_page_count(self.doc) - 1:
            self._load_page(self.current_page_index + 1)

    def on_add_page(self, button):
        """Add a new blank page after the current page"""
        if not self.doc:
            show_error_dialog(self, "Lütfen önce bir belge açın veya oluşturun.", "Belge Yok")
            return

        # Get current page dimensions
        current_page = self.doc.load_page(self.current_page_index)
        page_width = current_page.rect.width
        page_height = current_page.rect.height

        # Insert page after current page
        insert_position = self.current_page_index + 1
        success, message = pdf_handler.insert_blank_page(self.doc, insert_position, page_width, page_height)

        if success:
            self.document_modified = True
            self.status_label.set_text(message)
            self._load_thumbnails()
            self._load_page(insert_position)
            self._update_ui_state()
        else:
            show_error_dialog(self, message, "Sayfa Ekleme Hatası")

    def update_page_label(self):
        count = pdf_handler.get_page_count(self.doc)
        self.page_label.set_text(f"Sayfa {self.current_page_index + 1} of {count}" if count > 0 else "Sayfa 0 / 0")

    def on_thumbnail_selected(self, selection_model, position, n_items):
         selected_index = selection_model.get_selected()
         if selected_index != Gtk.INVALID_LIST_POSITION and selected_index != self.current_page_index:
              if hasattr(self, '_syncing_thumb') and self._syncing_thumb: return
              self._load_page(selected_index)

    def _sync_thumbnail_selection(self):
         if not self.doc or not self.thumbnail_selection_model: return
         self._syncing_thumb = True
         self.thumbnail_selection_model.set_selected(self.current_page_index)
         self._syncing_thumb = False

    def on_page_reorder(self, from_index, to_index):
        """Reorder pages in the document (infrastructure ready for future drag-drop)"""
        if from_index == to_index or from_index < 0 or to_index < 0:
            return
        
        success, message = pdf_handler.move_page(self.doc, from_index, to_index)
        
        if success:
            self.document_modified = True
            self.status_label.set_text(message)
            self._load_thumbnails()
            self._load_page(to_index)
            self._update_ui_state()
        else:
            show_error_dialog(self, message, "Sayfa Taşıma Hatası")


    def on_pdf_view_pressed(self, gesture, n_press, x, y):
        if not self.doc or self.current_pdf_page_width == 0 or self.current_pdf_page_height == 0:
            return

        if self.font_scan_in_progress:
            show_error_dialog(self, "Fontlar hala taranıyor. Lütfen bekleyin.", "Font Tarama")
            return

        drawing_area_width = self.pdf_view.get_allocated_width()
        drawing_area_height = self.pdf_view.get_allocated_height()

        page_w_zoomed = self.current_pdf_page_width
        page_h_zoomed = self.current_pdf_page_height

        page_offset_x = max(0, (drawing_area_width - page_w_zoomed) / 2)
        page_offset_y = max(0, (drawing_area_height - page_h_zoomed) / 2)

        click_x_on_page_zoomed = x - page_offset_x
        click_y_on_page_zoomed = y - page_offset_y

        is_on_page = (0 <= click_x_on_page_zoomed < page_w_zoomed and
                    0 <= click_y_on_page_zoomed < page_h_zoomed)
        
        self.commit_pending_format_change()

        if not is_on_page:
            if self.text_edit_popover and self.text_edit_popover.is_visible():
                self._apply_and_hide_editor()
            self.selected_text = None
            self.selected_image = None
            self.pdf_view.queue_draw()
            self._update_ui_state()
            return

        page_x_unzoomed = click_x_on_page_zoomed / self.zoom_level
        page_y_unzoomed = click_y_on_page_zoomed / self.zoom_level


        if self.tool_mode == "select":
            if self.text_edit_popover and self.text_edit_popover.is_visible():
                self._apply_and_hide_editor()

            clicked_image = self._find_image_at_pos(page_x_unzoomed, page_y_unzoomed)
            clicked_text = self._find_text_at_pos(page_x_unzoomed, page_y_unzoomed)
            clicked_shape = self._find_shape_at_pos(page_x_unzoomed, page_y_unzoomed)

            if clicked_image:
                self.selected_image = clicked_image
                self.selected_text = None
                self.selected_shape = None
            elif clicked_shape:
                self.selected_shape = clicked_shape
                self.selected_text = None
                self.selected_image = None
            elif clicked_text:
                self.selected_image = None
                self.selected_shape = None
                if clicked_text == self.selected_text and n_press > 1:
                    self._setup_text_editor(clicked_text, drawing_area_click_x=x, drawing_area_click_y=y)
                else:
                    self.selected_text = clicked_text
                    self.pending_format_change_obj = self.selected_text
                    self.before_format_change_state = copy.deepcopy(self.selected_text.__dict__)
            else:
                self.selected_text = None
                self.selected_image = None
                self.selected_shape = None

            self.pdf_view.queue_draw()
            self._update_ui_state()

        elif self.tool_mode == "add_text":
            if self.text_edit_popover and self.text_edit_popover.is_visible():
                self._apply_and_hide_editor()
                return

            font_fam_display, font_pdf_name, font_size, color, is_bold, is_italic = self._get_current_format_settings()
            baseline_y_unzoomed = page_y_unzoomed + (font_size * 0.9)

            target_family_key = font_fam_display
            target_base14 = 'helv'
            iter = self.font_combo.get_active_iter()
            if iter:
                model_key = self.font_store[iter][1]
                normalized_for_base14 = re.sub(r'[^a-zA-Z0-9]', '', model_key).lower()
                for name_key, base14_val in BASE14_FALLBACK_MAP.items():
                    if name_key in normalized_for_base14:
                        target_base14 = base14_val
                        break

            new_text_obj = EditableText(
                x=page_x_unzoomed,
                y=page_y_unzoomed,
                text="Yeni Metin",
                font_size=font_size,
                color=color,
                is_new=True,
                baseline=baseline_y_unzoomed
            )
            new_text_obj.font_family_base = target_family_key
            new_text_obj.font_family_original = f"{target_family_key} (Kullanıcı Ekledi)"
            new_text_obj.is_bold = is_bold
            new_text_obj.is_italic = is_italic
            new_text_obj.pdf_fontname_base14 = target_base14
            new_text_obj.page_number = self.current_page_index

            self.selected_text = new_text_obj
            self.selected_image = None
            self._update_text_format_controls(self.selected_text)
            self._setup_text_editor(new_text_obj, drawing_area_click_x=x, drawing_area_click_y=y)
            self._update_ui_state()

        elif self.tool_mode == "add_image":
            # Image creation is now drag-based
            pass

        elif self.tool_mode == "add_ellipse":
            # Ellipse creation is now drag-based
            pass

        elif self.tool_mode == "add_rectangle":
            # Rectangle creation is now drag-based
            pass

    def on_text_format_changed(self, widget, *args):
        if self.font_scan_in_progress:
            return

        if self.pending_format_change_obj:
            
            font_family_key = None
            iter = self.font_combo.get_active_iter()
            if iter:
                font_family_key = self.font_store[iter][1]

            font_size = self.font_size_spin.get_value()

            rgba = self.color_button.get_rgba()
            color = (rgba.red, rgba.green, rgba.blue)

            is_bold = self.bold_button.get_active()
            is_italic = self.italic_button.get_active()

            changed = False

            if font_family_key and self.pending_format_change_obj.font_family_base != font_family_key:
                self.pending_format_change_obj.font_family_base = font_family_key
                changed = True
            
            if self.pending_format_change_obj.font_size != font_size:
                self.pending_format_change_obj.font_size = font_size
                changed = True
                # Recalculate the text bbox so the selection border matches the new font size
                obj = self.pending_format_change_obj
                if obj.bbox:
                    x1, y1, x2, y2 = obj.bbox
                    old_h = y2 - y1
                    old_w = x2 - x1
                    # Estimate new height/width from Pango layout measurement
                    try:
                        import cairo
                        import gi
                        gi.require_version('Pango', '1.0')
                        from gi.repository import Pango

                        _surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                        _cr = cairo.Context(_surf)
                        _layout = PangoCairo.create_layout(_cr)
                        _fd_str = f"{obj.font_family_base} {font_size}"
                        if obj.is_bold: _fd_str += " Bold"
                        if obj.is_italic: _fd_str += " Italic"
                        _layout.set_font_description(Pango.FontDescription.from_string(_fd_str))
                        _layout.set_text(obj.text if obj.text else "Ay", -1)
                        _p_w, _p_h = _layout.get_size()
                        
                        # Pango device units are 96 DPI, PDF points are 72 DPI
                        _w = (_p_w / Pango.SCALE) * 0.75
                        _h = (_p_h / Pango.SCALE) * 0.75
                        
                        new_w = max(_w, old_w) if _w > 0 else old_w
                        new_h = _h if _h > 0 else old_h
                        obj.bbox = (x1, y1, x1 + new_w, y1 + new_h)
                    except Exception as e:
                        print(f"DEBUG: Error recalculating text bbox: {e}")
                        pass  # Bbox will be updated on next page redraw
                
            if self.pending_format_change_obj.color != color:
                self.pending_format_change_obj.color = color
                changed = True
                
            if self.pending_format_change_obj.is_bold != is_bold:
                self.pending_format_change_obj.is_bold = is_bold
                changed = True
                
            if self.pending_format_change_obj.is_italic != is_italic:
                self.pending_format_change_obj.is_italic = is_italic
                changed = True

            if changed:
                self.document_modified = True
                self._update_ui_state()
                self.pdf_view.queue_draw()
                print(f"DEBUG: Format anlık olarak güncellendi: Family='{font_family_key}', Size={font_size}")

    def on_shape_format_changed(self, widget, *args):
        """Handle shape fill, stroke, and width changes"""
        # Get fill color
        fill_rgba = self.shape_fill_button.get_rgba()
        fill_color = (fill_rgba.red, fill_rgba.green, fill_rgba.blue)

        # Get stroke color
        stroke_rgba = self.shape_stroke_button.get_rgba()
        stroke_color = (stroke_rgba.red, stroke_rgba.green, stroke_rgba.blue)

        # Get stroke width
        stroke_width = self.shape_stroke_width_spin.get_value()
        
        # Get transparency setting
        is_transparent = self.shape_transparent_toggle.get_active()

        # Always update next_shape properties for future shapes
        self.next_shape_fill = fill_color
        self.next_shape_stroke = stroke_color
        self.next_shape_stroke_width = stroke_width
        self.next_shape_transparent = is_transparent

        # Also update selected shape if one exists
        if self.selected_shape:
            # Update shape properties
            changed = False
            if self.selected_shape.fill_color != fill_color:
                self.selected_shape.fill_color = fill_color
                changed = True
            if self.selected_shape.stroke_color != stroke_color:
                self.selected_shape.stroke_color = stroke_color
                changed = True
            if self.selected_shape.stroke_width != stroke_width:
                self.selected_shape.stroke_width = stroke_width
                changed = True
            if self.selected_shape.is_transparent != is_transparent:
                self.selected_shape.is_transparent = is_transparent
                changed = True

            if changed:
                self.selected_shape.modified = True
                self.document_modified = True
                self.pdf_view.queue_draw()
                print(f"DEBUG: Şekil biçimi güncellendi")

    def on_text_edit_done(self, button):
        self._apply_and_hide_editor(force_apply=True)

    def on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            if self.text_edit_popover and self.text_edit_popover.is_visible():
                 self.hide_text_editor()
                 if self.selected_text and self.selected_text.is_new:
                      self.selected_text = None
                      self.pdf_view.queue_draw()
                 elif self.selected_text:
                      self.pdf_view.queue_draw()
                 self._update_ui_state()
                 return True
            elif self.selected_text:
                 self.selected_text = None
                 self.pdf_view.queue_draw()
                 self._update_ui_state()
                 return True
            elif self.tool_mode == "add_text":
                 self.on_tool_selected(None, "select")
                 return True

        elif keyval == Gdk.KEY_Delete:
            self.commit_pending_format_change()
            obj_to_delete = self.selected_text or self.selected_image or self.selected_shape
            if obj_to_delete and not (self.text_edit_popover and self.text_edit_popover.is_visible()):
                if isinstance(obj_to_delete, EditableText):
                    confirm_text = f"Seçilen metni sil?\n'{obj_to_delete.text[:50]}...'"
                elif isinstance(obj_to_delete, EditableShape):
                    confirm_text = "Seçilen şekli silmek istediğinizden emin misiniz?"
                else:
                    confirm_text = "Seçilen resmi silmek istediğinizden emin misiniz?"
                
                if show_confirm_dialog(self, confirm_text, "Silme Onayı", destructive=True):
                    command = DeleteObjectCommand(self, obj_to_delete)
                    command.execute()
                    self.undo_manager.add_command(command)

                    self.selected_text = None
                    self.selected_image = None
                    self.selected_shape = None
                    self._update_ui_state()
                return True
            elif self.selected_image:
                confirm = show_confirm_dialog(self, "Seçilen resmi silmek istediğinizden emin misiniz?", "Resmi Sil")
                if confirm:
                    self.status_label.set_text("Resim siliniyor...")
                    success, error_msg = pdf_handler.delete_image_from_page(self.doc, self.selected_image)
                    if success:
                        self.document_modified = True
                        self._load_page(self.current_page_index)
                        self.status_label.set_text("Resim silindi.")
                    else:
                        show_error_dialog(self, f"Resim silinemedi: {error_msg}", "Silme Hatası")
                    self.selected_image = None
                    self._update_ui_state()
                return True
        return False

    def on_tool_selected(self, button, tool_name):
        if self.text_edit_popover and self.text_edit_popover.is_visible():
             print("Araç değiştirilmeden önce değişiklikler uygulanıyor...")
             self._apply_and_hide_editor(force_apply=True)

        # Shape controls sensitivity is now handled fully by _update_ui_state
        # (enabled whenever add_ellipse/add_rectangle tool is active or a shape is selected)

        if self.selected_text:
            self.selected_text = None

        if self.selected_image:
            self.selected_image = None

        if self.selected_shape:
            self.selected_shape = None

        self.pdf_view.queue_draw()

        self.tool_mode = tool_name
        print(f"Araç şu şekilde değiştirildi: {self.tool_mode}")
        self._update_ui_state() 

    def on_drag_begin(self, gesture, start_x, start_y):
        if not self.doc:
            return

        page_w, page_h = self.current_pdf_page_width, self.current_pdf_page_height
        page_offset_x = max(0, (self.pdf_view.get_allocated_width() - page_w) / 2)
        page_offset_y = max(0, (self.pdf_view.get_allocated_height() - page_h) / 2)

        page_x = (start_x - page_offset_x) / self.zoom_level
        page_y = (start_y - page_offset_y) / self.zoom_level

        # Handle shape/image creation by dragging
        if self.tool_mode == "add_ellipse":
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.dragging_to_create = True
            self.drag_start_page_pos = (page_x, page_y)
            self.temp_shape = EditableShape(
                shape_type=EditableShape.SHAPE_ELLIPSE,
                bbox=(page_x, page_y, page_x, page_y),
                fill_color=self.next_shape_fill,
                stroke_color=self.next_shape_stroke,
                stroke_width=self.next_shape_stroke_width,
                page_number=self.current_page_index,
                is_new=True,
                is_transparent=self.next_shape_transparent
            )
            return
        elif self.tool_mode == "add_rectangle":
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.dragging_to_create = True
            self.drag_start_page_pos = (page_x, page_y)
            self.temp_shape = EditableShape(
                shape_type=EditableShape.SHAPE_RECTANGLE,
                bbox=(page_x, page_y, page_x, page_y),
                fill_color=self.next_shape_fill,
                stroke_color=self.next_shape_stroke,
                stroke_width=self.next_shape_stroke_width,
                page_number=self.current_page_index,
                is_new=True,
                is_transparent=self.next_shape_transparent
            )
            return
        elif self.tool_mode == "add_image":
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            # Create empty placeholder - will show file picker on drag_end
            self.dragging_to_create = True
            self.drag_start_page_pos = (page_x, page_y)
            self.temp_image_bbox = (page_x, page_y, page_x, page_y)  # Will be updated in on_drag_update
            return

        # Check for resize handles on selected object first
        selected_obj = self.selected_text or self.selected_image or self.selected_shape
        if selected_obj and self.tool_mode == "select":
            resize_handle = self._find_resize_handle_at_pos(start_x, start_y, selected_obj)
            if resize_handle:
                self.resize_handle = resize_handle
                self.resize_start_bbox = selected_obj.bbox
                self.dragged_object = selected_obj
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                self.drag_start_pos = (start_x, start_y)
                self.drag_begin_state = copy.deepcopy(selected_obj.__dict__)
                return

        # Normal drag mode - move objects (only in 'drag' tool mode)
        if self.tool_mode == "drag":
            # In drag mode, find any object (text, image, or shape) and allow moving
            self.dragged_object = self._find_image_at_pos(page_x, page_y) or self._find_text_at_pos(page_x, page_y) or self._find_shape_at_pos(page_x, page_y)
            if not self.dragged_object:
                gesture.set_state(Gtk.EventSequenceState.DENIED)
                return
        elif self.tool_mode == "select":
            # In select mode: selection only, never movement - deny all drags
            # (click-based selection is already handled in on_pdf_view_pressed)
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        else:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return

        if self.dragged_object:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.drag_start_pos = (start_x, start_y)
            self.drag_begin_state = copy.deepcopy(self.dragged_object.__dict__)

        if self.dragged_object:
            if not hasattr(self.dragged_object, 'original_bbox') or not self.dragged_object.original_bbox:
                self.dragged_object.original_bbox = self.dragged_object.bbox

            x1, y1, _, _ = self.dragged_object.bbox
            self.drag_object_start_pos = (x1, y1)
        else:
            gesture.set_state(Gtk.EventSequenceState.DENIED)

    def on_drag_update(self, gesture, offset_x, offset_y):
        # Prevent any dragging if text editor is active
        if self.text_edit_popover and self.text_edit_popover.is_visible():
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
            
        # Handle shape/image creation
        if self.dragging_to_create:
            if self.temp_image_bbox is not None:
                # Update image zone bbox based on drag
                start_x, start_y = self.drag_start_page_pos
                delta_x = offset_x / self.zoom_level
                delta_y = offset_y / self.zoom_level
                
                current_x = start_x + delta_x
                current_y = start_y + delta_y
                
                x1 = min(start_x, current_x)
                y1 = min(start_y, current_y)
                x2 = max(start_x, current_x)
                y2 = max(start_y, current_y)
                
                # Ensure minimum size
                if x2 - x1 < 20:
                    x2 = x1 + 20
                if y2 - y1 < 20:
                    y2 = y1 + 20
                    
                self.temp_image_bbox = (x1, y1, x2, y2)
                self.pdf_view.queue_draw()
                return
            if self.temp_shape:
                # Update shape bbox based on drag - use offset from start position
                start_x, start_y = self.drag_start_page_pos
                delta_x = offset_x / self.zoom_level
                delta_y = offset_y / self.zoom_level
                
                current_x = start_x + delta_x
                current_y = start_y + delta_y
                
                x1 = min(start_x, current_x)
                y1 = min(start_y, current_y)
                x2 = max(start_x, current_x)
                y2 = max(start_y, current_y)
                
                # Ensure minimum size
                if x2 - x1 < 10:
                    x2 = x1 + 10
                if y2 - y1 < 10:
                    y2 = y1 + 10
                    
                self.temp_shape.bbox = (x1, y1, x2, y2)
                self.pdf_view.queue_draw()
            return
        
        if not self.dragged_object:
            return

        # Handle resizing if a resize handle is being dragged
        if self.resize_handle:
            self._handle_resize_update(offset_x, offset_y)
            return

        # Normal drag - move object
        # Only allow movement in 'drag' tool mode
        if self.tool_mode != "drag":
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
            
        delta_x = offset_x / self.zoom_level
        delta_y = offset_y / self.zoom_level

        start_obj_x, start_obj_y = self.drag_object_start_pos
        new_x = start_obj_x + delta_x
        new_y = start_obj_y + delta_y

        w = self.dragged_object.original_bbox[2] - self.dragged_object.original_bbox[0]
        h = self.dragged_object.original_bbox[3] - self.dragged_object.original_bbox[1]
        
        self.dragged_object.x = new_x
        self.dragged_object.y = new_y
        self.dragged_object.bbox = (new_x, new_y, new_x + w, new_y + h)
        
        if isinstance(self.dragged_object, EditableText):
            original_top_y = self.dragged_object.original_bbox[1]
            original_baseline = self.dragged_object.span_data.get("origin", (0, original_top_y + self.dragged_object.font_size))[1]
            baseline_offset = original_baseline - original_top_y
            
            self.dragged_object.baseline = new_y + baseline_offset

            self.selected_text = self.dragged_object
            self.selected_image = None
            self.selected_shape = None
        
        elif isinstance(self.dragged_object, EditableImage):
            self.selected_image = self.dragged_object
            self.selected_text = None
            self.selected_shape = None
            
        elif isinstance(self.dragged_object, EditableShape):
            self.selected_shape = self.dragged_object
            self.selected_image = None
            self.selected_text = None

        self.pdf_view.queue_draw()

    def _handle_resize_update(self, offset_x, offset_y):
        """Handle resizing an object based on resize handle"""
        if not self.resize_handle or not self.resize_start_bbox or not self.dragged_object:
            return

        x1, y1, x2, y2 = self.resize_start_bbox

        # Convert pixel offset to page coordinates
        delta_x = offset_x / self.zoom_level
        delta_y = offset_y / self.zoom_level

        new_x1, new_y1, new_x2, new_y2 = x1, y1, x2, y2

        # Adjust bbox based on which handle is being dragged
        if "w" in self.resize_handle:  # Left handles
            new_x1 = x1 + delta_x
        if "e" in self.resize_handle:  # Right handles
            new_x2 = x2 + delta_x
        if "n" in self.resize_handle:  # Top handles
            new_y1 = y1 + delta_y
        if "s" in self.resize_handle:  # Bottom handles
            new_y2 = y2 + delta_y

        # Ensure minimum size (10 units minimum)
        min_size = 10
        if new_x2 - new_x1 < min_size:
            if "e" in self.resize_handle:
                new_x2 = new_x1 + min_size
            else:
                new_x1 = new_x2 - min_size
        if new_y2 - new_y1 < min_size:
            if "s" in self.resize_handle:
                new_y2 = new_y1 + min_size
            else:
                new_y1 = new_y2 - min_size

        self.dragged_object.bbox = (new_x1, new_y1, new_x2, new_y2)
        
        # Update position
        self.dragged_object.x = new_x1
        self.dragged_object.y = new_y1

        self.pdf_view.queue_draw()

    def on_drag_end(self, gesture, offset_x, offset_y):
        # Finalize shape/image creation
        if self.dragging_to_create:
            self.dragging_to_create = False
            if self.temp_shape:
                # Check minimum size
                x1, y1, x2, y2 = self.temp_shape.bbox
                if (x2 - x1) < 10 or (y2 - y1) < 10:
                    # Too small, cancel
                    self.temp_shape = None
                    self.pdf_view.queue_draw()
                    return
                
                # Add shape to document
                self.selected_shape = self.temp_shape
                self.selected_text = None
                self.selected_image = None
                command = AddObjectCommand(self, self.temp_shape)
                command.execute()
                self.undo_manager.add_command(command)
                self.document_modified = True
                self.temp_shape = None
                self.pdf_view.queue_draw()
                self._update_ui_state()
            elif self.temp_image_bbox:
                # Show file picker for image to fill the zone
                x1, y1, x2, y2 = self.temp_image_bbox
                if (x2 - x1) < 20 or (y2 - y1) < 20:
                    # Too small, cancel
                    self.temp_image_bbox = None
                    self.pdf_view.queue_draw()
                    return
                
                # Show file chooser
                dialog = Gtk.FileChooserDialog(
                    title="Lütfen bir resim dosyası seçin",
                    transient_for=self, modal=True, action=Gtk.FileChooserAction.OPEN
                )
                dialog.add_buttons("_İptal", Gtk.ResponseType.CANCEL, "_Aç", Gtk.ResponseType.ACCEPT)
                filter_img = Gtk.FileFilter(name="Resim dosyaları")
                for mime in ["image/png", "image/jpeg", "image/gif", "image/bmp"]:
                    filter_img.add_mime_type(mime)
                dialog.add_filter(filter_img)
                
                def on_image_selected(d, response_id):
                    if response_id == Gtk.ResponseType.ACCEPT:
                        file = d.get_file()
                        if file:
                            try:
                                with open(file.get_path(), 'rb') as f:
                                    image_bytes = f.read()
                                
                                image_obj = EditableImage(
                                    bbox=self.temp_image_bbox,
                                    page_number=self.current_page_index,
                                    xref=None,
                                    image_bytes=image_bytes
                                )
                                
                                self.selected_image = image_obj
                                self.selected_text = None
                                self.selected_shape = None
                                command = AddObjectCommand(self, image_obj)
                                command.execute()
                                self.undo_manager.add_command(command)
                                self.document_modified = True
                                self.pdf_view.queue_draw()
                                self._update_ui_state()
                            except Exception as e:
                                show_error_dialog(self, f"Resim eklenirken hata: {e}", "Hata")
                    
                    self.temp_image_bbox = None
                    d.destroy()
                
                dialog.connect('response', on_image_selected)
                dialog.present()
            return
        
        if not self.dragged_object or not hasattr(self, 'drag_begin_state'):
            if self.dragged_object:
                self.dragged_object = None
            self.resize_handle = None
            self.resize_start_bbox = None
            self.pdf_view.queue_draw()
            return

        self.commit_pending_format_change()

        old_properties = self.drag_begin_state
        
        new_properties = copy.deepcopy(self.dragged_object.__dict__)

        dragged_obj_ref = self.dragged_object
        self.dragged_object = None
        self.resize_handle = None
        self.resize_start_bbox = None
        del self.drag_begin_state
        
        if abs(offset_x) < 1 and abs(offset_y) < 1:
            self.pdf_view.queue_draw()
            return

        print("DEBUG: Sürükleme işlemi için bir komut oluşturuluyor.")
        command = EditObjectCommand(self, dragged_obj_ref, old_properties, new_properties)
        
        command.execute()
        self.undo_manager.add_command(command)

        # Restore selection to the object doing the dragging
        if isinstance(dragged_obj_ref, EditableText):
            self.selected_text = dragged_obj_ref
            self.selected_image = None
            self.selected_shape = None
        elif isinstance(dragged_obj_ref, EditableImage):
            self.selected_image = dragged_obj_ref
            self.selected_text = None
            self.selected_shape = None
        elif isinstance(dragged_obj_ref, EditableShape):
            self.selected_shape = dragged_obj_ref
            self.selected_text = None
            self.selected_image = None

        self._update_ui_state()
        self.pdf_view.queue_draw()

    def _on_quick_guide_activated(self, action, param):
        dialog = Gtk.Dialog(transient_for=self, modal=True)
        dialog.set_default_size(500, 420)
        
        header = Gtk.HeaderBar()
        
        dialog.set_titlebar(header)

        title_label = Gtk.Label(label="Hızlı Başlangıç Kılavuzu")
        title_label.add_css_class("title-4")
        header.set_title_widget(title_label)
        
        content_area = dialog.get_content_area()
        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_vexpand(True) 
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content_area.append(scrolled_window)
        
        clamp = Adw.Clamp(maximum_size=450)
        scrolled_window.set_child(clamp)
        
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content_box.set_margin_top(20)
        content_box.set_margin_bottom(20)
        clamp.set_child(content_box)
        
        guide_select_label = Gtk.Label(
            use_markup=True,
            label="<b>1. Seç ve Düzenle Aracı (V)</b>\n"
                "Varsayılan araçtır. Sayfadaki metin veya resimlere tıklayarak seçin. "
                "Seçili bir metne <i>çift tıklayarak</i> düzenleme penceresini açın.",
            xalign=0, wrap=True
        )
        guide_add_text_label = Gtk.Label(
            use_markup=True,
            label="<b>2. Metin Ekle Aracı (T)</b>\n"
                "Sayfada metin eklemek istediğiniz yere tıklayın. Açılan pencereye metninizi "
                "yazın ve üst araç çubuğundan font, boyut ve renk ayarlarını yapın.",
            xalign=0, wrap=True
        )
        guide_add_image_label = Gtk.Label(
            use_markup=True,
            label="<b>3. Resim Ekle Aracı (I)</b>\n"
                "Sayfada resim eklemek istediğiniz yere tıklayın. Açılacak dosya seçme "
                "ekranından resminizi seçin.",
            xalign=0, wrap=True
        )
        guide_move_label = Gtk.Label(
            use_markup=True,
            label="<b>4. Taşıma Aracı (M)</b>\n"
                "Bu aracı seçtikten sonra, sayfadaki herhangi bir metin veya resim "
                "öğesini tıklayıp sürükleyerek yerini değiştirebilirsiniz.",
            xalign=0, wrap=True
        )
        content_box.append(guide_select_label)
        content_box.append(guide_add_text_label)
        content_box.append(guide_add_image_label)
        content_box.append(guide_move_label)
        
        dialog.present()

    def _update_undo_redo_buttons(self, *args):
        self.undo_button.set_sensitive(bool(self.undo_manager.undo_stack))
        self.redo_button.set_sensitive(bool(self.undo_manager.redo_stack))
    
    def commit_pending_format_change(self):
        if self.pending_format_change_obj and self.before_format_change_state:
            current_state = copy.deepcopy(self.pending_format_change_obj.__dict__)
            
            if self.before_format_change_state != current_state:
                print("DEBUG: Bekleyen format değişikliği bir komut olarak kaydediliyor.")
                command = EditObjectCommand(self, self.pending_format_change_obj, self.before_format_change_state, current_state)
                command.execute()
                self.undo_manager.add_command(command)

        self.pending_format_change_obj = None
        self.before_format_change_state = None

    def on_new_clicked(self, widget=None):
        if self.check_unsaved_changes():
            return

        self.close_document()

        doc, error_msg = pdf_handler.create_new_pdf()

        if error_msg:
            show_error_dialog(self, error_msg)
            self.close_document()
        elif doc:
            self.doc = doc
            self.current_file_path = None
            self.current_page_index = 0
            self.set_title("Word-Sys's PDF Editor - İsimsiz*")
            self.document_modified = True
            
            self._load_thumbnails()
            self.status_label.set_text("Yeni boş belge oluşturuldu. Değişiklikleri kaydetmeyi unutmayın.")

    def do_close_request(self):
        if self.check_unsaved_changes():
            return True
        else:
            self.close_document()
            return False
    def on_stroke_width_scroll(self, controller, dx, dy):
        """Handle mouse wheel scroll to adjust shape stroke width"""
        if not self.selected_shape:
            return False
        
        # Increase/decrease stroke width based on scroll direction
        dy_abs = abs(dy)
        increment = 0.5 if dy > 0 else -0.5
        
        new_width = max(0.5, self.selected_shape.stroke_width + increment)
        self.selected_shape.stroke_width = round(new_width, 1)
        
        self.pdf_view.queue_draw()
        return True

