#!/usr/bin/env python3
import sys
import gi
import pypdf # Note: pypdf is imported but not used. fitz (PyMuPDF) is used.
import cairo
import tempfile
from pathlib import Path
# from PIL import Image # Not used directly
import io
import fitz # PyMuPDF
import os
import numpy as np
import subprocess
import shutil
import threading
import time # For demonstration purposes if needed

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gio, GLib, Adw, Gdk, GdkPixbuf, Pango, GObject, PangoCairo

# --- Helper Functions ---

def normalize_color(color_val):
    """
    Normalizes color to a tuple of floats (r, g, b) between 0.0 and 1.0.
    Handles integers (assumes grayscale) and tuples/lists.
    """
    if isinstance(color_val, (int, float)):
        # Assume grayscale integer or float
        val = float(color_val)
        # PyMuPDF color integers are often not > 1 unless it's packed RGB
        # If fitz returns floats 0-1 directly, this handles it.
        # If it returns int 0-255, this might need adjustment based on color space.
        # Let's assume fitz with flags=11 gives floats or simple grayscale int 0.
        if val > 1.0: # Heuristic for common 0-255 int representation
             val = val / 255.0
        val = max(0.0, min(1.0, val))
        return (val, val, val)
    elif isinstance(color_val, (list, tuple)) and len(color_val) >= 3:
        # Assume RGB tuple/list
        rgb = list(color_val[:3])
        for i in range(3):
            if isinstance(rgb[i], (int, float)):
                val = float(rgb[i])
                if val > 1.0: # Heuristic for 0-255
                    val = val / 255.0
                rgb[i] = max(0.0, min(1.0, val))
            else:
                rgb[i] = 0.0 # Default if type is unexpected
        return tuple(rgb)
    # Default to black if input is unusable
    return (0.0, 0.0, 0.0)

# --- Editable Text Class ---

class EditableText:
    def __init__(self, x, y, text, font_size=12, font_family="Helvetica",
                 color=(0, 0, 0), span_data=None):
        self.x = x # Original bbox[0]
        self.y = y # Original bbox[1]
        self.text = text
        self.original_text = text
        self.font_size = font_size

        # Basic font mapping (can be expanded)
        font_mapping = {
            'Sans': 'helv', #'Helvetica',
            'Serif': 'timr', #'Times-Roman',
            'Monospace': 'cour', #'Courier',
            # Add mappings based on common names found in span['font'] if needed
            'Helvetica': 'helv',
            'Times': 'timr',
            'Times-Roman': 'timr',
            'Courier': 'cour',
        }
        # Prefer specific font name, fallback to mapped, then default
        pdf_font_name = span_data.get('font', 'Helvetica') if span_data else 'Helvetica'
        self.font_family = pdf_font_name # Store the original name
        self.pdf_fontname = font_mapping.get(pdf_font_name.split('+')[-1].split('-')[0], 'helv') # Attempt to map to base14

        # Normalize and store color immediately
        self.color = normalize_color(color) # Current color (editable)
        self.original_color = self.color # Store the initial normalized color

        self.selected = False
        self.editing = False
        self.span_data = span_data # Store original span dict if needed later
        self.modified = False
        self.bbox = span_data.get("bbox", (x,y,x+10, y+font_size)) if span_data else (x,y,x+10, y+font_size) # Get original bbox
        self.baseline = self.bbox[3] # y-coordinate of baseline
        self.page_number = None # Set when extracted

        # For potential dragging (not implemented in click logic yet)
        self.dragging = False
        self.drag_start_x = 0
        self.drag_start_y = 0

# --- GObjects & Factories ---

class PdfPage(GObject.GObject):
    __gtype_name__ = 'PdfPage'
    index = GObject.Property(type=int)
    thumbnail = GObject.Property(type=GdkPixbuf.Pixbuf)

    def __init__(self, index, thumbnail):
        super().__init__(index=index, thumbnail=thumbnail)

class PageThumbnailFactory(Gtk.SignalListItemFactory):
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
            # Handle case where thumbnail might be missing or invalid
            picture.set_paintable(None)
            picture.set_visible(False)

        label.set_text(f"Page {pdf_page.index + 1}")

# --- Main Application Window ---

class PdfEditorWindow(Adw.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_title("Pardus PDF Editor")
        self.set_default_size(1200, 800)

        self.current_file_path = None
        self.doc = None # fitz.Document
        self.current_page_index = 0
        self.zoom_level = 1.0
        self.pages_model = Gio.ListStore(item_type=PdfPage) # Model for thumbnails
        self.editable_texts = [] # List of EditableText objects for the current page
        self.selected_text = None # The currently selected EditableText object
        self.text_edit_popover = None # Popover for editing text
        self.text_edit_view = None # TextView inside the popover
        self.is_saving = False # Flag to prevent concurrent saves

        self._build_ui()
        self._setup_controllers()
        self._connect_actions()
        self._update_ui_state() # Initial state (controls disabled)

        # Apply custom CSS
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .toolbar {
                padding: 6px;
            }
            .pdf-view {
                background-color: #808080; /* Grey background for contrast */
            }
            .statusbar {
                padding: 4px 8px;
                border-top: 1px solid @borders;
                background-color: @theme_bg_color;
            }
            popover > .box { /* Style popover content box */
                 padding: 10px;
            }
            textview {
                 font-family: monospace; /* Ensure consistent font in editor */
                 min-height: 100px; /* Minimum height for text editor */
                 margin-bottom: 6px;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )


    def _build_ui(self):
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # Use set_content for Adw.ApplicationWindow
        self.set_content(self.main_box)

        # --- Header Bar ---
        # Create the Adw.HeaderBar instance
        header = Adw.HeaderBar()
        # Add the header bar as the FIRST child of the main vertical box
        self.main_box.append(header)
        # DO NOT call self.set_titlebar(header) for Adw.ApplicationWindow

        # Pack widgets into the HeaderBar instance
        self.open_button = Gtk.Button(label="Open")
        self.open_button.connect("clicked", self.on_open_clicked)
        header.pack_start(self.open_button)

        self.save_button = Gtk.Button(label="Save")
        self.save_button.get_style_context().add_class("suggested-action")
        self.save_button.connect("clicked", self.on_save_clicked)
        header.pack_start(self.save_button)

        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        header.pack_end(menu_button)

        # Define menu model for the popover
        menu = Gio.Menu()
        # Use window-specific action names (without "win." prefix as added)
        menu.append("Save As...", "win.save_as")
        menu.append("Export As...", "win.export_as")
        # Add Preferences later if needed
        # menu.append("Preferences", "win.preferences")
        # Use app-specific action name (with "app." prefix)
        menu.append("Quit", "app.quit")

        popover_menu = Gtk.PopoverMenu()
        popover_menu.set_menu_model(menu)
        menu_button.set_popover(popover_menu)
        # --- End Header Bar Setup ---

        # --- Main Paned Layout ---
        # Add the paned view AFTER the header bar in the main_box
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True)
        # Make the paned view expand to fill available vertical space below the header/toolbar
        self.paned.set_vexpand(True)
        self.main_box.append(self.paned)

        # --- Sidebar ---
        # Create sidebar content. _create_sidebar will call self.paned.set_start_child().
        self._create_sidebar()

        # --- Main Content Area (Toolbar + PDF View) ---
        # This box will contain the main toolbar and the PDF view area (overlay)
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_box.set_vexpand(True) # Allow content area to expand vertically

        # Create the main toolbar (for zoom, nav, text format)
        self._create_main_toolbar()
        # Add the toolbar to the content_box
        content_box.append(self.main_toolbar)

        # PDF view setup with Overlay and ScrolledWindow
        self.overlay = Gtk.Overlay()
        self.overlay.set_vexpand(True)
        self.overlay.set_hexpand(True)

        self.pdf_scroll = Gtk.ScrolledWindow()
        self.pdf_scroll.set_hexpand(True)
        self.pdf_scroll.set_vexpand(True)
        self.pdf_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.pdf_view = Gtk.DrawingArea()
        self.pdf_view.set_draw_func(self.draw_pdf_page)
        self.pdf_view.add_css_class('pdf-view')
        self.pdf_view.set_content_width(1) # Initial dummy size
        self.pdf_view.set_content_height(1)
        self.pdf_view.set_hexpand(True) # Allow drawing area to expand horizontally
        self.pdf_view.set_vexpand(True) # Allow drawing area to expand vertically

        # Use a Viewport if DrawingArea might exceed ScrolledWindow size
        self.pdf_viewport = Gtk.Viewport()
        self.pdf_viewport.set_child(self.pdf_view)
        self.pdf_scroll.set_child(self.pdf_viewport)

        # Set the scrollable PDF area as the main child of the overlay
        self.overlay.set_child(self.pdf_scroll)
        # Add the overlay (containing the PDF view) to the content_box
        content_box.append(self.overlay)

        # Set the content_box as the end child of the paned view
        self.paned.set_end_child(content_box)
        # Set initial position of the pane divider
        self.paned.set_position(200) # Initial sidebar width

        # --- Status Bar ---
        # Add the status bar as the LAST child of the main vertical box
        status_bar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        status_bar_box.add_css_class('statusbar')
        self.status_label = Gtk.Label(label="No document loaded.", xalign=0.0)
        status_bar_box.append(self.status_label)
        # Status bar should not expand vertically
        status_bar_box.set_vexpand(False)
        self.main_box.append(status_bar_box)

        # --- Placeholder for when no document is open ---
        # Add this label to the overlay so it appears on top of the (empty) PDF view area
        self.empty_label = Gtk.Label(label="Open a PDF file to start editing\nor drop a file here.")
        self.empty_label.set_vexpand(True)
        self.empty_label.set_halign(Gtk.Align.CENTER)
        self.empty_label.set_valign(Gtk.Align.CENTER)
        self.empty_label.add_css_class("dim-label") # Adwaita style for placeholder text
        self.overlay.add_overlay(self.empty_label)

    def _create_sidebar(self):
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=6, margin_end=6, margin_top=6, margin_bottom=6)
        sidebar_box.set_size_request(180, -1)

        # Thumbnails
        thumbnails_label = Gtk.Label(label="Pages", xalign=0.0)
        thumbnails_label.add_css_class('title-4') # Adwaita heading style
        sidebar_box.append(thumbnails_label)

        factory = PageThumbnailFactory()
        # Use Gtk.GridView for potentially better layout control
        self.thumbnails_list = Gtk.GridView.new(None, factory) # No selection model needed for simple view
        self.thumbnails_list.set_max_columns(1)
        self.thumbnails_list.set_min_columns(1)
        self.thumbnails_list.set_vexpand(True)

        # Allow selecting thumbnails to navigate pages
        self.thumbnail_selection_model = Gtk.SingleSelection(model=self.pages_model)
        self.thumbnails_list.set_model(self.thumbnail_selection_model)
        self.thumbnail_selection_model.connect("selection-changed", self.on_thumbnail_selected)

        thumbnails_scroll = Gtk.ScrolledWindow()
        thumbnails_scroll.set_child(self.thumbnails_list)
        thumbnails_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        thumbnails_scroll.set_vexpand(True)
        sidebar_box.append(thumbnails_scroll)

        # Tools (Keep simple for now)
        # tools_label = Gtk.Label(label="Tools", xalign=0.0)
        # tools_label.add_css_class('title-4')
        # sidebar_box.append(tools_label)
        # tools_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        # tools_box.add_css_class('linked') # Makes buttons look connected

        # TODO: Implement Tool Buttons (Add Text, Image, etc.)
        # Example:
        # add_text_button = Gtk.Button(label="Add Text", icon_name="insert-text-symbolic")
        # add_text_button.connect("clicked", self.on_add_text_tool_activate) # Need specific tool activation logic
        # tools_box.append(add_text_button)
        # sidebar_box.append(tools_box)

        self.paned.set_start_child(sidebar_box)

    def _create_main_toolbar(self):
        self.main_toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.main_toolbar.add_css_class('toolbar')

        # Zoom Controls
        zoom_out_button = Gtk.Button.new_from_icon_name("zoom-out-symbolic")
        zoom_out_button.set_tooltip_text("Zoom Out (Ctrl+ScrollDown)")
        zoom_out_button.connect("clicked", self.on_zoom_out)
        self.main_toolbar.append(zoom_out_button)

        self.zoom_label = Gtk.Label(label="100%")
        self.main_toolbar.append(self.zoom_label)

        zoom_in_button = Gtk.Button.new_from_icon_name("zoom-in-symbolic")
        zoom_in_button.set_tooltip_text("Zoom In (Ctrl+ScrollUp)")
        zoom_in_button.connect("clicked", self.on_zoom_in)
        self.main_toolbar.append(zoom_in_button)

        self.main_toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=6, margin_end=6))

        # Page Navigation
        self.prev_button = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        self.prev_button.set_tooltip_text("Previous Page")
        self.prev_button.connect("clicked", self.on_prev_page)
        self.main_toolbar.append(self.prev_button)

        self.page_label = Gtk.Label(label="Page 0 of 0")
        self.main_toolbar.append(self.page_label)

        self.next_button = Gtk.Button.new_from_icon_name("go-next-symbolic")
        self.next_button.set_tooltip_text("Next Page")
        self.next_button.connect("clicked", self.on_next_page)
        self.main_toolbar.append(self.next_button)

        self.main_toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=6, margin_end=6))

        # --- Text Formatting Controls (Initially disabled) ---
        self.font_store = Gtk.ListStore(str, str) # Display Name, PDF Font Name
        # Using PDF Base 14 fonts for simplicity/portability
        self.font_store.append(["Sans", "helv"])
        self.font_store.append(["Serif", "timr"])
        self.font_store.append(["Monospace", "cour"])
        # Could add more standard fonts if needed: 'symb', 'zapf'

        self.font_combo = Gtk.ComboBox(model=self.font_store)
        cell = Gtk.CellRendererText()
        self.font_combo.pack_start(cell, True)
        self.font_combo.add_attribute(cell, "text", 0) # Display name from column 0
        self.font_combo.set_active(0) # Default to Sans/Helvetica
        self.font_combo.set_tooltip_text("Font Family")
        self.font_combo.connect("changed", self.on_text_format_changed)
        self.main_toolbar.append(self.font_combo)

        self.font_size_spin = Gtk.SpinButton.new_with_range(6, 96, 1)
        self.font_size_spin.set_value(11) # Default font size
        self.font_size_spin.set_tooltip_text("Font Size")
        self.font_size_spin.connect("value-changed", self.on_text_format_changed)
        self.main_toolbar.append(self.font_size_spin)

        self.color_button = Gtk.ColorButton()
        # Set default color slightly off-black for visibility if needed
        # default_rgba = Gdk.RGBA()
        # default_rgba.parse("black")
        # self.color_button.set_rgba(default_rgba)
        self.color_button.set_tooltip_text("Font Color")
        self.color_button.connect("color-set", self.on_text_format_changed)
        self.main_toolbar.append(self.color_button)

    def _setup_controllers(self):
        # Drag and Drop Target
        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect('drop', self.on_drop)
        self.add_controller(drop_target) # Add controller to the window

        # Zooming with Ctrl+Scroll
        scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL # Only vertical scroll matters for zoom
        )
        scroll_controller.connect('scroll', self.on_scroll_zoom)
        self.pdf_view.add_controller(scroll_controller) # Attach to PDF view

        # Clicking on Text
        click_controller = Gtk.GestureClick.new()
        # Set to CAPTURE phase to potentially intercept before scrolling starts drag
        click_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click_controller.connect('pressed', self.on_pdf_view_pressed)
        click_controller.connect('released', self.on_pdf_view_released) # Handle double-click on release
        self.pdf_view.add_controller(click_controller)

        # Keyboard interaction (e.g., Esc to cancel editing)
        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect('key-pressed', self.on_key_pressed)
        self.add_controller(key_controller) # Add to window to catch keys globally


    def _connect_actions(self):
        # Window specific actions (prefixed with "win.")
        action_save_as = Gio.SimpleAction.new('save_as', None)
        action_save_as.connect('activate', self.on_save_as)
        self.add_action(action_save_as)

        action_export_as = Gio.SimpleAction.new('export_as', None)
        action_export_as.connect('activate', self.on_export_as)
        self.add_action(action_export_as)

        # action_prefs = Gio.SimpleAction.new('preferences', None)
        # action_prefs.connect('activate', self.on_preferences)
        # self.add_action(action_prefs)

    def _update_ui_state(self):
        """Enable/disable UI elements based on whether a document is loaded."""
        has_doc = self.doc is not None
        page_count = self.doc.page_count if has_doc else 0
        has_pages = page_count > 0
        can_go_prev = has_pages and self.current_page_index > 0
        can_go_next = has_pages and self.current_page_index < page_count - 1

        self.save_button.set_sensitive(has_doc)
        # Find the "Save As..." and "Export As..." actions in the menu model? No, just enable actions.
        save_as_action = self.lookup_action("save_as")
        if save_as_action:
            save_as_action.set_enabled(has_doc)

        export_as_action = self.lookup_action("export_as")
        if export_as_action:
            export_as_action.set_enabled(has_doc)

        self.prev_button.set_sensitive(can_go_prev)
        self.next_button.set_sensitive(can_go_next)

        # Text formatting controls enabled only when text is selected
        has_selected_text = self.selected_text is not None
        self.font_combo.set_sensitive(has_selected_text)
        self.font_size_spin.set_sensitive(has_selected_text)
        self.color_button.set_sensitive(has_selected_text)

        # Show/hide empty state label
        self.empty_label.set_visible(not has_doc)
        self.pdf_scroll.set_visible(has_doc) # Hide scrollview when no doc

        if has_doc:
             self.update_page_label()
        else:
            self.page_label.set_text("Page 0 of 0")
            self.zoom_label.set_text("100%")
            self.status_label.set_text("No document loaded.")


    def load_document(self, filepath):
        """Loads a PDF document from the given filepath."""
        if self.doc:
            self.close_document() # Close previous document first

        self.status_label.set_text(f"Loading {os.path.basename(filepath)}...")
        # Force UI update before potentially long operation
        # REMOVE THE FOLLOWING LINE (and the Gtk.main_iteration() if it was there)
        # while Gtk.events_pending():
        #     Gtk.main_iteration() # REMOVE THIS TOO

        try:
            self.doc = fitz.open(filepath)
            if self.doc.needs_pass:
                 self.show_error_dialog("Password protected PDFs are not supported yet.")
                 self.close_document()
                 return

            self.current_file_path = filepath
            self.current_page_index = 0
            self.set_title(f"Pardus PDF Editor - {os.path.basename(filepath)}")

            self.pages_model.remove_all() # Clear old thumbnails
            # Load thumbnails (can be slow, consider async)
            self._load_thumbnails() # This might still block if long

            self._load_page(self.current_page_index) # Load first page content

            self.status_label.set_text(f"Loaded: {os.path.basename(filepath)}")

        except Exception as e:
            self.show_error_dialog(f"Error opening PDF: {e}\nPath: {filepath}")
            self.close_document() # Ensure cleanup on error
        finally:
            self._update_ui_state()

    def _load_thumbnails(self):
        if not self.doc:
            return
        # Simple synchronous loading for now
        thumb_size = 128 # Max dimension for thumbnail
        for i in range(self.doc.page_count):
            page = self.doc.load_page(i)
            zoom_factor = min(thumb_size / page.rect.width, thumb_size / page.rect.height)
            matrix = fitz.Matrix(zoom_factor, zoom_factor)
            try:
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                gdk_pixbuf = GdkPixbuf.Pixbuf.new_from_data(
                    pix.samples, GdkPixbuf.Colorspace.RGB, False, 8,
                    pix.width, pix.height, pix.stride
                )
                pdf_page_obj = PdfPage(index=i, thumbnail=gdk_pixbuf)
                self.pages_model.append(pdf_page_obj)
            except Exception as thumb_error:
                 print(f"Warning: Could not generate thumbnail for page {i+1}: {thumb_error}")
                 placeholder_pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 80, 100)
                 placeholder_pixbuf.fill(0xaaaaaaFF) # Grey placeholder
                 pdf_page_obj = PdfPage(index=i, thumbnail=placeholder_pixbuf)
                 self.pages_model.append(pdf_page_obj)

            # REMOVE THE MANUAL UI UPDATE BLOCK BELOW
            # Update UI periodically during loading
            # if i % 10 == 0:
            #    while Gtk.events_pending():
            #        Gtk.main_iteration() # REMOVE THIS

    def _load_page(self, page_index):
        """Loads content for a specific page index."""
        if not self.doc or not (0 <= page_index < self.doc.page_count):
            return

        self.current_page_index = page_index
        self.selected_text = None # Deselect text when changing page
        self.hide_text_editor() # Hide editor if open

        # Extract text elements for editing
        self._extract_editable_text(page_index)

        # Update drawing area size and trigger redraw
        page = self.doc.load_page(page_index)
        width = int(page.rect.width * self.zoom_level)
        height = int(page.rect.height * self.zoom_level)
        self.pdf_view.set_content_width(width)
        self.pdf_view.set_content_height(height)
        self.pdf_view.queue_draw()

        self.update_page_label()
        self._update_ui_state()
        self._sync_thumbnail_selection()


    def close_document(self):
        """Closes the currently open document and resets the state."""
        if self.doc:
            try:
                self.doc.close()
            except Exception as e:
                print(f"Error closing PDF document: {e}")
        self.doc = None
        self.current_file_path = None
        self.current_page_index = 0
        self.editable_texts = []
        self.selected_text = None
        self.hide_text_editor()
        self.pages_model.remove_all()
        self.set_title("Pardus PDF Editor")
        self.pdf_view.set_content_width(1)
        self.pdf_view.set_content_height(1)
        self.pdf_view.queue_draw() # Clear the view
        self._update_ui_state()

    def save_document(self, save_path):
        """Saves the document to the specified path."""
        if not self.doc or self.is_saving:
            return
        self.is_saving = True
        self.status_label.set_text(f"Saving {os.path.basename(save_path)}...")
        while Gtk.events_pending(): Gtk.main_iteration()

        try:
            # Apply any pending changes from the editor BEFORE saving
            if self.text_edit_popover and self.text_edit_popover.is_visible():
                self._apply_and_hide_editor()

            # Apply changes stored in EditableText objects (if any were made outside editor)
            # Note: Current logic applies changes immediately via popover 'Done'.
            # If direct manipulation were added, changes would need applying here.

            # Clean page content streams before saving
            # This is CPU intensive, do it optionally or only if edits made?
            # For now, do it always on save to potentially fix coords.
            # Consider adding a flag self.document_modified
            # if self.document_modified: # Only clean if needed
            #    for page in self.doc:
            #        page.clean_contents()

            # Save the document
            # Options: garbage collection, deflate, incremental saving
            self.doc.save(save_path, garbage=4, deflate=True)
            self.current_file_path = save_path # Update current path if save successful
            self.set_title(f"Pardus PDF Editor - {os.path.basename(save_path)}") # Update title
            self.status_label.set_text(f"Document saved: {os.path.basename(save_path)}")
            # self.document_modified = False # Reset modified flag

        except Exception as e:
            self.show_error_dialog(f"Error saving PDF: {e}")
            self.status_label.set_text("Save failed.")
        finally:
            self.is_saving = False


    # --- Drawing ---

    def draw_pdf_page(self, area, cr, width, height):
        """Draw callback for the PDF view DrawingArea."""
        # Check if a valid document and page index exist
        if not self.doc or not (0 <= self.current_page_index < self.doc.page_count):
            # Draw a grey background if no document/page is loaded
            cr.set_source_rgb(0.7, 0.7, 0.7)
            cr.paint()
            # Clear any existing surface reference
            self.current_page_surface = None
            self.current_page_surface_data_ref = None
            return

        try:
            # Load the current page
            page = self.doc.load_page(self.current_page_index)
            # Create the zoom matrix for rendering
            zoom_matrix = fitz.Matrix(self.zoom_level, self.zoom_level)

            # Get the page pixmap at the current zoom level
            # Using alpha=False is generally faster if transparency isn't needed
            pix = page.get_pixmap(matrix=zoom_matrix, alpha=False)

            # Convert the PyMuPDF pixmap to a Cairo surface and get the data reference
            surface, data_ref = self.pixmap_to_cairo_surface(pix) # Get both values

            # --- Corrected Check: Ensure surface and data_ref are not None ---
            if surface is not None and data_ref is not None:
                 # *** IMPORTANT: Store references to keep data alive ***
                 self.current_page_surface = surface
                 self.current_page_surface_data_ref = data_ref

                 # Set the created surface as the source and paint it
                 cr.set_source_surface(self.current_page_surface, 0, 0)
                 cr.paint()
            else:
                 # Clear references if surface creation failed
                 self.current_page_surface = None
                 self.current_page_surface_data_ref = None

                 # Draw a fallback background indicating an error
                 cr.set_source_rgb(1.0, 0.8, 0.8) # Pinkish error background
                 cr.paint()
                 # Draw error text
                 cr.set_source_rgb(0,0,0)
                 cr.move_to(10, 20)
                 try:
                    # Use PangoCairo for potentially better text rendering if available
                    layout = PangoCairo.create_layout(cr)
                    layout.set_text("Error rendering page (surface creation failed)", -1)
                    font_desc = Pango.FontDescription("Sans 10")
                    layout.set_font_description(font_desc)
                    PangoCairo.show_layout(cr, layout)
                 except Exception: # Fallback to simple Cairo text
                    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
                    cr.set_font_size(12)
                    cr.show_text("Error rendering page (surface creation failed)")


            # --- Draw selection highlight for the selected text element ---
            if self.selected_text:
                cr.save() # Save current Cairo state
                # Set highlight color (semi-transparent blue)
                cr.set_source_rgba(0.2, 0.4, 1.0, 0.3)
                # Get bounding box of selected text
                x1, y1, x2, y2 = self.selected_text.bbox
                # Scale bounding box coordinates by zoom level for drawing
                rect_x = x1 * self.zoom_level
                rect_y = y1 * self.zoom_level
                rect_w = (x2 - x1) * self.zoom_level
                rect_h = (y2 - y1) * self.zoom_level
                # Draw the highlight rectangle
                cr.rectangle(rect_x, rect_y, rect_w, rect_h)
                cr.fill() # Fill the rectangle
                cr.restore() # Restore Cairo state

        except Exception as e:
            # Clear surface references on any drawing error
            self.current_page_surface = None
            self.current_page_surface_data_ref = None

            print(f"Error during drawing page {self.current_page_index}: {e}")
            # Draw a prominent error message directly on the canvas
            cr.set_source_rgb(1.0, 0.0, 0.0) # Red background
            cr.rectangle(0, 0, width, height)
            cr.fill()
            cr.set_source_rgb(1.0, 1.0, 1.0) # White text
            cr.move_to(10, 20)
            try:
                 # Use PangoCairo for potentially better text rendering
                 layout = PangoCairo.create_layout(cr)
                 layout.set_text(f"Error rendering page {self.current_page_index+1}:\n{e}", -1)
                 font_desc = Pango.FontDescription("Sans 10")
                 layout.set_font_description(font_desc)
                 PangoCairo.show_layout(cr, layout)
            except Exception: # Fallback to simple Cairo text
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
                cr.set_font_size(12)
                cr.show_text(f"Error rendering page {self.current_page_index+1}: {e}")

    def pixmap_to_cairo_surface(self, pix):
        """
        Converts a PyMuPDF Pixmap to a Cairo ImageSurface.
        Returns a tuple: (cairo.ImageSurface, data_reference) or (None, None) on error.
        The caller is responsible for holding the data_reference to keep the buffer alive.
        """
        data = None  # Will hold the mutable buffer passed to Cairo
        fmt = None   # Cairo format
        stride = 0 # Row stride in bytes
        data_ref = None # This will hold the Python object (bytearray or numpy array) owning the buffer

        try:
            if pix.alpha:
                # Handle RGBA pixmap
                if pix.n != 4:
                    print(f"Warning: Pixmap has alpha but {pix.n} components instead of 4.")
                    return None, None
                fmt = cairo.FORMAT_ARGB32 # Assumes BGRA byte order for Cairo on little-endian
                # Create a mutable bytearray copy for Cairo.
                # PyMuPDF RGBA samples might be in RGBA order. Cairo ARGB32 expects BGRA.
                # We might need to shuffle bytes if colors are wrong. Let's try direct first.
                # If colors are swapped (red/blue), uncomment and adapt the shuffling below.
                # samples_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, 4))
                # bgra_data = np.zeros((pix.height, pix.width, 4), dtype=np.uint8)
                # bgra_data[..., 0] = samples_np[..., 2] # B
                # bgra_data[..., 1] = samples_np[..., 1] # G
                # bgra_data[..., 2] = samples_np[..., 0] # R
                # bgra_data[..., 3] = samples_np[..., 3] # A
                # data = bytearray(bgra_data.tobytes()) # Use bytes from shuffled array
                data = bytearray(pix.samples) # Try direct first
                stride = pix.stride
                data_ref = data # Keep reference to the bytearray

            else:
                # Handle RGB pixmap -> Convert to ARGB32 for Cairo
                if pix.n != 3:
                    print(f"Warning: Pixmap is RGB but has {pix.n} components instead of 3.")
                    return None, None

                # Create a *writable* NumPy array for the ARGB data (BGRA order)
                rgba_data = np.zeros((pix.height, pix.width, 4), dtype=np.uint8)

                try:
                    # Create a view into the samples buffer first
                    rgb_view = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, 3))
                except ValueError as e:
                    # If reshape fails (e.g., non-contiguous stride), copy might be needed.
                    # This depends on how PyMuPDF allocates the sample buffer.
                    print(f"Warning: Could not directly reshape pix.samples ({e}), copying data.")
                    rgb_view = np.frombuffer(pix.samples, dtype=np.uint8).copy().reshape((pix.height, pix.width, 3))

                # Fill the ARGB data (BGRA order for Cairo's FORMAT_ARGB32)
                rgba_data[:, :, 0] = rgb_view[:, :, 2]  # Blue component
                rgba_data[:, :, 1] = rgb_view[:, :, 1]  # Green component
                rgba_data[:, :, 2] = rgb_view[:, :, 0]  # Red component
                rgba_data[:, :, 3] = 255                # Alpha component (fully opaque)

                data = rgba_data.data # Get the mutable buffer protocol object from the array
                fmt = cairo.FORMAT_ARGB32
                # Calculate the stride for the ARGB data we created (width * 4 bytes/pixel)
                stride = pix.width * 4
                data_ref = rgba_data # Keep reference to the numpy array owning the buffer

            if data is None:
                 print("Error: Data buffer for Cairo surface is None.")
                 return None, None

            # Create the Cairo surface using the prepared mutable data buffer
            surface = cairo.ImageSurface.create_for_data(
                data,
                fmt,
                pix.width,
                pix.height,
                stride
            )

            # Return BOTH the surface and the Python object holding the data buffer
            return surface, data_ref

        except Exception as e:
            # Print more details on error
            print(f"Error creating Cairo surface from pixmap: {e}")
            print(f"Pixmap details: width={pix.width}, height={pix.height}, alpha={pix.alpha}, n={pix.n}, stride={pix.stride}")
            return None, None

    # --- Text Editing Logic ---

    def _extract_editable_text(self, page_number):
        """Extracts text spans as EditableText objects for the given page."""
        self.editable_texts = []
        if not self.doc:
            return

        try:
            page = self.doc.load_page(page_number)
            # flags=11 -> get detailed text info including font, color, etc.
            text_dict = page.get_text("dict", flags=11)

            for block in text_dict.get("blocks", []):
                if block.get("type") == 0: # Text block
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            text = span.get("text", "").strip()
                            bbox = span.get("bbox")
                            if not text or not bbox:
                                continue # Skip empty spans or those without geometry

                            editable = EditableText(
                                x=bbox[0],
                                y=bbox[1],
                                text=text,
                                font_size=span.get("size", 11),
                                color=span.get("color", 0), # Let EditableText normalize
                                span_data=span # Pass the whole span dict
                            )
                            editable.page_number = page_number
                            self.editable_texts.append(editable)

        except Exception as e:
            print(f"Error extracting text from page {page_number}: {e}")
            self.show_error_dialog(f"Could not extract text structure from page {page_number + 1}.")


    def _find_text_at_pos(self, page_x, page_y):
        """Finds the EditableText object at the given page coordinates."""
        # Iterate in reverse order so topmost elements are checked first if overlapping
        for text_obj in reversed(self.editable_texts):
            x1, y1, x2, y2 = text_obj.bbox
            if x1 <= page_x <= x2 and y1 <= page_y <= y2:
                return text_obj
        return None

    def _update_text_format_controls(self, text_obj):
        """Updates toolbar controls to match the selected text object."""
        if not text_obj: return

        # Font Family
        active_font_index = 0 # Default to first font (Sans)
        # Try to find the font in our liststore
        font_name_to_find = text_obj.pdf_fontname # Use the mapped name
        for i, row in enumerate(self.font_store):
            if row[1] == font_name_to_find: # Check against PDF font name (column 1)
                 active_font_index = i
                 break
            # Fallback: check display name (less reliable)
            elif row[0].lower() in text_obj.font_family.lower():
                 active_font_index = i
                 # Don't break, keep looking for exact match if possible
        self.font_combo.set_active(active_font_index)

        # Font Size
        self.font_size_spin.set_value(text_obj.font_size)

        # Color
        rgba = Gdk.RGBA()
        rgba.red, rgba.green, rgba.blue = text_obj.color # Assumes color is normalized (0-1) tuple
        rgba.alpha = 1.0
        self.color_button.set_rgba(rgba)

    def _setup_text_editor(self, text_obj):
        """Creates and shows the popover text editor."""
        self.hide_text_editor() # Ensure any previous editor is hidden

        if not text_obj:
            return

        self.text_edit_popover = Gtk.Popover()
        self.text_edit_popover.set_autohide(False) # We control hiding
        self.text_edit_popover.set_has_arrow(True)
        self.text_edit_popover.set_position(Gtk.PositionType.BOTTOM)

        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        popover_box.add_css_class("box") # Add class for styling padding etc.
        self.text_edit_popover.set_child(popover_box)

        # Scrolled window for the text view
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(100) # Min height
        scroll.set_max_content_height(300) # Max height
        popover_box.append(scroll)

        # Text View
        self.text_edit_view = Gtk.TextView()
        self.text_edit_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text_edit_view.get_buffer().set_text(text_obj.text)
        self.text_edit_view.add_css_class("textview") # Style via CSS
        scroll.set_child(self.text_edit_view)

        # Apply current text format (approximate) to the TextView for visual consistency
        # This uses Pango attributes, not direct CSS for font size/family per instance easily
        # buffer = self.text_edit_view.get_buffer()
        # tag = buffer.create_tag("edit_style",
        #                         family=text_obj.font_family, # Pango needs family name
        #                         size_points=text_obj.font_size,
        #                         foreground_rgba=self.color_button.get_rgba())
        # start = buffer.get_start_iter()
        # end = buffer.get_end_iter()
        # buffer.apply_tag(tag, start, end)


        # Done Button
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.END)
        done_button = Gtk.Button(label="Done")
        done_button.add_css_class("suggested-action")
        done_button.connect("clicked", self.on_text_edit_done)
        button_box.append(done_button)
        popover_box.append(button_box)

        # Set the popover relative to the text bbox on the DrawingArea
        x1, y1, x2, y2 = text_obj.bbox
        widget_x = (x1 * self.zoom_level) # Convert PDF x to widget x
        widget_y = (y2 * self.zoom_level) # Anchor to bottom of text bbox

        rect = Gdk.Rectangle()
        rect.x = int(widget_x)
        rect.y = int(widget_y)
        rect.width = int((x2 - x1) * self.zoom_level)
        rect.height = 1 # Minimal height for anchor point

        self.text_edit_popover.set_parent(self.pdf_view) # Parent is the drawing area
        self.text_edit_popover.set_pointing_to(rect)
        self.text_edit_popover.popup()

        self.text_edit_view.grab_focus()


    def hide_text_editor(self):
        """Hides the text editor popover."""
        if self.text_edit_popover:
            self.text_edit_popover.popdown()
            self.text_edit_popover = None
            self.text_edit_view = None


    def _apply_text_changes(self, text_obj, new_text):
        """Applies the edited text back to the PDF page."""
        if not self.doc or not text_obj or text_obj.page_number is None:
            return False

        page = self.doc.load_page(text_obj.page_number)

        # --- This is the destructive part: Redact old, insert new ---
        try:
            # 1. Redact the original text area
            # Use a slightly larger rect for redaction to ensure full removal? Careful not to remove adjacent things.
            # Add a small margin, e.g., 1 point
            margin = 0.5
            redact_rect = fitz.Rect(text_obj.bbox) # Copy original bbox
            # redact_rect.x0 -= margin
            # redact_rect.y0 -= margin
            # redact_rect.x1 += margin
            # redact_rect.y1 += margin
            # Make sure rect stays within page bounds
            redact_rect.normalize()
            # redact_rect.intersect(page.rect) # Clip to page

            # Add redaction annotation (doesn't remove yet)
            # Fill color doesn't matter much as it will be removed
            annot = page.add_redact_annot(redact_rect, fill=(1,1,1))
            if not annot:
                 print("Warning: Could not add redaction annotation.")
                 # Don't proceed with insertion if redaction failed?

            # Apply redactions *now* to remove the content under the annotation
            # Applying redactions invalidates references to objects on the page,
            # so it's best done just before inserting new content or saving.
            # However, we need the space clear for insertion.
            apply_result = page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE) # Don't remove images
            if not apply_result:
                 print("Warning: Applying redactions failed.")
                 # Maybe try to remove the redaction annotation we added?

            # 2. Insert the new text
            # Use the bottom-left corner of the original bbox as insertion point.
            # x = text_obj.bbox[0]
            # y = text_obj.bbox[3] # Baseline
            # Use stored x and baseline
            insert_point = fitz.Point(text_obj.x, text_obj.baseline)

            # Get font properties from the EditableText object
            fontname = text_obj.pdf_fontname # Use the mapped short name
            fontsize = text_obj.font_size
            color = text_obj.color # Use the current (potentially edited) color

            # Handle multi-line text (split by '\n')
            lines = new_text.split('\n')
            # Estimate line height - this is an approximation!
            # A more accurate way involves font metrics, but that's complex.
            # Using fontsize directly often works okay for simple cases.
            line_height_factor = 1.1 # Adjust as needed
            line_height = fontsize * line_height_factor

            current_y = text_obj.baseline
            for i, line in enumerate(lines):
                 line_point = fitz.Point(text_obj.x, current_y)
                 # Try inserting with the specific font, fallback to base14
                 try:
                     rc = page.insert_text(
                          line_point,
                          line,
                          fontname=fontname,
                          fontsize=fontsize,
                          color=color,
                          # rotate=0, # etc.
                     )
                     if rc < 0: print(f"Warning: PyMuPDF insert_text returned error {rc} for line: {line}")
                 except Exception as insert_e:
                      print(f"Error inserting text line '{line}' with font '{fontname}': {insert_e}. Falling back.")
                      # Fallback to Helvetica ('helv')
                      try:
                           rc = page.insert_text(line_point, line, fontname="helv", fontsize=fontsize, color=color)
                           if rc < 0: print(f"Warning: PyMuPDF insert_text fallback failed with error {rc}")
                      except Exception as fallback_e:
                           print(f"ERROR: Text insertion failed completely for line '{line}': {fallback_e}")

                 # Move y position down for the next line
                 current_y += line_height

            # 3. Update the EditableText object
            text_obj.text = new_text
            text_obj.modified = True # Mark as modified (though change is now in PDF)
            # We SHOULD update the bbox here, but calculating the new bbox accurately
            # after insertion is very difficult without re-analyzing the page.
            # For now, keep the old bbox for selection, it might be inaccurate.

            # 4. Optionally clean contents (maybe better done only on save)
            # page.clean_contents()

            return True # Success

        except Exception as e:
            print(f"Error applying text changes: {e}")
            self.show_error_dialog(f"Failed to modify text on page {text_obj.page_number + 1}.\nError: {e}")
            # Should we try to revert? Very difficult. Best to reload page?
            # Reloading page will lose the change attempt.
            # self._load_page(text_obj.page_number) # Revert visual state
            return False # Failure

    # --- Event Handlers ---

    def on_drop(self, drop_target, value, x, y):
        """Handles file drops onto the window."""
        if isinstance(value, Gio.File):
            filepath = value.get_path()
            if filepath and filepath.lower().endswith('.pdf'):
                self.load_document(filepath)
                return True
        return False

    def on_open_clicked(self, button):
        """Handles the 'Open' button click."""
        dialog = Gtk.FileChooserDialog(
            title="Open PDF File",
            transient_for=self,
            modal=True,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            "_Cancel", Gtk.ResponseType.CANCEL,
            "_Open", Gtk.ResponseType.ACCEPT,
        )

        filter_pdf = Gtk.FileFilter()
        filter_pdf.set_name("PDF files (*.pdf)")
        filter_pdf.add_pattern("*.pdf")
        filter_pdf.add_mime_type("application/pdf")
        dialog.add_filter(filter_pdf)

        filter_all = Gtk.FileFilter()
        filter_all.set_name("All files")
        filter_all.add_pattern("*")
        dialog.add_filter(filter_all)

        dialog.connect("response", self.on_open_response)
        dialog.present()

    def on_open_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            if file:
                self.load_document(file.get_path())
        dialog.destroy()

    def on_save_clicked(self, button):
        """Handles the 'Save' button click."""
        if self.current_file_path:
            self.save_document(self.current_file_path)
        else:
            self.on_save_as(None, None) # Trigger Save As if no current file

    def on_save_as(self, action, param):
        """Handles the 'Save As...' action."""
        if not self.doc: return

        dialog = Gtk.FileChooserDialog(
            title="Save PDF As...",
            transient_for=self,
            modal=True,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            "_Cancel", Gtk.ResponseType.CANCEL,
            "_Save", Gtk.ResponseType.ACCEPT,
        )
        dialog.set_current_name(os.path.basename(self.current_file_path or "edited_document.pdf"))

        filter_pdf = Gtk.FileFilter()
        filter_pdf.set_name("PDF files (*.pdf)")
        filter_pdf.add_pattern("*.pdf")
        filter_pdf.add_mime_type("application/pdf")
        dialog.add_filter(filter_pdf)

        dialog.connect("response", self.on_save_as_response)
        dialog.present()

    def on_save_as_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            if file:
                path = file.get_path()
                if not path.lower().endswith('.pdf'):
                    path += '.pdf'
                self.save_document(path) # Save to the new path
        dialog.destroy()

    def on_export_as(self, action, param):
        """Handles the 'Export As...' action."""
        if not self.doc:
            self.show_error_dialog("No document loaded to export.")
            return

        dialog = Gtk.FileChooserDialog(
            title="Export As...",
            transient_for=self,
            modal=True,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            "_Cancel", Gtk.ResponseType.CANCEL,
            "_Export", Gtk.ResponseType.ACCEPT,
        )
        base_name = Path(self.current_file_path).stem if self.current_file_path else "document"
        dialog.set_current_name(base_name)

        # Define filters
        filters = {
            "PDF": ("PDF files (*.pdf)", "*.pdf", "application/pdf"),
            "DOCX": ("Word Document (*.docx)", "*.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            "TXT": ("Text File (*.txt)", "*.txt", "text/plain"),
        }

        for name, (pattern_name, pattern, mime) in filters.items():
            file_filter = Gtk.FileFilter()
            file_filter.set_name(f"{name} - {pattern_name}")
            file_filter.add_pattern(pattern)
            if mime: file_filter.add_mime_type(mime)
            dialog.add_filter(file_filter)

        dialog.connect("response", self.on_export_response)
        dialog.present()

    def on_export_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            file = dialog.get_file()
            chosen_filter = dialog.get_filter()
            if file and chosen_filter:
                path = file.get_path()
                filter_name = chosen_filter.get_name().split(" - ")[0] # Get "PDF", "DOCX", "TXT"

                if filter_name == "DOCX":
                    if not path.lower().endswith('.docx'): path += '.docx'
                    self.export_as_docx(path)
                elif filter_name == "TXT":
                    if not path.lower().endswith('.txt'): path += '.txt'
                    self.export_as_text(path)
                elif filter_name == "PDF":
                     if not path.lower().endswith('.pdf'): path += '.pdf'
                     # Exporting as PDF is just saving
                     self.save_document(path)
                     self.status_label.set_text(f"Document exported as PDF: {os.path.basename(path)}")
                else:
                    # Default to saving as PDF if filter is unknown
                     if not path.lower().endswith('.pdf'): path += '.pdf'
                     self.save_document(path)
                     self.status_label.set_text(f"Document exported as PDF: {os.path.basename(path)}")

        dialog.destroy()


    def export_as_docx(self, output_path):
        """Exports the current PDF to DOCX using LibreOffice."""
        if not shutil.which('libreoffice'):
            self.show_error_dialog("LibreOffice Not Found\n\nLibreOffice is required to export documents as DOCX. Please install it and ensure it's in your system's PATH.")
            self.status_label.set_text("Export failed: LibreOffice not found.")
            return

        self.status_label.set_text("Exporting to DOCX (using LibreOffice)...")
        while Gtk.events_pending(): Gtk.main_iteration()

        temp_pdf_path = None
        try:
            # Save the potentially modified PDF to a temporary file
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_pdf:
                temp_pdf_path = temp_pdf.name
                # Save current state (important if modified)
                self.doc.save(temp_pdf_path, garbage=4, deflate=True)

            output_dir = os.path.dirname(output_path)
            expected_output_name = Path(temp_pdf_path).stem + ".docx"
            expected_output_file = os.path.join(output_dir, expected_output_name)

            # Ensure expected output doesn't already exist from a previous run
            if os.path.exists(expected_output_file):
                 os.remove(expected_output_file)

            # Run LibreOffice conversion
            command = [
                'libreoffice', '--headless', '--convert-to', 'docx',
                '--outdir', output_dir,
                temp_pdf_path
            ]
            process = subprocess.run(command, capture_output=True, text=True, check=False) # check=False to inspect errors

            if process.returncode != 0:
                 error_msg = f"LibreOffice conversion failed (code {process.returncode}).\n\nError:\n{process.stderr or process.stdout}"
                 print(error_msg)
                 self.show_error_dialog(error_msg)
                 self.status_label.set_text("Export failed: LibreOffice error.")
                 return # Stop here if conversion failed

            # Check if the expected output file was created
            if os.path.exists(expected_output_file):
                 # Rename the converted file to the desired output path
                 shutil.move(expected_output_file, output_path)
                 self.status_label.set_text(f"Document exported as DOCX: {os.path.basename(output_path)}")
            else:
                 # This shouldn't happen if returncode was 0, but check anyway
                 error_msg = "LibreOffice conversion seemed to succeed, but the output DOCX file was not found."
                 print(error_msg)
                 print(f"Expected file: {expected_output_file}")
                 self.show_error_dialog(error_msg)
                 self.status_label.set_text("Export failed: Output file missing.")

        except subprocess.CalledProcessError as e:
             self.show_error_dialog(f"Error running LibreOffice for DOCX conversion: {e}\n{e.stderr}")
             self.status_label.set_text("Export failed: LibreOffice execution error.")
        except Exception as e:
             self.show_error_dialog(f"Error exporting as DOCX: {e}")
             self.status_label.set_text("Export failed: Unexpected error.")
        finally:
             # Clean up the temporary PDF file
             if temp_pdf_path and os.path.exists(temp_pdf_path):
                 try:
                     os.unlink(temp_pdf_path)
                 except Exception as unlink_e:
                     print(f"Warning: Could not delete temporary file {temp_pdf_path}: {unlink_e}")


    def export_as_text(self, output_path):
        """Exports the document content as a single text file."""
        if not self.doc: return
        self.status_label.set_text("Exporting as text...")
        while Gtk.events_pending(): Gtk.main_iteration()

        try:
            with open(output_path, 'w', encoding='utf-8') as txt_file:
                for page_num in range(self.doc.page_count):
                    page = self.doc.load_page(page_num)
                    # Use "text" format for simpler extraction
                    text = page.get_text("text", sort=True) # sort=True tries to maintain reading order
                    txt_file.write(f"--- Page {page_num + 1} ---\n\n")
                    txt_file.write(text)
                    txt_file.write("\n\n") # Separator between pages
            self.status_label.set_text(f"Document exported as text: {os.path.basename(output_path)}")
        except Exception as e:
            self.show_error_dialog(f"Error exporting as text: {e}")
            self.status_label.set_text("Export failed: Could not write text file.")

    def on_zoom_in(self, button=None):
        """Increases zoom level."""
        if not self.doc: return
        old_zoom = self.zoom_level
        self.zoom_level = min(8.0, self.zoom_level * 1.2) # Increase max zoom
        if abs(old_zoom - self.zoom_level) > 0.01:
            self.zoom_label.set_text(f"{int(self.zoom_level * 100)}%")
            self._load_page(self.current_page_index) # Reload page at new zoom

    def on_zoom_out(self, button=None):
        """Decreases zoom level."""
        if not self.doc: return
        old_zoom = self.zoom_level
        self.zoom_level = max(0.1, self.zoom_level / 1.2) # Decrease min zoom
        if abs(old_zoom - self.zoom_level) > 0.01:
            self.zoom_label.set_text(f"{int(self.zoom_level * 100)}%")
            self._load_page(self.current_page_index) # Reload page at new zoom

    def on_scroll_zoom(self, controller, dx, dy):
        """Handles Ctrl+Scroll wheel zooming."""
        # Check if Ctrl key is pressed
        if controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            if dy < 0: # Scroll up
                self.on_zoom_in()
            elif dy > 0: # Scroll down
                self.on_zoom_out()
            return True # Event handled
        return False # Event not handled (allows normal scrolling)

    def on_prev_page(self, button):
        """Navigates to the previous page."""
        if self.doc and self.current_page_index > 0:
            self._load_page(self.current_page_index - 1)

    def on_next_page(self, button):
        """Navigates to the next page."""
        if self.doc and self.current_page_index < self.doc.page_count - 1:
            self._load_page(self.current_page_index + 1)

    def update_page_label(self):
        """Updates the page navigation label."""
        if self.doc:
            self.page_label.set_text(f"Page {self.current_page_index + 1} of {self.doc.page_count}")
        else:
            self.page_label.set_text("Page 0 of 0")

    def on_thumbnail_selected(self, selection_model, position, n_items):
         """Handles clicking on a thumbnail in the sidebar."""
         selected_index = selection_model.get_selected()
         if selected_index != Gtk.INVALID_LIST_POSITION and selected_index != self.current_page_index:
              # Prevent recursive call if _load_page triggers selection change
              if hasattr(self, '_syncing_thumb') and self._syncing_thumb:
                   return
              print(f"Thumbnail selected: {selected_index}")
              self._load_page(selected_index)

    def _sync_thumbnail_selection(self):
         """Updates the selected thumbnail to match the current page."""
         if not self.doc or not self.thumbnail_selection_model:
              return
         # Prevent triggering on_thumbnail_selected during sync
         self._syncing_thumb = True
         self.thumbnail_selection_model.set_selected(self.current_page_index)
         # Scroll the selected thumbnail into view
         item = self.pages_model.get_item(self.current_page_index)
         if item and isinstance(self.thumbnails_list, Gtk.Scrollable): # GridView might not be directly scrollable container needs it
              pass # TODO: Need reliable way to scroll GridView item into view
         self._syncing_thumb = False


    def on_pdf_view_pressed(self, gesture, n_press, x, y):
        """Handles single/double clicks on the PDF view."""
        if not self.doc: return

        # Convert widget coordinates (x, y) to page coordinates
        page_x = x / self.zoom_level
        page_y = y / self.zoom_level

        clicked_text = self._find_text_at_pos(page_x, page_y)

        if clicked_text:
            # If already selected and clicked again (n_press > 1) -> start editing
            if clicked_text == self.selected_text and n_press > 1:
                 print("Double-click detected, starting edit.")
                 self._setup_text_editor(clicked_text)
                 # gesture.set_state(Gtk.EventSequenceState.CLAIMED) # Claim event
            # Single click on a text element
            elif self.selected_text != clicked_text:
                self.selected_text = clicked_text
                print(f"Selected text: '{self.selected_text.text[:20]}...'")
                self.hide_text_editor() # Hide editor if selecting different text
                self.pdf_view.queue_draw() # Redraw to show selection highlight
                self._update_text_format_controls(self.selected_text)
                self._update_ui_state() # Update toolbar sensitivity
                # gesture.set_state(Gtk.EventSequenceState.CLAIMED) # Claim event
            # Else: Clicked same selected text once, do nothing special
        else:
            # Clicked outside any known text element
            if self.selected_text:
                 # Apply changes if editor was open, otherwise just deselect
                 if self.text_edit_popover and self.text_edit_popover.is_visible():
                      print("Clicked outside editor, applying changes.")
                      self._apply_and_hide_editor() # Apply changes from editor
                 else:
                      print("Deselecting text.")
                      self.selected_text = None
                      self.hide_text_editor()
                      self.pdf_view.queue_draw() # Remove selection highlight
                      self._update_ui_state() # Update toolbar sensitivity


    def on_pdf_view_released(self, gesture, n_press, x, y):
        """Handle release, mainly for confirming double-click action."""
        # The double-click logic is now primarily in on_pdf_view_pressed checking n_press > 1
        # because 'pressed' gives the count immediately. 'released' confirms the gesture end.
        pass

    def on_text_format_changed(self, widget, *args):
        """Handles changes in font, size, or color controls."""
        if not self.selected_text or (self.text_edit_popover and self.text_edit_popover.is_visible()):
             # Don't apply format changes while the editor popover is active,
             # wait until 'Done' is clicked. Or apply live? Let's wait.
             # Also don't apply if nothing is selected.
             return

        # Update the selected EditableText object's properties
        # Font
        iter = self.font_combo.get_active_iter()
        if iter:
            display_name = self.font_store[iter][0]
            pdf_name = self.font_store[iter][1]
            # Update both representations if needed
            self.selected_text.font_family = display_name # Or store the raw name?
            self.selected_text.pdf_fontname = pdf_name

        # Size
        self.selected_text.font_size = self.font_size_spin.get_value()

        # Color
        rgba = self.color_button.get_rgba()
        self.selected_text.color = (rgba.red, rgba.green, rgba.blue)

        self.selected_text.modified = True # Mark as needing potential update later?
        # Immediate apply? No, current logic applies on editor 'Done'.
        # If we want live formatting without editor, we need apply_text_changes here.
        print(f"Text format changed for selection: {self.selected_text.font_family}, {self.selected_text.font_size:.1f}, {self.selected_text.color}")

        # Maybe redraw the text immediately if we implement direct drawing?
        # For now, the change is stored but only applied when editor is used.


    def on_text_edit_done(self, button):
        """Handles clicking 'Done' in the text editor popover."""
        self._apply_and_hide_editor()

    def _apply_and_hide_editor(self):
        """Reads text from editor, applies changes to PDF, hides editor."""
        if not self.text_edit_view or not self.selected_text:
             self.hide_text_editor()
             return

        buffer = self.text_edit_view.get_buffer()
        start = buffer.get_start_iter()
        end = buffer.get_end_iter()
        new_text = buffer.get_text(start, end, True) # Get text from TextView

        # Apply the changes to the PDF
        success = self._apply_text_changes(self.selected_text, new_text)

        if success:
             # Reload page to show the result of redaction/insertion
             # This also updates the self.editable_texts list
             current_scroll_adj_v = self.pdf_scroll.get_vadjustment().get_value()
             current_scroll_adj_h = self.pdf_scroll.get_hadjustment().get_value()
             self._load_page(self.current_page_index) # Reloads text objects, redraws
             # Restore scroll position
             GLib.idle_add(lambda: self.pdf_scroll.get_vadjustment().set_value(current_scroll_adj_v))
             GLib.idle_add(lambda: self.pdf_scroll.get_hadjustment().set_value(current_scroll_adj_h))

             # Keep text selected after successful edit? Maybe deselect.
             # self.selected_text = None # Deselect after edit
             # self._update_ui_state()
        # else: # Error already shown by _apply_text_changes

        self.hide_text_editor()


    def on_key_pressed(self, controller, keyval, keycode, state):
        """Handles global key presses."""
        # Escape key handling
        if keyval == Gdk.KEY_Escape:
            if self.text_edit_popover and self.text_edit_popover.is_visible():
                 print("Escape pressed, cancelling edit.")
                 # Just hide the editor without applying changes
                 self.hide_text_editor()
                 # Re-select the text to show original state/highlight
                 if self.selected_text:
                     self._update_text_format_controls(self.selected_text) # Reset controls too?
                     self.pdf_view.queue_draw()
                 return True # Event handled
            elif self.selected_text:
                 print("Escape pressed, deselecting text.")
                 self.selected_text = None
                 self.pdf_view.queue_draw()
                 self._update_ui_state()
                 return True # Event handled

        # Delete key handling
        elif keyval == Gdk.KEY_Delete:
             if self.selected_text and not (self.text_edit_popover and self.text_edit_popover.is_visible()):
                  print("Delete key pressed on selected text.")
                  # Implement deletion by applying empty text change
                  confirm = self.show_confirm_dialog(f"Delete the selected text?\n'{self.selected_text.text[:50]}...'")
                  if confirm:
                       success = self._apply_text_changes(self.selected_text, "") # Apply empty string
                       if success:
                            # Reload page to reflect deletion
                            self._load_page(self.current_page_index)
                  return True # Event handled

        return False # Event not handled

    # --- Dialogs ---

    def show_error_dialog(self, message):
        """Displays an error message dialog."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            modal=True,
            heading="Error",
            body=message,
        )
        dialog.add_response("ok", "_Close")
        dialog.set_default_response("ok")
        dialog.connect("response", lambda d, r: d.close())
        dialog.present()

    def show_confirm_dialog(self, message, title="Confirm"):
        """Displays a confirmation dialog and returns True if confirmed."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            modal=True,
            heading=title,
            body=message,
        )
        dialog.add_response("cancel", "_Cancel")
        dialog.add_response("confirm", "_Confirm")
        dialog.set_default_response("cancel")
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE) # Red confirm button

        response = dialog.run() # Using run() for synchronous response
        dialog.destroy()
        return response == "confirm"


    # --- Placeholder Tool Handlers ---
    # def on_add_text_tool_activate(self, button):
    #     self.show_error_dialog("Add Text tool not implemented yet.")

    # def on_add_image(self, button):
    #      self.show_error_dialog("Add Image tool not implemented yet.")

    # def on_add_signature(self, button):
    #      self.show_error_dialog("Add Signature tool not implemented yet.")

    # def on_highlight(self, button):
    #     self.show_error_dialog("Highlight tool not implemented yet.")

    # def on_add_comment(self, button):
    #      self.show_error_dialog("Add Comment tool not implemented yet.")


# --- Application Class ---

class PdfEditorApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id='org.pardus.pdfeditor',
                         flags=Gio.ApplicationFlags.HANDLES_OPEN) # Handle file opening
        self.window = None

        # Define application actions (like quit)
        quit_action = Gio.SimpleAction.new('quit', None)
        quit_action.connect('activate', self.on_quit)
        self.add_action(quit_action)
        self.set_accels_for_action('app.quit', ['<Control>q'])

    def do_activate(self):
        """Activates the application (e.g., when run without files)."""
        if not self.window:
            self.window = PdfEditorWindow(application=self)
        self.window.present()

    def do_open(self, files, n_files, hint):
        """Handles opening files passed via command line or D-Bus."""
        if not self.window:
            # Need to create window first if activating via file open
            self.window = PdfEditorWindow(application=self)

        if n_files > 0:
            # Load the first file provided
            filepath = files[0].get_path()
            if filepath:
                # Defer loading until main loop is running and window is shown
                GLib.idle_add(self.window.load_document, filepath)

        self.window.present()


    def on_quit(self, action, param):
        """Handles the quit action."""
        if self.window:
             # Check for unsaved changes maybe?
             self.window.close_document() # Clean up document handle
        self.quit() # Quits the application


# --- Main Entry Point ---

def main():
    # Initialize Adwaita styles
    Adw.init()
    app = PdfEditorApplication()
    return app.run(sys.argv)

if __name__ == '__main__':
    sys.exit(main())