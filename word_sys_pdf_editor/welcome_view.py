import gi
import random
from pathlib import Path

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib

from .i18n import _, get_language, set_language


class WelcomeView(Adw.Bin):
    def __init__(self, parent_window, **kwargs):
        super().__init__(**kwargs)
        self.parent_window = parent_window

        self.recent_manager = Gtk.RecentManager.get_default()
        self._build_ui()
        self._populate_recent_files()
        self.recent_manager.connect("changed", self._populate_recent_files)

    def _build_ui(self):
        clamp = Adw.Clamp(maximum_size=800, tightening_threshold=300)
        self.set_child(clamp)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        main_box.set_vexpand(True)
        main_box.set_valign(Gtk.Align.CENTER)
        clamp.set_child(main_box)
        main_box.set_margin_bottom(40)

        try:
            icon_path = Path(__file__).resolve().parent / "img" / "f-pv1.png"
            app_icon = Gtk.Picture.new_for_filename(str(icon_path))
            app_icon.set_size_request(200, 200)
            app_icon.set_valign(Gtk.Align.END)
            app_icon.set_halign(Gtk.Align.CENTER)
            app_icon.set_margin_bottom(20)
            main_box.append(app_icon)
        except Exception as e:
            print(f"Welcome screen icon could not be loaded: {e}")

        title = Gtk.Label(label="Word-Sys's PDF Editor")
        title.add_css_class("title-1")
        main_box.append(title)

        subtitle = Gtk.Label(label=_("app_subtitle"))
        subtitle.add_css_class("dim-label")
        main_box.append(subtitle)

        lang_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
        )
        lang_box.set_margin_top(4)

        lang_icon = Gtk.Image.new_from_icon_name("preferences-desktop-locale-symbolic")
        lang_box.append(lang_icon)

        lang_label = Gtk.Label(label=_("lang_label"))
        lang_label.add_css_class("dim-label")
        lang_box.append(lang_label)

        self._lang_en_btn = Gtk.ToggleButton(label=_("lang_en"))
        self._lang_en_btn.set_active(get_language() == "en")
        self._lang_en_btn.add_css_class("flat")
        lang_box.append(self._lang_en_btn)

        self._lang_tr_btn = Gtk.ToggleButton(label=_("lang_tr"), group=self._lang_en_btn)
        self._lang_tr_btn.set_active(get_language() == "tr")
        self._lang_tr_btn.add_css_class("flat")
        lang_box.append(self._lang_tr_btn)

        self._lang_en_btn.connect("toggled", self._on_lang_toggled, "en")
        self._lang_tr_btn.connect("toggled", self._on_lang_toggled, "tr")

        main_box.append(lang_box)

        button_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER
        )
        button_box.set_margin_top(16)
        main_box.append(button_box)

        new_button = Gtk.Button(label=_("btn_new"))
        new_button.connect("clicked", lambda w: self.parent_window.on_new_clicked())
        button_box.append(new_button)

        open_button = Gtk.Button(label=_("btn_open"))
        open_button.get_style_context().add_class("suggested-action")
        open_button.connect("clicked", self.on_open_clicked)
        button_box.append(open_button)

        guide_button = Gtk.Button(label=_("btn_guide"))
        guide_button.set_action_name("win.quick_guide")
        button_box.append(guide_button)

        recent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        recent_box.set_margin_top(30)
        main_box.append(recent_box)

        recent_label = Gtk.Label(label=_("recent_header"))
        recent_label.set_use_markup(True)
        recent_label.set_halign(Gtk.Align.START)
        recent_box.append(recent_label)

        self.recent_list_box = Gtk.ListBox()
        self.recent_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.recent_list_box.add_css_class("boxed-list")
        recent_box.append(self.recent_list_box)

        tips = _("tips")
        tip_text = random.choice(tips) if isinstance(tips, list) else str(tips)
        tip_label = Gtk.Label(label=tip_text)
        tip_label.add_css_class("dim-label")
        tip_label.set_halign(Gtk.Align.CENTER)
        tip_label.set_wrap(True)
        tip_label.set_margin_top(40)
        main_box.append(tip_label)

    def _on_lang_toggled(self, button, lang_code):
        if not button.get_active():
            return
        if lang_code == get_language():
            return
        self._confirm_language_switch(lang_code)

    def _confirm_language_switch(self, lang_code):
        from gi.repository import Gtk
        from .ui_components import show_confirm_dialog

        if self.parent_window.doc and self.parent_window.document_modified:
            if not show_confirm_dialog(
                self.parent_window,
                _("unsaved_changes"),
                _("unsaved_title"),
                destructive=False,
            ):
                cur = get_language()
                self._lang_en_btn.handler_block_by_func(self._on_lang_toggled)
                self._lang_tr_btn.handler_block_by_func(self._on_lang_toggled)
                self._lang_en_btn.set_active(cur == "en")
                self._lang_tr_btn.set_active(cur == "tr")
                self._lang_en_btn.handler_unblock_by_func(self._on_lang_toggled)
                self._lang_tr_btn.handler_unblock_by_func(self._on_lang_toggled)
                return

        dialog = Gtk.MessageDialog(
            transient_for=self.parent_window,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=_("lang_restart_title") + "\n\n" + _("lang_restart_msg"),
        )

        def on_response(d, response_id):
            d.destroy()
            if response_id == Gtk.ResponseType.OK:
                set_language(lang_code)
            else:
                cur = get_language()
                self._lang_en_btn.handler_block_by_func(self._on_lang_toggled)
                self._lang_tr_btn.handler_block_by_func(self._on_lang_toggled)
                self._lang_en_btn.set_active(cur == "en")
                self._lang_tr_btn.set_active(cur == "tr")
                self._lang_en_btn.handler_unblock_by_func(self._on_lang_toggled)
                self._lang_tr_btn.handler_unblock_by_func(self._on_lang_toggled)

        dialog.connect("response", on_response)
        dialog.present()

    def _populate_recent_files(self, *args):
        child = self.recent_list_box.get_first_child()
        while child:
            self.recent_list_box.remove(child)
            child = self.recent_list_box.get_first_child()

        items = self.recent_manager.get_items()
        pdf_files_found = 0
        for item in items:
            if item.get_mime_type() == "application/pdf" and pdf_files_found < 5:
                row = self._create_recent_file_row(item)
                self.recent_list_box.append(row)
                pdf_files_found += 1

        self.recent_list_box.set_visible(pdf_files_found > 0)

    def _create_recent_file_row(self, item):
        action_row = Adw.ActionRow()
        action_row.set_title(item.get_display_name())
        try:
            file_path = Path(item.get_uri_display())
            action_row.set_subtitle(str(file_path.parent))
        except Exception:
            action_row.set_subtitle(item.get_uri_display())
        action_row.set_activatable(True)
        action_row.connect("activated", self.on_recent_file_activated, item.get_uri())
        icon = Gtk.Image.new_from_icon_name("application-pdf-symbolic")
        action_row.add_prefix(icon)
        return action_row

    def on_open_clicked(self, button):
        if self.parent_window:
            self.parent_window.on_open_clicked(button)

    def on_recent_file_activated(self, row, uri):
        if self.parent_window:
            gfile = Gio.File.new_for_uri(uri)
            self.parent_window.load_document(gfile.get_path())