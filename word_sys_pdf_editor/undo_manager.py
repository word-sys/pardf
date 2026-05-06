import copy
from . import pdf_handler
from .models import EditableText, EditableShape
from .i18n import _

class Command:
    def __init__(self, window):
        self.window = window

    def execute(self):
        raise NotImplementedError

    def undo(self):
        raise NotImplementedError

    def _erase_ghost_if_needed(self, target_object, page_num):
        if getattr(target_object, 'is_new', True) or getattr(target_object, '_ghost_redacted', False):
            return
            
        import fitz
        from . import pdf_handler
        from .models import EditableText, EditableImage, EditableShape
        
        pdf_handler.restore_page_from_snapshot(self.window.doc, page_num)
        
        orig_bbox = getattr(target_object, 'original_bbox', target_object.bbox)
        if isinstance(target_object, EditableShape):
            x0, y0, x1, y1 = orig_bbox
            redact_rect = fitz.Rect(x0 - 20, y0 - 20, x1 + 20, y1 + 20)
        else:
            redact_rect = fitz.Rect(orig_bbox)
        try:
            page = self.window.doc.load_page(page_num)
            page.add_redact_annot(redact_rect)
            
            if isinstance(target_object, EditableText):
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=False)
            elif isinstance(target_object, EditableImage):
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE, graphics=False)
            else:
                try:
                    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=True)
                except Exception:
                    page.apply_redactions()
                    
            self.window.doc.load_page(page_num)
            pdf_handler.save_page_snapshot(self.window.doc, page_num, force=True)
            target_object._ghost_redacted = True
        except Exception as e:
            print(f"Warning: could not erase ghost from snapshot for page {page_num}: {e}")

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

    def _erase_ghost_if_needed(self, page_num, properties_to_clear):
        if getattr(self.target_object, 'is_new', True) or getattr(self.target_object, '_ghost_redacted', False):
            return
            
        import fitz
        from . import pdf_handler
        from .models import EditableText, EditableImage, EditableShape
        
        pdf_handler.restore_page_from_snapshot(self.window.doc, page_num)
        
        orig_bbox = getattr(self.target_object, 'original_bbox', self.target_object.bbox)
        if isinstance(self.target_object, EditableShape):
            x0, y0, x1, y1 = orig_bbox
            redact_rect = fitz.Rect(x0 - 20, y0 - 20, x1 + 20, y1 + 20)
        else:
            redact_rect = fitz.Rect(orig_bbox)
        try:
            page = self.window.doc.load_page(page_num)
            page.add_redact_annot(redact_rect)
            
            if isinstance(self.target_object, EditableText):
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=False)
            elif isinstance(self.target_object, EditableImage):
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE, graphics=False)
            else:
                try:
                    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=True)
                except Exception:
                    page.apply_redactions()
                    
            self.window.doc.load_page(page_num)
            pdf_handler.save_page_snapshot(self.window.doc, page_num, force=True)
            self.target_object._ghost_redacted = True
        except Exception as e:
            print(f"Warning: could not erase ghost from snapshot for page {page_num}: {e}")

    def _apply_properties_to_pdf(self, properties_to_apply, properties_to_clear):
        page_num = getattr(self.target_object, 'page_number', None)
        if page_num is not None:
            self._erase_ghost_if_needed(page_num, properties_to_clear)
            
        if isinstance(self.target_object, EditableShape):
            temp_obj = copy.deepcopy(self.target_object)
            temp_obj.__dict__.update(copy.deepcopy(properties_to_apply))
            temp_obj.original_bbox = properties_to_clear['bbox']
            if page_num is not None:
                pdf_handler.rebuild_page(
                    self.window.doc, page_num,
                    self.window.editable_texts,
                    self.window.editable_shapes,
                    self.window.editable_images,
                    exclude_obj=self.target_object
                )
            success, msg = pdf_handler.apply_object_edit(self.window.doc, temp_obj)
            if success:
                self.target_object.is_baked = True
                if page_num is not None:
                    self.window._refresh_thumbnail(page_num)
            else:
                from .ui_components import show_error_dialog
                show_error_dialog(self.window, f"Şekil taşınırken hata: {msg}")
            return success
            
        temp_obj_for_pdf = copy.deepcopy(self.target_object)
        temp_obj_for_pdf.__dict__.update(copy.deepcopy(properties_to_apply))
        temp_obj_for_pdf.original_bbox = properties_to_clear['bbox']
        
        page_num_fallback = getattr(self.target_object, 'page_number', None)
        if page_num_fallback is not None:
            pdf_handler.rebuild_page(
                self.window.doc, page_num_fallback,
                self.window.editable_texts,
                self.window.editable_shapes,
                self.window.editable_images,
                exclude_obj=self.target_object
            )
        
        success, msg = pdf_handler.apply_object_edit(self.window.doc, temp_obj_for_pdf)
        
        if not success:
            from .ui_components import show_error_dialog
            show_error_dialog(self.window, f"İşlem sırasında hata: {msg}")
        
        return success

    def _update_live_object(self, properties_to_apply):
        self.target_object.__dict__.update(copy.deepcopy(properties_to_apply))
        self.target_object.original_bbox = self.target_object.bbox
        self.target_object.modified = False
        self.window.document_modified = True
        if not isinstance(self.target_object, EditableShape):
            page_num = getattr(self.target_object, 'page_number', None)
            if page_num is not None:
                self.window._refresh_thumbnail(page_num)

    def execute(self):
        if self._apply_properties_to_pdf(self.new_properties, self.old_properties):
            self._update_live_object(self.new_properties)
            self.window.status_label.set_text(_("change_applied"))
            self.window.pdf_view.queue_draw()

    def undo(self):
        if self._apply_properties_to_pdf(self.old_properties, self.new_properties):
            self._update_live_object(self.old_properties)
            self.window.status_label.set_text(_("reverted"))
            self.window.pdf_view.queue_draw()

class AddObjectCommand(Command):
    def __init__(self, window, new_object):
        super().__init__(window)
        self.new_object = new_object
        self.is_text = isinstance(new_object, EditableText)
        self.is_shape = isinstance(new_object, EditableShape)
        self.is_image = not (self.is_text or self.is_shape)

    def _refresh_thumb(self):
        page_num = getattr(self.new_object, 'page_number', None)
        if page_num is not None:
            self.window._refresh_thumbnail(page_num)

    def execute(self):
        if self.is_text:
            if self.new_object not in self.window.editable_texts:
                self.window.editable_texts.append(self.new_object)
        elif self.is_shape:
            if self.new_object not in self.window.editable_shapes:
                self.window.editable_shapes.append(self.new_object)
        else:
            if self.new_object not in self.window.editable_images:
                self.window.editable_images.append(self.new_object)
                
        self.new_object.is_baked = True
        page_num = getattr(self.new_object, 'page_number', self.window.current_page_index)
        pdf_handler.rebuild_page(
            self.window.doc, page_num,
            self.window.editable_texts,
            self.window.editable_shapes,
            self.window.editable_images
        )
        self._refresh_thumb()

        self.window.document_modified = True
        self.window.status_label.set_text(_("object_added"))
        self.window._update_ui_state()
        self.window.pdf_view.queue_draw()

    def undo(self):
        if self.is_text and self.new_object in self.window.editable_texts:
            self.window.editable_texts.remove(self.new_object)
        elif self.is_shape and self.new_object in self.window.editable_shapes:
            self.window.editable_shapes.remove(self.new_object)
        elif self.is_image and self.new_object in self.window.editable_images:
            self.window.editable_images.remove(self.new_object)

        page_num = getattr(self.new_object, 'page_number', self.window.current_page_index)
        pdf_handler.rebuild_page(
            self.window.doc, page_num,
            self.window.editable_texts,
            self.window.editable_shapes,
            self.window.editable_images
        )

        self.window.document_modified = True
        self.window.status_label.set_text(_("reverted"))
        self.window._refresh_thumbnail(page_num)
        self.window.pdf_view.queue_draw()



class DeleteObjectCommand(Command):
    def __init__(self, window, deleted_object):
        super().__init__(window)
        self.deleted_object = deleted_object
        self.is_text = isinstance(deleted_object, EditableText)
        self.is_shape = isinstance(deleted_object, EditableShape)

    def execute(self):
        if self.is_text and self.deleted_object in self.window.editable_texts:
            self.window.editable_texts.remove(self.deleted_object)
        elif self.is_shape and self.deleted_object in self.window.editable_shapes:
            self.window.editable_shapes.remove(self.deleted_object)
        elif not self.is_text and not self.is_shape and self.deleted_object in self.window.editable_images:
            self.window.editable_images.remove(self.deleted_object)

        page_num = getattr(self.deleted_object, 'page_number', self.window.current_page_index)
        if page_num is not None:
            self._erase_ghost_if_needed(self.deleted_object, page_num)
            
        pdf_handler.rebuild_page(
            self.window.doc, page_num,
            self.window.editable_texts,
            self.window.editable_shapes,
            self.window.editable_images
        )

        self.window.document_modified = True
        self.window.status_label.set_text(_("object_deleted"))
        self.window._refresh_thumbnail(page_num)
        self.window.pdf_view.queue_draw()

    def undo(self):
        if self.is_text and self.deleted_object not in self.window.editable_texts:
            self.window.editable_texts.append(self.deleted_object)
        elif self.is_shape and self.deleted_object not in self.window.editable_shapes:
            self.window.editable_shapes.append(self.deleted_object)
        elif not self.is_text and not self.is_shape and self.deleted_object not in self.window.editable_images:
            self.window.editable_images.append(self.deleted_object)

        page_num = getattr(self.deleted_object, 'page_number', self.window.current_page_index)
        pdf_handler.rebuild_page(
            self.window.doc, page_num,
            self.window.editable_texts,
            self.window.editable_shapes,
            self.window.editable_images
        )

        self.window.document_modified = True
        self.window.status_label.set_text(_("delete_reverted"))
        self.window._refresh_thumbnail(page_num)
        self.window.pdf_view.queue_draw()

class CompositeCommand(Command):
    def __init__(self, window, commands):
        super().__init__(window)
        self.commands = commands
        
    def execute(self):
        for command in self.commands:
            command.execute()
            
    def undo(self):
        for command in reversed(self.commands):
            command.undo()