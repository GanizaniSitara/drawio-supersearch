#!/usr/bin/env python3
"""
Batch Conversion Pipeline: Lucidchart Screenshots → DrawIO XML → C4 Models

Processes all Lucidchart screenshots in the data directory:
1. Classifies each diagram by type
2. Converts screenshots to DrawIO XML
3. Converts architecture diagrams to C4 models
4. Generates quality scores and review queues

Usage:
    # Convert all screenshots
    python scripts/convert_batch.py

    # Convert specific space
    python scripts/convert_batch.py --spaces OPS,INFRA

    # Classify only (no conversion, cheaper)
    python scripts/convert_batch.py --classify-only

    # Convert with C4 (architecture diagrams only)
    python scripts/convert_batch.py --c4

    # Dry run - show what would be converted
    python scripts/convert_batch.py --dry-run

    # Limit for testing
    python scripts/convert_batch.py --limit 10

    # Use specific model
    python scripts/convert_batch.py --model claude-sonnet-4-20250514
"""

import os
import sys
import json
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

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
logger = logging.getLogger(__name__)


def find_screenshots(content_dir, spaces=None):
    """
    Find all Lucidchart screenshot PNGs in the data directory.

    Args:
        content_dir: Base content directory
        spaces: Optional list of space keys to filter

    Returns:
        List of dicts with path, space_key, diagram_name, metadata
    """
    images_dir = os.path.join(content_dir, "images")
    metadata_dir = os.path.join(content_dir, "metadata")
    diagrams_dir = os.path.join(content_dir, "diagrams")

    screenshots = []

    if not os.path.exists(images_dir):
        logger.warning(f"Images directory not found: {images_dir}")
        return screenshots

    for space_key in sorted(os.listdir(images_dir)):
        space_images = os.path.join(images_dir, space_key)
        if not os.path.isdir(space_images):
            continue

        if spaces and space_key not in spaces:
            continue

        for filename in sorted(os.listdir(space_images)):
            if not filename.lower().endswith(".png"):
                continue

            image_path = os.path.join(space_images, filename)
            diagram_name = filename.rsplit(".", 1)[0]

            # Check if already has a .drawio file (already converted)
            drawio_path = os.path.join(diagrams_dir, space_key, f"{diagram_name}.drawio")
            has_drawio = os.path.exists(drawio_path)

            # Load metadata if available
            meta_path = os.path.join(metadata_dir, space_key, f"{filename}.json")
            metadata = {}
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        metadata = json.load(f)
                except (json.JSONDecodeError, IOError):
                    pass

            # Check if this is a Lucidchart screenshot
            source = metadata.get("source", "")
            is_lucidchart = source in ("lucidchart", "lucidchart-fullpage")

            screenshots.append({
                "image_path": image_path,
                "space_key": space_key,
                "diagram_name": diagram_name,
                "metadata": metadata,
                "has_drawio": has_drawio,
                "is_lucidchart": is_lucidchart,
                "meta_path": meta_path,
            })

    return screenshots


def process_single(
    screenshot, drawio_converter, c4_converter, classifier, content_dir,
    do_convert=True, do_c4=False, do_classify=True
):
    """
    Process a single screenshot through the pipeline.

    Returns:
        dict with results
    """
    result = {
        "image_path": screenshot["image_path"],
        "space_key": screenshot["space_key"],
        "diagram_name": screenshot["diagram_name"],
        "classification": None,
        "drawio_result": None,
        "c4_result": None,
        "drawio_quality": None,
        "c4_quality": None,
        "error": None,
    }

    try:
        # Step 1: Classify
        if do_classify:
            body_text = screenshot["metadata"].get("body_text", "")
            classification = classifier.classify(
                text_content=body_text if body_text else None,
                image_path=screenshot["image_path"],
            )
            result["classification"] = {
                "type": classification.diagram_type,
                "confidence": classification.confidence,
                "is_architecture": classification.is_architecture,
                "method": classification.method,
            }

        # Step 2: Convert to DrawIO
        if do_convert and not screenshot["has_drawio"]:
            diagrams_dir = os.path.join(content_dir, "diagrams", screenshot["space_key"])
            os.makedirs(diagrams_dir, exist_ok=True)
            output_path = os.path.join(diagrams_dir, f"{screenshot['diagram_name']}.drawio")

            conversion = drawio_converter.convert_and_save(
                screenshot["image_path"],
                output_path=output_path,
                diagram_name=screenshot["diagram_name"],
            )

            result["drawio_result"] = {
                "confidence": conversion.confidence,
                "shapes": conversion.shapes_detected,
                "connections": conversion.connections_detected,
                "text_count": len(conversion.text_elements),
                "error": conversion.error,
            }

            # Score quality
            if conversion.drawio_xml:
                quality = score_drawio_conversion(conversion.drawio_xml)
                result["drawio_quality"] = {
                    "overall": quality.overall,
                    "grade": quality.grade,
                    "issues": quality.issues,
                }

        # Step 3: Convert to C4 (if architecture diagram)
        if do_c4 and result.get("classification", {}).get("is_architecture", False):
            drawio_path = os.path.join(
                content_dir, "diagrams", screenshot["space_key"],
                f"{screenshot['diagram_name']}.drawio",
            )

            if os.path.exists(drawio_path):
                c4_output_dir = os.path.join(content_dir, "c4", screenshot["space_key"])
                c4_model = c4_converter.convert_and_save(drawio_path, output_dir=c4_output_dir)
            else:
                # Convert directly from screenshot
                c4_output_dir = os.path.join(content_dir, "c4", screenshot["space_key"])
                c4_model = c4_converter.convert_and_save(
                    screenshot["image_path"], output_dir=c4_output_dir
                )

            result["c4_result"] = {
                "title": c4_model.title,
                "level": c4_model.level,
                "systems": len(c4_model.systems),
                "containers": len(c4_model.containers),
                "relationships": len(c4_model.relationships),
                "confidence": c4_model.confidence,
                "error": c4_model.error,
            }

            # Score C4 quality
            quality = score_c4_model(c4_model)
            result["c4_quality"] = {
                "overall": quality.overall,
                "grade": quality.grade,
                "issues": quality.issues,
            }

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Error processing {screenshot['diagram_name']}: {e}")

    return result


def save_batch_report(results, output_path):
    """Save batch processing report."""
    report = {
        "timestamp": datetime.now().isoformat(),
        "total_processed": len(results),
        "summary": {
            "converted": sum(1 for r in results if r.get("drawio_result") and not r["drawio_result"].get("error")),
            "c4_converted": sum(1 for r in results if r.get("c4_result") and not r["c4_result"].get("error")),
            "errors": sum(1 for r in results if r.get("error")),
            "classifications": {},
        },
        "quality": {
            "drawio_grades": {},
            "c4_grades": {},
            "needs_review": [],
        },
        "results": results,
    }

    # Aggregate classification counts
    for r in results:
        cls = r.get("classification", {})
        if cls:
            dtype = cls.get("type", "unknown")
            report["summary"]["classifications"][dtype] = (
                report["summary"]["classifications"].get(dtype, 0) + 1
            )

    # Aggregate quality grades
    for r in results:
        dq = r.get("drawio_quality", {})
        if dq:
            grade = dq.get("grade", "?")
            report["quality"]["drawio_grades"][grade] = (
                report["quality"]["drawio_grades"].get(grade, 0) + 1
            )
            if grade in ("D", "F"):
                report["quality"]["needs_review"].append({
                    "diagram": r["diagram_name"],
                    "space": r["space_key"],
                    "grade": grade,
                    "issues": dq.get("issues", []),
                })

        cq = r.get("c4_quality", {})
        if cq:
            grade = cq.get("grade", "?")
            report["quality"]["c4_grades"][grade] = (
                report["quality"]["c4_grades"].get(grade, 0) + 1
            )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    return report


def print_summary(report):
    """Print a summary of the batch processing."""
    s = report["summary"]
    q = report["quality"]

    print("\n" + "=" * 60)
    print("BATCH CONVERSION REPORT")
    print("=" * 60)
    print(f"Total processed: {report['total_processed']}")
    print(f"DrawIO converted: {s['converted']}")
    print(f"C4 converted: {s['c4_converted']}")
    print(f"Errors: {s['errors']}")

    if s["classifications"]:
        print("\nDiagram Types:")
        for dtype, count in sorted(s["classifications"].items(), key=lambda x: -x[1]):
            print(f"  {dtype:15s}: {count}")

    if q["drawio_grades"]:
        print("\nDrawIO Quality Grades:")
        for grade in ["A", "B", "C", "D", "F"]:
            count = q["drawio_grades"].get(grade, 0)
            if count:
                print(f"  {grade}: {count}")

    if q["c4_grades"]:
        print("\nC4 Quality Grades:")
        for grade in ["A", "B", "C", "D", "F"]:
            count = q["c4_grades"].get(grade, 0)
            if count:
                print(f"  {grade}: {count}")

    review_count = len(q["needs_review"])
    if review_count:
        print(f"\nNeeds manual review: {review_count} diagrams")
        for item in q["needs_review"][:10]:
            print(f"  [{item['grade']}] {item['space']}/{item['diagram']}")
            for issue in item["issues"][:2]:
                print(f"      - {issue}")
        if review_count > 10:
            print(f"  ... and {review_count - 10} more")

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Batch convert Lucidchart screenshots to DrawIO and C4 models"
    )
    parser.add_argument(
        "--content-dir",
        default="./data/content",
        help="Content directory (default: ./data/content)",
    )
    parser.add_argument(
        "--spaces",
        help="Comma-separated space keys to process",
    )
    parser.add_argument(
        "--classify-only",
        action="store_true",
        help="Only classify diagrams (no conversion)",
    )
    parser.add_argument(
        "--c4",
        action="store_true",
        help="Also convert architecture diagrams to C4 models",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="Skip diagrams that already have .drawio files (default: True)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-convert even if .drawio file exists",
    )
    parser.add_argument(
        "--lucidchart-only",
        action="store_true",
        help="Only process screenshots from Lucidchart (skip native DrawIO)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of diagrams to process",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be processed without doing it",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Claude model for conversion (default: claude-sonnet-4-20250514)",
    )
    parser.add_argument(
        "--classifier-model",
        default="claude-haiku-4-5-20251001",
        help="Claude model for classification (default: claude-haiku-4-5-20251001)",
    )
    parser.add_argument(
        "--api-key",
        help="Anthropic API key (or set ANTHROPIC_API_KEY env var)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel workers (default: 1, be careful with API rate limits)",
    )
    parser.add_argument(
        "--report",
        default="./data/conversion_report.json",
        help="Output path for conversion report",
    )

    args = parser.parse_args()

    # Resolve content directory
    content_dir = os.path.abspath(args.content_dir)

    spaces = None
    if args.spaces:
        spaces = [s.strip() for s in args.spaces.split(",")]

    # Find all screenshots
    print("Scanning for screenshots...")
    screenshots = find_screenshots(content_dir, spaces=spaces)
    print(f"Found {len(screenshots)} screenshots")

    # Filter
    if args.lucidchart_only:
        screenshots = [s for s in screenshots if s["is_lucidchart"]]
        print(f"After Lucidchart filter: {len(screenshots)}")

    if not args.force and args.skip_existing:
        already_converted = sum(1 for s in screenshots if s["has_drawio"])
        if already_converted:
            print(f"Skipping {already_converted} already-converted diagrams")
        if not args.classify_only:
            screenshots = [s for s in screenshots if not s["has_drawio"]]

    if args.limit:
        screenshots = screenshots[: args.limit]
        print(f"Limited to {args.limit} diagrams")

    if not screenshots:
        print("No screenshots to process.")
        return

    if args.dry_run:
        print(f"\n[DRY RUN] Would process {len(screenshots)} diagrams:")
        type_counts = {}
        for s in screenshots:
            space = s["space_key"]
            type_counts[space] = type_counts.get(space, 0) + 1
        for space, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"  {space}: {count} diagrams")
        return

    # Initialize converters
    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key and not args.classify_only:
        print("Error: ANTHROPIC_API_KEY required for conversion")
        print("Set environment variable or pass --api-key")
        sys.exit(1)

    drawio_converter = None
    c4_converter = None
    classifier = DiagramClassifier(api_key=api_key, model=args.classifier_model)

    if not args.classify_only:
        drawio_converter = LucidchartToDrawioConverter(api_key=api_key, model=args.model)
        if args.c4:
            c4_converter = DrawioToC4Converter(api_key=api_key, model=args.model)

    # Process screenshots
    print(f"\nProcessing {len(screenshots)} diagrams...")
    start_time = time.time()
    results = []

    for idx, screenshot in enumerate(screenshots):
        elapsed = time.time() - start_time
        rate = (idx + 1) / max(elapsed, 0.1)
        remaining = (len(screenshots) - idx - 1) / max(rate, 0.001)

        print(
            f"\r[{idx+1}/{len(screenshots)}] "
            f"{screenshot['space_key']}/{screenshot['diagram_name'][:30]}... "
            f"({rate:.1f}/min, ~{remaining:.0f}s remaining)",
            end="",
            flush=True,
        )

        result = process_single(
            screenshot,
            drawio_converter=drawio_converter,
            c4_converter=c4_converter,
            classifier=classifier,
            content_dir=content_dir,
            do_convert=not args.classify_only,
            do_c4=args.c4,
            do_classify=True,
        )
        results.append(result)

    print()  # newline after progress

    # Save report
    elapsed = time.time() - start_time
    print(f"\nCompleted in {elapsed:.1f}s")

    report = save_batch_report(results, args.report)
    print(f"Report saved: {args.report}")
    print_summary(report)


if __name__ == "__main__":
    main()
