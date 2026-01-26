#!/usr/bin/env python3
"""
Create a lightweight demo subset for cloud deployment.
Selects a subset of spaces/diagrams to keep the total size under 200MB.
"""

import os
import shutil
import sqlite3
import random

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
CONTENT_DIR = os.path.join(DATA_DIR, 'content')
IMAGES_DIR = os.path.join(CONTENT_DIR, 'images')
DB_PATH = os.path.join(DATA_DIR, 'diagrams.db')

# Output
DEMO_DIR = os.path.join(BASE_DIR, 'demo_data')
DEMO_CONTENT_DIR = os.path.join(DEMO_DIR, 'content')
DEMO_IMAGES_DIR = os.path.join(DEMO_CONTENT_DIR, 'images')
DEMO_DB_PATH = os.path.join(DEMO_DIR, 'diagrams.db')

# Config - aim for ~100-200MB total
MAX_SPACES = 15
MAX_DIAGRAMS_PER_SPACE = 50
TARGET_SIZE_MB = 150


def get_space_sizes():
    """Get size of each space's images folder."""
    sizes = {}
    if os.path.exists(IMAGES_DIR):
        for space in os.listdir(IMAGES_DIR):
            space_path = os.path.join(IMAGES_DIR, space)
            if os.path.isdir(space_path):
                total = sum(
                    os.path.getsize(os.path.join(space_path, f))
                    for f in os.listdir(space_path)
                    if os.path.isfile(os.path.join(space_path, f))
                )
                count = len([f for f in os.listdir(space_path) if f.endswith('.png')])
                sizes[space] = {'size': total, 'count': count}
    return sizes


def select_spaces(space_sizes, max_spaces, target_mb):
    """Select diverse spaces that fit within size budget."""
    # Sort by count (prefer spaces with moderate counts)
    sorted_spaces = sorted(space_sizes.items(), key=lambda x: x[1]['count'], reverse=True)

    selected = []
    total_size = 0
    target_bytes = target_mb * 1024 * 1024

    for space, info in sorted_spaces:
        if len(selected) >= max_spaces:
            break
        if total_size + info['size'] < target_bytes:
            selected.append(space)
            total_size += info['size']

    return selected


def copy_space_images(space, max_diagrams):
    """Copy images for a space (limited count)."""
    src_dir = os.path.join(IMAGES_DIR, space)
    dst_dir = os.path.join(DEMO_IMAGES_DIR, space)

    os.makedirs(dst_dir, exist_ok=True)

    files = [f for f in os.listdir(src_dir) if f.endswith('.png')]
    if len(files) > max_diagrams:
        files = random.sample(files, max_diagrams)

    copied = []
    for f in files:
        shutil.copy2(os.path.join(src_dir, f), os.path.join(dst_dir, f))
        copied.append(f)

    return copied


def create_demo_database(selected_spaces, copied_files_by_space):
    """Create a subset database with only the selected diagrams."""
    # Connect to source
    src_conn = sqlite3.connect(DB_PATH)
    src_conn.row_factory = sqlite3.Row

    # Create destination
    if os.path.exists(DEMO_DB_PATH):
        os.remove(DEMO_DB_PATH)
    dst_conn = sqlite3.connect(DEMO_DB_PATH)

    # Get schema
    schema = src_conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='diagrams'"
    ).fetchone()

    if schema:
        dst_conn.execute(schema['sql'])

    # Copy matching rows
    total_copied = 0
    for space in selected_spaces:
        copied_files = copied_files_by_space.get(space, [])
        # Extract diagram names from filenames (remove .png)
        diagram_names = [f[:-4] if f.endswith('.png') else f for f in copied_files]

        for name in diagram_names:
            row = src_conn.execute(
                "SELECT * FROM diagrams WHERE space_key = ? AND diagram_name = ?",
                (space, name)
            ).fetchone()

            if row:
                cols = list(row.keys())
                placeholders = ','.join(['?' for _ in cols])
                dst_conn.execute(
                    f"INSERT INTO diagrams ({','.join(cols)}) VALUES ({placeholders})",
                    list(row)
                )
                total_copied += 1

    dst_conn.commit()
    src_conn.close()
    dst_conn.close()

    return total_copied


def main():
    print("=" * 60)
    print("Creating Demo Subset for Cloud Deployment")
    print("=" * 60)

    # Clean output
    if os.path.exists(DEMO_DIR):
        shutil.rmtree(DEMO_DIR)
    os.makedirs(DEMO_IMAGES_DIR, exist_ok=True)

    # Analyze spaces
    print("\nAnalyzing spaces...")
    space_sizes = get_space_sizes()
    print(f"  Found {len(space_sizes)} spaces")

    # Select spaces
    selected = select_spaces(space_sizes, MAX_SPACES, TARGET_SIZE_MB)
    print(f"\nSelected {len(selected)} spaces:")
    for s in selected:
        info = space_sizes[s]
        print(f"  {s}: {info['count']} diagrams, {info['size']/1024/1024:.1f} MB")

    # Copy images
    print("\nCopying images...")
    copied_by_space = {}
    for space in selected:
        copied = copy_space_images(space, MAX_DIAGRAMS_PER_SPACE)
        copied_by_space[space] = copied
        print(f"  {space}: {len(copied)} images")

    # Create database
    print("\nCreating database subset...")
    db_count = create_demo_database(selected, copied_by_space)
    print(f"  Copied {db_count} database records")

    # Calculate total size
    total_size = 0
    for root, dirs, files in os.walk(DEMO_DIR):
        for f in files:
            total_size += os.path.getsize(os.path.join(root, f))

    print(f"\n" + "=" * 60)
    print(f"Demo subset created: {DEMO_DIR}")
    print(f"  Total size: {total_size / 1024 / 1024:.1f} MB")
    print(f"  Spaces: {len(selected)}")
    print(f"  Diagrams: {sum(len(v) for v in copied_by_space.values())}")
    print("=" * 60)
    print("\nNext: Copy demo_data/* to data/ and rebuild index")


if __name__ == '__main__':
    main()
