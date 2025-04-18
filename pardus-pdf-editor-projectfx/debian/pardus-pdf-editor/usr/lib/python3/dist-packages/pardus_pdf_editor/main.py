import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gio, GLib, Adw

# Use relative import within the package
from .window import PdfEditorWindow

class PdfEditorApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id='org.pardus.pdfeditor',
                         flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.window = None

        quit_action = Gio.SimpleAction.new('quit', None)
        quit_action.connect('activate', self.on_quit)
        self.add_action(quit_action)
        self.set_accels_for_action('app.quit', ['<Control>q'])

    def do_activate(self):
        if not self.window:
            self.window = PdfEditorWindow(application=self)
        self.window.present()

    def do_open(self, files, n_files, hint):
        # Ensure window is created even when opening files first
        if not self.window:
             # Call do_activate implicitly to create the window
             self.activate()

        if n_files > 0:
            filepath = files[0].get_path()
            if filepath:
                # Use idle_add to ensure window is fully initialized and shown
                # before starting file load process.
                GLib.idle_add(self.window.load_document, filepath)

        self.window.present()


    def on_quit(self, action, param):
        # Check for unsaved changes before quitting
        if self.window:
             if self.window.check_unsaved_changes():
                  return # User cancelled quit
             self.window.close_document() # Clean up document handle if needed

        print("Quitting application.")
        self.quit() # Quits the GLib.Application main loop

def main():
    # Initialize Adwaita (often done implicitly, but good practice)
    Adw.init()
    app = PdfEditorApplication()
    return app.run(sys.argv)

# This part is removed if main.py is run as part of the package
# if __name__ == '__main__':
#     sys.exit(main())