import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gio, GLib, Adw

from .window import PdfEditorWindow

class PdfEditorApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id='org.wordsys.pdfeditor',
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
        if not self.window:
             self.activate()

        if n_files > 0:
            filepath = files[0].get_path()
            if filepath:
                GLib.idle_add(self.window.load_document, filepath)

        self.window.present()


    def on_quit(self, action, param):
        if self.window:
             if self.window.check_unsaved_changes():
                  return
             self.window.close_document()

        print("Quitting application.")
        self.quit()

def main():
    Adw.init()
    app = PdfEditorApplication()
    return app.run(sys.argv)