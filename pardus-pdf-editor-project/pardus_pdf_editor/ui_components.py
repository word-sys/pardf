import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, GdkPixbuf, Adw

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
    """Displays an error message dialog."""
    dialog = Adw.MessageDialog(
        transient_for=parent_window,
        modal=True,
        heading=title,
        body=message,
    )
    dialog.add_response("ok", "_Close")
    dialog.set_default_response("ok")
    dialog.connect("response", lambda d, r: d.close())
    dialog.present()

def show_confirm_dialog(parent_window, message, title="Confirm", destructive=True):
    """Displays a confirmation dialog and returns True if confirmed."""
    dialog = Adw.MessageDialog(
        transient_for=parent_window,
        modal=True,
        heading=title,
        body=message,
    )
    dialog.add_response("cancel", "_Cancel")
    dialog.add_response("confirm", "_Confirm")
    dialog.set_default_response("cancel")
    if destructive:
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)

    # Gtk.Dialog.run() is deprecated in GTK4. Use signals.
    # We need a way to block or use callbacks. For simplicity here,
    # we'll return the dialog and the caller needs a callback.
    # A simpler synchronous-like approach for this specific case:
    response = None
    def on_response(dialog, resp_id):
        nonlocal response
        response = resp_id
        dialog.close() # Close before destroy

    dialog.connect("response", on_response)
    dialog.present()
    # Block execution (simplistic approach, better use async/await or callbacks in real app)
    # This requires the main loop to be running.
    while response is None:
         # This is NOT ideal, but avoids complex callback structures for this example.
         # Use GLib.MainLoop with quit() in the callback for proper blocking if needed.
         if Gtk.events_pending():
             Gtk.main_iteration()
         else:
             # Avoid busy-waiting too aggressively
             import time
             time.sleep(0.01)

    dialog.destroy()
    return response == "confirm"