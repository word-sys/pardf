import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, GdkPixbuf, Adw, GLib

class PageThumbnailFactory(Gtk.SignalListItemFactory):
    """Factory to create widgets for the thumbnail GridView."""
    def __init__(self):
        super().__init__()
        self.connect("setup", self._on_setup)
        self.connect("bind", self._on_bind)

    def _on_setup(self, factory, list_item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6)
        image = Gtk.Picture()
        image.set_can_shrink(False)
        label = Gtk.Label()
        box.append(image)
        box.append(label)
        list_item.set_child(box)

    def _on_bind(self, factory, list_item):
        box = list_item.get_child()
        picture = box.get_first_child()
        label = box.get_last_child()
        pdf_page = list_item.get_item() # This is the GObject PdfPage

        if pdf_page and pdf_page.thumbnail:
            texture = Gdk.Texture.new_for_pixbuf(pdf_page.thumbnail)
            picture.set_paintable(texture)
            picture.set_visible(True)
        else:
            picture.set_paintable(None)
            picture.set_visible(False)

        label.set_text(f"Page {pdf_page.index + 1}")


def show_error_dialog(parent_window, message, title="Error"):
    """Displays an error message dialog (Using Gtk for compatibility)."""
    # Use Gtk.MessageDialog and set secondary_text in the constructor
    dialog = Gtk.MessageDialog(
        transient_for=parent_window,
        modal=True,
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.CLOSE,
        text=title,
        secondary_text=message # Set secondary text directly here
    )
    # No need for format_secondary_text or set_secondary_text

    dialog.connect("response", lambda d, response_id: d.destroy())
    dialog.present()

def show_confirm_dialog(parent_window, message, title="Confirm", destructive=True):
    """Displays a confirmation dialog (Using Gtk for compatibility) and returns True if confirmed."""
    message_type = Gtk.MessageType.QUESTION
    if destructive:
        message_type = Gtk.MessageType.WARNING

    # Use Gtk.MessageDialog and set secondary_text in the constructor
    dialog = Gtk.MessageDialog(
        transient_for=parent_window,
        modal=True,
        message_type=message_type,
        buttons=Gtk.ButtonsType.NONE,
        text=title,
        secondary_text=message # Set secondary text directly here
    )
    # No need for format_secondary_text or set_secondary_text

    dialog.add_buttons(
        "_Cancel", Gtk.ResponseType.CANCEL,
        "_Confirm", Gtk.ResponseType.ACCEPT
    )
    dialog.set_default_response(Gtk.ResponseType.CANCEL)

    # Keep the existing blocking mechanism
    response = None
    def on_response(d, resp_id):
        nonlocal response
        response = resp_id
        d.destroy()

    dialog.connect("response", on_response)
    dialog.present()

    # Block execution (simplistic approach, better use async/await or callbacks in real app)
    # This requires the main loop to be running.
    while response is None:
         # In GTK4, use GLib.MainContext.default().iteration()
         # Passing True allows blocking if no events are ready, preventing busy loop.
         context = GLib.MainContext.default()
         context.iteration(True) # Process one event loop iteration

    dialog.destroy()
    return response == Gtk.ResponseType.ACCEPT
