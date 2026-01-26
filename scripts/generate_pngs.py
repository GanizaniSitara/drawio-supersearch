#!/usr/bin/env python3
"""
Generate PNG images from DrawIO files using draw.io.exe

This script finds all DrawIO files that don't have matching PNG files
and generates them using the local draw.io desktop application.

Usage:
    python scripts/generate_pngs.py [--limit N] [--parallel N]

    --limit N     Only process first N files (for testing)
    --parallel N  Number of parallel processes (default: 4)

Each worker uses a separate temp directory to avoid cache conflicts.
"""

import os
import sys
import subprocess
import argparse
import shutil
import tempfile
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import time
import multiprocessing

# Detect platform and set paths accordingly
import platform

if platform.system() == 'Windows':
    # Windows paths
    SOURCE_DIR = r"C:\git\drawio_c4_lint\c4_github_examples\Data"
    DRAWIO_EXE = r"C:\Program Files\draw.io\draw.io.exe"
    TEMP_BASE = r"C:\temp\drawio_workers"
else:
    # WSL/Linux paths
    SOURCE_DIR = "/mnt/c/git/drawio_c4_lint/c4_github_examples/Data"
    DRAWIO_EXE = "/mnt/c/Program Files/draw.io/draw.io.exe"
    TEMP_BASE = "/mnt/c/temp/drawio_workers"

DRAWIO_DIR = os.path.join(SOURCE_DIR, "drawio_github")
PNG_OUTPUT_DIR = os.path.join(SOURCE_DIR, "generated_pngs")

def get_drawio_files():
    """Get list of all DrawIO files."""
    files = []
    for f in os.listdir(DRAWIO_DIR):
        if f.endswith('.drawio'):
            files.append(f)
    return sorted(files)

def get_existing_pngs():
    """Get set of PNG basenames that already exist."""
    existing = set()

    # Check original PNG location (flat in SOURCE_DIR)
    for f in os.listdir(SOURCE_DIR):
        if f.endswith('.png'):
            # Remove .png extension to get the base name
            base = f[:-4]  # e.g., "diagram.drawio--owner--repo"
            existing.add(base)

    # Check generated PNG location
    if os.path.exists(PNG_OUTPUT_DIR):
        for f in os.listdir(PNG_OUTPUT_DIR):
            if f.endswith('.png'):
                base = f[:-4]
                existing.add(base)

    return existing

def get_missing_pngs(drawio_files, existing_pngs):
    """Get list of DrawIO files that don't have corresponding PNGs."""
    missing = []
    for f in drawio_files:
        # DrawIO filename: diagram.drawio--owner--repo.drawio
        # PNG filename: diagram.drawio--owner--repo.png (without the .drawio extension)
        base = f[:-7] if f.endswith('.drawio') else f  # Remove .drawio
        if base not in existing_pngs:
            missing.append(f)
    return missing

def wsl_to_windows_path(wsl_path):
    """Convert WSL /mnt/c/ path to Windows C:\\ path."""
    if wsl_path.startswith('/mnt/'):
        # /mnt/c/foo -> C:\foo
        drive = wsl_path[5].upper()
        rest = wsl_path[6:].replace('/', '\\')  # Start from index 6 to include the leading /
        return f"{drive}:{rest}"
    return wsl_path

def get_worker_id():
    """Get a unique worker ID for this process."""
    return multiprocessing.current_process().name

def generate_png(args):
    """Generate PNG from a DrawIO file. Returns (filename, success, message).

    Args is a tuple of (drawio_filename, worker_num) to support per-worker temp dirs.
    """
    drawio_filename, worker_num = args
    input_path = os.path.join(DRAWIO_DIR, drawio_filename)

    # Output name: remove .drawio extension and add .png
    base = drawio_filename[:-7] if drawio_filename.endswith('.drawio') else drawio_filename
    output_path = os.path.join(PNG_OUTPUT_DIR, f"{base}.png")

    # Create per-worker temp directory to avoid cache conflicts
    if platform.system() == 'Windows':
        worker_temp = os.path.join(TEMP_BASE, f"worker{worker_num}")
    else:
        worker_temp = os.path.join(TEMP_BASE, f"worker{worker_num}")

    os.makedirs(worker_temp, exist_ok=True)

    # On Windows, use paths directly; on WSL, convert to Windows paths for draw.io.exe
    if platform.system() == 'Windows':
        win_input = input_path
        win_output = output_path
        win_temp = worker_temp
    else:
        win_input = wsl_to_windows_path(input_path)
        win_output = wsl_to_windows_path(output_path)
        win_temp = wsl_to_windows_path(worker_temp)

    try:
        # Simple draw.io CLI: -x export, -f format, -o output
        # Note: Running with --parallel 1 is recommended to avoid cache conflicts
        result = subprocess.run(
            [DRAWIO_EXE, '-x', '-f', 'png', '-o', win_output, win_input],
            capture_output=True,
            text=True,
            timeout=60  # 60 second timeout per file
        )

        if result.returncode == 0 and os.path.exists(output_path):
            return (drawio_filename, True, "OK")
        else:
            error = result.stderr[:200] if result.stderr else "Unknown error"
            return (drawio_filename, False, error)

    except subprocess.TimeoutExpired:
        return (drawio_filename, False, "Timeout")
    except Exception as e:
        return (drawio_filename, False, str(e)[:200])

def main():
    parser = argparse.ArgumentParser(description='Generate PNGs from DrawIO files')
    parser.add_argument('--limit', type=int, help='Limit number of files to process')
    parser.add_argument('--parallel', type=int, default=4, help='Number of parallel processes')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')
    args = parser.parse_args()

    print("=" * 70)
    print("DrawIO to PNG Generator")
    print("=" * 70)
    print(f"\nSource: {DRAWIO_DIR}")
    print(f"Output: {PNG_OUTPUT_DIR}")
    print(f"draw.io: {DRAWIO_EXE}")

    # Check draw.io exists
    if not os.path.exists(DRAWIO_EXE):
        print(f"\nERROR: draw.io not found at {DRAWIO_EXE}")
        sys.exit(1)

    # Create output directory
    os.makedirs(PNG_OUTPUT_DIR, exist_ok=True)

    # Get files
    print("\nScanning files...")
    drawio_files = get_drawio_files()
    print(f"  Found {len(drawio_files):,} DrawIO files")

    existing_pngs = get_existing_pngs()
    print(f"  Found {len(existing_pngs):,} existing PNG files")

    missing = get_missing_pngs(drawio_files, existing_pngs)
    print(f"  Missing {len(missing):,} PNG files")

    if args.limit:
        missing = missing[:args.limit]
        print(f"  Limited to {len(missing)} files")

    if args.dry_run:
        print("\nDry run - would generate:")
        for f in missing[:20]:
            print(f"  {f}")
        if len(missing) > 20:
            print(f"  ... and {len(missing) - 20} more")
        return

    if not missing:
        print("\nAll PNGs already exist!")
        return

    print(f"\nGenerating {len(missing):,} PNG files with {args.parallel} parallel processes...")
    print(f"Using temp directories in: {TEMP_BASE}")
    print("-" * 70)

    # Create temp directories for each worker
    os.makedirs(TEMP_BASE, exist_ok=True)
    for i in range(args.parallel):
        worker_dir = os.path.join(TEMP_BASE, f"worker{i}")
        os.makedirs(worker_dir, exist_ok=True)
        # Create subdirs that draw.io/Electron might need
        os.makedirs(os.path.join(worker_dir, 'electron'), exist_ok=True)

    # Assign each file to a worker (round-robin)
    work_items = [(f, i % args.parallel) for i, f in enumerate(missing)]

    start_time = time.time()
    success_count = 0
    error_count = 0

    with ProcessPoolExecutor(max_workers=args.parallel) as executor:
        futures = {executor.submit(generate_png, item): item[0] for item in work_items}

        for i, future in enumerate(as_completed(futures), 1):
            filename, success, message = future.result()

            if success:
                success_count += 1
                # Show successful exports
                print(f"  OK: {filename[:70]}...")
            else:
                error_count += 1
                # Only show first 10 errors to reduce noise
                if error_count <= 10:
                    print(f"  ERROR: {filename[:50]}: {message[:50]}")
                elif error_count == 11:
                    print(f"  ... (suppressing further error details)")

            if i % 100 == 0 or i == len(missing):
                elapsed = time.time() - start_time
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(missing) - i) / rate if rate > 0 else 0
                print(f"  === Progress: {i}/{len(missing)} ({success_count} OK, {error_count} errors) "
                      f"- {rate:.1f}/s, ETA: {eta/60:.1f}m ===")

    elapsed = time.time() - start_time
    print("-" * 70)
    print(f"Completed in {elapsed/60:.1f} minutes")
    print(f"  Success: {success_count:,}")
    print(f"  Errors: {error_count:,}")

    # Count total PNGs now available
    total_pngs = len(get_existing_pngs())
    print(f"  Total PNGs available: {total_pngs:,}")

if __name__ == '__main__':
    main()
