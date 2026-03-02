#!/usr/bin/env python3
"""
DrawIO Browser - Flask Web Application

Browse and search DrawIO diagrams extracted from Confluence.
Features:
- Browse by Confluence space
- Full-text search (diagram names, page titles, content)
- PNG preview with download links
- Edit Local / Edit Web buttons
- Whoosh index for fast content search
"""

import os
import sys
import json
import sqlite3
import re
from urllib.parse import unquote
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, make_response

from whoosh.index import create_in, open_dir, exists_in
from whoosh.fields import Schema, TEXT, ID, STORED
from whoosh.qparser import MultifieldParser, OrGroup
from whoosh.analysis import StemmingAnalyzer

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from extractor.config import Settings
from extractor.drawio_tools import decode_diagram_data

try:
    from lxml import etree
except ImportError:
    import xml.etree.ElementTree as etree

app = Flask(__name__)

# Global settings (loaded on startup)
_settings = None


def get_settings():
    """Get application settings."""
    global _settings
    if _settings is None:
        _settings = Settings.get()
    return _settings


# =============================================================================
# DrawIO Text Extraction
# =============================================================================

def extract_text_from_drawio(filepath):
    """
    Extract all text content from a .drawio file.
    Returns concatenated text from all mxCell value attributes.
    """
    try:
        tree = etree.parse(filepath)
        root = tree.getroot()

        texts = []

        # Get diagram elements
        for diagram in root.findall('.//diagram'):
            diagram_name = diagram.get('name', '')
            if diagram_name:
                texts.append(diagram_name)

            # Check if content is compressed
            content = diagram.text
            if content and content.strip():
                # Decode compressed content
                decoded = decode_diagram_data(content.strip())
                if decoded:
                    try:
                        # Parse the decoded XML
                        inner_root = etree.fromstring(decoded.encode('utf-8'))
                        # Extract text from mxCell value attributes
                        for cell in inner_root.findall('.//mxCell'):
                            value = cell.get('value', '')
                            if value:
                                # Strip HTML tags
                                clean_text = re.sub(r'<[^>]+>', ' ', value)
                                clean_text = re.sub(r'\s+', ' ', clean_text).strip()
                                if clean_text:
                                    texts.append(clean_text)
                    except Exception:
                        pass

            # Also check for uncompressed mxGraphModel
            for model in diagram.findall('.//mxGraphModel'):
                for cell in model.findall('.//mxCell'):
                    value = cell.get('value', '')
                    if value:
                        clean_text = re.sub(r'<[^>]+>', ' ', value)
                        clean_text = re.sub(r'\s+', ' ', clean_text).strip()
                        if clean_text:
                            texts.append(clean_text)

        return ' '.join(texts)
    except Exception:
        return ''


# =============================================================================
# Database Functions
# =============================================================================

def get_db_path():
    """Get database path from settings."""
    return get_settings()['database_path']


def get_index_dir():
    """Get Whoosh index directory from settings."""
    return get_settings()['index_directory']


def init_db():
    """Initialize SQLite database."""
    db_path = get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS diagrams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            space_key TEXT NOT NULL,
            diagram_name TEXT NOT NULL,
            page_title TEXT,
            page_id TEXT,
            confluence_page_url TEXT,
            author TEXT,
            author_display TEXT,
            created_date TEXT,
            file_size INTEGER,
            drawio_path TEXT,
            image_path TEXT,
            metadata_path TEXT,
            content_text TEXT
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_space ON diagrams(space_key)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_name ON diagrams(diagram_name)')

    # Applications tables
    c.execute('''
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS diagram_applications (
            diagram_id INTEGER NOT NULL,
            application_id INTEGER NOT NULL,
            PRIMARY KEY (diagram_id, application_id),
            FOREIGN KEY (diagram_id) REFERENCES diagrams(id),
            FOREIGN KEY (application_id) REFERENCES applications(id)
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_da_diagram ON diagram_applications(diagram_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_da_app ON diagram_applications(application_id)')

    conn.commit()
    conn.close()


def get_db():
    """Get database connection."""
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def db_is_populated():
    """Check if database has data."""
    db_path = get_db_path()
    if not os.path.exists(db_path):
        return False
    conn = get_db()
    count = conn.execute('SELECT COUNT(*) FROM diagrams').fetchone()[0]
    conn.close()
    return count > 0


# =============================================================================
# Whoosh Index Functions
# =============================================================================

def get_schema():
    """Define Whoosh schema."""
    return Schema(
        id=ID(stored=True, unique=True),
        space_key=TEXT(stored=True),
        diagram_name=TEXT(stored=True, analyzer=StemmingAnalyzer()),
        page_title=TEXT(stored=True, analyzer=StemmingAnalyzer()),
        author=TEXT(stored=True),
        content=TEXT(analyzer=StemmingAnalyzer())
    )


def init_index():
    """Initialize Whoosh index directory."""
    index_dir = get_index_dir()
    if not os.path.exists(index_dir):
        os.makedirs(index_dir)
        create_in(index_dir, get_schema())


def index_is_populated():
    """Check if Whoosh index exists and has documents."""
    index_dir = get_index_dir()
    if not exists_in(index_dir):
        return False
    try:
        ix = open_dir(index_dir)
        with ix.searcher() as searcher:
            return searcher.doc_count() > 0
    except Exception:
        return False


# =============================================================================
# Application Helpers
# =============================================================================

def load_applications():
    """Read application names from the configured file.
    Skips blank lines and lines starting with #."""
    settings = get_settings()
    filepath = settings.get('applications_file', '')
    if not filepath or not os.path.exists(filepath):
        return []
    names = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                names.append(line)
    return names


def applications_enabled():
    """Check whether an applications file is configured and exists."""
    settings = get_settings()
    filepath = settings.get('applications_file', '')
    return bool(filepath) and os.path.exists(filepath)


# =============================================================================
# Indexing Functions
# =============================================================================

def index_all_diagrams(progress_callback=None):
    """
    Scan all diagrams and populate database + Whoosh index.
    """
    settings = get_settings()
    metadata_dir = settings['metadata_directory']
    diagrams_dir = settings['diagrams_directory']
    images_dir = settings['images_directory']

    init_db()
    init_index()

    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM diagram_applications')
    c.execute('DELETE FROM applications')
    c.execute('DELETE FROM diagrams')  # Clear existing data

    # Load applications and build lookup map
    app_names = load_applications()
    app_id_map = {}  # {lowercase_name: id}
    for name in app_names:
        c.execute('INSERT INTO applications (name) VALUES (?)', (name,))
        app_id_map[name.lower()] = c.lastrowid

    ix = open_dir(get_index_dir())
    writer = ix.writer()

    # Get all spaces from metadata directory
    if not os.path.exists(metadata_dir):
        return 0

    spaces = [d for d in os.listdir(metadata_dir)
              if os.path.isdir(os.path.join(metadata_dir, d))]

    total_indexed = 0

    for space_idx, space_key in enumerate(spaces):
        metadata_space_dir = os.path.join(metadata_dir, space_key)

        if progress_callback:
            progress_callback(space_idx + 1, len(spaces), space_key, total_indexed)

        # Process each metadata file
        for meta_file in os.listdir(metadata_space_dir):
            if not meta_file.endswith('.json'):
                continue

            meta_path = os.path.join(metadata_space_dir, meta_file)

            try:
                with open(meta_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)

                # Extract info from metadata
                title = meta.get('title', '')
                diagram_name = title.replace('.png', '') if title.endswith('.png') else title

                # Extract page title and URL from webui link
                # Check both DrawIO format (_links.webui) and Lucidchart format (page_link)
                webui = meta.get('_links', {}).get('webui', '') or meta.get('page_link', '')
                page_title = meta.get('page_title', '')  # Lucidchart saves this directly
                confluence_page_url = ''

                # Extract page ID from container (DrawIO) or direct page_id (Lucidchart)
                container = meta.get('_expandable', {}).get('container', '')
                page_id = container.split('/')[-1] if container else meta.get('page_id', '')

                if webui:
                    if 'viewpage.action' in webui:
                        # pageId-based URL — keep the full path including query params
                        confluence_page_url = webui
                    else:
                        # Display-based URL — strip query params
                        confluence_page_url = webui.split('?')[0]
                    # Extract page title from the path if not already set
                    if not page_title and '/display/' in webui:
                        parts = webui.split('/')
                        if len(parts) >= 4:
                            page_part = parts[3].split('?')[0]
                            page_title = unquote(page_part.replace('+', ' '))
                elif page_id:
                    # Fallback: construct URL from page_id if webui link not available
                    confluence_page_url = f'/pages/viewpage.action?pageId={page_id}'

                # Author info
                version = meta.get('version', {})
                author_info = version.get('by', {})
                author = author_info.get('username', '')
                author_display = author_info.get('displayName', author)

                # Date
                created_date = version.get('when', '')[:10] if version.get('when') else ''

                # File size
                file_size = meta.get('extensions', {}).get('fileSize', 0)

                # Build file paths
                drawio_path = os.path.join(diagrams_dir, space_key, f'{diagram_name}.drawio')
                image_path = os.path.join(images_dir, space_key, f'{diagram_name}.png')

                # Extract text content from .drawio file, or use body_text from metadata (Lucidchart)
                content_text = ''
                if os.path.exists(drawio_path):
                    content_text = extract_text_from_drawio(drawio_path)
                if not content_text:
                    # Fallback to body_text from metadata (used by Lucidchart screenshotter)
                    content_text = meta.get('body_text', '')

                # Insert into database
                c.execute('''
                    INSERT INTO diagrams
                    (space_key, diagram_name, page_title, page_id, confluence_page_url,
                     author, author_display, created_date, file_size, drawio_path,
                     image_path, metadata_path, content_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (space_key, diagram_name, page_title, page_id, confluence_page_url,
                      author, author_display, created_date, file_size, drawio_path,
                      image_path, meta_path, content_text))

                diagram_id = c.lastrowid

                # Match diagram to applications
                searchable_text = ' '.join([
                    diagram_name or '',
                    page_title or '',
                    content_text or ''
                ]).lower()
                for app_lower, app_db_id in app_id_map.items():
                    if app_lower in searchable_text:
                        c.execute(
                            'INSERT INTO diagram_applications (diagram_id, application_id) VALUES (?, ?)',
                            (diagram_id, app_db_id)
                        )

                # Add to Whoosh index
                writer.add_document(
                    id=str(diagram_id),
                    space_key=space_key,
                    diagram_name=diagram_name,
                    page_title=page_title,
                    author=author_display,
                    content=content_text
                )

                total_indexed += 1

            except Exception as e:
                print(f"Error processing {meta_path}: {e}")
                continue

    conn.commit()
    conn.close()
    writer.commit()

    return total_indexed


# =============================================================================
# Flask Routes
# =============================================================================

@app.route('/')
def index():
    """Home page - show spaces or applications overview."""
    if not db_is_populated():
        return render_template('needs_index.html')

    sort = request.args.get('sort', 'count')  # 'count' or 'alpha'
    view = request.args.get('view', 'spaces')  # 'spaces' or 'apps'
    has_apps = applications_enabled()

    conn = get_db()
    total = conn.execute('SELECT COUNT(*) FROM diagrams').fetchone()[0]

    if view == 'apps' and has_apps:
        if sort == 'alpha':
            applications = conn.execute('''
                SELECT a.id, a.name, COUNT(da.diagram_id) as count
                FROM applications a
                LEFT JOIN diagram_applications da ON a.id = da.application_id
                GROUP BY a.id
                ORDER BY a.name ASC
            ''').fetchall()
        else:
            applications = conn.execute('''
                SELECT a.id, a.name, COUNT(da.diagram_id) as count
                FROM applications a
                LEFT JOIN diagram_applications da ON a.id = da.application_id
                GROUP BY a.id
                ORDER BY count DESC
            ''').fetchall()
        conn.close()
        return render_template('index.html', applications=applications, spaces=[],
                             total=total, sort=sort, view=view, has_apps=has_apps)
    else:
        if sort == 'alpha':
            spaces = conn.execute('''
                SELECT space_key, COUNT(*) as count
                FROM diagrams
                GROUP BY space_key
                ORDER BY space_key ASC
            ''').fetchall()
        else:
            spaces = conn.execute('''
                SELECT space_key, COUNT(*) as count
                FROM diagrams
                GROUP BY space_key
                ORDER BY count DESC
            ''').fetchall()
        conn.close()
        return render_template('index.html', spaces=spaces, applications=[],
                             total=total, sort=sort, view='spaces', has_apps=has_apps)


@app.route('/application/<int:app_id>')
def application_view(app_id):
    """View all diagrams matched to an application."""
    conn = get_db()

    application = conn.execute(
        'SELECT * FROM applications WHERE id = ?', (app_id,)
    ).fetchone()
    if not application:
        conn.close()
        return "Application not found", 404

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    diagrams = conn.execute('''
        SELECT d.* FROM diagrams d
        JOIN diagram_applications da ON d.id = da.diagram_id
        WHERE da.application_id = ?
        ORDER BY d.diagram_name
        LIMIT ? OFFSET ?
    ''', (app_id, per_page, offset)).fetchall()

    total = conn.execute('''
        SELECT COUNT(*) FROM diagram_applications WHERE application_id = ?
    ''', (app_id,)).fetchone()[0]

    conn.close()

    total_pages = (total + per_page - 1) // per_page

    return render_template('application.html',
                         application=application,
                         diagrams=diagrams,
                         page=page,
                         total_pages=total_pages,
                         total=total)


@app.route('/space/<space_key>')
def space_view(space_key):
    """View all diagrams in a space."""
    conn = get_db()

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    diagrams = conn.execute('''
        SELECT * FROM diagrams
        WHERE space_key = ?
        ORDER BY diagram_name
        LIMIT ? OFFSET ?
    ''', (space_key, per_page, offset)).fetchall()

    total = conn.execute(
        'SELECT COUNT(*) FROM diagrams WHERE space_key = ?',
        (space_key,)
    ).fetchone()[0]

    conn.close()

    total_pages = (total + per_page - 1) // per_page

    return render_template('space.html',
                         space_key=space_key,
                         diagrams=diagrams,
                         page=page,
                         total_pages=total_pages,
                         total=total)


@app.route('/diagram/<int:diagram_id>')
def diagram_view(diagram_id):
    """View single diagram details."""
    conn = get_db()
    diagram = conn.execute(
        'SELECT * FROM diagrams WHERE id = ?',
        (diagram_id,)
    ).fetchone()

    if not diagram:
        conn.close()
        return "Diagram not found", 404

    # Get prev/next diagrams within the same space for carousel navigation
    space_key = diagram['space_key']
    diagram_name = diagram['diagram_name']

    # Previous diagram (alphabetically before current)
    prev_diagram = conn.execute('''
        SELECT id FROM diagrams
        WHERE space_key = ? AND diagram_name < ?
        ORDER BY diagram_name DESC
        LIMIT 1
    ''', (space_key, diagram_name)).fetchone()

    # Next diagram (alphabetically after current)
    next_diagram = conn.execute('''
        SELECT id FROM diagrams
        WHERE space_key = ? AND diagram_name > ?
        ORDER BY diagram_name ASC
        LIMIT 1
    ''', (space_key, diagram_name)).fetchone()

    # Get position info for display (e.g., "5 of 42")
    position = conn.execute('''
        SELECT COUNT(*) FROM diagrams
        WHERE space_key = ? AND diagram_name <= ?
    ''', (space_key, diagram_name)).fetchone()[0]

    total_in_space = conn.execute('''
        SELECT COUNT(*) FROM diagrams WHERE space_key = ?
    ''', (space_key,)).fetchone()[0]

    conn.close()

    # Get settings for template
    settings = get_settings()
    confluence_url = settings.get('confluence_url', '')
    show_edit_buttons = settings.get('show_edit_buttons', True)

    return render_template('diagram.html',
                         diagram=diagram,
                         confluence_url=confluence_url,
                         show_edit_buttons=show_edit_buttons,
                         prev_id=prev_diagram['id'] if prev_diagram else None,
                         next_id=next_diagram['id'] if next_diagram else None,
                         position=position,
                         total_in_space=total_in_space)


@app.route('/search')
def search():
    """Search diagrams."""
    query = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)
    group_by = request.args.get('group', '')  # 'space' to group by space
    per_page = 50

    if not query:
        return render_template('search.html', results=[], query='', total=0, group_by=group_by)

    if not index_is_populated():
        return render_template('search.html', results=[], query=query,
                             error="Index not built. Run indexing first.", group_by=group_by)

    try:
        ix = open_dir(get_index_dir())

        with ix.searcher() as searcher:
            parser = MultifieldParser(
                ['diagram_name', 'page_title', 'content', 'author'],
                schema=ix.schema,
                group=OrGroup
            )
            q = parser.parse(query)

            results = searcher.search(q, limit=1000)

            # Collect all result IDs with their space keys for grouping
            result_data = []
            for hit in results:
                result_data.append({
                    'id': int(hit['id']),
                    'space_key': hit['space_key']
                })

            total = len(result_data)

            # For grouped view, get all results (no pagination)
            # For flat view, apply pagination
            if group_by == 'space':
                page_ids = [r['id'] for r in result_data]
            else:
                start = (page - 1) * per_page
                end = start + per_page
                page_ids = [r['id'] for r in result_data[start:end]]
    except Exception as e:
        return render_template('search.html', results=[], query=query,
                             error=str(e), group_by=group_by)

    if page_ids:
        conn = get_db()
        placeholders = ','.join('?' * len(page_ids))
        diagrams = conn.execute(
            f'SELECT * FROM diagrams WHERE id IN ({placeholders})',
            page_ids
        ).fetchall()
        conn.close()
    else:
        diagrams = []

    total_pages = (total + per_page - 1) // per_page

    # Group results by space if requested
    grouped_results = None
    group_sort = request.args.get('sort', 'count')  # 'count' or 'alpha'
    if group_by == 'space' and diagrams:
        from collections import OrderedDict
        grouped = {}
        for d in diagrams:
            space = d['space_key']
            if space not in grouped:
                grouped[space] = []
            grouped[space].append(d)
        # Sort by count or alphabetically
        if group_sort == 'alpha':
            grouped_results = OrderedDict(sorted(grouped.items(), key=lambda x: x[0]))
        else:
            grouped_results = OrderedDict(sorted(grouped.items(), key=lambda x: -len(x[1])))

    return render_template('search.html',
                         results=diagrams,
                         grouped_results=grouped_results,
                         query=query,
                         total=total,
                         page=page,
                         total_pages=total_pages,
                         group_by=group_by,
                         group_sort=group_sort)


@app.route('/image/<space_key>/<path:filename>')
def serve_image(space_key, filename):
    """Serve diagram image."""
    settings = get_settings()
    image_path = os.path.join(settings['images_directory'], space_key, filename)
    if os.path.exists(image_path):
        return send_file(image_path, mimetype='image/png')
    return "Image not found", 404


@app.route('/download/<space_key>/<path:filename>')
def download_drawio(space_key, filename):
    """Download .drawio file with CORS support for draw.io web editor."""
    settings = get_settings()
    diagrams_dir = settings['diagrams_directory']

    # Handle both with and without .drawio extension
    if not filename.endswith('.drawio'):
        filename = f"{filename}.drawio"

    drawio_path = os.path.join(diagrams_dir, space_key, filename)
    if os.path.exists(drawio_path):
        # Read file and create response with CORS headers
        with open(drawio_path, 'rb') as f:
            content = f.read()
        response = make_response(content)
        response.headers['Content-Type'] = 'application/xml'
        response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        # CORS headers for draw.io web editor
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response
    return "File not found", 404


@app.route('/download/<space_key>/<path:filename>', methods=['OPTIONS'])
def download_drawio_options(space_key, filename):
    """Handle CORS preflight for download endpoint."""
    response = make_response()
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response


# =============================================================================
# Interactive DrawIO Viewer Routes
# =============================================================================

@app.route('/api/drawio-xml/<space_key>/<path:filename>')
def api_drawio_xml(space_key, filename):
    """Serve raw DrawIO XML content for the interactive viewer."""
    settings = get_settings()
    diagrams_dir = settings['diagrams_directory']

    if not filename.endswith('.drawio'):
        filename = f"{filename}.drawio"

    drawio_path = os.path.join(diagrams_dir, space_key, filename)
    if not os.path.exists(drawio_path):
        return jsonify({'error': 'Diagram not found'}), 404

    with open(drawio_path, 'r', encoding='utf-8') as f:
        xml_content = f.read()

    response = make_response(xml_content)
    response.headers['Content-Type'] = 'application/xml'
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


@app.route('/api/drawio-xml/c4/<space_key>/<path:filename>')
def api_c4_drawio_xml(space_key, filename):
    """Serve raw C4 DrawIO XML content for the interactive viewer."""
    c4_dir = _get_c4_dir()

    if not filename.endswith('.drawio'):
        filename = f"{filename}.c4.drawio"

    filepath = os.path.join(c4_dir, space_key, filename)
    if not os.path.exists(filepath):
        return jsonify({'error': 'C4 diagram not found'}), 404

    with open(filepath, 'r', encoding='utf-8') as f:
        xml_content = f.read()

    response = make_response(xml_content)
    response.headers['Content-Type'] = 'application/xml'
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


@app.route('/viewer/<int:diagram_id>')
def viewer(diagram_id):
    """Interactive DrawIO viewer with live rendering and clickthrough."""
    conn = get_db()
    diagram = conn.execute(
        'SELECT * FROM diagrams WHERE id = ?', (diagram_id,)
    ).fetchone()

    if not diagram:
        conn.close()
        return "Diagram not found", 404

    space_key = diagram['space_key']
    diagram_name = diagram['diagram_name']

    # Check if .drawio file exists (needed for interactive viewer)
    settings = get_settings()
    drawio_path = os.path.join(
        settings['diagrams_directory'], space_key, f'{diagram_name}.drawio'
    )
    has_drawio = os.path.exists(drawio_path)

    # Get prev/next for carousel
    prev_diagram = conn.execute('''
        SELECT id FROM diagrams
        WHERE space_key = ? AND diagram_name < ?
        ORDER BY diagram_name DESC LIMIT 1
    ''', (space_key, diagram_name)).fetchone()

    next_diagram = conn.execute('''
        SELECT id FROM diagrams
        WHERE space_key = ? AND diagram_name > ?
        ORDER BY diagram_name ASC LIMIT 1
    ''', (space_key, diagram_name)).fetchone()

    position = conn.execute('''
        SELECT COUNT(*) FROM diagrams
        WHERE space_key = ? AND diagram_name <= ?
    ''', (space_key, diagram_name)).fetchone()[0]

    total_in_space = conn.execute('''
        SELECT COUNT(*) FROM diagrams WHERE space_key = ?
    ''', (space_key,)).fetchone()[0]

    # Build a map of all diagram names for clickthrough resolution
    all_diagrams = conn.execute('''
        SELECT id, diagram_name, space_key FROM diagrams
        ORDER BY diagram_name
    ''').fetchall()

    conn.close()

    # Build clickthrough lookup: shape label text (lowercased) -> diagram ID
    # Prioritize same-space diagrams
    clickthrough_map = {}
    for d in all_diagrams:
        name = d['diagram_name'].lower().strip()
        if name not in clickthrough_map or d['space_key'] == space_key:
            clickthrough_map[name] = d['id']

    confluence_url = settings.get('confluence_url', '')

    return render_template('viewer.html',
                         diagram=diagram,
                         has_drawio=has_drawio,
                         confluence_url=confluence_url,
                         prev_id=prev_diagram['id'] if prev_diagram else None,
                         next_id=next_diagram['id'] if next_diagram else None,
                         position=position,
                         total_in_space=total_in_space,
                         clickthrough_map=json.dumps(clickthrough_map))


@app.route('/api/search-diagram')
def api_search_diagram():
    """API to find a diagram by name for clickthrough resolution."""
    name = request.args.get('name', '').strip()
    if not name:
        return jsonify({'found': False})

    conn = get_db()
    # Exact match first
    diagram = conn.execute(
        'SELECT id, diagram_name, space_key FROM diagrams WHERE LOWER(diagram_name) = ?',
        (name.lower(),)
    ).fetchone()

    if not diagram:
        # Fuzzy match - contains
        diagram = conn.execute(
            'SELECT id, diagram_name, space_key FROM diagrams WHERE LOWER(diagram_name) LIKE ? LIMIT 1',
            (f'%{name.lower()}%',)
        ).fetchone()

    conn.close()

    if diagram:
        return jsonify({
            'found': True,
            'id': diagram['id'],
            'name': diagram['diagram_name'],
            'space': diagram['space_key'],
            'url': f'/viewer/{diagram["id"]}'
        })
    return jsonify({'found': False})


@app.route('/api/stats')
def api_stats():
    """API endpoint for statistics."""
    conn = get_db()

    total = conn.execute('SELECT COUNT(*) FROM diagrams').fetchone()[0]
    spaces = conn.execute('SELECT COUNT(DISTINCT space_key) FROM diagrams').fetchone()[0]
    authors = conn.execute('SELECT COUNT(DISTINCT author) FROM diagrams').fetchone()[0]

    top_spaces = conn.execute('''
        SELECT space_key, COUNT(*) as count
        FROM diagrams
        GROUP BY space_key
        ORDER BY count DESC
        LIMIT 10
    ''').fetchall()

    conn.close()

    return jsonify({
        'total_diagrams': total,
        'total_spaces': spaces,
        'total_authors': authors,
        'top_spaces': [dict(r) for r in top_spaces]
    })


@app.route('/build-index')
def build_index_page():
    """Page to trigger index building."""
    return render_template('build_index.html')


@app.route('/api/build-index', methods=['POST'])
def api_build_index():
    """API to trigger index building."""
    try:
        count = index_all_diagrams()
        return jsonify({'success': True, 'indexed': count})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# =============================================================================
# C4 Model Routes
# =============================================================================

def _get_c4_dir():
    """Get the C4 models directory."""
    settings = get_settings()
    return os.path.join(settings['content_directory'], 'c4')


def _load_c4_models():
    """Load all C4 model JSON files from the c4 directory."""
    c4_dir = _get_c4_dir()
    models = []

    if not os.path.exists(c4_dir):
        return models

    for space_key in sorted(os.listdir(c4_dir)):
        space_dir = os.path.join(c4_dir, space_key)
        if not os.path.isdir(space_dir):
            continue

        for filename in sorted(os.listdir(space_dir)):
            if not filename.endswith('.c4.json'):
                continue

            filepath = os.path.join(space_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                diagram_name = filename.replace('.c4.json', '')
                data['space_key'] = space_key
                data['diagram_name'] = diagram_name
                data['filepath'] = filepath

                # Check for companion DrawIO C4 diagram
                c4_drawio_path = os.path.join(space_dir, f'{diagram_name}.c4.drawio')
                data['has_c4_drawio'] = os.path.exists(c4_drawio_path)

                # Check for original screenshot
                images_dir = get_settings()['images_directory']
                original_image = os.path.join(images_dir, space_key, f'{diagram_name}.png')
                data['has_original_image'] = os.path.exists(original_image)

                models.append(data)
            except (json.JSONDecodeError, IOError):
                continue

    return models


@app.route('/c4')
def c4_index():
    """Browse C4 architecture models."""
    models = _load_c4_models()

    if not models:
        return render_template('c4_index.html', models=[], spaces={},
                             total=0, total_systems=0, total_relationships=0)

    # Aggregate stats
    total_systems = sum(len(m.get('systems', [])) for m in models)
    total_relationships = sum(len(m.get('relationships', [])) for m in models)

    # Group by space
    spaces = {}
    for m in models:
        space = m['space_key']
        if space not in spaces:
            spaces[space] = []
        spaces[space].append(m)

    sort = request.args.get('sort', 'count')
    if sort == 'alpha':
        spaces = dict(sorted(spaces.items()))
    else:
        spaces = dict(sorted(spaces.items(), key=lambda x: -len(x[1])))

    return render_template('c4_index.html',
                         models=models,
                         spaces=spaces,
                         total=len(models),
                         total_systems=total_systems,
                         total_relationships=total_relationships,
                         sort=sort)


@app.route('/c4/<space_key>/<diagram_name>')
def c4_detail(space_key, diagram_name):
    """View a single C4 model."""
    c4_dir = _get_c4_dir()
    json_path = os.path.join(c4_dir, space_key, f'{diagram_name}.c4.json')

    if not os.path.exists(json_path):
        return "C4 model not found", 404

    with open(json_path, 'r', encoding='utf-8') as f:
        model = json.load(f)

    model['space_key'] = space_key
    model['diagram_name'] = diagram_name

    # Check for C4 DrawIO diagram
    c4_drawio_path = os.path.join(c4_dir, space_key, f'{diagram_name}.c4.drawio')
    model['has_c4_drawio'] = os.path.exists(c4_drawio_path)

    # Check for original image
    images_dir = get_settings()['images_directory']
    original_image = os.path.join(images_dir, space_key, f'{diagram_name}.png')
    model['has_original_image'] = os.path.exists(original_image)

    return render_template('c4_detail.html', model=model)


@app.route('/c4/download/<space_key>/<path:filename>')
def download_c4(space_key, filename):
    """Download C4 DrawIO diagram."""
    c4_dir = _get_c4_dir()
    filepath = os.path.join(c4_dir, space_key, filename)

    if os.path.exists(filepath):
        return send_file(filepath,
                        mimetype='application/xml' if filename.endswith('.drawio') else 'application/json')
    return "File not found", 404


@app.route('/api/c4/stats')
def api_c4_stats():
    """API endpoint for C4 model statistics."""
    models = _load_c4_models()

    all_systems = []
    all_technologies = set()
    for m in models:
        for s in m.get('systems', []):
            all_systems.append(s)
            if s.get('technology'):
                all_technologies.add(s['technology'])
        for c in m.get('containers', []):
            if c.get('technology'):
                all_technologies.add(c['technology'])

    return jsonify({
        'total_models': len(models),
        'total_systems': len(all_systems),
        'total_technologies': len(all_technologies),
        'technologies': sorted(all_technologies),
        'spaces_with_c4': len(set(m['space_key'] for m in models)),
    })


# =============================================================================
# Conversion Report Route
# =============================================================================

@app.route('/conversion-report')
def conversion_report():
    """View the batch conversion report."""
    settings = get_settings()
    report_path = os.path.join(
        os.path.dirname(settings['content_directory']),
        'conversion_report.json'
    )

    if not os.path.exists(report_path):
        return render_template('conversion_report.html', report=None)

    with open(report_path, 'r', encoding='utf-8') as f:
        report = json.load(f)

    return render_template('conversion_report.html', report=report)


# =============================================================================
# Main
# =============================================================================

def create_app(settings_path=None):
    """Create and configure the Flask app."""
    if settings_path:
        Settings.reload(settings_path)

    init_db()
    init_index()

    return app


if __name__ == '__main__':
    settings = get_settings()
    init_db()
    init_index()

    print("Starting DrawIO Browser...")
    print(f"Database: {get_db_path()}")
    print(f"Index: {get_index_dir()}")
    print(f"Content: {settings['content_directory']}")

    app.run(
        host=settings['host'],
        port=settings['port'],
        debug=settings['debug']
    )
