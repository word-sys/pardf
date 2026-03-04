import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, GdkPixbuf, Adw, GLib, GObject, Gio


class PageThumbnailFactory(Gtk.SignalListItemFactory):
    def __init__(self, editor_window=None):
        super().__init__()
        self.editor_window = editor_window
        self.connect("setup", self._on_setup)
        self.connect("bind", self._on_bind)

    def _on_setup(self, factory, list_item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6)
        
        image = Gtk.Picture()
        image.set_size_request(150, -1)
        image.set_can_shrink(False)

        label = Gtk.Label()
        label.set_halign(Gtk.Align.CENTER)
        
        box.append(image)
        box.append(label)
        list_item.set_child(box)

    def _on_bind(self, factory, list_item):
        box = list_item.get_child()
        picture = box.get_first_child()
        label = box.get_last_child()
        pdf_page = list_item.get_item()

        if pdf_page and pdf_page.thumbnail:
            texture = Gdk.Texture.new_for_pixbuf(pdf_page.thumbnail)
            picture.set_paintable(texture)
            picture.set_visible(True)
        else:
            picture.set_paintable(None)
            picture.set_visible(False)

        page_index = pdf_page.index
        label.set_text(f"Sayfa {page_index + 1}")

        for ctrl in list(box.observe_controllers()):
            if isinstance(ctrl, Gtk.DragSource) or isinstance(ctrl, Gtk.DropTarget):
                box.remove_controller(ctrl)

        drag_source = Gtk.DragSource.new()
        drag_source.set_actions(Gdk.DragAction.MOVE)

        def on_prepare(source, x, y, idx=page_index):
            val = GObject.Value(GObject.TYPE_INT, idx)
            return Gdk.ContentProvider.new_for_value(val)

        def on_drag_begin(source, drag, idx=page_index, pic=picture):
            pdf_pg = list_item.get_item()
            if pdf_pg and pdf_pg.thumbnail:
                tex = Gdk.Texture.new_for_pixbuf(pdf_pg.thumbnail)
                Gtk.DragSource.set_icon(source, tex, 0, 0)

        drag_source.connect("prepare", on_prepare)
        drag_source.connect("drag-begin", on_drag_begin)
        box.add_controller(drag_source)

        drop_target = Gtk.DropTarget.new(GObject.TYPE_INT, Gdk.DragAction.MOVE)

        def on_drop(target, value, x, y, to_idx=page_index):
            from_idx = value
            if from_idx == to_idx:
                return False
            if self.editor_window:
                self.editor_window.on_page_reorder(from_idx, to_idx)
            return True

        drop_target.connect("drop", on_drop)
        box.add_controller(drop_target)


def show_error_dialog(parent_window, message, title="Error"):
    dialog = Gtk.MessageDialog(
        transient_for=parent_window,
        modal=True,
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.CLOSE,
        text=title,
        secondary_text=message
    )

    dialog.connect("response", lambda d, response_id: d.destroy())
    dialog.present()

def show_confirm_dialog(parent_window, message, title="Confirm", destructive=True):
    message_type = Gtk.MessageType.QUESTION
    if destructive:
        message_type = Gtk.MessageType.WARNING

    dialog = Gtk.MessageDialog(
        transient_for=parent_window,
        modal=True,
        message_type=message_type,
        buttons=Gtk.ButtonsType.NONE,
        text=title,
        secondary_text=message
    )

    dialog.add_buttons(
        "_Cancel", Gtk.ResponseType.CANCEL,
        "_Confirm", Gtk.ResponseType.ACCEPT
    )
    dialog.set_default_response(Gtk.ResponseType.CANCEL)

    response = None
    def on_response(d, resp_id):
        nonlocal response
        response = resp_id
        d.destroy()

    dialog.connect("response", on_response)
    dialog.present()

    while response is None:
         context = GLib.MainContext.default()
         context.iteration(True)

    dialog.destroy()
    return response == Gtk.ResponseType.ACCEPT

def show_save_changes_dialog(parent_window):
    dialog = Gtk.MessageDialog(
        transient_for=parent_window,
        modal=True,
        message_type=Gtk.MessageType.QUESTION,
        buttons=Gtk.ButtonsType.NONE,
        text="Kaydedilmemiş Değişiklikler",
        secondary_text="Kaydedilmemiş değişiklikler var. Ne yapmak istersiniz?"
    )

    dialog.add_buttons(
        "İptal", Gtk.ResponseType.CANCEL,
        "Kaydetme", Gtk.ResponseType.REJECT,
        "Kaydet", Gtk.ResponseType.ACCEPT
    )
    dialog.set_default_response(Gtk.ResponseType.ACCEPT)

    response = None
    def on_response(d, resp_id):
        nonlocal response
        response = resp_id
        d.destroy()

    dialog.connect("response", on_response)
    dialog.present()

    while response is None:
         context = GLib.MainContext.default()
         context.iteration(True)

    return response