#!/usr/bin/env python3
"""
CLI entry points for the diagram conversion pipeline.

Commands:
    discover    Scan screenshots directory and register in database
    classify    Classify diagrams by type
    convert     Convert screenshots to DrawIO XML
    c4          Convert eligible diagrams to C4 models
    pipeline    Run the full pipeline (discover → classify → convert → c4)
    serve       Start the FastAPI search server
    stats       Show pipeline statistics
"""

import os
import sys
import json
import logging
import argparse

from .config import ConversionConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def progress_printer(stage_or_current, current_or_total=None,
                     total_or_name=None, name_or_none=None):
    """Flexible progress callback that handles both 3-arg and 4-arg forms."""
    if name_or_none is not None:
        # 4-arg form: stage, current, total, name
        print(f"  [{stage_or_current}] {current_or_total}/{total_or_name}: {name_or_none}",
              flush=True)
    else:
        # 3-arg form: current, total, name
        print(f"  {stage_or_current}/{current_or_total}: {total_or_name}", flush=True)


def cmd_discover(args, config):
    """Discover and register screenshots."""
    from .pipeline.batch_processor import BatchProcessor

    processor = BatchProcessor(config)
    screenshots = processor.discover_screenshots()

    if args.dry_run:
        print(f"\nFound {len(screenshots)} screenshots:")
        for s in screenshots[:20]:
            print(f"  [{s['space_key']}] {s['diagram_name']}")
        if len(screenshots) > 20:
            print(f"  ... and {len(screenshots) - 20} more")
        return

    count = processor.register_screenshots(screenshots)
    print(f"\nRegistered {count} screenshots in database")


def cmd_classify(args, config):
    """Classify diagrams by type."""
    from .pipeline.batch_processor import BatchProcessor

    processor = BatchProcessor(config)

    if not args.vision:
        print("Using text-only heuristic classification (faster, less accurate)")
        print("Use --vision for Claude Vision classification")

    stats = processor.classify_batch(
        limit=args.limit,
        use_vision=args.vision,
        progress_callback=progress_printer,
    )
    print(f"\nClassification complete: {stats['classified']} classified, "
          f"{stats['errors']} errors, {stats['tokens']} tokens used")


def cmd_convert(args, config):
    """Convert screenshots to DrawIO XML."""
    from .pipeline.batch_processor import BatchProcessor

    processor = BatchProcessor(config)
    stats = processor.convert_batch(
        limit=args.limit,
        progress_callback=progress_printer,
    )
    print(f"\nConversion complete: {stats['converted']} converted, "
          f"{stats['failed']} failed, {stats['skipped']} skipped, "
          f"{stats['tokens']} tokens used")


def cmd_c4(args, config):
    """Convert diagrams to C4 models."""
    from .pipeline.batch_processor import BatchProcessor

    processor = BatchProcessor(config)
    stats = processor.convert_c4_batch(
        limit=args.limit,
        progress_callback=progress_printer,
    )
    print(f"\nC4 conversion complete: {stats['converted']} models created, "
          f"{stats['failed']} failed, {stats['tokens']} tokens used")


def cmd_pipeline(args, config):
    """Run the full conversion pipeline."""
    from .pipeline.batch_processor import BatchProcessor

    processor = BatchProcessor(config)
    results = processor.run_full_pipeline(
        limit=args.limit,
        classify_with_vision=args.vision,
        progress_callback=progress_printer,
    )

    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print(f"  Discovered: {results['discovery']['found']}")
    print(f"  Classified: {results['classification']['classified']}")
    print(f"  Converted:  {results['conversion']['converted']}")
    print(f"  C4 Models:  {results['c4']['converted']}")
    print(f"  Total Tokens: {results['stats']['total_tokens']}")
    print("=" * 60)


def cmd_serve(args, config):
    """Start the FastAPI search server."""
    import uvicorn
    from .server.app import create_app

    create_app(config)
    print(f"Starting server at http://{config.server_host}:{config.server_port}")
    uvicorn.run(
        "diagram_conversion.server.app:app",
        host=config.server_host,
        port=config.server_port,
        reload=args.reload,
    )


def cmd_stats(args, config):
    """Show pipeline statistics."""
    from .pipeline.database import ConversionDB

    db = ConversionDB(config.db_path)
    stats = db.get_stats()

    print("\n" + "=" * 60)
    print("PIPELINE STATISTICS")
    print("=" * 60)
    print(f"  Total conversions: {stats['total_conversions']}")
    print(f"  Avg confidence:    {stats['avg_confidence']:.1%}")
    print(f"  Total tokens:      {stats['total_tokens']:,}")
    print()
    print("  By Status:")
    for status, count in stats["by_status"].items():
        print(f"    {status}: {count}")
    print()
    print("  By Type:")
    for dtype, count in stats["by_type"].items():
        print(f"    {dtype}: {count}")
    print()
    print("  Review Status:")
    for status, count in stats["by_review"].items():
        print(f"    {status}: {count}")
    print()
    print(f"  C4 Models: {stats['c4_models']}")
    for level, count in stats["c4_by_level"].items():
        print(f"    {level}: {count}")
    print(f"  Unique systems: {stats['unique_systems']}")
    print(f"  Unique technologies: {stats['unique_technologies']}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Diagram Conversion Pipeline: Lucidchart → DrawIO → C4",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", help="Path to conversion.ini config file")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # discover
    p_discover = subparsers.add_parser("discover", help="Discover and register screenshots")
    p_discover.add_argument("--dry-run", action="store_true", help="Show what would be registered")

    # classify
    p_classify = subparsers.add_parser("classify", help="Classify diagrams by type")
    p_classify.add_argument("--limit", type=int, default=0, help="Max diagrams to classify")
    p_classify.add_argument("--vision", action="store_true", help="Use Claude Vision (more accurate, costs API)")
    p_classify.add_argument("--no-vision", dest="vision", action="store_false")
    p_classify.set_defaults(vision=False)

    # convert
    p_convert = subparsers.add_parser("convert", help="Convert screenshots to DrawIO XML")
    p_convert.add_argument("--limit", type=int, default=0, help="Max diagrams to convert")

    # c4
    p_c4 = subparsers.add_parser("c4", help="Convert to C4 models")
    p_c4.add_argument("--limit", type=int, default=0, help="Max diagrams to convert")

    # pipeline
    p_pipeline = subparsers.add_parser("pipeline", help="Run full pipeline")
    p_pipeline.add_argument("--limit", type=int, default=0, help="Max items per stage")
    p_pipeline.add_argument("--vision", action="store_true", default=False,
                            help="Use Claude Vision for classification")

    # serve
    p_serve = subparsers.add_parser("serve", help="Start search server")
    p_serve.add_argument("--reload", action="store_true", help="Auto-reload on changes")

    # stats
    subparsers.add_parser("stats", help="Show pipeline statistics")

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load config
    if args.config:
        config = ConversionConfig.from_ini(args.config)
    else:
        config = ConversionConfig()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "discover": cmd_discover,
        "classify": cmd_classify,
        "convert": cmd_convert,
        "c4": cmd_c4,
        "pipeline": cmd_pipeline,
        "serve": cmd_serve,
        "stats": cmd_stats,
    }

    cmd_fn = commands.get(args.command)
    if cmd_fn:
        cmd_fn(args, config)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
