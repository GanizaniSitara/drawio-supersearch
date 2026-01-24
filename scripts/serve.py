#!/usr/bin/env python3
"""
CLI script to start the DrawIO Browser web server.

Usage:
    python scripts/serve.py              # Start development server
    python scripts/serve.py --port 8080  # Custom port

For production:
    gunicorn -w 4 -b 0.0.0.0:5000 browser.app:app
"""

import os
import sys
import argparse

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extractor.config import Settings
from browser.app import app, db_is_populated, index_is_populated, init_db, init_index


def main():
    parser = argparse.ArgumentParser(
        description='Start the DrawIO Browser web server'
    )
    parser.add_argument(
        '--host',
        help='Host to bind to (default: from settings.ini)'
    )
    parser.add_argument(
        '--port',
        type=int,
        help='Port to bind to (default: from settings.ini)'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug mode'
    )
    parser.add_argument(
        '--config',
        help='Path to settings.ini file'
    )

    args = parser.parse_args()

    print("=" * 60)
    print("DrawIO Browser Web Server")
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

    # Apply command line overrides
    host = args.host or settings['host']
    port = args.port or settings['port']
    debug = args.debug or settings['debug']

    print(f"\nContent directory: {settings['content_directory']}")
    print(f"Database: {settings['database_path']}")

    # Initialize database and index
    init_db()
    init_index()

    # Check if index is built
    if not db_is_populated() or not index_is_populated():
        print("\nWarning: Index not built!")
        print("Run 'python scripts/index.py' first, or build from the web UI.")
        print("")

    print(f"\nStarting server at http://{host}:{port}")
    print("Press Ctrl+C to stop\n")

    try:
        app.run(host=host, port=port, debug=debug)
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == '__main__':
    main()
