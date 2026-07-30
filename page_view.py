"""
Graphics view with annotation overlays for PAGE XML segmentation editing.

Provides:
  - QGraphicsView with scroll-wheel zoom and keyboard panning
  - AnnotationItem / RegionItem: selectable path items for text-line coords,
    baselines, and region outlines
  - VertexHandle: draggable round handles for editing polygon vertices
  - Line-number labels drawn at the top-left of each coords polygon
  - Two-phase line drawing (coords → baseline)
  - Text-mode click-to-select-nearest-line
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QColor, QPen, QBrush, QPainterPath, QPainterPathStroker
from PyQt6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsPathItem,
    QGraphicsItem, QGraphicsEllipseItem, QGraphicsTextItem,
)

# ---------------------------------------------------------------------------
# Visual style constants
# ---------------------------------------------------------------------------
PEN_BASELINE = QPen(QColor("cyan"), 2)
PEN_COORDS = QPen(QColor("yellow"), 1.5)
PEN_SELECTED = QPen(QColor("red"), 3)
PEN_SELECTED_BASELINE = QPen(QColor("lime"), 3)       # selected baseline highlight colour
PEN_REGION = QPen(QColor(255, 100, 100, 180), 2)
BRUSH_REGION = QBrush(QColor(255, 100, 100, 30))
BRUSH_SELECTED = QBrush(QColor(255, 0, 0, 40))
BRUSH_HIGHLIGHT = QBrush(QColor(0, 120, 255, 60))     # text-mode line highlight
PEN_HIGHLIGHT = QPen(QColor(0, 120, 255), 2)
HANDLE_BRUSH = QBrush(QColor("white"))
HANDLE_PEN = QPen(QColor("red"), 2)
HANDLE_SIZE = 8


# ---------------------------------------------------------------------------
# Coordinate → QPainterPath helper
# ---------------------------------------------------------------------------

def points_to_path(pts):
    """Convert a list of (x, y) tuples to an open QPainterPath."""
    if not pts:
        return QPainterPath()
    path = QPainterPath()
    path.moveTo(*pts[0])
    for p in pts[1:]:
        path.lineTo(*p)
    return path


# ---------------------------------------------------------------------------
# Vertex handle (draggable white circle)
# ---------------------------------------------------------------------------

class VertexHandle(QGraphicsEllipseItem):
    """
    A draggable circular handle for editing a polygon/polyline vertex.

    *points_list* — reference to the model list (e.g. line.coords) so edits
                    write through immediately.
    *idx*         — index into points_list that this handle controls.
    *path_item*   — the AnnotationItem/RegionItem whose path is re-built on drag.
    *on_release*  — callback fired when the mouse button is released.
    *closed*      — if True, first and last points are kept in sync
                    (for closed polygons — coords/regions, not baselines).
    """
    def __init__(self, points_list, idx, path_item, on_release=None, closed=False):
        # Centred rectangle of HANDLE_SIZE × HANDLE_SIZE
        super().__init__(-HANDLE_SIZE // 2, -HANDLE_SIZE // 2,
                         HANDLE_SIZE, HANDLE_SIZE)
        self.points_list = points_list
        self.idx = idx
        self.path_item = path_item
        self.on_release = on_release
        self.closed = closed

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setBrush(HANDLE_BRUSH)
        self.setPen(HANDLE_PEN)
        self.setZValue(10)
        if idx < len(points_list):
            self.setPos(*points_list[idx])

    def _sync_closed_polygon(self):
        """
        For closed polygons, make sure points_list[0] == points_list[-1].
        When the first vertex moves, push the change to the last and vice versa.
        """
        if not self.closed or len(self.points_list) < 3:
            return
        if self.idx == 0:
            self.points_list[-1] = self.points_list[0]
        elif self.idx == len(self.points_list) - 1:
            self.points_list[0] = self.points_list[-1]

    def itemChange(self, change, value):
        """
        Intercept ItemPositionChange to update the model and the parent path
        in real-time during a drag.
        """
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            new_pos = value
            if self.idx < len(self.points_list):
                self.points_list[self.idx] = (new_pos.x(), new_pos.y())
                self._sync_closed_polygon()
                if self.path_item:
                    new_path = points_to_path(self.points_list)
                    self.path_item.setPath(new_path)
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        pos = self.pos()
        if self.idx < len(self.points_list):
            self.points_list[self.idx] = (pos.x(), pos.y())
            self._sync_closed_polygon()
        if self.on_release:
            self.on_release()


# ---------------------------------------------------------------------------
# Annotation item  (line coords / baseline)
# ---------------------------------------------------------------------------

class AnnotationItem(QGraphicsPathItem):
    """
    A selectable path item for a text-line coordinate polygon or baseline.

    *points_attr* is "coords" or "baseline" — used to look up the right
    attribute on *data_obj* (a PageTextLine).
    `shape()` returns an 8 px wide stroke so that clicks near the outline
    register as hits (polygons only respond to border clicks).
    """
    def __init__(self, path, pen, data_obj, points_attr, parent=None):
        super().__init__(path, parent)
        self.data_obj = data_obj
        self.points_attr = points_attr
        self.setPen(pen)
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self._normal_pen = pen

    def points_list(self):
        """Return the model list this item controls."""
        return getattr(self.data_obj, self.points_attr)

    def shape(self):
        """
        Override default shape to produce a wide outline (8 px) so the item
        is only picked up on its border, not its interior.
        """
        p = QPainterPath(self.path())
        if self.points_attr == "coords" and len(self.points_list()) >= 3:
            p.closeSubpath()
        stroker = QPainterPathStroker()
        stroker.setWidth(8)
        return stroker.createStroke(p)

    def itemChange(self, change, value):
        """Swap pen between normal and selected colour."""
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedChange:
            sel_pen = PEN_SELECTED_BASELINE if self.points_attr == "baseline" else PEN_SELECTED
            self.setPen(sel_pen if value else self._normal_pen)
        return super().itemChange(change, value)


# ---------------------------------------------------------------------------
# Region item
# ---------------------------------------------------------------------------

class RegionItem(QGraphicsPathItem):
    """
    A selectable path item for a text-region outline.
    Has a semi-transparent fill and responds to border clicks via shape().
    """
    def __init__(self, path, region, pen, brush, parent=None):
        super().__init__(path, parent)
        self.region = region
        self.setPen(pen)
        self.setBrush(brush)
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self._normal_pen = pen
        self._normal_brush = brush

    def shape(self):
        stroker = QPainterPathStroker()
        stroker.setWidth(8)
        return stroker.createStroke(self.path())

    def points_list(self):
        return self.region.coords

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedChange:
            self.setPen(PEN_SELECTED if value else self._normal_pen)
            self.setBrush(BRUSH_SELECTED if value else self._normal_brush)
        return super().itemChange(change, value)


# ---------------------------------------------------------------------------
# Main graphics view
# ---------------------------------------------------------------------------

class PageView(QGraphicsView):
    """
    The central image viewer and annotation editor.

    Signals
    -------
    selection_changed(data_obj)  — emitted when the user clicks an item
    handle_released(data_obj)    — emitted after a vertex drag or delete
    line_drawn(coords, baseline) — emitted when the two-phase drawing finishes
    """

    selection_changed = pyqtSignal(object)
    handle_released = pyqtSignal(object)
    line_drawn = pyqtSignal(object, object)  # coords, baseline
    clean_requested = pyqtSignal(object)     # data_obj whose points to clean
    new_line_requested = pyqtSignal()
    status_message = pyqtSignal(str, int)    # message, timeout_ms

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self._pixmap_item = None
        self._annotation_items = []   # all RegionItem / AnnotationItem objects
        self._vertex_handles = []
        self._line_labels = []        # QGraphicsTextItem for line numbers
        self._text_highlight_item = None
        self._all_lines = []          # flattened list of all PageTextLines
        self._press_pos = None        # for text-mode click-detection
        self._selected_attr = None    # "coords", "baseline", or "region" — tracks last-clicked attr type
        self.edit_mode = "segmentation"

        # Two-phase line drawing state (segmentation mode only)
        self._drawing_line = False
        self._draw_phase = None       # "coords" or "baseline"
        self._draw_coords = []
        self._draw_baseline = []
        self._draw_coords_item = None
        self._draw_baseline_item = None
        self._draw_handles = []

        # Zoom / pan settings
        self.scroll_wheel_zoom_factor = 1.03
        self.keyboard_zoom_factor = 1.25
        self.keyboard_span_factor = 50

        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    # -----------------------------------------------------------------------
    # Image / annotation loading
    # -----------------------------------------------------------------------

    def load_image(self, image_path):
        """Clear the scene and load a new background image."""
        self.cancel_drawing()
        self._scene.clear()
        self._annotation_items = []
        self._vertex_handles = []
        self._line_labels = []
        self._text_highlight_item = None
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            raise RuntimeError(f"Could not load image: {image_path}")
        self._pixmap_item = QGraphicsPixmapItem(pixmap)
        self._scene.addItem(self._pixmap_item)

    def _make_label(self, text, x, y, labels_list):
        """Create a small white text label at (x, y) that ignores view transforms."""
        lbl = QGraphicsTextItem(text)
        lbl.setDefaultTextColor(QColor("white"))
        lbl.setZValue(15)
        lbl.setPos(x, y)
        lbl.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self._scene.addItem(lbl)
        labels_list.append(lbl)

    def load_annotations(self, doc):
        """
        Create RegionItem / AnnotationItem / VertexHandle / labels from
        the PageDocument model.
        """
        self._all_lines = doc.all_lines
        for region in doc.regions:
            # Region outline (only in segmentation mode)
            if self.edit_mode == "segmentation" and len(region.coords) >= 3:
                rpath = points_to_path(region.coords)
                ritem = RegionItem(rpath, region, PEN_REGION, BRUSH_REGION)
                self._scene.addItem(ritem)
                self._annotation_items.append(ritem)

            # Line items + line-number labels
            for i, line in enumerate(region.lines, 1):
                if self.edit_mode == "segmentation":
                    if len(line.coords) >= 2:
                        lpath = points_to_path(line.coords)
                        litem = AnnotationItem(lpath, PEN_COORDS, line, "coords")
                        self._scene.addItem(litem)
                        self._annotation_items.append(litem)
                        # Place a number label at the top-left of the coords bbox
                        xs = [p[0] for p in line.coords]
                        ys = [p[1] for p in line.coords]
                        self._make_label(str(i), min(xs), min(ys), self._line_labels)

                    if len(line.baseline) >= 2:
                        bpath = points_to_path(line.baseline)
                        bitem = AnnotationItem(bpath, PEN_BASELINE, line, "baseline")
                        self._scene.addItem(bitem)
                        self._annotation_items.append(bitem)

    def _clear_labels(self, labels_list):
        """Remove all labels in *labels_list* from the scene."""
        for lbl in labels_list:
            self._scene.removeItem(lbl)
        labels_list.clear()

    def refresh_annotations(self, doc):
        """Full rebuild: remove everything and re-run load_annotations."""
        self.cancel_drawing()
        self._remove_handles()
        self.clear_line_highlight()
        self._clear_labels(self._line_labels)
        to_remove = [it for it in self._annotation_items]
        for it in to_remove:
            self._scene.removeItem(it)
        self._annotation_items = []
        self.load_annotations(doc)

    # -----------------------------------------------------------------------
    # Line highlight (text mode)
    # -----------------------------------------------------------------------

    def show_line_highlight(self, line):
        """Draw a semi-transparent blue polygon over *line* (text-mode selection)."""
        self.clear_line_highlight()
        if not line.coords or len(line.coords) < 2:
            return
        path = points_to_path(line.coords)
        if len(line.coords) >= 3:
            path.closeSubpath()
        item = QGraphicsPathItem(path)
        item.setPen(PEN_HIGHLIGHT)
        item.setBrush(BRUSH_HIGHLIGHT)
        item.setZValue(5)
        self._scene.addItem(item)
        self._text_highlight_item = item

    def clear_line_highlight(self):
        if self._text_highlight_item:
            self._scene.removeItem(self._text_highlight_item)
            self._text_highlight_item = None

    # -----------------------------------------------------------------------
    # Hit testing
    # -----------------------------------------------------------------------

    def _find_nearest_line(self, scene_pos):
        """
        Return the line whose coords centroid is closest to *scene_pos*.
        Used in text mode for click-to-select.
        """
        best_dist = float("inf")
        best_line = None
        x, y = scene_pos.x(), scene_pos.y()
        for line in self._all_lines:
            if not line.coords:
                continue
            cx = sum(p[0] for p in line.coords) / len(line.coords)
            cy = sum(p[1] for p in line.coords) / len(line.coords)
            dx = x - cx
            dy = y - cy
            dist = dx * dx + dy * dy
            if dist < best_dist:
                best_dist = dist
                best_line = line
        return best_line

    # -----------------------------------------------------------------------
    # Vertex handles
    # -----------------------------------------------------------------------

    def _make_handle(self, pts, idx, path_item):
        """Create a VertexHandle and add it to the scene."""
        # Closed-polygon detection: regions and coords are closed; baselines are open
        closed = hasattr(path_item, 'region') or getattr(path_item, 'points_attr', None) == "coords"
        handle = VertexHandle(pts, idx, path_item,
                              on_release=self._on_handle_release,
                              closed=closed)
        self._scene.addItem(handle)
        self._vertex_handles.append(handle)

    def _show_handles(self, annotation_item):
        """Remove old handles and create new ones for all vertices of *annotation_item*."""
        self._remove_handles()
        pts = annotation_item.points_list()
        for i in range(len(pts)):
            self._make_handle(pts, i, annotation_item)

    def _remove_handles(self):
        for h in self._vertex_handles:
            self._scene.removeItem(h)
        self._vertex_handles = []

    def _on_handle_release(self):
        """Emit handle_released for the item that owns the first handle."""
        data_obj = None
        if self._vertex_handles:
            h = self._vertex_handles[0]
            if hasattr(h.path_item, 'region'):
                data_obj = h.path_item.region
            elif hasattr(h.path_item, 'data_obj'):
                data_obj = h.path_item.data_obj
        if data_obj:
            self.handle_released.emit(data_obj)

    # -----------------------------------------------------------------------
    # Two-phase line drawing
    # -----------------------------------------------------------------------

    def start_drawing_line(self):
        """Enter drawing mode: first phase collects coords, second phase baseline."""
        self._drawing_line = True
        self._draw_phase = "coords"
        self._draw_coords = []
        self._draw_baseline = []
        self._remove_handles()
        self._clean_draw_items()
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.status_message.emit("Adding new line: click to draw region, right-click to close polygon.", 0)

    def cancel_drawing(self):
        """Abort drawing and reset state."""
        self._drawing_line = False
        self._draw_phase = None
        self._draw_coords = []
        self._draw_baseline = []
        self._clean_draw_items()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.status_message.emit("", 0)

    def _clean_draw_items(self):
        """Remove temporary preview items for the current drawing phase."""
        if self._draw_coords_item:
            self._scene.removeItem(self._draw_coords_item)
            self._draw_coords_item = None
        if self._draw_baseline_item:
            self._scene.removeItem(self._draw_baseline_item)
            self._draw_baseline_item = None
        for h in self._draw_handles:
            self._scene.removeItem(h)
        self._draw_handles = []

    def _make_draw_dot(self, pt, brush, z):
        h = QGraphicsEllipseItem(-4, -4, 8, 8)
        h.setPos(*pt)
        h.setBrush(brush)
        h.setPen(QPen(QColor("red"), 2))
        h.setZValue(z)
        self._scene.addItem(h)
        self._draw_handles.append(h)

    def _update_draw_display(self):
        """Redraw the temporary polygon/polyline and vertex dots during drawing."""
        self._clean_draw_items()
        if self._draw_coords:
            path = points_to_path(self._draw_coords)
            if self._draw_phase == "baseline" and len(self._draw_coords) >= 3:
                path.closeSubpath()
            item = QGraphicsPathItem(path)
            pen = QPen(QColor("lime"), 2)
            if self._draw_phase == "coords":
                pen.setStyle(Qt.PenStyle.DashLine)
            item.setPen(pen)
            item.setZValue(20)
            self._scene.addItem(item)
            self._draw_coords_item = item
            for pt in self._draw_coords:
                self._make_draw_dot(pt, QBrush(QColor("white")), 21)
        if self._draw_baseline:
            if len(self._draw_baseline) >= 2:
                path = points_to_path(self._draw_baseline)
                item = QGraphicsPathItem(path)
                item.setPen(QPen(QColor("cyan"), 2))
                item.setZValue(22)
                self._scene.addItem(item)
                self._draw_baseline_item = item
            for pt in self._draw_baseline:
                self._make_draw_dot(pt, QBrush(QColor("cyan")), 23)

    def _finish_draw_coords(self):
        """Close the coords phase and switch to baseline phase."""
        if len(self._draw_coords) < 3:
            self.cancel_drawing()
            return
        self._draw_phase = "baseline"
        self._update_draw_display()
        self.status_message.emit("Draw the two baseline points.", 0)

    def _finish_draw_baseline(self):
        """Finish drawing: emit line_drawn signal with (coords, baseline)."""
        if len(self._draw_baseline) < 2:
            self.cancel_drawing()
            return
        self._drawing_line = False
        self._draw_phase = None
        self._clean_draw_items()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        coords = list(self._draw_coords)
        baseline = list(self._draw_baseline)
        self._draw_coords = []
        self._draw_baseline = []
        self.line_drawn.emit(coords, baseline)

    # -----------------------------------------------------------------------
    # Zoom / pan events
    # -----------------------------------------------------------------------

    def reset_zoom(self):
        self.resetTransform()
        if self._pixmap_item:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        self.reset_zoom()

    def wheelEvent(self, event):
        factor = self.scroll_wheel_zoom_factor
        if event.angleDelta().y() < 0:
            factor = 1 / factor
        self.scale(factor, factor)

    def keyPressEvent(self, event):
        key = event.key()
        # Drawing-mode keyboard shortcuts
        if self._drawing_line:
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._draw_phase == "coords":
                    self._finish_draw_coords()
                elif self._draw_phase == "baseline":
                    self._finish_draw_baseline()
            elif key == Qt.Key.Key_Escape:
                self.cancel_drawing()
            elif key == Qt.Key.Key_Backspace:
                if self._draw_phase == "coords" and self._draw_coords:
                    self._draw_coords.pop()
                    self._update_draw_display()
                elif self._draw_phase == "baseline" and self._draw_baseline:
                    self._draw_baseline.pop()
                    self._update_draw_display()
            return
        # Navigation
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Minus):
            factor = self.keyboard_zoom_factor
            if key == Qt.Key.Key_Minus:
                factor = 1 / factor
            self.scale(factor, factor)
        elif key == Qt.Key.Key_0:
            self.reset_zoom()
        elif key == Qt.Key.Key_Left:
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - self.keyboard_span_factor
            )
        elif key == Qt.Key.Key_Right:
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() + self.keyboard_span_factor
            )
        elif key == Qt.Key.Key_Up:
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - self.keyboard_span_factor
            )
        elif key == Qt.Key.Key_Down:
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() + self.keyboard_span_factor
            )
        elif key == Qt.Key.Key_M and self.edit_mode == "segmentation":
            sel = self.scene().selectedItems()
            if sel:
                item = sel[0]
                if isinstance(item, (AnnotationItem, RegionItem)):
                    data_obj = getattr(item, 'region', None) or getattr(item, 'data_obj', None)
                    if data_obj:
                        self.clean_requested.emit(data_obj)
        elif key == Qt.Key.Key_N and self.edit_mode == "segmentation":
            self.new_line_requested.emit()
        else:
            super().keyPressEvent(event)

    # -----------------------------------------------------------------------
    # Vertex find/delete helpers
    # -----------------------------------------------------------------------

    def _find_closest_segment(self, pts, scene_pos):
        """
        Return the index (1‑based) at which inserting a new vertex would
        split the closest edge.
        """
        if len(pts) < 2:
            return 0
        best_idx = 0
        best_dist = float("inf")
        x, y = scene_pos.x(), scene_pos.y()
        for i in range(len(pts) - 1):
            ax, ay = pts[i]
            bx, by = pts[i + 1]
            abx, aby = bx - ax, by - ay
            apx, apy = x - ax, y - ay
            ab_len2 = abx * abx + aby * aby
            if ab_len2 == 0:
                continue
            t = (apx * abx + apy * aby) / ab_len2
            t = max(0, min(1, t))
            cx = ax + t * abx
            cy = ay + t * aby
            dx, dy = x - cx, y - cy
            dist = dx * dx + dy * dy
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        return best_idx + 1

    def _delete_vertex(self, handle):
        """
        Delete the vertex controlled by *handle* from its points list.
        Minimum 2 points are preserved.
        """
        pts = handle.points_list
        idx = handle.idx
        if len(pts) <= 2:
            return
        del pts[idx]
        data_obj = getattr(handle.path_item, 'region', None) or getattr(handle.path_item, 'data_obj', None)
        if data_obj:
            self.handle_released.emit(data_obj)

    # -----------------------------------------------------------------------
    # Mouse interaction
    # -----------------------------------------------------------------------

    def mousePressEvent(self, event):
        """
        Handle clicks for three modes:
          1. Drawing mode (new line creation)
          2. Text mode (click-to-select nearest line)
          3. Segmentation mode (select items, drag handles, add vertices, delete vertices)
        """
        # --- Phase 1: two-phase line drawing ---
        if self._drawing_line:
            if event.button() == Qt.MouseButton.LeftButton:
                scene_pos = self.mapToScene(event.pos())
                if self._draw_phase == "coords":
                    self._draw_coords.append((scene_pos.x(), scene_pos.y()))
                    self._update_draw_display()
                elif self._draw_phase == "baseline":
                    self._draw_baseline.append((scene_pos.x(), scene_pos.y()))
                    self._update_draw_display()
                    if len(self._draw_baseline) >= 2:
                        self._finish_draw_baseline()
            elif event.button() == Qt.MouseButton.RightButton:
                if self._draw_phase == "coords":
                    if self._draw_coords and self._draw_coords[0] != self._draw_coords[-1]:
                        self._draw_coords.append(self._draw_coords[0])
                    self._finish_draw_coords()
                elif self._draw_phase == "baseline":
                    self._finish_draw_baseline()
            return

        item = self.itemAt(event.pos())

        # --- Text mode: click-detection for nearest line ---
        if self.edit_mode == "text":
            self._press_pos = event.pos()
            super().mousePressEvent(event)
            return

        # --- Right-click on vertex = delete ---
        if event.button() == Qt.MouseButton.RightButton:
            if isinstance(item, VertexHandle) and self.edit_mode == "segmentation":
                self._delete_vertex(item)
            return

        # --- Left-click on vertex handle: select/drag ---
        if isinstance(item, VertexHandle):
            if self.edit_mode == "segmentation":
                self.setDragMode(QGraphicsView.DragMode.NoDrag)
                super().mousePressEvent(event)
                self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            else:
                super().mousePressEvent(event)
            return

        # --- Click on annotation / region item: select, possibly add vertex ---
        if isinstance(item, (AnnotationItem, RegionItem)):
            added = False
            # Click on an already-selected item's border adds a new vertex
            if self.edit_mode == "segmentation" and item.isSelected():
                scene_pos = self.mapToScene(event.pos())
                pts = item.points_list()
                idx = self._find_closest_segment(pts, scene_pos)
                pts.insert(idx, (scene_pos.x(), scene_pos.y()))
                item.setPath(points_to_path(pts))
                added = True
                self._remove_handles()
                pts = item.points_list()
                for i in range(len(pts)):
                    self._make_handle(pts, i, item)
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            super().mousePressEvent(event)
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        else:
            # Click on empty background: deselect everything
            self._remove_handles()
            for it in self._annotation_items:
                it.setSelected(False)
            super().mousePressEvent(event)
            self.selection_changed.emit(None)
            return

        # After selection: determine which item was picked and show handles
        first = self.scene().selectedItems()[0] if self.scene().selectedItems() else None
        if first is None:
            self._remove_handles()
            self.selection_changed.emit(None)
            return

        data_obj = None
        if isinstance(first, RegionItem):
            data_obj = first.region
            self._selected_attr = "region"
            if self.edit_mode == "segmentation" and not added:
                self._show_handles(first)
        elif isinstance(first, AnnotationItem):
            data_obj = first.data_obj
            self._selected_attr = first.points_attr
            if self.edit_mode == "segmentation" and not added:
                self._show_handles(first)
        else:
            self._remove_handles()
        self.selection_changed.emit(data_obj)

    def mouseReleaseEvent(self, event):
        """
        In text mode: detect short clicks (no drag) and find the nearest line.
        Always restore arrow cursor.
        """
        if self.edit_mode == "text" and self._press_pos is not None:
            delta = event.pos() - self._press_pos
            self._press_pos = None
            if delta.manhattanLength() < 5:
                scene_pos = self.mapToScene(event.pos())
                line = self._find_nearest_line(scene_pos)
                self.selection_changed.emit(line)
                super().mouseReleaseEvent(event)
                self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
                return
        super().mouseReleaseEvent(event)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
