"""
Data model classes for PAGE XML (http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15).

The model uses lxml for parsing and round-trip serialisation.  Python objects hold
the data in-memory; _from_elem() / _to_elem() bridge to/from XML.  Only TextRegion
and TextLine are actively used — Word and Glyph classes exist but are dead code.
"""

from pathlib import Path
import math
import unicodedata
import lxml.etree as ET

PAGE_NS = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15"


 # ----
# Coordinate helpers
 # ----

def parse_points(s):
    """Convert a PAGE XML points attribute like "100,200 150,250" into a list of (x, y) floats."""
    return [tuple(map(float, p.split(","))) for p in s.split()]


def format_points(points):
    """Inverse of parse_points — writes integer coords without ".0" for whole numbers."""
    return " ".join(f"{int(x)},{int(y)}" for x, y in points)


def clean_points(points, threshold=5.0):
    """
    Remove redundant points that are within *threshold* (Euclidean) of their neighbours.
    Keeps first and last points; iteratively removes intermediate near-duplicates.
    """
    if len(points) < 3:
        return list(points)
    first = points[0]
    last = points[-1]
    result = [first]
    for p in points[1:-1]:
        dx = p[0] - result[-1][0]
        dy = p[1] - result[-1][1]
        if (dx * dx + dy * dy) >= threshold * threshold:
            result.append(p)
    if len(result) == 1:
        result.append(last)
    else:
        dx = last[0] - result[-1][0]
        dy = last[1] - result[-1][1]
        if (dx * dx + dy * dy) >= threshold * threshold:
            result.append(last)
        else:
            result[-1] = last
    return result


def stitch_polygons(pts_a, pts_b):
    """
    Merge two polygon outlines into one composite polygon.

    Strategy: find the closest pair of points across the two polygons, then check for
    a second well-separated pair within 4× the minimum distance.  If a two-bridge
    connection exists, try all four arc-direction combinations (CW/CWW × CW/CCW) and
    pick the one yielding the largest polygon area (outer boundary).  Falls back to a
    single-bridge cross-connect when the bridges are too close together.
    """
    if not pts_a or not pts_b:
        return list(pts_a) + list(pts_b)
    n, m = len(pts_a), len(pts_b)

    # Find the closest pair
    best_d2 = float("inf")
    i1 = j1 = 0
    for i in range(n):
        for j in range(m):
            d2 = (pts_a[i][0] - pts_b[j][0]) ** 2 + (pts_a[i][1] - pts_b[j][1]) ** 2
            if d2 < best_d2:
                best_d2 = d2
                i1, j1 = i, j

    # Collect all pairs within 4x the minimum distance
    threshold = max(best_d2 * 4, 100.0)
    close_pairs = []
    for i in range(n):
        for j in range(m):
            d2 = (pts_a[i][0] - pts_b[j][0]) ** 2 + (pts_a[i][1] - pts_b[j][1]) ** 2
            if d2 <= threshold:
                close_pairs.append((d2, i, j))

    if len(close_pairs) < 2:
        # Single bridge: use one-bridge cross-connect
        result = pts_a[:i1 + 1] + pts_b[j1:] + pts_b[:j1] + pts_a[i1 + 1:]
        return result

    # Pick the two closest pairs at opposite ends (by x-coordinate)
    close_pairs.sort(key=lambda t: pts_a[t[1]][0] + pts_b[t[2]][0])

    # Check the leftmost and rightmost pairs are well-separated on both polygons
    left = close_pairs[0]
    right = close_pairs[-1]
    di = min((left[1] - right[1]) % n, (right[1] - left[1]) % n)
    dj = min((left[2] - right[2]) % m, (right[2] - left[2]) % m)
    if left == right or di < 2 or dj < 2:
        # Single bridge: go around A from start to closest, then all of B, then rest of A
        result = pts_a[:i1 + 1] + pts_b[j1:] + pts_b[:j1] + pts_a[i1 + 1:]
        return result

    left_i, left_j = left[1], left[2]
    right_i, right_j = right[1], right[2]

    # Build both arcs (CW and CCW) for each polygon
    def arc(pts, i, j, step):
        if step > 0:
            step_fn = lambda idx: (idx + 1) % len(pts)
        else:
            step_fn = lambda idx: (idx - 1) % len(pts)
        result = []
        idx = i
        while True:
            result.append(pts[idx])
            if idx == j:
                break
            idx = step_fn(idx)
        return result

    cw_a = arc(pts_a, left_i, right_i, 1)
    ccw_a = arc(pts_a, left_i, right_i, -1)
    cw_b = arc(pts_b, right_j, left_j, 1)
    ccw_b = arc(pts_b, right_j, left_j, -1)

    # Pick the combination producing the largest polygon area (traces the outer boundary)
    def poly_area(pts):
        s = 0.0
        for k in range(len(pts)):
            x1, y1 = pts[k]
            x2, y2 = pts[(k + 1) % len(pts)]
            s += x1 * y2 - x2 * y1
        return abs(s)

    candidates = [(cw_a, cw_b), (cw_a, ccw_b), (ccw_a, cw_b), (ccw_a, ccw_b)]
    best = max(candidates, key=lambda t: poly_area(t[0] + t[1]))
    return best[0] + best[1]


 # ----
# Data model classes  (Word / Glyph are legacy — not actively used)
 # ----

class PageGlyph:
    """
    A single glyph within a word.  Dead code — kept for backward compatibility
    with PAGE XML files that include <Glyph> elements.
    """
    def __init__(self, elem=None):
        self.id = ""
        self.coords = []
        self.text = ""
        self.confidence = 0.0
        if elem is not None:
            self._from_elem(elem)

    def _from_elem(self, elem):
        self.id = elem.get("id", "")
        c = elem.find(f"{{{PAGE_NS}}}Coords")
        if c is not None:
            self.coords = parse_points(c.get("points", ""))
        te = elem.find(f"{{{PAGE_NS}}}TextEquiv")
        if te is not None:
            u = te.find(f"{{{PAGE_NS}}}Unicode")
            if u is not None and u.text:
                self.text = u.text
            self.confidence = float(te.get("conf", 0))

    def _to_elem(self, parent):
        e = ET.SubElement(parent, f"{{{PAGE_NS}}}Glyph")
        if self.id:
            e.set("id", self.id)
        c = ET.SubElement(e, f"{{{PAGE_NS}}}Coords")
        c.set("points", format_points(self.coords))
        te = ET.SubElement(e, f"{{{PAGE_NS}}}TextEquiv")
        te.set("conf", f"{self.confidence:.4f}")
        u = ET.SubElement(te, f"{{{PAGE_NS}}}Unicode")
        u.text = self.text
        return e


class PageWord:
    """
    A word within a text line.  Dead code — kept for backward compatibility
    with PAGE XML files that include <Word> elements.
    """
    def __init__(self, elem=None):
        self.id = ""
        self.coords = []
        self.text = ""
        self.confidence = 0.0
        self.glyphs = []
        if elem is not None:
            self._from_elem(elem)

    def _from_elem(self, elem):
        self.id = elem.get("id", "")
        c = elem.find(f"{{{PAGE_NS}}}Coords")
        if c is not None:
            self.coords = parse_points(c.get("points", ""))
        te = elem.find(f"{{{PAGE_NS}}}TextEquiv")
        if te is not None:
            u = te.find(f"{{{PAGE_NS}}}Unicode")
            if u is not None and u.text:
                self.text = u.text
            self.confidence = float(te.get("conf", 0))
        for g in elem.findall(f"{{{PAGE_NS}}}Glyph"):
            self.glyphs.append(PageGlyph(g))

    def _to_elem(self, parent):
        e = ET.SubElement(parent, f"{{{PAGE_NS}}}Word")
        if self.id:
            e.set("id", self.id)
        c = ET.SubElement(e, f"{{{PAGE_NS}}}Coords")
        c.set("points", format_points(self.coords))
        te = ET.SubElement(e, f"{{{PAGE_NS}}}TextEquiv")
        te.set("conf", f"{self.confidence:.4f}")
        u = ET.SubElement(te, f"{{{PAGE_NS}}}Unicode")
        u.text = self.text
        for g in self.glyphs:
            g._to_elem(e)
        return e


class PageTextLine:
    """
    A single text line: polygon outline, baseline, and OCR text.
    """
    def __init__(self, elem=None):
        self.id = ""
        self.coords = []      # list of (x, y) tuples — closed polygon
        self.baseline = []    # list of (x, y) tuples — open polyline
        self.text = ""        # Unicode (OCR) text
        self.confidence = 0.0
        if elem is not None:
            self._from_elem(elem)

    def _from_elem(self, elem):
        self.id = elem.get("id", "")
        c = elem.find(f"{{{PAGE_NS}}}Coords")
        if c is not None:
            self.coords = parse_points(c.get("points", ""))
        bl = elem.find(f"{{{PAGE_NS}}}Baseline")
        if bl is not None:
            self.baseline = parse_points(bl.get("points", ""))
        te = elem.find(f"{{{PAGE_NS}}}TextEquiv")
        if te is not None:
            u = te.find(f"{{{PAGE_NS}}}Unicode")
            if u is not None and u.text:
                self.text = u.text
            self.confidence = float(te.get("conf", 0))

    def _to_elem(self, parent):
        e = ET.SubElement(parent, f"{{{PAGE_NS}}}TextLine")
        if self.id:
            e.set("id", self.id)
        c = ET.SubElement(e, f"{{{PAGE_NS}}}Coords")
        c.set("points", format_points(self.coords))
        bl = ET.SubElement(e, f"{{{PAGE_NS}}}Baseline")
        bl.set("points", format_points(self.baseline))
        te = ET.SubElement(e, f"{{{PAGE_NS}}}TextEquiv")
        te.set("conf", f"{self.confidence:.4f}")
        u = ET.SubElement(te, f"{{{PAGE_NS}}}Unicode")
        u.text = self.text
        return e


class PageRegion:
    """
    A text region containing a list of text lines.
    Type is parsed from the optional `custom` attribute (Kraken convention).
    """
    def __init__(self, elem=None):
        self.id = ""
        self.type = ""
        self.coords = []
        self.text = ""
        self.confidence = 0.0
        self.lines = []
        if elem is not None:
            self._from_elem(elem)

    def _from_elem(self, elem):
        self.id = elem.get("id", "")
        # Kraken stores the region type inside the `custom` attribute:
        #   custom="type {type:heading;}"
        custom = elem.get("custom", "")
        if "type {type:" in custom:
            self.type = custom.split("type {type:")[1].split("}")[0]
        c = elem.find(f"{{{PAGE_NS}}}Coords")
        if c is not None:
            self.coords = parse_points(c.get("points", ""))
        te = elem.find(f"{{{PAGE_NS}}}TextEquiv")
        if te is not None:
            u = te.find(f"{{{PAGE_NS}}}Unicode")
            if u is not None and u.text:
                self.text = u.text
            self.confidence = float(te.get("conf", 0))
        for tl in elem.findall(f"{{{PAGE_NS}}}TextLine"):
            self.lines.append(PageTextLine(tl))

    def _to_elem(self, parent):
        e = ET.SubElement(parent, f"{{{PAGE_NS}}}TextRegion")
        if self.id:
            e.set("id", self.id)
        if self.type:
            e.set("custom", f"type {{type:{self.type};}}")
        c = ET.SubElement(e, f"{{{PAGE_NS}}}Coords")
        c.set("points", format_points(self.coords))
        for tl in self.lines:
            tl._to_elem(e)
        return e

    # ---- Region-level operations (move / merge / delete lines) -------------

    def merge_with_next_line(self, line):
        """Merge *line* with the line immediately after it in self.lines."""
        idx = self.lines.index(line)
        if idx >= len(self.lines) - 1:
            return False
        next_line = self.lines[idx + 1]
        merged = stitch_polygons(line.coords, next_line.coords)
        # Rotate so the first point of the first line stays first in the merged polygon
        first_pt = line.coords[0]
        try:
            rot = merged.index(first_pt)
        except ValueError:
            rot = 0
        line.coords = merged[rot:] + merged[:rot]
        if line.coords[-1] != line.coords[0]:
            line.coords.append(line.coords[0])
        if line.baseline and next_line.baseline:
            line.baseline = [line.baseline[0], next_line.baseline[-1]]
        elif next_line.baseline:
            line.baseline = list(next_line.baseline)
        if line.text or next_line.text:
            line.text = (line.text + " " + next_line.text).strip()
        self.lines.pop(idx + 1)
        return True

    def move_line_up(self, line):
        """Swap *line* with the preceding line in the list."""
        idx = self.lines.index(line)
        if idx == 0:
            return False
        self.lines[idx], self.lines[idx - 1] = self.lines[idx - 1], self.lines[idx]
        return True

    def move_line_down(self, line):
        """Swap *line* with the following line in the list."""
        idx = self.lines.index(line)
        if idx >= len(self.lines) - 1:
            return False
        self.lines[idx], self.lines[idx + 1] = self.lines[idx + 1], self.lines[idx]
        return True

    def delete_line(self, line):
        """Remove *line* from self.lines."""
        if line not in self.lines:
            return False
        self.lines.remove(line)
        return True


class PageDocument:
    """
    Top-level document that wraps the lxml tree and provides load/save.
    Holds the image metadata and a list of PageRegion objects.
    """
    def __init__(self):
        self.tree = None
        self.filepath = ""
        self.image_filename = ""
        self.image_width = 0
        self.image_height = 0
        self.regions = []

    def load(self, filepath):
        """Parse a PAGE XML file from disk and populate the data model."""
        self.filepath = filepath
        self.tree = ET.parse(filepath)
        root = self.tree.getroot()
        page = root.find(f"{{{PAGE_NS}}}Page")
        if page is None:
            raise ValueError("No <Page> element found")
        self.image_filename = page.get("imageFilename", "")
        self.image_width = int(page.get("imageWidth", 0))
        self.image_height = int(page.get("imageHeight", 0))
        self.regions = []
        for r in page.findall(f"{{{PAGE_NS}}}TextRegion"):
            self.regions.append(PageRegion(r))

    def resolve_image_path(self):
        """
        Resolve the image filename to an absolute path.
        Tries XML-relative first, falls back to CWD-relative.
        """
        xml_dir = Path(self.filepath).resolve().parent
        img = Path(self.image_filename)
        if img.is_absolute():
            return str(img)
        candidate = (xml_dir / img).resolve()
        if candidate.exists():
            return str(candidate)
        return str((Path.cwd() / img).resolve())

    @property
    def all_lines(self):
        """Flattened list of every PageTextLine across all regions."""
        return [l for r in self.regions for l in r.lines]

    def save(self, filepath=None):
        """
        Serialize the in-memory model back to XML.
        Strips old <TextRegion> elements from the tree and re-creates them from
        the model.  All text is NFC-normalized before writing.
        """
        if filepath:
            self.filepath = filepath
        if self.tree is None:
            return
        # NFC-normalise all text before writing
        for r in self.regions:
            for l in r.lines:
                if l.text:
                    l.text = unicodedata.normalize("NFC", l.text)
        root = self.tree.getroot()
        page = root.find(f"{{{PAGE_NS}}}Page")
        if page is None:
            return
        # Remove old regions from the XML tree
        for r in page.findall(f"{{{PAGE_NS}}}TextRegion"):
            page.remove(r)
        # Write back from the model
        for r in self.regions:
            r._to_elem(page)
        self.tree.write(self.filepath, xml_declaration=True, encoding="UTF-8", pretty_print=True)
