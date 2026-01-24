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
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for

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
    c.execute('DELETE FROM diagrams')  # Clear existing data

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

                # Extract page title from webui link
                webui = meta.get('_links', {}).get('webui', '')
                page_title = ''
                if '/display/' in webui:
                    parts = webui.split('/')
                    if len(parts) >= 4:
                        page_part = parts[3].split('?')[0]
                        page_title = unquote(page_part.replace('+', ' '))

                # Extract page ID from container
                container = meta.get('_expandable', {}).get('container', '')
                page_id = container.split('/')[-1] if container else ''

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

                # Extract text content from .drawio file
                content_text = ''
                if os.path.exists(drawio_path):
                    content_text = extract_text_from_drawio(drawio_path)

                # Insert into database
                c.execute('''
                    INSERT INTO diagrams
                    (space_key, diagram_name, page_title, page_id, author, author_display,
                     created_date, file_size, drawio_path, image_path, metadata_path, content_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (space_key, diagram_name, page_title, page_id, author, author_display,
                      created_date, file_size, drawio_path, image_path, meta_path, content_text))

                diagram_id = c.lastrowid

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
    """Home page - show spaces overview."""
    if not db_is_populated():
        return render_template('needs_index.html')

    conn = get_db()
    spaces = conn.execute('''
        SELECT space_key, COUNT(*) as count
        FROM diagrams
        GROUP BY space_key
        ORDER BY count DESC
    ''').fetchall()

    total = conn.execute('SELECT COUNT(*) FROM diagrams').fetchone()[0]
    conn.close()

    return render_template('index.html', spaces=spaces, total=total)


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
    conn.close()

    if not diagram:
        return "Diagram not found", 404

    return render_template('diagram.html', diagram=diagram)


@app.route('/search')
def search():
    """Search diagrams."""
    query = request.args.get('q', '').strip()
    space_filter = request.args.get('space', '')
    page = request.args.get('page', 1, type=int)
    per_page = 50

    if not query:
        return render_template('search.html', results=[], query='', total=0)

    if not index_is_populated():
        return render_template('search.html', results=[], query=query,
                             error="Index not built. Run indexing first.")

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

            filtered_ids = []
            for hit in results:
                if space_filter and hit['space_key'] != space_filter:
                    continue
                filtered_ids.append(int(hit['id']))

            total = len(filtered_ids)

            start = (page - 1) * per_page
            end = start + per_page
            page_ids = filtered_ids[start:end]
    except Exception as e:
        return render_template('search.html', results=[], query=query,
                             error=str(e))

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

    conn = get_db()
    spaces = conn.execute(
        'SELECT DISTINCT space_key FROM diagrams ORDER BY space_key'
    ).fetchall()
    conn.close()

    return render_template('search.html',
                         results=diagrams,
                         query=query,
                         space_filter=space_filter,
                         spaces=spaces,
                         total=total,
                         page=page,
                         total_pages=total_pages)


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
    """Download .drawio file."""
    settings = get_settings()
    diagrams_dir = settings['diagrams_directory']

    # Handle both with and without .drawio extension
    if not filename.endswith('.drawio'):
        filename = f"{filename}.drawio"

    drawio_path = os.path.join(diagrams_dir, space_key, filename)
    if os.path.exists(drawio_path):
        return send_file(drawio_path, as_attachment=True)
    return "File not found", 404


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
