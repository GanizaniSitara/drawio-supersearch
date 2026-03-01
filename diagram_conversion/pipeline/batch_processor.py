"""
Batch Processing Pipeline for diagram conversion.

Orchestrates the full pipeline:
1. Discover screenshots from the extraction output
2. Classify each diagram by type
3. Convert screenshots → DrawIO XML
4. Convert applicable diagrams → C4 models
5. Score quality and flag for review
"""

import os
import json
import time
import logging
import concurrent.futures
from typing import Optional, Callable

from ..config import ConversionConfig
from ..converters.drawio_converter import DrawIOConverter, ConversionResult
from ..converters.classifier import DiagramClassifier, ClassificationResult
from ..converters.c4_converter import C4Converter, C4ConversionResult
from .database import ConversionDB

logger = logging.getLogger(__name__)


class BatchProcessor:
    """Orchestrates the diagram conversion pipeline."""

    def __init__(self, config: ConversionConfig):
        self.config = config
        config.ensure_directories()

        self.db = ConversionDB(config.db_path)
        self.drawio_converter = DrawIOConverter(
            api_key=config.anthropic_api_key,
            model=config.vision_model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )
        self.classifier = DiagramClassifier(
            api_key=config.anthropic_api_key,
            model=config.model,
        )
        self.c4_converter = C4Converter(
            api_key=config.anthropic_api_key,
            model=config.model,
        )

    # ── Discovery ───────────────────────────────────────────────────

    def discover_screenshots(self) -> list[dict]:
        """
        Scan the screenshots directory and metadata to find all diagrams.
        Returns list of dicts with path and metadata info.
        """
        screenshots_dir = self.config.screenshots_dir
        metadata_dir = self.config.metadata_dir
        discovered = []

        if not os.path.exists(screenshots_dir):
            logger.warning(f"Screenshots directory not found: {screenshots_dir}")
            return discovered

        for space_key in sorted(os.listdir(screenshots_dir)):
            space_img_dir = os.path.join(screenshots_dir, space_key)
            if not os.path.isdir(space_img_dir):
                continue

            for filename in sorted(os.listdir(space_img_dir)):
                if not filename.lower().endswith((".png", ".jpg", ".jpeg")):
                    continue

                image_path = os.path.join(space_img_dir, filename)
                diagram_name = os.path.splitext(filename)[0]

                # Try to load metadata
                meta = {}
                meta_candidates = [
                    os.path.join(metadata_dir, space_key, f"{filename}.json"),
                    os.path.join(metadata_dir, space_key, f"{diagram_name}.json"),
                    os.path.join(metadata_dir, space_key, f"{diagram_name}.png.json"),
                ]
                for meta_path in meta_candidates:
                    if os.path.exists(meta_path):
                        with open(meta_path, "r", encoding="utf-8") as f:
                            meta = json.load(f)
                        break

                discovered.append({
                    "image_path": image_path,
                    "diagram_name": diagram_name,
                    "space_key": space_key,
                    "page_title": meta.get("page_title", ""),
                    "page_id": meta.get("page_id", ""),
                    "page_link": meta.get("page_link", ""),
                    "body_text": meta.get("body_text", ""),
                })

        logger.info(f"Discovered {len(discovered)} screenshots across "
                     f"{len(set(d['space_key'] for d in discovered))} spaces")
        return discovered

    def register_screenshots(self, screenshots: Optional[list[dict]] = None):
        """Register discovered screenshots in the database."""
        if screenshots is None:
            screenshots = self.discover_screenshots()

        for s in screenshots:
            self.db.upsert_conversion(
                source_path=s["image_path"],
                source_name=s["diagram_name"],
                space_key=s["space_key"],
                page_title=s["page_title"],
                page_id=s["page_id"],
                confluence_url=s.get("page_link", ""),
            )

        logger.info(f"Registered {len(screenshots)} screenshots in database")
        return len(screenshots)

    # ── Classification ──────────────────────────────────────────────

    def classify_batch(self, limit: int = 0,
                       use_vision: bool = True,
                       progress_callback: Optional[Callable] = None) -> dict:
        """
        Classify unclassified diagrams.

        Args:
            limit: Max diagrams to classify (0 = all pending)
            use_vision: If True, use Claude Vision for classification.
                        If False, use text-only heuristics.
            progress_callback: Optional fn(current, total, name)
        """
        conn_db = self.db._connect()
        rows = conn_db.execute(
            "SELECT * FROM conversions WHERE diagram_type = 'unknown' "
            "ORDER BY id LIMIT ?",
            (limit if limit > 0 else 999999,),
        ).fetchall()
        conn_db.close()

        items = [dict(r) for r in rows]
        stats = {"total": len(items), "classified": 0, "errors": 0, "tokens": 0}

        run_id = self.db.start_pipeline_run("classify", len(items))

        for i, item in enumerate(items):
            if progress_callback:
                progress_callback(i + 1, len(items), item["source_name"])

            try:
                if use_vision:
                    result = self.classifier.classify(
                        item["source_path"],
                        extra_context=f"Page: {item['page_title']} | Space: {item['space_key']}",
                    )
                else:
                    result = self.classifier.classify_from_text(
                        item["source_name"],
                        item.get("page_title", ""),
                        item.get("description", ""),
                    )

                self.db.upsert_conversion(
                    item["source_path"],
                    diagram_type=result.diagram_type,
                    classification_confidence=result.confidence,
                    is_system_diagram=1 if result.is_system_diagram else 0,
                    c4_convertible=1 if result.c4_convertible else 0,
                    description=result.description,
                    key_elements=json.dumps(result.key_elements),
                    tokens_used=(item.get("tokens_used", 0) or 0) + result.tokens_used,
                )

                stats["classified"] += 1
                stats["tokens"] += result.tokens_used

            except Exception as e:
                logger.error(f"Classification error for {item['source_name']}: {e}")
                stats["errors"] += 1

            # Rate limiting
            if use_vision and i < len(items) - 1:
                time.sleep(0.5)

        self.db.complete_pipeline_run(run_id, **{
            "processed": stats["classified"] + stats["errors"],
            "succeeded": stats["classified"],
            "failed": stats["errors"],
            "total_tokens": stats["tokens"],
        })

        return stats

    # ── DrawIO Conversion ───────────────────────────────────────────

    def convert_batch(self, limit: int = 0,
                      progress_callback: Optional[Callable] = None) -> dict:
        """
        Convert pending screenshots to DrawIO XML.

        Args:
            limit: Max to convert (0 = all pending)
            progress_callback: Optional fn(current, total, name)
        """
        pending = self.db.get_pending_conversions(
            limit=limit if limit > 0 else 99999
        )

        stats = {"total": len(pending), "converted": 0, "failed": 0,
                 "tokens": 0, "skipped": 0}

        run_id = self.db.start_pipeline_run("convert", len(pending))

        for i, item in enumerate(pending):
            if progress_callback:
                progress_callback(i + 1, len(pending), item["source_name"])

            # Skip if source doesn't exist
            if not os.path.exists(item["source_path"]):
                logger.warning(f"Source missing: {item['source_path']}")
                self.db.upsert_conversion(
                    item["source_path"],
                    drawio_status="error",
                    drawio_error="Source file missing",
                )
                stats["skipped"] += 1
                continue

            # Build output path
            space_dir = os.path.join(
                self.config.drawio_output_dir,
                item.get("space_key", "unknown"),
            )
            os.makedirs(space_dir, exist_ok=True)
            output_path = os.path.join(space_dir, f"{item['source_name']}.drawio")

            # Build extra context from metadata
            extra_context = ""
            if item.get("page_title"):
                extra_context += f"Page title: {item['page_title']}\n"
            if item.get("description"):
                extra_context += f"Description: {item['description']}\n"

            try:
                result = self.drawio_converter.convert(
                    item["source_path"],
                    extra_context=extra_context,
                    output_path=output_path,
                )

                # Compute quality score
                quality = self._compute_quality_score(result, item)

                # Determine review status
                if result.success and result.confidence_score >= self.config.auto_accept_threshold:
                    review_status = "accepted"
                elif result.success and result.confidence_score >= self.config.review_threshold:
                    review_status = "needs_review"
                else:
                    review_status = "needs_review"

                self.db.upsert_conversion(
                    item["source_path"],
                    drawio_path=output_path if result.success else "",
                    drawio_status="success" if result.success else "error",
                    drawio_confidence=result.confidence_score,
                    shape_count=result.shape_count,
                    connection_count=result.connection_count,
                    text_elements=json.dumps(result.text_elements),
                    drawio_error=result.error,
                    quality_score=quality,
                    review_status=review_status,
                    tokens_used=(item.get("tokens_used", 0) or 0) + result.tokens_used,
                )

                if result.success:
                    stats["converted"] += 1
                else:
                    stats["failed"] += 1
                stats["tokens"] += result.tokens_used

            except Exception as e:
                logger.error(f"Conversion error for {item['source_name']}: {e}")
                self.db.upsert_conversion(
                    item["source_path"],
                    drawio_status="error",
                    drawio_error=str(e),
                )
                stats["failed"] += 1

            # Rate limiting
            if i < len(pending) - 1:
                time.sleep(1.0)

        self.db.complete_pipeline_run(run_id, **{
            "processed": stats["converted"] + stats["failed"] + stats["skipped"],
            "succeeded": stats["converted"],
            "failed": stats["failed"],
            "skipped": stats["skipped"],
            "total_tokens": stats["tokens"],
        })

        return stats

    def _compute_quality_score(self, result: ConversionResult,
                               item: dict) -> float:
        """Compute an overall quality score combining multiple signals."""
        if not result.success:
            return 0.0

        score = result.confidence_score * 0.6

        # Bonus for matching classification expectations
        if item.get("diagram_type") not in ("unknown", "other"):
            score += 0.1

        # Bonus for text extraction
        if len(result.text_elements) > 3:
            score += 0.15
        elif len(result.text_elements) > 0:
            score += 0.1

        # Bonus for connections (implies meaningful diagram)
        if result.connection_count > 0:
            score += 0.15

        return min(score, 1.0)

    # ── C4 Conversion ───────────────────────────────────────────────

    def convert_c4_batch(self, limit: int = 0,
                         progress_callback: Optional[Callable] = None) -> dict:
        """
        Convert eligible diagrams to C4 models.

        Args:
            limit: Max to convert (0 = all eligible)
            progress_callback: Optional fn(current, total, name)
        """
        candidates = self.db.get_c4_candidates(
            limit=limit if limit > 0 else 99999
        )

        stats = {"total": len(candidates), "converted": 0, "failed": 0,
                 "tokens": 0}

        run_id = self.db.start_pipeline_run("c4_convert", len(candidates))

        for i, item in enumerate(candidates):
            if progress_callback:
                progress_callback(i + 1, len(candidates), item["source_name"])

            try:
                drawio_path = item.get("drawio_path", "")

                if drawio_path and os.path.exists(drawio_path):
                    # Convert from DrawIO XML
                    with open(drawio_path, "r", encoding="utf-8") as f:
                        drawio_xml = f.read()
                    c4_result = self.c4_converter.convert_from_drawio(
                        drawio_xml, source_path=drawio_path
                    )
                else:
                    # Fall back to direct image conversion
                    c4_result = self.c4_converter.convert_from_image(
                        item["source_path"]
                    )

                if c4_result.success and c4_result.model:
                    model = c4_result.model

                    # Save C4 DrawIO file
                    c4_space_dir = os.path.join(
                        self.config.c4_output_dir,
                        item.get("space_key", "unknown"),
                    )
                    os.makedirs(c4_space_dir, exist_ok=True)
                    c4_drawio_path = os.path.join(
                        c4_space_dir, f"{item['source_name']}_c4.drawio"
                    )
                    c4_json_path = os.path.join(
                        c4_space_dir, f"{item['source_name']}_c4.json"
                    )

                    # Write C4 DrawIO
                    with open(c4_drawio_path, "w", encoding="utf-8") as f:
                        f.write(model.to_drawio_c4())

                    # Write C4 JSON model
                    with open(c4_json_path, "w", encoding="utf-8") as f:
                        f.write(model.to_json())

                    # Save to database
                    model_id = self.db.save_c4_model(
                        item["id"],
                        model.to_dict(),
                        drawio_c4_path=c4_drawio_path,
                    )

                    self.db.upsert_conversion(
                        item["source_path"],
                        c4_path=c4_json_path,
                        c4_status="success",
                        c4_level=model.c4_level,
                        c4_system_count=len(model.systems),
                        c4_relationship_count=len(model.relationships),
                        tokens_used=(item.get("tokens_used", 0) or 0) + c4_result.tokens_used,
                    )

                    stats["converted"] += 1
                else:
                    self.db.upsert_conversion(
                        item["source_path"],
                        c4_status="error",
                        c4_error=c4_result.error,
                    )
                    stats["failed"] += 1

                stats["tokens"] += c4_result.tokens_used

            except Exception as e:
                logger.error(f"C4 conversion error for {item['source_name']}: {e}")
                self.db.upsert_conversion(
                    item["source_path"],
                    c4_status="error",
                    c4_error=str(e),
                )
                stats["failed"] += 1

            # Rate limiting
            if i < len(candidates) - 1:
                time.sleep(1.0)

        self.db.complete_pipeline_run(run_id, **{
            "processed": stats["converted"] + stats["failed"],
            "succeeded": stats["converted"],
            "failed": stats["failed"],
            "total_tokens": stats["tokens"],
        })

        return stats

    # ── Full pipeline ───────────────────────────────────────────────

    def run_full_pipeline(self, limit: int = 0, classify_with_vision: bool = True,
                          progress_callback: Optional[Callable] = None) -> dict:
        """
        Run the complete conversion pipeline:
        1. Discover and register screenshots
        2. Classify diagram types
        3. Convert to DrawIO XML
        4. Convert applicable to C4 models

        Args:
            limit: Max items per stage (0 = all)
            classify_with_vision: Use vision API for classification
            progress_callback: Optional fn(stage, current, total, name)
        """
        results = {}

        def wrap_cb(stage):
            if progress_callback:
                return lambda cur, tot, name: progress_callback(stage, cur, tot, name)
            return None

        # Stage 1: Discover
        logger.info("=== Stage 1: Discovering screenshots ===")
        screenshots = self.discover_screenshots()
        count = self.register_screenshots(screenshots)
        results["discovery"] = {"found": count}

        # Stage 2: Classify
        logger.info("=== Stage 2: Classifying diagrams ===")
        results["classification"] = self.classify_batch(
            limit=limit,
            use_vision=classify_with_vision,
            progress_callback=wrap_cb("classify"),
        )

        # Stage 3: Convert to DrawIO
        logger.info("=== Stage 3: Converting to DrawIO ===")
        results["conversion"] = self.convert_batch(
            limit=limit,
            progress_callback=wrap_cb("convert"),
        )

        # Stage 4: C4 conversion
        logger.info("=== Stage 4: Converting to C4 models ===")
        results["c4"] = self.convert_c4_batch(
            limit=limit,
            progress_callback=wrap_cb("c4"),
        )

        # Summary
        results["stats"] = self.db.get_stats()
        return results
