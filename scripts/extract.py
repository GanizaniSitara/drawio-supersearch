#!/usr/bin/env python3
"""
CLI script to extract DrawIO diagrams from Confluence.

Usage:
    python scripts/extract.py                    # Extract all spaces
    python scripts/extract.py --spaces ADO,API   # Extract specific spaces
    python scripts/extract.py --dry-run          # Show what would be extracted
"""

import os
import sys
import argparse

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extractor.config import Settings
from extractor.confluence_extractor import ConfluenceExtractor


def progress_callback(space_idx, total_spaces, space_key, diagrams_so_far):
    """Print progress during extraction."""
    pct = (space_idx / total_spaces) * 100
    print(f"\r[{pct:5.1f}%] Space {space_idx}/{total_spaces}: {space_key:<20} | Total: {diagrams_so_far}",
          end='', flush=True)


def main():
    parser = argparse.ArgumentParser(
        description='Extract DrawIO diagrams from Confluence'
    )
    parser.add_argument(
        '--spaces',
        help='Comma-separated space keys (default: all spaces)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be extracted without downloading'
    )
    parser.add_argument(
        '--config',
        help='Path to settings.ini file'
    )
    parser.add_argument(
        '--incremental',
        action='store_true',
        help='Only extract new/changed diagrams (not yet implemented)'
    )

    args = parser.parse_args()

    print("=" * 60)
    print("DrawIO Confluence Extractor")
    print("=" * 60)

    # Load settings
    try:
        if args.config:
            Settings.reload(args.config)
        settings = Settings.get()
    except FileNotFoundError as e:
        print(f"\nError: {e}")
        print("\nPlease copy settings.ini.example to settings.ini and configure your Confluence details.")
        sys.exit(1)

    print(f"\nConfluence URL: {settings['confluence_url']}")
    print(f"Content directory: {settings['content_directory']}")

    if args.dry_run:
        print("\n*** DRY RUN MODE - No files will be downloaded ***\n")

    # Parse spaces
    spaces = None
    if args.spaces:
        spaces = [s.strip() for s in args.spaces.split(',')]
        print(f"Extracting spaces: {', '.join(spaces)}")
    else:
        print("Extracting ALL spaces")

    print("\nStarting extraction...\n")

    try:
        extractor = ConfluenceExtractor()
        total = extractor.extract_all(
            spaces=spaces,
            progress_callback=progress_callback,
            dry_run=args.dry_run
        )
        print(f"\n\nExtraction complete! Total diagrams: {total}")

        if not args.dry_run:
            print("\nNext step: Run 'python scripts/index.py' to build the search index")

    except Exception as e:
        print(f"\n\nError during extraction: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
