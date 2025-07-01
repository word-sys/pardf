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
from .welcome_view import WelcomeView 
from .models import PdfPage, EditableText, BASE14_FALLBACK_MAP, EditableImage
from .ui_components import PageThumbnailFactory, show_error_dialog, show_confirm_dialog
from . import utils

class PdfEditorWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_title("Pardus PDF Editor")
        self.set_default_size(1200, 800)
        self.set_icon_name("pardus-pdf-editor")

        self.current_file_path = None
        self.doc = None 
        self.current_page_index = 0
        self.zoom_level = 1.0
        self.pages_model = Gio.ListStore(item_type=PdfPage)
        self.editable_texts = [] 
        self.editable_images = []
        self.selected_text = None
        self.selected_image = None
        self.text_edit_popover = None
        self.text_edit_view = None
        self.is_saving = False
        self.dragged_object = None
        self.drag_start_pos = (0, 0)
        self.drag_object_start_pos = (0, 0)
        self.document_modified = False 
        self.tool_mode = "select" 
        self.current_pdf_page_width = 0
        self.current_pdf_page_height = 0
        self.bold_button = None
        self.italic_button = None
        self.font_scan_in_progress = True

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

        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True, vexpand=True)
        
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
        
        self.select_tool_button = Gtk.Button(icon_name="input-mouse-symbolic", label="Seç")
        self.select_tool_button.set_tooltip_text("Nesneleri Seç ve Düzenle (V)")
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
        self.main_toolbar.append(self.prev_button)
        self.main_toolbar.append(self.page_label)
        self.main_toolbar.append(self.next_button)

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

    def _setup_controllers(self):
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

    def _connect_actions(self):
        action_save_as = Gio.SimpleAction.new('save_as', None)
        action_save_as.connect('activate', self.on_save_as)
        self.add_action(action_save_as)

        action_export_as = Gio.SimpleAction.new('export_as', None)
        action_export_as.connect('activate', self.on_export_as)
        self.add_action(action_export_as)

        action_about = Gio.SimpleAction.new('about', None)
        action_about.connect('activate', self.on_about_activated)
        self.add_action(action_about)

        action_quick_guide = Gio.SimpleAction.new('quick_guide', None)
        action_quick_guide.connect('activate', self._on_quick_guide_activated)
        self.add_action(action_quick_guide)

    def _update_ui_state(self):
        has_doc = self.doc is not None
        page_count = pdf_handler.get_page_count(self.doc) if self.doc else 0
        has_pages = page_count > 0
        can_go_prev = has_pages and self.current_page_index > 0
        can_go_next = has_pages and self.current_page_index < page_count - 1

        self.save_button.set_sensitive(has_doc and self.document_modified)
        self.lookup_action("save_as").set_enabled(has_doc)
        self.lookup_action("export_as").set_enabled(has_doc)
        self.prev_button.set_sensitive(can_go_prev)
        self.next_button.set_sensitive(can_go_next)

        text_tool_active = self.tool_mode == "add_text"
        text_selected = self.selected_text is not None
        format_enabled_base = ((self.selected_text is not None) or (self.tool_mode == "add_text")) and (self.selected_image is None)
        
        self.font_combo.set_sensitive(format_enabled_base and not self.font_scan_in_progress)
        self.font_size_spin.set_sensitive(format_enabled_base)
        self.color_button.set_sensitive(format_enabled_base)
        if self.bold_button: self.bold_button.set_sensitive(format_enabled_base)
        if self.italic_button: self.italic_button.set_sensitive(format_enabled_base)

        if text_selected:
            self._update_text_format_controls(self.selected_text)
        elif not (self.text_edit_popover and self.text_edit_popover.is_visible()):
            self._update_text_format_controls(None)

        self.select_tool_button.get_style_context().remove_class('active')
        self.add_text_tool_button.get_style_context().remove_class('active')
        self.add_image_tool_button.get_style_context().remove_class('active')
        self.drag_tool_button.get_style_context().remove_class('active')
        
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
            self.set_title("Pardus PDF Düzenleyicisi")
            self.document_modified = False

    def on_about_activated(self, action, param):
        about_dialog = Gtk.AboutDialog(transient_for=self, modal=True)

        about_dialog.set_program_name("ParDF - Pardus PDF Düzenleyicisi")
        about_dialog.set_version("1.6.1-1")
        about_dialog.set_authors(["Barın Güzeldemirci (word-sys)"])

        try:
            about_dialog.set_license_type(Gtk.License.GPL_3_0)
        except AttributeError:
            try:
                about_dialog.set_license_type(Gtk.License.CUSTOM)
                print("Warning: Gtk.License.GPL_3_0 not found, using CUSTOM.")
            except AttributeError:
                print("Warning: Gtk.AboutDialog.set_license_type not available on this GTK version.")

        try:
            license_file_path = Path(__file__).resolve().parent.parent / "LICENSE"
            if license_file_path.exists():
                with open(license_file_path, 'r', encoding='utf-8') as f:
                    license_text = f.read()
                about_dialog.set_license(license_text)
                about_dialog.set_wrap_license(True)
            else:
                print(f"Warning: LICENSE file not found at {license_file_path}")
                about_dialog.set_license("Lisans bilgileri için proje dosyalarına bakınız.")
        except Exception as e:
            print(f"Error reading LICENSE file: {e}")
            about_dialog.set_license("Lisans okunurken bir hata oluştu.")

        about_dialog.set_website("https://github.com/word-sys/pardf")
        about_dialog.set_website_label("Proje GitHub Sayfası")

        comments_text = "Linux için Basit PDF Metin Düzenleyici.\n\n" + \
                        """ParDF, Milli Teknoloji Hamlesi ve TEKNOFEST ruhuyla, Pardus üzerinde PDF dosyalarındaki metin içeriğini düzenlemek üzere tasarlanmış açık kaynaklı bir araçtır.
Kullanıcıların PDF sayfalarını görüntülemesine, mevcut metin bloklarını seçmesine, değiştirmesine, silmesine ve yeni metin blokları eklemesine olanak tanır.
Temel metin işleme yeteneklerine odaklanarak, Linux ortamında kullanıcı dostu bir PDF düzenleme deneyimi sunmayı hedefler."""
        about_dialog.set_comments(comments_text)

        try:
            app_icon_path = Path(__file__).resolve().parent / "img" / "icon.png"
            if app_icon_path.exists():
                texture = Gdk.Texture.new_from_filename(str(app_icon_path))
                about_dialog.set_logo(texture)
            else:
                print(f"Warning: Local icon not found at {app_icon_path}, relying on system icon theme for 'pardus-pdf-editor'")
                about_dialog.set_logo_icon_name("pardus-pdf-editor")
        except GLib.Error as e:
            print(f"Warning: Could not load application icon for About dialog: {e}")
            about_dialog.set_logo_icon_name("pardus-pdf-editor")
        except Exception as e_gen: 
             print(f"General Warning: Could not load application icon for About dialog: {e_gen}")
             about_dialog.set_logo_icon_name("pardus-pdf-editor")

        about_dialog.set_copyright("© 2024-2025 Barın Güzeldemirci")

        about_dialog.present()

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

    def _finish_loading(self, doc, error_msg, filepath):
        if error_msg:
            show_error_dialog(self, error_msg)
            self.status_label.set_text("Doküman yüklenemedi.")
            self.close_document()
        elif doc:
            self.doc = doc
            self.current_file_path = filepath
            self.current_page_index = 0
            self.set_title(f"Pardus PDF Editor - {os.path.basename(filepath)}")
            self.status_label.set_text(f"Küçük resimler yükleniyor...")
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
                self.status_label.set_text(f"Yüklendi: {os.path.basename(self.current_file_path)}")
                if page_count > 0:
                    self._load_page(0)
                else:
                    self._update_ui_state()
                return GLib.SOURCE_REMOVE

        GLib.idle_add(_load_next_thumb)

    def _load_page(self, page_index):
        if not self.doc or not (0 <= page_index < pdf_handler.get_page_count(self.doc)):
            print(f"Warning: Invalid attempt to load page {page_index}.")
            self.current_page_index = max(0, min(page_index, pdf_handler.get_page_count(self.doc) -1))
            self.current_pdf_page_width = 0
            self.current_pdf_page_height = 0
            self.editable_texts = []
            self.selected_text = None
            self.selected_image = None
            self.hide_text_editor()
            if hasattr(self, 'pdf_view'):
                 self.pdf_view.set_content_width(1)
                 self.pdf_view.set_content_height(1)
                 self.pdf_view.queue_draw()
            self._update_ui_state()
            return

        self.current_page_index = page_index
        self.selected_text = None
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

        self._sync_thumbnail_selection()
        self._update_ui_state()

    def close_document(self):
        pdf_handler.close_pdf_document(self.doc)
        self.doc = None
        self.current_file_path = None
        self.current_page_index = 0
        self.editable_texts = []
        self.editable_images = []
        self.selected_text = None
        self.selected_image = None
        self.hide_text_editor()
        self.pages_model.remove_all()
        self.document_modified = False
        self.pdf_view.set_content_width(1)
        self.pdf_view.set_content_height(1)
        self.pdf_view.queue_draw()
        self._update_ui_state()


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
            if save_path == self.current_file_path or self.current_file_path is None:
                self.current_file_path = save_path
            self.document_modified = False
            self.set_title(f"Pardus PDF Editor - {os.path.basename(save_path)}")
            self.status_label.set_text(f"Belge kaydedildi: {os.path.basename(save_path)}")
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
            if self.dragged_object.original_bbox:
                orig_x1, orig_y1, orig_x2, orig_y2 = self.dragged_object.original_bbox
                cr.save()
                cr.set_source_rgb(1, 1, 1)
                cr.rectangle(page_offset_x + (orig_x1 * self.zoom_level),
                            page_offset_y + (orig_y1 * self.zoom_level),
                            (orig_x2 - orig_x1) * self.zoom_level,
                            (orig_y2 - orig_y1) * self.zoom_level)
                cr.fill()
                cr.restore()

            x1, y1, x2, y2 = self.dragged_object.bbox
            ghost_x = page_offset_x + (x1 * self.zoom_level)
            ghost_y = page_offset_y + (y1 * self.zoom_level)
            ghost_w = (x2 - x1) * self.zoom_level
            ghost_h = (y2 - y1) * self.zoom_level

            cr.save()
            if isinstance(self.dragged_object, EditableImage) and self.dragged_object.image_bytes:
                loader = GdkPixbuf.PixbufLoader.new()
                try:
                    loader.write(self.dragged_object.image_bytes)
                    pixbuf = loader.get_pixbuf()
                    if pixbuf:
                        scaled_pixbuf = pixbuf.scale_simple(int(ghost_w), int(ghost_h), GdkPixbuf.InterpType.BILINEAR)
                        Gdk.cairo_set_source_pixbuf(cr, scaled_pixbuf, ghost_x, ghost_y)
                        cr.paint_with_alpha(0.6)
                except GLib.Error as e:
                    cr.set_source_rgba(0.2, 0.5, 0.8, 0.5)
                    cr.rectangle(ghost_x, ghost_y, ghost_w, ghost_h)
                    cr.fill()
                finally:
                    loader.close()
            elif isinstance(self.dragged_object, EditableText):
                layout = PangoCairo.create_layout(cr)
                font_desc_str = f"{self.dragged_object.font_family_base} {self.dragged_object.font_size}"
                if self.dragged_object.is_bold: font_desc_str += " Bold"
                if self.dragged_object.is_italic: font_desc_str += " Italic"

                font_desc = Pango.FontDescription(font_desc_str)
                layout.set_font_description(font_desc)
                layout.set_text(self.dragged_object.text, -1)

                r, g, b = self.dragged_object.color
                cr.set_source_rgba(r, g, b, 0.6)

                cr.move_to(ghost_x, ghost_y)
                PangoCairo.show_layout(cr, layout)
            cr.restore()

        selected_obj = self.selected_text or self.selected_image
        if selected_obj and not self.dragged_object:
            is_image = isinstance(selected_obj, EditableImage)
            style_context = area.get_style_context()
            color_name = "accent_color"
            default_color = "#3584e4"

            found, rgba = style_context.lookup_color(color_name)
            if not found:
                rgba = Gdk.RGBA()
                rgba.parse(default_color)

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
                        pixbuf = GdkPixbuf.Pixbuf.new_from_file(image_path)
                        img_w, img_h = pixbuf.get_width(), pixbuf.get_height()

                        target_w = 150.0
                        target_h = (img_h / img_w) * target_w if img_w > 0 else 150.0

                        rect = fitz.Rect(page_x_unzoomed, page_y_unzoomed,
                                        page_x_unzoomed + target_w, page_y_unzoomed + target_h)

                        self.status_label.set_text("Resim ekleniyor...")
                        success, msg = pdf_handler.add_image_to_page(
                            self.doc, self.current_page_index, image_path, rect
                        )
                        if success:
                            self.document_modified = True
                            self._load_page(self.current_page_index)
                            self.status_label.set_text("Resim eklendi.")
                        else:
                            show_error_dialog(self, f"Resim eklenemedi: {msg}", "Hata")
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
                        elif target_family_base and combo_family_key and combo_family_key.lower() in target_family_base.lower(): # And vice versa
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
        buffer = self.text_edit_view.get_buffer()
        start = buffer.get_start_iter()
        end = buffer.get_end_iter()
        new_text = buffer.get_text(start, end, True)

        original_text = text_obj_to_apply.original_text if not text_obj_to_apply.is_new else ""
        text_actually_changed = (new_text != original_text) or text_obj_to_apply.is_new

        self.hide_text_editor()

        if force_apply or text_actually_changed or text_obj_to_apply.modified:
            self.status_label.set_text("Metin değişiklikleri uygulanıyor...")

            text_obj_to_apply.text = new_text
            success, error_msg = pdf_handler.apply_object_edit(self.doc, text_obj_to_apply)

            if success:
                self.document_modified = True
                current_scroll_adj_v = self.pdf_scroll.get_vadjustment().get_value()
                current_scroll_adj_h = self.pdf_scroll.get_hadjustment().get_value()
                self._load_page(self.current_page_index)
                GLib.idle_add(lambda: self.pdf_scroll.get_vadjustment().set_value(current_scroll_adj_v))
                GLib.idle_add(lambda: self.pdf_scroll.get_hadjustment().set_value(current_scroll_adj_h))
                self.status_label.set_text("Metin değişiklikleri uygulandı.")
            else:
                show_error_dialog(self, f"Metin değişiklikleri uygulanamadı: {error_msg}")
                self._load_page(self.current_page_index)
                self.status_label.set_text("Metin değişiklikleri uygulanamadı.")
        else:
            self.status_label.set_text("Uygulanacak değişiklik yok.")

        self.selected_text = None
        self.pdf_view.queue_draw()
        self._update_ui_state()

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

    def on_drop(self, drop_target, value, x, y):
        if isinstance(value, Gio.File):
            filepath = value.get_path()
            if filepath and filepath.lower().endswith('.pdf'):
                GLib.idle_add(self.load_document, filepath)
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

    def on_save_clicked(self, button):
        if self.current_file_path:
            self.save_document(self.current_file_path, incremental=True)
        else:
            self.on_save_as(None, None)
        
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

            if clicked_image:
                self.selected_image = clicked_image
                self.selected_text = None
            elif clicked_text:
                self.selected_image = None
                if clicked_text == self.selected_text and n_press > 1:
                    self._setup_text_editor(clicked_text, drawing_area_click_x=x, drawing_area_click_y=y)
                else:
                    self.selected_text = clicked_text
            else:
                self.selected_text = None
                self.selected_image = None

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
            self._handle_add_image_action(page_x_unzoomed, page_y_unzoomed)

    def on_text_format_changed(self, widget, *args):
        if self.font_scan_in_progress: return
        if self.selected_text and not (self.text_edit_popover and self.text_edit_popover.is_visible()):
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
            if font_family_key and self.selected_text.font_family_base != font_family_key:
                self.selected_text.font_family_base = font_family_key
                changed = True
                print(f"DEBUG: Font family base changed to: {font_family_key}")

            if self.selected_text.font_size != font_size:
                self.selected_text.font_size = font_size; changed = True
            if self.selected_text.color != color:
                 self.selected_text.color = color; changed = True
            if self.selected_text.is_bold != is_bold:
                 self.selected_text.is_bold = is_bold; changed = True
            if self.selected_text.is_italic != is_italic:
                 self.selected_text.is_italic = is_italic; changed = True

            if changed:
                self.selected_text.modified = True
                self.document_modified = True
                print(f"Stored format change for: {self.selected_text.text[:20]}...")
                print(f"  New state: Family='{font_family_key}', Size={font_size}, Bold={is_bold}, Italic={is_italic}")
                self._update_ui_state()
        elif self.tool_mode == "add_text" and not self.selected_text:
            print("DEBUG: Format changed while in 'add_text' mode (for next text box).")

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
             if self.selected_text and not (self.text_edit_popover and self.text_edit_popover.is_visible()):
                  confirm = show_confirm_dialog(self, f"Seçilen metni sil?\n'{self.selected_text.text[:50]}...'")
                  if confirm:
                       self.status_label.set_text("Metin siliniyor...")
                       success, error_msg = pdf_handler.apply_text_edit(self.doc, self.selected_text, "")
                       if success:
                            self.document_modified = True
                            self._load_page(self.current_page_index)
                            self.status_label.set_text("Metin silindi.")
                       else:
                            show_error_dialog(self, f"Metin silinemedi: {error_msg}")
                            self.status_label.set_text("Metin silinemedi.")
                       self.selected_text = None
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

        if self.selected_text:
            self.selected_text = None

        if self.selected_image:
            self.selected_image = None

        self.pdf_view.queue_draw()

        self.tool_mode = tool_name
        print(f"Araç şu şekilde değiştirildi: {self.tool_mode}")
        self._update_ui_state() 

    def on_drag_begin(self, gesture, start_x, start_y):
        if self.tool_mode != "drag" or not self.doc:
            return

        page_w, page_h = self.current_pdf_page_width, self.current_pdf_page_height
        page_offset_x = max(0, (self.pdf_view.get_allocated_width() - page_w) / 2)
        page_offset_y = max(0, (self.pdf_view.get_allocated_height() - page_h) / 2)

        page_x = (start_x - page_offset_x) / self.zoom_level
        page_y = (start_y - page_offset_y) / self.zoom_level

        self.dragged_object = self._find_image_at_pos(page_x, page_y) or self._find_text_at_pos(page_x, page_y)

        if self.dragged_object:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.drag_start_pos = (start_x, start_y)

            if not self.dragged_object.original_bbox:
                self.dragged_object.original_bbox = self.dragged_object.bbox

            x1, y1, _, _ = self.dragged_object.bbox
            self.drag_object_start_pos = (x1, y1)
        else:
            gesture.set_state(Gtk.EventSequenceState.DENIED)

    def on_drag_update(self, gesture, offset_x, offset_y):
        if not self.dragged_object:
            return

        delta_x = offset_x / self.zoom_level
        delta_y = offset_y / self.zoom_level

        start_obj_x, start_obj_y = self.drag_object_start_pos
        new_x = start_obj_x + delta_x
        new_y = start_obj_y + delta_y

        if isinstance(self.dragged_object, EditableText):
            self.dragged_object.x = new_x
            self.dragged_object.y = new_y

            original_baseline_offset = self.dragged_object.baseline - self.drag_object_start_pos[1]
            self.dragged_object.baseline = new_y + original_baseline_offset

            w = self.dragged_object.bbox[2] - self.dragged_object.bbox[0]
            h = self.dragged_object.bbox[3] - self.dragged_object.bbox[1]
            self.dragged_object.bbox = (new_x, new_y, new_x + w, new_y + h)

            self.selected_text = self.dragged_object
            self.selected_image = None

        elif isinstance(self.dragged_object, EditableImage):
            w = self.dragged_object.bbox[2] - self.dragged_object.bbox[0]
            h = self.dragged_object.bbox[3] - self.dragged_object.bbox[1]
            self.dragged_object.bbox = (new_x, new_y, new_x + w, new_y + h)

            self.selected_image = self.dragged_object
            self.selected_text = None

        self.pdf_view.queue_draw()


    def on_drag_end(self, gesture, offset_x, offset_y):
        if not self.dragged_object:
            return

        self.status_label.set_text("Nesne konumu güncelleniyor...")

        self.dragged_object.modified = True

        success, msg = pdf_handler.apply_object_edit(self.doc, self.dragged_object)

        if success:
            self.document_modified = True

            self._load_page(self.current_page_index)
            self.status_label.set_text("Nesne taşındı. Kaydetmeyi unutmayın.")
        else:
            show_error_dialog(self, f"Nesne taşınamadı: {msg}")
            self._load_page(self.current_page_index)

        self.dragged_object = None
        self.selected_image = None
        self.selected_text = None
        self._update_ui_state()

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

    def do_close_request(self):
        if self.check_unsaved_changes():
            return True
        else:
            self.close_document()
            return False