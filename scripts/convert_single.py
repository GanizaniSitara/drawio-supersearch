#!/usr/bin/env python3
"""
Single Diagram Converter - Test and validate conversion on individual diagrams.

Usage:
    # Convert a Lucidchart screenshot to DrawIO
    python scripts/convert_single.py screenshot.png

    # Convert and also generate C4 model
    python scripts/convert_single.py screenshot.png --c4

    # Convert a DrawIO file to C4 model
    python scripts/convert_single.py diagram.drawio --c4

    # Specify output directory
    python scripts/convert_single.py screenshot.png -o output/

    # Use a specific model
    python scripts/convert_single.py screenshot.png --model claude-sonnet-4-20250514

    # Classify only
    python scripts/convert_single.py screenshot.png --classify
"""

import os
import sys
import json
import argparse
import logging
from pathlib import Path

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converter.lucidchart_to_drawio import LucidchartToDrawioConverter
from converter.drawio_to_c4 import DrawioToC4Converter
from converter.classifier import DiagramClassifier
from converter.quality import score_drawio_conversion, score_c4_model

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    parser = argparse.ArgumentParser(
        description="Convert a single diagram (screenshot or DrawIO) with quality analysis"
    )
    parser.add_argument(
        "input",
        help="Input file path (PNG screenshot or .drawio file)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output directory (default: same as input)",
    )
    parser.add_argument(
        "--c4",
        action="store_true",
        help="Also generate C4 architecture model",
    )
    parser.add_argument(
        "--classify",
        action="store_true",
        help="Only classify the diagram type (no conversion)",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Claude model for conversion",
    )
    parser.add_argument(
        "--api-key",
        help="Anthropic API key (or set ANTHROPIC_API_KEY env var)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output including raw API responses",
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        sys.exit(1)

    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY required")
        print("Set environment variable or pass --api-key")
        sys.exit(1)

    output_dir = args.output or str(input_path.parent)
    os.makedirs(output_dir, exist_ok=True)

    is_screenshot = input_path.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp")
    is_drawio = input_path.suffix.lower() == ".drawio"

    # Classify
    if args.classify or True:  # Always classify
        print("\n--- Classification ---")
        classifier = DiagramClassifier(api_key=api_key)

        if is_screenshot:
            result = classifier.classify(image_path=str(input_path))
        else:
            with open(input_path, "r", encoding="utf-8") as f:
                content = f.read()
            result = classifier.classify(text_content=content)

        print(f"Type:           {result.diagram_type}")
        print(f"Confidence:     {result.confidence:.2f}")
        print(f"Architecture:   {'Yes' if result.is_architecture else 'No'}")
        print(f"Method:         {result.method}")

        if args.classify:
            return

    # Convert screenshot to DrawIO
    if is_screenshot:
        print("\n--- Lucidchart → DrawIO Conversion ---")
        converter = LucidchartToDrawioConverter(api_key=api_key, model=args.model)

        output_path = os.path.join(output_dir, f"{input_path.stem}.drawio")
        result = converter.convert_and_save(
            str(input_path),
            output_path=output_path,
            diagram_name=input_path.stem,
        )

        print(f"Shapes:         {result.shapes_detected}")
        print(f"Connections:    {result.connections_detected}")
        print(f"Text elements:  {len(result.text_elements)}")
        print(f"Confidence:     {result.confidence:.2f}")

        if result.error:
            print(f"Error:          {result.error}")
        else:
            print(f"Output:         {output_path}")

            # Quality score
            quality = score_drawio_conversion(result.drawio_xml)
            print(f"\nQuality Grade:  {quality.grade} ({quality.overall:.2f})")
            if quality.issues:
                print("Issues:")
                for issue in quality.issues:
                    print(f"  - {issue}")

        if args.verbose and result.raw_response:
            print(f"\n--- Raw API Response ---\n{result.raw_response[:2000]}")

        # C4 conversion if requested
        if args.c4 and result.drawio_xml:
            print("\n--- DrawIO → C4 Model Conversion ---")
            c4_converter = DrawioToC4Converter(api_key=api_key, model=args.model)
            c4_model = c4_converter.convert_and_save(output_path, output_dir=output_dir)

            print(f"Title:          {c4_model.title}")
            print(f"Level:          {c4_model.level}")
            print(f"Systems:        {len(c4_model.systems)}")
            print(f"Containers:     {len(c4_model.containers)}")
            print(f"Relationships:  {len(c4_model.relationships)}")
            print(f"Confidence:     {c4_model.confidence:.2f}")

            if c4_model.error:
                print(f"Error:          {c4_model.error}")

            quality = score_c4_model(c4_model)
            print(f"\nC4 Quality:     {quality.grade} ({quality.overall:.2f})")
            if quality.issues:
                print("Issues:")
                for issue in quality.issues:
                    print(f"  - {issue}")

    # Convert DrawIO to C4
    elif is_drawio:
        if not args.c4:
            print("Input is a .drawio file. Use --c4 to convert to C4 model.")
            print("For screenshot → DrawIO conversion, provide a PNG file.")
            return

        print("\n--- DrawIO → C4 Model Conversion ---")
        c4_converter = DrawioToC4Converter(api_key=api_key, model=args.model)
        c4_model = c4_converter.convert_and_save(str(input_path), output_dir=output_dir)

        print(f"Title:          {c4_model.title}")
        print(f"Level:          {c4_model.level}")
        print(f"Persons:        {len(c4_model.persons)}")
        print(f"Systems:        {len(c4_model.systems)}")
        print(f"Containers:     {len(c4_model.containers)}")
        print(f"Components:     {len(c4_model.components)}")
        print(f"Relationships:  {len(c4_model.relationships)}")
        print(f"Confidence:     {c4_model.confidence:.2f}")

        if c4_model.error:
            print(f"Error:          {c4_model.error}")

        quality = score_c4_model(c4_model)
        print(f"\nC4 Quality:     {quality.grade} ({quality.overall:.2f})")
        if quality.issues:
            print("Issues:")
            for issue in quality.issues:
                print(f"  - {issue}")

        if c4_model.systems or c4_model.containers:
            print("\nC4 Elements:")
            for s in c4_model.systems:
                ext = " [External]" if s.external else ""
                print(f"  System: {s.name}{ext} - {s.description}")
            for c in c4_model.containers:
                print(f"  Container: {c.name} ({c.container_type}) - {c.description}")
            for r in c4_model.relationships:
                print(f"  {r.source} → {r.target}: {r.description}")

    else:
        print(f"Unsupported file type: {input_path.suffix}")
        print("Supported: .png, .jpg, .jpeg, .gif, .webp, .drawio")
        sys.exit(1)


if __name__ == "__main__":
    main()
