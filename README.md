# Pagesont — Simple PAGE XML Editor

A WYSIWYG editor for PAGE XML documents with two modes: segmentation editing and text proofreading.

## Quick start

```bash
pip install PyQt6 lxml shapely
python viewer.py seg [file.xml]   # segmentation mode
python viewer.py text [file.xml]  # proofreading mode
```

## Segmentation mode (`seg`)

Edit region polygons, line coordinates, and baselines on top of the page image.

- **Left click** — select region / line / baseline
- **Drag vertex** (white circle) — move a point
- **Left click on selected polygon edge** — add a vertex
- **Right click on vertex** — delete it
- **M** — merge nearby points on selected item
- **N** — start drawing a new line (click to place coords, right-click/Enter to close; then place 2 baseline points)

Sidebar buttons: merge lines, reorder, delete, new line.

![image](https://catonif.github.io/demo/pagesont/segmentation.png)

## Text mode (`text`)

Review and correct OCR output. The proofread panel lists every line with diff highlighting. Click the page image to jump to the nearest line.

- **Enter** in corrected-text field → move to next line
- **Ctrl+T** — export plain text
- **Ctrl+Shift+T** — copy plain text to clipboard

![image](https://catonif.github.io/demo/pagesont/proofreading.png)

## Data model

Built on PAGE XML 2019-07-15, but only supports display for `TextRegion` and `TextLine`, while words and glyphs are stripped. All text is NFC-normalized on save. Polygons have no enforced winding order.
