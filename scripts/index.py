#!/usr/bin/env python3
"""
CLI script to build the DrawIO diagram search index.
Run this after extracting diagrams and before starting the web server.

Usage:
    python scripts/index.py              # Build index
    python scripts/index.py --rebuild    # Clear and rebuild index
"""

import os
import sys
import argparse

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extractor.config import Settings
from browser.app import index_all_diagrams, db_is_populated, index_is_populated


def progress_callback(space_idx, total_spaces, space_key, total_indexed):
    """Print progress during indexing."""
    pct = (space_idx / total_spaces) * 100
    print(f"\r[{pct:5.1f}%] Space {space_idx}/{total_spaces}: {space_key:<20} | Total indexed: {total_indexed}",
          end='', flush=True)


def main():
    parser = argparse.ArgumentParser(
        description='Build DrawIO diagram search index'
    )
    parser.add_argument(
        '--rebuild',
        action='store_true',
        help='Clear existing index and rebuild from scratch'
    )
    parser.add_argument(
        '--config',
        help='Path to settings.ini file'
    )

    args = parser.parse_args()

    print("=" * 60)
    print("DrawIO Diagram Index Builder")
    print("=" * 60)

    # Load settings
    try:
        if args.config:
            Settings.reload(args.config)
        settings = Settings.get()
    except FileNotFoundError as e:
        print(f"\nError: {e}")
        print("\nPlease copy settings.ini.example to settings.ini and configure your settings.")
        sys.exit(1)

    print(f"\nContent directory: {settings['content_directory']}")
    print(f"Database: {settings['database_path']}")
    print(f"Index directory: {settings['index_directory']}")

    # Check if content exists
    metadata_dir = settings['metadata_directory']
    if not os.path.exists(metadata_dir):
        print(f"\nError: Content directory not found: {metadata_dir}")
        print("Please run 'python scripts/extract.py' first to extract diagrams from Confluence.")
        sys.exit(1)

    # Check if index already exists
    if db_is_populated() and index_is_populated() and not args.rebuild:
        print("\nIndex already exists!")
        response = input("Rebuild? (y/N): ").strip().lower()
        if response != 'y':
            print("Aborted.")
            return

    print("\nStarting indexing...")
    print("This may take several minutes depending on the number of diagrams.\n")

    try:
        count = index_all_diagrams(progress_callback)
        print(f"\n\nSuccessfully indexed {count} diagrams!")
        print("\nNext step: Run 'python scripts/serve.py' to start the web browser")

    except Exception as e:
        print(f"\n\nError during indexing: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
