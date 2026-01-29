#!/usr/bin/env python3
"""
OCR Text Extraction for Lucidchart Screenshots

Extracts text from screenshot images using Tesseract OCR and prints to console.
This is a standalone utility for testing OCR on captured screenshots.

Requirements:
    pip install pytesseract Pillow

    System dependency (Tesseract):
    - macOS: brew install tesseract
    - Ubuntu/Debian: apt-get install tesseract-ocr
    - Windows: https://github.com/tesseract-ocr/tesseract

Usage:
    python extractor/ocr_extract.py path/to/image.png
    python extractor/ocr_extract.py path/to/images/*.png
    python extractor/ocr_extract.py --dir content/images/SPACE
"""

import os
import sys
import argparse
import glob

try:
    import pytesseract
    from PIL import Image
except ImportError:
    print("=" * 60)
    print("ERROR: OCR dependencies not installed")
    print("=" * 60)
    print()
    print("Please install:")
    print("    pip install pytesseract Pillow")
    print()
    print("And install Tesseract OCR:")
    print("    macOS:  brew install tesseract")
    print("    Ubuntu: apt-get install tesseract-ocr")
    print("=" * 60)
    sys.exit(1)


def extract_text_from_image(image_path, verbose=False):
    """
    Extract text from an image using OCR.

    Args:
        image_path: Path to the image file
        verbose: Print extra info

    Returns:
        str: Extracted text
    """
    try:
        image = Image.open(image_path)

        if verbose:
            print(f"  Image size: {image.size}")
            print(f"  Image mode: {image.mode}")

        # OCR config optimized for diagrams
        # --oem 3: Use both legacy and LSTM engines
        # --psm 6: Assume uniform block of text
        custom_config = r'--oem 3 --psm 6'
        text = pytesseract.image_to_string(image, config=custom_config)

        # Clean up whitespace
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        cleaned_text = '\n'.join(lines)

        return cleaned_text

    except Exception as e:
        return f"ERROR: {e}"


def main():
    parser = argparse.ArgumentParser(
        description='Extract text from Lucidchart screenshots using OCR'
    )
    parser.add_argument('images', nargs='*', help='Image file paths')
    parser.add_argument('--dir', help='Directory containing images to process')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    parser.add_argument('--json', action='store_true', help='Output as JSON')

    args = parser.parse_args()

    # Collect image paths
    image_paths = []

    if args.images:
        for pattern in args.images:
            # Handle glob patterns
            matches = glob.glob(pattern)
            if matches:
                image_paths.extend(matches)
            elif os.path.exists(pattern):
                image_paths.append(pattern)
            else:
                print(f"Warning: {pattern} not found", file=sys.stderr)

    if args.dir:
        if os.path.isdir(args.dir):
            for ext in ['*.png', '*.jpg', '*.jpeg', '*.PNG', '*.JPG']:
                image_paths.extend(glob.glob(os.path.join(args.dir, ext)))
        else:
            print(f"Error: {args.dir} is not a directory", file=sys.stderr)
            sys.exit(1)

    if not image_paths:
        print("No images specified. Use --help for usage.")
        sys.exit(1)

    # Remove duplicates and sort
    image_paths = sorted(set(image_paths))

    print(f"Processing {len(image_paths)} image(s)...\n")

    results = {}

    for image_path in image_paths:
        print("=" * 60)
        print(f"FILE: {image_path}")
        print("=" * 60)

        text = extract_text_from_image(image_path, verbose=args.verbose)
        results[image_path] = text

        if text:
            print(text)
        else:
            print("(no text extracted)")

        print()

    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    total_chars = sum(len(t) for t in results.values())
    non_empty = sum(1 for t in results.values() if t and not t.startswith("ERROR"))
    print(f"Images processed: {len(image_paths)}")
    print(f"Images with text: {non_empty}")
    print(f"Total characters: {total_chars}")

    if args.json:
        import json
        print("\nJSON Output:")
        print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
