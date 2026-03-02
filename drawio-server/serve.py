#!/usr/bin/env python3
"""
DrawIO Diagram Server — lightweight standalone viewer.

A self-contained web server that serves .drawio files as interactive
browsable diagrams. Works like a statically-generated Confluence for
diagrams — no database, no indexing, just point at a directory of
.drawio files and go.

Usage:
    # Serve diagrams from a directory
    python serve.py /path/to/diagrams

    # Serve from current directory
    python serve.py .

    # Custom port
    python serve.py /path/to/diagrams --port 9000

    # Generate static HTML site (no server needed)
    python serve.py /path/to/diagrams --generate-static --output ./site
"""

import os
import re
import sys
import json
import argparse
import mimetypes
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import quote, unquote

# Add parent for drawio_tools import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from extractor.drawio_tools import decode_diagram_data
except ImportError:
    decode_diagram_data = None

try:
    from lxml import etree
except ImportError:
    import xml.etree.ElementTree as etree


def scan_diagrams(root_dir):
    """
    Scan a directory tree for .drawio files.

    Returns list of dicts with path, name, space, text, tab names, mtime, etc.
    """
    root = Path(root_dir).resolve()
    diagrams = []

    for drawio_path in sorted(root.rglob("*.drawio")):
        rel = drawio_path.relative_to(root)
        parts = rel.parts

        # Determine "space" from first subdirectory, or "root"
        space = parts[0] if len(parts) > 1 else "_root"
        name = drawio_path.stem

        # Extract text content and tab names from the drawio XML
        text_content, tab_names = extract_metadata(str(drawio_path))

        # File modification time
        try:
            mtime = drawio_path.stat().st_mtime
            from datetime import datetime
            modified = datetime.fromtimestamp(mtime).strftime("%b %d, %Y %H:%M")
        except Exception:
            modified = ""

        # Check for companion PNG
        png_path = drawio_path.with_suffix(".png")
        if not png_path.exists() and len(parts) > 1:
            alt_png = root / "images" if space == "_root" else root.parent / "images" / space
            alt_png = alt_png / f"{name}.png"
            if alt_png.exists():
                png_path = alt_png

        diagrams.append({
            "path": str(drawio_path),
            "rel_path": str(rel),
            "name": name,
            "space": space,
            "text_content": text_content[:500] if text_content else "",
            "tab_names": tab_names,
            "modified": modified,
            "has_png": png_path.exists(),
            "png_path": str(png_path) if png_path.exists() else None,
        })

    return diagrams


def extract_metadata(filepath):
    """Extract text labels and tab/page names from a .drawio file.

    Returns (text_content, tab_names) tuple.
    """
    try:
        tree = etree.parse(filepath)
        root = tree.getroot()
        texts = []
        tab_names = []

        for diagram in root.findall(".//diagram"):
            name = diagram.get("name", "")
            if name:
                texts.append(name)
                tab_names.append(name)

            content = diagram.text
            if content and content.strip() and decode_diagram_data:
                decoded = decode_diagram_data(content.strip())
                if decoded:
                    try:
                        inner = etree.fromstring(decoded.encode("utf-8"))
                        for cell in inner.findall(".//mxCell"):
                            value = cell.get("value", "")
                            if value:
                                clean = re.sub(r"<[^>]+>", " ", value)
                                clean = re.sub(r"\s+", " ", clean).strip()
                                if clean:
                                    texts.append(clean)
                    except Exception:
                        pass

            for model in diagram.findall(".//mxGraphModel"):
                for cell in model.findall(".//mxCell"):
                    value = cell.get("value", "")
                    if value:
                        clean = re.sub(r"<[^>]+>", " ", value)
                        clean = re.sub(r"\s+", " ", clean).strip()
                        if clean:
                            texts.append(clean)

        return " ".join(texts), tab_names
    except Exception:
        return "", []


def build_index(diagrams):
    """Group diagrams by space and build navigation index."""
    spaces = {}
    for d in diagrams:
        space = d["space"]
        if space not in spaces:
            spaces[space] = []
        spaces[space].append(d)

    # Sort spaces by diagram count descending
    sorted_spaces = dict(
        sorted(spaces.items(), key=lambda x: (-len(x[1]), x[0]))
    )
    return sorted_spaces


# =============================================================================
# HTML Templates (self-contained, no Jinja dependency)
# =============================================================================

STYLE = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #fff; color: #172b4d; line-height: 1.6;
}
a { color: #0052cc; text-decoration: none; }
a:hover { text-decoration: underline; color: #0065ff; }

/* ── Top nav bar (Confluence-like) ── */
.topnav {
    background: #0052cc; color: white; height: 48px;
    display: flex; align-items: center; padding: 0 20px;
    position: sticky; top: 0; z-index: 100;
    box-shadow: 0 2px 4px rgba(0,0,0,0.15);
}
.topnav-logo {
    font-weight: 700; font-size: 1.05em; color: white;
    text-decoration: none; display: flex; align-items: center; gap: 8px;
}
.topnav-logo:hover { text-decoration: none; color: #deebff; }
.topnav-logo svg { width: 22px; height: 22px; fill: white; }
.topnav-links { display: flex; gap: 4px; margin-left: 30px; }
.topnav-links a {
    color: #deebff; padding: 6px 14px; border-radius: 4px;
    font-size: 0.88em; font-weight: 500;
}
.topnav-links a:hover { background: rgba(255,255,255,0.15); text-decoration: none; color: white; }
.topnav-links a.active { background: rgba(255,255,255,0.2); color: white; }
.topnav-right { margin-left: auto; display: flex; align-items: center; gap: 10px; }
.topnav-search {
    background: rgba(255,255,255,0.15); border: none; color: white;
    padding: 6px 14px; border-radius: 4px; font-size: 0.85em; width: 200px;
}
.topnav-search::placeholder { color: rgba(255,255,255,0.6); }
.topnav-search:focus { background: white; color: #172b4d; outline: none; }

/* ── Main content container ── */
.container { max-width: 1200px; margin: 0 auto; padding: 30px 40px; }

/* ── Breadcrumbs (Confluence style) ── */
.breadcrumb {
    font-size: 0.82em; color: #6b778c; margin-bottom: 8px;
    display: flex; align-items: center; gap: 4px;
}
.breadcrumb a { color: #6b778c; }
.breadcrumb a:hover { color: #0052cc; }
.breadcrumb .sep { color: #c1c7d0; }

/* ── Page title (Confluence style) ── */
.page-title {
    font-size: 1.7em; font-weight: 600; color: #172b4d;
    margin-bottom: 4px; line-height: 1.3;
}

/* ── Page metadata bar ── */
.page-meta {
    display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
    padding: 8px 0 16px; border-bottom: 1px solid #ebecf0;
    margin-bottom: 24px; font-size: 0.82em; color: #6b778c;
}
.page-meta-item { display: flex; align-items: center; gap: 5px; }
.page-meta-item svg { width: 14px; height: 14px; fill: #97a0af; }
.page-label {
    display: inline-block; padding: 2px 8px; border-radius: 3px;
    background: #dfe1e6; color: #42526e; font-size: 0.85em;
}

/* ── Stats boxes (home page) ── */
.stats { display: flex; gap: 20px; margin-bottom: 28px; flex-wrap: wrap; }
.stat-box {
    background: #f4f5f7; padding: 18px 24px; border-radius: 6px;
    border: 1px solid #ebecf0;
}
.stat-value { font-size: 1.8em; font-weight: 700; color: #0052cc; }
.stat-label { color: #6b778c; font-size: 0.82em; }

/* ── Space cards grid ── */
.space-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
    gap: 14px; margin-bottom: 28px;
}
.space-card {
    background: white; padding: 18px 20px; border-radius: 6px;
    border: 1px solid #dfe1e6; transition: box-shadow 0.15s;
}
.space-card:hover { box-shadow: 0 4px 14px rgba(9,30,66,0.12); }
.space-card a { color: #172b4d; text-decoration: none; display: block; }
.space-card a:hover { text-decoration: none; }
.space-icon {
    width: 36px; height: 36px; border-radius: 6px; background: #0052cc;
    color: white; display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 1em; margin-bottom: 10px;
}
.space-name { font-size: 1.05em; font-weight: 600; }
.space-count { color: #6b778c; font-size: 0.82em; margin-top: 2px; }

/* ── Diagram card grid ── */
.diagram-list {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 14px;
}
.diagram-card {
    background: white; border-radius: 6px; overflow: hidden;
    border: 1px solid #dfe1e6; transition: box-shadow 0.15s;
}
.diagram-card:hover { box-shadow: 0 4px 14px rgba(9,30,66,0.12); }
.diagram-card a { text-decoration: none; color: inherit; display: block; }
.diagram-card a:hover { text-decoration: none; }
.diagram-thumb {
    width: 100%; height: 170px; object-fit: contain;
    background: #fafbfc; border-bottom: 1px solid #ebecf0;
}
.diagram-thumb-placeholder {
    width: 100%; height: 170px; display: flex; align-items: center;
    justify-content: center; background: #fafbfc; border-bottom: 1px solid #ebecf0;
    color: #97a0af; font-size: 0.85em;
}
.diagram-card-body { padding: 12px 14px; }
.diagram-card-title { font-weight: 600; color: #172b4d; margin-bottom: 2px; word-break: break-word; font-size: 0.92em; }
.diagram-card-meta { font-size: 0.78em; color: #97a0af; }

/* ── Sidebar + content layout (viewer pages) ── */
.layout { display: flex; min-height: calc(100vh - 48px); }
.sidebar {
    width: 260px; min-width: 260px; background: #fafbfc;
    border-right: 1px solid #ebecf0; padding: 0;
    overflow-y: auto; position: sticky; top: 48px;
    height: calc(100vh - 48px);
}
.sidebar-header {
    padding: 16px 16px 12px; border-bottom: 1px solid #ebecf0;
    font-weight: 600; color: #172b4d; font-size: 0.92em;
    display: flex; align-items: center; gap: 8px;
}
.sidebar-header .s-icon {
    width: 24px; height: 24px; border-radius: 4px; background: #0052cc;
    color: white; display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 0.72em; flex-shrink: 0;
}
.sidebar-search {
    margin: 10px 12px; padding: 6px 10px; border: 1px solid #dfe1e6;
    border-radius: 4px; width: calc(100% - 24px); font-size: 0.82em;
    background: white;
}
.sidebar-search:focus { border-color: #4c9aff; outline: none; }
.sidebar-section {
    padding: 8px 0;
}
.sidebar-section-title {
    padding: 4px 16px; font-size: 0.72em; font-weight: 600;
    color: #6b778c; text-transform: uppercase; letter-spacing: 0.5px;
}
.sidebar-item {
    display: block; padding: 5px 16px 5px 24px; font-size: 0.84em;
    color: #42526e; text-decoration: none; border-left: 3px solid transparent;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.sidebar-item:hover { background: #ebecf0; text-decoration: none; color: #172b4d; }
.sidebar-item.active {
    background: #deebff; border-left-color: #0052cc;
    color: #0052cc; font-weight: 500;
}
.content { flex: 1; min-width: 0; }
.content-inner { max-width: 1000px; padding: 28px 40px; }

/* ── Viewer ── */
.viewer-wrap {
    background: white; border: 1px solid #dfe1e6; border-radius: 6px;
    overflow: hidden; margin-bottom: 24px;
}
.viewer-toolbar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 14px; background: #f4f5f7; border-bottom: 1px solid #ebecf0;
    flex-wrap: wrap; gap: 8px;
}
.viewer-toolbar .left, .viewer-toolbar .right { display: flex; align-items: center; gap: 6px; }
.tbtn {
    padding: 4px 12px; border: 1px solid #dfe1e6; border-radius: 4px;
    background: white; color: #42526e; cursor: pointer; font-size: 0.82em;
    text-decoration: none; display: inline-flex; align-items: center; gap: 4px;
}
.tbtn:hover { background: #ebecf0; text-decoration: none; color: #172b4d; }
.tbtn-primary { background: #0052cc; color: white; border-color: #0052cc; }
.tbtn-primary:hover { background: #0065ff; }
#viewer-canvas {
    width: 100%; min-height: 65vh; position: relative; background: #fff;
}

/* ── Page tabs (diagram tabs) ── */
.page-tabs {
    display: flex; gap: 0; border-bottom: 2px solid #dfe1e6;
    margin-bottom: 20px;
}
.page-tab {
    padding: 8px 16px; font-size: 0.85em; color: #6b778c;
    border-bottom: 2px solid transparent; margin-bottom: -2px;
    cursor: default;
}
.page-tab.active { color: #0052cc; border-bottom-color: #0052cc; font-weight: 500; }

/* ── Page properties panel ── */
.page-properties {
    background: #f4f5f7; border: 1px solid #ebecf0; border-radius: 6px;
    padding: 16px 20px; margin-top: 20px;
}
.page-properties h3 {
    font-size: 0.85em; color: #6b778c; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px;
}
.page-properties table { width: 100%; border-collapse: collapse; }
.page-properties td {
    padding: 6px 0; font-size: 0.88em; border-bottom: 1px solid #ebecf0;
}
.page-properties td:first-child { color: #6b778c; width: 120px; font-weight: 500; }
.page-properties td:last-child { color: #172b4d; }

/* ── Search ── */
.search-box { margin-bottom: 24px; }
.search-box input {
    width: 100%; padding: 10px 14px; border: 2px solid #dfe1e6;
    border-radius: 6px; font-size: 14px; background: white; color: #172b4d;
}
.search-box input:focus { border-color: #4c9aff; outline: none; }
.no-results { color: #97a0af; padding: 40px; text-align: center; }

/* ── Responsive ── */
@media (max-width: 768px) {
    .sidebar { display: none; }
    .content-inner { padding: 20px 16px; }
    .container { padding: 20px 16px; }
    .topnav-links { display: none; }
    .topnav-search { width: 140px; }
}
"""

# SVG icon for the diagram logo
LOGO_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><line x1="10" y1="6.5" x2="14" y2="6.5"/><line x1="6.5" y1="10" x2="6.5" y2="14"/></svg>'

PAGE_HEADER = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — DrawIO Viewer</title>
<style>{style}</style>
</head>
<body>
<div class="topnav">
  <a href="{root}index.html" class="topnav-logo">{logo} Diagrams</a>
  <div class="topnav-links">
    <a href="{root}index.html">Spaces</a>
    <a href="{root}all.html">All Diagrams</a>
    <a href="{root}search.html">Search</a>
  </div>
  <div class="topnav-right">
    <input type="text" class="topnav-search" placeholder="Quick search..." onkeydown="if(event.key==='Enter')window.location.href='{root}search.html'">
  </div>
</div>
<main class="container">
"""

PAGE_FOOTER = """
</main>
</body>
</html>
"""

# Viewer pages use a different layout (sidebar + content)
VIEWER_HEADER = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — DrawIO Viewer</title>
<style>{style}</style>
</head>
<body>
<div class="topnav">
  <a href="index.html" class="topnav-logo">{logo} Diagrams</a>
  <div class="topnav-links">
    <a href="index.html">Spaces</a>
    <a href="all.html">All Diagrams</a>
    <a href="search.html">Search</a>
  </div>
  <div class="topnav-right">
    <input type="text" class="topnav-search" placeholder="Quick search..." onkeydown="if(event.key==='Enter')window.location.href='search.html'">
  </div>
</div>
<div class="layout">
{sidebar}
<div class="content">
<div class="content-inner">
"""

VIEWER_FOOTER = """
</div>
</div>
</div>
</body>
</html>
"""


def generate_index_page(spaces, total_count):
    """Generate the main index page HTML."""
    space_cards = ""
    for space, diagrams in spaces.items():
        display_name = space if space != "_root" else "Top-level"
        initial = display_name[0].upper()
        space_cards += f"""
<div class="space-card">
  <a href="space_{quote(space)}.html">
    <div class="space-icon">{_h(initial)}</div>
    <div class="space-name">{_h(display_name)}</div>
    <div class="space-count">{len(diagrams)} diagram{"s" if len(diagrams) != 1 else ""}</div>
  </a>
</div>"""

    return PAGE_HEADER.format(title="Home", style=STYLE, root="", logo=LOGO_SVG) + f"""
<div class="page-title">Diagram Spaces</div>
<div class="page-meta">
  <span class="page-meta-item">{total_count} diagrams across {len(spaces)} spaces</span>
</div>
<div class="stats">
  <div class="stat-box"><div class="stat-value">{total_count}</div><div class="stat-label">Total Diagrams</div></div>
  <div class="stat-box"><div class="stat-value">{len(spaces)}</div><div class="stat-label">Spaces</div></div>
</div>
<div class="space-grid">{space_cards}</div>
""" + PAGE_FOOTER


def generate_space_page(space, diagrams, all_diagram_names):
    """Generate a space listing page."""
    display_name = space if space != "_root" else "Top-level"
    cards = ""
    for d in sorted(diagrams, key=lambda x: x["name"].lower()):
        slug = _slug(d["name"])
        thumb = ""
        if d["has_png"]:
            thumb = f'<img class="diagram-thumb" src="png/{quote(d["space"])}/{quote(d["name"])}.png" alt="{_h(d["name"])}">'
        else:
            thumb = f'<div class="diagram-thumb-placeholder">DrawIO Diagram</div>'
        tab_info = ""
        if d.get("tab_names"):
            tab_info = f' &middot; {len(d["tab_names"])} tab{"s" if len(d["tab_names"]) != 1 else ""}'
        cards += f"""
<div class="diagram-card">
  <a href="view_{quote(d["space"])}_{slug}.html">
    {thumb}
    <div class="diagram-card-body">
      <div class="diagram-card-title">{_h(d["name"])}</div>
      <div class="diagram-card-meta">{_h(d.get("modified", ""))}{tab_info}</div>
    </div>
  </a>
</div>"""

    return PAGE_HEADER.format(title=display_name, style=STYLE, root="", logo=LOGO_SVG) + f"""
<div class="breadcrumb">
  <a href="index.html">Spaces</a><span class="sep">/</span><strong>{_h(display_name)}</strong>
</div>
<div class="page-title">{_h(display_name)}</div>
<div class="page-meta">
  <span class="page-meta-item">{len(diagrams)} diagram{"s" if len(diagrams) != 1 else ""}</span>
</div>
<div class="diagram-list">{cards}</div>
""" + PAGE_FOOTER


def _build_sidebar(space, space_diagrams, current_name):
    """Build the sidebar HTML for a viewer page."""
    display_space = space if space != "_root" else "Top-level"
    initial = display_space[0].upper()

    items = ""
    for d in sorted(space_diagrams, key=lambda x: x["name"].lower()):
        slug = _slug(d["name"])
        active = " active" if d["name"] == current_name else ""
        items += f'<a href="view_{quote(d["space"])}_{slug}.html" class="sidebar-item{active}" title="{_h(d["name"])}">{_h(d["name"])}</a>\n'

    return f"""
<div class="sidebar">
  <div class="sidebar-header">
    <span class="s-icon">{_h(initial)}</span>
    <a href="space_{quote(space)}.html" style="color:inherit;text-decoration:none;">{_h(display_space)}</a>
  </div>
  <input type="text" class="sidebar-search" placeholder="Filter pages..."
    oninput="var q=this.value.toLowerCase();document.querySelectorAll('.sidebar-item').forEach(function(el){{el.style.display=el.textContent.toLowerCase().indexOf(q)===-1?'none':'block';}});">
  <div class="sidebar-section">
    <div class="sidebar-section-title">Pages</div>
    {items}
  </div>
</div>"""


def generate_viewer_page(diagram, all_diagrams, spaces, prev_d=None, next_d=None):
    """Generate a single diagram viewer page with embedded diagrams.net viewer."""
    space = diagram["space"]
    name = diagram["name"]
    slug = _slug(name)
    xml_file = f"xml/{quote(space)}/{quote(name)}.drawio"
    display_space = space if space != "_root" else "Top-level"

    # Build clickthrough map
    ct_map = {}
    for d in all_diagrams:
        ct_map[d["name"].lower()] = f"view_{quote(d['space'])}_{_slug(d['name'])}.html"

    # Prev / next links
    prev_link = ""
    next_link = ""
    if prev_d:
        prev_link = f'<a href="view_{quote(prev_d["space"])}_{_slug(prev_d["name"])}.html" class="tbtn">&#x2190; Prev</a>'
    if next_d:
        next_link = f'<a href="view_{quote(next_d["space"])}_{_slug(next_d["name"])}.html" class="tbtn">Next &#x2192;</a>'

    # Sidebar with page tree for this space
    space_diagrams = spaces.get(space, [])
    sidebar_html = _build_sidebar(space, space_diagrams, name)

    # Tab indicators (diagram pages/tabs within the .drawio file)
    tab_html = ""
    tab_names = diagram.get("tab_names", [])
    if tab_names and len(tab_names) > 1:
        tabs = ""
        for i, tn in enumerate(tab_names):
            active = " active" if i == 0 else ""
            tabs += f'<span class="page-tab{active}">{_h(tn)}</span>'
        tab_html = f'<div class="page-tabs">{tabs}</div>'

    # Metadata
    modified = diagram.get("modified", "")
    modified_html = f'<span class="page-meta-item"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>{_h(modified)}</span>' if modified else ""
    tab_count = len(tab_names) if tab_names else 0
    tab_count_html = f'<span class="page-meta-item">{tab_count} page{"s" if tab_count != 1 else ""}</span>' if tab_count else ""

    # Page properties table
    props_rows = f'<tr><td>Space</td><td><a href="space_{quote(space)}.html">{_h(display_space)}</a></td></tr>'
    if modified:
        props_rows += f'<tr><td>Modified</td><td>{_h(modified)}</td></tr>'
    if tab_names:
        props_rows += f'<tr><td>Pages</td><td>{", ".join(_h(t) for t in tab_names)}</td></tr>'
    props_rows += f'<tr><td>Source</td><td><a href="{xml_file}" download>{_h(name)}.drawio</a></td></tr>'

    # Text excerpt for body content
    text_excerpt = diagram.get("text_content", "").strip()
    text_html = ""
    if text_excerpt:
        text_html = f'<div style="margin-top:20px;padding:16px 0;border-top:1px solid #ebecf0;color:#42526e;font-size:0.9em;"><strong style="color:#6b778c;font-size:0.82em;text-transform:uppercase;letter-spacing:0.5px;display:block;margin-bottom:6px;">Extracted Content</strong>{_h(text_excerpt)}</div>'

    return VIEWER_HEADER.format(title=name, style=STYLE, logo=LOGO_SVG, sidebar=sidebar_html) + f"""
<div class="breadcrumb">
  <a href="index.html">Spaces</a><span class="sep">/</span>
  <a href="space_{quote(space)}.html">{_h(display_space)}</a><span class="sep">/</span>
  <strong>{_h(name)}</strong>
</div>

<div class="page-title">{_h(name)}</div>
<div class="page-meta">
  <span class="page-meta-item"><span class="page-label">{_h(display_space)}</span></span>
  {modified_html}
  {tab_count_html}
</div>

{tab_html}

<div class="viewer-wrap">
  <div class="viewer-toolbar">
    <div class="left">
      {prev_link}
      {next_link}
    </div>
    <div class="right">
      <a href="{xml_file}" download="{_h(name)}.drawio" class="tbtn tbtn-primary">Download .drawio</a>
    </div>
  </div>
  <div id="viewer-canvas"></div>
</div>

<div class="page-properties">
  <h3>Page Properties</h3>
  <table>{props_rows}</table>
</div>
{text_html}

<script>
var CLICKTHROUGH = {json.dumps(ct_map)};

function initViewer() {{
  var canvas = document.getElementById('viewer-canvas');
  fetch('{xml_file}')
    .then(function(r) {{ return r.text(); }})
    .then(function(xml) {{
      var div = document.createElement('div');
      div.className = 'mxgraph';
      div.setAttribute('data-mxgraph', JSON.stringify({{
        highlight: '#0052cc', nav: true, resize: true,
        toolbar: 'pages zoom', edit: null, xml: xml
      }}));
      div.style.cssText = 'width:100%;min-height:60vh;';
      canvas.appendChild(div);

      var s = document.createElement('script');
      s.src = 'https://viewer.diagrams.net/js/viewer-static.min.js';
      s.onload = function() {{
        setTimeout(function() {{ attachClickthrough(canvas); }}, 1500);
      }};
      document.body.appendChild(s);
    }})
    .catch(function() {{
      canvas.innerHTML = '<div style="padding:60px;text-align:center;color:#97a0af;">Could not load diagram. <a href="' + '{xml_file}' + '" download>Download the .drawio file</a> and open in draw.io Desktop.</div>';
    }});
}}

function attachClickthrough(container) {{
  var svgs = container.querySelectorAll('svg');
  svgs.forEach(function(svg) {{
    svg.addEventListener('click', function(e) {{
      var el = e.target;
      for (var i = 0; i < 6 && el; i++) {{
        var t = (el.textContent || '').trim();
        if (t) {{
          var key = t.toLowerCase();
          if (CLICKTHROUGH[key]) {{ window.location.href = CLICKTHROUGH[key]; return; }}
          for (var k in CLICKTHROUGH) {{
            if (key.indexOf(k) !== -1 || k.indexOf(key) !== -1) {{
              window.location.href = CLICKTHROUGH[k]; return;
            }}
          }}
          break;
        }}
        el = el.parentElement;
      }}
    }});

    svg.addEventListener('mouseover', function(e) {{
      var el = e.target;
      for (var i = 0; i < 6 && el; i++) {{
        var t = (el.textContent || '').trim();
        if (t) {{
          var key = t.toLowerCase();
          var match = !!CLICKTHROUGH[key];
          if (!match) {{
            for (var k in CLICKTHROUGH) {{
              if (key.indexOf(k) !== -1 || k.indexOf(key) !== -1) {{ match = true; break; }}
            }}
          }}
          if (match) e.target.style.cursor = 'pointer';
          break;
        }}
        el = el.parentElement;
      }}
    }});
  }});
}}

document.addEventListener('keydown', function(e) {{
  if (e.target.tagName === 'INPUT') return;
  if (e.key === 'ArrowLeft') {{
    {"window.location.href = 'view_" + quote(prev_d["space"]) + "_" + _slug(prev_d["name"]) + ".html';" if prev_d else ""}
  }} else if (e.key === 'ArrowRight') {{
    {"window.location.href = 'view_" + quote(next_d["space"]) + "_" + _slug(next_d["name"]) + ".html';" if next_d else ""}
  }}
}});

document.addEventListener('DOMContentLoaded', initViewer);
</script>
""" + VIEWER_FOOTER


def generate_all_page(spaces, total_count):
    """Generate page listing all diagrams across all spaces."""
    cards = ""
    for space, diagrams in spaces.items():
        for d in sorted(diagrams, key=lambda x: x["name"].lower()):
            slug = _slug(d["name"])
            thumb = ""
            if d["has_png"]:
                thumb = f'<img class="diagram-thumb" src="png/{quote(d["space"])}/{quote(d["name"])}.png" alt="{_h(d["name"])}">'
            else:
                thumb = '<div class="diagram-thumb-placeholder">DrawIO Diagram</div>'

            cards += f"""
<div class="diagram-card">
  <a href="view_{quote(d["space"])}_{slug}.html">
    {thumb}
    <div class="diagram-card-body">
      <div class="diagram-card-title">{_h(d["name"])}</div>
      <div class="diagram-card-meta">{_h(d["space"])}{" &middot; " + _h(d.get("modified","")) if d.get("modified") else ""}</div>
    </div>
  </a>
</div>"""

    return PAGE_HEADER.format(title="All Diagrams", style=STYLE, root="", logo=LOGO_SVG) + f"""
<div class="breadcrumb"><a href="index.html">Spaces</a><span class="sep">/</span><strong>All Diagrams</strong></div>
<div class="page-title">All Diagrams</div>
<div class="page-meta"><span class="page-meta-item">{total_count} diagrams</span></div>
<div class="diagram-list">{cards}</div>
""" + PAGE_FOOTER


def generate_search_page(diagrams):
    """Generate a client-side search page."""
    # Build search index as JSON
    search_data = []
    for d in diagrams:
        slug = _slug(d["name"])
        search_data.append({
            "name": d["name"],
            "space": d["space"],
            "text": d["text_content"][:300],
            "url": f"view_{quote(d['space'])}_{slug}.html",
        })

    return PAGE_HEADER.format(title="Search", style=STYLE, root="", logo=LOGO_SVG) + f"""
<div class="breadcrumb"><a href="index.html">Spaces</a><span class="sep">/</span><strong>Search</strong></div>
<div class="page-title">Search Diagrams</div>
<div class="page-meta"><span class="page-meta-item">Search across all spaces and diagram content</span></div>
<div class="search-box">
  <input type="text" id="search-input" placeholder="Search diagram names and content..." autofocus>
</div>
<div id="results" class="diagram-list"></div>

<script>
var SEARCH_DATA = {json.dumps(search_data)};

document.getElementById('search-input').addEventListener('input', function() {{
  var q = this.value.toLowerCase().trim();
  var results = document.getElementById('results');
  if (!q) {{ results.innerHTML = ''; return; }}

  var matches = SEARCH_DATA.filter(function(d) {{
    return d.name.toLowerCase().indexOf(q) !== -1 ||
           d.space.toLowerCase().indexOf(q) !== -1 ||
           d.text.toLowerCase().indexOf(q) !== -1;
  }});

  if (!matches.length) {{
    results.innerHTML = '<div class="no-results">No diagrams found matching &ldquo;' + q + '&rdquo;</div>';
    return;
  }}

  results.innerHTML = matches.slice(0, 100).map(function(d) {{
    return '<div class="diagram-card"><a href="' + d.url + '">' +
      '<div class="diagram-thumb-placeholder">DrawIO</div>' +
      '<div class="diagram-card-body">' +
      '<div class="diagram-card-title">' + d.name + '</div>' +
      '<div class="diagram-card-meta">' + d.space + '</div>' +
      '</div></a></div>';
  }}).join('');
}});
</script>
""" + PAGE_FOOTER


def _h(text):
    """HTML-escape text."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _slug(name):
    """Make a URL-safe slug from a diagram name."""
    return re.sub(r"[^\w\-]", "_", name)


# =============================================================================
# Static site generation
# =============================================================================

def generate_static_site(diagrams_dir, output_dir):
    """
    Generate a complete static site from a directory of .drawio files.

    The output can be served by any static file server (nginx, python -m http.server, etc).
    """
    print(f"Scanning {diagrams_dir} for .drawio files...")
    diagrams = scan_diagrams(diagrams_dir)
    print(f"Found {len(diagrams)} diagrams")

    if not diagrams:
        print("No .drawio files found.")
        return

    spaces = build_index(diagrams)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    # Create xml/ and png/ directories for serving diagram files
    (output / "xml").mkdir(exist_ok=True)
    (output / "png").mkdir(exist_ok=True)

    # Copy .drawio files into xml/<space>/ structure
    for d in diagrams:
        space_dir = output / "xml" / d["space"]
        space_dir.mkdir(parents=True, exist_ok=True)
        src = Path(d["path"])
        dst = space_dir / f"{d['name']}.drawio"
        if not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime:
            dst.write_bytes(src.read_bytes())

        # Copy PNG if available
        if d["has_png"] and d["png_path"]:
            png_dir = output / "png" / d["space"]
            png_dir.mkdir(parents=True, exist_ok=True)
            png_src = Path(d["png_path"])
            png_dst = png_dir / f"{d['name']}.png"
            if not png_dst.exists() or png_src.stat().st_mtime > png_dst.stat().st_mtime:
                png_dst.write_bytes(png_src.read_bytes())

    # Generate index page
    index_html = generate_index_page(spaces, len(diagrams))
    (output / "index.html").write_text(index_html, encoding="utf-8")
    print(f"  Generated index.html")

    # Generate space pages
    for space, space_diagrams in spaces.items():
        html = generate_space_page(space, space_diagrams, [d["name"] for d in diagrams])
        (output / f"space_{space}.html").write_text(html, encoding="utf-8")

    print(f"  Generated {len(spaces)} space pages")

    # Generate viewer pages
    all_flat = []
    for space_diagrams in spaces.values():
        all_flat.extend(sorted(space_diagrams, key=lambda x: x["name"].lower()))

    for i, d in enumerate(all_flat):
        prev_d = all_flat[i - 1] if i > 0 else None
        next_d = all_flat[i + 1] if i < len(all_flat) - 1 else None
        slug = _slug(d["name"])
        html = generate_viewer_page(d, diagrams, spaces, prev_d, next_d)
        (output / f"view_{d['space']}_{slug}.html").write_text(html, encoding="utf-8")

    print(f"  Generated {len(all_flat)} viewer pages")

    # Generate all-diagrams page
    all_html = generate_all_page(spaces, len(diagrams))
    (output / "all.html").write_text(all_html, encoding="utf-8")

    # Generate search page
    search_html = generate_search_page(diagrams)
    (output / "search.html").write_text(search_html, encoding="utf-8")

    print(f"\nStatic site generated in: {output}")
    print(f"Serve with: python -m http.server -d {output} 8080")


# =============================================================================
# Live server mode (dev convenience)
# =============================================================================

class DiagramHandler(SimpleHTTPRequestHandler):
    """HTTP handler that serves the generated static site."""

    def __init__(self, *args, site_dir=None, **kwargs):
        self.site_dir = site_dir
        super().__init__(*args, directory=site_dir, **kwargs)

    def log_message(self, format, *args):
        # Quieter logging
        if '404' in str(args) or '500' in str(args):
            super().log_message(format, *args)


def run_server(diagrams_dir, port=8080, host="0.0.0.0"):
    """Generate site and start a live dev server."""
    import tempfile

    site_dir = tempfile.mkdtemp(prefix="drawio-server-")
    generate_static_site(diagrams_dir, site_dir)

    print(f"\nStarting server at http://localhost:{port}")
    print("Press Ctrl+C to stop\n")

    handler = lambda *args, **kwargs: DiagramHandler(*args, site_dir=site_dir, **kwargs)
    server = HTTPServer((host, port), handler)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
        server.server_close()

        # Clean up
        import shutil
        shutil.rmtree(site_dir, ignore_errors=True)


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Standalone DrawIO diagram viewer — like a static Confluence for diagrams"
    )
    parser.add_argument(
        "diagrams_dir",
        help="Directory containing .drawio files (scanned recursively)",
    )
    parser.add_argument(
        "--port", type=int, default=8080,
        help="Server port (default: 8080)",
    )
    parser.add_argument(
        "--host", default="0.0.0.0",
        help="Server host (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--generate-static", action="store_true",
        help="Generate static HTML site instead of running a server",
    )
    parser.add_argument(
        "--output", "-o", default="./drawio-site",
        help="Output directory for static generation (default: ./drawio-site)",
    )

    args = parser.parse_args()

    if not os.path.isdir(args.diagrams_dir):
        print(f"Error: {args.diagrams_dir} is not a directory")
        sys.exit(1)

    if args.generate_static:
        generate_static_site(args.diagrams_dir, args.output)
    else:
        run_server(args.diagrams_dir, port=args.port, host=args.host)


if __name__ == "__main__":
    main()
