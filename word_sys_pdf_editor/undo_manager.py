import copy
from . import pdf_handler
from .models import EditableText

class Command:
    def __init__(self, window):
        self.window = window

    def execute(self):
        raise NotImplementedError

    def undo(self):
        raise NotImplementedError

class UndoManager:
    def __init__(self, window):
        self.window = window
        self.undo_stack = []
        self.redo_stack = []
        self._update_ui_callback = self.window._update_undo_redo_buttons

    def add_command(self, command):
        self.undo_stack.append(command)
        self.redo_stack.clear()
        self._update_ui_callback()

    def undo(self):
        if not self.undo_stack:
            return
        command = self.undo_stack.pop()
        command.undo()
        self.redo_stack.append(command)
        self._update_ui_callback()
        self.window.pdf_view.queue_draw()

    def redo(self):
        if not self.redo_stack:
            return
        command = self.redo_stack.pop()
        command.execute()
        self.undo_stack.append(command)
        self._update_ui_callback()
        self.window.pdf_view.queue_draw()

    def clear(self):
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._update_ui_callback()

class EditObjectCommand(Command):
    def __init__(self, window, target_object, old_properties, new_properties):
        super().__init__(window)
        self.target_object = target_object
        self.old_properties = old_properties
        self.new_properties = new_properties

    def _apply_properties_to_pdf(self, properties_to_apply, properties_to_clear):

        temp_obj_for_pdf = copy.deepcopy(self.target_object)
        temp_obj_for_pdf.__dict__.update(copy.deepcopy(properties_to_apply))
        temp_obj_for_pdf.original_bbox = properties_to_clear['bbox']
        
        success, msg = pdf_handler.apply_object_edit(self.window.doc, temp_obj_for_pdf)
        
        if not success:
            self.window.show_error_dialog(self.window, f"İşlem sırasında hata: {msg}")
        
        return success

    def _update_live_object(self, properties_to_apply):
        self.target_object.__dict__.update(copy.deepcopy(properties_to_apply))
        self.target_object.original_bbox = self.target_object.bbox
        self.target_object.modified = False
        self.window.document_modified = True

    def execute(self):
        if self._apply_properties_to_pdf(self.new_properties, self.old_properties):
            self._update_live_object(self.new_properties)
            self.window.status_label.set_text("Değişiklik uygulandı.")
            self.window.pdf_view.queue_draw()

    def undo(self):
        if self._apply_properties_to_pdf(self.old_properties, self.new_properties):
            self._update_live_object(self.old_properties)
            self.window.status_label.set_text("Geri alındı.")
            self.window.pdf_view.queue_draw()

class AddObjectCommand(Command):
    def __init__(self, window, new_object):
        super().__init__(window)
        self.new_object = new_object
        self.is_text = isinstance(new_object, EditableText)

    def execute(self):
        if self.is_text:
            self.window.editable_texts.append(self.new_object)
        else:
            self.window.editable_images.append(self.new_object)
        
        pdf_handler.apply_object_edit(self.window.doc, self.new_object)
        self.window.document_modified = True
        self.window.status_label.set_text("Nesne eklendi.")
        self.window._update_ui_state()
        self.window.pdf_view.queue_draw()

    def undo(self):
        if self.is_text:
            if self.new_object in self.window.editable_texts:
                self.window.editable_texts.remove(self.new_object)
        else:
            if self.new_object in self.window.editable_images:
                self.window.editable_images.remove(self.new_object)
        
        self.window.document_modified = True
        self.window.status_label.set_text("Geri alındı.")
        self.window._update_ui_state()
        self.window.pdf_view.queue_draw()

    def undo(self):
        if self.is_text:
            pdf_handler.apply_text_edit(self.window.doc, self.new_object, "")
        else:
            pdf_handler.delete_image_from_page(self.window.doc, self.new_object)
        self.window._load_page(self.window.current_page_index, preserve_scroll=True)


class DeleteObjectCommand(Command):
    def __init__(self, window, deleted_object):
        super().__init__(window)
        self.deleted_object = deleted_object
        self.is_text = isinstance(deleted_object, EditableText)

    def execute(self):
        if self.is_text:
            pdf_handler.apply_text_edit(self.window.doc, self.deleted_object, "")
        else:
            pdf_handler.delete_image_from_page(self.window.doc, self.deleted_object)
        self.window._load_page(self.window.current_page_index, preserve_scroll=True)

    def undo(self):
        pdf_handler.apply_object_edit(self.window.doc, self.deleted_object)
        self.window._load_page(self.window.current_page_index, preserve_scroll=True)