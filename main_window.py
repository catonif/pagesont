"""
Main window for the Pagesont PAGE XML editor.

Houses:
  - QTreeView showing the region/line hierarchy
  - PageView for the image + annotation overlays
  - PropertiesPanel with an editable form (segmentation) or proofread view (text)

Wires signals between the view, tree, and panel to keep them in sync.
"""

import difflib
import unicodedata
import uuid
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, QEvent
from PyQt6.QtGui import QAction, QIcon, QTextCharFormat, QColor, QGuiApplication
from PyQt6.QtWidgets import (
    QMainWindow, QSplitter, QTreeView, QWidget, QVBoxLayout,
    QFormLayout, QLabel, QLineEdit, QTextEdit, QPushButton,
    QFileDialog, QMessageBox, QHBoxLayout,
    QStackedWidget, QScrollArea,
)
from PyQt6.QtGui import QStandardItemModel, QStandardItem

from page_model import PageDocument, PageRegion, PageTextLine
from page_view import PageView
from page_model import format_points, parse_points, clean_points


# Custom data role for storing model objects in QStandardItem
DATA_ROLE = Qt.ItemDataRole.UserRole + 1

# Text-format styles for diff highlighting in the proofread view
DIFF_INSERT = QTextCharFormat()
DIFF_INSERT.setBackground(QColor("#c8e6c9"))   # green
DIFF_DELETE = QTextCharFormat()
DIFF_DELETE.setBackground(QColor("#ffcdd2"))   # red
DIFF_REPLACE = QTextCharFormat()
DIFF_REPLACE.setBackground(QColor("#ffe0b2"))  # orange

# Directory with all the icons.
ICON_DIR = Path(__file__).resolve().parent / "icons"

def create_button(func, text, icon, stylesheet=False):
    btn = QPushButton(icon, text)
    if stylesheet:
        btn.setStyleSheet(stylesheet)
    btn.clicked.connect(func)
    return btn

BTN_ICON_SIMPLIFY = QIcon(str(ICON_DIR / "circles_ext.svg"))
BTN_TEXT_SIMPLIFY = "Simplify polygon"
BTN_ICON_NEWLINE = QIcon(str(ICON_DIR / "variable_add.svg"))
BTN_TEXT_NEWLINE = "New line"
BTN_ICON_MERGELINES = QIcon(str(ICON_DIR / "cell_merge.svg"))
BTN_TEXT_MERGELINES = "Merge with next line"
BTN_ICON_MOVEUP = QIcon(str(ICON_DIR / "arrow_upward.svg"))
BTN_TEXT_MOVEUP = "Move up in tree"
BTN_ICON_MOVEDOWN = QIcon(str(ICON_DIR / "arrow_downward.svg"))
BTN_TEXT_MOVEDOWN = "Move down in tree"
BTN_ICON_DELETELINE = QIcon(str(ICON_DIR / "delete_forever.svg"))
BTN_TEXT_DELETELINE = "Delete line"

# ======================================================================
# PropertiesPanel — right-side sidebar
# ======================================================================

class PropertiesPanel(QWidget):
    """
    Shows two stacked pages:
      - A form for editing a selected region or line (segmentation mode)
      - A proofread view listing all lines with OCR text + corrected-text fields (text mode)

    Signals
    -------
    changed(obj)                   — emitted when a property is edited
    proofread_focus_changed(line)  — emitted when cursor moves in a corrected-text field
    merge_line_requested(line)
    move_line_requested(line, direction)
    delete_line_requested(line)
    new_line_requested(region)
    """

    changed = pyqtSignal(object)
    proofread_focus_changed = pyqtSignal(object)
    merge_line_requested = pyqtSignal(object)
    move_line_requested = pyqtSignal(object, str)
    delete_line_requested = pyqtSignal(object)
    new_line_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data_obj = None
        self._proofread_widgets = {}   # line.id -> {'ocr': QTextEdit, 'corr': QTextEdit}
        self._proofread_list = []      # ordered list of line entries

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        self._build_form_page()

        self._proofread_page = None  # lazily created

    # ---- Form page (segmentation mode) ----------------------------------

    def _build_form_page(self):
        """Build the editable form shown when a region or line is selected."""
        page = QWidget()
        inner = QVBoxLayout(page)
        inner.setContentsMargins(0, 0, 0, 0)

        self.info_label = QLabel("Select an item")
        self.info_label.setWordWrap(True)
        inner.addWidget(self.info_label)

        self.form = QFormLayout()
        inner.addLayout(self.form)

        inner.addStretch()

        self.buttons_layout = QVBoxLayout()
        inner.addLayout(self.buttons_layout)

        self.stack.addWidget(page)

    # ---- Proofread page (text mode) -------------------------------------

    def _ensure_proofread_page(self):
        """Create the proofread scroll-area page on first use."""
        if self._proofread_page:
            return
        self._proofread_page = QWidget()
        layout = QVBoxLayout(self._proofread_page)
        layout.setContentsMargins(0, 0, 0, 0)

        self._proofread_scroll = QScrollArea()
        self._proofread_scroll.setWidgetResizable(True)
        layout.addWidget(self._proofread_scroll)

        self.stack.addWidget(self._proofread_page)

    def show_proofread(self, lines):
        """
        Populate the proofread view with all lines.

        Each line gets a read-only OCR text box and an editable corrected-text
        box below it.  Diff highlighting shows changes in real time.
        """
        self._ensure_proofread_page()
        self._data_obj = None
        self._proofread_widgets = {}
        self._proofread_list = []

        content = QWidget()
        vbox = QVBoxLayout(content)
        vbox.setSpacing(0)

        for i, line in enumerate(lines):
            # --- OCR text (read-only) ---
            ocr_edit = QTextEdit()
            ocr_edit.setPlainText(line.text)
            ocr_edit.setReadOnly(True)
            ocr_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            ocr_edit.setStyleSheet(
                "QTextEdit { background-color: #eee; border: 0; padding: 2px; font-family: monospace; }"
            )
            ocr_lines = line.text.count('\n') + 1
            ocr_edit.setFixedHeight(max(ocr_lines * 18 + 6, 28))
            vbox.addWidget(ocr_edit)

            # --- Corrected text (editable) ---
            corr_edit = QTextEdit()
            corr_edit.setPlaceholderText("Type corrected text here...")
            corr_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            corr_edit.setFixedHeight(28)
            corr_edit.setStyleSheet(
                "QTextEdit { background-color: #fff; border: 0; padding: 2px; font-family: monospace; }"
            )
            vbox.addWidget(corr_edit)

            self._proofread_widgets[line.id] = {
                'ocr': ocr_edit,
                'corr': corr_edit,
            }
            self._proofread_list.append({
                'line': line,
                'ocr': ocr_edit,
                'corr': corr_edit,
            })
            corr_edit.installEventFilter(self)

            # Wire up diff-highlighting and height auto-adjust
            corr_edit.textChanged.connect(lambda o=ocr_edit, c=corr_edit: self._highlight_diff(o, c))
            corr_edit.textChanged.connect(lambda e=corr_edit: self._adjust_edit_height(e))
            corr_edit.cursorPositionChanged.connect(lambda l=line: self.proofread_focus_changed.emit(l))

        vbox.addStretch()

        if self._proofread_scroll.widget():
            old = self._proofread_scroll.widget()
            old.deleteLater()
        self._proofread_scroll.setWidget(content)
        self.stack.setCurrentWidget(self._proofread_page)

    def hide_proofread(self):
        self.stack.setCurrentIndex(0)

    def scroll_to_line(self, line_id):
        w = self._proofread_widgets.get(line_id)
        if w and self._proofread_scroll:
            self._proofread_scroll.ensureWidgetVisible(w['ocr'])

    def focus_line(self, line_id):
        w = self._proofread_widgets.get(line_id)
        if w:
            w['corr'].setFocus()

    def save_proofread_texts(self, model):
        """
        Copy corrected text from the proofread widgets back into the model's
        line.text fields.
        """
        for region in model.regions:
            for line in region.lines:
                w = self._proofread_widgets.get(line.id)
                if w:
                    text = w['corr'].toPlainText()
                    if text:
                        line.text = text

    # ---- Diff highlighting -------------------------------------------------

    def _highlight_diff(self, ocr_edit, corr_edit):
        """Apply coloured backgrounds to show insert/delete/replace diffs."""
        s = difflib.SequenceMatcher(None, ocr_edit.toPlainText(), corr_edit.toPlainText())

        ocr_selections = []
        corr_selections = []

        def highlight_selection(textedit, selections, format, start, end):
            if start >= end:
                return
            selection = QTextEdit.ExtraSelection()
            selection.format = format
            cursor = textedit.textCursor()
            cursor.setPosition(start)
            cursor.setPosition(end, cursor.MoveMode.KeepAnchor)
            selection.cursor = cursor
            selections.append(selection)

        for tag, ocr_start, ocr_end, corr_start, corr_end in s.get_opcodes():
            if tag == 'equal':
                continue
            if tag == 'delete':
                highlight_selection(ocr_edit, ocr_selections, DIFF_DELETE, ocr_start, ocr_end)
            elif tag == 'insert':
                highlight_selection(corr_edit, corr_selections, DIFF_INSERT, corr_start, corr_end)
            elif tag == 'replace':
                d = min(ocr_end - ocr_start, corr_end - corr_start)
                highlight_selection(ocr_edit, ocr_selections, DIFF_REPLACE, ocr_start, ocr_start + d)
                highlight_selection(corr_edit, corr_selections, DIFF_REPLACE, corr_start, corr_start + d)
                highlight_selection(ocr_edit, ocr_selections, DIFF_DELETE, ocr_start + d, ocr_end)
                highlight_selection(corr_edit, corr_selections, DIFF_INSERT, corr_start + d, corr_end)

        ocr_edit.setExtraSelections(ocr_selections)
        corr_edit.setExtraSelections(corr_selections)

    def _adjust_edit_height(self, edit):
        """Auto-grow the corrected-text edit to fit its content."""
        doc = edit.document()
        doc.setTextWidth(edit.viewport().width())
        h = int(doc.size().height()) + edit.frameWidth()
        edit.setFixedHeight(max(h, 28))

    # ---- Enter-key navigation in proofread mode ---------------------------

    def eventFilter(self, obj, event):
        """
        Enter (without Shift) in a corrected-text field moves focus to the
        next line's corrected field.  Shift+Enter inserts a newline.
        """
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                move_amount = 0
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Down):
                    move_amount = 1
                elif key == Qt.Key.Key_Up:
                    move_amount = -1
                if move_amount != 0:
                    for i, entry in enumerate(self._proofread_list):
                        if entry['corr'] is obj:
                            if 0 <= i + move_amount < len(self._proofread_list):
                                self._proofread_list[i + move_amount]['corr'].setFocus()
                            return True
        return super().eventFilter(obj, event)

    # ---- Form helpers ------------------------------------------------------

    def _add_row(self, label, widget):
        self.form.addRow(label, widget)
        return widget

    def _add_text_row(self, label, value, slot):
        edit = QLineEdit(value)
        edit.editingFinished.connect(slot)
        self._add_row(label, edit)
        return edit

    def _clear(self):
        """Remove all widgets from the form and buttons layout."""
        self._data_obj = None
        self.info_label.setText("Select an item")
        while self.form.count():
            item = self.form.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        while self.buttons_layout.count():
            item = self.buttons_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ---- Show region form --------------------------------------------------

    def show_region(self, region):
        self._clear()
        self._data_obj = region
        self.info_label.setText(f"<b>TextRegion</b> [{region.id}]")

        type_edit = self._add_text_row("Type:", region.type, lambda: self._set_region_type(region, type_edit.text()))
        self._add_row("Coords:", QLabel(f"{len(region.coords)} points"))
        self._add_row("Lines:", QLabel(str(len(region.lines))))
        text_edit = self._add_text_row("Text:", region.text, lambda: self._set_region_text(region, text_edit.text()))

        self.buttons_layout.addWidget(create_button(
            lambda: self._clean_coords(region.coords),
            BTN_TEXT_SIMPLIFY,
            BTN_ICON_SIMPLIFY
        ))

        self.buttons_layout.addWidget(create_button(
            lambda: self.new_line_requested.emit(region),
            BTN_TEXT_NEWLINE,
            BTN_ICON_NEWLINE
        ))

    # ---- Show line form ----------------------------------------------------

    def show_line(self, line, region=None):
        self._clear()
        self._data_obj = line
        self.info_label.setText(f"<b>TextLine</b> [{line.id}]")

        coords_edit = self._add_text_row("Coords:", format_points(line.coords), lambda: self._set_line_coords(line, coords_edit.text()))
        baseline_edit = self._add_text_row("Baseline:", format_points(line.baseline), lambda: self._set_line_baseline(line, baseline_edit.text()))
        text_edit = self._add_text_row("Text:", line.text, lambda: self._set_line_text(line, text_edit.text()))

        self.buttons_layout.addWidget(create_button(
            lambda: self._clean_coords(line.coords),
            BTN_TEXT_SIMPLIFY,
            BTN_ICON_SIMPLIFY
        ))

        self.buttons_layout.addWidget(create_button(
            lambda: self.merge_line_requested.emit(line),
            BTN_TEXT_MERGELINES,
            BTN_ICON_MERGELINES
        ))

        move_widget = QWidget()
        move_layout = QHBoxLayout(move_widget)
        move_layout.setContentsMargins(0, 0, 0, 0)
        move_layout.addWidget(create_button(
            lambda: self.move_line_requested.emit(line, "up"),
            BTN_TEXT_MOVEUP,
            BTN_ICON_MOVEUP
        ))
        move_layout.addWidget(create_button(
            lambda: self.move_line_requested.emit(line, "down"),
            BTN_TEXT_MOVEDOWN,
            BTN_ICON_MOVEDOWN
        ))
        self.buttons_layout.addWidget(move_widget)

        self.buttons_layout.addWidget(create_button(
            lambda: self.delete_line_requested.emit(line),
            BTN_TEXT_DELETELINE,
            BTN_ICON_DELETELINE
        ))

        if region is not None:
            self.buttons_layout.addWidget(create_button(
                lambda: self.new_line_requested.emit(region),
                BTN_TEXT_NEWLINE,
                BTN_ICON_NEWLINE
            ))

    def show_nothing(self):
        self.stack.setCurrentIndex(0)
        self._clear()

    # ---- Property setters (called from form editing) -----------------------

    def _set_region_type(self, region, value):
        region.type = value
        self.changed.emit(region)

    def _set_region_text(self, region, value):
        region.text = value
        self.changed.emit(region)

    def _set_line_coords(self, line, value):
        try:
            line.coords = parse_points(value)
            self.changed.emit(line)
        except Exception:
            pass

    def _set_line_baseline(self, line, value):
        try:
            line.baseline = parse_points(value)
            self.changed.emit(line)
        except Exception:
            pass

    def _set_line_text(self, line, value):
        line.text = value
        self.changed.emit(line)

    def _clean_coords(self, pts):
        cleaned = clean_points(pts)
        if len(cleaned) != len(pts):
            pts[:] = cleaned
            self.changed.emit(self._data_obj)



# ======================================================================
# MainWindow — orchestrates everything
# ======================================================================

class MainWindow(QMainWindow):
    def __init__(self, mode="segmentation"):
        super().__init__()
        self._mode = mode
        self.setWindowTitle("Pagesont — " + ("Fix Segmentation" if mode == "segmentation" else "Check Text"))
        self.resize(1400, 900)

        self.doc = PageDocument()
        self._current_obj = None

        self._build_menu()
        self._build_ui()

    @property
    def mode(self):
        return self._mode

    # ---- Menu bar ----------------------------------------------------------

    def _build_menu(self):
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")

        open_action = QAction("&Open XML...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)

        save_action = QAction("&Save", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_file)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save &As...", self)
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self.save_as_file)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()

        export_action = QAction("Export as Plain &Text...", self)
        export_action.setShortcut("Ctrl+T")
        export_action.triggered.connect(self.export_plain_text)
        file_menu.addAction(export_action)

        copy_action = QAction("&Copy as Plain Text", self)
        copy_action.setShortcut("Ctrl+Shift+T")
        copy_action.triggered.connect(self.copy_plain_text)
        file_menu.addAction(copy_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    # ---- UI layout ---------------------------------------------------------

    def _build_ui(self):
        """Three-panel layout: tree | image | properties."""
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: tree view
        self.tree_view = QTreeView()
        self.tree_model = QStandardItemModel()
        self.tree_model.setHorizontalHeaderLabels(["Page XML"])
        self.tree_view.setModel(self.tree_model)
        self.tree_view.setHeaderHidden(False)
        self.tree_view.selectionModel().selectionChanged.connect(self._on_tree_selection)
        splitter.addWidget(self.tree_view)

        # Centre: page view
        self.page_view = PageView()
        self.page_view.selection_changed.connect(self._on_view_selection)
        self.page_view.handle_released.connect(self._on_property_changed)
        splitter.addWidget(self.page_view)

        # Right: properties panel
        self.properties = PropertiesPanel()
        self.properties.changed.connect(self._on_property_changed)
        self.properties.proofread_focus_changed.connect(self._on_proofread_focus)
        self.properties.merge_line_requested.connect(self._on_merge_line)
        self.properties.move_line_requested.connect(lambda line, direction: self._on_move_line(line, direction))
        self.properties.delete_line_requested.connect(self._on_delete_line)
        self.properties.new_line_requested.connect(self._on_new_line_requested)
        self.page_view.line_drawn.connect(self._on_line_drawn)
        self.page_view.clean_requested.connect(self._on_clean_requested)
        self.page_view.new_line_requested.connect(self._on_new_line_requested_view)
        self.page_view.status_message.connect(self.statusBar().showMessage)
        splitter.addWidget(self.properties)

        splitter.setSizes([300, 700, 300])
        self.setCentralWidget(splitter)

        self.page_view.edit_mode = self._mode
        # In text mode the tree is hidden — the proofread view replaces it
        if self._mode == "text":
            self.tree_view.hide()

    # ---- Plain-text export / copy ------------------------------------------

    def _flush_proofread(self):
        if self._mode == "text":
            self.properties.save_proofread_texts(self.doc)

    def _get_plain_text(self):
        """Collect all line text (NFC-normalised) separated by newlines."""
        self._flush_proofread()
        lines = []
        for l in self.doc.all_lines:
            t = l.text or ""
            lines.append(unicodedata.normalize("NFC", t))
        return "\n".join(lines)

    def copy_plain_text(self):
        if self.doc is None:
            return
        QGuiApplication.clipboard().setText(self._get_plain_text())
        self.statusBar().showMessage("Plain text succesfully copied to clipboard.", 5000)

    def export_plain_text(self):
        if self.doc is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Plain Text", "", "Text files (*.txt)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(self._get_plain_text())
        self.statusBar().showMessage(f"Plain text exported to {path}.", 5000)

    # ---- File I/O ----------------------------------------------------------

    def open_file(self, filepath=None):
        if filepath is None:
            filepath, _ = QFileDialog.getOpenFileName(
                self, "Open PAGE XML", "", "PAGE XML (*.xml);;All Files (*)"
            )
            if not filepath:
                return

        try:
            self.doc.load(filepath)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load XML:\n{e}")
            return

        # Load the corresponding image
        img_path = self.doc.resolve_image_path()
        if not Path(img_path).exists():
            QMessageBox.warning(self, "Warning", f"Image not found:\n{img_path}")
        else:
            try:
                self.page_view.load_image(img_path)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load image:\n{e}")
                return

        self._current_obj = None
        self.page_view.load_annotations(self.doc)
        self.page_view.reset_zoom()
        self._populate_tree()
        if self._mode == "text":
            self.tree_view.hide()
            self.properties.show_proofread(self.doc.all_lines)
        else:
            self.tree_view.show()
            self.properties.show_nothing()

    def save_file(self):
        self._flush_proofread()
        if not self.doc.filepath:
            self.save_as_file()
        else:
            try:
                self.doc.save()
                self.statusBar().showMessage(f"Saved at {self.doc.filepath}.", 5000)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save:\n{e}")

    def save_as_file(self):
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Save PAGE XML", "", "PAGE XML (*.xml);;All Files (*)"
        )
        if not filepath:
            return
        self._flush_proofread()
        try:
            self.doc.save(filepath)
            self.statusBar().showMessage(f"Saved at {filepath}.", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save:\n{e}")

    # ---- Tree population ---------------------------------------------------

    def _populate_tree(self):
        """Rebuild the QStandardItemModel from the current document."""
        self.tree_model.clear()
        self.tree_model.setHorizontalHeaderLabels(["Page XML"])

        for region in self.doc.regions:
            region_item = QStandardItem(self._region_label(region))
            region_item.setData(region, DATA_ROLE)
            region_item.setEditable(False)
            self.tree_model.appendRow(region_item)

            for i, line in enumerate(region.lines, 1):
                line_item = QStandardItem(self._line_label(line, i))
                line_item.setData(line, DATA_ROLE)
                line_item.setEditable(False)
                region_item.appendRow(line_item)

        self.tree_view.expandAll()

    @staticmethod
    def _region_label(r):
        return f"TextRegion [{r.id[:8]}…]"

    @staticmethod
    def _line_label(l, idx):
        preview = l.text[:30] + "…" if len(l.text) > 30 else l.text
        return f"Line {idx} [{l.id[:8]}…] {preview}"

    # ---- Tree / view selection synchronisation ----------------------------

    def _on_tree_selection(self, selected, _deselected):
        """User clicked in the tree → select corresponding item in the view."""
        indexes = selected.indexes()
        if not indexes:
            return
        data_obj = indexes[0].data(DATA_ROLE)
        if data_obj is None:
            self.properties.show_nothing()
            return
        self._current_obj = data_obj
        self.page_view._selected_attr = None   # tree selection always prefers coords
        self._select_in_view(data_obj)
        self._show_handles_for(data_obj)
        self._show_properties(data_obj)

    def _on_view_selection(self, data_obj):
        """User clicked in the image view → update tree and properties."""
        if data_obj is None:
            if self._mode == "text":
                return
            self.properties.show_nothing()
            return
        self._current_obj = data_obj
        self._select_in_tree(data_obj)
        self._show_properties(data_obj)
        if self._mode == "text" and isinstance(data_obj, PageTextLine):
            self.page_view.show_line_highlight(data_obj)

    def _refresh_for(self, data_obj):
        """Full refresh after a structural change: rebuild view, tree, selection, handles, and properties."""
        self.page_view.refresh_annotations(self.doc)
        self._populate_tree()
        self._select_in_tree(data_obj)
        self._select_in_view(data_obj)
        self._show_handles_for(data_obj)
        self._show_properties(data_obj)
        self.page_view.refresh_annotations(self.doc)
        self._populate_tree()
        self._select_in_tree(data_obj)
        self._select_in_view(data_obj)
        self._show_handles_for(data_obj)
        self._show_properties(data_obj)

    def _on_property_changed(self, data_obj):
        """A property was edited in the form → refresh view, tree, handles."""
        self.page_view.refresh_annotations(self.doc)
        self._refresh_tree_labels()
        self._select_in_view(data_obj)
        self._show_handles_for(data_obj)
        self._show_properties(data_obj)
        if self._mode != "segmentation":
            self.page_view._remove_handles()
            if isinstance(data_obj, PageTextLine):
                self.properties.scroll_to_line(data_obj.id)

    # ---- Line operations ---------------------------------------------------

    @staticmethod
    def _region_for_line(line, regions):
        """Return the PageRegion that contains *line*, or None."""
        for r in regions:
            if line in r.lines:
                return r
        return None

    def _on_merge_line(self, line):
        region = self._region_for_line(line, self.doc.regions)
        if region is not None and region.merge_with_next_line(line):
            self._refresh_for(line)
            self.statusBar().showMessage("Lines merged.", 3000)

    def _on_move_line(self, line, direction):
        region = self._region_for_line(line, self.doc.regions)
        if region is None:
            return
        moved = region.move_line(line, direction)
        if moved:
            self._refresh_for(line)
            self.statusBar().showMessage(f"Line moved {direction}.", 3000)

    def _on_delete_line(self, line):
        region = self._region_for_line(line, self.doc.regions)
        if region is None or not region.delete_line(line):
            return
        self.page_view.refresh_annotations(self.doc)
        self._populate_tree()
        self._select_in_view(region)
        self._show_properties(region)
        self.statusBar().showMessage("Line deleted.", 3000)

    def _on_new_line_requested(self, region):
        """Enter drawing mode; the view handles click-by-click creation."""
        self._current_obj = region
        self.page_view.start_drawing_line()

    def _on_new_line_requested_view(self):
        region = None
        if isinstance(self._current_obj, PageRegion):
            region = self._current_obj
        elif isinstance(self._current_obj, PageTextLine):
            region = self._region_for_line(self._current_obj, self.doc.regions)
        if region is None:
            # Fall back to the first region if nothing is selected
            region = self.doc.regions[0] if self.doc.regions else None
        if region is not None:
            self._on_new_line_requested(region)

    def _on_line_drawn(self, coords, baseline):
        """
        Callback from PageView when the user finishes drawing a new line.
        Creates a new PageTextLine, appends it to the current region, and
        rebuilds the tree.
        """
        target = self._current_obj
        region = target if isinstance(target, PageRegion) else \
                 self._region_for_line(target, self.doc.regions) if isinstance(target, PageTextLine) else None
        if region is None:
            return
        line = PageTextLine()
        line.id = f"_{uuid.uuid4()}"
        line.coords = coords
        line.baseline = baseline
        region.lines.append(line)
        self._refresh_for(line)
        self.statusBar().showMessage("New line created.", 3000)

    def _on_proofread_focus(self, line):
        """Proofread cursor moved → highlight the corresponding line on the image."""
        self.page_view.show_line_highlight(line)
        self.properties.scroll_to_line(line.id)

    def _on_clean_requested(self, data_obj):
        if hasattr(data_obj, 'coords'):
            self.properties._data_obj = data_obj
            self.properties._clean_coords(data_obj.coords)
            self.statusBar().showMessage("Nearby points merged.", 3000)

    # ---- Handle / selection helpers ----------------------------------------

    def _show_handles_for(self, data_obj):
        """Show vertex handles for the given item, respecting selected_attr."""
        if self._mode != "segmentation":
            return
        sel_baseline = self.page_view._selected_attr == "baseline"
        for item in self.page_view._annotation_items:
            obj = getattr(item, 'region', None) or getattr(item, 'data_obj', None)
            if obj is not data_obj:
                continue
            is_baseline = getattr(item, 'points_attr', None) == "baseline"
            if is_baseline == sel_baseline:
                self.page_view._show_handles(item)
                break

    def _refresh_tree_labels(self):
        """Update tree item texts without rebuilding the tree structure."""
        model = self.tree_model
        for row in range(model.rowCount()):
            ri = model.item(row)
            r = ri.data(DATA_ROLE)
            if isinstance(r, PageRegion):
                ri.setText(self._region_label(r))
            for cr in range(ri.rowCount()):
                li = ri.child(cr)
                l = li.data(DATA_ROLE)
                if isinstance(l, PageTextLine):
                    li.setText(self._line_label(l, cr + 1))

    def _select_in_view(self, data_obj):
        """Set the selected state of annotation items matching *data_obj*."""
        sel_baseline = self.page_view._selected_attr == "baseline"
        for item in self.page_view._annotation_items:
            obj = getattr(item, 'region', None) or getattr(item, 'data_obj', None)
            is_target = obj is data_obj
            if getattr(item, 'points_attr', None) == "baseline":
                item.setSelected(is_target and sel_baseline)
            else:
                item.setSelected(is_target and not sel_baseline)

    def _select_in_tree(self, data_obj):
        """Set the tree's current index to the item matching *data_obj*."""
        model = self.tree_model
        for row in range(model.rowCount()):
            region_item = model.item(row)
            if region_item.data(DATA_ROLE) is data_obj:
                self.tree_view.setCurrentIndex(region_item.index())
                return
            for child_row in range(region_item.rowCount()):
                line_item = region_item.child(child_row)
                if line_item.data(DATA_ROLE) is data_obj:
                    self.tree_view.setCurrentIndex(line_item.index())
                    return

    def _show_properties(self, data_obj):
        """Show the appropriate property form for *data_obj*."""
        if isinstance(data_obj, PageRegion):
            self.properties.show_region(data_obj)
        elif isinstance(data_obj, PageTextLine):
            if self._mode == "segmentation":
                region = self._region_for_line(data_obj, self.doc.regions)
                self.properties.show_line(data_obj, region)
            elif self._mode == "text":
                self.properties.scroll_to_line(data_obj.id)
                self.properties.focus_line(data_obj.id)
        else:
            self.properties.show_nothing()
