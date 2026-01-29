#!/usr/bin/env python3
"""
Performance Profiling Script for DrawIO SuperSearch

Profiles different components to identify bottlenecks:
- Indexing performance
- Search performance
- Database queries
- Whoosh index operations

Usage:
    python scripts/profile_performance.py                    # Run all profiles
    python scripts/profile_performance.py --index            # Profile indexing only
    python scripts/profile_performance.py --search "query"   # Profile search
    python scripts/profile_performance.py --stats            # Show index/DB stats
"""

import os
import sys
import time
import argparse
import cProfile
import pstats
import io
import sqlite3
from datetime import datetime

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from extractor.config import Settings


def get_settings():
    return Settings.get()


def format_size(size_bytes):
    """Format bytes to human readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def format_time(seconds):
    """Format seconds to human readable string."""
    if seconds < 0.001:
        return f"{seconds*1000000:.1f} us"
    elif seconds < 1:
        return f"{seconds*1000:.1f} ms"
    elif seconds < 60:
        return f"{seconds:.2f} s"
    else:
        return f"{seconds/60:.1f} min"


def get_index_stats():
    """Get statistics about the index and database."""
    settings = get_settings()
    stats = {}

    # Database stats
    db_path = os.path.join(settings['content_directory'], 'diagrams.db')
    if os.path.exists(db_path):
        stats['db_size'] = os.path.getsize(db_path)

        conn = sqlite3.connect(db_path)
        c = conn.cursor()

        c.execute('SELECT COUNT(*) FROM diagrams')
        stats['diagram_count'] = c.fetchone()[0]

        c.execute('SELECT COUNT(*) FROM applications')
        stats['app_count'] = c.fetchone()[0]

        c.execute('SELECT COUNT(*) FROM diagram_applications')
        stats['app_links'] = c.fetchone()[0]

        # Get average content_text length
        c.execute('SELECT AVG(LENGTH(content_text)) FROM diagrams WHERE content_text IS NOT NULL')
        avg_len = c.fetchone()[0]
        stats['avg_content_length'] = int(avg_len) if avg_len else 0

        # Get total content size
        c.execute('SELECT SUM(LENGTH(content_text)) FROM diagrams')
        total = c.fetchone()[0]
        stats['total_content_size'] = total if total else 0

        conn.close()
    else:
        stats['db_size'] = 0
        stats['diagram_count'] = 0

    # Whoosh index stats
    index_dir = os.path.join(settings['content_directory'], 'whoosh_index')
    if os.path.exists(index_dir):
        total_size = 0
        file_count = 0
        for f in os.listdir(index_dir):
            fp = os.path.join(index_dir, f)
            if os.path.isfile(fp):
                total_size += os.path.getsize(fp)
                file_count += 1
        stats['index_size'] = total_size
        stats['index_files'] = file_count
    else:
        stats['index_size'] = 0
        stats['index_files'] = 0

    # Metadata/images stats
    metadata_dir = settings['metadata_directory']
    images_dir = settings['images_directory']

    metadata_count = 0
    metadata_size = 0
    if os.path.exists(metadata_dir):
        for root, dirs, files in os.walk(metadata_dir):
            for f in files:
                if f.endswith('.json'):
                    metadata_count += 1
                    metadata_size += os.path.getsize(os.path.join(root, f))
    stats['metadata_count'] = metadata_count
    stats['metadata_size'] = metadata_size

    images_count = 0
    images_size = 0
    if os.path.exists(images_dir):
        for root, dirs, files in os.walk(images_dir):
            for f in files:
                if f.endswith('.png'):
                    images_count += 1
                    images_size += os.path.getsize(os.path.join(root, f))
    stats['images_count'] = images_count
    stats['images_size'] = images_size

    return stats


def print_stats():
    """Print index and database statistics."""
    print("=" * 60)
    print("INDEX & DATABASE STATISTICS")
    print("=" * 60)

    stats = get_index_stats()

    print(f"\nDatabase:")
    print(f"  Size:              {format_size(stats['db_size'])}")
    print(f"  Diagrams:          {stats['diagram_count']:,}")
    print(f"  Applications:      {stats['app_count']}")
    print(f"  App links:         {stats['app_links']:,}")
    print(f"  Avg content len:   {stats['avg_content_length']:,} chars")
    print(f"  Total content:     {format_size(stats['total_content_size'])}")

    print(f"\nWhoosh Index:")
    print(f"  Size:              {format_size(stats['index_size'])}")
    print(f"  Files:             {stats['index_files']}")

    print(f"\nSource Files:")
    print(f"  Metadata files:    {stats['metadata_count']:,} ({format_size(stats['metadata_size'])})")
    print(f"  Image files:       {stats['images_count']:,} ({format_size(stats['images_size'])})")

    print("=" * 60)

    return stats


def profile_indexing(verbose=False):
    """Profile the indexing process."""
    print("=" * 60)
    print("PROFILING: Indexing")
    print("=" * 60)

    # Import here to avoid loading everything upfront
    from browser.app import index_all_diagrams

    # Profile with cProfile
    profiler = cProfile.Profile()

    start_time = time.time()
    profiler.enable()

    def progress(space_idx, total, space_key, count):
        if verbose:
            print(f"\r  [{space_idx}/{total}] {space_key}: {count} diagrams", end='', flush=True)

    total = index_all_diagrams(progress_callback=progress if verbose else None)

    profiler.disable()
    elapsed = time.time() - start_time

    if verbose:
        print()  # newline after progress

    print(f"\nIndexing completed:")
    print(f"  Diagrams indexed:  {total:,}")
    print(f"  Total time:        {format_time(elapsed)}")
    if total > 0:
        print(f"  Per diagram:       {format_time(elapsed/total)}")

    # Print top time consumers
    print(f"\nTop 15 time-consuming functions:")
    print("-" * 60)

    s = io.StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
    ps.print_stats(15)
    print(s.getvalue())

    return elapsed, total


def profile_search(query, num_iterations=10):
    """Profile search performance."""
    print("=" * 60)
    print(f"PROFILING: Search (query='{query}')")
    print("=" * 60)

    # Import here
    from browser.app import init_db, init_index, get_index_dir
    from whoosh.index import open_dir
    from whoosh.qparser import MultifieldParser

    init_db()
    init_index()

    index_dir = get_index_dir()
    ix = open_dir(index_dir)

    # Warm up
    with ix.searcher() as searcher:
        parser = MultifieldParser(["diagram_name", "page_title", "content"], ix.schema)
        q = parser.parse(query)
        _ = searcher.search(q, limit=20)

    # Profile multiple iterations
    times = []
    result_count = 0

    profiler = cProfile.Profile()
    profiler.enable()

    for i in range(num_iterations):
        start = time.time()
        with ix.searcher() as searcher:
            parser = MultifieldParser(["diagram_name", "page_title", "content"], ix.schema)
            q = parser.parse(query)
            results = searcher.search(q, limit=20)
            result_count = len(results)
        elapsed = time.time() - start
        times.append(elapsed)

    profiler.disable()

    avg_time = sum(times) / len(times)
    min_time = min(times)
    max_time = max(times)

    print(f"\nSearch results: {result_count} matches")
    print(f"\nTiming ({num_iterations} iterations):")
    print(f"  Average:  {format_time(avg_time)}")
    print(f"  Min:      {format_time(min_time)}")
    print(f"  Max:      {format_time(max_time)}")

    # Print top time consumers
    print(f"\nTop 10 time-consuming functions:")
    print("-" * 60)

    s = io.StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
    ps.print_stats(10)
    print(s.getvalue())

    return avg_time, result_count


def profile_db_queries():
    """Profile database query performance."""
    print("=" * 60)
    print("PROFILING: Database Queries")
    print("=" * 60)

    settings = get_settings()
    db_path = os.path.join(settings['content_directory'], 'diagrams.db')

    if not os.path.exists(db_path):
        print("Database not found. Run indexing first.")
        return

    conn = sqlite3.connect(db_path)

    queries = [
        ("Count all diagrams", "SELECT COUNT(*) FROM diagrams"),
        ("Get all diagrams", "SELECT * FROM diagrams"),
        ("Search by name (LIKE)", "SELECT * FROM diagrams WHERE diagram_name LIKE '%test%'"),
        ("Search content (LIKE)", "SELECT * FROM diagrams WHERE content_text LIKE '%test%'"),
        ("Join with applications", """
            SELECT d.*, a.name as app_name
            FROM diagrams d
            LEFT JOIN diagram_applications da ON d.id = da.diagram_id
            LEFT JOIN applications a ON da.application_id = a.id
        """),
        ("Group by space", "SELECT space_key, COUNT(*) FROM diagrams GROUP BY space_key"),
    ]

    for name, query in queries:
        times = []
        rows = 0

        for _ in range(5):
            start = time.time()
            c = conn.cursor()
            c.execute(query)
            results = c.fetchall()
            rows = len(results)
            elapsed = time.time() - start
            times.append(elapsed)

        avg = sum(times) / len(times)
        print(f"\n{name}:")
        print(f"  Rows:     {rows:,}")
        print(f"  Time:     {format_time(avg)} (avg of 5)")

    conn.close()


def profile_app_startup():
    """Profile Flask app startup time."""
    print("=" * 60)
    print("PROFILING: App Startup")
    print("=" * 60)

    # Measure import time
    start = time.time()

    profiler = cProfile.Profile()
    profiler.enable()

    # Force reimport
    if 'browser.app' in sys.modules:
        del sys.modules['browser.app']

    from browser import app as flask_app

    profiler.disable()
    import_time = time.time() - start

    print(f"\nImport time: {format_time(import_time)}")

    # Print top time consumers
    print(f"\nTop 10 time-consuming imports/initializations:")
    print("-" * 60)

    s = io.StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
    ps.print_stats(10)
    print(s.getvalue())


def main():
    parser = argparse.ArgumentParser(
        description='Profile DrawIO SuperSearch performance'
    )
    parser.add_argument('--stats', action='store_true',
                        help='Show index/database statistics')
    parser.add_argument('--index', action='store_true',
                        help='Profile indexing')
    parser.add_argument('--search', type=str, metavar='QUERY',
                        help='Profile search with given query')
    parser.add_argument('--db', action='store_true',
                        help='Profile database queries')
    parser.add_argument('--startup', action='store_true',
                        help='Profile app startup')
    parser.add_argument('--all', action='store_true',
                        help='Run all profiles')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Verbose output')

    args = parser.parse_args()

    # Default to --stats if nothing specified
    if not any([args.stats, args.index, args.search, args.db, args.startup, args.all]):
        args.stats = True

    print(f"\nDrawIO SuperSearch Performance Profiler")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    if args.stats or args.all:
        print_stats()
        print()

    if args.startup or args.all:
        profile_app_startup()
        print()

    if args.db or args.all:
        profile_db_queries()
        print()

    if args.index or args.all:
        profile_indexing(verbose=args.verbose)
        print()

    if args.search:
        profile_search(args.search)
        print()
    elif args.all:
        profile_search("test")
        print()


if __name__ == '__main__':
    main()
