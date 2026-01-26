#!/usr/bin/env python3
"""
Create Demo Data Subset with 100% PNG Coverage

Creates demo_data/ from data/ by selecting only diagrams that have:
1. PNG image file
2. Metadata JSON file
3. DrawIO source file (optional)

Ensures every diagram in the demo has a preview image.
"""

import os
import shutil
from collections import defaultdict

# Paths
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_DIR, "data", "content")
DEMO_DIR = os.path.join(PROJECT_DIR, "demo_data", "content")

# Target configuration
TARGET_SPACES = 15  # Number of spaces to include
MAX_DIAGRAMS_PER_SPACE = 100  # Cap per space for demo size


def find_complete_diagrams():
    """Find diagrams that have both PNG and metadata JSON."""
    images_dir = os.path.join(DATA_DIR, "images")
    metadata_dir = os.path.join(DATA_DIR, "metadata")
    diagrams_dir = os.path.join(DATA_DIR, "diagrams")

    if not os.path.exists(images_dir):
        print(f"Error: {images_dir} not found")
        return {}

    results = defaultdict(list)

    for space_key in os.listdir(images_dir):
        space_images = os.path.join(images_dir, space_key)
        space_metadata = os.path.join(metadata_dir, space_key)
        space_diagrams = os.path.join(diagrams_dir, space_key)

        if not os.path.isdir(space_images):
            continue

        for png_file in os.listdir(space_images):
            if not png_file.endswith('.png'):
                continue

            diagram_name = png_file[:-4]  # Remove .png

            # Check files exist
            png_path = os.path.join(space_images, png_file)
            meta_path = os.path.join(space_metadata, f"{diagram_name}.png.json")
            drawio_path = os.path.join(space_diagrams, f"{diagram_name}.drawio")

            # PNG is required, metadata is required, drawio is optional
            if os.path.exists(png_path) and os.path.exists(meta_path):
                results[space_key].append({
                    'name': diagram_name,
                    'png': png_path,
                    'metadata': meta_path,
                    'drawio': drawio_path if os.path.exists(drawio_path) else None,
                    'size': os.path.getsize(png_path)
                })

    return results


def select_best_spaces(diagrams_by_space, target_spaces, max_per_space):
    """Select spaces with most complete diagrams."""
    sorted_spaces = sorted(
        diagrams_by_space.items(),
        key=lambda x: len(x[1]),
        reverse=True
    )

    selected = {}
    for space_key, diagrams in sorted_spaces[:target_spaces]:
        # Take up to max_per_space diagrams, preferring smaller files
        sorted_diagrams = sorted(diagrams, key=lambda x: x['size'])[:max_per_space]
        selected[space_key] = sorted_diagrams

    return selected


def create_demo_structure(selected_spaces):
    """Create the demo_data directory structure."""
    if os.path.exists(DEMO_DIR):
        print(f"Removing existing {DEMO_DIR}...")
        shutil.rmtree(DEMO_DIR)

    images_dir = os.path.join(DEMO_DIR, "images")
    metadata_dir = os.path.join(DEMO_DIR, "metadata")
    diagrams_dir = os.path.join(DEMO_DIR, "diagrams")

    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(metadata_dir, exist_ok=True)
    os.makedirs(diagrams_dir, exist_ok=True)

    total_copied = 0

    for space_key, diagrams in selected_spaces.items():
        os.makedirs(os.path.join(images_dir, space_key), exist_ok=True)
        os.makedirs(os.path.join(metadata_dir, space_key), exist_ok=True)
        os.makedirs(os.path.join(diagrams_dir, space_key), exist_ok=True)

        for diagram in diagrams:
            name = diagram['name']

            # Copy PNG
            dst_png = os.path.join(images_dir, space_key, f"{name}.png")
            shutil.copy2(diagram['png'], dst_png)

            # Copy metadata
            dst_meta = os.path.join(metadata_dir, space_key, f"{name}.png.json")
            shutil.copy2(diagram['metadata'], dst_meta)

            # Copy drawio if exists
            if diagram['drawio']:
                dst_drawio = os.path.join(diagrams_dir, space_key, f"{name}.drawio")
                shutil.copy2(diagram['drawio'], dst_drawio)

            total_copied += 1

        print(f"  {space_key}: {len(diagrams)} diagrams")

    return total_copied


def main():
    print("=" * 60)
    print("Create Demo Data Subset (100% PNG Coverage)")
    print("=" * 60)
    print(f"\nSource: {DATA_DIR}")
    print(f"Output: {DEMO_DIR}")
    print(f"Target: {TARGET_SPACES} spaces, max {MAX_DIAGRAMS_PER_SPACE} per space")

    print("\nScanning for complete diagrams (with PNG)...")
    diagrams_by_space = find_complete_diagrams()

    total_diagrams = sum(len(d) for d in diagrams_by_space.values())
    print(f"  Found {total_diagrams} complete diagrams across {len(diagrams_by_space)} spaces")

    if not diagrams_by_space:
        print("Error: No complete diagrams found. Run generate_demo_data.py first.")
        return

    print(f"\nSelecting top {TARGET_SPACES} spaces...")
    selected = select_best_spaces(diagrams_by_space, TARGET_SPACES, MAX_DIAGRAMS_PER_SPACE)

    print(f"\nCreating demo data structure...")
    total = create_demo_structure(selected)

    print(f"\n{'=' * 60}")
    print(f"Created demo_data with {total} diagrams across {len(selected)} spaces")
    print(f"All diagrams have PNG previews (100% coverage)")
    print("=" * 60)


if __name__ == '__main__':
    main()
