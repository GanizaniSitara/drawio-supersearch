#!/usr/bin/env python3
"""
One-off cleanup script to fix metadata directories.

Performs:
1. Removes _debug_*.html files (full page dumps)
2. Cleans body_text in JSON files (removes base64 image data)

Usage:
    python scripts/cleanup_metadata.py              # Dry run (show what would change)
    python scripts/cleanup_metadata.py --fix        # Actually fix files
"""

import os
import sys
import re
import json
import argparse

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extractor.config import Settings


def format_size(size_bytes):
    """Format bytes to human readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def clean_body_text(text):
    """Remove base64 images and other binary data from body_text."""
    if not text:
        return text

    original_len = len(text)

    # Remove base64 encoded images and data URIs
    text = re.sub(r'data:[^;]+;base64,[A-Za-z0-9+/=]+', '', text, flags=re.IGNORECASE)

    # Remove long base64-like strings (safety net)
    text = re.sub(r'[A-Za-z0-9+/=]{100,}', '', text)

    # Clean up excessive whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    return text


def find_debug_files(metadata_dir):
    """Find all debug HTML files in metadata directory."""
    debug_files = []

    if not os.path.exists(metadata_dir):
        return debug_files

    for root, dirs, files in os.walk(metadata_dir):
        for f in files:
            if f.startswith('_debug_') and f.endswith('.html'):
                full_path = os.path.join(root, f)
                size = os.path.getsize(full_path)
                debug_files.append((full_path, size))

    return debug_files


def find_bloated_json_files(metadata_dir, threshold_kb=50):
    """Find JSON files with bloated body_text (likely containing base64)."""
    bloated_files = []

    if not os.path.exists(metadata_dir):
        return bloated_files

    for root, dirs, files in os.walk(metadata_dir):
        for f in files:
            if f.endswith('.json') and not f.startswith('_'):
                full_path = os.path.join(root, f)
                size = os.path.getsize(full_path)

                # Check if file is larger than threshold
                if size > threshold_kb * 1024:
                    bloated_files.append((full_path, size))
                else:
                    # Also check for base64 content in smaller files
                    try:
                        with open(full_path, 'r', encoding='utf-8') as fp:
                            content = fp.read()
                            if 'data:image' in content or re.search(r'[A-Za-z0-9+/=]{200,}', content):
                                bloated_files.append((full_path, size))
                    except:
                        pass

    return bloated_files


def fix_json_file(path):
    """Clean a JSON file's body_text field. Returns (original_size, new_size)."""
    original_size = os.path.getsize(path)

    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        changed = False

        # Clean body_text
        if 'body_text' in data and data['body_text']:
            original_text = data['body_text']
            cleaned_text = clean_body_text(original_text)
            if len(cleaned_text) < len(original_text):
                data['body_text'] = cleaned_text
                changed = True

        # Clean ocr_text if present
        if 'ocr_text' in data and data['ocr_text']:
            original_text = data['ocr_text']
            cleaned_text = clean_body_text(original_text)
            if len(cleaned_text) < len(original_text):
                data['ocr_text'] = cleaned_text
                changed = True

        if changed:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

        new_size = os.path.getsize(path)
        return original_size, new_size

    except Exception as e:
        print(f"  Error processing {path}: {e}")
        return original_size, original_size


def main():
    parser = argparse.ArgumentParser(
        description='Clean up metadata directories (remove debug files, fix bloated JSON)'
    )
    parser.add_argument('--fix', action='store_true',
                        help='Actually fix files (default is dry-run)')
    parser.add_argument('--config', help='Path to settings.ini')

    args = parser.parse_args()

    if args.config:
        Settings.reload(args.config)

    settings = Settings.get()
    metadata_dir = settings['metadata_directory']

    print("=" * 60)
    print("METADATA CLEANUP")
    print("=" * 60)
    print(f"Metadata directory: {metadata_dir}")
    print(f"Mode: {'FIX' if args.fix else 'DRY RUN'}")
    print("=" * 60)

    # 1. Find and handle debug HTML files
    print("\n[1/2] Checking for debug HTML files...")
    debug_files = find_debug_files(metadata_dir)

    if debug_files:
        debug_size = sum(size for _, size in debug_files)
        print(f"  Found {len(debug_files)} debug files ({format_size(debug_size)})")

        if args.fix:
            for path, _ in debug_files:
                try:
                    os.remove(path)
                except Exception as e:
                    print(f"  Error deleting {path}: {e}")
            print(f"  Deleted {len(debug_files)} files")
    else:
        print("  No debug files found")

    # 2. Find and fix bloated JSON files
    print("\n[2/2] Checking for bloated JSON files...")
    bloated_files = find_bloated_json_files(metadata_dir)

    if bloated_files:
        total_original = sum(size for _, size in bloated_files)
        print(f"  Found {len(bloated_files)} files with base64/bloated content ({format_size(total_original)})")

        if args.fix:
            total_saved = 0
            for path, original_size in bloated_files:
                orig, new = fix_json_file(path)
                saved = orig - new
                total_saved += saved
                if saved > 1024:  # Only report if saved > 1KB
                    rel_path = os.path.relpath(path, metadata_dir)
                    print(f"    {rel_path}: {format_size(orig)} -> {format_size(new)}")

            print(f"\n  Fixed {len(bloated_files)} files, saved {format_size(total_saved)}")
        else:
            # Show sample of bloated files
            for path, size in bloated_files[:5]:
                rel_path = os.path.relpath(path, metadata_dir)
                print(f"    {rel_path} ({format_size(size)})")
            if len(bloated_files) > 5:
                print(f"    ... and {len(bloated_files) - 5} more")
    else:
        print("  No bloated JSON files found")

    print("\n" + "=" * 60)
    if not args.fix:
        print("Dry run complete. Run with --fix to apply changes.")
    else:
        print("Cleanup complete!")
    print("=" * 60)


if __name__ == '__main__':
    main()
