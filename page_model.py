"""
Data model classes for PAGE XML (http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15).

The model uses lxml for parsing and round-trip serialisation.  Python objects hold
the data in-memory; _from_elem() / _to_elem() bridge to/from XML.  Only TextRegion
and TextLine are actively used — Word and Glyph classes exist but are dead code.
"""

from pathlib import Path
import shapely
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union, nearest_points
import unicodedata
import lxml.etree as ET

PAGE_NS = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15"


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def parse_points(s):
    """Convert a PAGE XML points attribute like "100,200 150,250" into a list of (x, y) floats."""
    return [tuple(map(float, p.split(","))) for p in s.split()]

def format_points(points):
    """Inverse of parse_points — writes integer coords without ".0" for whole numbers."""
    return " ".join(f"{int(x)},{int(y)}" for x, y in points)

def clean_points(points, tolerance=5.0):
    """
    Simplify polygon with the Douglas-Peucker algorithm.
    """
    return list(shapely.simplify(
        Polygon(points),
        tolerance=tolerance
    ).exterior.coords)


def stitch_polygons(pts_a, pts_b):
    """
    Merge two polygon outlines into one composite polygon.
    """
    poly_a = Polygon(pts_a)
    poly_b = Polygon(pts_b)
    if poly_a.intersects(poly_b):
        return list(poly_a.union(poly_b).exterior.coords)
    else:
        # TODO: This only works for horizontal scripts.
        p1, p2 = nearest_points(poly_a, poly_b)
        p1 = (p1.x, poly_a.centroid.y)
        p2 = (p2.x, poly_b.centroid.y)
        bridge = LineString([ p1, p2 ]).buffer(
            min(poly_a.area / poly_a.length, poly_b.area / poly_b.length),
            cap_style='square'
        )
        return list(unary_union([ poly_a, poly_b, bridge ]).exterior.coords)


# ---------------------------------------------------------------------------
# Data model classes
# ---------------------------------------------------------------------------


class PageTextLine:
    """
    A single text line: polygon outline, baseline, and OCR text.
    """
    def __init__(self, elem=None):
        self.id = ""
        self.coords = []      # list of (x, y) tuples — closed polygon
        self.baseline = []    # list of (x, y) tuples — open polyline
        self.text = ""        # Unicode (OCR) text
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

    def _to_elem(self, parent):
        e = ET.SubElement(parent, f"{{{PAGE_NS}}}TextLine")
        if self.id:
            e.set("id", self.id)
        c = ET.SubElement(e, f"{{{PAGE_NS}}}Coords")
        c.set("points", format_points(self.coords))
        bl = ET.SubElement(e, f"{{{PAGE_NS}}}Baseline")
        bl.set("points", format_points(self.baseline))
        te = ET.SubElement(e, f"{{{PAGE_NS}}}TextEquiv")
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

    def move_line(self, line, direction):
        """Swap *line* with the following or preceding line in the list.

        *direction*: "up" or "down"
        """
        # Retrieve index of line to be moved.
        idx = self.lines.index(line)
        # Parse direction string.
        if direction == "up":
            new_idx = idx - 1
        elif direction == "down":
            new_idx = idx + 1
        else:
            raise ValueError(f"Unsupported direction {direction}.")
        # Bounds reached, abort.
        if not (0 <= new_idx < len(self.lines)):
            return False
        # Swap lines.
        self.lines[idx], self.lines[new_idx] = self.lines[new_idx], self.lines[idx]
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
        the model.  All text is NFD-normalized before writing.
        """
        if filepath:
            self.filepath = filepath
        if self.tree is None:
            return
        # NFD-normalise all text before writing
        for r in self.regions:
            for l in r.lines:
                if l.text:
                    l.text = unicodedata.normalize("NFD", l.text)
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
