#!/usr/bin/env python3
"""
One-off cleanup script to remove debug HTML files from metadata directories.

Removes:
- _debug_*.html files (full page dumps that bloat the index)

Usage:
    python scripts/cleanup_metadata.py              # Dry run (show what would be deleted)
    python scripts/cleanup_metadata.py --delete     # Actually delete files
"""

import os
import sys
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


def main():
    parser = argparse.ArgumentParser(
        description='Clean up debug HTML files from metadata directories'
    )
    parser.add_argument('--delete', action='store_true',
                        help='Actually delete files (default is dry-run)')
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
    print(f"Mode: {'DELETE' if args.delete else 'DRY RUN'}")
    print("=" * 60)

    # Find debug files
    debug_files = find_debug_files(metadata_dir)

    if not debug_files:
        print("\nNo debug files found. Metadata directory is clean.")
        return

    total_size = sum(size for _, size in debug_files)

    print(f"\nFound {len(debug_files)} debug files ({format_size(total_size)} total)")
    print()

    # Show first few files
    for path, size in debug_files[:10]:
        rel_path = os.path.relpath(path, metadata_dir)
        print(f"  {rel_path} ({format_size(size)})")

    if len(debug_files) > 10:
        print(f"  ... and {len(debug_files) - 10} more files")

    print()

    if args.delete:
        print("Deleting files...")
        deleted = 0
        for path, _ in debug_files:
            try:
                os.remove(path)
                deleted += 1
            except Exception as e:
                print(f"  Error deleting {path}: {e}")

        print(f"\nDeleted {deleted} files, freed {format_size(total_size)}")
    else:
        print("Dry run - no files deleted.")
        print(f"Run with --delete to remove {len(debug_files)} files and free {format_size(total_size)}")


if __name__ == '__main__':
    main()
