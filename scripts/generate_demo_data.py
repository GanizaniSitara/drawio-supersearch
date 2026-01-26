#!/usr/bin/env python3
"""
Generate Demo Data for DrawIO SuperSearch

Creates demo data from GitHub-sourced DrawIO files for use as example/demo content.
Uses keyword-based clustering to group diagrams into meaningful "spaces" based on
the most distinctive words extracted from diagram names and content.

Source: /mnt/c/git/drawio_c4_lint/c4_github_examples/Data/
- drawio_github/ - 81K .drawio source files
- *.png - 23K pre-rendered PNG images (will generate missing ones)

Filename format: <diagram_name>.drawio--<owner>--<repo>.drawio (or .png)
"""

import os
import re
import json
import shutil
import sqlite3
import base64
import zlib
import subprocess
from collections import defaultdict, Counter
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import unquote
import random

# Source paths
SOURCE_DIR = "/mnt/c/git/drawio_c4_lint/c4_github_examples/Data"
DRAWIO_DIR = os.path.join(SOURCE_DIR, "drawio_github")

# Output paths (relative to script location)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
OUTPUT_DIR = os.path.join(PROJECT_DIR, "data", "content")
DB_PATH = os.path.join(PROJECT_DIR, "data", "diagrams.db")

# Draw.io executable for PNG generation
DRAWIO_EXE = "/mnt/c/Program Files/draw.io/draw.io.exe"

# Comprehensive stopwords - these will NEVER become space names
STOPWORDS = {
    # Years
    '2014', '2015', '2016', '2017', '2018', '2019', '2020', '2021', '2022', '2023', '2024', '2025', '2026',
    # Common English words
    'and', 'the', 'in', 'of', 'to', 'a', 'for', 'with', 'on', 'at', 'from', 'by', 'an',
    'or', 'is', 'it', 'as', 'be', 'are', 'was', 'were', 'been', 'being', 'have', 'has',
    'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might',
    'this', 'that', 'these', 'those', 'my', 'your', 'our', 'their', 'its',
    'not', 'but', 'if', 'then', 'else', 'when', 'where', 'how', 'what', 'which', 'who',
    'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other', 'some', 'such',
    'no', 'nor', 'only', 'own', 'same', 'so', 'than', 'too', 'very', 'just', 'also',
    'can', 'get', 'got', 'us', 'me', 'him', 'her', 'they', 'them', 'we', 'he', 'she',
    'up', 'down', 'out', 'into', 'over', 'under', 'again', 'further', 'once',
    # Generic diagram/file terms
    'diagram', 'diagrams', 'drawio', 'draw', 'io', 'file', 'files', 'image', 'images',
    'example', 'examples', 'sample', 'samples', 'test', 'tests', 'testing',
    'new', 'old', 'v1', 'v2', 'v3', 'version', 'final', 'draft', 'copy', 'backup',
    'page', 'pages', 'sheet', 'sheets', 'tab', 'tabs', 'user', 'users',
    'untitled', 'temp', 'tmp', 'demo', 'poc', 'wip',
    # GitHub-specific
    'github', 'repo', 'repository', 'main', 'master', 'branch', 'readme', 'docs',
    # HTML/CSS terms
    'div', 'span', 'font', 'style', 'color', 'border', 'width', 'height', 'size',
    'text', 'align', 'center', 'left', 'right', 'top', 'bottom', 'middle',
    'padding', 'margin', 'background', 'none', 'auto', 'solid', 'transparent',
    'px', 'pt', 'em', 'rem', 'percent', 'rgb', 'rgba', 'hex',
    'true', 'false', 'null', 'none', 'empty', 'undefined',
    'href', 'src', 'alt', 'title', 'class', 'name', 'value', 'type', 'id',
    'display', 'block', 'inline', 'flex', 'grid', 'table', 'position', 'absolute', 'relative',
    'overflow', 'hidden', 'visible', 'scroll', 'clip',
    'vertical', 'horizontal', 'baseline', 'nowrap', 'break', 'word',
    'bold', 'normal', 'italic', 'underline', 'inherit', 'initial', 'important',
    # HTML entities
    'nbsp', 'amp', 'lt', 'gt', 'quot', 'apos', 'copy', 'reg', 'trade',
    # DrawIO/mxGraph terms
    'mxcell', 'mxgraph', 'mxpoint', 'mxgeometry', 'mxrectangle',
    'shape', 'shapes', 'connector', 'connectors', 'arrow', 'arrows',
    'vertex', 'edge', 'edges', 'parent', 'child', 'source', 'target',
    'stroke', 'fill', 'opacity', 'rounded', 'shadow', 'dashed',
    'label', 'labels', 'html', 'latex', 'whitespace', 'wrap',
    'endsize', 'startsize', 'endfill', 'startfill', 'endarrow', 'startarrow',
    'curved', 'orthogonal', 'jetty', 'segment', 'exit', 'entry', 'perimeter',
    # Programming generic terms (too common)
    'string', 'double', 'int', 'integer', 'float', 'boolean', 'bool', 'void',
    'yes', 'no', 'ok', 'okay', 'cancel', 'submit', 'save', 'delete', 'edit',
    'sum', 'count', 'total', 'number', 'num', 'index', 'key',
    'item', 'items', 'list', 'array', 'object', 'objects',
    'start', 'end', 'begin', 'finish', 'first', 'last', 'next', 'prev', 'previous',
    'input', 'output', 'return', 'returns', 'result', 'results',
    'cluster', 'group', 'category', 'section', 'part', 'component', 'module',
    'thing', 'things', 'stuff', 'data', 'info', 'information',
    # Single letters and common short words
    'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm',
    'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z',
    'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten',
}

# Number of target spaces (will be adjusted based on data)
TARGET_SPACES = 80


def parse_filename(filename):
    """
    Parse GitHub DrawIO filename.
    Format: <diagram_name>.drawio--<owner>--<repo>.drawio (or .png)
    """
    if filename.endswith('.png'):
        base = filename[:-4]
    elif filename.endswith('.drawio'):
        base = filename[:-7]
    else:
        return None

    parts = base.split('--')
    if len(parts) < 3:
        return None

    repo = parts[-1]
    owner = parts[-2]
    diagram_name = '--'.join(parts[:-2])

    if diagram_name.endswith('.drawio'):
        diagram_name = diagram_name[:-7]

    return diagram_name, owner, repo


def extract_keywords_from_name(name):
    """Extract meaningful keywords from a diagram/file name."""
    # Replace separators with spaces
    text = re.sub(r'[-_./\\]', ' ', name)
    # Split camelCase
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    # Remove non-alpha
    text = re.sub(r'[^a-zA-Z\s]', ' ', text)
    # Lowercase and split
    words = text.lower().split()
    # Filter stopwords and short words (< 3 chars)
    return [w for w in words if w not in STOPWORDS and len(w) >= 3]


def extract_keywords_from_drawio(filepath):
    """Extract keywords from DrawIO file content."""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        keywords = []

        # Extract diagram names
        for match in re.finditer(r'<diagram[^>]*name="([^"]*)"', content):
            name = match.group(1)
            if name:
                keywords.extend(extract_keywords_from_name(name))

        # Try to decode compressed content
        for match in re.finditer(r'<diagram[^>]*>([^<]+)</diagram>', content):
            compressed_data = match.group(1).strip()
            if compressed_data:
                try:
                    decoded = base64.b64decode(compressed_data)
                    decompressed = zlib.decompress(decoded, -15)
                    xml_content = unquote(decompressed.decode('utf-8'))

                    # Extract value attributes from mxCell elements
                    for cell_match in re.finditer(r'value="([^"]*)"', xml_content):
                        value = cell_match.group(1)
                        if value:
                            # Strip HTML and extract text
                            clean = re.sub(r'<[^>]+>', ' ', value)
                            clean = re.sub(r'&[#a-zA-Z0-9]+;', ' ', clean)
                            clean = re.sub(r'[a-z-]+:\s*[^;]+;', ' ', clean)
                            keywords.extend(extract_keywords_from_name(clean))
                except Exception:
                    pass

        # Also check uncompressed mxCell values
        for match in re.finditer(r'<mxCell[^>]*value="([^"]*)"', content):
            value = match.group(1)
            if value:
                clean = re.sub(r'<[^>]+>', ' ', value)
                clean = re.sub(r'&[#a-zA-Z0-9]+;', ' ', clean)
                keywords.extend(extract_keywords_from_name(clean))

        return keywords
    except Exception:
        return []


def cluster_by_keywords(diagrams, target_spaces=TARGET_SPACES):
    """
    Cluster diagrams by their most distinctive keyword.

    Strategy:
    1. Count global keyword frequency across all diagrams
    2. For each diagram, find its most distinctive keyword (present but not too common)
    3. Group diagrams by their top keyword
    4. Merge small groups, split large groups to hit target_spaces
    """
    print(f"\nClustering {len(diagrams)} diagrams by keyword...")

    # Count keywords across all diagrams
    global_keyword_counts = Counter()
    diagram_keywords = {}

    for idx, d in enumerate(diagrams):
        if idx % 10000 == 0:
            print(f"  Extracting keywords: {idx}/{len(diagrams)}")

        diagram_name, owner, repo, png_path, drawio_path = d

        # Get keywords from name (weighted higher)
        name_keywords = extract_keywords_from_name(diagram_name)

        # Get keywords from content
        content_keywords = []
        if drawio_path and os.path.exists(drawio_path):
            content_keywords = extract_keywords_from_drawio(drawio_path)

        # Weight name keywords higher (appear 3x)
        all_keywords = name_keywords * 3 + content_keywords
        diagram_keywords[idx] = all_keywords
        global_keyword_counts.update(set(all_keywords))  # Count unique per diagram

    print(f"  Found {len(global_keyword_counts)} unique keywords")

    # Find good keywords for clustering (not too rare, not too common)
    total_diagrams = len(diagrams)
    min_docs = max(5, total_diagrams // 1000)  # At least 0.1% of diagrams
    max_docs = total_diagrams // 5  # At most 20% of diagrams

    good_keywords = {
        kw for kw, count in global_keyword_counts.items()
        if min_docs <= count <= max_docs
    }
    print(f"  {len(good_keywords)} keywords in good frequency range ({min_docs}-{max_docs})")

    # If we don't have enough good keywords, relax the constraints
    if len(good_keywords) < target_spaces:
        max_docs = total_diagrams // 3  # Allow up to 33%
        good_keywords = {
            kw for kw, count in global_keyword_counts.items()
            if min_docs <= count <= max_docs
        }
        print(f"  Relaxed to {len(good_keywords)} keywords")

    # Assign each diagram to its best keyword
    assignments = {}
    for idx, keywords in diagram_keywords.items():
        # Find the most specific (lowest frequency) good keyword for this diagram
        best_keyword = None
        best_score = float('inf')

        for kw in keywords:
            if kw in good_keywords:
                score = global_keyword_counts[kw]
                if score < best_score:
                    best_score = score
                    best_keyword = kw

        if best_keyword:
            assignments[idx] = best_keyword
        else:
            # No good keyword found - use most common keyword from this diagram
            if keywords:
                kw_counts = Counter(keywords)
                most_common = kw_counts.most_common(1)[0][0]
                assignments[idx] = most_common
            else:
                assignments[idx] = 'miscellaneous'

    # Group by assignment
    groups = defaultdict(list)
    for idx, keyword in assignments.items():
        groups[keyword].append(idx)

    print(f"  Initial grouping: {len(groups)} groups")

    # Strategy: Take the top groups by size, then merge smallest until target
    # Sort groups by size (descending)
    sorted_groups = sorted(groups.items(), key=lambda x: -len(x[1]))

    # Print top 20 initial groups for debugging
    print(f"  Top 20 initial groups:")
    for kw, indices in sorted_groups[:20]:
        print(f"    {kw.upper()}: {len(indices)} diagrams")

    # Take top N groups where N = target_spaces * 2 (we'll merge down)
    # This ensures we keep the most meaningful groups
    top_n = min(len(sorted_groups), target_spaces * 2)

    final_groups = {}
    misc_diagrams = []

    for i, (keyword, indices) in enumerate(sorted_groups):
        if i < top_n:
            final_groups[keyword.upper()] = indices
        else:
            misc_diagrams.extend(indices)

    print(f"  Kept top {len(final_groups)} groups, {len(misc_diagrams)} diagrams in misc")

    # Merge misc diagrams into existing groups (distribute evenly)
    if misc_diagrams:
        random.shuffle(misc_diagrams)
        group_keys = list(final_groups.keys())
        for i, idx in enumerate(misc_diagrams):
            target_group = group_keys[i % len(group_keys)]
            final_groups[target_group].append(idx)

    # Now merge smallest groups until we hit target_spaces
    while len(final_groups) > target_spaces:
        # Find the two smallest groups and merge them
        sorted_by_size = sorted(final_groups.items(), key=lambda x: len(x[1]))
        smallest_key, smallest_indices = sorted_by_size[0]
        second_key, second_indices = sorted_by_size[1]

        # Merge into the second smallest (keeps the name)
        final_groups[second_key].extend(smallest_indices)
        del final_groups[smallest_key]

    print(f"  Final: {len(final_groups)} groups")

    # Print top groups
    sorted_groups = sorted(final_groups.items(), key=lambda x: -len(x[1]))[:20]
    for name, indices in sorted_groups:
        print(f"    {name}: {len(indices)} diagrams")

    return final_groups


def generate_png(drawio_path, output_png_path, timeout=30):
    """Generate PNG from DrawIO file using draw.io.exe."""
    if not os.path.exists(DRAWIO_EXE):
        return False

    try:
        # Convert WSL path to Windows path for draw.io.exe
        def to_windows_path(path):
            if path.startswith('/mnt/c/'):
                return 'C:' + path[6:].replace('/', '\\')
            return path

        win_input = to_windows_path(drawio_path)
        win_output = to_windows_path(output_png_path)

        cmd = [
            DRAWIO_EXE,
            '-x',  # export mode
            '-f', 'png',  # format
            '-o', win_output,
            win_input
        ]

        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return os.path.exists(output_png_path)
    except Exception as e:
        return False


def scan_available_files():
    """Scan source directories and find all DrawIO files with optional PNG matches."""
    print("Scanning source directories...")

    # Get all PNG files
    png_files = {}
    for f in os.listdir(SOURCE_DIR):
        if f.endswith('.png'):
            parsed = parse_filename(f)
            if parsed:
                key = (parsed[0], parsed[1], parsed[2])
                png_files[key] = os.path.join(SOURCE_DIR, f)

    print(f"  Found {len(png_files)} PNG files")

    # Get all DrawIO files
    all_diagrams = []
    if os.path.exists(DRAWIO_DIR):
        drawio_files = [f for f in os.listdir(DRAWIO_DIR) if f.endswith('.drawio')]
        print(f"  Found {len(drawio_files)} DrawIO files")

        for f in drawio_files:
            parsed = parse_filename(f)
            if parsed:
                diagram_name, owner, repo = parsed
                drawio_path = os.path.join(DRAWIO_DIR, f)
                key = (diagram_name, owner, repo)
                png_path = png_files.get(key)

                all_diagrams.append((diagram_name, owner, repo, png_path, drawio_path))

    print(f"  Total diagrams: {len(all_diagrams)}")
    print(f"  Diagrams with PNG: {sum(1 for d in all_diagrams if d[3])}")

    return all_diagrams


def create_output_structure(diagrams, groups, generate_pngs=False, limit=None):
    """Create the SuperSearch directory structure from grouped data."""

    # Create output directories
    diagrams_dir = os.path.join(OUTPUT_DIR, 'diagrams')
    images_dir = os.path.join(OUTPUT_DIR, 'images')
    metadata_dir = os.path.join(OUTPUT_DIR, 'metadata')

    os.makedirs(diagrams_dir, exist_ok=True)
    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(metadata_dir, exist_ok=True)

    # Track counts
    space_counts = {}
    total_processed = 0
    pngs_generated = 0
    pngs_copied = 0

    for space_key, indices in groups.items():
        if limit and total_processed >= limit:
            break

        space_counts[space_key] = 0

        # Create space directories
        space_diagrams = os.path.join(diagrams_dir, space_key)
        space_images = os.path.join(images_dir, space_key)
        space_metadata = os.path.join(metadata_dir, space_key)

        os.makedirs(space_diagrams, exist_ok=True)
        os.makedirs(space_images, exist_ok=True)
        os.makedirs(space_metadata, exist_ok=True)

        for idx in indices:
            if limit and total_processed >= limit:
                break

            diagram_name, owner, repo, png_path, drawio_path = diagrams[idx]

            # Sanitize diagram name for filesystem
            safe_name = re.sub(r'[<>:"/\\|?*]', '_', diagram_name)
            safe_name = safe_name[:200]

            dst_png = os.path.join(space_images, f"{safe_name}.png")
            dst_drawio = os.path.join(space_diagrams, f"{safe_name}.drawio")

            # Handle PNG
            has_png = False
            if png_path and os.path.exists(png_path):
                if not os.path.exists(dst_png):
                    try:
                        shutil.copy2(png_path, dst_png)
                        pngs_copied += 1
                        has_png = True
                    except Exception:
                        pass
                else:
                    has_png = True
            elif generate_pngs and drawio_path and os.path.exists(drawio_path):
                # Try to generate PNG
                if generate_png(drawio_path, dst_png):
                    pngs_generated += 1
                    has_png = True

            # Skip if no PNG and we require it
            if not has_png and not os.path.exists(dst_png):
                # Still copy the drawio for completeness, but don't count
                if drawio_path and os.path.exists(drawio_path) and not os.path.exists(dst_drawio):
                    try:
                        shutil.copy2(drawio_path, dst_drawio)
                    except Exception:
                        pass
                continue

            # Copy DrawIO
            if drawio_path and os.path.exists(drawio_path) and not os.path.exists(dst_drawio):
                try:
                    shutil.copy2(drawio_path, dst_drawio)
                except Exception:
                    pass

            # Generate metadata
            diagram_id = total_processed + 1000
            days_ago = random.randint(1, 730)
            created_date = datetime.now() - timedelta(days=days_ago)

            metadata = {
                'id': str(diagram_id),
                'title': f"{diagram_name}.png",
                'space': {'key': space_key},
                '_expandable': {'container': f'/rest/api/content/{diagram_id}'},
                'version': {'number': random.randint(1, 10)},
                'metadata': {'github_owner': owner, 'github_repo': repo},
                '_links': {'download': f'/download/{space_key}/{safe_name}.png'},
                'created_date': created_date.strftime('%Y-%m-%d'),
                'author_display': f"{owner}/{repo}" if owner and repo else owner,
            }

            meta_path = os.path.join(space_metadata, f"{safe_name}.png.json")
            with open(meta_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2)

            space_counts[space_key] += 1
            total_processed += 1

            if total_processed % 1000 == 0:
                print(f"  Progress: {total_processed} processed, {pngs_copied} copied, {pngs_generated} generated")

    print(f"\nCreated {len(space_counts)} spaces with {total_processed} diagrams")
    print(f"  PNGs copied: {pngs_copied}")
    print(f"  PNGs generated: {pngs_generated}")

    # Print top 20 spaces
    print("\nTop 20 spaces:")
    for space, count in sorted(space_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"  {space}: {count} diagrams")

    return space_counts


def create_database(space_counts):
    """Create SQLite database with diagram metadata."""
    print(f"\nCreating database at {DB_PATH}...")

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''
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

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_space ON diagrams(space_key)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_name ON diagrams(diagram_name)')

    metadata_dir = os.path.join(OUTPUT_DIR, 'metadata')
    diagrams_dir = os.path.join(OUTPUT_DIR, 'diagrams')
    images_dir = os.path.join(OUTPUT_DIR, 'images')
    record_count = 0

    for space_key in os.listdir(metadata_dir):
        space_path = os.path.join(metadata_dir, space_key)
        if not os.path.isdir(space_path):
            continue

        for meta_file in os.listdir(space_path):
            if not meta_file.endswith('.json'):
                continue

            meta_path = os.path.join(space_path, meta_file)
            try:
                with open(meta_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)

                diagram_name = meta_file.replace('.png.json', '')
                drawio_path = os.path.join(diagrams_dir, space_key, f'{diagram_name}.drawio')
                image_path = os.path.join(images_dir, space_key, f'{diagram_name}.png')

                file_size = 0
                if os.path.exists(image_path):
                    file_size = os.path.getsize(image_path)

                github_owner = meta.get('metadata', {}).get('github_owner', '')
                github_repo = meta.get('metadata', {}).get('github_repo', '')
                author_display = f"{github_owner}/{github_repo}" if github_owner and github_repo else github_owner

                cursor.execute('''
                    INSERT INTO diagrams
                    (space_key, diagram_name, page_title, page_id, confluence_page_url,
                     author, author_display, created_date, file_size, drawio_path,
                     image_path, metadata_path, content_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    space_key,
                    diagram_name,
                    diagram_name,
                    meta.get('id', ''),
                    '',
                    github_owner,
                    author_display,
                    meta.get('created_date', ''),
                    file_size,
                    drawio_path if os.path.exists(drawio_path) else '',
                    image_path if os.path.exists(image_path) else '',
                    meta_path,
                    ''
                ))
                record_count += 1

            except Exception as e:
                print(f"  Error processing {meta_path}: {e}")

    conn.commit()
    conn.close()

    print(f"  Created {record_count} database records")
    return record_count


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Generate demo data for DrawIO SuperSearch')
    parser.add_argument('--limit', type=int, help='Limit number of files to process')
    parser.add_argument('--spaces', type=int, default=TARGET_SPACES, help=f'Target number of spaces (default: {TARGET_SPACES})')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without copying files')
    parser.add_argument('--clean', action='store_true', help='Remove existing output before generating')
    parser.add_argument('--generate-pngs', action='store_true', help='Generate PNGs for diagrams without them (slow)')
    parser.add_argument('--clusters', type=int, help='Alias for --spaces')

    args = parser.parse_args()

    target_spaces = args.clusters if args.clusters else args.spaces

    print("=" * 60)
    print("DrawIO SuperSearch Demo Data Generator (Keyword Clustering)")
    print("=" * 60)
    print(f"\nSource: {SOURCE_DIR}")
    print(f"Output: {OUTPUT_DIR}")
    print(f"Database: {DB_PATH}")
    print(f"Target Spaces: {target_spaces}")

    if args.clean:
        print("\nCleaning existing output...")
        if os.path.exists(OUTPUT_DIR):
            shutil.rmtree(OUTPUT_DIR)
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)

    # Scan files
    diagrams = scan_available_files()

    if args.dry_run:
        print("\n[DRY RUN] Would process:")
        for diagram_name, owner, repo, png_path, drawio_path in diagrams[:20]:
            has_png = "+" if png_path else "-"
            print(f"  {diagram_name[:50]:<50} (png: {has_png})")
        print(f"  ... and {len(diagrams) - 20} more")
        return

    # Cluster diagrams by keyword
    groups = cluster_by_keywords(diagrams, target_spaces=target_spaces)

    # Create output structure
    space_counts = create_output_structure(
        diagrams, groups,
        generate_pngs=args.generate_pngs,
        limit=args.limit
    )

    # Create database
    create_database(space_counts)

    print("\n" + "=" * 60)
    print("Done! Now run:")
    print("  python scripts/index.py --rebuild")
    print("  python scripts/serve.py")
    print("=" * 60)


if __name__ == '__main__':
    main()
