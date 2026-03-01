"""
Quality Scoring for Diagram Conversions

Provides quality metrics for:
- Lucidchart → DrawIO conversions (structural completeness)
- DrawIO → C4 model conversions (architectural richness)

Quality scores help prioritize manual review of low-confidence conversions.
"""

import re
import xml.etree.ElementTree as ET
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class DrawioQualityScore:
    """Quality assessment of a DrawIO conversion."""
    overall: float = 0.0  # 0.0 - 1.0
    shape_count: int = 0
    connection_count: int = 0
    text_element_count: int = 0
    has_valid_xml: bool = False
    has_shapes: bool = False
    has_connections: bool = False
    has_text: bool = False
    has_layout: bool = False
    has_colors: bool = False
    issues: list = field(default_factory=list)
    grade: str = ""  # A, B, C, D, F

    def compute_grade(self):
        """Assign letter grade based on overall score."""
        if self.overall >= 0.85:
            self.grade = "A"
        elif self.overall >= 0.70:
            self.grade = "B"
        elif self.overall >= 0.50:
            self.grade = "C"
        elif self.overall >= 0.30:
            self.grade = "D"
        else:
            self.grade = "F"
        return self.grade


@dataclass
class C4QualityScore:
    """Quality assessment of a C4 model conversion."""
    overall: float = 0.0
    system_count: int = 0
    container_count: int = 0
    relationship_count: int = 0
    has_persons: bool = False
    has_systems: bool = False
    has_relationships: bool = False
    has_descriptions: bool = False
    has_technologies: bool = False
    issues: list = field(default_factory=list)
    grade: str = ""

    def compute_grade(self):
        if self.overall >= 0.85:
            self.grade = "A"
        elif self.overall >= 0.70:
            self.grade = "B"
        elif self.overall >= 0.50:
            self.grade = "C"
        elif self.overall >= 0.30:
            self.grade = "D"
        else:
            self.grade = "F"
        return self.grade


def score_drawio_conversion(drawio_xml):
    """
    Score the quality of a DrawIO XML conversion.

    Args:
        drawio_xml: The generated DrawIO XML string

    Returns:
        DrawioQualityScore
    """
    score = DrawioQualityScore()

    if not drawio_xml:
        score.issues.append("Empty XML content")
        score.compute_grade()
        return score

    # Check XML validity
    try:
        root = ET.fromstring(drawio_xml)
        score.has_valid_xml = True
    except ET.ParseError as e:
        score.issues.append(f"Invalid XML: {e}")
        score.overall = 0.05
        score.compute_grade()
        return score

    # Count shapes and connections
    shapes = 0
    connections = 0
    text_count = 0
    has_positions = False
    has_colors = False

    for cell in root.iter("mxCell"):
        style = cell.get("style", "")
        value = cell.get("value", "")

        if cell.get("edge") == "1":
            connections += 1
            source = cell.get("source", "")
            target = cell.get("target", "")
            if not source or not target:
                score.issues.append("Edge without source or target")
        elif cell.get("vertex") == "1":
            shapes += 1
            if value:
                text_count += 1

            # Check for geometry
            geom = cell.find("mxGeometry")
            if geom is not None:
                x = geom.get("x")
                y = geom.get("y")
                if x and y:
                    has_positions = True

            # Check for colors
            if "fillColor=" in style or "strokeColor=" in style:
                has_colors = True

    score.shape_count = shapes
    score.connection_count = connections
    score.text_element_count = text_count
    score.has_shapes = shapes > 0
    score.has_connections = connections > 0
    score.has_text = text_count > 0
    score.has_layout = has_positions
    score.has_colors = has_colors

    # Check for issues
    if shapes == 0:
        score.issues.append("No shapes found")
    if connections == 0 and shapes > 1:
        score.issues.append("Multiple shapes but no connections")
    if text_count == 0:
        score.issues.append("No text labels on shapes")
    if not has_positions:
        score.issues.append("No position data (x,y) on shapes")

    # Check for disconnected edges (source/target pointing to non-existent IDs)
    cell_ids = set()
    for cell in root.iter("mxCell"):
        cell_id = cell.get("id", "")
        if cell_id:
            cell_ids.add(cell_id)

    orphan_edges = 0
    for cell in root.iter("mxCell"):
        if cell.get("edge") == "1":
            source = cell.get("source", "")
            target = cell.get("target", "")
            if source and source not in cell_ids:
                orphan_edges += 1
            if target and target not in cell_ids:
                orphan_edges += 1

    if orphan_edges > 0:
        score.issues.append(f"{orphan_edges} edges reference non-existent shapes")

    # Calculate overall score
    components = []
    components.append(0.15 if score.has_valid_xml else 0)
    components.append(min(0.25, shapes * 0.05))  # Up to 0.25 for shapes
    components.append(min(0.20, connections * 0.05))  # Up to 0.20 for connections
    components.append(min(0.20, text_count * 0.04))  # Up to 0.20 for text
    components.append(0.10 if has_positions else 0)
    components.append(0.10 if has_colors else 0)

    # Penalty for orphan edges
    if orphan_edges > 0:
        penalty = min(0.15, orphan_edges * 0.05)
        components.append(-penalty)

    score.overall = max(0.0, min(1.0, sum(components)))
    score.compute_grade()

    return score


def score_c4_model(c4_model):
    """
    Score the quality of a C4 model conversion.

    Args:
        c4_model: C4Model instance

    Returns:
        C4QualityScore
    """
    score = C4QualityScore()

    if c4_model.error:
        score.issues.append(f"Conversion error: {c4_model.error}")
        score.compute_grade()
        return score

    score.system_count = len(c4_model.systems)
    score.container_count = len(c4_model.containers)
    score.relationship_count = len(c4_model.relationships)
    score.has_persons = len(c4_model.persons) > 0
    score.has_systems = len(c4_model.systems) > 0
    score.has_relationships = len(c4_model.relationships) > 0

    # Check description quality
    described_items = 0
    total_items = (
        len(c4_model.persons) + len(c4_model.systems)
        + len(c4_model.containers) + len(c4_model.components)
    )
    for item_list in [c4_model.persons, c4_model.systems, c4_model.containers, c4_model.components]:
        for item in item_list:
            if item.description:
                described_items += 1

    score.has_descriptions = described_items > 0

    # Check technology annotations
    tech_items = 0
    for item_list in [c4_model.systems, c4_model.containers, c4_model.components]:
        for item in item_list:
            if hasattr(item, "technology") and item.technology:
                tech_items += 1

    score.has_technologies = tech_items > 0

    # Issues
    if not score.has_systems:
        score.issues.append("No systems identified")
    if not score.has_relationships:
        score.issues.append("No relationships between elements")
    if total_items > 0 and described_items == 0:
        score.issues.append("No descriptions on any elements")
    if c4_model.title == "Not an architecture diagram":
        score.issues.append("Diagram not recognized as architecture")
        score.overall = 0.0
        score.compute_grade()
        return score

    # Calculate overall score
    components = []
    components.append(min(0.25, len(c4_model.systems) * 0.08))
    components.append(min(0.15, len(c4_model.containers) * 0.05))
    components.append(min(0.20, len(c4_model.relationships) * 0.05))
    components.append(0.10 if score.has_persons else 0)
    components.append(
        0.15 * (described_items / max(total_items, 1)) if total_items > 0 else 0
    )
    components.append(0.10 if score.has_technologies else 0)
    components.append(0.05)  # Base score for having any model

    score.overall = max(0.0, min(1.0, sum(components)))
    score.compute_grade()

    return score
