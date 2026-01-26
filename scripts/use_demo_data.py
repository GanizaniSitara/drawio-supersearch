#!/usr/bin/env python3
"""
Swap in demo data for deployment.
Backs up full data and copies lightweight demo subset.
"""

import os
import shutil

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DEMO_DIR = os.path.join(BASE_DIR, 'demo_data')
BACKUP_DIR = os.path.join(BASE_DIR, 'data_full_backup')


def main():
    print("Swapping in demo data for deployment...")

    if not os.path.exists(DEMO_DIR):
        print("ERROR: demo_data/ not found. Run create_demo_subset.py first.")
        return

    # Backup current data
    if os.path.exists(DATA_DIR) and not os.path.exists(BACKUP_DIR):
        print(f"Backing up {DATA_DIR} to {BACKUP_DIR}...")
        shutil.move(DATA_DIR, BACKUP_DIR)
    elif os.path.exists(DATA_DIR):
        print("Removing existing data/ (backup already exists)...")
        shutil.rmtree(DATA_DIR)

    # Copy demo data
    print(f"Copying {DEMO_DIR} to {DATA_DIR}...")
    shutil.copytree(DEMO_DIR, DATA_DIR)

    print("Done! Now rebuild the index:")
    print("  python scripts/index.py --rebuild")


if __name__ == '__main__':
    main()
