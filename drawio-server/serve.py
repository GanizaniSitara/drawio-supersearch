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

    Returns list of dicts:
        {path, rel_path, name, space, text_content, has_png}
    """
    root = Path(root_dir).resolve()
    diagrams = []

    for drawio_path in sorted(root.rglob("*.drawio")):
        rel = drawio_path.relative_to(root)
        parts = rel.parts

        # Determine "space" from first subdirectory, or "root"
        space = parts[0] if len(parts) > 1 else "_root"
        name = drawio_path.stem

        # Extract text content from the drawio XML
        text_content = extract_text(str(drawio_path))

        # Check for companion PNG
        png_path = drawio_path.with_suffix(".png")
        # Also check in sibling images dir
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
            "has_png": png_path.exists(),
            "png_path": str(png_path) if png_path.exists() else None,
        })

    return diagrams


def extract_text(filepath):
    """Extract text labels from a .drawio file."""
    try:
        tree = etree.parse(filepath)
        root = tree.getroot()
        texts = []

        for diagram in root.findall(".//diagram"):
            name = diagram.get("name", "")
            if name:
                texts.append(name)

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

        return " ".join(texts)
    except Exception:
        return ""


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
    background: #f5f5f5; color: #333; line-height: 1.6;
}
a { color: #3498db; text-decoration: none; }
a:hover { text-decoration: underline; }
.container { max-width: 1400px; margin: 0 auto; padding: 20px; }
header {
    background: #1a5276; color: white; padding: 15px 0; margin-bottom: 30px;
}
header .container { display: flex; justify-content: space-between; align-items: center; }
header h1 { font-size: 1.4em; }
header h1 a { color: white; text-decoration: none; }
header nav a { color: #d4e6f1; margin-left: 20px; padding: 8px 15px; border-radius: 4px; }
header nav a:hover { background: rgba(255,255,255,0.1); text-decoration: none; }
.stats { display: flex; gap: 25px; margin-bottom: 30px; flex-wrap: wrap; }
.stat-box {
    background: white; padding: 18px 28px; border-radius: 8px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.08);
}
.stat-value { font-size: 2em; font-weight: 700; color: #1a5276; }
.stat-label { color: #7f8c8d; font-size: 0.85em; }
.breadcrumb { margin-bottom: 20px; font-size: 0.9em; }
.breadcrumb a { color: #3498db; }
.breadcrumb .sep { color: #bbb; margin: 0 8px; }
.space-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 15px; margin-bottom: 30px;
}
.space-card {
    background: white; padding: 20px; border-radius: 8px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.08); transition: transform 0.15s;
}
.space-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.12); }
.space-card a { color: #2c3e50; text-decoration: none; display: block; }
.space-name { font-size: 1.15em; font-weight: 600; }
.space-count { color: #7f8c8d; font-size: 0.85em; margin-top: 4px; }
.diagram-list {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 16px;
}
.diagram-card {
    background: white; border-radius: 8px; overflow: hidden;
    box-shadow: 0 2px 4px rgba(0,0,0,0.08); transition: transform 0.15s;
}
.diagram-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.12); }
.diagram-card a { text-decoration: none; color: inherit; display: block; }
.diagram-thumb {
    width: 100%; height: 180px; object-fit: contain;
    background: #f8f9fa; border-bottom: 1px solid #eee;
}
.diagram-thumb-placeholder {
    width: 100%; height: 180px; display: flex; align-items: center;
    justify-content: center; background: #f0f4f8; border-bottom: 1px solid #eee;
    color: #95a5a6; font-size: 0.9em;
}
.diagram-card-body { padding: 14px; }
.diagram-card-title { font-weight: 600; color: #2c3e50; margin-bottom: 4px; word-break: break-word; }
.diagram-card-meta { font-size: 0.8em; color: #95a5a6; }

/* Viewer page */
.viewer-wrap {
    background: white; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.08);
    overflow: hidden;
}
.viewer-toolbar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 16px; background: #f8f9fa; border-bottom: 1px solid #eee;
    flex-wrap: wrap; gap: 8px;
}
.viewer-toolbar .left, .viewer-toolbar .right { display: flex; align-items: center; gap: 6px; }
.tbtn {
    padding: 5px 12px; border: 1px solid #ddd; border-radius: 4px;
    background: white; color: #333; cursor: pointer; font-size: 0.82em;
    text-decoration: none; display: inline-flex; align-items: center; gap: 4px;
}
.tbtn:hover { background: #e8e8e8; border-color: #bbb; text-decoration: none; }
.tbtn-primary { background: #1a5276; color: white; border-color: #1a5276; }
.tbtn-primary:hover { background: #154360; }
#viewer-canvas {
    width: 100%; min-height: 70vh; position: relative; background: #fafafa;
}
.viewer-info { padding: 20px; border-top: 1px solid #eee; }
.viewer-info h2 { color: #2c3e50; margin-bottom: 10px; }
.viewer-info .meta { font-size: 0.85em; color: #7f8c8d; }

/* Search */
.search-box { margin-bottom: 25px; }
.search-box input {
    width: 100%; padding: 12px 16px; border: 2px solid #ddd;
    border-radius: 8px; font-size: 15px; background: white;
}
.search-box input:focus { border-color: #3498db; outline: none; }
.no-results { color: #95a5a6; padding: 40px; text-align: center; }
"""

PAGE_HEADER = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — DrawIO Viewer</title>
<style>{style}</style>
</head>
<body>
<header>
<div class="container">
<h1><a href="{root}index.html">DrawIO Diagram Viewer</a></h1>
<nav>
<a href="{root}index.html">Spaces</a>
<a href="{root}all.html">All Diagrams</a>
<a href="{root}search.html">Search</a>
</nav>
</div>
</header>
<main class="container">
"""

PAGE_FOOTER = """
</main>
</body>
</html>
"""


def generate_index_page(spaces, total_count):
    """Generate the main index page HTML."""
    space_cards = ""
    for space, diagrams in spaces.items():
        display_name = space if space != "_root" else "Top-level"
        space_cards += f"""
<div class="space-card">
  <a href="space_{quote(space)}.html">
    <div class="space-name">{_h(display_name)}</div>
    <div class="space-count">{len(diagrams)} diagram{"s" if len(diagrams) != 1 else ""}</div>
  </a>
</div>"""

    return PAGE_HEADER.format(title="Home", style=STYLE, root="") + f"""
<div class="stats">
  <div class="stat-box"><div class="stat-value">{total_count}</div><div class="stat-label">Total Diagrams</div></div>
  <div class="stat-box"><div class="stat-value">{len(spaces)}</div><div class="stat-label">Spaces</div></div>
</div>
<h2 style="color:#2c3e50;margin-bottom:18px;">Browse by Space</h2>
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

        cards += f"""
<div class="diagram-card">
  <a href="view_{quote(d["space"])}_{slug}.html">
    {thumb}
    <div class="diagram-card-body">
      <div class="diagram-card-title">{_h(d["name"])}</div>
      <div class="diagram-card-meta">{_h(d["space"])}</div>
    </div>
  </a>
</div>"""

    return PAGE_HEADER.format(title=display_name, style=STYLE, root="") + f"""
<div class="breadcrumb">
  <a href="index.html">Home</a><span class="sep">/</span><strong>{_h(display_name)}</strong>
</div>
<h2 style="color:#2c3e50;margin-bottom:18px;">{_h(display_name)} — {len(diagrams)} diagrams</h2>
<div class="diagram-list">{cards}</div>
""" + PAGE_FOOTER


def generate_viewer_page(diagram, all_diagrams, prev_d=None, next_d=None):
    """Generate a single diagram viewer page with embedded diagrams.net viewer."""
    space = diagram["space"]
    name = diagram["name"]
    slug = _slug(name)
    xml_file = f"xml/{quote(space)}/{quote(name)}.drawio"

    # Build clickthrough map for this diagram
    ct_map = {}
    for d in all_diagrams:
        ct_map[d["name"].lower()] = f"view_{quote(d['space'])}_{_slug(d['name'])}.html"

    prev_link = ""
    next_link = ""
    if prev_d:
        prev_link = f'<a href="view_{quote(prev_d["space"])}_{_slug(prev_d["name"])}.html" class="tbtn">&#x2190; Prev</a>'
    if next_d:
        next_link = f'<a href="view_{quote(next_d["space"])}_{_slug(next_d["name"])}.html" class="tbtn">Next &#x2192;</a>'

    return PAGE_HEADER.format(title=name, style=STYLE, root="") + f"""
<div class="breadcrumb">
  <a href="index.html">Home</a><span class="sep">/</span>
  <a href="space_{quote(space)}.html">{_h(space)}</a><span class="sep">/</span>
  <strong>{_h(name)}</strong>
</div>

<div class="viewer-wrap">
  <div class="viewer-toolbar">
    <div class="left">
      {prev_link}
      {next_link}
    </div>
    <div class="right">
      <a href="{xml_file}" download="{_h(name)}.drawio" class="tbtn tbtn-primary">Download .drawio</a>
      <a href="space_{quote(space)}.html" class="tbtn">Back to space</a>
    </div>
  </div>
  <div id="viewer-canvas"></div>
  <div class="viewer-info">
    <h2>{_h(name)}</h2>
    <div class="meta">Space: <a href="space_{quote(space)}.html">{_h(space)}</a></div>
  </div>
</div>

<script>
var CLICKTHROUGH = {json.dumps(ct_map)};

function initViewer() {{
  var canvas = document.getElementById('viewer-canvas');
  // Fetch the DrawIO XML and render with diagrams.net viewer
  fetch('{xml_file}')
    .then(function(r) {{ return r.text(); }})
    .then(function(xml) {{
      var div = document.createElement('div');
      div.className = 'mxgraph';
      div.setAttribute('data-mxgraph', JSON.stringify({{
        highlight: '#0000ff', nav: false, resize: true,
        toolbar: null, edit: null, xml: xml
      }}));
      div.style.cssText = 'width:100%;min-height:65vh;';
      canvas.appendChild(div);

      var s = document.createElement('script');
      s.src = 'https://viewer.diagrams.net/js/viewer-static.min.js';
      s.onload = function() {{
        setTimeout(function() {{ attachClickthrough(canvas); }}, 1500);
      }};
      document.body.appendChild(s);
    }})
    .catch(function() {{
      canvas.innerHTML = '<div style="padding:60px;text-align:center;color:#999;">Could not load diagram. <a href="' + '{xml_file}' + '" download>Download the .drawio file</a> and open in draw.io Desktop.</div>';
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

// Keyboard: left/right arrows for navigation
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
""" + PAGE_FOOTER


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
      <div class="diagram-card-meta">{_h(d["space"])}</div>
    </div>
  </a>
</div>"""

    return PAGE_HEADER.format(title="All Diagrams", style=STYLE, root="") + f"""
<div class="breadcrumb"><a href="index.html">Home</a><span class="sep">/</span><strong>All Diagrams</strong></div>
<h2 style="color:#2c3e50;margin-bottom:18px;">All Diagrams ({total_count})</h2>
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

    return PAGE_HEADER.format(title="Search", style=STYLE, root="") + f"""
<div class="breadcrumb"><a href="index.html">Home</a><span class="sep">/</span><strong>Search</strong></div>
<h2 style="color:#2c3e50;margin-bottom:18px;">Search Diagrams</h2>
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
        html = generate_viewer_page(d, diagrams, prev_d, next_d)
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
